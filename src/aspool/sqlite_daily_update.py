"""Local daily source changes and their dependent calculations in one transaction.

Fetching happens before this writer. An empty response never deletes stored rows.
Full-history imports belong to explicit maintenance, not this business entrypoint.
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from collections import defaultdict
from datetime import date

from .api_contract import DataPoolError
from .sqlite_daily_derived import (
    DERIVED_COLUMNS,
    _factor_at,
    classify_trading,
    recompute_symbol_features,
)
from .sqlite_market_summary import recompute_daily_summary

FACT_FIELDS = frozenset(
    (
        "source_pre_close",
        "source_pre_close_source",
        "source_is_st",
        "source_is_st_source",
        "trading_status",
        "trading_status_source",
    )
)
PRICE_FIELDS = frozenset(("open", "high", "low", "close"))
REFERENCE_FIELDS = frozenset(("name", "name_as_of", "name_source"))


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected ISO date")
    return value


def daily_window(market_sessions, *, as_of, start=None, end=None):
    """Default to five confirmed sessions; a repair always specifies both ends."""
    as_of = _day(as_of)
    days = sorted({_day(day) for day in market_sessions if day <= as_of})
    if (start is None) != (end is None):
        raise ValueError("Repair requires start and end")
    if start is not None:
        start, end = _day(start), _day(end)
        if start > end or end > as_of:
            raise ValueError("Invalid repair range")
        days = [day for day in days if start <= day <= end]
    else:
        days = days[-5:]
    if not days or len(days) > 10:
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Expected 1..10 input sessions")
    return days


def _read(conn, table, key):
    cursor = conn.execute(f"SELECT * FROM {table} WHERE symbol=? AND trade_date=?", key)
    row = cursor.fetchone()
    return dict(zip([item[0] for item in cursor.description], row)) if row else {}


def _inputs(rows, allowed):
    merged = {}
    for index, original in enumerate(rows):
        if index >= 100_000:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Too many source records")
        row = dict(original)
        symbol, day = row.pop("symbol"), _day(row.pop("trade_date"))
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise ValueError("Expected canonical stock symbol")
        if row.keys() - allowed:
            raise ValueError(f"Unsupported source fields: {sorted(row.keys() - allowed)}")
        for value in row.values():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Non-finite source value")
        prior = merged.get((symbol, day), {})
        for value_field, source_field in (
            ("source_pre_close", "source_pre_close_source"),
            ("source_is_st", "source_is_st_source"),
            ("trading_status", "trading_status_source"),
        ):
            source = row.get(source_field)
            if source and source.startswith(("derived:", "limit_derived:")):
                raise ValueError("Computed values cannot be submitted as source facts")
            if (
                value_field in row
                and prior.get(value_field) is not None
                and row[value_field] != prior[value_field]
                and source
                and prior.get(source_field)
                and source != prior[source_field]
            ):
                raise DataPoolError(
                    "SOURCE_CONFLICT", f"Conflicting dated source inputs: {symbol} {day}"
                )
        # Like tick's keep-last merge, repeated keys are deterministic. Omitted
        # columns retain their earlier values; explicit NULL is a correction.
        merged.setdefault((symbol, day), {}).update(row)
    return merged


def _write(conn, table, key, fields, old):
    delta = {name: value for name, value in fields.items() if old.get(name) != value}
    if old and not delta:
        return set()
    stamp = max(time.time_ns() // 1000, old.get("updated_at", 0) + 1)
    if old:
        conn.execute(
            f"UPDATE {table} SET "
            + ",".join(name + "=?" for name in delta)
            + ",updated_at=? WHERE symbol=? AND trade_date=?",
            (*delta.values(), stamp, *key),
        )
    else:
        values = dict(symbol=key[0], trade_date=key[1], **fields, updated_at=stamp)
        conn.execute(
            f"INSERT INTO {table} ("
            + ",".join(values)
            + ") VALUES ("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )
    return set(delta) if old else set(fields) | {"_insert"}


def apply_daily_changes(
    conn: sqlite3.Connection,
    *,
    bars=(),
    dated_facts=(),
    factor_extensions=(),
    market_sessions,
    listed_days=None,
    fill_missing_metrics=False,
    merge_event_revisions=False,
) -> dict:
    """Apply at most ten source dates, propagating actual dependencies atomically.

    Source adapters provide canonical units and dated provenance. This function
    owns BEGIN/COMMIT; an active caller transaction is rejected. No batch IDs,
    bar deletes or automatic full-history repair are accepted. Verified event
    merges can revise a bounded factor suffix when explicitly enabled.
    A missing required bootstrap or an excessive suffix fails without writes.
    """
    if conn.in_transaction:
        raise ValueError("Writer requires an idle connection")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_bars)")}
    incoming_bars = _inputs(bars, columns - {"symbol", "trade_date", "updated_at"})
    incoming_facts = _inputs(dated_facts, FACT_FIELDS)
    extensions = list(factor_extensions)
    if len(extensions) > 500 or len({item["symbol"] for item in extensions}) != len(extensions):
        raise ValueError("Expected at most one factor extension per symbol, up to 500 symbols")
    keys = incoming_bars.keys() | incoming_facts.keys()
    sessions = {_day(day) for day in market_sessions}
    input_dates = {day for _, day in keys}
    if len(input_dates) > 10 or not input_dates <= sessions or len(keys) > 100_000:
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Source range outside session budget")
    result = dict(
        changed_rows=0,
        changed_metric_rows=0,
        changed_factor_rows=0,
        affected_sessions=[],
        recomputed_feature_rows=0,
        changed_feature_rows=0,
        summary_rows=0,
        read_rows=0,
    )
    if not keys and not extensions:
        return dict(result, elapsed_ms=0)
    # Import only when used so pure window selection remains independent.
    from .sqlite_event_update import SymbolUpdateError, merge_recent_events
    from .sqlite_reference_factors import advance_factor_coverage, update_reference_factors

    started = time.monotonic()
    deadline = started + 30
    old_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute("PRAGMA busy_timeout=30000")
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    from .sqlite_summary_quality import QUALITY_FIELDS, quality_columns

    with_quality = set(QUALITY_FIELDS) <= quality_columns(conn)
    affected = set()
    derived = defaultdict(set)
    close_dependencies = defaultdict(set)
    ma_dependencies = set()
    volume_dependencies = defaultdict(set)
    volume_propagation = defaultdict(set)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for index, key in enumerate(sorted(keys)):
            if index % 128 == 0 and time.monotonic() >= deadline:
                raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Writer deadline exceeded")
            symbol, day = key
            old_bar, old_fact = _read(conn, "daily_bars", key), _read(conn, "daily_features", key)
            before = dict(old_bar, **{k: v for k, v in old_fact.items() if k not in old_bar})
            before["bar_date"] = day if old_bar else None
            try:
                old_status = classify_trading(before)
            except DataPoolError as exc:
                if exc.code != "SOURCE_CONFLICT":
                    raise
                # A historical contradictory source row is invalid input, but
                # must remain repairable by a correcting source observation.
                old_status = "INVALID"
            bar_delta = (
                _write(conn, "daily_bars", key, incoming_bars[key], old_bar)
                if key in incoming_bars
                else set()
            )
            facts = incoming_facts.get(key, {})
            for value_field, source_field in (
                ("source_pre_close", "source_pre_close_source"),
                ("source_is_st", "source_is_st_source"),
                ("trading_status", "trading_status_source"),
            ):
                if value_field not in facts:
                    continue
                old_source = old_fact.get(source_field)
                source = facts.get(source_field, old_source)
                if not source:
                    raise ValueError(f"{value_field} needs dated provenance")
                if (
                    old_fact.get(value_field) is not None
                    and old_source != source
                    and old_fact[value_field] != facts[value_field]
                    and old_source
                    and not old_source.startswith("raw_fallback:")
                    and not (
                        value_field == "source_is_st"
                        and {old_source, source} <= {"tdxman:quote", "tdxman:directory"}
                    )
                ):
                    error = DataPoolError(
                        "SOURCE_CONFLICT",
                        f"Conflicting dated source correction: {symbol} {day} {value_field}",
                    )
                    if merge_event_revisions:
                        raise SymbolUpdateError(symbol, error)
                    raise error
            if not old_fact:
                facts = dict(facts, calc_status="TRADED")
            fact_delta = _write(conn, "daily_features", key, facts, old_fact)
            if (
                fill_missing_metrics
                and "volume" in incoming_bars.get(key, {})
                and (old_bar.get("vol_ratio") is None or old_bar.get("turnover_rate") is None)
            ):
                # An unchanged K-line can still need its first metric calculation.
                volume_dependencies[symbol].add(day)
            listing_ready = old_fact.get("limit_reason") == "missing_listing_date" and day in (
                listed_days or {}
            ).get(symbol, {})
            if not bar_delta and not fact_delta and not listing_ready:
                continue
            result["changed_rows"] += bool(bar_delta) + bool(fact_delta)
            if result["changed_rows"] > 100_000:
                raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Too many changed source rows")
            affected.add(day)
            after_bar, after_fact = (
                _read(conn, "daily_bars", key),
                _read(conn, "daily_features", key),
            )
            after = dict(after_bar, **{k: v for k, v in after_fact.items() if k not in after_bar})
            after["bar_date"] = day if after_bar else None
            try:
                status = classify_trading(after)
            except DataPoolError as exc:
                if merge_event_revisions:
                    raise SymbolUpdateError(symbol, exc) from exc
                raise
            identity_changed = status != old_status or "_insert" in bar_delta
            if (
                bar_delta & (PRICE_FIELDS | REFERENCE_FIELDS)
                or fact_delta
                or identity_changed
                or listing_ready
                or after_fact.get("limit_status") is None
                and status == "TRADED"
            ):
                derived[symbol].add(day)
            if "close" in bar_delta or identity_changed:
                close_dependencies[symbol].add(day)
                ma_dependencies.add(symbol)
            if bar_delta & {"volume", "float_share", "float_share_source"} or identity_changed:
                volume_dependencies[symbol].add(day)
            if "volume" in bar_delta or identity_changed:
                volume_propagation[symbol].add(day)

        from .sqlite_volume_metrics import recompute_volume_metrics

        for symbol, days in volume_dependencies.items():
            changed_days, reads = recompute_volume_metrics(
                conn, symbol, days, propagate_days=volume_propagation[symbol]
            )
            affected.update(changed_days)
            result["read_rows"] += reads
            result["changed_metric_rows"] += len(changed_days)

        for symbol, changed_days in close_dependencies.items():
            for day in changed_days:
                # Index seek to the next actual valid bar, skipping suspended
                # placeholders and invalid source rows without inventing bars.
                next_row = conn.execute(
                    "SELECT b.trade_date FROM daily_bars b LEFT JOIN daily_features f "
                    "USING(symbol,trade_date) WHERE b.symbol=? AND b.trade_date>? "
                    "AND coalesce(f.trading_status,'') NOT IN ('SUSPENDED','停牌') "
                    "AND b.low>0 AND b.low<=min(b.open,b.close) "
                    "AND max(b.open,b.close)<=b.high ORDER BY b.trade_date LIMIT 1",
                    (symbol, day),
                ).fetchone()
                if next_row:
                    derived[symbol].add(next_row[0])
                    if conn.execute(
                        "SELECT 1 FROM corporate_actions WHERE symbol=? AND record_kind='factor' "
                        "AND source LIKE 'derived:%' AND effective_date>? "
                        "AND effective_date<=? LIMIT 1",
                        (symbol, day, next_row[0]),
                    ).fetchone():
                        raise DataPoolError(
                            "FACTOR_REBUILD_REQUIRED",
                            "Changed event reference needs the explicit factor maintenance writer",
                        )
        # The source adapter has already fetched and verified event coverage.
        # Extend factors after raw writes so new ex-dates can use prior new bars,
        # but before references/MA calculations; every dependency commits together.
        for extension in extensions:
            if merge_event_revisions:
                try:
                    stats = merge_recent_events(conn, **extension)
                except ValueError as exc:
                    raise SymbolUpdateError(extension["symbol"], exc) from exc
            else:
                stats = advance_factor_coverage(conn, **extension)
            result["changed_factor_rows"] += stats["changed_rows"]
            if not stats["changed_rows"] or stats["affected_from"] is None:
                continue
            symbol = extension["symbol"]
            rows = conn.execute(
                "SELECT trade_date FROM daily_features WHERE symbol=? "
                "AND trade_date BETWEEN ? AND ? ORDER BY trade_date LIMIT 62",
                (symbol, stats["affected_from"], stats["affected_through"]),
            ).fetchall()
            if len(rows) > 60:
                raise DataPoolError(
                    "LOCAL_UPDATE_BUDGET_EXCEEDED", "Factor extension affects too many dates"
                )
            if not rows:
                continue
            derived[symbol].update(row[0] for row in rows)
            affected.update(row[0] for row in rows)
            ma_dependencies.add(symbol)

        for symbol, days in derived.items():
            # Reference withdrawal must not transiently violate KNOWN's
            # constraints. Dependents are restored before commit or rolled back.
            stamp_floors = {}
            for day in days:
                row = _read(conn, "daily_bars", (symbol, day))
                row.update(_read(conn, "daily_features", (symbol, day)))
                stamp_floors[day] = row["updated_at"]
                row["bar_date"] = day if "close" in row else None
                status = classify_trading(row)
                empty = dict.fromkeys(DERIVED_COLUMNS)
                empty["calc_status"] = status
                if status == "INVALID":
                    empty.update(limit_status="INVALID", streak_known=0)
                conn.execute(
                    "UPDATE daily_features SET "
                    + ",".join(field + "=?" for field in DERIVED_COLUMNS)
                    + " WHERE symbol=? AND trade_date=?",
                    (*empty.values(), symbol, day),
                )
            update_reference_factors(conn, symbol=symbol, dates=sorted(days))
            # An established selected factor interval is sufficient to adjust
            # the previous trading close; sparse absence of events alone is not.
            for day in sorted(days):
                conn.execute(
                    "UPDATE daily_features SET updated_at=? WHERE symbol=? AND trade_date=? "
                    "AND updated_at<?",
                    (stamp_floors[day], symbol, day, stamp_floors[day]),
                )
                fact = _read(conn, "daily_features", (symbol, day))
                source = fact.get("source_pre_close_source") or ""
                if (
                    fact.get("source_pre_close") is not None
                    and source
                    and not source.startswith(("raw_fallback:", "derived:"))
                ):
                    continue
                previous = conn.execute(
                    "SELECT b.trade_date,b.close FROM daily_bars b JOIN daily_features f "
                    "USING(symbol,trade_date) WHERE b.symbol=? AND b.trade_date<? "
                    "AND f.calc_status='TRADED' AND b.low>0 "
                    "AND b.low<=min(b.open,b.close) AND max(b.open,b.close)<=b.high "
                    "ORDER BY b.trade_date DESC LIMIT 1",
                    (symbol, day),
                ).fetchone()
                current_factor = _factor_at(conn, symbol, day)
                prior_factor = _factor_at(conn, symbol, previous[0]) if previous else None
                if prior_factor and current_factor:
                    conn.execute(
                        "UPDATE daily_features SET pre_close=?,pre_close_source=? "
                        "WHERE symbol=? AND trade_date=?",
                        (
                            previous[1] * prior_factor / current_factor,
                            "derived:previous_close+selected_factors",
                            symbol,
                            day,
                        ),
                    )
            stats = recompute_symbol_features(
                conn,
                symbol=symbol,
                start=min(days),
                end=max(days),
                propagate=True,
                successor_window=20 if symbol in ma_dependencies else 0,
                listed_days=(listed_days or {}).get(symbol),
                max_rows=250_000 - result["read_rows"],
                max_affected_dates=60,
            )
            result["recomputed_feature_rows"] += stats["processed_rows"]
            result["changed_feature_rows"] += stats["changed_rows"]
            result["read_rows"] += stats["read_rows"]
            affected.update(stats["changed_dates"])
            if with_quality:
                # A changed candidate also affects sessions with no row for this stock.
                for changed_day in stats["promotion_changed_dates"]:
                    following = conn.execute(
                        "SELECT trade_date FROM daily_features WHERE symbol=? AND trade_date>? "
                        "AND calc_status<>'NO_TRADE' ORDER BY trade_date LIMIT 1",
                        (symbol, changed_day),
                    ).fetchone()
                    affected.update(
                        row[0]
                        for row in conn.execute(
                            "SELECT DISTINCT period_key FROM market_daily_summary "
                            "WHERE frequency='D' "
                            "AND period_key>? AND period_key<=? ORDER BY period_key LIMIT 61",
                            (changed_day, following[0] if following else "9999-12-31"),
                        )
                    )
        if len(affected) > 60 or not affected <= sessions:
            raise DataPoolError(
                "LOCAL_UPDATE_BUDGET_EXCEEDED", "Affected dates exceed supplied calendar"
            )
        for day in sorted(affected):
            if conn.execute(
                "SELECT 1 FROM market_daily_summary WHERE frequency IN ('W','M') "
                "AND period_start<=? AND period_end>=? LIMIT 1",
                (day, day),
            ).fetchone():
                raise DataPoolError(
                    "PERIOD_UPDATE_NOT_READY", "Existing period summaries require a period writer"
                )
            stats = recompute_daily_summary(conn, trade_date=day, inputs_changed=True)
            result["summary_rows"] += stats["changed_rows"]
            result["read_rows"] += stats["read_rows"]
        if time.monotonic() >= deadline:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Writer deadline exceeded")
        conn.commit()
    except Exception as exc:
        conn.set_progress_handler(None, 0)
        conn.rollback()
        if isinstance(exc, sqlite3.OperationalError) and time.monotonic() >= deadline:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Writer deadline exceeded") from exc
        raise
    finally:
        conn.set_progress_handler(None, 0)
        conn.execute(f"PRAGMA busy_timeout={old_timeout}")
    return dict(
        result,
        affected_sessions=sorted(affected),
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )
