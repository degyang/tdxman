"""Read-only ETF daily bars stored with security (stock-like) semantics."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .api_contract import DAILY_FIELDS, OPTIONAL_FIELDS, DataPoolError
from .daily_access import DailyStorage
from .pool import pool_lock

# 支持的符号格式: 000001.SH (规范) 或 SH.000001 (兼容)
_SYMBOL_PATTERN = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$|^(SH|SZ|BJ)\.(\d{6})$")

ETF_START = pd.Timestamp("2010-01-01").date()


def _normalize_symbol(s: str) -> str:
    """将符号规范化为存储格式 SH.XXXXXX。

    支持输入:
    - 000001.SH -> SH.000001
    - SH.000001 -> SH.000001
    """
    s = s.strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.match(r"^(SH|SZ|BJ)\.(\d{6})$", s)
    if m:
        return s
    raise DataPoolError(
        "INVALID_ARGUMENT",
        f"Invalid symbol format: {s}; use 000001.SH or SH.000001"
    )


def _selected(fields):
    selected = (
        list(DAILY_FIELDS)
        if fields is None
        else ([fields] if isinstance(fields, str) else list(fields))
    )
    if not selected or len(set(selected)) != len(selected) or set(selected) - DAILY_FIELDS.keys():
        raise DataPoolError("FIELD_UNSUPPORTED", "Invalid or duplicate ETF daily fields")
    return selected


def _read(
    root, symbols=None, start=None, end=None, lookback=None, fields=None, listing=False,
    adjust="none", adjustment_base=None,
):
    if adjust not in {"none", "qfq", "hfq"}:
        raise DataPoolError("INVALID_ARGUMENT", "adjust must be none, qfq or hfq")
    if adjustment_base is not None:
        try:
            adjustment_base = pd.Timestamp(adjustment_base).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise DataPoolError("INVALID_ARGUMENT", "Invalid adjustment_base") from exc
        if adjust == "none":
            raise DataPoolError("INVALID_ARGUMENT", "adjustment_base requires qfq or hfq")
    requested_fields = _selected(fields)
    selected = list(requested_fields)
    if adjust != "none" and not listing:
        selected.extend(name for name in ("symbol", "date") if name not in selected)
    if lookback is not None and (
        isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1
    ):
        raise DataPoolError("INVALID_ARGUMENT", "lookback must be a positive integer")
    try:
        start = pd.Timestamp(start).date() if start is not None else ETF_START
        start = max(start, ETF_START)
        end = pd.Timestamp(end).date() if end is not None else None
        if start is not None and end is not None and start > end:
            raise ValueError("start after end")
    except (TypeError, ValueError) as exc:
        raise DataPoolError("INVALID_ARGUMENT", "Invalid date bounds") from exc
    requested = None
    if symbols is not None:
        requested = [symbols] if isinstance(symbols, str) else list(symbols)
        # 规范化为存储格式
        normalized = []
        for v in requested:
            if not isinstance(v, str):
                raise DataPoolError("INVALID_ARGUMENT", f"Symbol must be a string: {v}")
            normalized.append(_normalize_symbol(v))
        requested = normalized
    with pool_lock(root):
        if (Path(root) / "etfs.sqlite").exists():
            from .sqlite_etf_store import read

            try:
                frame = read(
                    root, requested=requested, start=start, end=end, lookback=lookback,
                    selected=selected, listing=listing,
                )
                return (
                    frame
                    if listing
                    else _adjust(
                        root, frame, requested_fields, adjust, adjustment_base
                    )
                )
            except (sqlite3.Error, ValueError) as exc:
                if isinstance(exc, DataPoolError):
                    raise
                raise DataPoolError("ETF_INVALID", str(exc)) from exc
        storage = DailyStorage(root)
        with duckdb.connect() as conn:
            try:
                files = storage.bind(conn, "bars", symbols=requested, start=start, end=end)
            except ValueError as exc:
                raise DataPoolError("ETF_INVALID", str(exc)) from exc
            if (not files and not storage.files(requested) and not storage.has_data()):
                raise DataPoolError("ETF_NOT_FOUND", "No ETF daily bars in aspool")
            names = {row[0] for row in conn.execute("describe bars").fetchall()}
            if not files or "asset_type" not in names:
                if not storage.has_field("asset_type"):
                    raise DataPoolError("ETF_NOT_FOUND", "ETF bars have not been synchronized")
            asset_clause = "asset_type = 'etf'" if "asset_type" in names else "false"
            clauses = [asset_clause, "trade_date >= ?"]
            params: list[object] = [start]
            if end:
                clauses.append("trade_date <= ?")
                params.append(end)
            if requested is not None:
                # 使用规范化后的存储格式匹配
                clauses.append("market || '.' || symbol IN (SELECT unnest(?))")
                params.append(requested)
            where = " WHERE " + " AND ".join(clauses)
            if conn.execute(
                f"SELECT market, symbol, trade_date FROM bars{where} "
                "GROUP BY ALL HAVING count(*) > 1 LIMIT 1",
                params,
            ).fetchone():
                raise DataPoolError("ETF_INVALID", "Duplicate ETF daily keys")
            if listing:
                name_expression = "arg_max(name, trade_date)" if "name" in names else "NULL"
                # 输出规范格式: symbol.market (如 000001.SH)
                return conn.execute(
                    "SELECT symbol || '.' || market AS symbol, market, "
                    "CAST(symbol AS VARCHAR) AS code, "
                    f"{name_expression} AS name, min(trade_date) AS start, "
                    "max(trade_date) AS end, count(*) AS row_count "
                    f"FROM bars{where} GROUP BY market, symbol ORDER BY symbol",
                    params,
                ).fetchdf()
            turnover = (
                "coalesce(turnover_rate, turnover)"
                if "turnover_rate" in names and "turnover" in names
                else "turnover_rate"
                if "turnover_rate" in names
                else "turnover"
                if "turnover" in names
                else "NULL"
            )
            volume = (
                "coalesce(volume, vol)"
                if "volume" in names and "vol" in names
                else "volume"
                if "volume" in names
                else "vol"
                if "vol" in names
                else "NULL"
            )
            extensions = ", ".join(
                f'CAST("{key}" AS {dtype}) AS "{key}"'
                if key in names
                else f'CAST(NULL AS {dtype}) AS "{key}"'
                for key, (dtype, _) in OPTIONAL_FIELDS.items()
            )
            # 输出规范格式: symbol.market (如 000001.SH)
            sql = f"""SELECT symbol || '.' || market AS symbol, market,
                CAST(symbol AS VARCHAR) AS code,
                trade_date AS date, open, high, low, close, {volume} AS volume,
                amount, {turnover} AS turnover_rate, {extensions} FROM bars{where}"""
            if lookback:
                sql += (
                    " QUALIFY row_number() OVER "
                    "(PARTITION BY market, symbol ORDER BY trade_date DESC) <= ?"
                )
                params.append(lookback)
            frame = conn.execute(sql + " ORDER BY symbol, date", params).fetchdf()
    required = ["open", "high", "low", "close", "volume", "amount"]
    if (
        frame[required].isna().any().any()
        or not np.isfinite(frame[required].to_numpy(dtype=float)).all()
    ):
        raise DataPoolError("ETF_INVALID", "ETF OHLCV/amount is incomplete")
    if (frame[required] < 0).any().any() or (frame.high < frame.low).any():
        raise DataPoolError("ETF_INVALID", "Invalid ETF daily values")
    frame.attrs.update(
        contract_version=2,
        asset_type="etf",
        price_adjustment="raw",
        volume_unit="share",
        amount_unit="CNY",
        turnover_rate_unit="percent",
        point_in_time=False,
        dataset_version=None,
    )
    return _adjust(root, frame[selected].copy(), requested_fields, adjust, adjustment_base)


def _adjust(root, frame, requested_fields, adjust, adjustment_base):
    attrs = dict(frame.attrs)
    attrs.update(adjustment=adjust, adjustment_base=adjustment_base)
    if adjust == "none" or frame.empty:
        result = frame[list(requested_fields)].copy()
        result.attrs.update(attrs)
        return result
    from .platform_v2 import layout_version

    v2 = layout_version(root) >= 2
    path = Path(root) / ("adjustments.sqlite" if v2 else "etfs.sqlite")
    table = "etf_adjustment_factors" if v2 else "adjustment_factors"
    result = frame.copy()
    used = {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        for symbol, positions in result.groupby("symbol", sort=False).groups.items():
            rows = conn.execute(
                f"SELECT {('effective_date' if v2 else 'trade_date')},cumulative_factor "
                f"FROM {table} WHERE symbol=? ORDER BY 1",
                (symbol,),
            ).fetchall()
            if not rows:
                raise DataPoolError("ADJUSTMENT_NOT_READY", f"No ETF factors for {symbol}")
            dates = result.loc[positions, "date"].dt.strftime("%Y-%m-%d")
            values = pd.Series(float("nan"), index=positions, dtype="float64")
            for index in positions:
                day = result.at[index, "date"].strftime("%Y-%m-%d")
                factor = next(
                    (value for effective, value in reversed(rows) if effective <= day), None
                )
                if factor is not None:
                    values.at[index] = factor
            if values.isna().any():
                raise DataPoolError(
                    "ADJUSTMENT_NOT_READY", f"ETF factor coverage is incomplete for {symbol}"
                )
            if adjustment_base:
                reference = next(
                    (value for effective, value in reversed(rows) if effective <= adjustment_base),
                    None,
                )
            elif adjust == "qfq":
                reference = values.loc[dates.idxmax()]
            else:
                reference = rows[0][1]
            if reference is None or reference <= 0:
                raise DataPoolError("ADJUSTMENT_NOT_READY", f"ETF base is uncovered for {symbol}")
            multiplier = values / reference
            for field in ("open", "high", "low", "close", "pre_close"):
                if field in result:
                    result.loc[positions, field] = (
                        pd.to_numeric(result.loc[positions, field]) * multiplier.loc[positions]
                    )
            used[symbol] = reference
    result = result[list(requested_fields)].copy()
    result.attrs.update(attrs, price_adjustment=adjust, adjustment_factors=used)
    return result


def read_etf_daily(root, **kwargs):
    return _read(root, **kwargs)


def list_etfs(root, symbols=None):
    return _read(root, symbols=symbols, listing=True)
