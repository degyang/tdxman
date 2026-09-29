"""Independent SQLite index history; no stock rules or publication batches."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from .api_contract import DataPoolError

FIELDS = (
    "market",
    "code",
    "name",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "up_count",
    "down_count",
    "breadth_status",
)
DDL = """
PRAGMA user_version=3;
CREATE TABLE daily_bars (
    symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('SH','SZ','BJ')), code TEXT NOT NULL,
    name TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
    low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, amount REAL NOT NULL,
    up_count INTEGER NOT NULL, down_count INTEGER NOT NULL,
    breadth_status TEXT NOT NULL CHECK(breadth_status IN ('AVAILABLE','UNAVAILABLE')),
    source TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,trade_date),
    CHECK(symbol=code||'.'||market AND length(code)=6),
    CHECK(open>=0 AND high>=max(open,close) AND low<=min(open,close) AND low>=0),
    CHECK(close>=0 AND volume>=0 AND amount>=0 AND up_count>=0 AND down_count>=0),
    CHECK(max(open,high,low,close,volume,amount)<1e308),
    CHECK(typeof(up_count)='integer' AND typeof(down_count)='integer')
) WITHOUT ROWID;
CREATE INDEX daily_bars_by_date ON daily_bars(trade_date,symbol);
"""


@contextmanager
def index_connection(root, *, read_only=True):
    path = Path(root).resolve() / "indices.sqlite"
    conn = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"), uri=True, timeout=30
    )
    try:
        conn.execute("PRAGMA cache_size=-32768")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3):
            raise ValueError("Unsupported index schema version")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                raise ValueError("Index WAL unavailable")
            conn.execute("PRAGMA synchronous=FULL")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_bars)")}
            if "breadth_status" not in columns:
                conn.execute(
                    "ALTER TABLE daily_bars ADD COLUMN breadth_status TEXT NOT NULL "
                    "DEFAULT 'UNAVAILABLE' CHECK(breadth_status IN ('AVAILABLE','UNAVAILABLE'))"
                )
            if version == 1:
                conn.execute(
                    "UPDATE daily_bars SET breadth_status=CASE "
                    "WHEN up_count+down_count>0 THEN 'AVAILABLE' ELSE 'UNAVAILABLE' END "
                    "WHERE breadth_status <> CASE WHEN up_count+down_count>0 "
                    "THEN 'AVAILABLE' ELSE 'UNAVAILABLE' END"
                )
                version = 2
            if version < 3:
                conn.execute("DROP INDEX IF EXISTS daily_bars_by_date")
                conn.execute("ALTER TABLE daily_bars RENAME TO daily_bars_before_bj")
                conn.executescript(DDL)
                columns = ",".join(("symbol", *FIELDS, "source", "updated_at"))
                conn.execute(
                    f"INSERT INTO daily_bars({columns}) SELECT {columns} FROM daily_bars_before_bj"
                )
                conn.execute("DROP TABLE daily_bars_before_bj")
        yield conn
    finally:
        conn.close()


def last_dates(conn, symbol):
    return [
        r[0]
        for r in conn.execute(
            "SELECT trade_date FROM daily_bars WHERE symbol=? ORDER BY trade_date DESC LIMIT 5",
            (symbol,),
        )
    ]


def save_rows(conn, item, records, *, source):
    """One security commits atomically; omitted dates are never deletions."""
    from .index_pool import normalize

    if conn.in_transaction:
        raise ValueError("Index writer requires an idle connection")
    accepted, rejected = [], []
    for record in records:
        if record.get("operation", "update") not in ("insert", "update"):
            raise ValueError("Explicit index deletions are unsupported")
        try:
            accepted.extend(normalize([record], item))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            rejected.append(dict(date=str(record.get("date")), error=str(exc)))
    rows = normalize(accepted, item)
    for row in rows:
        row["breadth_status"] = (
            "AVAILABLE" if row["up_count"] + row["down_count"] > 0 else "UNAVAILABLE"
        )
    if not rows:
        raise ValueError("No valid index records")
    if len(rows) > 64000:
        raise ValueError("Index input exceeds 64000 rows")
    symbol = f"{item['code']}.{item['market']}"
    columns = ",".join(FIELDS)
    placeholders = ",".join("?" for _ in FIELDS)
    added = changed = unchanged = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            row["trade_date"] = row["trade_date"].isoformat()
            values = tuple(row[f] for f in FIELDS)
            previous = conn.execute(
                f"SELECT {columns},updated_at FROM daily_bars WHERE symbol=? AND trade_date=?",
                (symbol, row["trade_date"]),
            ).fetchone()
            if previous and previous[:-1] == values:
                unchanged += 1
                continue
            stamp = max(time.time_ns() // 1000, previous[-1] + 1 if previous else 0)
            if previous:
                conn.execute(
                    "UPDATE daily_bars SET "
                    + ",".join(f + "=?" for f in FIELDS)
                    + ",source=?,updated_at=? WHERE symbol=? AND trade_date=?",
                    (*values, source, stamp, symbol, row["trade_date"]),
                )
                changed += 1
            else:
                conn.execute(
                    f"INSERT INTO daily_bars(symbol,{columns},source,updated_at) "
                    f"VALUES (?,{placeholders},?,?)",
                    (symbol, *values, source, stamp),
                )
                added += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    first = conn.execute(
        "SELECT trade_date FROM daily_bars WHERE symbol=? ORDER BY trade_date LIMIT 1",
        (symbol,),
    ).fetchone()[0]
    last = last_dates(conn, symbol)[0]
    return dict(
        **item,
        fetched=len(records),
        accepted=len(rows),
        rejected=rejected,
        added=added,
        changed=changed,
        unchanged=unchanged,
        start=first,
        end=last,
        source_end=rows[-1]["trade_date"],
    )


def read_indices(root, *, symbols, start, end, lookback, selected, listing):
    """Validated public arguments enter here; field projection reaches SQLite."""
    from .index_api import INDEX_FIELDS

    symbols = None if symbols is None else sorted({s[3:] + "." + s[:2] for s in symbols})
    aliases = {f: f for f in INDEX_FIELDS}
    with index_connection(root) as schema_conn:
        stored_columns = {row[1] for row in schema_conn.execute("PRAGMA table_info(daily_bars)")}
    if "breadth_status" not in stored_columns:
        aliases["breadth_status"] = (
            "CASE WHEN up_count+down_count>0 THEN 'AVAILABLE' ELSE 'UNAVAILABLE' END"
        )
    aliases["date"] = "trade_date"
    projection = ",".join(f'{aliases[f]} AS "{f}"' for f in selected)
    with index_connection(root) as conn:
        conn.execute("BEGIN")
        if conn.execute("SELECT 1 FROM daily_bars LIMIT 1").fetchone() is None:
            raise DataPoolError("INDEX_NOT_FOUND", "No index daily bars in pool")
        clauses, params = [], []
        if start:
            clauses.append("trade_date>=?")
            params.append(start.isoformat())
        if end:
            clauses.append("trade_date<=?")
            params.append(end.isoformat())
        dates_where = " AND ".join(clauses)
        # Per-symbol LIMIT seeks avoid ranking entire histories for a small lookback.
        if symbols is None and lookback:
            symbols, last = [], ""
            while True:
                found = conn.execute(
                    "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1",
                    (last,),
                ).fetchone()
                if found is None:
                    break
                last = found[0]
                symbols.append(last)
        if listing:
            if symbols is not None:
                clauses.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
                params.extend(symbols)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = conn.execute(
                "SELECT symbol,market,code,name,min(trade_date) AS start,"
                "max(trade_date) AS end,count(*) AS row_count FROM daily_bars"
                + where
                + " GROUP BY symbol ORDER BY symbol",
                params,
            ).fetchall()
            # SQLite's max() with multiple aggregates does not guarantee the name's row.
            rows = [
                (
                    *r[:3],
                    conn.execute(
                        "SELECT name FROM daily_bars WHERE symbol=? AND trade_date=?",
                        (r[0], r[5]),
                    ).fetchone()[0],
                    *r[4:],
                )
                for r in rows
            ]
            frame = pd.DataFrame(
                rows, columns=["symbol", "market", "code", "name", "start", "end", "row_count"]
            )
            for field in ("start", "end"):
                frame[field] = pd.to_datetime(frame[field])
            return frame
        rows = []
        if symbols is not None:
            for symbol in symbols:
                sql = f"SELECT {projection} FROM daily_bars WHERE symbol=?"
                if dates_where:
                    sql += " AND " + dates_where
                sql += " ORDER BY trade_date DESC" if lookback else " ORDER BY trade_date"
                values = [symbol, *params]
                if lookback:
                    sql += " LIMIT ?"
                    values.append(lookback)
                chunk = conn.execute(sql, values).fetchall()
                rows.extend(reversed(chunk) if lookback else chunk)
        else:
            where = " WHERE " + dates_where if dates_where else ""
            rows = conn.execute(
                f"SELECT {projection} FROM daily_bars" + where + " ORDER BY symbol,trade_date",
                params,
            ).fetchall()
    frame = pd.DataFrame(rows, columns=selected)
    for field in selected:
        kind = INDEX_FIELDS[field][0]
        if kind == "DATE":
            frame[field] = pd.to_datetime(frame[field])
        elif kind in ("DOUBLE", "BIGINT"):
            frame[field] = frame[field].astype("float64" if kind == "DOUBLE" else "int64")
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
    return frame
