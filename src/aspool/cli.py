from __future__ import annotations

import json
from pathlib import Path

import click
import duckdb
import pyarrow.csv as pacsv

from .config import free_stockdb_root
from .free_stockdb import import_adjustments, import_daily, import_minutes, validate_period
from .fundamentals import refresh_fundamentals, update_from_quotes
from .store import default_root, initialize
from .tdx_online import update_daily_offline, update_online

PERIODS = ["daily", "minutes"]


def _root(value: Path | None) -> Path:
    return value.expanduser().resolve() if value else default_root()


def _free_stockdb_root() -> Path:
    return free_stockdb_root()


def _validate_source(source: Path) -> None:
    required = [source / "data", source / "pybao", source / "stockdb", source / "stockdb.conf"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise click.UsageError("free-stockdb 数据源不完整: " + ", ".join(missing))


@click.group()
def cli() -> None:
    """维护本地 A 股长期历史数据池。"""


@cli.command()
@click.option("--root", type=click.Path(path_type=Path))
def init(root: Path | None) -> None:
    """初始化本地数据池。"""
    target = _root(root)
    initialize(target)
    click.echo(target)


@cli.command("import")
@click.option(
    "--source", type=click.Choice(["free-stockdb"]), default="free-stockdb", show_default=True
)
@click.option("--period", type=click.Choice(PERIODS))
@click.option("--factor", is_flag=True, help="导入日线和分钟线共用的复权因子。")
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1), help="仅导入前 N 个标的，用于验证。")
def import_free_stockdb(
    source: str, root: Path | None, limit: int | None, period: str | None, factor: bool
) -> None:
    """一次性完整导入 free-stockdb 的原始数据或复权因子。"""
    if bool(period) == factor:
        raise click.UsageError("必须且只能指定 --period 或 --factor")
    history = _free_stockdb_root()
    _validate_source(history)
    target = _root(root)
    if factor:
        factor_rows = import_adjustments(target, history)
        click.echo(f"导入完成：{factor_rows} 条共享复权因子")
        return
    stats = (
        import_daily(target, history, incremental=False, limit=limit)
        if period == "daily"
        else import_minutes(target, history, limit=limit)
    )
    check = validate_period(target, period)
    if check["duplicates"] or check["invalid"]:
        raise click.ClickException(f"导入校验失败：{check}")
    click.echo(f"导入完成：{stats.symbols} 个标的，{stats.rows} 行原始 {period} K；校验：{check}")


@cli.command()
@click.option(
    "--source", type=click.Choice(["tdx", "free-stockdb"]), default="tdx", show_default=True
)
@click.option("--period", type=click.Choice(PERIODS), default="daily", show_default=True)
@click.option(
    "--tdx-mode",
    type=click.Choice(["online", "offline"]),
    default="online",
    show_default=True,
    help="source=tdx 时使用在线 MAC K 线或本地 vipdoc K 线。",
)
@click.option(
    "--async", "async_mode", is_flag=True, help="使用 tdxman 异步 MAC 客户端补齐在线尾部。"
)
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1))
def sync(
    source: str, period: str, tdx_mode: str, async_mode: bool, root: Path | None, limit: int | None
) -> None:
    """从指定源校准历史数据，并补齐至最新在线行情。"""
    target = _root(root)
    initialize(target)
    if source == "free-stockdb":
        history = _free_stockdb_root()
        _validate_source(history)
        stats = (
            import_daily(target, history, incremental=False, limit=limit)
            if period == "daily"
            else import_minutes(target, history, limit=limit)
        )
        check = validate_period(target, period)
        if check["duplicates"] or check["invalid"]:
            raise click.ClickException(f"历史导入校验失败：{check}")
        click.echo(f"历史校准：{stats.symbols} 个标的，{stats.rows} 行 {period} K；校验通过")
    if source == "tdx" and tdx_mode == "offline":
        if period != "daily":
            raise click.UsageError("tdx 离线同步当前仅支持 daily；分钟线请使用 free-stockdb 导入")
        symbols, rows = update_daily_offline(target, limit)
    else:
        symbols, rows = update_online(target, period, async_mode, limit)
    click.echo(f"在线补齐：{symbols} 个标的，新增或更新 {rows} 行 {period} K")


