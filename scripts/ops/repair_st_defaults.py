"""Repair an explicit year-sized window of missing ST selections and dependent limits."""

import argparse
import json
import time
from datetime import date
from pathlib import Path

from aspool.pool import pool_lock
from aspool.sqlite_canonical import canonical_operation
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_st_repair import repair_symbol_st
from aspool.sqlite_stock_store import stock_connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--all-existing",
        action="store_true",
        help="Recompute every existing row in this explicit window",
    )
    args = parser.parse_args()
    span = (date.fromisoformat(args.end) - date.fromisoformat(args.start)).days
    if not 0 <= span <= 366:
        parser.error("Use an explicit window of at most 366 calendar days")
    previous = json.loads(args.report.read_text()) if args.report.exists() else {}
    if previous.get("status") == "completed":
        previous = {}
    resume_dates = (
        previous.get("affected_dates", [])
        if (
            previous.get("root") == str(args.root.resolve())
            and previous.get("start") == args.start
            and previous.get("end") == args.end
        )
        else []
    )
    report = dict(
        root=str(args.root.resolve()),
        start=args.start,
        end=args.end,
        status="running",
        all_existing=args.all_existing,
        symbols=0,
        selected_rows=0,
        processed_rows=0,
        reference_rows=0,
        feature_rows=0,
        summary_rows=0,
        board_rows=0,
    )
    started = time.monotonic()

    def save(*, announce=True):
        report["seconds"] = round(time.monotonic() - started, 3)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.report)
        if announce:
            print(json.dumps(report), flush=True)

    try:
        with pool_lock(args.root, write=True), stock_connection(args.root, read_only=False) as conn:
            conn.execute("PRAGMA cache_size=-262144")
            conn.execute("PRAGMA features.cache_size=-524288")
            if conn.execute(
                "SELECT 1 FROM market_daily_summary WHERE frequency IN ('W','M') "
                "AND period_start<=? AND period_end>=?",
                (args.end, args.start),
            ).fetchone():
                raise ValueError("Existing W/M summaries require a period writer")
            condition = (
                ""
                if args.all_existing
                else ("AND (limit_reason='missing_st' OR is_st_source='assumed:not_st') ")
            )
            targets = conn.execute(
                "SELECT symbol,min(trade_date),max(trade_date) FROM daily_features "
                "WHERE trade_date BETWEEN ? AND ? " + condition + "GROUP BY symbol ORDER BY symbol",
                (args.start, args.end),
            ).fetchall()
            # Include prior successful selections so an interrupted run can repair summaries.
            days = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT trade_date FROM daily_features "
                    "WHERE trade_date BETWEEN ? AND ? " + condition,
                    (args.start, args.end),
                )
            }
            days.update(resume_dates)
            changed_days = set(previous.get("changed_input_dates", resume_dates))
            board_dependencies = {
                day: set(symbols) for day, symbols in previous.get("board_dependencies", {}).items()
            }
            report["target_symbols"] = len(targets)
            save()
            with canonical_operation(conn, "st_default_repair"):
                for symbol, start, end in targets:
                    conn.execute("BEGIN IMMEDIATE")
                    result = repair_symbol_st(
                        conn,
                        symbol=symbol,
                        start=start,
                        end=end,
                        recompute_existing=args.all_existing,
                    )
                    days.update(result["changed_dates"])
                    changed_days.update(result["changed_dates"])
                    for day in result.get("board_changed_dates", []):
                        board_dependencies.setdefault(day, set()).add(symbol)
                    # Persist dependencies before commit; rolled-back dates are safe to revisit.
                    report["affected_dates"] = sorted(days)
                    report["changed_input_dates"] = sorted(changed_days)
                    report["board_dependencies"] = {
                        day: sorted(symbols) for day, symbols in board_dependencies.items()
                    }
                    save(announce=False)
                    conn.commit()
                    report["symbols"] += 1
                    report["selected_rows"] += result["selected_rows"]
                    report["reference_rows"] += result.get("reference_rows", 0)
                    report["feature_rows"] += result.get("changed_rows", 0)
                    report["processed_rows"] += result.get("processed_rows", 0)
                    if report["symbols"] % 100 == 0:
                        save()
                for index, day in enumerate(sorted(days)):
                    if conn.execute(
                        "SELECT 1 FROM market_daily_summary WHERE frequency IN ('W','M') "
                        "AND period_start<=? AND period_end>=?",
                        (day, day),
                    ).fetchone():
                        raise ValueError("Existing W/M summaries require a period writer")
                    conn.execute("BEGIN IMMEDIATE")
                    stat = recompute_daily_summary(
                        conn,
                        trade_date=day,
                        inputs_changed=day in changed_days,
                    )
                    if (
                        day in board_dependencies
                        and conn.execute("PRAGMA table_info(board_daily)").fetchone()
                    ):
                        from aspool.sqlite_board_daily import recompute_board_daily

                        report["board_rows"] += recompute_board_daily(
                            conn,
                            day=day,
                            changed_symbols=board_dependencies[day],
                        )
                    conn.commit()
                    report["summary_rows"] += stat["changed_rows"]
                    report["current_summary_date"] = day
                    if index % 20 == 0:
                        save()
            report["affected_dates"] = sorted(days)
        report["status"] = "completed"
        save()
    except BaseException as exc:
        report.update(status="failed", error=repr(exc))
        save()
        raise


if __name__ == "__main__":
    main()
