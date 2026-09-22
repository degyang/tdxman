"""Explicit source routing for Tick Stock Panel requests."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from tdxman.models.enums import KlineCategory, Market

from .contracts import DAILY_COLUMNS, ProviderError
from .diagnostics import check_budget
from .normalize import empty_minute, normalize_daily, normalize_minute, polars

logger = logging.getLogger(__name__)


def read_mac_minute(client, symbol, *, asset_type, period, start, end,
                    max_bars, latest_count=None):
    """Use the same raw K-line endpoint as `tdxman kline`, with bounded coverage."""
    from tdxman.mac.enums import Adjust, Period

    if asset_type == "index":
        raise ProviderError("UNIT_UNVERIFIED", "Index minute volume has not been verified")
    code, exchange = symbol.split(".")
    if max_bars < 1:
        raise ProviderError("CONFIG_INVALID", "Minute limit must be positive")
    limit = min(max_bars, latest_count) if latest_count is not None else max_bars
    if latest_count is not None and latest_count > max_bars:
        raise ProviderError("INCOMPLETE_COVERAGE", "Requested count exceeds minute limit")
    offset, oldest = 0, None
    frames = []
    while offset < limit:
        check_budget()
        size = min(700, limit - offset)
        try:
            raw = client.get_stock_kline(
                {"SZ": 0, "SH": 1, "BJ": 2}[exchange], code, Period(period),
                start=offset, count=size, adjust=Adjust.NONE,
            )
        except Exception as exc:
            raise ProviderError("UPSTREAM_UNAVAILABLE", f"Minute fetch failed: {symbol}") from exc
        page = normalize_minute(raw, asset_type, symbol=symbol, start=None, end=None)
        if page.is_empty():
            break
        current = page["datetime"].min()
        if oldest is not None and current >= oldest:
            raise ProviderError("INCOMPLETE_COVERAGE", "Minute pagination made no progress")
        oldest = current
        frames.append(page)
        offset += len(raw)
        if start is not None and current <= start:
            break
        if len(raw) < size:
            break
        if offset >= limit and start is not None and latest_count is None:
            raise ProviderError("INCOMPLETE_COVERAGE", "Minute history reached configured limit")
    if not frames:
        return empty_minute()
    pl = polars()
    result = pl.concat(frames).unique(["symbol", "datetime"], keep="last").sort("datetime")
    if start is not None:
        result = result.filter(pl.col("datetime") >= start)
    if end is not None:
        result = result.filter(pl.col("datetime") <= end)
    logger.info(
        "minute_coverage symbol=%s requested_start=%s requested_end=%s fetched=%d rows=%d "
        "actual_start=%s actual_end=%s window_limit=%d",
        symbol, start, end, offset, result.height,
        result["datetime"].min(), result["datetime"].max(), limit,
    )
    return result

_MARKETS = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}


def read_standard_daily(
    client: Any,
    symbol: str,
    *,
    asset_type: str,
    start: date | None,
    end: date | None,
    page_size: int,
    max_bars: int,
) -> Any:
    """Read one stock/ETF daily series with bounded offset pagination."""
    if asset_type not in {"stock", "etf", "index"}:
        raise ProviderError("INVALID_ASSET_TYPE", f"Unsupported asset_type: {asset_type!r}")
    if page_size < 1 or page_size > 800:
        raise ProviderError("CONFIG_INVALID", "online_page_size must be between 1 and 800")
    if max_bars < page_size:
        raise ProviderError(
            "CONFIG_INVALID", "online_max_bars_per_symbol must be at least online_page_size"
        )

    code, exchange = symbol.split(".")
    if asset_type == "index" and code.startswith("88"):
        raise ProviderError(
            "UNIT_UNVERIFIED", "Industry boards require a verified MAC daily volume route"
        )
    market = _MARKETS[exchange]
    pages: list[pd.DataFrame] = []
    offset = 0
    previous_oldest: date | None = None
    reached_boundary = False
    exhausted = False

    while offset < max_bars:
        check_budget()
        count = min(page_size, max_bars - offset)
        try:
            getter = client.get_index_bars if asset_type == "index" else client.get_security_bars
            page = getter(market, code, KlineCategory.DAY, offset, count)
        except Exception as exc:
            raise ProviderError(
                "UPSTREAM_UNAVAILABLE", f"Online daily request failed for {symbol}"
            ) from exc
        if page.empty:
            exhausted = True
            break
        if "date" not in page.columns:
            raise ProviderError("SCHEMA_MISMATCH", "Online daily data is missing date")

        current = page.copy()
        current["symbol"] = symbol
        if "vol" in current.columns and "volume" not in current.columns:
            current.rename(columns={"vol": "volume"}, inplace=True)
        pages.append(current)

        dates = pd.to_datetime(current["date"], errors="coerce")
        if dates.isna().any():
            raise ProviderError("SCHEMA_MISMATCH", "Online daily data has invalid dates")
        oldest = dates.min().date()
        if previous_oldest is not None and oldest >= previous_oldest:
            raise ProviderError("INCOMPLETE_COVERAGE", "Online daily pagination made no progress")
        previous_oldest = oldest
        offset += len(current)
        if start is not None and oldest <= start:
            reached_boundary = True
            break
        if len(current) < count:
            exhausted = True
            break

    if not exhausted and not reached_boundary and offset >= max_bars:
        raise ProviderError(
            "INCOMPLETE_COVERAGE", f"Online daily history reached configured limit for {symbol}"
        )
    if not pages:
        return normalize_daily(pd.DataFrame(columns=DAILY_COLUMNS), asset_type)
    raw = pd.concat(pages, ignore_index=True)
    return normalize_daily(raw, asset_type, symbols=[symbol], start=start, end=end)
