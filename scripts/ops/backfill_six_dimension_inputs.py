"""Initialize an explicit (<=60 sessions) six-dimension public input window."""

from __future__ import annotations

import argparse
import json
import resource
import time
from datetime import date
from pathlib import Path

import duckdb

from aspool.pool import pool_lock
from aspool.sqlite_board_daily import recompute_board_daily
from aspool.sqlite_canonical import canonical_writer
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_six_dimension import save_sessions, six_columns, upgrade_six_dimension_schema
from aspool.sqlite_stock_store import stock_connection


@canonical_writer("six_dimension_init")
def initialize_day(conn, *, day, sessions, replace_snapshot=False):
    if conn.in_transaction:
        raise ValueError("Idle writer required")
    try:
        conn.execute("BEGIN IMMEDIATE")
        save_sessions(conn, sessions)
        result = recompute_daily_summary(conn, trade_date=day)
        result["board_rows"] = recompute_board_daily(
            conn, day=day, replace_snapshot=replace_snapshot
        )
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def backfill(root, *, start, end, upgrade_schema=False, replace_snapshot=False):
    if (
        date.fromisoformat(start).isoformat() != start
        or date.fromisoformat(end).isoformat() != end
        or start > end
    ):
        raise ValueError("Expected explicit ISO dates")
    root = Path(root).resolve()
    started = time.monotonic()
    with duckdb.connect((root / "catalog.duckdb").as_posix(), read_only=True) as calendar:
        days = [
            row[0].isoformat()
            for row in calendar.execute(
                "SELECT trade_date FROM security_calendar WHERE is_open AND trade_date BETWEEN ? "
                "AND ? ORDER BY trade_date",
                [start, end],
            ).fetchall()
        ]
        preceding = [
            row[0].isoformat()
            for row in calendar.execute(
                "SELECT trade_date FROM security_calendar WHERE is_open AND trade_date<? ORDER "
                "BY trade_date DESC LIMIT 5",
                [start],
            ).fetchall()
        ]
    if not days or len(days) > 60:
        raise ValueError("Expected 1..60 confirmed market sessions")
    if upgrade_schema:
        upgrade_six_dimension_schema(root)
    result = dict(
        start=start,
        end=end,
        sessions=days,
        preceding_sessions=sorted(preceding),
        summary_rows=0,
        board_rows=0,
        read_rows=0,
        per_day=[],
        missing_dates=[],
    )
    with stock_connection(root, read_only=False) as conn:
        if not six_columns(conn):
            raise ValueError("Explicit --upgrade-schema required")
        for day in days:
            if not conn.execute(
                "SELECT 1 FROM daily_features WHERE trade_date=? LIMIT 1", (day,)
            ).fetchone():
                result["missing_dates"].append(day)
                continue
            with pool_lock(root, write=True):
                stats = initialize_day(
                    conn,
                    day=day,
                    sessions=sorted(preceding) + days,
                    replace_snapshot=replace_snapshot,
                )
            result["summary_rows"] += stats["changed_rows"]
            result["board_rows"] += stats["board_rows"]
            result["read_rows"] += stats["read_rows"]
            result["per_day"].append(dict(date=day, **stats))
    result.update(
        seconds=round(time.monotonic() - started, 3),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--upgrade-schema", action="store_true")
    parser.add_argument(
        "--replace-snapshot",
        action="store_true",
        help="Explicitly replace retained historical membership",
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve().is_relative_to(args.root.resolve()):
        parser.error("Report must be outside the data root")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    result = backfill(
        args.root,
        start=args.start,
        end=args.end,
        upgrade_schema=args.upgrade_schema,
        replace_snapshot=args.replace_snapshot,
    )
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
