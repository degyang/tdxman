"""Explicit bounded-window maintenance for additive public summary quality fields."""

import argparse
import json
import resource
import time
from datetime import date
from pathlib import Path

from aspool.platform_v2 import layout_version
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_stock_store import stock_connection
from aspool.sqlite_summary_quality import QUALITY_FIELDS, quality_columns, upgrade_quality_columns
from aspool.sqlite_summary_repair import apply_summary_changes


def backfill(root, *, start, end, upgrade_schema=False):
    if (
        date.fromisoformat(start).isoformat() != start
        or date.fromisoformat(end).isoformat() != end
        or start > end
    ):
        raise ValueError("Expected an explicit ISO date range")
    started = time.monotonic()
    result = dict(
        root=str(Path(root).resolve()), start=start, end=end, days=0, changed_rows=0, read_rows=0
    )
    with stock_connection(root, read_only=False) as conn:
        if upgrade_schema:
            if layout_version(root) == 3:
                if not set(QUALITY_FIELDS) <= quality_columns(conn):
                    raise ValueError(
                        "Canonical schema changes require an explicit storage migration"
                    )
            else:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    upgrade_quality_columns(conn)
        elif not set(QUALITY_FIELDS) <= quality_columns(conn):
            raise ValueError("Quality columns absent; explicit --upgrade-schema is required")
        days = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT period_key FROM market_daily_summary WHERE frequency='D' "
                "AND period_key BETWEEN ? AND ? ORDER BY period_key",
                (start, end),
            )
        ]
        if not days:
            raise ValueError("No precomputed market sessions in this window")
        for day in days:
            if layout_version(root) == 3:
                stats = apply_summary_changes(conn, day=day)
            else:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    stats = recompute_daily_summary(conn, trade_date=day)
            result["days"] += 1
            result["changed_rows"] += stats["changed_rows"]
            result["read_rows"] += stats["read_rows"]
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
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve().is_relative_to(args.root.resolve()):
        parser.error("Report must be outside the data root")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"status": "running"}) + "\n")
    try:
        result = dict(
            status="completed",
            **backfill(
                args.root, start=args.start, end=args.end, upgrade_schema=args.upgrade_schema
            ),
        )
    except Exception as exc:
        args.report.write_text(json.dumps(dict(status="failed", error=str(exc))) + "\n")
        raise
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
