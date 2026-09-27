"""DG-03 complete-schema monthly alternative; isolated evaluation roots only.

Immutable month files are selected by a catalog manifest. Publication atomically
replaces a fully checkpointed private catalog under the shared pool lock. This
reference protocol copies the complete catalog: its cost is deliberately visible.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from .api_contract import public_read
from .dg03_candidate import (
    CandidateEventAmountBatches,
    CandidatePool,
    copy_ancillary,
    ident,
    literal,
    normalized_row,
)
from .pool import pool_lock


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sync(path, directory=False):
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def connection(root, read_only=False):
    c = duckdb.connect(str(Path(root) / "catalog.duckdb"), read_only=read_only)
    c.execute("SET threads=1")
    c.execute("SET memory_limit='1GB'")
    return c


def month(day):
    return str(day)[:7]


def part_path(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to((Path(root) / "parts").resolve()):
        raise ValueError("manifest part outside root")
    return path


def month_bounds(key):
    year, number = map(int, key.split("-"))
    return date(year, number, 1), date(year + int(number == 12), number % 12 + 1, 1)


def initialize(snapshot, target, schema_source):
    target = Path(target).resolve()
    if target.is_relative_to(Path(snapshot).resolve()) or str(target).startswith(
        str(Path.home() / ".aspool")
    ):
        raise ValueError("unsafe monthly target")
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(snapshot) / "catalog.duckdb", target / "catalog.duckdb")
    shutil.copy2(Path(schema_source) / "source-schema.json", target / "source-schema.json")
    copy_ancillary(snapshot, target)
    (target / "parts").mkdir()
    (target / "history").mkdir()
    (target / "work").mkdir()
    with connection(target) as c:
        c.execute("CREATE TABLE dg03_revision(id INTEGER PRIMARY KEY,revision BIGINT)")
        c.execute("INSERT INTO dg03_revision VALUES (1,0)")
        c.execute(
            "CREATE TABLE dg03_changes(revision BIGINT,market VARCHAR,code VARCHAR,trade_date DATE,"
            "operation VARCHAR,source VARCHAR,reason VARCHAR,"
            "before_json VARCHAR,after_json VARCHAR)"
        )
        c.execute(
            "CREATE TABLE dg03_monthly_parts(month VARCHAR PRIMARY KEY,path VARCHAR NOT NULL,"
            "first_day DATE,last_day DATE,row_count BIGINT,sha256 VARCHAR)"
        )
    return target


def install_part(c, root, key, query):
    path = Path(root) / "parts" / f"{key}-{uuid.uuid4().hex}.parquet"
    c.execute(
        f"COPY ({query} ORDER BY __market,__code,trade_date) TO {literal(path)} "
        "(FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 32768)"
    )
    sync(path)
    n, first, last, unique_keys = c.execute(
        f"SELECT count(*),min(trade_date),max(trade_date),"
        f"count(DISTINCT (__market,__code,trade_date)) FROM read_parquet({literal(path)})"
    ).fetchone()
    lo, hi = month_bounds(key)
    assert n == unique_keys and (not n or lo <= first <= last < hi)
    if not n:
        path.unlink()
        c.execute("DELETE FROM dg03_monthly_parts WHERE month=?", [key])
        return None
    c.execute(
        "INSERT INTO dg03_monthly_parts VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(month) DO UPDATE SET path=excluded.path,first_day=excluded.first_day,"
        "last_day=excluded.last_day,row_count=excluded.row_count,sha256=excluded.sha256",
        [key, str(path.relative_to(root)), first, last, n, digest(path)],
    )
    return path


class MonthlyStorage:
    def __init__(self, root):
        self.root = Path(root)

    def bind(self, conn, name, *, symbols=None, start=None, end=None, fields=None):
        conn.execute("SET threads=1")
        conn.execute(f"ATTACH {literal(self.root / 'catalog.duckdb')} AS candidate (READ_ONLY)")
        conditions, args = [], []
        if start:
            conditions.append("last_day >= ?")
            args.append(start)
        if end:
            conditions.append("first_day <= ?")
            args.append(end)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        files = [
            str(part_path(self.root, r[0]))
            for r in conn.execute(
                "SELECT path FROM candidate.dg03_monthly_parts" + where + " ORDER BY month", args
            ).fetchall()
        ]
        files = files or [str(self.root / "empty.parquet")]
        relation = conn.read_parquet(files, hive_partitioning=False, union_by_name=False)
        columns = [
            ident(n)
            for n in relation.columns
            if n not in {"symbol", "market", "__market", "__code"}
        ]
        columns += ["__market AS market", "__code AS symbol"]
        conn.execute(
            f"CREATE TEMP VIEW {ident(name)} AS SELECT "
            + ",".join(columns)
            + " FROM ("
            + relation.sql_query()
            + ")"
        )
        return [Path(p) for p in files]

    def files(self, *args):
        return [self.root / "empty.parquet"]

    def has_data(self):
        return True

    def has_field(self, field):
        return field in pq.ParquetFile(self.root / "empty.parquet").schema_arrow.names


class WorkPool(CandidatePool):
    write_threads = 1


class MonthlyPool(CandidatePool):
    storage_type = MonthlyStorage

    def iter_limit_events_with_amount(self, **kwargs):
        return MonthlyEventAmountBatches(self.root, **kwargs)

    @public_read
    def read_etf_daily(self, **kwargs):
        from .dg03_etf import read_etf_daily

        return read_etf_daily(self.root, storage_type=MonthlyStorage, **kwargs)

    @public_read
    def list_etfs(self, *, symbols=None):
        from .dg03_etf import list_etfs

        return list_etfs(self.root, symbols, storage_type=MonthlyStorage)

    def apply(self, changes, *, source, reason, _fault_hook=None):
        if not source or not reason:
            raise ValueError("source and reason required")
        changes = list(changes)
        touched = sorted(
            {month(r["trade_date"]) for r in changes if r.get("domain", "bars") == "bars"}
        )
        with pool_lock(self.root, write=True):
            with connection(self.root, True) as base:
                parts = {
                    key: str(part_path(self.root, path))
                    for key, path in base.execute(
                        "SELECT month,path FROM dg03_monthly_parts"
                    ).fetchall()
                }
            paths = [parts[key] for key in touched if key in parts] or [
                str(self.root / "empty.parquet")
            ]
            # Detect semantic no-ops before copying any persistent catalog/files.
            with duckdb.connect() as check:
                check.execute("SET threads=1; SET memory_limit='1GB'")
                check.execute(
                    f"ATTACH {literal(self.root / 'catalog.duckdb')} AS prior (READ_ONLY)"
                )
                check.execute(
                    "CREATE TEMP TABLE bars AS "
                    + check.read_parquet(
                        paths, hive_partitioning=False, union_by_name=False
                    ).sql_query()
                )
                changed, seen, changed_months = False, set(), set()
                for item in changes:
                    market, code, day = (
                        item["market"],
                        item["code"],
                        pd.Timestamp(item["trade_date"]).date(),
                    )
                    domain = item.get("domain", "bars")
                    if domain not in {"bars", "facts"}:
                        raise ValueError("unknown domain")
                    identity = (domain, market, code, day)
                    if identity in seen:
                        raise ValueError("duplicate change key")
                    seen.add(identity)
                    table = "bars" if domain == "bars" else "prior.security_daily_facts"
                    names = [r[0] for r in check.execute(f"DESCRIBE {table}").fetchall()]
                    predicate = (
                        "__market=? AND __code=? AND trade_date=?"
                        if domain == "bars"
                        else "symbol=? AND trade_date=?"
                    )
                    key = [market, code, day] if domain == "bars" else [f"{code}.{market}", day]
                    old = check.execute(f"SELECT * FROM {table} WHERE {predicate}", key).fetchone()
                    before = dict(zip(names, old)) if old else None
                    _, after, _ = normalized_row(item, names, before, market, code, day, domain)
                    changed |= before != after
                    if before != after and domain == "bars":
                        changed_months.add(month(day))
                if not changed:
                    revision = check.execute("SELECT revision FROM prior.dg03_revision").fetchone()[
                        0
                    ]
                    return {
                        "changed": 0,
                        "revision": revision,
                        "published_months": 0,
                        "catalog_copied_bytes": 0,
                    }
            stage = self.root / "work" / uuid.uuid4().hex
            stage.mkdir()
            shutil.copy2(self.root / "catalog.duckdb", stage / "catalog.duckdb")
            os.link(self.root / "source-schema.json", stage / "source-schema.json")
            paths = [parts[key] for key in touched if key in parts] or [
                str(self.root / "empty.parquet")
            ]
            with connection(stage) as c:
                rel = c.read_parquet(paths, hive_partitioning=False, union_by_name=False)
                c.execute("CREATE TABLE dg03_daily AS " + rel.sql_query())
                c.execute("ALTER TABLE dg03_daily ADD PRIMARY KEY(__market,__code,trade_date)")
                c.execute("CREATE INDEX dg03_security ON dg03_daily(__code)")
            result = WorkPool(stage).apply(changes, source=source, reason=reason)
            if not result["changed"]:
                shutil.rmtree(stage)
                return {
                    **result,
                    "published_months": 0,
                    "catalog_copied_bytes": (self.root / "catalog.duckdb").stat().st_size,
                }
            with connection(stage) as c:
                # Boundary repairs must include untouched months. This deliberately
                # scans only key/date columns, never treats a month as whole history.
                affected = sorted(
                    {
                        (r["market"], r["code"])
                        for r in changes
                        if r.get("domain", "bars") == "bars" and r.get("operation") == "delete"
                    }
                )
                if "coverage" not in {row[0] for row in c.execute("SHOW TABLES").fetchall()}:
                    affected = []
                other = [path for key, path in parts.items() if key not in touched]
                if affected and other:
                    c.execute(
                        "CREATE TEMP VIEW unchanged AS "
                        + c.read_parquet(
                            other, hive_partitioning=False, union_by_name=False
                        ).sql_query()
                    )
                elif affected:
                    c.execute("CREATE TEMP VIEW unchanged AS SELECT * FROM dg03_daily WHERE false")
                for market, code in affected:
                    # Corrections do not change coverage; ordinary appends can use
                    # the tested delta path. Recompute only explicit deletions.
                    if not any(
                        r.get("operation") == "delete"
                        and r["market"] == market
                        and r["code"] == code
                        for r in changes
                    ):
                        continue
                    first, last, n = c.execute(
                        "SELECT min(trade_date),max(trade_date),count(*) FROM ("
                        "SELECT __market,__code,trade_date FROM unchanged UNION ALL "
                        "SELECT __market,__code,trade_date FROM dg03_daily) "
                        "WHERE __market=? AND __code=?",
                        [market, code],
                    ).fetchone()
                    if n:
                        c.execute(
                            "INSERT INTO coverage VALUES (?,?,?,?,?,?,current_timestamp) "
                            "ON CONFLICT(symbol) DO UPDATE SET market=excluded.market,"
                            "start_date=excluded.start_date,end_date=excluded.end_date,"
                            "row_count=excluded.row_count,source=excluded.source,"
                            "updated_at=excluded.updated_at",
                            [code, market, first, last, n, source],
                        )
                    else:
                        c.execute(
                            "DELETE FROM coverage WHERE market=? AND symbol=?", [market, code]
                        )
                for key in sorted(changed_months):
                    lo, hi = month_bounds(key)
                    install_part(
                        c,
                        self.root,
                        key,
                        "SELECT * FROM dg03_daily WHERE "
                        f"trade_date >= DATE {literal(lo)} AND trade_date < DATE {literal(hi)}",
                    )
                c.execute("DROP TABLE dg03_daily")
                c.execute("CHECKPOINT")
            sync(self.root / "parts", directory=True)
            sync(stage / "catalog.duckdb")
            if _fault_hook:
                _fault_hook("before_publish")
            previous = self.root / "history" / f"catalog-{uuid.uuid4().hex}.duckdb"
            os.link(self.root / "catalog.duckdb", previous)
            sync(self.root / "history", directory=True)
            copied = (stage / "catalog.duckdb").stat().st_size
            os.replace(stage / "catalog.duckdb", self.root / "catalog.duckdb")
            sync(self.root, directory=True)
            if _fault_hook:
                _fault_hook("after_publish")
            shutil.rmtree(stage)
            return {
                **result,
                "published_months": len(changed_months),
                "catalog_copied_bytes": copied,
                "previous_catalog": str(previous),
            }


class MonthlyEventAmountBatches(CandidateEventAmountBatches):
    """Keep publication/revision checks while reading only event date months."""

    def _attach_amount(self, frame, lo, hi):
        import numpy as np

        from .api_contract import DataPoolError

        with pool_lock(self.root), duckdb.connect() as c:
            self._configure(c)
            MonthlyStorage(self.root).bind(c, "daily", start=lo, end=hi)
            c.register("event_keys", frame[["trade_date", "symbol"]])
            amount = c.execute(
                "SELECT e.trade_date,e.symbol,d.amount FROM event_keys e JOIN daily d "
                "ON e.trade_date=d.trade_date AND e.symbol=d.symbol||'.'||d.market "
                "WHERE d.trade_date BETWEEN ? AND ?",
                [lo, hi],
            ).fetchdf()
        finite = amount.amount.dropna().to_numpy(dtype=float)
        if not np.isfinite(finite).all() or (finite < 0).any():
            raise DataPoolError("DAILY_INVALID", "Invalid daily amount")
        return frame.merge(amount, on=["trade_date", "symbol"], how="left", validate="one_to_one")
