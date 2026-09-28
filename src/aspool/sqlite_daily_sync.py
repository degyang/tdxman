"""Bounded SH/SZ source fetching for the SQLite daily writer."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from tdxman.models.enums import Market

from .sqlite_daily_update import apply_daily_changes, daily_window
from .sqlite_stock_store import stock_connection


def sync_baostock_daily(
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
):
    """Fetch a finite window, then commit bounded groups without repeated cross-sections.

    The caller owns the serial BaostockClient session. BJ is unsupported by
    this provider and is reported, never silently considered synchronized.
    Network failures/empty responses retain old data; writer failures propagate.
    """
    if not 1 <= max_symbols_per_write <= 500:
        raise ValueError("Expected 1..500 symbols per write")
    sessions = list(market_sessions)
    window = daily_window(sessions, as_of=as_of, start=start, end=end)
    result = dict(
        requested_sessions=window,
        success=[],
        empty=[],
        failed=[],
        unsupported=[],
        factor_unavailable=[],
    )
    with stock_connection(root, read_only=False) as conn:
        pending_symbols, pending_bars, pending_facts, pending_extensions = [], [], [], []

        def flush():
            if not pending_symbols:
                return
            applied = apply_daily_changes(
                conn,
                bars=pending_bars,
                dated_facts=pending_facts,
                factor_extensions=pending_extensions,
                market_sessions=sessions,
                listed_days=listed_days,
            )
            result["success"].append(dict(symbols=list(pending_symbols), **applied))
            pending_symbols.clear()
            pending_bars.clear()
            pending_facts.clear()
            pending_extensions.clear()

        for symbol in dict.fromkeys(symbols):
            code, market = symbol.split(".")
            if market not in ("SH", "SZ"):
                result["unsupported"].append(symbol)
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
                continue
            if frame.empty:
                result["empty"].append(symbol)
                continue
            bars, facts = [], []
            for raw in frame.to_dict("records"):
                row = {k: (None if pd.isna(v) else v) for k, v in raw.items()}
                day = row["date"].isoformat()
                if row["symbol"] != symbol or day not in window:
                    raise ValueError("Source returned a wrong symbol or date")
                key = dict(symbol=symbol, trade_date=day)
                fact = dict(
                    key, trading_status=row["trading_status"], trading_status_source="baostock"
                )
                # Empty provider cells mean unavailable, not an authorized
                # withdrawal of an earlier observation. Explicit retractions
                # remain available through the canonical patch writer.
                for field in ("pre_close", "is_st"):
                    if row[field] is not None:
                        fact["source_" + field] = row[field]
                        fact["source_" + field + "_source"] = "baostock"
                facts.append(fact)
                # A source-confirmed suspension is a date fact, not a fabricated
                # flat bar. A conflict with an existing real trade fails atomically.
                if row["trading_status"] == "SUSPENDED":
                    continue
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
                )
                bar = dict(
                    key,
                    **{name: row[name] for name in names if row[name] is not None},
                    ohlcv_source="baostock",
                )
                for field in ("turnover_rate", "pct_chg"):
                    if row[field] is not None:
                        bar[field + "_source"] = "baostock"
                bars.append(bar)
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
            elif through > tail:
                if action_fetcher is None:
                    result["failed"].append(
                        dict(
                            symbol=symbol,
                            error="Verified action fetcher required to advance factor coverage",
                        )
                    )
                    continue
                first = (date.fromisoformat(tail) + timedelta(days=1)).isoformat()
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
                        source="tdx:xdxr",
                    )
                )
            pending_symbols.append(symbol)
            pending_bars.extend(bars)
            pending_facts.extend(facts)
            if len(pending_symbols) >= max_symbols_per_write:
                flush()
        flush()
    return result


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
    for item in response["events"]:
        day = str(item["date"])[:10]
        if not start <= day <= end:
            continue
        event = dict(item, effective_date=day, source="tdx:xdxr")
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
