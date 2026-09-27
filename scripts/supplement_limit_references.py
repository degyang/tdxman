"""Fetch dated reference/status facts for selected stocks without rewriting bars."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa

from aspool.daily_access import DailyStorage
from aspool.pool import writer
from aspool.security_facts import initialize_facts
from aspool.store import catalog
from tdxman.baostock import BaostockClient
from tdxman.models.enums import Market


def fetch_group(args):
    targets, start, end, output = args
    results = []
    with ExitStack() as stack:
        client = stack.enter_context(BaostockClient(timeout=8))
        for market, code, _ in targets:
            path = output / f"baostock-reference-sample-{code}.parquet"
            if path.exists():
                results.append((market, code, str(path), None))
                continue
            try:
                parts, cursor = [], start
                while cursor <= end:
                    last = min(end, date(cursor.year, 12, 31))
                    parts.append(client.get_daily(
                        Market[market], code, start=cursor, end=last, count=None))
                    cursor = last + timedelta(days=1)
                frame = pd.concat(parts, ignore_index=True)
                frame.to_parquet(path, index=False)
                results.append((market, code, str(path), None))
                print(f"FETCHED {code}.{market} {len(frame)}", flush=True)
            except Exception as exc:
                results.append((market, code, None, str(exc)))
                print(f"FAILED {code}.{market}: {exc}", flush=True)
                # A timed-out protocol stream may contain a partial response.
                # Reconnect before querying a different security.
                try:
                    stack.close()
                except Exception as close_error:
                    print(f"Session cleanup: {close_error}", flush=True)
                client = stack.enter_context(BaostockClient(timeout=8))
    return results


@writer
def publish(root, results, basics, calendar):
    from aspool.limit_events import _mark_stale, _published_dates_from, initialize_limits

    initialize_facts(root)
    initialize_limits(root)
    dates = [pd.read_parquet(path, columns=["date"]).date.min()
             for _, _, path, error in results if not error and path]
    dates = [day for day in dates if pd.notna(day)]
    if dates:
        _mark_stale(root, _published_dates_from(root, min(dates)),
                    "参考价和证券事实已补齐，等待重算")
    report = {"facts": 0, "conflicting_ohlc": 0, "source_errors": [], "symbols": 0}
    with catalog(root) as conn:
        conn.execute("BEGIN")
        try:
            for table, frame in [("security_lifecycle", basics), ("security_calendar", calendar)]:
                conn.register("incoming_facts", pa.Table.from_pandas(frame, preserve_index=False))
                conn.execute(f"INSERT OR REPLACE INTO {table} BY NAME SELECT * FROM incoming_facts")
                conn.unregister("incoming_facts")
            for market, code, path, error in results:
                if error:
                    report["source_errors"].append({"symbol": f"{code}.{market}", "error": error})
                    continue
                stored = {}
                source_frame = pd.read_parquet(path)
                table = DailyStorage(root).read(market, code, source_frame.date.min(),
                                               source_frame.date.max(),
                                               ["trade_date", "open", "high", "low", "close"])
                if table is not None:
                    stored.update({r["trade_date"]: r for r in table.to_pylist()})
                facts = []
                for row in source_frame.to_dict("records"):
                    prior = stored.get(row["date"])
                    matched = prior is not None and all(
                        pd.notna(row.get(k)) and pd.notna(prior.get(k))
                        and abs(float(row[k]) - float(prior[k])) < 0.011
                        for k in ("open", "high", "low", "close")
                    )
                    if not matched and row["trading_status"] != "SUSPENDED":
                        report["conflicting_ohlc"] += 1
                    facts.append(dict(
                        symbol=row["symbol"], trade_date=row["date"],
                        pre_close=row["pre_close"] if matched else None,
                        is_st=row["is_st"], trading_status=row["trading_status"],
                        source="baostock", fetched_at=row["fetched_at"],
                    ))
                if facts:
                    conn.register("incoming_facts", pa.Table.from_pylist(facts))
                    conn.execute("INSERT OR REPLACE INTO security_daily_facts BY NAME "
                                 "SELECT * FROM incoming_facts")
                    conn.unregister("incoming_facts")
                report["facts"] += len(facts)
                report["symbols"] += 1
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    args = parser.parse_args()
    targets = json.loads(args.targets.read_text())
    output = args.targets.parent
    basic_path, calendar_path = (output / "baostock-lifecycle.parquet",
                                 output / "baostock-calendar.parquet")
    if basic_path.exists() and calendar_path.exists():
        basics, calendar = pd.read_parquet(basic_path), pd.read_parquet(calendar_path)
        if max(calendar.trade_date) < args.end:
            raise ValueError("Cached calendar does not cover the requested end")
    else:
        with BaostockClient(timeout=20) as client:
            raw = client._query("query_stock_basic")
            basics = pd.DataFrame([dict(
                symbol=r["code"][3:] + "." + r["code"][:2].upper(),
                listing_date=date.fromisoformat(r["ipoDate"]),
                delisting_date=date.fromisoformat(r["outDate"]) if r["outDate"] else None,
                name=r["code_name"], source="baostock", fetched_at=datetime.now(timezone.utc),
            ) for r in raw if r["type"] == "1" and r["ipoDate"]
                and r["code"][:2] in {"sh", "sz"}])
            calendar = client.get_trade_calendar(date(1990, 12, 19), args.end)
            calendar = calendar.rename(columns={"date": "trade_date"})
        basics.to_parquet(basic_path, index=False)
        calendar.to_parquet(calendar_path, index=False)
    groups = [targets[i::args.workers] for i in range(args.workers)]
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch_group, (g, args.start, args.end, output)): g
                   for g in groups}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                # Preserve completed files even if a session could not reconnect.
                for market, code, _ in futures[future]:
                    path = output / f"baostock-reference-sample-{code}.parquet"
                    results.append((market, code, str(path) if path.exists() else None,
                                    None if path.exists() else str(exc)))
    report = publish(args.root.expanduser().resolve(), results, basics, calendar)
    (output / f"{args.targets.stem}-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
