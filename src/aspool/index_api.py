"""Read-only index API for consumers; does not depend on the working-tree universe."""

import re
import sqlite3

import duckdb
import numpy as np
import pandas as pd

from .api_contract import DataPoolError
from .pool import pool_lock

# 支持的符号格式: 000300.SH (规范) 或 SH.000300 (兼容)
_SYMBOL_PATTERN = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$|^(SH|SZ|BJ)\.(\d{6})$")

INDEX_FIELDS = {
    "symbol": ("VARCHAR", None),
    "market": ("VARCHAR", None),
    "code": ("VARCHAR", None),
    "name": ("VARCHAR", None),
    "date": ("DATE", None),
    **{key: ("DOUBLE", "point") for key in ("open", "high", "low", "close")},
    "volume": ("DOUBLE", "tdx_index_volume"),
    "amount": ("DOUBLE", "CNY"),
    "up_count": ("BIGINT", "count"),
    "down_count": ("BIGINT", "count"),
    "breadth_status": ("VARCHAR", None),
}


def _normalize_symbol(s: str) -> str:
    """将符号规范化为存储格式 SH.XXXXXX。

    支持输入:
    - 000300.SH -> SH.000300
    - SH.000300 -> SH.000300
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
        f"Invalid symbol format: {s}; use 000300.SH or SH.000300"
    )


def _symbols(values):
    if values is None:
        return None
    try:
        result = [values] if isinstance(values, str) else list(values)
    except TypeError as exc:
        raise DataPoolError("INVALID_ARGUMENT", "symbols must be a string or list") from exc
    # 规范化为存储格式
    normalized = []
    for v in result:
        if not isinstance(v, str):
            raise DataPoolError("INVALID_ARGUMENT", f"Symbol must be a string: {v}")
        normalized.append(_normalize_symbol(v))
    return normalized


def _read(root, symbols, start=None, end=None, lookback=None, fields=None, listing=False):
    selected = (
        list(INDEX_FIELDS)
        if fields is None
        else ([fields] if isinstance(fields, str) else list(fields))
    )
    if (
        not selected
        or any(not isinstance(f, str) for f in selected)
        or (len(set(selected)) != len(selected) or set(selected) - INDEX_FIELDS.keys())
    ):
        raise DataPoolError("FIELD_UNSUPPORTED", "Invalid or duplicate index fields")
    symbols = _symbols(symbols)
    if lookback is not None and (
        isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1
    ):
        raise DataPoolError("INVALID_ARGUMENT", "lookback must be a positive integer")
    try:
        bounds = [pd.Timestamp(v).date() if v is not None else None for v in (start, end)]
        if any(v is not None and pd.isna(v) for v in bounds):
            raise ValueError("Missing date")
        start, end = bounds
        if start and end and start > end:
            raise ValueError("start after end")
    except (ValueError, TypeError) as exc:
        raise DataPoolError("INVALID_ARGUMENT", "Invalid date bounds") from exc
    with pool_lock(root):
        if (root / "indices.sqlite").is_file():
            from .sqlite_index_store import read_indices

            try:
                return read_indices(root, symbols=symbols, start=start, end=end,
                                    lookback=lookback, selected=selected, listing=listing)
            except DataPoolError:
                raise
            except (sqlite3.Error, ValueError) as exc:
                raise DataPoolError("INDEX_INVALID", str(exc)) from exc
        files = sorted((root / "lake/indices/daily").glob("market=*/symbol=*/bars.parquet"))
        if not files:
            raise DataPoolError("INDEX_NOT_FOUND", "No index daily bars in pool")
        with duckdb.connect() as conn:
            try:
                conn.read_parquet([str(p) for p in files], hive_partitioning=False).create_view("b")
                clauses, params = [], []
                if symbols is not None:
                    clauses.append("market || '.' || code IN (SELECT unnest(?))")
                    params.append(symbols)
                if start:
                    clauses.append("trade_date >= ?")
                    params.append(start)
                if end:
                    clauses.append("trade_date <= ?")
                    params.append(end)
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                if conn.execute(
                    f"SELECT market,code,trade_date FROM b{where} "
                    "GROUP BY ALL HAVING count(*) > 1 LIMIT 1",
                    params,
                ).fetchone():
                    raise DataPoolError("INDEX_INVALID", "Duplicate index dates")
                if listing:
                    # 输出规范格式: code.market (如 000300.SH)
                    return conn.execute(
                        "SELECT code || '.' || market AS symbol, market, code, "
                        "arg_max(name, trade_date) AS name, min(trade_date) AS start, "
                        "max(trade_date) AS end, count(*) AS row_count "
                        f"FROM b{where} GROUP BY market,code ORDER BY symbol",
                        params,
                    ).fetchdf()
                # 输出规范格式: code.market (如 000300.SH)
                sql = (
                    "SELECT code || '.' || market AS symbol, market,code,name,trade_date AS date,"
                    "open,high,low,close,volume,amount,up_count,down_count,"
                    "CASE WHEN up_count+down_count>0 THEN 'AVAILABLE' ELSE 'UNAVAILABLE' END "
                    f"AS breadth_status FROM b{where}"
                )
                if lookback:
                    sql += (
                        " QUALIFY row_number() OVER "
                        "(PARTITION BY market,code ORDER BY trade_date DESC) <= ?"
                    )
                    params.append(lookback)
                frame = conn.execute(sql + " ORDER BY symbol,date", params).fetchdf()
            except duckdb.Error as exc:
                raise DataPoolError("INDEX_INVALID", "Cannot read index data") from exc
    required = ["open", "high", "low", "close", "volume", "amount", "up_count", "down_count"]
    if (
        frame[required].isna().any().any()
        or not np.isfinite(frame[required].to_numpy(dtype=float)).all()
        or (frame[required] < 0).any().any()
        or (frame.high < frame.low).any()
    ):
        raise DataPoolError("INDEX_INVALID", "Invalid index values")
    frame.attrs.update(
        contract_version=2,
        asset_type="index",
        price_unit="point",
        volume_unit="tdx_index_volume",
        amount_unit="CNY",
        breadth_missing_value=None,
        breadth_status_field="breadth_status",
        point_in_time=False,
        dataset_version=None,
        price_adjustment="raw",
    )
    return frame[selected].copy()


def read_index_daily(root, **kwargs):
    return _read(root, **kwargs)


def list_indices(root, symbols=None):
    return _read(root, symbols=symbols, listing=True)
