"""ETF daily history with projected, indexed reads and local transactional writes."""

import math
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .api_contract import DAILY_FIELDS, DataPoolError

TYPES = {"VARCHAR": "TEXT", "DOUBLE": "REAL", "BOOLEAN": "INTEGER", "DATE": "TEXT"}
COLUMNS = {("trade_date" if k == "date" else k): TYPES[v[0]] for k, v in DAILY_FIELDS.items()}
# Preserve source fields, even when they are not exposed by the public API.
COLUMNS.update(datetime="TEXT", vol="REAL", float_shares="REAL", asset_type="TEXT", turnover="REAL")
FIELDS = tuple(COLUMNS)
DDL = (
    "PRAGMA user_version=1; CREATE TABLE daily_bars ("
    + ",".join(
        f'"{k}" {v}' + (" NOT NULL" if k in {"symbol", "trade_date", "market", "code"} else "")
        for k, v in COLUMNS.items()
    )
    + """, source TEXT NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(symbol,trade_date), CHECK(symbol=code||'.'||market),
 CHECK(market IN ('SH','SZ')), CHECK(asset_type='etf'),
 CHECK(open>=0 AND high>=low AND low>=0 AND close>=0 AND volume>=0 AND amount>=0),
 CHECK(max(open,high,low,close,volume,amount)<1e308)
) WITHOUT ROWID;
CREATE INDEX daily_bars_by_date ON daily_bars(trade_date,symbol);
CREATE TABLE adjustment_factors (
 symbol TEXT NOT NULL, trade_date TEXT NOT NULL, cumulative_factor REAL NOT NULL
 CHECK(cumulative_factor>0), source TEXT NOT NULL,
 PRIMARY KEY(symbol,trade_date)
) WITHOUT ROWID;
"""
)


@contextmanager
def connection(root, *, read_only=True):
    from .base_delta import assert_replica_complete

    assert_replica_complete(root)
    path = Path(root).resolve() / "etfs.sqlite"
    conn = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"), uri=True, timeout=30
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA cache_size=-32768")
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported ETF schema")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        yield conn
    finally:
        conn.close()


def normalize(raw, item):
    row = dict(raw)
    unknown = set(row) - set(FIELDS) - {"date", "source", "updated_at"}
    if unknown:
        raise ValueError(f"Unmapped ETF fields: {sorted(unknown)}")
    symbol = f"{item['code']}.{item['market']}"
    if row.get("symbol", item["code"]) not in (symbol, item["code"]):
        raise ValueError("ETF symbol mismatch")
    if row.get("market", item["market"]) != item["market"]:
        raise ValueError("ETF market mismatch")
    if row.get("asset_type", "etf") != "etf":
        raise ValueError("Non-ETF data in ETF migration")
    day = row.get("trade_date", row.get("date", row.get("datetime")))
    day = str(day)[:10]
    date.fromisoformat(day)
    row.update(
        symbol=symbol, market=item["market"], code=item["code"], trade_date=day, asset_type="etf"
    )
    row.pop("date", None)
    if row.get("volume") is None:
        row["volume"] = row.get("vol")
    if row.get("turnover_rate") is None:
        row["turnover_rate"] = row.get("turnover")
    for key, value in tuple(row.items()):
        if isinstance(value, (datetime, date)):
            row[key] = value.isoformat()
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Nonfinite ETF {key}")
    for key in ("open", "high", "low", "close", "volume", "amount"):
        if row.get(key) is None or float(row[key]) < 0:
            raise ValueError(f"Invalid ETF {key}")
    if row["high"] < row["low"]:
        raise ValueError("Invalid ETF high/low")
    return row


