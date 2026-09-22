"""Deterministic Tick Stock Panel schema and unit normalization."""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable
from datetime import date, datetime
from typing import Any, cast

import pandas as pd

from .contracts import DAILY_COLUMNS, MINUTE_COLUMNS, ProviderError


def polars() -> Any:
    """Import Polars only when this optional integration is actually used."""
    try:
        import polars as pl
    except ImportError as exc:
        raise ProviderError(
            "DEPENDENCY_MISSING",
            "Tick Stock Panel support requires `tdxman[tick-stock-panel]`.",
        ) from exc
    return pl


def empty_daily() -> Any:
    """Return an empty daily frame with the complete, stable consumer schema."""
    pl = polars()
    return pl.DataFrame(
        schema={
            "symbol": pl.String,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "amount": pl.Float64,
        }
    )


def empty_minute() -> Any:
    """Return an empty minute frame with the complete consumer schema."""
    pl = polars()
    return pl.DataFrame(
        schema={
            "symbol": pl.String,
            "datetime": pl.Datetime("us"),
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "amount": pl.Float64,
        }
    )


def normalize_symbol(value: object) -> str:
    """Return canonical ``code.EXCHANGE`` identity without guessing a market."""
    text = str(value).strip().upper().replace(" ", ".")
    parts = text.split(".")
    if len(parts) != 2:
        raise ProviderError("INVALID_SYMBOL", f"Symbol must include an exchange: {value!r}")
    first, second = parts
    if first in {"SH", "SZ", "BJ"}:
        code, exchange = second, first
    else:
        code, exchange = first, second
    if len(code) != 6 or not code.isdigit() or exchange not in {"SH", "SZ", "BJ"}:
        raise ProviderError("INVALID_SYMBOL", f"Invalid symbol: {value!r}")
    return f"{code}.{exchange}"


def normalize_symbols(symbols: object) -> list[str]:
    """Normalize and de-duplicate explicit requested symbols."""
    if symbols is None:
        raise ProviderError("INVALID_ARGUMENT", "symbols must be an explicit sequence")
    if isinstance(symbols, str):
        values = [symbols]
    elif isinstance(symbols, Iterable):
        values = list(symbols)
    else:
        raise ProviderError("INVALID_ARGUMENT", "symbols must be an explicit sequence")
    return list(dict.fromkeys(normalize_symbol(value) for value in values))


