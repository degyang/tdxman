"""Chronological, daily atomic repair of paired source history and limit statistics."""

from __future__ import annotations

import json
from collections import Counter
from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal

from .sqlite_canonical import canonical_writer
from .sqlite_daily_derived import DERIVED_COLUMNS, derive_daily_row, finite
from .sqlite_daily_state import has_real_trade
from .sqlite_daily_update import _read, _write, valuation_patch
from .sqlite_qfq import PRICES, ensure_qfq_schema, paired_reference

ACTIVE = ContextVar("qfq_history_repair_root", default=None)
LIMIT_COLUMNS = [c for c in DERIVED_COLUMNS if c not in ("ma20", "above_ma20")]


@canonical_writer("qfq_audit")
def import_seed(conn, stage, start):
    """Persist predecessor observations too, so a base-only restore can replay."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = 0
        for (payload,) in stage.execute(
            "SELECT payload FROM observations WHERE trade_date<?", (start,)
        ):
            observation = json.loads(payload)
            key = observation["symbol"], observation["trade_date"]
            qfq = dict(
                observation["qfq"],
                source=observation["source"] + ":qfq",
                source_as_of=observation["source_as_of"],
            )
            changed += bool(
                _write(conn, "stock_qfq_bars", key, qfq, _read(conn, "stock_qfq_bars", key))
            )
        conn.commit()
        return changed
    except BaseException:
        conn.rollback()
        raise


def dictionaries(cursor):
    fields = [v[0] for v in cursor.description]
    return [dict(zip(fields, r)) for r in cursor]


def assess_day(conn, day):
    """Separate hard unknowns, explicitly estimated values and source coverage."""
    rows = dictionaries(
        conn.execute(
            "SELECT b.symbol,b.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,"
            "f.trading_status,f.calc_status,f.limit_status,f.limit_reason,f.pre_close_source,"
            "f.is_st_source,f.limit_up_price,f.limit_down_price,"
            "f.close_limit_up,f.close_limit_down,f.touch_limit_up,f.touch_limit_down "
            "FROM daily_bars b LEFT JOIN daily_features f USING(symbol,trade_date) "
            "WHERE b.trade_date=?",
            (day,),
        )
    )
    counts = Counter(existing_rows=len(rows))
    anomalies = []
    for row in rows:
        real = has_real_trade(row)
        if real:
            counts["traded_rows"] += 1
            if row["trading_status"] != "TRADING":
                counts["state_conflicts"] += 1
            if row["calc_status"] != "TRADED" or row["limit_status"] not in ("KNOWN", "NO_LIMIT"):
                counts["uncomputable"] += 1
                anomalies.append(
                    dict(
                        symbol=row["symbol"],
                        date=day,
                        reason=row["limit_reason"] or "missing_enriched",
                        **{k: row[k] for k in PRICES},
                    )
                )
            if row["limit_status"] == "KNOWN" and (
                finite(row["limit_up_price"])
                and row["high"] > row["limit_up_price"] + 0.021
                or finite(row["limit_down_price"])
                and row["low"] < row["limit_down_price"] - 0.021
            ):
                counts["price_rule_conflicts"] += 1
                anomalies.append(
                    dict(symbol=row["symbol"], date=day, reason="price_outside_limits")
                )
        elif row["calc_status"] == "INVALID":
            counts["uncomputable"] += 1
            anomalies.append(dict(symbol=row["symbol"], date=day, reason="invalid_ohlc"))
        if real and str(row["pre_close_source"] or "").startswith("estimated:"):
            counts["estimated_reference"] += 1
        if real and str(row["is_st_source"] or "").startswith("assumed:"):
            counts["assumed_non_st"] += 1
        for key in ("close_limit_up", "close_limit_down", "touch_limit_up", "touch_limit_down"):
            counts[key] += bool(row[key]) and row["calc_status"] == "TRADED"
    for key in (
        "uncomputable",
        "state_conflicts",
        "estimated_reference",
        "assumed_non_st",
        "price_rule_conflicts",
    ):
        counts.setdefault(key, 0)
    missing = conn.execute(
        "SELECT f.symbol FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date) "
        "WHERE f.trade_date=? AND b.symbol IS NULL "
        "AND COALESCE(f.trading_status,'') NOT IN ('NOT_LISTED','SUSPENDED','NO_TRADE')",
        (day,),
    ).fetchall()
    counts["missing_bars"] = len(missing)
    # Beijing securities are outside the existing Regime limit-event scope;
    # retain their missing-data evidence without treating them as unknown limits.
    counts["uncomputable"] += sum(not symbol.endswith(".BJ") for (symbol,) in missing)
    anomalies.extend(dict(symbol=s, date=day, reason="missing_base_bar") for (s,) in missing)
    events = ("close_limit_up", "close_limit_down", "touch_limit_up", "touch_limit_down")
    published = conn.execute(
        "SELECT "
        + ",".join(k + "_count" for k in events)
        + " FROM market_daily_summary WHERE frequency='D' AND period_key=? "
        "AND scope='all_stocks'",
        (day,),
    ).fetchone()
    counts["summary_mismatches"] = (
        sum(counts[k] != value for k, value in zip(events, published)) if published else 1
    )
    counts["qfq_covered_rows"] = (
        conn.execute("SELECT count(*) FROM stock_qfq_bars WHERE trade_date=?", (day,)).fetchone()[0]
        if conn.execute("SELECT 1 FROM main.sqlite_master WHERE name='stock_qfq_bars'").fetchone()
        else 0
    )
    return dict(trade_date=day, **counts), anomalies


@canonical_writer("qfq_audit")
def repair_day(conn, *, day, observations, previous, streaks, ages, listing_dates, calendar=()):
    """Publish one full market day with its source qfq, facts and aggregates."""
    from .sqlite_board_daily import recompute_board_daily
    from .sqlite_market_summary import recompute_daily_summary
    from .sqlite_volume_metrics import recompute_volume_metrics

    bars = {
        r["symbol"]: r
        for r in dictionaries(conn.execute("SELECT * FROM daily_bars WHERE trade_date=?", (day,)))
    }
    facts = {
        r["symbol"]: r
        for r in dictionaries(
            conn.execute("SELECT * FROM daily_features WHERE trade_date=?", (day,))
        )
    }
    sources = {r["symbol"]: r for r in observations}
    report = dict(
        trade_date=day, qfq_rows=0, raw_rows=0, feature_rows=0, summary_rows=0, board_rows=0
    )
    report["price_changed_symbols"] = []
    changed, raw_changed, board_changed = set(), set(), set()
    next_previous, next_streaks, next_ages = {}, {}, {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for symbol in sorted(bars.keys() | sources.keys() | facts.keys()):
            key = (symbol, day)
            bar, fact, observation = (
                bars.get(symbol, {}),
                facts.get(symbol, {}),
                sources.get(symbol),
            )
            qfq = None
            if observation:
                qfq = dict(
                    observation["qfq"],
                    source=observation["source"] + ":qfq",
                    source_as_of=observation["source_as_of"],
                )
                old_qfq = _read(conn, "stock_qfq_bars", key)
                report["qfq_rows"] += bool(_write(conn, "stock_qfq_bars", key, qfq, old_qfq))
                incoming = observation["raw"]
                patch = {}
                if not bar or any(
                    not finite(bar.get(k)) or abs(bar[k] - incoming[k]) > 0.0051 for k in PRICES
                ):
                    patch.update({k: incoming[k] for k in PRICES})
                    patch["ohlcv_source"] = observation["source"] + ":none"
                    report["price_changed_symbols"].append(symbol)
                if finite(incoming.get("vol")) and incoming["vol"] >= 0:
                    if not finite(bar.get("volume")) or abs(bar["volume"] - incoming["vol"]) > max(
                        1, incoming["vol"] * 1e-6
                    ):
                        patch["volume"] = incoming["vol"]
                if finite(incoming.get("amount"), positive=True) and not finite(
                    bar.get("amount"), positive=True
                ):
                    patch["amount"] = incoming["amount"]
                if not bar.get("float_share") and finite(
                    incoming.get("float_shares"), positive=True
                ):
                    patch.update(
                        float_share=incoming["float_shares"] * 10000,
                        float_share_source=observation["source"],
                    )
                if patch:
                    patch.update(valuation_patch(dict(bar, **patch), set(patch)))
                    _write(conn, "daily_bars", key, patch, bar)
                    bar = dict(bar, **patch, symbol=symbol, trade_date=day)
                    report["raw_rows"] += 1
                    raw_changed.add(symbol)
            if not bar:
                listing = listing_dates.get(symbol)
                if listing and day < listing:
                    row = dict(
                        fact,
                        symbol=symbol,
                        trade_date=day,
                        bar_date=None,
                        trading_status="NOT_LISTED",
                    )
                    values, _ = derive_daily_row(row, previous_streak=None)
                    patch = {k: values[k] for k in LIMIT_COLUMNS}
                    patch.update(
                        trading_status="NOT_LISTED",
                        trading_status_source="derived:source_listing_date",
                    )
                    if _write(conn, "daily_features", key, patch, fact):
                        report["feature_rows"] += 1
                        changed.add(symbol)
                continue
            if qfq is None:
                qfq = _read(conn, "stock_qfq_bars", key) or None
            row = {**bar, **fact, "symbol": symbol, "trade_date": day, "bar_date": day}
            # OHLC always belongs to the base bar, never to source feature fields.
            row.update({k: bar.get(k) for k in (*PRICES, "volume", "amount")})
            patch = {}
            if observation and observation.get("facts"):
                observed_status = observation["facts"].get("trading_status")
                if observed_status in ("TRADING", "SUSPENDED"):
                    patch.update(
                        trading_status=observed_status, trading_status_source=observation["source"]
                    )
                for field, value in observation["facts"].items():
                    if value is not None and field in ("pre_close", "is_st"):
                        patch["source_" + field] = value
                        patch["source_" + field + "_source"] = observation["source"]
                row.update(patch)
            real = has_real_trade(row)
            if (
                observation
                and not real
                and bar.get("volume") == 0
                and not patch.get("trading_status")
            ):
                patch.update(
                    trading_status="NO_TRADE", trading_status_source="derived:source_zero_volume"
                )
            if real and row.get("trading_status") != "TRADING":
                patch.update(trading_status="TRADING", trading_status_source="derived:stored_ohlcv")
            st_source = str(row.get("source_is_st_source") or "")
            if (
                row.get("source_is_st") in (0, 1)
                and st_source
                and not st_source.startswith(("raw_fallback:", "assumed:"))
            ):
                patch.update(is_st=row["source_is_st"], is_st_source=st_source)
            elif row.get("is_st") is None:
                patch.update(is_st=0, is_st_source="assumed:not_st")
            previous_bar, previous_qfq = previous.get(symbol, (None, None))
            source = str(row.get("source_pre_close_source") or "")
            if (
                finite(row.get("source_pre_close"), positive=True)
                and source
                and not source.startswith(("raw_fallback:", "derived:", "estimated:"))
            ):
                reference, basis = row["source_pre_close"], source
            elif (
                observation
                or not finite(row.get("pre_close"), positive=True)
                or str(row.get("pre_close_source") or "").startswith(
                    ("estimated:", "derived:paired_qfq")
                )
            ):
                reference, basis = paired_reference(row, qfq, previous_bar, previous_qfq)
            else:
                reference, basis = row.get("pre_close"), row.get("pre_close_source")
            if real and reference is not None:
                rounded = float(
                    Decimal(str(reference)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
                old_reference = row.get("pre_close")
                old_basis = str(row.get("pre_close_source") or "")
                if (
                    finite(old_reference, positive=True)
                    and abs(old_reference - rounded) < 0.0051
                    and old_basis
                    and not old_basis.startswith(("estimated:", "raw_fallback:"))
                ):
                    reference, basis = old_reference, old_basis
                else:
                    reference = rounded
                patch.update(pre_close=reference, pre_close_source=basis)
            row.update(patch)
            age = ages.get(symbol, 0) + int(real)
            listing = listing_dates.get(symbol)
            listed_days = None
            if listing and listing > day:
                listing = None
            # An observed count above five proves the IPO exemption has elapsed;
            # use an exact first observed date only if the directory agrees.
            if listing and calendar:
                from bisect import bisect_left, bisect_right

                listed_days = bisect_right(calendar, day) - bisect_left(calendar, listing)
            elif listing == day:
                listed_days = 1
            values, next_streak = derive_daily_row(
                row,
                previous_streak=streaks.get(symbol),
                observed_sessions=min(age, 6),
                listed_days=listed_days,
            )
            patch.update({k: values[k] for k in LIMIT_COLUMNS})
            # Existing MA20 is not added/redefined by this task. Price corrections
            # are followed by the existing bounded feature calculator below.
            if not fact:
                patch.update(ma20=None, above_ma20=None)
            if _write(conn, "daily_features", key, patch, fact):
                report["feature_rows"] += 1
                changed.add(symbol)
            if symbol in raw_changed or any(
                row.get(k) != fact.get(k) for k in ("pre_close", "is_st", "trading_status")
            ):
                board_changed.add(symbol)
            if real:
                next_previous[symbol] = (dict(row), qfq)
                next_streaks[symbol] = next_streak
                next_ages[symbol] = age
            if symbol in raw_changed:
                recompute_volume_metrics(conn, symbol, [day], propagate_days=[day])
        affected = changed | raw_changed
        if affected:
            if conn.execute(
                "SELECT 1 FROM market_daily_summary WHERE frequency IN ('W','M') AND "
                "period_start<=? AND period_end>=? LIMIT 1",
                (day, day),
            ).fetchone():
                raise ValueError("Historical period summaries need explicit maintenance")
            report["summary_rows"] = recompute_daily_summary(
                conn, trade_date=day, inputs_changed=True
            )["changed_rows"]
            if board_changed:
                report["board_rows"] = recompute_board_daily(
                    conn, day=day, changed_symbols=board_changed
                )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    previous.update(next_previous)
    streaks.update(next_streaks)
    ages.update(next_ages)
    return report


@canonical_writer("qfq_audit")
def repair_price_dependents(conn, symbol, dates, listing_date=None):
    """Reuse existing MA20/limit propagation after a true raw-price correction."""
    from .sqlite_daily_derived import recompute_symbol_features
    from .sqlite_qfq import refresh_references

    following = [
        r[0]
        for r in conn.execute(
            "SELECT trade_date FROM daily_features WHERE symbol=? AND trade_date>? "
            "AND calc_status='TRADED' ORDER BY trade_date LIMIT 20",
            (symbol, max(dates)),
        )
    ]
    through = max([max(dates), *following])
    window = [
        r[0]
        for r in conn.execute(
            "SELECT trade_date FROM daily_features WHERE symbol=? AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date",
            (symbol, min(dates), through),
        )
    ]
    if not window:
        return []
    calendar = [
        r[0] for r in conn.execute("SELECT trade_date FROM market_sessions ORDER BY trade_date")
    ]
    listed = (
        {d: sum(listing_date <= s <= d for s in calendar) for d in window} if listing_date else None
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        refresh_references(conn, symbol, window, allow_fallback=True)
        result = recompute_symbol_features(
            conn,
            symbol=symbol,
            start=window[0],
            end=window[-1],
            propagate=True,
            successor_window=20,
            max_rows=100000,
            max_affected_dates=10000,
            listed_days=listed,
        )
        affected = set(window) | set(result["changed_dates"])
        conn.commit()
        return sorted(affected)
    except BaseException:
        conn.rollback()
        raise


@canonical_writer("qfq_audit")
def publish_dependents(conn, day, symbols):
    from .sqlite_board_daily import recompute_board_daily
    from .sqlite_market_summary import recompute_daily_summary

    conn.execute("BEGIN IMMEDIATE")
    try:
        recompute_daily_summary(conn, trade_date=day, inputs_changed=True)
        recompute_board_daily(conn, day=day, changed_symbols=symbols)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def run(root, out, start, end, *, repair, recovery, threshold=5, stop_requested=None):
    from pathlib import Path

    import duckdb

    from .pool import pool_lock
    from .qfq_audit import save, stage_connection
    from .sqlite_publication import (
        durable_remove,
        durable_write,
        migration_path,
        pending_path,
        recover_publication,
    )
    from .sqlite_stock_store import stock_connection

    if repair:
        if not recovery:
            raise ValueError("--repair needs --recovery with a verified consistent backup")
        manifest = json.loads((recovery / "recovery.json").read_text())
        if not Path(manifest.get("root", "")).samefile(root) or not manifest.get(
            "public_read_verified"
        ):
            raise ValueError("Recovery does not match this pool")
        if any(
            not (recovery / r["name"]).exists() or r["quick_check"] != "ok"
            for r in manifest["files"]
        ):
            raise ValueError("Recovery files incomplete")
    token = ACTIVE.set(str(root)) if repair else None
    recovered_prices = []
    try:
        if repair:
            marker = migration_path(root)
            was_pending = marker.exists()
            if marker.exists():
                old = json.loads(marker.read_text())
                if (
                    old.get("operation") != "qfq-history-audit"
                    or old.get("report_dir") != str(out)
                    or old.get("start") != start
                    or old.get("end") != end
                ):
                    raise ValueError("Another maintenance operation is pending")
            if pending_path(root).exists():
                from .sqlite_publication import validate_intent

                intent = validate_intent(root)
                recovered_prices = [
                    tuple(row["key"])
                    for row in intent["rows"]
                    if row["alias"] == "main" and row["table"] == "daily_bars"
                ]
                recover_publication(root)
            ensure_qfq_schema(root)
        with pool_lock(root, write=repair), stage_connection(out) as stage:
            stage.executemany("INSERT OR IGNORE INTO price_changes VALUES (?,?)", recovered_prices)
            stage.commit()
            if repair:
                durable_write(
                    migration_path(root),
                    dict(operation="qfq-history-audit", report_dir=str(out), start=start, end=end),
                )
            with duckdb.connect(str(root / "catalog.duckdb"), read_only=not repair) as cat:
                columns = [r[0] for r in cat.execute("DESCRIBE securities").fetchall()]
                source_file = out / "listing-source.json"
                if repair and source_file.exists() and "listing_date" in columns:
                    updates = []
                    for row in json.loads(source_file.read_text()):
                        if row.get("ipoDate") and row.get("code"):
                            market, code = row["code"].split(".")
                            updates.append((row["ipoDate"], code + "." + market.upper()))
                    if updates:
                        cat.execute("BEGIN TRANSACTION")
                        cat.executemany(
                            "UPDATE securities SET listing_date=? "
                            "WHERE symbol=? AND listing_date IS NULL",
                            updates,
                        )
                        cat.execute("COMMIT")
                listing_dates = (
                    dict(
                        cat.execute(
                            "SELECT symbol,CAST(listing_date AS VARCHAR) FROM securities"
                        ).fetchall()
                    )
                    if "listing_date" in columns
                    else {}
                )
            with stock_connection(root, read_only=not repair) as conn:
                conn.execute("PRAGMA cache_size=-524288")
                conn.execute("PRAGMA features.cache_size=-1048576")
                days = [
                    r[0]
                    for r in conn.execute(
                        "SELECT trade_date FROM market_sessions WHERE trade_date "
                        "BETWEEN ? AND ? ORDER BY trade_date",
                        (start, end),
                    )
                ]
                previous, streaks, ages = {}, {}, {}
                calendar = [
                    r[0]
                    for r in conn.execute(
                        "SELECT trade_date FROM market_sessions ORDER BY trade_date"
                    )
                ]
                if not days:
                    raise ValueError(
                        "No market sessions in the requested window; audit is incomplete"
                    )
                if repair:
                    import_seed(conn, stage, start)
                    symbols, last = [], ""
                    while True:
                        r = conn.execute(
                            "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1",
                            (last,),
                        ).fetchone()
                        if not r:
                            break
                        last = r[0]
                        symbols.append(last)
                    for symbol in symbols:
                        prior = dictionaries(
                            conn.execute(
                                "SELECT b.*,f.consecutive_up,f.streak_known FROM "
                                "daily_bars b LEFT JOIN daily_features f "
                                "USING(symbol,trade_date) WHERE b.symbol=? AND "
                                "b.trade_date<? AND b.volume>0 AND b.low>0 ORDER BY "
                                "b.trade_date DESC LIMIT 6",
                                (symbol, start),
                            )
                        )
                        ages[symbol] = len(prior)
                        if prior:
                            r = prior[0]
                            previous[symbol] = (
                                r,
                                _read(conn, "stock_qfq_bars", (symbol, r["trade_date"])) or None,
                            )
                            streaks[symbol] = (
                                r.get("consecutive_up") if r.get("streak_known") else None
                            )
                summaries, anomalies, receipts = [], [], []
                for day in days:
                    observations = (
                        [
                            json.loads(r[0])
                            for r in stage.execute(
                                "SELECT payload FROM observations WHERE trade_date=? "
                                "ORDER BY symbol",
                                (day,),
                            )
                        ]
                        if repair
                        else []
                    )
                    if repair:
                        receipt = repair_day(
                            conn,
                            day=day,
                            observations=observations,
                            previous=previous,
                            streaks=streaks,
                            ages=ages,
                            listing_dates=listing_dates,
                            calendar=calendar,
                        )
                        receipts.append(receipt)
                        stage.executemany(
                            "INSERT OR IGNORE INTO price_changes VALUES (?,?)",
                            [(symbol, day) for symbol in receipt.get("price_changed_symbols", [])],
                        )
                        stage.execute(
                            "INSERT OR REPLACE INTO repaired_dates VALUES (?,?)",
                            (day, json.dumps(receipt)),
                        )
                        stage.commit()
                    summary, issues = assess_day(conn, day)
                    summary["requires_analysis"] = (
                        summary["uncomputable"] > threshold
                        or summary["summary_mismatches"] > 0
                        or summary["price_rule_conflicts"] > 0
                        or summary["state_conflicts"] > 0
                    )
                    summaries.append(summary)
                    anomalies.extend(issues)
                    if stop_requested and stop_requested():
                        raise InterruptedError(
                            "Stopped after a committed day; rerun the same repair command"
                        )
                    if len(summaries) % 10 == 0:
                        progress = dict(
                            processed_days=len(summaries),
                            total_days=len(days),
                            last_date=day,
                            over_threshold=sum(r["uncomputable"] > threshold for r in summaries),
                        )
                        save(out / "audit-progress.json", progress)
                        save(out / "working-quality.json", summaries)
                        save(out / "working-anomalies.json", anomalies)
                        print(json.dumps(progress), flush=True)
                if repair:
                    price_dates = {}
                    for receipt in receipts:
                        for symbol in receipt.get("price_changed_symbols", []):
                            price_dates.setdefault(symbol, []).append(receipt["trade_date"])
                    if was_pending:
                        for symbol, day in stage.execute(
                            "SELECT symbol,trade_date FROM price_changes"
                        ):
                            price_dates.setdefault(symbol, []).append(day)
                    dependent_days = {}
                    for symbol, changed_dates in price_dates.items():
                        affected = repair_price_dependents(
                            conn, symbol, changed_dates, listing_dates.get(symbol)
                        )
                        for day in affected:
                            dependent_days.setdefault(day, set()).add(symbol)
                        print(
                            json.dumps(
                                dict(price_dependencies=symbol, affected_days=len(affected))
                            ),
                            flush=True,
                        )
                    for day, symbols in sorted(dependent_days.items()):
                        publish_dependents(conn, day, symbols)
                    # Final readback is independent of attempted repair counters.
                    summaries, anomalies = [], []
                    for day in days:
                        summary, issues = assess_day(conn, day)
                        summary["requires_analysis"] = (
                            summary["uncomputable"] > threshold
                            or summary["summary_mismatches"] > 0
                            or summary["price_rule_conflicts"] > 0
                            or summary["state_conflicts"] > 0
                        )
                        summaries.append(summary)
                        anomalies.extend(issues)
                result = dict(
                    start=start,
                    end=end,
                    threshold=threshold,
                    days=len(days),
                    status="needs_analysis"
                    if any(r["requires_analysis"] for r in summaries)
                    else "completed",
                    over_threshold_days=[r for r in summaries if r["uncomputable"] > threshold],
                    analysis_days=[r for r in summaries if r["requires_analysis"]],
                    uncomputable=sum(r["uncomputable"] for r in summaries),
                    quality_totals={
                        k: sum(r.get(k, 0) for r in summaries)
                        for k in (
                            "existing_rows",
                            "qfq_covered_rows",
                            "estimated_reference",
                            "assumed_non_st",
                            "price_rule_conflicts",
                            "state_conflicts",
                            "summary_mismatches",
                            "missing_bars",
                        )
                    },
                    totals={
                        k: sum(r.get(k, 0) for r in receipts)
                        for k in (
                            "qfq_rows",
                            "raw_rows",
                            "feature_rows",
                            "summary_rows",
                            "board_rows",
                        )
                    },
                )
                save(out / "daily-quality.json", summaries)
                save(out / "anomalies.json", anomalies)
                save(out / "repair-receipts.json", receipts)
                save(out / "report.json", result)
            if repair:
                durable_remove(migration_path(root))
            print(json.dumps(result), flush=True)
            return result
    finally:
        if token is not None:
            ACTIVE.reset(token)
