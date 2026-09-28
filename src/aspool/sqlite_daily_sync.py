"""Bounded SH/SZ source fetching for the SQLite daily writer."""

from __future__ import annotations

from datetime import date

import pandas as pd

from tdxman.models.enums import Market

from .sqlite_daily_update import apply_daily_changes, daily_window
from .sqlite_stock_store import stock_connection


def sync_baostock_daily(
    root, *, client, symbols, market_sessions, as_of, start=None, end=None, listed_days=None
):
    """Fetch five recent sessions or an explicit repair, committing each stock.

    The caller owns the serial BaostockClient session. BJ is unsupported by
    this provider and is reported, never silently considered synchronized.
    Network failures/empty responses retain old data; writer failures propagate.
    """
    sessions = list(market_sessions)
    window = daily_window(sessions, as_of=as_of, start=start, end=end)
    result = dict(requested_sessions=window, success=[], empty=[], failed=[], unsupported=[])
    with stock_connection(root, read_only=False) as conn:
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
                facts.append(
                    dict(
                        key,
                        source_pre_close=row["pre_close"],
                        source_pre_close_source="baostock",
                        source_is_st=row["is_st"],
                        source_is_st_source="baostock",
                        trading_status=row["trading_status"],
                        trading_status_source="baostock",
                    )
                )
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
                bars.append(
                    dict(
                        key,
                        **{name: row[name] for name in names},
                        ohlcv_source="baostock",
                        turnover_rate_source="baostock",
                        pct_chg_source="baostock",
                    )
                )
            applied = apply_daily_changes(
                conn,
                bars=bars,
                dated_facts=facts,
                market_sessions=sessions,
                listed_days=listed_days,
            )
            result["success"].append(dict(symbol=symbol, **applied))
    return result