def save_rows(conn, item, records, *, source):
    if conn.in_transaction or not records or len(records) > 64000:
        raise ValueError("ETF writer requires idle connection and 1..64000 rows")
    incoming = sorted((normalize(r, item) for r in records), key=lambda r: r["trade_date"])
    if len({r["trade_date"] for r in incoming}) != len(incoming):
        raise ValueError("Duplicate ETF dates")
    symbol = f"{item['code']}.{item['market']}"
    start = incoming[0]["trade_date"]
    columns = ",".join(f'"{k}"' for k in FIELDS)
    added = changed = unchanged = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM daily_bars WHERE symbol=? AND trade_date<? "
                "ORDER BY trade_date DESC LIMIT 5",
                (symbol, start),
            )
        ][::-1]
        # Include the bounded later history so corrected volumes repair later ratios.
        existing = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM daily_bars WHERE symbol=? AND trade_date>=? "
                "ORDER BY trade_date LIMIT 64001",
                (symbol, start),
            )
        ]
        if len(existing) > 64000:
            raise ValueError("ETF revision suffix exceeds budget")
        old = {r["trade_date"]: r for r in existing}
        merged = {k: dict(v) for k, v in old.items()}
        for row in incoming:
            day = row["trade_date"]
            merged[day] = {**merged.get(day, {}), **{k: v for k, v in row.items() if v is not None}}
            merged[day]["name"] = item["name"]
        rows = prior + [merged[k] for k in sorted(merged)]
        for i in range(len(prior), len(rows)):
            row = rows[i]
            if i >= 5:
                average = sum(r["volume"] for r in rows[i - 5 : i]) / 5
                row["vol_ratio"] = row["volume"] / average if average > 0 else None
                row["vol_ratio_source"] = "derived:five_observations"
            if row.get("float_shares") and row["float_shares"] > 0:
                row["turnover_rate"] = row["volume"] / (row["float_shares"] * 100)
                row["turnover_rate_source"] = "derived:dated_float_share"
            previous = old.get(row["trade_date"])
            values = tuple(row.get(k) for k in FIELDS)
            if previous and values == tuple(previous.get(k) for k in FIELDS):
                unchanged += 1
                continue
            stamp = max(time.time_ns() // 1000, (previous or {}).get("updated_at", 0) + 1)
            if previous:
                conn.execute(
                    "UPDATE daily_bars SET "
                    + ",".join(f'"{k}"=?' for k in FIELDS)
                    + ",source=?,updated_at=? WHERE symbol=? AND trade_date=?",
                    (*values, source, stamp, symbol, row["trade_date"]),
                )
                changed += 1
            else:
                conn.execute(
                    f"INSERT INTO daily_bars({columns},source,updated_at) VALUES ("
                    + ",".join("?" for _ in range(len(FIELDS) + 2))
                    + ")",
                    (*values, source, stamp),
                )
                added += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return dict(
        **item,
        added=added,
        changed=changed,
        unchanged=unchanged,
        source_end=incoming[-1]["trade_date"],
        overlap_start=start,
    )


def read(root, *, requested, start, end, lookback, selected, listing):
    symbols = None if requested is None else sorted({s[3:] + "." + s[:2] for s in requested})
    with connection(root) as conn:
        conn.execute("BEGIN")
        if conn.execute("SELECT 1 FROM daily_bars LIMIT 1").fetchone() is None:
            raise DataPoolError("ETF_NOT_FOUND", "No ETF daily bars")
        params = [start.isoformat()]
        clauses = ["trade_date>=?"]
        if end:
            clauses.append("trade_date<=?")
            params.append(end.isoformat())
        if listing:
            if symbols is not None:
                clauses.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
                params.extend(symbols)
            result = conn.execute(
                "SELECT symbol,market,code,min(trade_date) AS start,max(trade_date) AS end,"
                "count(*) AS row_count FROM daily_bars WHERE "
                + " AND ".join(clauses)
                + " GROUP BY symbol ORDER BY symbol",
                params,
            ).fetchall()
            rows = []
            for r in result:
                name = conn.execute(
                    "SELECT name FROM daily_bars WHERE symbol=? AND trade_date=?",
                    (r["symbol"], r["end"]),
                ).fetchone()[0]
                rows.append(dict(r, name=name))
            frame = pd.DataFrame(
                rows, columns=["symbol", "market", "code", "name", "start", "end", "row_count"]
            )
            for field in ("start", "end"):
                frame[field] = pd.to_datetime(frame[field])
            return frame
        if symbols is None and lookback:
            symbols, last = [], ""
            while found := conn.execute(
                "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1", (last,)
            ).fetchone():
                last = found[0]
                symbols.append(last)
        projection = ",".join(
            f'"{("trade_date" if k == "date" else k)}" AS "{k}"' for k in selected
        )
        rows = []
        if symbols is None:
            rows = conn.execute(
                f"SELECT {projection} FROM daily_bars WHERE "
                + " AND ".join(clauses)
                + " ORDER BY symbol,trade_date",
                params,
            ).fetchall()
        else:
            for symbol in symbols:
                sql = f"SELECT {projection} FROM daily_bars WHERE symbol=? AND " + " AND ".join(
                    clauses
                )
                sql += " ORDER BY trade_date DESC LIMIT ?" if lookback else " ORDER BY trade_date"
                values = [symbol, *params, *([lookback] if lookback else [])]
                chunk = conn.execute(sql, values).fetchall()
                rows.extend(reversed(chunk) if lookback else chunk)
    frame = pd.DataFrame([tuple(r) for r in rows], columns=selected)
    for field in selected:
        kind = DAILY_FIELDS[field][0]
        if kind == "DATE":
            frame[field] = pd.to_datetime(frame[field])
        elif kind == "DOUBLE":
            frame[field] = frame[field].astype("float64")
        elif kind == "BOOLEAN":
            frame[field] = frame[field].astype("boolean")
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
    return frame


@contextmanager
def factor_connection(root):
    """Expose the historical ETF factor columns from their unique owner."""
    from .platform_v2 import layout_version
    from .pool import pool_lock

    root = Path(root).resolve()
    with pool_lock(root):
        if layout_version(root) < 3:
            with connection(root) as conn:
                yield conn
            return
        path = root / "adjustments.sqlite"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute(
                "CREATE TEMP VIEW adjustment_factors AS SELECT symbol, "
                "effective_date AS trade_date,cumulative_factor,source "
                "FROM main.etf_adjustment_factors"
            )
            conn.execute("PRAGMA query_only=ON")
            yield conn
