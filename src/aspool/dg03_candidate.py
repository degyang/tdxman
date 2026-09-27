"""Isolated DG-03 experiment. Never selected by production DataPool.

The public read implementations below are frozen copies of be60d78 with only
DailyStorage replaced. This keeps validation/overlay/error/metadata semantics
inspectable without a production registry or process-global monkey patch.
"""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from .api_contract import DAILY_FIELDS, OPTIONAL_FIELDS, DataPoolError, public_read
from .limit_amount import EventAmountBatches
from .pool import DataPool, _normalize_symbols, _overlay_dated_fields, pool_lock
from .store import read_only_catalog


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class CandidateStorage:
    def __init__(self, root):
        self.root = Path(root)

    def bind(self, conn, name, *, symbols=None, start=None, end=None):
        conn.execute(f"ATTACH {literal(self.root / 'catalog.duckdb')} AS candidate (READ_ONLY)")
        names = [r[0] for r in conn.execute("DESCRIBE candidate.dg03_daily").fetchall()]
        columns = [ident(n) for n in names if n not in {"symbol", "market", "__market", "__code"}]
        columns += ["__market AS market", "__code AS symbol"]
        clauses = []
        if start:
            clauses.append(f"trade_date >= DATE {literal(start)}")
        if end:
            clauses.append(f"trade_date <= DATE {literal(end)}")
        if symbols is not None:
            clauses.append(
                "("
                + (
                    " OR ".join(
                        f"(__market={literal(s.split('.')[0])} "
                        f"AND __code={literal(s.split('.')[1])})"
                        for s in symbols
                    )
                    or "false"
                )
                + ")"
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        conn.execute(
            f"CREATE TEMP VIEW {ident(name)} AS SELECT "
            + ",".join(columns)
            + " FROM candidate.dg03_daily"
            + where
        )
        return [self.root / "catalog.duckdb"]

    def has_data(self):
        return True

    def files(self, *args):
        return [self.root / "catalog.duckdb"]

    def has_field(self, field):
        with duckdb.connect(str(self.root / "catalog.duckdb"), read_only=True) as c:
            return field in {r[0] for r in c.execute("DESCRIBE dg03_daily").fetchall()}


DailyStorage = CandidateStorage


def copy_ancillary(snapshot, target):
    """Keep existing index/fundamental/adjustment files on their unchanged APIs."""
    lake = Path(snapshot) / "lake"
    if not lake.exists():
        return
    for directory in lake.iterdir():
        if directory.name != "bars":
            shutil.copytree(directory, Path(target) / "lake" / directory.name)


def build(snapshot, target, *, limit=None):
    """Copy the complete catalog, preserving its constraints; retain every raw field."""
    snapshot, target = Path(snapshot).resolve(), Path(target).resolve()
    if (
        target == snapshot
        or snapshot in target.parents
        or str(target).startswith(str(Path.home() / ".aspool"))
    ):
        raise ValueError("unsafe experiment target")
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(snapshot / "catalog.duckdb", target / "catalog.duckdb")
    copy_ancillary(snapshot, target)
    files = sorted((snapshot / "lake/bars/daily").glob("market=*/symbol=*/**/bars.parquet"))
    if limit:
        files = files[:limit]
    inventory = []
    for p in files:
        f = pq.ParquetFile(p)
        inventory.append(
            {
                "path": str(p.relative_to(snapshot)),
                "rows": f.metadata.num_rows,
                "bytes": p.stat().st_size,
                "arrow_schema_base64": base64.b64encode(
                    f.schema_arrow.serialize().to_pybytes()
                ).decode(),
            }
        )
    (target / "source-schema.json").write_text(json.dumps(inventory, indent=2))
    with duckdb.connect(str(target / "catalog.duckdb")) as c:
        c.execute("SET threads=2; SET memory_limit='1GB'")
        relation = c.read_parquet(
            [str(p) for p in files], union_by_name=True, hive_partitioning=False, filename=True
        )
        c.execute("CREATE TEMP VIEW incoming AS " + relation.sql_query())
        query = """SELECT * EXCLUDE(filename),
            regexp_extract(filename, 'market=([^/]+)', 1) AS __market,
            regexp_extract(filename, 'symbol=([^/]+)', 1) AS __code FROM incoming"""
        c.execute("CREATE TABLE dg03_daily AS " + query + " ORDER BY trade_date,__market,__code")
        c.execute("ALTER TABLE dg03_daily ADD PRIMARY KEY (__market,__code,trade_date)")
        c.execute("CREATE INDEX dg03_security ON dg03_daily(__code)")
        c.execute("CREATE TABLE dg03_revision(id INTEGER PRIMARY KEY, revision BIGINT)")
        c.execute("INSERT INTO dg03_revision VALUES (1,0)")
        c.execute("""CREATE TABLE dg03_changes(revision BIGINT, market VARCHAR, code VARCHAR,
                     trade_date DATE, operation VARCHAR, source VARCHAR, reason VARCHAR,
                     before_json VARCHAR, after_json VARCHAR)""")
        c.execute("CHECKPOINT")
    return inventory


class CandidatePool(DataPool):
    """Explicit experiment-only API; callers supply an independent candidate root."""

    def __init__(self, root):
        super().__init__(root)
        if not (self.root / "source-schema.json").exists():
            # Growth/recovery copies opt in by copying the same schema manifest.
            raise ValueError("candidate requires its source-schema.json manifest")

    def apply(self, changes, *, source, reason, _fault_hook=None):
        """Explicit raw fact changes; NULL merge ignored, clear/delete require operations.

        Derived recomputation remains DG-05: record conservative stale suffixes when
        present, never clear them. No network or production writer uses this API.
        """
        if not source or not reason:
            raise ValueError("source and reason required")
        with (
            pool_lock(self.root, write=True),
            duckdb.connect(str(self.root / "catalog.duckdb")) as c,
        ):
            c.execute("SET threads=2; SET memory_limit='1GB'")
            c.execute("BEGIN")
            committed = False
            try:
                revision = c.execute("SELECT revision FROM dg03_revision").fetchone()[0]
                raw_names = [r[0] for r in c.execute("DESCRIBE dg03_daily").fetchall()]
                records = []
                seen = set()
                for change in changes:
                    market, code, day = change["market"], change["code"], change["trade_date"]
                    day = pd.Timestamp(day).date()
                    key = [market, code, day]
                    domain = change.get("domain", "bars")
                    if domain not in {"bars", "facts"}:
                        raise ValueError("unknown domain")
                    identity = (domain, market, code, day)
                    if identity in seen:
                        raise ValueError("duplicate change key")
                    seen.add(identity)
                    table = "dg03_daily" if domain == "bars" else "security_daily_facts"
                    names = (
                        raw_names
                        if domain == "bars"
                        else [r[0] for r in c.execute("DESCRIBE security_daily_facts").fetchall()]
                    )
                    predicate = (
                        "__market=? AND __code=? AND trade_date=?"
                        if domain == "bars"
                        else "symbol=? AND trade_date=?"
                    )
                    query_key = key if domain == "bars" else [f"{code}.{market}", day]
                    old = c.execute(
                        f"SELECT * FROM {table} WHERE {predicate}", query_key
                    ).fetchone()
                    before = dict(zip(names, old)) if old else None
                    operation = change.get("operation", "merge")
                    if operation not in {"merge", "clear", "delete"}:
                        raise ValueError("unknown change operation")
                    after = None if operation == "delete" else dict(before or dict.fromkeys(names))
                    if after is not None:
                        values = change.get("values", {})
                        immutable = (
                            {"__market", "__code", "trade_date"}
                            if domain == "bars"
                            else {"symbol", "trade_date"}
                        )
                        if set(values) - (set(names) - immutable):
                            raise ValueError("unknown or immutable field")
                        for field, value in values.items():
                            if operation == "merge" and (value is None or pd.isna(value)):
                                continue
                            if (
                                field == "pre_close"
                                and value is not None
                                and (
                                    not isinstance(value, (int, float))
                                    or not float("-inf") < value < float("inf")
                                    or value <= 0
                                )
                            ):
                                raise ValueError("invalid pre_close")
                            if (
                                field == "is_st"
                                and value is not None
                                and not isinstance(value, bool)
                            ):
                                raise ValueError("invalid is_st")
                            after[field] = None if operation == "clear" else value
                        if domain == "facts":
                            after.update(symbol=f"{code}.{market}", trade_date=day)
                            if not after.get("source") or after.get("fetched_at") is None:
                                raise ValueError("facts require source and fetched_at")
                        else:
                            after.update(__market=market, __code=code, trade_date=day)
                            required = ["open", "high", "low", "close", "volume", "amount"]
                            if any(
                                after[k] is None or not 0 <= after[k] < float("inf")
                                for k in required
                            ):
                                raise ValueError("invalid OHLCV/amount")
                            if after["high"] < after["low"]:
                                raise ValueError("high below low")
                    if before == after:
                        continue
                    # UPDATE avoids DuckDB delete/reinsert PK conflicts in a transaction.
                    if after is None:
                        c.execute(
                            f"DELETE FROM {table} WHERE {predicate}",
                            query_key,
                        )
                    elif before is None:
                        c.execute(
                            f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in names) + ")",
                            [after[n] for n in names],
                        )
                    else:
                        fields = [n for n in names if n not in immutable and before[n] != after[n]]
                        c.execute(
                            f"UPDATE {table} SET "
                            + ",".join(ident(n) + "=?" for n in fields)
                            + f" WHERE {predicate}",
                            [after[n] for n in fields] + query_key,
                        )
                    records.append(
                        [
                            revision + 1,
                            *key,
                            ("facts:" if domain == "facts" else "") + operation,
                            source,
                            reason,
                            json.dumps(before, default=str),
                            json.dumps(after, default=str),
                        ]
                    )
                if records:
                    c.executemany("INSERT INTO dg03_changes VALUES (?,?,?,?,?,?,?,?,?)", records)
                    c.execute("UPDATE dg03_revision SET revision=revision+1")
                    tables = {r[0] for r in c.execute("SHOW TABLES").fetchall()}
                    if "daily_limit_staleness" in tables:
                        first = min(r[3] for r in records)
                        c.execute(
                            """INSERT INTO daily_limit_staleness
                            SELECT trade_date, ?, current_timestamp FROM daily_limit_publication
                            WHERE trade_date >= ? ON CONFLICT DO NOTHING""",
                            [reason, first],
                        )
                    if "coverage" in tables:
                        grouped = {}
                        for record in records:
                            if not record[4].startswith("facts:"):
                                grouped.setdefault((record[1], record[2]), []).append(record)
                        for (market, code), key_records in sorted(grouped.items()):
                            added = [r[3] for r in key_records if r[7] == "null"]
                            removed = [r[3] for r in key_records if r[8] == "null"]
                            if not added and not removed:
                                continue
                            old_extent = c.execute(
                                "SELECT start_date,end_date,row_count FROM coverage "
                                "WHERE market=? AND symbol=?",
                                [market, code],
                            ).fetchone()
                            if old_extent and not ({old_extent[0], old_extent[1]} & set(removed)):
                                # Ordinary appends and interior deletions use metadata only.
                                extent = (
                                    min([old_extent[0], *added]),
                                    max([old_extent[1], *added]),
                                    old_extent[2] + len(added) - len(removed),
                                )
                            else:
                                # Boundary deletion or missing coverage is an explicit repair path.
                                extent = c.execute(
                                    """SELECT min(trade_date),max(trade_date),count(*)
                                    FROM dg03_daily WHERE __market=? AND __code=?""",
                                    [market, code],
                                ).fetchone()
                            if extent[2]:
                                c.execute(
                                    """INSERT INTO coverage VALUES (?,?,?,?,?,?,current_timestamp)
                                    ON CONFLICT(symbol) DO UPDATE SET market=excluded.market,
                                    start_date=excluded.start_date,end_date=excluded.end_date,
                                    row_count=excluded.row_count,source=excluded.source,
                                    updated_at=excluded.updated_at""",
                                    [code, market, *extent, source],
                                )
                            else:
                                c.execute(
                                    "DELETE FROM coverage WHERE symbol=? AND market=?",
                                    [code, market],
                                )
                if _fault_hook:
                    _fault_hook("before_commit")
                c.execute("COMMIT")
                committed = True
                if _fault_hook:
                    _fault_hook("after_commit")
                return {"changed": len(records), "revision": revision + bool(records)}
            except BaseException:
                if not committed:
                    c.execute("ROLLBACK")
                raise

    @public_read
    def read_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        selected = (
            list(DAILY_FIELDS)
            if fields is None
            else ([fields] if isinstance(fields, str) else list(fields))
        )
        if (
            not selected
            or len(set(selected)) != len(selected)
            or set(selected) - DAILY_FIELDS.keys()
        ):
            raise DataPoolError(
                "FIELD_UNSUPPORTED", "fields must contain unique public daily fields"
            )
        if lookback is not None and (
            isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1
        ):
            raise DataPoolError("INVALID_ARGUMENT", "lookback must be a positive integer")
        try:
            start = pd.Timestamp(start).date() if start is not None else None
            end = pd.Timestamp(end).date() if end is not None else None
            if (start is not None and pd.isna(start)) or (end is not None and pd.isna(end)):
                raise ValueError("Missing date boundary")
        except (ValueError, TypeError) as exc:
            raise DataPoolError("INVALID_ARGUMENT", "Invalid date boundary") from exc
        if start and end and start > end:
            raise DataPoolError("INVALID_ARGUMENT", "start must not be after end")
        normalized = _normalize_symbols(symbols)
        with pool_lock(self.root):
            storage = DailyStorage(self.root)
            with (
                tempfile.TemporaryDirectory(prefix="aspool-daily-") as temp,
                duckdb.connect() as conn,
            ):
                conn.execute("SET memory_limit = '512MB'")
                conn.execute("SET threads = 2")
                conn.execute("SET temp_directory = ?", [temp])
                try:
                    files = storage.bind(
                        conn,
                        "bars",
                        symbols=normalized if symbols is not None else None,
                        start=start,
                        end=end,
                    )
                except ValueError as exc:
                    raise DataPoolError("DAILY_INVALID", str(exc)) from exc
                if (
                    not files
                    and not storage.files(normalized if symbols is not None else None)
                    and not storage.has_data()
                ):
                    raise DataPoolError("DAILY_NOT_FOUND", "No daily bars in aspool")
                names = {row[0] for row in conn.execute("describe bars").fetchall()}
                market, code = "market", "symbol"
                asset_filter = '"asset_type" IS NULL OR "asset_type" <> \'etf\''
                rate = (
                    "turnover_rate"
                    if "turnover_rate" in names
                    else "turnover"
                    if "turnover" in names
                    else "NULL"
                )
                if "turnover_rate" in names and "turnover" in names:
                    rate = "coalesce(turnover_rate, turnover)"
                volume = "volume" if "volume" in names else "NULL"
                if "vol" in names:
                    volume = f"coalesce({volume}, vol)"
                clauses, params = [], []
                if "asset_type" in names:
                    clauses.append(f"({asset_filter})")
                if start:
                    clauses.append("trade_date >= ?")
                    params.append(start)
                if end:
                    clauses.append("trade_date <= ?")
                    params.append(end)
                if symbols is not None:
                    clauses.append(f"{market} || '.' || {code} IN (SELECT unnest(?))")
                    params.append(normalized)
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                if not lookback:
                    count = conn.execute(f"SELECT count(*) FROM bars{where}", params).fetchone()[0]
                    if count > 500_000:
                        raise DataPoolError(
                            "DAILY_TOO_LARGE",
                            "DataFrame read exceeds 500000 rows; "
                            "use bounded date windows or iter_limit_events_with_amount",
                        )
                # Validate the filtered population before a window can hide duplicates.
                duplicate = conn.execute(
                    f"SELECT {market}, {code}, trade_date FROM bars{where} "
                    f"GROUP BY {market}, {code}, trade_date HAVING count(*) > 1 LIMIT 1",
                    params,
                ).fetchone()
                if duplicate is not None:
                    raise DataPoolError("DAILY_INVALID", "Duplicate daily keys")
                selected_rows = f"SELECT * FROM bars{where}"
                query_params = list(params)
                if lookback:
                    selected_rows += (
                        " QUALIFY row_number() OVER "
                        "(PARTITION BY market, symbol ORDER BY trade_date DESC) <= ?"
                    )
                    query_params.append(lookback)
                # Run validation on the selected population even when callers omit prices.
                # This query materializes only a scalar and never a wide Pandas frame.
                bad = " OR ".join(
                    f"{column} IS NULL OR NOT isfinite({column}) OR {column} < 0"
                    for column in ("open", "high", "low", "close", "amount")
                )
                bad += f" OR {volume} IS NULL OR NOT isfinite({volume}) OR {volume} < 0"
                bad += " OR high < low"
                if (
                    conn.execute(
                        f"SELECT 1 FROM ({selected_rows}) q WHERE {bad} LIMIT 1", query_params
                    ).fetchone()
                    is not None
                ):
                    raise DataPoolError("DAILY_INVALID", "Invalid or incomplete daily OHLCV/amount")
                row_count = conn.execute(
                    f"SELECT count(*) FROM ({selected_rows}) q", query_params
                ).fetchone()[0]
                if row_count > 500_000:
                    raise DataPoolError(
                        "DAILY_TOO_LARGE",
                        "DataFrame read exceeds 500000 rows; use bounded date windows or "
                        "iter_limit_events_with_amount for event data",
                    )
                internal = list(dict.fromkeys([*selected, "symbol", "date"]))
                expressions = {
                    "symbol": "symbol || '.' || market AS symbol",
                    "market": "market",
                    "code": "CAST(symbol AS VARCHAR) AS code",
                    "date": "trade_date AS date",
                    "volume": f"{volume} AS volume",
                    "turnover_rate": f"{rate} AS turnover_rate",
                }
                for key, (dtype, _) in OPTIONAL_FIELDS.items():
                    expressions[key] = (
                        f'CAST("{key}" AS {dtype}) AS "{key}"'
                        if key in names
                        else f'CAST(NULL AS {dtype}) AS "{key}"'
                    )
                projection = ", ".join(expressions.get(key, f'"{key}"') for key in internal)
                frame = conn.execute(
                    f"SELECT {projection} FROM ({selected_rows}) q ORDER BY symbol, date",
                    query_params,
                ).fetchdf()
            frame = _overlay_dated_fields(self.root, frame, selected)
        frame.attrs.update(
            contract_version=2,
            price_adjustment="raw",
            volume_unit="share",
            amount_unit="CNY",
            turnover_rate_unit="percent",
            available_at=None,
            point_in_time=False,
            dataset_version=None,
        )
        return frame[selected].copy()

    @public_read
    def status(self):
        with pool_lock(self.root):
            storage = DailyStorage(self.root)
            with duckdb.connect() as conn:
                try:
                    files = storage.bind(conn, "bars")
                except ValueError as exc:
                    raise DataPoolError("DAILY_INVALID", str(exc)) from exc
                if not files:
                    return {"backend": "aspool", "status": "empty", "root": str(self.root)}
                names = {row[0] for row in conn.execute("describe bars").fetchall()}
                asset_clause = (
                    "WHERE asset_type IS NULL OR asset_type <> 'etf'"
                    if "asset_type" in names
                    else ""
                )
                rows, symbols, start, end = conn.execute(
                    f"""SELECT count(*), count(distinct (market, symbol)),
                    min(trade_date), max(trade_date) FROM bars {asset_clause}"""
                ).fetchone()
                etf_rows = etf_symbols = etf_start = etf_end = 0
                if "asset_type" in names:
                    etf_rows, etf_symbols, etf_start, etf_end = conn.execute(
                        """SELECT count(*), count(distinct (market, symbol)),
                        min(trade_date), max(trade_date) FROM bars WHERE asset_type = 'etf'"""
                    ).fetchone()
        return dict(
            backend="aspool",
            status="available",
            root=str(self.root),
            row_count=rows,
            symbol_count=symbols,
            start=str(start),
            end=str(end),
            price_adjustment="raw",
            etf_row_count=etf_rows,
            etf_symbol_count=etf_symbols,
            etf_start=str(etf_start) if etf_start is not None else None,
            etf_end=str(etf_end) if etf_end is not None else None,
        )

    @public_read
    def read_etf_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        """Read ETF daily bars with security (share/amount/turnover) semantics."""
        from .dg03_etf import read_etf_daily

        return read_etf_daily(
            self.root, symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )

    @public_read
    def list_etfs(self, *, symbols=None):
        """List synchronized ETFs and their actual date coverage."""
        from .dg03_etf import list_etfs

        return list_etfs(self.root, symbols=symbols)

    def iter_limit_events_with_amount(
        self,
        *,
        start,
        end,
        symbols=None,
        fields=None,
        close_limit_up=None,
        min_consecutive_up=None,
        batch_days=7,
        max_rows=25_000,
        memory_limit="512MB",
        threads=2,
        temp_directory=None,
    ):
        """Read published events and same-day CNY amount in bounded date batches.

        Use as a context manager. A changed publication/stale state raises
        LIMIT_REVISION_CHANGED, so callers must discard their staging output.
        """

        return CandidateEventAmountBatches(
            self.root,
            start=start,
            end=end,
            symbols=symbols,
            fields=fields,
            close_limit_up=close_limit_up,
            min_consecutive_up=min_consecutive_up,
            batch_days=batch_days,
            max_rows=max_rows,
            memory_limit=memory_limit,
            threads=threads,
            temp_directory=temp_directory,
        )