@cli.command()
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1))
def update(root: Path | None, limit: int | None) -> None:
    """收盘后用 quote 更新当前或最近交易日的完整日线记录。"""
    try:
        symbols, rows = update_from_quotes(_root(root), limit)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"报价更新完成：{symbols} 个标的，更新 {rows} 行最新日线并刷新基本面快照")


@cli.command("fundamentals")
@click.option("--async", "async_mode", is_flag=True, help="使用 tdxman 异步 MAC 客户端。")
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1), help="仅维护前 N 个标的，用于验证。")
def fundamentals(async_mode: bool, root: Path | None, limit: int | None) -> None:
    """手动维护全市场低频基本面快照。"""
    symbols, rows = refresh_fundamentals(_root(root), async_mode=async_mode, limit=limit)
    click.echo(f"基本面快照完成：{symbols} 个标的，写入 {rows} 条")


@cli.command()
@click.option("--root", type=click.Path(path_type=Path))
def status(root: Path | None) -> None:
    """显示日线、分钟线覆盖范围和最近同步状态。"""
    target = _root(root)
    initialize(target)
    conn = duckdb.connect(target / "catalog.duckdb", read_only=True)
    try:
        summaries = {
            period: conn.execute(
                f"select count(*), min(start_date), max(end_date), sum(row_count) from {table}"
            ).fetchone()
            for period, table in [("daily", "coverage"), ("minutes", "coverage_minutes")]
        }
        latest = conn.execute(
            "select command, status, started_at, finished_at, symbols, rows_written "
            "from sync_runs order by started_at desc limit 1"
        ).fetchone()
    finally:
        conn.close()
    click.echo(f"root: {target}")
    for period, summary in summaries.items():
        click.echo(
            f"{period}: symbols={summary[0]}, range={summary[1]} ~ {summary[2]}, "
            f"rows={summary[3] or 0}"
        )
    if latest:
        click.echo(f"last import: {latest}")


@cli.command()
@click.argument("symbol")
@click.option("--period", type=click.Choice(PERIODS), default="daily", show_default=True)
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]))
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]))
@click.option("--format", "fmt", type=click.Choice(["table", "csv", "json"]), default="table")
@click.option("--root", type=click.Path(path_type=Path))
def query(
    symbol: str, period: str, start: object, end: object, fmt: str, root: Path | None
) -> None:
    """读取本地指定标的的日线或一分钟线。"""
    target = _root(root)
    key = "trade_date" if period == "daily" else "timestamp"
    files = target / "lake" / "bars" / period / "market=*" / f"symbol={symbol}" / "bars.parquet"
    conn = duckdb.connect()
    try:
        sql = f"select * exclude (symbol) from read_parquet('{files}')"
        params: list[object] = []
        clauses: list[str] = []
        if start:
            clauses.append(f"{key} >= ?")
            params.append(start.date() if period == "daily" else start)
        if end:
            clauses.append(f"{key} <= ?")
            params.append(end.date() if period == "daily" else end)
        if clauses:
            sql += " where " + " and ".join(clauses)
        table = conn.execute(sql + f" order by {key}", params).fetch_arrow_table()
        if fmt == "json":
            rows = [
                {
                    key: value.isoformat() if hasattr(value, "isoformat") else value
                    for key, value in row.items()
                }
                for row in table.to_pylist()
            ]
            click.echo(json.dumps(rows, ensure_ascii=False))
        elif fmt == "csv":
            sink = __import__("pyarrow").BufferOutputStream()
            pacsv.write_csv(table, sink)
            click.echo(sink.getvalue().to_pybytes().decode(), nl=False)
        else:
            columns = table.column_names
            rows = table.to_pylist()
            widths = [
                max(len(column), *(len(str(row.get(column, ""))) for row in rows))
                for column in columns
            ]
            click.echo(" | ".join(column.ljust(width) for column, width in zip(columns, widths)))
            click.echo("-+-".join("-" * width for width in widths))
            for row in rows:
                click.echo(
                    " | ".join(
                        str(row.get(column, "")).ljust(width)
                        for column, width in zip(columns, widths)
                    )
                )
    finally:
        conn.close()
