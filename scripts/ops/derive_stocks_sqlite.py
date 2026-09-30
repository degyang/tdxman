"""Explicit, resumable initial derivation of an isolated migrated stock database.

Each stock commits independently. Daily summaries are built only after every
stock succeeds. The JSONL checkpoint is an ops artifact, never a business table.
"""

from __future__ import annotations

import argparse
import bisect
import json
import time
from pathlib import Path

import duckdb

from aspool.sqlite_daily_derived import DERIVED_COLUMNS, classify_trading, recompute_symbol_features
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_reference_factors import (
    build_selected_factors,
    select_is_st,
    select_reference_pre_close,
)
from aspool.sqlite_stock_store import stock_connection


def catalog_inputs(root):
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as catalog:
        calendar = {
            d.isoformat(): flag
            for d, flag in catalog.execute(
                "SELECT trade_date,is_open FROM security_calendar ORDER BY trade_date"
            ).fetchall()
        }
        listings = {
            s: d.isoformat()
            for s, d in catalog.execute(
                "SELECT symbol,listing_date FROM security_lifecycle WHERE listing_date IS NOT NULL"
            ).fetchall()
        }
    return calendar, listings


def derive_symbol(conn, symbol, *, calendar, listing=None):
    """Read one stock, never the all-market history, and replace derived values."""
    cur = conn.execute(
        "SELECT f.*,b.trade_date AS bar_date,b.open,b.high,b.low,b.close,b.amount,b.volume,"
        "b.name,b.name_as_of FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date) "
        "WHERE f.symbol=? ORDER BY f.trade_date",
        (symbol,),
    )
    columns = [x[0] for x in cur.description]
    rows = [dict(zip(columns, row)) for row in cur]
    if not rows:
        return dict(symbol=symbol, rows=0, factors=0)
    anchors = [
        dict(effective_date=d, source=s, source_cumulative_factor=v)
        for d, s, v in conn.execute(
            "SELECT effective_date,source,source_cumulative_factor FROM corporate_actions "
            "WHERE symbol=? AND record_kind='factor_anchor' ORDER BY effective_date",
            (symbol,),
        )
    ]
    # Stored anchors are already cumulative. No anchors is unknown, not factor 1.
    factors = (
        build_selected_factors(anchors=anchors, through=rows[-1]["trade_date"]) if anchors else []
    )
    factor_dates = [f["effective_date"] for f in factors]

    def factor_at(day):
        pos = bisect.bisect_right(factor_dates, day) - 1
        if pos >= 0 and day <= factors[pos]["valid_through"]:
            return factors[pos]["cumulative_factor"]
        return None

    conn.execute("BEGIN IMMEDIATE")
    try:
        stamp = max(time.time_ns() // 1000, max(row["updated_at"] for row in rows) + 1)
        for f in factors:
            conn.execute(
                "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
                "event_factor,cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
                "VALUES (?,?,'factor','selected:source_cumulative_factor','selected',?,?,?,?,?,?) "
                "ON CONFLICT(symbol,effective_date,record_kind,source,source_key) DO UPDATE SET "
                "event_factor=excluded.event_factor,cumulative_factor=excluded.cumulative_factor,"
                "valid_from=excluded.valid_from,valid_through=excluded.valid_through,"
                "factor_basis=excluded.factor_basis,updated_at=excluded.updated_at "
                "WHERE event_factor IS NOT excluded.event_factor "
                "OR cumulative_factor IS NOT excluded.cumulative_factor "
                "OR valid_through IS NOT excluded.valid_through "
                "OR factor_basis IS NOT excluded.factor_basis",
                (
                    symbol,
                    f["effective_date"],
                    f["event_factor"],
                    f["cumulative_factor"],
                    f["valid_from"],
                    f["valid_through"],
                    f["factor_basis"],
                    stamp,
                ),
            )
        previous = None
        known_ages = {}
        for index, row in enumerate(rows):
            day = row["trade_date"]
            if day not in calendar or not calendar[day]:
                raise ValueError(f"{symbol} {day}: source date is not a confirmed market session")
            status = classify_trading(row)
            raw_source = row["source_pre_close_source"] or ""
            direct = (
                [(row["source_pre_close"], raw_source)]
                if raw_source and not raw_source.startswith("raw_fallback:")
                else []
            )
            pre, pre_source = select_reference_pre_close(
                dated=direct,
                raw_candidate=(row["source_pre_close"], row["source_pre_close_source"]),
            )
            if not direct and previous:
                now_factor, before_factor = factor_at(day), factor_at(previous[0])
                if now_factor and before_factor:
                    pre = previous[1] * before_factor / now_factor
                    pre_source = "derived:previous_close+selected_factors"
            st, st_source, name_day = select_is_st(
                dated=[(row["source_is_st"], row["source_is_st_source"])],
                name=row["name"],
                name_as_of=row["name_as_of"],
                trade_date=day,
            )
            values = (pre, pre_source, st, st_source, name_day)
            old = tuple(
                row[key]
                for key in (
                    "pre_close",
                    "pre_close_source",
                    "is_st",
                    "is_st_source",
                    "is_st_name_date",
                )
            )
            if values != old:
                blank = dict.fromkeys(DERIVED_COLUMNS)
                blank["calc_status"] = status
                if status == "INVALID":
                    blank.update(limit_status="INVALID", streak_known=0)
                conn.execute(
                    "UPDATE daily_features SET pre_close=?,pre_close_source=?,is_st=?,"
                    "is_st_source=?,is_st_name_date=?,"
                    + ",".join(k + "=?" for k in blank)
                    + ",updated_at=? WHERE symbol=? AND trade_date=?",
                    (*values, *blank.values(), stamp, symbol, day),
                )
            if status == "TRADED":
                previous = (day, row["close"])
            if listing and index < 20:
                # Only the initial few observed rows need exact IPO age. Require
                # natural-day coverage, not just a list of visible open days.
                from datetime import date, timedelta

                start, end = date.fromisoformat(listing), date.fromisoformat(day)
                if start <= end:
                    span = [
                        (start + timedelta(days=i)).isoformat()
                        for i in range((end - start).days + 1)
                    ]
                    if all(d in calendar and calendar[d] is not None for d in span):
                        known_ages[day] = sum(calendar[d] for d in span)
        result = recompute_symbol_features(
            conn,
            symbol=symbol,
            start=rows[0]["trade_date"],
            end=rows[-1]["trade_date"],
            listed_days=known_ages,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return dict(symbol=symbol, rows=len(rows), factors=len(factors), **result)


def run(root, report, symbols=(), resume=False):
    root, report = Path(root).resolve(), Path(report).resolve()
    from aspool.platform_v2 import layout_version

    if layout_version(root) >= 2:
        raise ValueError(
            "Legacy development derive is retired for production layouts; "
            "use the canonical update/sync writer or an explicit storage migration"
        )
    report.parent.mkdir(parents=True, exist_ok=True)
    if report.exists() and not resume:
        raise ValueError("Report exists; choose a new path or explicitly resume")
    prior = (
        [json.loads(line) for line in report.read_text().splitlines()] if report.exists() else []
    )
    if prior and any(row.get("root") != str(root) for row in prior):
        raise ValueError("Checkpoint root mismatch")
    completed = {r["symbol"] for r in prior if r.get("kind") == "stock"}
    done_dates = {r["date"] for r in prior if r.get("kind") == "summary"}
    calendar, listings = catalog_inputs(root)
    started = time.monotonic()
    with stock_connection(root, read_only=False) as conn, report.open("a", buffering=1) as output:
        # Date cross-sections revisit B-tree paths for thousands of securities.
        # A bounded maintenance-only cache avoids repeated mounted-disk reads;
        # normal business connections retain their smaller cache setting.
        conn.execute("PRAGMA cache_size=-262144")

        def record(kind, **values):
            item = dict(
                root=str(root), kind=kind, elapsed_s=round(time.monotonic() - started, 3), **values
            )
            line = json.dumps(item, ensure_ascii=False)
            output.write(line + "\n")
            print(line, flush=True)

        universe = list(symbols)
        if not universe:
            last = ""
            while True:
                item = conn.execute(
                    "SELECT symbol FROM daily_features WHERE symbol>? ORDER BY symbol LIMIT 1",
                    (last,),
                ).fetchone()
                if not item:
                    break
                last = item[0]
                universe.append(last)
        failures = []
        for symbol in universe:
            if symbol in completed:
                continue
            try:
                result = derive_symbol(
                    conn, symbol, calendar=calendar, listing=listings.get(symbol)
                )
            except (ValueError, ArithmeticError) as exc:
                failures.append(symbol)
                record("error", symbol=symbol, error=str(exc))
                continue
            result.pop("changed_dates", None)
            record("stock", **result)
        # A selected-stock diagnostic never publishes a partial-market summary.
        if not symbols and not failures:
            first = conn.execute(
                "SELECT trade_date FROM daily_features ORDER BY trade_date LIMIT 1"
            ).fetchone()[0]
            last = conn.execute(
                "SELECT trade_date FROM daily_features ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()[0]
            days = [
                day
                for day in sorted(calendar)
                if first <= day <= last
                and calendar[day]
                and conn.execute(
                    "SELECT 1 FROM daily_features WHERE trade_date=? LIMIT 1", (day,)
                ).fetchone()
            ]
            for day in days:
                if day in done_dates:
                    continue
                conn.execute("BEGIN IMMEDIATE")
                result = recompute_daily_summary(conn, trade_date=day)
                conn.commit()
                record("summary", date=day, **result)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        record(
            "failed" if failures else "complete",
            stocks=len(universe),
            summaries=not bool(symbols) and not failures,
            failed_symbols=failures,
        )
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.root, args.report, args.symbol, args.resume)