class CandidateEventAmountBatches(EventAmountBatches):
    """Retain bounded batch/version checks; replace only the amount lookup."""

    def _versions(self):
        base = super()._versions()
        with pool_lock(self.root), read_only_catalog(self.root) as c:
            revision = c.execute("SELECT revision FROM dg03_revision").fetchone()[0]
        return base, revision

    def _attach_amount(self, frame, lo, hi):
        import numpy as np

        with pool_lock(self.root), read_only_catalog(self.root) as c:
            self._configure(c)
            keys = frame[["trade_date", "symbol"]].copy()
            keys["__code"] = keys.symbol.str.split(".").str[0]
            keys["__market"] = keys.symbol.str.split(".").str[1]
            c.register("event_keys", keys)
            amount = c.execute(
                """SELECT e.trade_date,e.symbol,d.amount FROM event_keys e
                JOIN dg03_daily d USING(__market,__code,trade_date)
                WHERE d.trade_date BETWEEN ? AND ?""",
                [lo, hi],
            ).fetchdf()
        finite = amount.amount.dropna().to_numpy(dtype=float)
        if not np.isfinite(finite).all() or (finite < 0).any():
            raise DataPoolError("DAILY_INVALID", "Invalid daily amount")
        return frame.merge(amount, on=["trade_date", "symbol"], how="left", validate="one_to_one")
