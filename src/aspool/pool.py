"""Read-only daily market data contract: shares, CNY, percentage points."""

from __future__ import annotations

import fcntl
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

import duckdb
import pandas as pd

from .api_contract import DAILY_FIELDS, OPTIONAL_FIELDS, DataPoolError, public_read
from .daily_access import DailyStorage

_pool_locks = ContextVar("aspool_pool_locks", default=())


def _holds_write_lock(root):
    return any(path == Path(root).expanduser().resolve() and write
               for path, write in _pool_locks.get())

# 支持的符号格式: 000001.SH (规范) 或 SH.000001 (兼容)
_SYMBOL_PATTERN = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$|^(SH|SZ|BJ)\.(\d{6})$")


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


def _normalize_symbols(values):
    """规范化符号列表为存储格式。"""
    if values is None:
        return None
    result = [values] if isinstance(values, str) else list(values)
    normalized = []
    for v in result:
        if not isinstance(v, str):
            raise DataPoolError("INVALID_ARGUMENT", f"Symbol must be a string: {v}")
        normalized.append(_normalize_symbol(v))
    return normalized


@contextmanager
def pool_lock(root: Path, *, write: bool = False):
    """Coordinate batch writers and readers without opening the DuckDB catalog."""
    root = Path(root).expanduser().resolve()
    held = next((mode for path, mode in _pool_locks.get() if path == root), None)
    if held is not None:
        if write and not held:
            raise RuntimeError("Pool lock upgrade requires releasing the read lock first")
        if not write:
            from .change_protocol import assert_readable

            assert_readable(root)
        yield
        return
    if write:
        from .change_protocol import _mkdir_durable

        _mkdir_durable(root)
    # Directory locks allow a reader to remain strictly read-only.
    import os

    fd = os.open(root, os.O_RDONLY)
    try:
        from time import perf_counter

        from .change_protocol import assert_readable, note_lock

        tick = perf_counter()
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        note_lock(perf_counter() - tick)
        if not write:
            assert_readable(root)
        token = _pool_locks.set((*_pool_locks.get(), (root, write)))
        try:
            yield
        finally:
            _pool_locks.reset(token)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def writer(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        from .store import catalog_session

        with pool_lock(root, write=True), catalog_session(root):
            from .change_protocol import recover

            recover(root)
            return function(root, *args, **kwargs)

    from .change_protocol import maintenance

    return maintenance(wrapped)


def _overlay_dated_fields(root: Path, frame: pd.DataFrame, selected=None) -> pd.DataFrame:
    """Expose dated facts and published reference corrections without rewriting bars."""
    from .store import existing_tables, read_only_catalog

    selected = set(frame.columns if selected is None else selected)
    dated = {"pre_close", "pre_close_source", "is_st", "is_st_source",
             "trading_status", "trading_status_source"}
    if not selected.intersection(dated):
        return frame
    tables = existing_tables(root)
    has_facts = "security_daily_facts" in tables
    has_references = {"daily_limit_references", "daily_limit_publication",
                      "daily_limit_staleness"} <= tables
    if frame.empty or not (has_facts or has_references):
        return frame
    joins, replacements = [], {}
    want_reference = bool(selected.intersection({"pre_close", "pre_close_source"}))
    want_facts = bool(selected.intersection(dated))
    reference, reference_source = "b.pre_close", "b.pre_close_source"
    if has_references and want_reference:
        joins.append("""left join (
            select r.symbol, r.trade_date, r.reference_pre_close, r.basis
            from daily_limit_references r
            join daily_limit_publication p using (trade_date, batch_id)
            where exists (select 1 from _daily_contract_rows k
                          where k.symbol=r.symbol and k.date=r.trade_date)
        ) r on r.symbol=b.symbol and r.trade_date=b.date
        left join daily_limit_staleness st on st.trade_date=r.trade_date""")
        reference = ("case when r.symbol is not null then "
                     "case when st.trade_date is null then r.reference_pre_close end "
                     "else b.pre_close end")
        reference_source = ("case when r.symbol is not null then 'limit_derived:' || r.basis "
                            "else b.pre_close_source end")
    if has_facts and want_facts:
        fact_fields = {"symbol", "trade_date"}
        if any(key.endswith("_source") for key in selected.intersection(dated)):
            fact_fields.add("source")
        for key in ("pre_close", "is_st", "trading_status"):
            if {key, key + "_source"}.intersection(selected):
                fact_fields.add(key)
        joins.append(
            "left join (select " + ", ".join(sorted(fact_fields))
            + " from security_daily_facts f where exists "
            "(select 1 from _daily_contract_rows k "
            "where k.symbol=f.symbol and k.date=f.trade_date)) f "
            "on f.symbol=b.symbol and f.trade_date=b.date"
        )
        reference = f"coalesce(f.pre_close, {reference})"
        reference_source = ("case when f.pre_close is not null then f.source else "
                            f"{reference_source} end")
        for key in ("is_st", "trading_status"):
            if key in selected:
                replacements[key] = f"coalesce(f.{key}, b.{key})"
            if key + "_source" in selected:
                replacements[key + "_source"] = (
                    f"case when f.{key} is not null then f.source else b.{key}_source end"
                )
    if want_reference:
        if "pre_close" in selected:
            replacements["pre_close"] = reference
        if "pre_close_source" in selected:
            replacements["pre_close_source"] = reference_source
    if not joins or not replacements:
        return frame
    selections = ", ".join(f"{expression} as {key}" for key, expression in replacements.items())
    with (
        tempfile.TemporaryDirectory(prefix="aspool-overlay-") as temp,
        read_only_catalog(root) as conn,
    ):
        conn.execute("SET memory_limit = '512MB'")
        conn.execute("SET threads = 2")
        conn.execute("SET temp_directory = ?", [temp])
        conn.register("_daily_contract_rows", frame)
        try:
            return conn.execute(
                f"select b.* replace ({selections}) from _daily_contract_rows b "
                + " ".join(joins) + " order by b.symbol, b.date"
            ).fetchdf()
        finally:
            conn.unregister("_daily_contract_rows")


class DataPool:
    """Read raw daily bars without network access or catalog mutations.

    volume is shares; amount is CNY; turnover_rate is percentage points
    (0.43 means 0.43%). Unknown source fields stay null.
    """

    def __init__(self, root: str | Path = "~/.aspool"):
        self.root = Path(root).expanduser().resolve()

    def stock_snapshot(self):
        """Read SQLite daily/features/events in one caller-owned snapshot."""
        if not (self.root / "stocks.sqlite").is_file():
            raise DataPoolError("CAPABILITY_UNAVAILABLE", "SQLite stock snapshot is unavailable")
        from .sqlite_read_api import StockSnapshot
        return StockSnapshot(self.root)

    @public_read
    def describe_market_fields(self, *, fields=None, frequency="D", scope="all_stocks"):
        """Describe available fields without reading or maintaining historical data."""
        from .sqlite_market_metadata import describe_fields
        with self.stock_snapshot() as reader:
            return describe_fields(reader.conn, fields=fields, frequency=frequency, scope=scope)

    @public_read
    def read_market_summary(
        self, *, start, end, frequency="D", scope="all_stocks", fields=None, closed_only=True
    ):
        with self.stock_snapshot() as reader:
            return reader.read_market_summary(start=start, end=end, frequency=frequency,
                                             scope=scope, fields=fields, closed_only=closed_only)

    def read_market_daily(self, *, start, end, scope="all_stocks", fields=None):
        return self.read_market_summary(start=start, end=end, scope=scope, fields=fields)

    def read_security_daily(self, *, symbols=None, start=None, end=None):
        """Read dated status/ST/reference-price facts, including suspended sessions."""
        if (self.root / "stocks.sqlite").is_file():
            with self.stock_snapshot() as reader:
                return reader.read_daily_features(symbols=symbols, start=start, end=end)
        from .security_facts import DAILY_TABLE, read_facts

        return read_facts(self.root, DAILY_TABLE, symbols=symbols, start=start, end=end)

    def read_security_info(self, *, symbols=None):
        """Read sourced listing and code-exit dates; names are current metadata."""
        catalog_path = self.root / "catalog.duckdb"
        if (self.root / "stocks.sqlite").is_file() and catalog_path.is_file():
            from .limit_api import _normalize_requested_symbols
            from .store import read_only_catalog

            clauses, params = [], []
            if symbols is not None:
                clauses.append("symbol IN (SELECT unnest(?))")
                params.append(_normalize_requested_symbols(symbols))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            with read_only_catalog(self.root) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "securities" in tables:
                    frame = conn.execute(
                        "SELECT symbol,code,market,asset_type,name,active,listing_date,"
                        "delisting_date,updated_at FROM securities"
                        + where
                        + " ORDER BY symbol",
                        params,
                    ).fetchdf()
                    frame.attrs.update(source="catalog.securities", point_in_time=False)
                    return frame
        from .security_facts import BASIC_TABLE, read_facts

        return read_facts(self.root, BASIC_TABLE, symbols=symbols)

    def read_trading_calendar(self, *, start=None, end=None):
        """Read the stored BaoStock Shanghai/Shenzhen calendar."""
        from .security_facts import CALENDAR_TABLE, read_facts

        return read_facts(self.root, CALENDAR_TABLE, start=start, end=end)

    @public_read
    def read_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        if (self.root / "stocks.sqlite").is_file():
            with self.stock_snapshot() as reader:
                return reader.read_daily(
                    symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
                )
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
                    files = storage.bind(conn, "bars", symbols=normalized if symbols is not None
                                         else None, start=start, end=end)
                except ValueError as exc:
                    raise DataPoolError("DAILY_INVALID", str(exc)) from exc
                if (not files and not storage.files(normalized if symbols is not None else None)
                        and not storage.has_data()):
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
                            "DAILY_TOO_LARGE", "DataFrame read exceeds 500000 rows; "
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
                if conn.execute(
                    f"SELECT 1 FROM ({selected_rows}) q WHERE {bad} LIMIT 1", query_params
                ).fetchone() is not None:
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
                    "market": "market", "code": "CAST(symbol AS VARCHAR) AS code",
                    "date": "trade_date AS date",
                    "volume": f"{volume} AS volume", "turnover_rate": f"{rate} AS turnover_rate",
                }
                for key, (dtype, _) in OPTIONAL_FIELDS.items():
                    expressions[key] = (
                        f'CAST("{key}" AS {dtype}) AS "{key}"' if key in names
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
        if (self.root / "stocks.sqlite").is_file():
            derived = self.describe_limits()
            return dict(backend="aspool", storage_backend="sqlite", root=str(self.root),
                        status="ready" if derived["ready"] else "empty", derived=derived)
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

    def _read_fundamentals(self, *, symbols=None, as_of=None):
        """Return the latest low-frequency fundamentals and ratios at a bar close.

        Shares are shares.  PE and PB use the selected daily close with the
        latest available trailing earnings and book value per share, so their
        numerator remains current without a daily quote request.
        """
        path = self.root / "lake/fundamentals/snapshots.parquet"
        from .fundamental_catalog import available, read

        with pool_lock(self.root):
            if available(self.root):
                frame = read(self.root)
            else:
                if not path.exists():
                    raise FileNotFoundError("No fundamentals snapshots in aspool")
                with duckdb.connect() as conn:
                    frame = conn.execute(
                        "select * from read_parquet(?) order by market, code", [str(path)]
                    ).fetchdf()
        frame = frame.rename(columns={"total_shares": "total_share", "float_shares": "float_share"})
        # 规范格式 code.market：与 read_daily 输出一致，避免 join 丢失。
        frame["symbol_id"] = frame.code + "." + frame.market
        if symbols is not None:
            requested = [symbols] if isinstance(symbols, str) else list(symbols)
            # _normalize_symbol 返回存储格式 market.code，需还原为规范格式 code.market，
            # 才能与 symbol_id 对齐。
            want = set()
            for value in requested:
                market_part, code_part = _normalize_symbol(value).split(".", 1)
                want.add(f"{code_part}.{market_part}")
            frame = frame[frame.symbol_id.isin(want)]
        prices = self.read_daily(symbols=sorted(frame.symbol_id.unique()), end=as_of, lookback=1,
                                 fields=["symbol", "date", "close"])
        closes = prices.groupby("symbol", as_index=False).tail(1).set_index("symbol").close
        frame["close"] = frame.symbol_id.map(closes)
        frame["total_mv"] = frame.close * frame.total_share
        frame["float_mv"] = frame.close * frame.float_share
        frame["pe_ttm"] = frame.close / frame.ttm_eps
        frame.loc[frame.ttm_eps <= 0, "pe_ttm"] = None
        frame["pb"] = frame.close / frame.net_assets
        frame.loc[frame.net_assets <= 0, "pb"] = None
        return frame.drop(columns="symbol_id")

    @public_read
    def read_index_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        """Read index daily bars independently of the stock universe."""
        from .index_api import read_index_daily

        return read_index_daily(
            self.root, symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )

    @public_read
    def list_indices(self, *, symbols=None):
        """List stored index identities and actual date coverage."""
        from .index_api import list_indices

        return list_indices(self.root, symbols=symbols)

    @public_read
    def read_etf_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        """Read ETF daily bars with security (share/amount/turnover) semantics."""
        from .etf_api import read_etf_daily

        return read_etf_daily(
            self.root, symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )

    @public_read
    def list_etfs(self, *, symbols=None):
        """List synchronized ETFs and their actual date coverage."""
        from .etf_api import list_etfs

        return list_etfs(self.root, symbols=symbols)

    # ------------------------------------------------------------------ #
    # 日终涨跌停派生（已发布批次）
    # ------------------------------------------------------------------ #

    @public_read
    def read_limit_summary(self, *, start=None, end=None):
        """已发布的日级涨跌停汇总。"""
        if (self.root / "stocks.sqlite").is_file():
            from .sqlite_read_api import read_limit_summary
            return read_limit_summary(self.root, start=start, end=end)
        from .limit_api import read_limit_summary

        return read_limit_summary(self.root, start=start, end=end)

    @public_read
    def read_limit_events(self, *, trade_date=None, start=None, end=None, symbols=None):
        """已发布的逐股涨跌停事件；输出规范 symbol。"""
        if (self.root / "stocks.sqlite").is_file():
            with self.stock_snapshot() as reader:
                return reader.read_limit_events(
                    trade_date=trade_date, start=start, end=end, symbols=symbols
                )
        from .limit_api import read_limit_events

        return read_limit_events(
            self.root, trade_date=trade_date, start=start, end=end, symbols=symbols
        )

    def iter_limit_events_with_amount(
        self, *, start, end, symbols=None, fields=None, close_limit_up=None,
        min_consecutive_up=None, batch_days=7, max_rows=25_000,
        memory_limit="512MB", threads=2, temp_directory=None,
    ):
        """Read events and same-day CNY amount in bounded date windows.

        Use as a context manager. SQLite pins one read snapshot and splits
        oversized dates into pages. Legacy pools check publication revisions.
        """
        if (self.root / "stocks.sqlite").is_file():
            from .sqlite_read_api import EventAmountBatches
        else:
            from .limit_amount import EventAmountBatches

        return EventAmountBatches(
            self.root, start=start, end=end, symbols=symbols, fields=fields,
            close_limit_up=close_limit_up, min_consecutive_up=min_consecutive_up,
            batch_days=batch_days, max_rows=max_rows, memory_limit=memory_limit,
            threads=threads, temp_directory=temp_directory,
        )

    @public_read
    def read_limit_exceptions(self, *, trade_date=None, start=None, end=None, symbols=None):
        """已发布的异常/未知/无约束记录。"""
        if (self.root / "stocks.sqlite").is_file():
            raise DataPoolError(
                "API_REMOVED", "read_limit_exceptions was removed in v3; "
                "use market summaries or daily features"
            )
        from .limit_api import read_limit_exceptions

        return read_limit_exceptions(
            self.root, trade_date=trade_date, start=start, end=end, symbols=symbols
        )

    @public_read
    def read_limit_references(self, *, start=None, end=None, symbols=None):
        """Published reference-price evidence, including unresolved conflicts."""
        if (self.root / "stocks.sqlite").is_file():
            raise DataPoolError(
                "API_REMOVED", "read_limit_references was removed in v3; "
                "use market summaries or daily features"
            )
        from .limit_api import read_limit_references

        return read_limit_references(self.root, start=start, end=end, symbols=symbols)

    @public_read
    def read_limit_coverage(self, *, start=None, end=None):
        """已发布批次的范围与完成状态；含 stale 标记。"""
        if (self.root / "stocks.sqlite").is_file():
            raise DataPoolError(
                "API_REMOVED", "read_limit_coverage was removed in v3; "
                "use market summaries or daily features"
            )
        from .limit_api import read_limit_coverage

        return read_limit_coverage(self.root, start=start, end=end)

    @public_read
    def read_limit_scope(self, *, trade_date=None, start=None, end=None):
        """已发布批次实际处理的证券名单。

        在名单内、无事件且无异常 => 确定无涨跌停事件。
        """
        if (self.root / "stocks.sqlite").is_file():
            raise DataPoolError(
                "API_REMOVED", "read_limit_scope was removed in v3; "
                "use market summaries or daily features"
            )
        from .limit_api import read_limit_scope

        return read_limit_scope(self.root, trade_date=trade_date, start=start, end=end)

    @public_read
    def read_limit_staleness(self, *, start=None, end=None):
        """被标记为陈旧/失败的日期，供消费者拒绝或降级使用。"""
        if (self.root / "stocks.sqlite").is_file():
            raise DataPoolError(
                "API_REMOVED", "read_limit_staleness was removed in v3; "
                "use market summaries or daily features"
            )
        from .limit_api import read_limit_staleness

        return read_limit_staleness(self.root, start=start, end=end)

    @public_read
    def describe_limits(self):
        """声明已实现的涨跌停派生能力与字段单位；只读。"""
        if (self.root / "stocks.sqlite").is_file():
            from .sqlite_read_api import describe
            return describe(self.root)
        from .limit_api import describe_limits

        return describe_limits(self.root)

    def compute_limit_events(
        self, trade_dates, *, scope_id="stock", asset_type="stock", propagate=True, cache_years=1
    ):
        """独立入口：重算并发布指定交易日的派生事件（可重试）。

        会自修改日向前传播至连板/事件不再变化；失败则标记 stale。
        """
        if (self.root / "stocks.sqlite").is_file():
            raise DataPoolError("API_REMOVED", "Use atomic daily changes or explicit maintenance")
        from .limit_events import compute_limit_events

        return compute_limit_events(
            self.root,
            list(trade_dates),
            scope_id=scope_id,
            asset_type=asset_type,
            propagate=propagate,
            cache_years=cache_years,
        )

    def describe(self):
        """Return the implemented public contract and explicit capability limits."""
        from .ex_domain import load_ex_categories
        from .index_api import INDEX_FIELDS

        result = {
            "contract_version": 2,
            "primary_key": ["symbol", "date"],
            "price_adjustment": "raw",
            "fields": {
                key: {
                    "type": dtype,
                    "unit": unit,
                    "nullable": key in OPTIONAL_FIELDS or key == "turnover_rate",
                }
                for key, (dtype, unit) in DAILY_FIELDS.items()
            },
            "index_fields": {
                key: {"type": dtype, "unit": unit, "nullable": False}
                for key, (dtype, unit) in INDEX_FIELDS.items()
            },
            "ex_categories": load_ex_categories(),
            "etf_fields": {
                key: {
                    "type": dtype,
                    "unit": unit,
                    "nullable": key in OPTIONAL_FIELDS or key == "turnover_rate",
                }
                for key, (dtype, unit) in DAILY_FIELDS.items()
            },
            "capabilities": {
                "index_daily": True,
                "index_listing": True,
                "etf_daily": True,
                "etf_listing": True,
                "daily": True,
                "field_projection": True,
                "immutable_versions": False,
                "point_in_time": False,
                "calendar": False,
                "historical_universe": False,
                "trading_status": False,
                "security_daily": True,
                "security_info": True,
                "stored_trading_calendar": True,
                "corporate_actions": False,
                "adjustments": False,
                "minute_bars": False,
            },
        }

        if (self.root / "stocks.sqlite").is_file():
            result.update(contract_version=3, backend="sqlite", market_frequencies=["D"])
            result["capabilities"].update(market_summary=True, read_snapshot=True)
        return result

    def read_research_daily(
        self, *, symbols=None, start=None, end=None, lookback=None, fields=None
    ):
        """Return the stable daily research contract for Fundwise.

        The internal fundamentals snapshot is deliberately not exposed.  It is
        only used by aspool writers to maintain the primary daily data store.
        """
        return self.read_daily(
            symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )
