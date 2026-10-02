"""Typed, historical fundamentals independent from the daily market store."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .api_contract import DataPoolError

FINANCE_FIELDS = (
    "liutong_guben",
    "zong_guben",
    "guojia_gu",
    "faqiren_faren_gu",
    "faren_gu",
    "b_gu",
    "h_gu",
    "zhigong_gu",
    "province",
    "industry",
    "ipo_date",
    "zong_zichan",
    "liudong_zichan",
    "guding_zichan",
    "wuxing_zichan",
    "liudong_fuzhai",
    "changqi_fuzhai",
    "ziben_gongjijin",
    "jing_zichan",
    "zhuying_shouru",
    "zhuying_lirun",
    "yingshou_zhangkuan",
    "yingye_lirun",
    "touzi_shouyu",
    "jingying_xianjinliu",
    "zong_xianjinliu",
    "cunhuo",
    "lirun_zonghe",
    "shuihou_lirun",
    "jing_lirun",
    "weifen_lirun",
    "meigujing_zichan",
)

DDL = """
PRAGMA user_version=1;
CREATE TABLE stock_financial_reports (
    symbol TEXT NOT NULL,
    period_end TEXT NOT NULL CHECK(length(period_end)=10),
    source TEXT NOT NULL,
    liutong_guben REAL, zong_guben REAL, guojia_gu REAL,
    faqiren_faren_gu REAL, faren_gu REAL, b_gu REAL, h_gu REAL, zhigong_gu REAL,
    province INTEGER, industry INTEGER, ipo_date TEXT,
    zong_zichan REAL, liudong_zichan REAL, guding_zichan REAL, wuxing_zichan REAL,
    liudong_fuzhai REAL, changqi_fuzhai REAL, ziben_gongjijin REAL, jing_zichan REAL,
    zhuying_shouru REAL, zhuying_lirun REAL, yingshou_zhangkuan REAL,
    yingye_lirun REAL, touzi_shouyu REAL, jingying_xianjinliu REAL,
    zong_xianjinliu REAL, cunhuo REAL, lirun_zonghe REAL, shuihou_lirun REAL,
    jing_lirun REAL, weifen_lirun REAL, meigujing_zichan REAL,
    source_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,period_end,source)
) STRICT, WITHOUT ROWID;
CREATE INDEX financial_reports_by_period
    ON stock_financial_reports(period_end,symbol);

CREATE TABLE stock_shareholder_counts (
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL CHECK(length(as_of_date)=10),
    source TEXT NOT NULL,
    shareholder_count REAL NOT NULL CHECK(shareholder_count>=0),
    source_report_date TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,as_of_date,source)
) STRICT, WITHOUT ROWID;
CREATE INDEX shareholder_counts_by_date
    ON stock_shareholder_counts(as_of_date,symbol);

