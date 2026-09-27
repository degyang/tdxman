"""Dated security facts, kept separately from actual traded OHLCV bars."""

from __future__ import annotations

from .api_contract import DataPoolError, public_read
from .pool import pool_lock
from .store import catalog, existing_tables, read_only_catalog

DAILY_TABLE = "security_daily_facts"
BASIC_TABLE = "security_lifecycle"
CALENDAR_TABLE = "security_calendar"


def initialize_facts(root):
    with catalog(root) as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {DAILY_TABLE} (
            symbol VARCHAR, trade_date DATE, pre_close DOUBLE, is_st BOOLEAN,
            trading_status VARCHAR, source VARCHAR, fetched_at TIMESTAMP,
            PRIMARY KEY (symbol, trade_date))""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {BASIC_TABLE} (
            symbol VARCHAR PRIMARY KEY, listing_date DATE, delisting_date DATE,
            name VARCHAR, source VARCHAR, fetched_at TIMESTAMP)""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {CALENDAR_TABLE} (
            trade_date DATE PRIMARY KEY, is_open BOOLEAN, source VARCHAR)""")


@public_read
def read_facts(root, table, *, symbols=None, start=None, end=None):
    from .limit_api import _bounds, _date_clause, _normalize_requested_symbols

    if table not in {DAILY_TABLE, BASIC_TABLE, CALENDAR_TABLE}:
        raise DataPoolError("INVALID_ARGUMENT", "Unsupported security facts dataset")
    lo, hi = _bounds(start, end)
    clauses, params = [], []
    if table != BASIC_TABLE:
        _date_clause("trade_date", lo, hi, clauses, params)
    if symbols is not None:
        if table == CALENDAR_TABLE:
            raise DataPoolError("INVALID_ARGUMENT", "Calendar has no symbol filter")
        clauses.append("symbol IN (SELECT unnest(?))")
        params.append(_normalize_requested_symbols(symbols))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order = (
        "trade_date"
        if table == CALENDAR_TABLE
        else ("symbol" if table == BASIC_TABLE else "symbol, trade_date")
    )
    with pool_lock(root):
        if table not in existing_tables(root):
            raise DataPoolError("DATASET_NOT_FOUND", "Run aspool sync --source baostock first")
        with read_only_catalog(root) as conn:
            result = conn.execute(
                f"SELECT * FROM {table}{where} ORDER BY {order}", params
            ).fetchdf()
    sources = sorted(result.source.dropna().unique().tolist()) if "source" in result else []
    result.attrs.update(source=",".join(sources), point_in_time=False)
    if "symbol" in result:
        result.attrs["scope"] = ",".join(sorted(result.symbol.str[-2:].unique().tolist()))
    return result


def lifecycle_map(root):
    if BASIC_TABLE not in existing_tables(root):
        return {}
    with read_only_catalog(root) as conn:
        rows = conn.execute(f"SELECT * FROM {BASIC_TABLE}").fetchdf().to_dict("records")
    from .change_protocol import note_range

    note_range("catalog_read_lifecycle", rows=len(rows))
    # Use Python dates/None; pandas NaT is not a known life-cycle boundary.
    import pandas as pd

    for row in rows:
        for field in ("listing_date", "delisting_date"):
            value = row[field]
            row[field] = None if pd.isna(value) else value.date()
    return {row["symbol"]: row for row in rows}


def daily_facts(root, symbol, start=None, end=None):
    if DAILY_TABLE not in existing_tables(root):
        return []
    clauses, params = ["symbol = ?"], [symbol]
    if start is not None:
        clauses.append("trade_date >= ?")
        params.append(start)
    if end is not None:
        clauses.append("trade_date <= ?")
        params.append(end)
    with read_only_catalog(root) as conn:
        cursor = conn.execute(
            f"SELECT * FROM {DAILY_TABLE} WHERE {' AND '.join(clauses)} ORDER BY trade_date", params
        )
        names = [column[0] for column in cursor.description]
        result = [dict(zip(names, row)) for row in cursor.fetchall()]
        from .change_protocol import note_range

        note_range("catalog_read_facts", rows=len(result),
                   start=result[0]["trade_date"] if result else None,
                   end=result[-1]["trade_date"] if result else None)
        return result


def calendar_days(root):
    if CALENDAR_TABLE not in existing_tables(root):
        return {}
    with read_only_catalog(root) as conn:
        result = dict(conn.execute(f"SELECT trade_date, is_open FROM {CALENDAR_TABLE}").fetchall())
        from .change_protocol import note_range

        note_range("catalog_read_calendar", rows=len(result), start=min(result) if result else None,
                   end=max(result) if result else None)
        return result
