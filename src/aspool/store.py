from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb


def default_root() -> Path:
    return Path.home() / ".aspool"


def _coverage_table(period: str) -> str:
    if period not in {"daily", "minutes"}:
        raise ValueError(f"unsupported period: {period}")
    return "coverage" if period == "daily" else "coverage_minutes"


def initialize(root: Path) -> None:
    for period in ("daily", "minutes"):
        (root / "lake" / "bars" / period).mkdir(parents=True, exist_ok=True)
    (root / "lake" / "fundamentals").mkdir(parents=True, exist_ok=True)
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
    root.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(root / "catalog.duckdb")
    try:
        yield conn
    finally:
        conn.close()


def bars_path(root: Path, period: str, market: str, symbol: str) -> Path:
    return (
        root / "lake" / "bars" / period / f"market={market}" / f"symbol={symbol}" / "bars.parquet"
    )


def daily_path(root: Path, market: str, symbol: str) -> Path:
    return bars_path(root, "daily", market, symbol)


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
    table = _coverage_table(period)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(root) as conn:
        conn.execute(
            f"""
            insert into {table} values (?, ?, ?, ?, ?, ?, ?)
            on conflict(symbol) do update set
                market = excluded.market, start_date = excluded.start_date,
                end_date = excluded.end_date, row_count = excluded.row_count,
                source = excluded.source, updated_at = excluded.updated_at
            """,
            [symbol, market, start, end, rows, source, now],
        )
