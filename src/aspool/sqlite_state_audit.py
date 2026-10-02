"""Audit ten years of stock-day evidence; repair states and bounded source anomalies.

Reports and source observations live outside the pool. Never infer suspension from
an absent bar. --repair requires a verified, consistent pre-repair recovery point.
"""

import argparse
import collections
import json
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

from aspool.sqlite_daily_state import (
    finalize_state_publication,
    has_real_trade,
    normalize_traded_states,
)
from aspool.sqlite_daily_update import apply_daily_changes
from aspool.sqlite_stock_store import stock_connection


def issues(row):
    status = row.get("trading_status")
    real = has_real_trade(row)
    found = []
    if real and not status:
        found.append("missing_trading_status")
    elif real and status not in ("TRADING", "TRADED", "NORMAL", "正常交易", "1"):
        found.append("status_conflicts_with_trade")
    if row.get("bar_date") is not None and not real:
        if status not in ("SUSPENDED", "NO_TRADE", "NOT_LISTED", "停牌"):
            found.append("invalid_or_unconfirmed_bar")
        else:
            found.append("legacy_nontrade_placeholder")
    if status == "TRADING" and row.get("calc_status") != "TRADED":
        found.append("trading_calc_mismatch")
    if row.get("bar_date") is None and status not in (
        "SUSPENDED",
        "NO_TRADE",
        "NOT_LISTED",
        "停牌",
    ):
        found.append("missing_bar")
    return found


def scan(conn, symbol, start, end):
    # Primary-key range per stock, avoiding millions of random date-index joins.
    keys = conn.execute(
        "SELECT trade_date FROM daily_bars WHERE symbol=? AND trade_date BETWEEN ? AND ? "
        "UNION SELECT trade_date FROM daily_features WHERE symbol=? "
        "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (symbol, start, end, symbol, start, end),
    ).fetchall()
    bars = {
        r["trade_date"]: r
        for r in dictionaries(
            conn.execute(
                "SELECT *,trade_date AS bar_date FROM daily_bars WHERE symbol=? "
                "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                (symbol, start, end),
            )
        )
    }
    facts = {
        r["trade_date"]: r
        for r in dictionaries(
            conn.execute(
                "SELECT * FROM daily_features WHERE symbol=? AND trade_date BETWEEN ? AND ? "
                "ORDER BY trade_date",
                (symbol, start, end),
            )
        )
    }
    for (day,) in keys:
        yield {**bars.get(day, {}), **facts.get(day, {}), "symbol": symbol, "trade_date": day}


def dictionaries(cursor):
    names = [r[0] for r in cursor.description]
    return (dict(zip(names, r)) for r in cursor)


def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    tmp.replace(path)


def source_repair(conn, rows, *, client, evidence, sessions):
    from tdxman.mac.enums import Period
    from tdxman.models.enums import Market

    symbol = rows[0]["symbol"]
    code, market = symbol.split(".")
    # 10 years require at most roughly 2500 market sessions. Protocol pagination
    # and returned-count safeguards reside in MacClient, not a 1024-bar adapter.
    frame = client.get_stock_kline(Market[market], code, Period.DAILY, count=3000)
    records = frame.to_dict("records")
    save(
        evidence / (symbol + ".json"),
        dict(
            symbol=symbol,
            fetched_at=str(date.today()),
            requested_count=3000,
            returned_count=len(records),
            coverage_start=str(records[0]["datetime"]) if records else None,
            coverage_end=str(records[-1]["datetime"]) if records else None,
            rows=records,
        ),
    )
    observed = {str(r["datetime"])[:10]: r for r in records}
    results = []
    for old in rows:
        day = old["trade_date"]
        raw = observed.get(day)
        if raw is None:
            results.append(dict(symbol=symbol, date=day, result="source_day_unavailable"))
            continue
        bar = {k: raw.get(k) for k in ("open", "high", "low", "close", "amount")}
        bar.update(volume=raw.get("vol"), symbol=symbol, trade_date=day)
        if not has_real_trade(bar):
            results.append(dict(symbol=symbol, date=day, result="source_not_a_verified_trade"))
            continue
        bar["ohlcv_source"] = "tdxman:kline"
        floating = raw.get("float_shares")
        from aspool.sqlite_daily_derived import finite

        if finite(floating, positive=True):
            # The MAC daily field is in ten-thousand shares, as in KlineSource.
            bar.update(float_share=floating * 10000, float_share_source="tdxman:kline")
        try:
            result = apply_daily_changes(
                conn,
                bars=[bar],
                dated_facts=[
                    dict(
                        symbol=symbol,
                        trade_date=day,
                        trading_status="TRADING",
                        trading_status_source="tdxman:kline",
                    )
                ],
                market_sessions=sessions,
                max_elapsed_seconds=300,
            )
        except Exception as exc:
            results.append(dict(symbol=symbol, date=day, result="repair_failed", error=str(exc)))
        else:
            results.append(dict(symbol=symbol, date=day, result="repaired", receipt=result))
    return results


