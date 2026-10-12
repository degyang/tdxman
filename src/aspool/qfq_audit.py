"""Collect paired raw/qfq history and audit daily limit statistics in bounded batches."""

from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
from pathlib import Path

from .sqlite_qfq import PRICES, valid_prices


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def stage_connection(out):
    conn = sqlite3.connect(out / "source-stage.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-131072")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS observations (
      symbol TEXT, trade_date TEXT, payload TEXT NOT NULL,
      PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS observations_date ON observations(trade_date,symbol);
    CREATE TABLE IF NOT EXISTS collection (
      symbol TEXT PRIMARY KEY, start TEXT, end TEXT, status TEXT, detail TEXT) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS price_changes (
      symbol TEXT,trade_date TEXT,PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS repaired_dates (
      trade_date TEXT PRIMARY KEY, receipt TEXT) WITHOUT ROWID;
    """)
    return conn


def collect(root, out, start, end, *, workers=4, retry=False, limit=None):
    from tdxman.mac.client import MacClient
    from tdxman.mac.enums import Adjust, Period
    from tdxman.models.enums import Market

    # Inventory is a read-only primary-key projection; source staging never writes
    # the live pool. A source fetch can run while the consistent backup is copied.
    with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as c:
        symbols, last = [], ""
        while True:
            row = c.execute(
                "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1", (last,)
            ).fetchone()
            if row is None:
                break
            last = row[0]
            symbols.append(last)
    if limit:
        symbols = symbols[:limit]
    local, clients = threading.local(), []
    guard = threading.Lock()
    as_of = datetime.now(timezone.utc).date().isoformat()
    # Five-year collection needs about 1,250 sessions, plus a predecessor. Older
    # windows page farther back rather than silently accepting a recent tail.
    elapsed = max(0, (date.today() - date.fromisoformat(start)).days)
    count = min(15000, max(1500, int(elapsed * 0.72) + 120))

    def fetch(symbol):
        code, market = symbol.split(".")
        for attempt in range(2):
            try:
                if not hasattr(local, "client"):
                    local.client = MacClient.from_best_host(timeout=8)
                    local.client.connect()
                    with guard:
                        clients.append(local.client)
                frames = [
                    local.client.get_stock_kline(
                        Market[market], code, Period.DAILY, count=count, adjust=kind
                    )
                    for kind in (Adjust.NONE, Adjust.QFQ)
                ]
                maps = []
                for frame in frames:
                    rows = json.loads(frame.to_json(orient="records", date_format="iso"))
                    mapping = {str(r["datetime"])[:10]: r for r in rows}
                    if len(mapping) != len(rows):
                        raise ValueError("Duplicate source dates")
                    maps.append(mapping)
                raw, qfq = maps
                days = sorted(set(raw) & set(qfq))
                selected = [d for d in days if start <= d <= end]
                prior = [d for d in days if d < start]
                if prior:
                    selected = prior[-1:] + selected
                observations = []
                for day in selected:
                    r, q = raw[day], qfq[day]
                    if not valid_prices(r, positive=True) or not valid_prices(q):
                        continue
                    observations.append(
                        dict(
                            symbol=symbol,
                            trade_date=day,
                            raw={
                                k: r[k]
                                for k in (*PRICES, "vol", "amount", "float_shares")
                                if k in r
                            },
                            qfq={k: q[k] for k in PRICES},
                            source="tdxman:mac",
                            source_as_of=as_of,
                        )
                    )
                detail = dict(
                    raw_rows=len(raw),
                    qfq_rows=len(qfq),
                    paired_rows=len(observations),
                    raw_first=min(raw) if raw else None,
                    raw_last=max(raw) if raw else None,
                    qfq_first=min(qfq) if qfq else None,
                    qfq_last=max(qfq) if qfq else None,
                    unpaired_dates=len(set(raw) ^ set(qfq)),
                    requested_count=count,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
                status = "collected" if observations else "source_unavailable"
                return symbol, observations, status, detail
            except Exception as exc:
                if hasattr(local, "client"):
                    try:
                        local.client.close()
                    except Exception:
                        pass
                    del local.client
                if attempt:
                    return symbol, [], "source_failed", dict(error=str(exc))

    with stage_connection(out) as stage:
        complete = {
            r[0]
            for r in stage.execute(
                "SELECT symbol FROM collection WHERE start=? AND end=? AND status='collected'",
                (start, end),
            )
        }
        todo = [s for s in symbols if retry or s not in complete]
        progress = dict(
            symbols=len(symbols),
            pending=len(todo),
            processed=0,
            paired_rows=0,
            failures=0,
            start=start,
            end=end,
        )
        with ThreadPoolExecutor(max_workers=workers) as executor:
            iterator = iter(todo)
            pending = {executor.submit(fetch, s) for _, s in zip(range(workers * 2), iterator)}
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    symbol, rows, status, detail = future.result()
                    stage.executemany(
                        "INSERT INTO observations VALUES (?,?,?) ON "
                        "CONFLICT(symbol,trade_date) DO UPDATE SET "
                        "payload=excluded.payload",
                        [(symbol, r["trade_date"], json.dumps(r, allow_nan=False)) for r in rows],
                    )
                    stage.execute(
                        "INSERT OR REPLACE INTO collection VALUES (?,?,?,?,?)",
                        (symbol, start, end, status, json.dumps(detail)),
                    )
                    progress["processed"] += 1
                    progress["paired_rows"] += len(rows)
                    progress["failures"] += status != "collected"
                    if progress["processed"] % 25 == 0:
                        stage.commit()
                        save(out / "collection-progress.json", progress)
                        print(json.dumps(progress), flush=True)
                    next_symbol = next(iterator, None)
                    if next_symbol:
                        pending.add(executor.submit(fetch, next_symbol))
        stage.commit()
        for client in clients:
            try:
                client.close()
            except Exception:
                pass
        progress["coverage"] = [
            dict(symbol=s, status=status, **json.loads(detail))
            for s, status, detail in stage.execute(
                "SELECT symbol,status,detail FROM collection ORDER BY symbol"
            )
        ]
        save(out / "collection-report.json", progress)
        return progress


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--report-dir", type=Path, required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument(
        "--phase",
        choices=["collect", "fallback", "gaps", "audit", "repair", "resolve", "all"],
        default="audit",
    )
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--retry", action="store_true")
    p.add_argument("--limit-symbols", type=int)
    p.add_argument("--unknown-threshold", type=int, default=5)
    p.add_argument("--recovery", type=Path)
    a = p.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    if not 0 <= (end - start).days <= 3660 or not 1 <= a.workers <= 8:
        p.error("Use a window up to ten years and workers=1..8")
    root, out = a.root.resolve(), a.report_dir.resolve()
    if out == root or root in out.parents:
        p.error("Keep source staging and reports outside the authoritative pool")
    out.mkdir(parents=True, exist_ok=True)
    if a.phase in ("collect", "all"):
        collect(root, out, a.start, a.end, workers=a.workers, retry=a.retry, limit=a.limit_symbols)
    if a.phase in ("fallback", "all"):
        from .qfq_audit_source import collect_fallback

        collect_fallback(root, out, a.start, a.end)
    if a.phase in ("gaps", "all"):
        from .qfq_audit_source import collect_gaps

        collect_gaps(root, out, a.start, a.end)
    if a.phase not in ("collect", "fallback", "gaps", "resolve"):
        import signal

        from .qfq_audit_repair import run

        stopped = False

        def request_stop(signum, frame):
            nonlocal stopped
            stopped = True

        if a.phase in ("repair", "all"):
            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)

        run(
            root,
            out,
            a.start,
            a.end,
            repair=a.phase in ("repair", "all"),
            recovery=a.recovery,
            threshold=a.unknown_threshold,
            stop_requested=lambda: stopped,
        )
    if a.phase in ("all", "resolve"):
        from .qfq_audit_repair import run
        from .qfq_audit_resolve import resolve

        resolve(root, out, a.start, a.end, a.recovery)
        run(root, out, a.start, a.end, repair=False, recovery=None, threshold=a.unknown_threshold)


if __name__ == "__main__":
    main()