CREATE TABLE dataset_state (
    dataset TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    max_date TEXT,
    updated_at INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
"""


@contextmanager
def fundamentals_connection(root, *, create=False, read_only=True):
    root = Path(root).expanduser().resolve()
    path = root / "fundamentals.sqlite"
    if create and not path.exists():
        if read_only:
            raise ValueError("Cannot create a read-only database")
        root.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
    mode = "ro" if read_only else "rw"
    from .pool import pool_lock

    contexts = ExitStack()
    contexts.enter_context(pool_lock(root, write=not read_only))
    try:
        conn = sqlite3.connect(path.as_uri() + f"?mode={mode}", uri=True, timeout=30)
    except BaseException:
        contexts.close()
        raise

    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA cache_size=-8192")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        if create and conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            conn.executescript(DDL)
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported fundamentals schema")
        yield conn
    finally:
        conn.close()
        contexts.close()


def _source_date(value):
    raw = str(int(value)) if value is not None and not pd.isna(value) else ""
    if len(raw) != 8:
        raise ValueError("finance.updated_date is unavailable")
    return datetime.strptime(raw, "%Y%m%d").date().isoformat()


def _optional_date(value):
    try:
        return _source_date(value)
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_finance(symbol, raw, *, fetched_at, source="tdxman:finance"):
    row = dict(raw)
    code, market = symbol.split(".")
    if str(row.get("code")) != code:
        raise ValueError("Finance response identity mismatch")
    returned_market = row.get("market")
    if returned_market is not None:
        explicit_name = getattr(returned_market, "name", None)
        if isinstance(returned_market, str):
            name = returned_market
        elif isinstance(explicit_name, str):
            name = explicit_name
        else:
            from tdxman.models.enums import Market

            try:
                name = Market(int(returned_market)).name
            except (TypeError, ValueError) as exc:
                raise ValueError("Finance response market mismatch") from exc
        if name != market:
            raise ValueError("Finance response market mismatch")
    period_end = _source_date(row.get("updated_date"))
    payload = {}
    for key in FINANCE_FIELDS:
        value = row.get(key)
        if value is None or pd.isna(value):
            payload[key] = None
            continue
        if key in {"province", "industry"}:
            payload[key] = int(value)
        elif key == "ipo_date":
            payload[key] = _optional_date(value)
        else:
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"Non-finite finance field: {key}")
            payload[key] = value
    document = json.dumps(
        {"symbol": symbol, "period_end": period_end, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    result = dict(
        symbol=symbol,
        period_end=period_end,
        source=source,
        **payload,
        source_hash=hashlib.sha256(document.encode()).hexdigest(),
        payload_json=document,
        published_at=None,
        fetched_at=fetched_at,
    )
    count = row.get("gudong_renshu")
    if count is not None and not pd.isna(count):
        count = float(count)
        if not math.isfinite(count) or count < 0:
            raise ValueError("Invalid shareholder count")
        result["shareholder_count"] = count
    return result


def write_finance_rows(conn, rows):
    if conn.in_transaction:
        raise ValueError("Writer requires an idle connection")
    columns = [
        "symbol",
        "period_end",
        "source",
        *FINANCE_FIELDS,
        "source_hash",
        "payload_json",
        "published_at",
        "fetched_at",
        "updated_at",
    ]
    changed_reports = changed_counts = 0
    stamp = time.time_ns() // 1000
    try:
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            old = conn.execute(
                "SELECT source_hash FROM stock_financial_reports "
                "WHERE symbol=? AND period_end=? AND source=?",
                (row["symbol"], row["period_end"], row["source"]),
            ).fetchone()
            if old is None or old[0] != row["source_hash"]:
                values = [row.get(name) for name in columns[:-1]] + [stamp]
                conn.execute(
                    "INSERT INTO stock_financial_reports("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ") ON CONFLICT(symbol,period_end,source) "
                    "DO UPDATE SET " + ",".join(f"{name}=excluded.{name}" for name in columns[3:]),
                    values,
                )
                changed_reports += 1
            if "shareholder_count" in row:
                old_count = conn.execute(
                    "SELECT shareholder_count FROM stock_shareholder_counts "
                    "WHERE symbol=? AND as_of_date=? AND source=?",
                    (row["symbol"], row["period_end"], row["source"]),
                ).fetchone()
                if old_count is None or old_count[0] != row["shareholder_count"]:
                    conn.execute(
                        "INSERT INTO stock_shareholder_counts VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(symbol,as_of_date,source) DO UPDATE SET "
                        "shareholder_count=excluded.shareholder_count,"
                        "source_report_date=excluded.source_report_date,"
                        "published_at=excluded.published_at,fetched_at=excluded.fetched_at,"
                        "updated_at=excluded.updated_at",
                        (
                            row["symbol"],
                            row["period_end"],
                            row["source"],
                            row["shareholder_count"],
                            row["period_end"],
                            None,
                            row["fetched_at"],
                            stamp,
                        ),
                    )
                    changed_counts += 1
        for dataset, changed, table, field in (
            ("stock_financial_reports", changed_reports, "stock_financial_reports", "period_end"),
            ("stock_shareholder_counts", changed_counts, "stock_shareholder_counts", "as_of_date"),
        ):
            if not changed:
                continue
            maximum = conn.execute(f"SELECT max({field}) FROM {table}").fetchone()[0]
            conn.execute(
                "INSERT INTO dataset_state VALUES (?,?,?,?) ON CONFLICT(dataset) DO UPDATE SET "
                "revision=dataset_state.revision+1,max_date=excluded.max_date,"
                "updated_at=excluded.updated_at",
                (dataset, 1, maximum, stamp),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"financial_reports": changed_reports, "shareholder_counts": changed_counts}


def _read(root, table, *, symbols=None, start=None, end=None, date_field):
    clauses, params = [], []
    if symbols is not None:
        values = [symbols] if isinstance(symbols, str) else list(symbols)
        if not values:
            clauses.append("0")
        else:
            clauses.append("symbol IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
    if start is not None:
        clauses.append(f"{date_field}>=?")
        params.append(date.fromisoformat(str(start)[:10]).isoformat())
    if end is not None:
        clauses.append(f"{date_field}<=?")
        params.append(date.fromisoformat(str(end)[:10]).isoformat())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        with fundamentals_connection(root) as conn:
            frame = pd.read_sql_query(
                f"SELECT * FROM {table}{where} ORDER BY symbol,{date_field} LIMIT 500001",
                conn,
                params=params,
            )
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        raise DataPoolError("FUNDAMENTALS_NOT_READY", "Fundamentals store is unavailable") from exc
    if len(frame) > 500_000:
        raise DataPoolError("FUNDAMENTALS_TOO_LARGE", "Use a smaller date or symbol range")
    if date_field in frame:
        frame[date_field] = pd.to_datetime(frame[date_field])
    frame.attrs.update(source="aspool.fundamentals.sqlite", point_in_time=False)
    return frame


def read_financial_reports(root, **kwargs):
    frame = _read(root, "stock_financial_reports", date_field="period_end", **kwargs)
    # Existing period_end keys came from source updated_date, not a verified report period.
    frame["source_updated_date"] = frame["period_end"]
    frame["report_period_end"] = pd.NaT
    frame.attrs.update(date_filter_basis="source_updated_date",
                       legacy_period_end_semantics="source_updated_date",
                       report_period_status="not_provided", known_at_field="published_at",
                       shares_unit="share", financial_unit_status="provider_conversion",
                       missing_announcement_semantics="not_known_at_historical_date")
    return frame


def read_shareholder_counts(root, **kwargs):
    return _read(root, "stock_shareholder_counts", date_field="as_of_date", **kwargs)


def fundamentals_status(root):
    path = Path(root).expanduser().resolve() / "fundamentals.sqlite"
    if not path.is_file():
        return {"ready": False, "path": str(path), "datasets": {}}
    with fundamentals_connection(root) as conn:
        datasets = {}
        for name, field in (
            ("stock_financial_reports", "period_end"),
            ("stock_shareholder_counts", "as_of_date"),
        ):
            rows, symbols, first, last = conn.execute(
                f"SELECT count(*),count(DISTINCT symbol),min({field}),max({field}) FROM {name}"
            ).fetchone()
            datasets[name] = dict(rows=rows, symbols=symbols, start=first, end=last)
    return {"ready": True, "path": str(path), "datasets": datasets}
