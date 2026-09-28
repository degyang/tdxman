"""Apply a finite daily source payload to the SQLite stock development store.

The source payload is {"bars": [...], "dated_facts": [...]}, using canonical
symbol, ISO trade_date, unadjusted CNY prices and the existing pool field units.
Omitted fields retain their value; NULL is an explicit source withdrawal.
"""

from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

from derive_stocks_sqlite import catalog_inputs

from aspool.sqlite_daily_update import apply_daily_changes, daily_window
from aspool.sqlite_stock_store import stock_connection


def apply_payload(root, payload, *, as_of, start=None, end=None):
    calendar, _ = catalog_inputs(Path(root))
    sessions = [day for day, opened in calendar.items() if opened]
    window = daily_window(sessions, as_of=as_of, start=start, end=end)
    if payload.keys() - {"bars", "dated_facts", "factor_extensions"}:
        raise ValueError("Only bars, dated_facts and verified factor_extensions are accepted")
    for records in (payload.get("bars", ()), payload.get("dated_facts", ())):
        for row in records:
            if row["trade_date"] not in window:
                raise ValueError("Source row outside the explicit update window")
    for extension in payload.get("factor_extensions", ()):
        if extension["verified_end"] > window[-1]:
            raise ValueError("Factor coverage exceeds the requested update window")
    with stock_connection(root, read_only=False) as conn:
        result = apply_daily_changes(
            conn,
            bars=payload.get("bars", ()),
            dated_facts=payload.get("dated_facts", ()),
            factor_extensions=payload.get("factor_extensions", ()),
            market_sessions=sessions,
        )
    return dict(result, requested_sessions=window)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--baostock", action="store_true")
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()
    if args.baostock:
        if not args.symbol:
            parser.error("Online sync needs explicit --symbol selections")
        from aspool.sqlite_daily_sync import (
            fetch_tdx_action_interval,
            refresh_source_calendar,
            sync_baostock_daily,
        )
        from tdxman.baostock import BaostockClient
        from tdxman.client import TdxClient

        with ExitStack() as stack:
            client = stack.enter_context(BaostockClient())
            calendar_end = args.end or args.as_of
            calendar_start = (
                args.start or (date.fromisoformat(calendar_end) - timedelta(days=30)).isoformat()
            )
            calendar_result = refresh_source_calendar(
                args.root,
                client=client,
                start=calendar_start,
                end=calendar_end,
            )
            calendar, _ = catalog_inputs(args.root)
            action_client = None

            def fetch_actions(symbol, start, end):
                global action_client
                if action_client is None:
                    action_client = stack.enter_context(
                        TdxClient.from_best_host(heartbeat_interval=0)
                    )
                return fetch_tdx_action_interval(action_client, symbol, start, end)

            result = sync_baostock_daily(
                args.root,
                client=client,
                symbols=args.symbol,
                market_sessions=[d for d, opened in calendar.items() if opened],
                as_of=args.as_of,
                start=args.start,
                end=args.end,
                action_fetcher=fetch_actions,
            )
            result["calendar"] = calendar_result
    else:
        if args.input.stat().st_size > 64 * 1024 * 1024:
            parser.error("Source payload exceeds 64 MiB; split it outside the writer")
        payload = json.loads(args.input.read_text())
        result = apply_payload(
            args.root,
            payload,
            as_of=args.as_of,
            start=args.start,
            end=args.end,
        )
    print(json.dumps(result, ensure_ascii=False))