def prepare_recovery(root, destination):
    """Back up all stock publication files under one pool lock and read them back."""
    from aspool import DataPool
    from aspool.pool import pool_lock
    from aspool.sqlite_publication import assert_published

    root, destination = root.resolve(), destination.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Recovery point must be outside the authoritative pool")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = dict(root=str(root), files=[], public_read_verified=False)
    with pool_lock(root, write=True):
        assert_published(root)
        if (root / "catalog.duckdb.wal").exists():
            raise ValueError("Close catalog WAL before taking the recovery point")
        for name in ("stocks.sqlite", "features.sqlite", "adjustments.sqlite"):
            with sqlite3.connect((root / name).as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(destination / name) as target:
                    source.backup(target, pages=8192)
                    if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("Recovery SQLite check failed: " + name)
            manifest["files"].append(
                dict(name=name, bytes=(destination / name).stat().st_size, quick_check="ok")
            )
            print(json.dumps(dict(recovery_file=name)), flush=True)
        shutil.copy2(root / "catalog.duckdb", destination / "catalog.duckdb")
        with stock_connection(root) as conn:
            symbol = conn.execute(
                "SELECT symbol FROM daily_bars ORDER BY symbol LIMIT 1"
            ).fetchone()
        if symbol is None:
            raise ValueError("Recovery readback requires an existing stock")
        original = DataPool(root).read_daily(
            symbols=[symbol[0]], lookback=1, fields=["symbol", "date", "volume", "trading_status"]
        )
        restored = DataPool(destination).read_daily(
            symbols=[symbol[0]], lookback=1, fields=list(original.columns)
        )
        if not original.equals(restored):
            raise ValueError("Recovery public readback differs from the source")
        manifest["public_read_verified"] = True
    save(destination / "recovery.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--source-repair", action="store_true")
    parser.add_argument("--recovery", type=Path)
    parser.add_argument(
        "--prepare-recovery",
        action="store_true",
        help="Create and verify --recovery before the audit",
    )
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if not 0 <= (end - start).days <= 3660:
        parser.error("Specify a window of at most 10 years (3660 days)")
    root, out = args.root.resolve(), args.report_dir.resolve()
    if out == root or root in out.parents:
        parser.error("Keep evidence outside the authoritative pool")
    if args.source_repair and not args.repair:
        parser.error("--source-repair requires --repair")
    if args.prepare_recovery:
        if not args.recovery:
            parser.error("--prepare-recovery requires --recovery")
        prepare_recovery(root, args.recovery)
    if args.repair:
        if not args.recovery:
            parser.error("--repair requires --recovery")
        recovery = json.loads((args.recovery / "recovery.json").read_text())
        if recovery.get("root") != str(root) or not recovery.get("public_read_verified"):
            parser.error("Recovery point must match root and pass public readback")
        if {f["name"] for f in recovery["files"]} != {
            "stocks.sqlite",
            "features.sqlite",
            "adjustments.sqlite",
        }:
            parser.error("Recovery point must include all three stock publication stores")
        for f in recovery["files"]:
            if f["quick_check"] != "ok" or not (args.recovery / f["name"]).exists():
                parser.error("Incomplete recovery point")
    out.mkdir(parents=True, exist_ok=True)
    counts, remaining = collections.Counter(), collections.Counter()
    report = dict(
        root=str(root),
        start=args.start,
        end=args.end,
        status="running",
        inspected_rows=0,
        repaired_states=0,
        enriched_verified_rows=0,
        processed_symbols=0,
        before={},
        after={},
        source_results=[],
    )
    stamp = time.time_ns() // 1000
    anomalies = []
    with stock_connection(root, read_only=not args.repair) as conn:
        conn.execute("PRAGMA cache_size=-262144")
        conn.execute("PRAGMA features.cache_size=-524288")
        symbols = set()
        for table in ("daily_features", "daily_bars"):
            last = ""
            while True:
                row = conn.execute(
                    f"SELECT symbol FROM {table} WHERE symbol>? ORDER BY symbol LIMIT 1",
                    (last,),
                ).fetchone()
                if row is None:
                    break
                last = row[0]
                symbols.add(last)
        symbols = sorted(symbols)
        sessions = [
            r[0] for r in conn.execute("SELECT trade_date FROM market_sessions ORDER BY trade_date")
        ]
        for symbol in symbols:
            rows = list(scan(conn, symbol, args.start, args.end))
            report["inspected_rows"] += len(rows)
            for row in rows:
                tags = issues(row)
                counts.update(tags)
                if any(t != "missing_trading_status" for t in tags):
                    anomalies.append(dict(row, issues=tags))
            if args.repair:
                result = normalize_traded_states(
                    conn, symbol=symbol, start=args.start, end=args.end, publication_stamp=stamp
                )
                report["repaired_states"] += result["changed_rows"]
                report["enriched_verified_rows"] += result["enriched_verified_rows"]
                if result["changed_rows"]:
                    with (out / "normalization-receipts.jsonl").open("a") as receipts:
                        receipts.write(json.dumps(dict(symbol=symbol, **result)) + "\n")
                remaining.update(
                    t for r in scan(conn, symbol, args.start, args.end) for t in issues(r)
                )
            else:
                remaining.update(t for r in rows for t in issues(r))
            report["processed_symbols"] += 1
            if report["processed_symbols"] % 25 == 0:
                report.update(before=dict(counts), after=dict(remaining))
                save(out / "report.json", report)
                print(
                    json.dumps(
                        {
                            k: report[k]
                            for k in ("processed_symbols", "inspected_rows", "repaired_states")
                        }
                    ),
                    flush=True,
                )
        save(out / "anomalies-before.json", anomalies)
        if args.source_repair:
            from tdxman.mac.client import MacClient

            source_out = out / "source-observations"
            source_out.mkdir(exist_ok=True)
            targets = collections.defaultdict(list)
            for row in anomalies:
                if any(
                    t in row["issues"]
                    for t in (
                        "invalid_or_unconfirmed_bar",
                        "status_conflicts_with_trade",
                        "trading_calc_mismatch",
                        "missing_bar",
                    )
                ):
                    targets[row["symbol"]].append(row)
            with MacClient.from_best_host() as client:
                for symbol, rows in targets.items():
                    try:
                        results = source_repair(
                            conn, rows, client=client, evidence=source_out, sessions=sessions
                        )
                    except Exception as exc:
                        results = [dict(symbol=symbol, result="source_failed", error=str(exc))]
                    report["source_results"].extend(results)
                    save(out / "report.json", report)
                    print(json.dumps(dict(source_symbol=symbol, results=results)), flush=True)
        if args.repair and report["repaired_states"]:
            report["final_publication"] = finalize_state_publication(
                conn, start=args.start, end=args.end
            )
        # Full post-repair rescan, not just a count of attempted patches.
        if args.repair:
            remaining.clear()
            final_anomalies = []
            for symbol in symbols:
                for row in scan(conn, symbol, args.start, args.end):
                    tags = issues(row)
                    remaining.update(tags)
                    if tags:
                        final_anomalies.append(dict(row, issues=tags))
            save(out / "anomalies-after.json", final_anomalies)
    report.update(
        status=(
            "completed_with_unresolved"
            if any(n for k, n in remaining.items() if k != "legacy_nontrade_placeholder")
            else "completed"
        ),
        before=dict(counts),
        after=dict(remaining),
        seconds=round(time.time_ns() / 1e9 - stamp / 1e6, 2),
    )
    save(out / "report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
