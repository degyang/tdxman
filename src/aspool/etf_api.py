"""Read-only ETF daily bars stored with security (stock-like) semantics."""

from __future__ import annotations

import re

import duckdb
import numpy as np
import pandas as pd

from .api_contract import DAILY_FIELDS, OPTIONAL_FIELDS, DataPoolError
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


def _read(root, symbols=None, start=None, end=None, lookback=None, fields=None, listing=False):
    selected = _selected(fields)
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
        files = sorted((root / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet"))
        if not files:
            raise DataPoolError("ETF_NOT_FOUND", "No ETF daily bars in aspool")
        with duckdb.connect() as conn:
            conn.read_parquet(
                [str(path) for path in files], union_by_name=True, hive_partitioning=True
            ).create_view("bars")
            names = {row[0] for row in conn.execute("describe bars").fetchall()}
            if "asset_type" not in names:
                raise DataPoolError("ETF_NOT_FOUND", "ETF bars have not been synchronized")
            clauses = ["asset_type = 'etf'", "trade_date >= ?"]
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
                    "SELECT symbol || '.' || market AS symbol, market, symbol AS code, "
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
            sql = f"""SELECT symbol || '.' || market AS symbol, market, symbol AS code,
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
    return frame[selected].copy()


def read_etf_daily(root, **kwargs):
    return _read(root, **kwargs)


def list_etfs(root, symbols=None):
    return _read(root, symbols=symbols, listing=True)
