"""Read-only index API for consumers; does not depend on the working-tree universe."""

import re

import duckdb
import numpy as np
import pandas as pd

from .api_contract import DataPoolError
from .pool import pool_lock

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
}


def _symbols(values):
    if values is None:
        return None
    try:
        result = [values] if isinstance(values, str) else list(values)
    except TypeError as exc:
        raise DataPoolError("INVALID_ARGUMENT", "symbols must be a string or list") from exc
    if any(not isinstance(v, str) or not re.fullmatch(r"(SH|SZ)\.[0-9]{6}", v) for v in result):
        raise DataPoolError("INVALID_ARGUMENT", "index symbols must be SH.000300 or SZ.399001")
    return result


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
                    return conn.execute(
                        "SELECT market || '.' || code AS symbol, market, code, "
                        "arg_max(name, trade_date) AS name, min(trade_date) AS start, "
                        "max(trade_date) AS end, count(*) AS row_count "
                        f"FROM b{where} GROUP BY market,code ORDER BY symbol",
                        params,
                    ).fetchdf()
                sql = (
                    "SELECT market || '.' || code AS symbol, market,code,name,trade_date AS date,"
                    f"open,high,low,close,volume,amount,up_count,down_count FROM b{where}"
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
        breadth_missing_value=0,
        point_in_time=False,
        dataset_version=None,
        price_adjustment="raw",
    )
    return frame[selected].copy()


def read_index_daily(root, **kwargs):
    return _read(root, **kwargs)


def list_indices(root, symbols=None):
    return _read(root, symbols=symbols, listing=True)
