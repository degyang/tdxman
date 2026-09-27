"""Concrete Parquet daily access; callers own pool locks and publication policy.

Physical layout, footer extents, field projection and key/range selection live here.
No backend registry or public revision contract is implied by this internal boundary.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


class DailyStorage:
    def __init__(self, root):
        self.root = Path(root)
        self.base = self.root / "lake/bars/daily"
        self._extents = {}

    def directory(self, market, code):
        return self.base / f"market={market}" / f"symbol={code}"

    def target(self, market, code, year=None):
        directory = self.directory(market, code)
        return (directory / f"year={year}" if year is not None else directory) / "bars.parquet"

    def identities(self):
        for market_dir in sorted(self.base.glob("market=*")):
            for code_dir in sorted(market_dir.glob("symbol=*")):
                market, code = market_dir.name[7:], code_dir.name[7:]
                if self.paths(market, code):
                    yield market, code

    def yearly(self, market, code):
        paths = self.paths(market, code)
        return bool(paths and paths[0].parent.name.startswith("year="))

    def outputs(self, market, code, table, touched, *, target=None):
        if target is None and self.yearly(market, code):
            years = pc.year(table["trade_date"].cast(pa.date32()))
            return [
                (self.target(market, code, year), table.filter(pc.equal(years, year)))
                for year in sorted({day.year for day in touched})
            ]
        return [(target or self.target(market, code), table)]

    def paths(self, market, code, start=None, end=None):
        directory = self.directory(market, code)
        legacy = directory / "bars.parquet"
        yearly = sorted(directory.glob("year=*/bars.parquet"))
        if legacy.exists() and yearly:
            raise ValueError(f"{code}: mixed legacy and yearly daily storage")
        # Validate partition extents before pruning: a wrong year must never hide rows.
        for path in yearly:
            year = int(path.parent.name[5:])
            first, last, _ = self.file_extent(path)
            if first is not None and (first.year != year or last.year != year):
                raise ValueError(f"{code}: daily dates outside partition year {year}")
        return (
            [
                p
                for p in yearly
                if (start is None or int(p.parent.name[5:]) >= start.year)
                and (end is None or int(p.parent.name[5:]) <= end.year)
            ]
            if yearly
            else ([legacy] if legacy.exists() else [])
        )

    def files(self, symbols=None, start=None, end=None):
        identities = (
            self.identities()
            if symbols is None
            else (symbol.split(".", 1) for symbol in dict.fromkeys(symbols))
        )
        return [p for market, code in identities for p in self.paths(market, code, start, end)]

    def bind(self, conn, name, *, symbols=None, start=None, end=None):
        """Bind a range candidate relation; consumers apply exact dates/keys in SQL."""
        files = self.files(symbols, start, end)
        if not files:
            # No files for an absent key/range must still produce a typed empty result.
            conn.execute(f"""CREATE TEMP VIEW {name} AS SELECT
                CAST(NULL AS VARCHAR) AS market, CAST(NULL AS VARCHAR) AS symbol,
                CAST(NULL AS DATE) AS trade_date,
                CAST(NULL AS DOUBLE) AS open, CAST(NULL AS DOUBLE) AS high,
                CAST(NULL AS DOUBLE) AS low, CAST(NULL AS DOUBLE) AS close,
                CAST(NULL AS DOUBLE) AS volume, CAST(NULL AS DOUBLE) AS amount,
                CAST(NULL AS VARCHAR) AS asset_type WHERE false""")
        else:
            relation = conn.read_parquet(
                [str(p) for p in files], union_by_name=True, hive_partitioning=True
            )
            conn.execute(f"CREATE TEMP VIEW {name} AS " + relation.sql_query())
        return files

    def has_data(self):
        # Only used for empty candidate selections. Stop at the first file; no full
        # pool inventory is constructed just to distinguish empty pool/absent key.
        return next(self.base.glob("market=*/symbol=*/**/bars.parquet"), None) is not None

    def has_field(self, field):
        """Legacy schema-presence fallback; metadata only, stop at the first match.

        Old pools have no schema inventory. Proving global absence necessarily
        inspects their footers; ordinary matching-key reads never use this probe.
        """
        from .change_protocol import note_range

        for path in self.base.glob("market=*/symbol=*/**/bars.parquet"):
            parquet = pq.ParquetFile(path)
            note_range("schema_probe", path=path)
            if field in parquet.schema_arrow.names:
                return True
        return False

    def file_extent(self, path):
        from .change_protocol import note_range

        stat = path.stat()
        fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        cached = self._extents.get(path)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        parquet = pq.ParquetFile(path)
        note_range("footer_read", path=path)
        if not parquet.metadata.num_rows:
            return None, None, 0
        index = parquet.schema_arrow.get_field_index("trade_date")
        if index < 0:
            raise ValueError(f"{path}: missing trade_date")
        bounds = []
        for group in range(parquet.num_row_groups):
            stats = parquet.metadata.row_group(group).column(index).statistics
            if stats is None or not stats.has_min_max or stats.null_count:
                # Legacy files without usable stats require a date-only fallback.
                days = parquet.read(columns=["trade_date"])["trade_date"].to_pylist()
                note_range(
                    "footer_date_fallback",
                    rows=len(days),
                    path=path,
                    bytes_proxy=path.stat().st_size,
                )
                if any(day is None for day in days):
                    raise ValueError(f"{path}: missing stored date")
                extent = min(days), max(days), len(days)
                self._extents[path] = fingerprint, extent
                return extent
            bounds.append((stats.min, stats.max))
        extent = (min(x[0] for x in bounds), max(x[1] for x in bounds), parquet.metadata.num_rows)
        self._extents[path] = fingerprint, extent
        return extent

    def extent(self, market, code):
        extents = [self.file_extent(p) for p in self.paths(market, code)]
        extents = [x for x in extents if x[2]]
        if not extents:
            return None, None, 0
        return min(x[0] for x in extents), max(x[1] for x in extents), sum(x[2] for x in extents)

    def read_file(self, path, fields=None, *, category="read", validate=True):
        from .change_protocol import note_range

        parquet = pq.ParquetFile(path)
        columns = None if fields is None else [f for f in fields if f in parquet.schema_arrow.names]
        table = parquet.read(columns=columns)
        days = table["trade_date"].to_pylist() if "trade_date" in table.column_names else []
        if validate and days and (any(day is None for day in days) or days != sorted(set(days))):
            raise ValueError(f"{path}: duplicate or unordered stored dates")
        note_range(
            category,
            rows=len(table),
            path=path,
            bytes_proxy=path.stat().st_size,
            start=days[0] if days else None,
            end=days[-1] if days else None,
        )
        return table

    def tables(self, market, code, start=None, end=None, fields=None, *, category="read"):
        previous = None
        for path in self.paths(market, code, start, end):
            table = self.read_file(path, fields, category=category)
            if "trade_date" in table.column_names and len(table):
                days = table["trade_date"].to_pylist()
                if previous is not None and previous >= days[0]:
                    raise ValueError(f"{code}: duplicate or unordered stored dates")
                previous = days[-1]
            yield path, table

    def batches(self, market, code, fields, start=None, end=None, *, category="read"):
        from .change_protocol import note_range

        previous = None
        for path in self.paths(market, code, start, end):
            parquet = pq.ParquetFile(path)
            # Keep dates internally for validation and exact filtering, even for
            # asset-only or other narrow projections.
            columns = [
                f for f in dict.fromkeys(["trade_date", *fields]) if f in parquet.schema_arrow.names
            ]
            note_range(category + "_files", path=path, bytes_proxy=path.stat().st_size)
            for batch in parquet.iter_batches(columns=columns):
                days = batch.column(batch.schema.get_field_index("trade_date")).to_pylist()
                if days and (
                    any(day is None for day in days)
                    or days != sorted(set(days))
                    or (previous is not None and previous >= days[0])
                ):
                    raise ValueError(f"{code}: duplicate or unordered stored dates")
                if days:
                    previous = days[-1]
                note_range(
                    category,
                    rows=len(batch),
                    start=days[0] if days else None,
                    end=days[-1] if days else None,
                )
                if start is not None:
                    batch = batch.filter(pc.greater_equal(batch.column(0), pa.scalar(start)))
                if end is not None:
                    batch = batch.filter(pc.less_equal(batch.column(0), pa.scalar(end)))
                yield batch.select([f for f in fields if f in batch.schema.names])

    def read(self, market, code, start=None, end=None, fields=None):
        tables = []
        columns = None if fields is None else list(dict.fromkeys(["trade_date", *fields]))
        for _, table in self.tables(market, code, start, end, columns):
            if start is not None:
                table = table.filter(pc.greater_equal(table["trade_date"], pa.scalar(start)))
            if end is not None:
                table = table.filter(pc.less_equal(table["trade_date"], pa.scalar(end)))
            if fields is not None:
                table = table.select([f for f in fields if f in table.column_names])
            tables.append(table)
        return pa.concat_tables(tables, promote_options="permissive") if tables else None

    def merge_paths(self, market, code, first, preceding=5, *, end=None):
        """Read the complete dependency suffix plus enough previous bars to warm it.

        Keep complete selected files for atomic rewrites. Never limit the suffix
        by a guessed calendar-day distance or truncate a sparse prior year.
        """
        paths = self.paths(market, code)
        offset = next(
            (
                i
                for i, p in enumerate(paths)
                if self.file_extent(p)[1] is not None and self.file_extent(p)[1] >= first
            ),
            len(paths),
        )
        count = 0
        if offset < len(paths):
            lower, _, _ = self.file_extent(paths[offset])
            if lower is not None and lower < first:
                from bisect import bisect_left

                days = self.read_file(paths[offset], ["trade_date"], category="dependency_dates")[
                    "trade_date"
                ].to_pylist()
                count = bisect_left(days, first)
        while offset and count < preceding:
            offset -= 1
            count += self.file_extent(paths[offset])[2]
        return [
            p
            for p in paths[offset:]
            if end is None or self.file_extent(p)[0] is None or self.file_extent(p)[0] <= end
        ]

    def tail_dates(self, market, code, count=5):
        days = []
        for path in reversed(self.paths(market, code)):
            days = (
                self.read_file(path, ["trade_date"], category="planning_read")[
                    "trade_date"
                ].to_pylist()
                + days
            )
            if len(days) >= count:
                break
        return days[-count:]

    def coverage(self, code):
        from .store import read_only_catalog

        with read_only_catalog(self.root) as conn:
            return conn.execute(
                "SELECT CAST(start_date AS DATE), CAST(end_date AS DATE), "
                "row_count FROM coverage WHERE symbol=?",
                [code],
            ).fetchone()

    def revisions(self, market, code):
        """Internal DG-01 logical revisions, separate from physical file identities."""
        from .store import read_only_catalog

        keys = [str(p.relative_to(self.root)) for p in self.paths(market, code)]
        with read_only_catalog(self.root) as conn:
            tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            revisions = (
                dict(
                    conn.execute(
                        "SELECT object_key, revision FROM business_revisions "
                        "WHERE object_key IN (SELECT unnest(?))",
                        [keys],
                    ).fetchall()
                )
                if "business_revisions" in tables
                else {}
            )
        return {key: revisions.get(key, 0) for key in keys}

    def event_amount(self, conn, keys, start, end):
        """Read only the event keys and amount, retaining duplicate/value validation."""
        import numpy as np
        import pandas as pd

        from .api_contract import DataPoolError

        symbols = [".".join(reversed(s.split(".", 1))) for s in keys.symbol.unique()]
        try:
            files = self.bind(conn, "_event_bars", symbols=symbols, start=start, end=end)
        except ValueError as exc:
            raise DataPoolError("DAILY_INVALID", str(exc)) from exc
        if not files:
            return pd.DataFrame(columns=["trade_date", "symbol", "amount"])
        names = {r[0] for r in conn.execute("DESCRIBE _event_bars").fetchall()}
        amount_column = "d.amount" if "amount" in names else "cast(null as double)"
        conn.register("_event_keys", keys[["trade_date", "symbol"]])
        try:
            base = """from _event_bars d join _event_keys e
                      on d.symbol || '.' || d.market=e.symbol and d.trade_date=e.trade_date
                      where d.trade_date between ? and ?"""
            params = [start, end]
            duplicate = conn.execute(
                "select e.trade_date, e.symbol " + base + " group by e.trade_date, e.symbol "
                "having count(*) > 1 limit 1",
                params,
            ).fetchone()
            if duplicate:
                raise DataPoolError("DAILY_INVALID", f"Duplicate daily key: {duplicate}")
            amount = conn.execute(
                f"select e.trade_date, e.symbol, {amount_column} AS amount " + base, params
            ).fetchdf()
        finally:
            conn.unregister("_event_keys")
            conn.execute("DROP VIEW _event_bars")
        finite = amount.amount.dropna().to_numpy(dtype=float)
        if not np.isfinite(finite).all() or (finite < 0).any():
            raise DataPoolError("DAILY_INVALID", "Invalid daily amount")
        return amount

    def publish(self, target, table, deltas, **kwargs):
        from .change_protocol import publish_file

        return publish_file(self.root, target, table, deltas, **kwargs)