def normalize_date_bound(value: object | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        parsed = pd.Timestamp(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ProviderError("INVALID_ARGUMENT", f"Invalid {name}: {value!r}") from exc
    if pd.isna(parsed):
        raise ProviderError("INVALID_ARGUMENT", f"Invalid {name}: {value!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("Asia/Shanghai")
    return cast(date, parsed.date())


def validate_date_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and start > end:
        raise ProviderError("INVALID_ARGUMENT", "start_time must not be after end_time")


def normalize_daily(
    frame: pd.DataFrame,
    asset_type: str,
    *,
    symbols: Collection[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Any:
    """Convert an aspool daily frame to the exact Tick Stock Panel contract."""
    if asset_type not in {"stock", "etf", "index"}:
        raise ProviderError("INVALID_ASSET_TYPE", f"Unsupported asset_type: {asset_type!r}")
    if frame.empty:
        return empty_daily()
    missing = set(DAILY_COLUMNS) - set(frame.columns)
    if missing:
        raise ProviderError("BAD_DATA", f"Daily data is missing fields: {sorted(missing)}")

    result = frame.loc[:, DAILY_COLUMNS].copy()
    result["symbol"] = result["symbol"].map(normalize_symbol)
    try:
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
        for column in DAILY_COLUMNS[2:]:
            result[column] = pd.to_numeric(result[column], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            "BAD_DATA", "Daily data contains invalid dates or numeric values"
        ) from exc

    if symbols is not None:
        result = result.loc[result["symbol"].isin(symbols)]
    if start is not None:
        result = result.loc[result["date"] >= start]
    if end is not None:
        result = result.loc[result["date"] <= end]
    if result.empty:
        return empty_daily()

    pl = polars()
    normalized = pl.from_pandas(result, include_index=False).with_columns(
        pl.col("symbol").cast(pl.String),
        pl.col("date").cast(pl.Date),
        *[pl.col(column).cast(pl.Float64) for column in DAILY_COLUMNS[2:]],
    )
    numeric_columns = DAILY_COLUMNS[2:]
    invalid_checks = [
        pl.col("date").is_null(),
        *[pl.col(column).is_null() for column in numeric_columns],
        ~pl.all_horizontal([pl.col(column).is_finite() for column in numeric_columns]),
        pl.col("open") > pl.col("high"),
        pl.col("open") < pl.col("low"),
        pl.col("low") < 0,
        pl.col("high") < pl.col("low"),
        pl.col("low") > pl.col("close"),
        pl.col("close") > pl.col("high"),
        pl.col("volume") < 0,
        pl.col("amount") < 0,
    ]
    invalid = normalized.select(pl.any_horizontal(*invalid_checks).any()).item()
    if invalid:
        raise ProviderError("BAD_DATA", "Daily data contains non-finite or negative values")
    volume = pl.col("volume") if asset_type == "index" else pl.col("volume") / 100.0
    return (
        normalized.with_columns(volume.alias("volume"))
        .unique(subset=["symbol", "date"], keep="last", maintain_order=True)
        .sort(["symbol", "date"])
        .select(DAILY_COLUMNS)
    )


def normalize_realtime_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one MAC quote row with explicit lot and timestamp semantics."""
    required = {"market", "code", "pre_close", "open", "high", "low", "close", "vol"}
    missing = required - row.keys()
    if missing:
        raise ProviderError(
            "SCHEMA_MISMATCH", f"Realtime quote is missing fields: {sorted(missing)}"
        )
    exchange = {0: "SZ", 1: "SH", 2: "BJ"}.get(int(row["market"]))
    code = str(row["code"]).strip()
    if exchange is None or not code.isdigit():
        raise ProviderError("SCHEMA_MISMATCH", "Realtime quote identity is invalid")

    def number(name: str, *, required: bool = False) -> float | None:
        value = row.get(name)
        if value is None:
            if required:
                raise ProviderError("SCHEMA_MISMATCH", f"Realtime {name} is missing")
            return None
        result = float(value)
        if not math.isfinite(result):
            raise ProviderError("BAD_DATA", f"Realtime {name} is not finite")
        return result

    last = number("close", required=True)
    previous = number("pre_close", required=True)
    volume = number("vol", required=True)
    amount = number("amount")
    if (volume is not None and volume < 0) or (amount is not None and amount < 0):
        raise ProviderError("BAD_DATA", "Realtime volume or amount is negative")
    change = last - previous if last is not None and previous is not None else None
    change_pct = change / previous if change is not None and previous and previous > 0 else None
    high = number("high", required=True)
    low = number("low", required=True)
    amplitude = (
        (high - low) / previous
        if high is not None and low is not None and previous and previous > 0
        else None
    )
    timestamp = _mac_quote_timestamp(row.get("server_update_date"), row.get("server_update_time"))
    turnover = number("turnover")
    return {
        "symbol": f"{code}.{exchange}",
        "name": str(row.get("name") or "") or None,
        "last_price": last,
        "prev_close": previous,
        "open": number("open", required=True),
        "high": high,
        "low": low,
        "volume": volume,
        "amount": amount,
        "change_amount": change,
        "change_pct": change_pct,
        "turnover_rate": turnover / 100.0 if turnover is not None else None,
        "amplitude": amplitude,
        "timestamp": timestamp,
    }


def _mac_quote_timestamp(raw_date: object, raw_time: object) -> int | None:
    if raw_date is None or raw_time is None:
        return None
    try:
        date_text = str(int(cast(Any, raw_date))).zfill(8)
        time_text = str(int(cast(Any, raw_time))).zfill(6)
        wall = datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")
        localized = pd.Timestamp(wall, tz="Asia/Shanghai")
    except (TypeError, ValueError, OverflowError):
        return None
    return int(localized.timestamp() * 1000)


def normalize_minute(
    frame: pd.DataFrame,
    asset_type: str,
    *,
    symbol: str,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> Any:
    """Normalize MAC minute bars; its K-line volume is shares and amount is CNY."""
    if asset_type not in {"stock", "etf"}:
        raise ProviderError("UNIT_UNVERIFIED", "Index minute volume has not been verified")
    if frame.empty:
        return empty_minute()
    required = {"datetime", "open", "high", "low", "close", "vol"}
    if missing := required - set(frame.columns):
        raise ProviderError("SCHEMA_MISMATCH", f"Minute data is missing fields: {sorted(missing)}")
    result = frame.reindex(
        columns=["datetime", "open", "high", "low", "close", "vol", "amount"]
    ).copy()
    result.rename(columns={"vol": "volume"}, inplace=True)
    result.insert(0, "symbol", symbol)
    try:
        result["datetime"] = pd.to_datetime(result["datetime"], errors="raise")
        if result["datetime"].dt.tz is not None:
            result["datetime"] = (
                result["datetime"].dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            )
        for column in MINUTE_COLUMNS[2:]:
            result[column] = pd.to_numeric(result[column], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            "BAD_DATA", "Minute data has invalid timestamps or numeric values"
        ) from exc
    if start is not None:
        result = result.loc[result["datetime"] >= start]
    if end is not None:
        result = result.loc[result["datetime"] <= end]
    if result.empty:
        return empty_minute()
    pl = polars()
    normalized = pl.from_pandas(result, include_index=False).with_columns(
        pl.col("symbol").cast(pl.String),
        pl.col("datetime").cast(pl.Datetime("us")),
        *[pl.col(column).cast(pl.Float64) for column in MINUTE_COLUMNS[2:]],
    )
    checks = [
        pl.col("datetime").is_null(),
        *[pl.col(column).is_null() for column in MINUTE_COLUMNS[2:-1]],
        ~pl.all_horizontal(
            [pl.col(column).is_finite() for column in MINUTE_COLUMNS[2:-1]]
        ),
        pl.col("amount").is_not_null() & ~pl.col("amount").is_finite(),
        pl.col("open") > pl.col("high"),
        pl.col("open") < pl.col("low"),
        pl.col("low") < 0,
        pl.col("high") < pl.col("low"),
        pl.col("low") > pl.col("close"),
        pl.col("close") > pl.col("high"),
        pl.col("volume") < 0,
        pl.col("amount").is_not_null() & (pl.col("amount") < 0),
    ]
    if normalized.select(pl.any_horizontal(*checks).any()).item():
        raise ProviderError("BAD_DATA", "Minute data is invalid")
    return (
        normalized.with_columns((pl.col("volume") / 100.0).alias("volume"))
        .unique(subset=["symbol", "datetime"], keep="last", maintain_order=True)
        .sort(["symbol", "datetime"])
        .select(MINUTE_COLUMNS)
    )
