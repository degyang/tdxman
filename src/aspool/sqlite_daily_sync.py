"""Bounded source fetching and canonical inputs for the SQLite daily writer."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta

import pandas as pd

from tdxman.models.enums import Market

from .sqlite_daily_update import apply_daily_changes, daily_window
from .sqlite_stock_store import stock_connection


def sync_daily_source(
    root,
    *,
    client,
    symbols,
    market_sessions,
    as_of,
    start=None,
    end=None,
    listed_days=None,
    max_symbols_per_write=500,
    action_fetcher=None,
    source="baostock",
    report=None,
    event_refresh_start=None,
    max_consecutive_failures=3,
):
    """Fetch a finite window, then commit bounded groups without repeated cross-sections.

    The caller owns source sessions and retries. BaoStock does not support BJ;
    TDX adapters do. Unsupported symbols are never reported as synchronized.
    Network failures/empty responses retain old data; writer failures propagate.
    """
    if source not in ("baostock", "tdxman:quote", "tdxman:kline"):
        raise ValueError("Unsupported daily source")
    if not 1 <= max_symbols_per_write <= 500:
        raise ValueError("Expected 1..500 symbols per write")
    if max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures must be positive")
    sessions = list(market_sessions)
    window = daily_window(sessions, as_of=as_of, start=start, end=end)
    result = report if report is not None else {}
    result.update(
        requested_sessions=window,
        success=[],
        empty=[],
        failed=[],
        unsupported=[],
        factor_unavailable=[],
        traded=[],
        no_trade=[],
        missing=[],
        invalid=[],
        aborted=False,
        remaining_block=[],
    )
    with stock_connection(root, read_only=False) as conn:
        pending_symbols, pending_bars, pending_facts, pending_extensions = [], [], [], []
        consecutive_failures = 0

        def flush():
            if not pending_symbols:
                return
            from .sqlite_event_update import SymbolUpdateError

            remaining = set(pending_symbols)
            while remaining:
                try:
                    applied = apply_daily_changes(
                        conn,
                        bars=[r for r in pending_bars if r["symbol"] in remaining],
                        dated_facts=[r for r in pending_facts if r["symbol"] in remaining],
                        factor_extensions=[
                            r for r in pending_extensions if r["symbol"] in remaining
                        ],
                        market_sessions=sessions,
                        listed_days=listed_days,
                        fill_missing_metrics=source != "tdxman:quote",
                        merge_event_revisions=event_refresh_start is not None,
                    )
                except SymbolUpdateError as exc:
                    # The rejected transaction has rolled back. Remove only its
                    # invalid input; never retry database/transaction deadline failures.
                    if exc.symbol not in remaining:
                        raise
                    remaining.remove(exc.symbol)
                    result["failed"].append(
                        dict(symbol=exc.symbol, error=str(exc), phase="validation")
                    )
                    continue
                result["success"].append(dict(symbols=sorted(remaining), **applied))
                break
            pending_symbols.clear()
            pending_bars.clear()
            pending_facts.clear()
            pending_extensions.clear()

        ordered_symbols = list(dict.fromkeys(symbols))
        for position, symbol in enumerate(ordered_symbols):
            code, market = symbol.split(".")
            if market not in (("SH", "SZ", "BJ") if source.startswith("tdxman:") else ("SH", "SZ")):
                result["missing"].append(symbol)
                pending_symbols.append(symbol)
                pending_facts.extend(
                    dict(symbol=symbol, trade_date=day, trading_status="MISSING",
                         trading_status_source=source)
                    for day in window
                )
                continue
            try:
                frame = client.get_daily(
                    Market[market],
                    code,
                    start=date.fromisoformat(window[0]),
                    end=date.fromisoformat(window[-1]),
                    count=None,
                )
            except Exception as exc:
                result["failed"].append(dict(symbol=symbol, error=str(exc)))
                result["missing"].append(symbol)
                pending_symbols.append(symbol)
                pending_facts.extend(
                    dict(symbol=symbol, trade_date=day, trading_status="MISSING",
                         trading_status_source=source)
                    for day in window
                )
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    result["aborted"] = True
                    result["remaining_block"] = ordered_symbols[position + 1 :]
                    break
                continue
            if frame.empty:
                result["empty"].append(symbol)
                result["missing"].append(symbol)
                pending_symbols.append(symbol)
                pending_facts.extend(
                    dict(symbol=symbol, trade_date=day, trading_status="MISSING",
                         trading_status_source=source)
                    for day in window
                )
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    result["aborted"] = True
                    result["remaining_block"] = ordered_symbols[position + 1 :]
                    break
                continue
            records = frame.to_dict("records")
            try:
                for raw in records:
                    returned_day = raw.get("date")
                    returned_day = (
                        returned_day.isoformat()
                        if hasattr(returned_day, "isoformat")
                        else str(returned_day)
                    )
                    if raw.get("symbol") != symbol or returned_day not in window:
                        raise ValueError("Source returned a wrong symbol or date")
            except (TypeError, ValueError) as exc:
                result["invalid"].append(dict(symbol=symbol, error=str(exc)))
                pending_symbols.append(symbol)
                pending_facts.extend(
                    dict(symbol=symbol, trade_date=day, trading_status="INVALID",
                         trading_status_source=source)
                    for day in window
                )
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    result["aborted"] = True
                    result["remaining_block"] = ordered_symbols[position + 1 :]
                    break
                continue
            bars, facts = [], []
            all_missing = True
            for raw in records:
                row = {k: (None if pd.isna(v) else v) for k, v in raw.items()}
                day = row["date"].isoformat()
                key = dict(symbol=symbol, trade_date=day)
                fact = dict(
                    key,
                )
                status = row.get("trading_status")
                if status != "MISSING":
                    all_missing = False
                has_bar = (
                    conn.execute(
                        "SELECT 1 FROM daily_bars WHERE symbol=? AND trade_date=?", (symbol, day)
                    ).fetchone()
                    is not None
                )
                if status is not None and not (status == "MISSING" and has_bar):
                    fact.update(trading_status=row["trading_status"], trading_status_source=source)
                # Empty provider cells mean unavailable, not an authorized
                # withdrawal of an earlier observation. Explicit retractions
                # remain available through the canonical patch writer.
                for field in ("pre_close", "is_st"):
                    if row.get(field) is not None and (field != "pre_close" or row[field] > 0):
                        fact["source_" + field] = row[field]
                        fact["source_" + field + "_source"] = row.get(field + "_source") or source
                facts.append(fact)
                # A source-confirmed suspension is a date fact, not a fabricated
                # flat bar. A conflict with an existing real trade fails atomically.
                if status in ("SUSPENDED", "NO_TRADE", "NOT_LISTED", "MISSING"):
                    result["missing" if status == "MISSING" else "no_trade"].append(symbol)
                    continue
                result["traded"].append(symbol)
                names = (
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "turnover_rate",
                    "pct_chg",
                    "pe_ttm",
                    "pb",
                    "name",
                    "name_as_of",
                    "vol_ratio",
                    "float_share",
                    "float_share_source",
                )
                bar = dict(
                    key,
                    **{name: row[name] for name in names if row.get(name) is not None},
                    ohlcv_source=source,
                )
                for field in ("turnover_rate", "pct_chg", "vol_ratio"):
                    if row.get(field) is not None:
                        bar[field + "_source"] = source
                if row.get("name") is not None:
                    bar["name_source"] = row.get("name_source") or source
                bars.append(bar)
            consecutive_failures = consecutive_failures + 1 if all_missing else 0
            through = max(fact["trade_date"] for fact in facts)
            tail = conn.execute(
                "SELECT max(valid_through) FROM corporate_actions "
                "WHERE symbol=? AND record_kind='factor'",
                (symbol,),
            ).fetchone()[0]
            if tail is None:
                result["factor_unavailable"].append(
                    dict(symbol=symbol, reason="no_verified_anchor")
                )
            elif through > tail or event_refresh_start is not None:
                if action_fetcher is None:
                    result["failed"].append(
                        dict(
                            symbol=symbol,
                            error="Verified action fetcher required to advance factor coverage",
                        )
                    )
                    continue
                first = (date.fromisoformat(tail) + timedelta(days=1)).isoformat()
                if event_refresh_start is not None:
                    first = min(first, event_refresh_start)
                events = None
                if source == "tdxman:quote" and event_refresh_start is None:
                    quoted_pre_close = next(
                        (fact.get("source_pre_close") for fact in facts
                         if fact["trade_date"] == through),
                        None,
                    )
                    prior = conn.execute(
                        "SELECT close FROM daily_bars WHERE symbol=? AND trade_date<? "
                        "ORDER BY trade_date DESC LIMIT 1", (symbol, through)
                    ).fetchone()
                    if (
                        quoted_pre_close is not None
                        and prior is not None
                        and math.isclose(float(quoted_pre_close), float(prior[0]),
                                         rel_tol=0.0005, abs_tol=0.011)
                    ):
                        # A dated quote whose pre-close agrees with the last real close
                        # verifies that no corporate action changed the reference price.
                        events = []
                if events is None:
                    try:
                        events = action_fetcher(symbol, first, through)
                    except Exception as exc:
                        result["failed"].append(dict(symbol=symbol, error=str(exc)))
                        continue
                pending_extensions.append(
                    dict(
                        symbol=symbol,
                        verified_start=first,
                        verified_end=through,
                        events=events,
                        source=("tdxman:quote-pre-close" if not events and source == "tdxman:quote"
                                else "tdx:xdxr"),
                    )
                )
            pending_symbols.append(symbol)
            pending_bars.extend(bars)
            pending_facts.extend(facts)
            if consecutive_failures >= max_consecutive_failures:
                result["aborted"] = True
                result["remaining_block"] = ordered_symbols[position + 1 :]
                break
            if len(pending_symbols) >= max_symbols_per_write:
                flush()
        flush()
    return result


def sync_baostock_daily(root, **kwargs):
    """Compatibility entrypoint for the existing explicit BaoStock ops script."""
    return sync_daily_source(root, source="baostock", **kwargs)


def fetch_tdx_action_interval(client, symbol, start, end):
    """Fetch complete TDX event history, verifying identity before an empty claim.

    TdxClient already normalizes per-ten-share protocol values to per-share
    values. Explicit canonical names prevent a second unit conversion.
    """
    from .enrichment import _fetch

    code, market = symbol.split(".")
    response = _fetch(client, (code, market))
    if end > response["fetched_date"]:
        raise ValueError("Cannot verify corporate actions beyond the fetch date")
    events = []
    slots = Counter()
    for item in response["events"]:
        day = str(item["date"])[:10]
        category = int(item["category"])
        slots[(day, category)] += 1
        if not start <= day <= end:
            continue
        # Preserve the identities assigned by the original SDK event migration.
        event = dict(
            item,
            effective_date=day,
            source="tdx:xdxr",
            source_key=f"category={category}:slot={slots[(day, category)]}",
        )
        if int(item["category"]) == 1:
            for target, original in (
                ("cash_dividend_per_share", "fenhong"),
                ("bonus_shares_per_share", "songzhuangu"),
                ("rights_shares_per_share", "peigu"),
                ("rights_price", "peigujia"),
            ):
                event[target] = item.get(original)
        events.append(event)
    return events


def refresh_source_calendar(root, *, client, start, end):
    """Fill a finite calendar window before selecting the daily source window.

    Calendar metadata has its own catalog transaction. It contains no computed
    results and can commit independently of a later failed market-data fetch.
    Existing contradictory dates need explicit maintenance, never silent repair.
    """
    from pathlib import Path

    import duckdb

    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first or (last - first).days >= 31:
        raise ValueError("Calendar refresh requires 1..31 natural days")
    expected = {(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)}
    frame = client.get_trade_calendar(first, last)
    observed = {}
    for row in frame.to_dict("records"):
        day = row["date"].isoformat()
        flag = row["is_open"]
        if day in observed or day not in expected or not isinstance(flag, bool):
            raise ValueError("Invalid calendar response")
        observed[day] = flag
    if observed.keys() != expected:
        raise ValueError("Incomplete calendar response")
    with duckdb.connect(str(Path(root) / "catalog.duckdb")) as catalog:
        catalog.execute("BEGIN")
        try:
            known = {
                day.isoformat(): opened
                for day, opened in catalog.execute(
                    "SELECT trade_date,is_open FROM security_calendar "
                    "WHERE trade_date BETWEEN ? AND ?",
                    [start, end],
                ).fetchall()
            }
            if any(
                day in known and known[day] is not None and known[day] != flag
                for day, flag in observed.items()
            ):
                raise ValueError("Calendar correction requires explicit maintenance")
            changed = [(day, flag) for day, flag in observed.items() if known.get(day) != flag]
            if changed:
                catalog.executemany(
                    "INSERT INTO security_calendar(trade_date,is_open,source) "
                    "VALUES (?,?,'baostock') "
                    "ON CONFLICT(trade_date) DO UPDATE SET "
                    "is_open=excluded.is_open,source=excluded.source",
                    changed,
                )
            catalog.execute("COMMIT")
        except Exception:
            catalog.execute("ROLLBACK")
            raise
    return dict(changed_dates=len(changed), start=start, end=end)
