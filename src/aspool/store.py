from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

_active_catalog = ContextVar("aspool_catalog", default=None)


@contextmanager
def catalog_session(root):
    with catalog(root) as conn:
        token = _active_catalog.set((Path(root).resolve(), conn))
        try:
            yield conn
        finally:
            _active_catalog.reset(token)


def default_root() -> Path:
    return Path.home() / ".aspool"


def _coverage_table(period: str) -> str:
    if period not in {"daily", "minutes"}:
        raise ValueError(f"unsupported period: {period}")
    return "coverage" if period == "daily" else "coverage_minutes"


def initialize(root: Path) -> None:
    from .change_protocol import _mkdir_durable

    if not (root / "stocks.sqlite").exists():
        for period in ("daily", "minutes"):
            _mkdir_durable(root / "lake" / "bars" / period)
        _mkdir_durable(root / "lake" / "fundamentals")
    with catalog(root) as conn:
        for table in ("coverage", "coverage_minutes"):
            conn.execute(
                f"""
                create table if not exists {table} (
                    symbol varchar primary key,
                    market varchar not null,
                    start_date timestamp not null,
                    end_date timestamp not null,
                    row_count bigint not null,
                    source varchar not null,
                    updated_at timestamp not null
                )
                """
            )
        conn.execute(
            """
            create table if not exists sync_runs (
                run_id varchar primary key,
                command varchar not null,
                source varchar not null,
                started_at timestamp not null,
                finished_at timestamp,
                symbols bigint default 0,
                rows_written bigint default 0,
                status varchar not null,
                error varchar
            )
            """
        )


@contextmanager
def catalog(root: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """读写目录库；不存在时创建。仅用于写入路径。"""
    active = _active_catalog.get()
    if active is not None and active[0] == Path(root).resolve():
        yield active[1]
        return
    from .change_protocol import _mkdir_durable
    from .pool import pool_lock

    with pool_lock(root, write=True):
        _mkdir_durable(root)
        conn = duckdb.connect(root / "catalog.duckdb")
        try:
            yield conn
        finally:
            conn.close()


@contextmanager
def read_only_catalog(root: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """只读打开目录库；不创建目录、不创建文件。

    Raises:
        FileNotFoundError: 目录或 catalog.duckdb 不存在。
    """
    active = _active_catalog.get()
    if active is not None and active[0] == Path(root).resolve():
        yield active[1]
        return
    root = Path(root).expanduser().resolve()
    from .change_protocol import assert_readable

    assert_readable(root)
    path = root / "catalog.duckdb"
    if not path.is_file():
        raise FileNotFoundError(f"aspool catalog not found: {path}")
    from .pool import pool_lock

    with pool_lock(root):
        conn = duckdb.connect(str(path), read_only=True)
        try:
            yield conn
        finally:
            conn.close()


def existing_tables(root: Path) -> set[str]:
    """只读列出已有的表；目录/库不存在时返回空集。"""
    try:
        with read_only_catalog(root) as conn:
            rows = conn.execute(
                "select table_name from information_schema.tables"
            ).fetchall()
    except (FileNotFoundError, duckdb.Error):
        return set()
    return {row[0] for row in rows}


def bars_path(root: Path, period: str, market: str, symbol: str) -> Path:
    if period == "daily":
        paths = daily_paths(root, market, symbol)
        if paths:
            return paths[-1]
    return (
        root / "lake" / "bars" / period / f"market={market}" / f"symbol={symbol}" / "bars.parquet"
    )


def daily_path(root: Path, market: str, symbol: str) -> Path:
    return bars_path(root, "daily", market, symbol)


def daily_directory(root: Path, market: str, symbol: str) -> Path:
    from .daily_access import DailyStorage

    return DailyStorage(root).directory(market, symbol)


def daily_year_path(root: Path, market: str, symbol: str, year: int) -> Path:
    from .daily_access import DailyStorage

    return DailyStorage(root).target(market, symbol, year)


def daily_paths(root: Path, market: str, symbol: str) -> list[Path]:
    from .daily_access import DailyStorage

    return DailyStorage(root).paths(market, symbol)


def read_daily_table(root: Path, market: str, symbol: str):
    from .daily_access import DailyStorage

    return DailyStorage(root).read(market, symbol)


def last_marker(root: Path, symbol: str, period: str = "daily") -> date | datetime | None:
    table = _coverage_table(period)
    with catalog(root) as conn:
        row = conn.execute(f"select end_date from {table} where symbol = ?", [symbol]).fetchone()
    return row[0] if row else None


def last_date(root: Path, symbol: str) -> date | None:
    value = last_marker(root, symbol, "daily")
    return value.date() if isinstance(value, datetime) else value


def record_coverage(
    root: Path,
    symbol: str,
    market: str,
    start: date | datetime,
    end: date | datetime,
    rows: int,
    source: str,
    period: str = "daily",
) -> None:
    _coverage_table(period)
    record_coverages(root, [(symbol, market, start, end, rows, source, period)])


def record_coverages(root: Path, entries: list[tuple]) -> None:
    """Commit one maintenance run's coverage changes in a single transaction."""
    if not entries:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(root) as conn:
        conn.execute("BEGIN")
        try:
            for symbol, market, start, end, rows, source, period in entries:
                table = _coverage_table(period)
                if table == "coverage":
                    from .change_protocol import coverage_change

                    coverage_change(conn, table,
                                    ["symbol", "market", "start_date", "end_date", "row_count",
                                     "source", "updated_at"],
                                    [symbol, market, start, end, rows, source, now], ["symbol"],
                                    reason="coverage_extent_refresh")
                    continue
                conn.execute(
                    f"""
                    insert into {table} values (?, ?, ?, ?, ?, ?, ?)
                    on conflict(symbol) do update set
                        market = excluded.market, start_date = excluded.start_date,
                        end_date = excluded.end_date, row_count = excluded.row_count,
                        source = excluded.source, updated_at = excluded.updated_at
            WHERE {table}.market IS DISTINCT FROM excluded.market
                OR {table}.start_date IS DISTINCT FROM excluded.start_date
                OR {table}.end_date IS DISTINCT FROM excluded.end_date
                OR {table}.row_count IS DISTINCT FROM excluded.row_count
                OR {table}.source IS DISTINCT FROM excluded.source
                    """,
                    [symbol, market, start, end, rows, source, now],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
