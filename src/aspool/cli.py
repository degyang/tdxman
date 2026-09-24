from __future__ import annotations

import json
import re
from pathlib import Path

import click
import duckdb
import pyarrow.csv as pacsv

from .config import free_stockdb_root
from .free_stockdb import import_adjustments, import_daily, import_minutes, validate_period
from .fundamentals import refresh_fundamentals, update_from_quotes
from .help import AspoolCommand, AspoolExGroup, AspoolGroup
from .store import default_root, initialize
from .tdx_online import update_daily_offline, update_online

PERIODS = ["daily", "minutes"]


class _AssetType(click.Choice):
    """Keep the established help width while documenting ETF in the reference text."""

    def get_metavar(self, param, ctx):
        return "[stock|index]"


def _root(value: Path | None) -> Path:
    return value.expanduser().resolve() if value else default_root()


def _free_stockdb_root() -> Path:
    return free_stockdb_root()


def _validate_source(source: Path) -> None:
    required = [source / "data", source / "pybao", source / "stockdb", source / "stockdb.conf"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise click.UsageError("free-stockdb 数据源不完整: " + ", ".join(missing))


def _coverage_symbol_count(root: Path, period: str) -> int:
    table = "coverage" if period == "daily" else "coverage_minutes"
    conn = duckdb.connect(root / "catalog.duckdb", read_only=True)
    try:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])
    finally:
        conn.close()


@click.group(cls=AspoolGroup)
def cli() -> None:
    """维护本地 A 股长期历史数据池。"""


@cli.group("ex", cls=AspoolExGroup)
def ex() -> None:
    """维护跨市场扩展资产；当前已交付 ETF，其他类别逐步接入。"""


@ex.command("categories", cls=AspoolCommand)
def ex_categories() -> None:
    """列出 ETF、港股、美股和大宗期货等扩展资产类别。"""
    from .ex_domain import load_ex_categories

    click.echo(json.dumps(load_ex_categories(), ensure_ascii=False, indent=2))


@cli.command(cls=AspoolCommand)
@click.option("--root", type=click.Path(path_type=Path))
def init(root: Path | None) -> None:
    """初始化本地数据池。"""
    target = _root(root)
    initialize(target)
    click.echo(target)


@cli.command("import", cls=AspoolCommand)
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


@cli.command(cls=AspoolCommand)
@click.option(
    "--type",
    "asset_type",
    type=_AssetType(["stock", "index", "ex", "etf"]),
    default="stock",
    show_default=True,
    help="维护股票、指数或 ETF 日线数据池",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="ex 资产类别；当前已交付 ETF，其他类别按 ex 配置规划",
)
@click.option(
    "--source", type=click.Choice(["tdx", "free-stockdb", "baostock"]),
    default="tdx", show_default=True
)
@click.option("--period", type=click.Choice(PERIODS), default="daily", show_default=True)
@click.option(
    "--tdx-mode",
    type=click.Choice(["online", "offline"]),
    default="online",
    show_default=True,
    help="source=tdx 时使用在线 K 线或本地 vipdoc K 线。",
)
@click.option("--async", "async_mode", is_flag=True, help="使用异步客户端获取在线 K 线。")
@click.option(
    "--workers",
    type=click.IntRange(1, 8),
    default=4,
    show_default=True,
    help="日线在线同步的独立连接数。",
)
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1))
@click.option("--start", "start_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="股票日线字段补齐区间起点，YYYY-MM-DD。")
@click.option("--end", "end_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="股票日线字段补齐区间终点，YYYY-MM-DD。")
@click.option("--lookback", type=click.IntRange(min=1),
              help="字段补齐交易日数；缺省读取配置（30）。")
@click.option("--baostock/--no-baostock", default=None,
              help="以 BaoStock 只读校对冲突样本；默认读取配置，不覆盖冲突数据。")
@click.option("--enrich/--no-enrich", default=True,
              help="补齐日期股本、参考价及量价指标，再重算涨跌停和连板。")
@click.option("--enrich-only", is_flag=True,
              help="跳过主源 K 线同步，仅执行股票日线字段补齐和派生重算。")
def sync(
    source: str,
    period: str,
    tdx_mode: str,
    async_mode: bool,
    root: Path | None,
    limit: int | None,
    asset_type: str = "stock",
    workers: int = 4,
    category: str | None = None,
    start_date=None,
    end_date=None,
    lookback: int | None = None,
    baostock: bool | None = None,
    enrich: bool = True,
    enrich_only: bool = False,
) -> None:
    """从指定源校准历史数据，并补齐至最新在线行情。"""
    target = _root(root)
    stock_daily = asset_type == "stock" and period == "daily"
    if not stock_daily and (start_date or end_date or lookback is not None
                            or baostock is not None or enrich_only):
        raise click.UsageError("字段补齐参数仅支持 stock 日线")
    if enrich_only and (not enrich or not stock_daily):
        raise click.UsageError("--enrich-only 需要 stock 日线及 --enrich")
    if start_date and end_date and start_date > end_date:
        raise click.UsageError("--start 不能晚于 --end")
    if enrich_only:
        _complete_stock_sync(target, start_date, end_date, lookback, limit, workers,
                             baostock, enrich)
        return
    if source == "baostock":
        if asset_type != "stock" or period != "daily" or async_mode or tdx_mode != "online":
            raise click.UsageError("baostock 仅支持 stock 日线串行补齐")
        _complete_stock_sync(target, start_date, end_date, lookback, limit, workers,
                             True, enrich, supplement=True)
        return
    if asset_type == "index":
        if source != "tdx" or period != "daily":
            raise click.UsageError("指数仅支持 --source tdx --period daily")
        from tdxman.exceptions import TdxError

        from .index_pool import sync_indices

        try:
            report, path = sync_indices(target, tdx_mode, async_mode, limit, workers=workers)
        except (OSError, ValueError, TdxError) as exc:
            raise click.ClickException(str(exc)) from exc
        successes = report["success"]
        click.echo(
            f"指数日线：成功 {len(successes)}，失败 {len(report['failed'])}；"
            f"新增 {sum(r['added'] for r in successes)}，"
            f"修改 {sum(r['changed'] for r in successes)}，"
            f"未变 {sum(r['unchanged'] for r in successes)}，"
            f"隔离异常 {sum(len(r['rejected']) for r in successes)}；报告：{path}"
        )
        if report["failed"]:
            raise click.ClickException("部分指数未完成，请查看报告后重试")
        return
    if asset_type in {"etf", "ex"}:
        if asset_type == "ex":
            from .ex_domain import ex_category

            category = (category or "ETF").upper()
            try:
                definition = ex_category(category)
            except ValueError as exc:
                raise click.UsageError(str(exc)) from exc
            if definition["status"] != "implemented":
                raise click.UsageError(f"ex 类别 {category} 尚未实现；当前已交付类别：ETF")
        elif category and category.upper() != "ETF":
            raise click.UsageError("--type etf 只能使用 --category ETF")
        if source != "tdx" or period != "daily":
            raise click.UsageError("ex/ETF 仅支持 --source tdx --period daily")
        initialize(target)
        try:
            if tdx_mode == "offline":
                from .universe import refresh_etf_universe, universe_is_stale

                if limit is None and universe_is_stale(target, asset_type="etf"):
                    refresh_etf_universe(target)
                symbols, rows = update_daily_offline(target, limit, "etf")
            else:
                symbols, rows = update_online(
                    target, "daily", async_mode, limit, workers=workers, asset_type="etf"
                )
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"ETF 日线校准：{symbols} 个标的，写入变化记录 {rows} 条")
        return
    initialize(target)
    if source == "tdx" and _coverage_symbol_count(target, period) == 0:
        raise click.ClickException(
            "tdx 同步只修补已导入标的；空数据池请先运行 "
            f"aspool import --period {period}，或 aspool sync --source free-stockdb "
            f"--period {period}"
        )
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
    primary_error = None
    if source == "tdx" and tdx_mode == "offline":
        if period != "daily":
            raise click.UsageError("tdx 离线同步当前仅支持 daily；分钟线请使用 free-stockdb 导入")
        symbols, rows = update_daily_offline(target, limit)
    else:
        try:
            symbols, rows = update_online(target, period, async_mode, limit, workers=workers,
                                          derive_limits=not (stock_daily and enrich))
        except ValueError as exc:
            if not stock_daily:
                raise click.ClickException(str(exc)) from exc
            primary_error = str(exc)
            symbols, rows = 0, 0
            click.echo(f"主源部分失败，继续字段补齐：{exc}")
    stage = "通达信 K 线校准" if source == "tdx" else "在线尾部校准"
    click.echo(f"{stage}：{symbols} 个标的，写入变化记录 {rows} 条 {period} K")
    if period == "daily" and not (source == "tdx" and tdx_mode == "offline"):
        _show_maintenance_report(target)
    if stock_daily:
        _complete_stock_sync(target, start_date, end_date, lookback, limit, workers,
                             baostock, enrich)
    if primary_error:
        raise click.ClickException(primary_error)


def _complete_stock_sync(root, start, end, lookback, limit, workers, baostock, enrich,
                         supplement=False):
    from .baostock_source import enabled, supplement_daily
    from .config import read_config

    failures = []
    options = dict(start=start.date() if start else None, end=end.date() if end else None,
                   lookback=lookback, limit=limit)
    if supplement:
        report, path = supplement_daily(root, **options, recompute_limits=not enrich)
        _show_baostock_report(report, path)
        if report["status"] != "ok":
            failures.append(f"BaoStock：{path}")
    if enrich:
        from .enrichment import enrich_daily

        options["lookback"] = lookback or int(read_config().get("aspool.baostock.lookback", "30"))
        click.echo("补齐日期基础数据、量价指标，再重算涨跌停及连板……")
        report, path = enrich_daily(root, **options, workers=workers,
                                   compare_baostock=enabled() if baostock is None else baostock)
        click.echo(f"字段补齐：{report['processed']}/{report['requested']} 个标的，"
                   f"变化 {report['changed_rows']} 行，"
                   f"涨跌停发布 {report.get('limit_batches', 0)} 日；"
                   f"状态 {report['status']}；报告：{path}")
        if report["status"] != "ok":
            failures.append(f"字段补齐：{path}")
    if failures:
        raise click.ClickException("部分阶段未完成：" + "；".join(failures))


def _show_maintenance_report(root):
    path = Path(root) / "reports/maintenance/latest.json"
    if path.exists():
        report = json.loads(path.read_text())
        click.echo(
            f"未返回行情 {len(report.get('missing', []))}，"
            f"拒绝 {len(report.get('rejected', []))}，"
            f"失败 {len(report.get('failed', []))}；报告：{report.get('report', path)}"
        )


def _show_baostock_report(report, path):
    click.echo(
        f"BaoStock 补齐：{report['success']}/{report['requested']} 个待补标的，"
        f"变化日线 {report['changed_rows']} 行，日期事实 {report['fact_rows']} 行；"
        f"冲突 {len(report['conflicts'])}，未覆盖标的 {len(report['unsupported'])}；报告：{path}"
    )


@cli.command(cls=AspoolCommand)
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1))
@click.option("--async", "async_mode", is_flag=True, help="使用异步报价客户端。")
@click.option(
    "--workers", type=click.IntRange(1, 8), default=4, show_default=True, help="独立报价连接数。"
)
@click.option("--baostock/--no-baostock", default=None,
              help="报价后运行 BaoStock 历史补齐；默认读取配置（启用）。")
@click.option("--lookback", type=click.IntRange(min=1), help="BaoStock 补齐交易日数；默认 30。")
def update(
    root: Path | None, limit: int | None, async_mode: bool, workers: int,
    baostock: bool | None = None, lookback: int | None = None,
) -> None:
    """收盘后更新报价，再用 BaoStock 补齐历史字段与交易状态。"""
    from tdxman.exceptions import TdxError

    from .baostock_source import enabled, supplement_daily
    from .fundamentals import QuoteUpdateError

    target = _root(root)
    use_baostock = enabled() if baostock is None else baostock
    if lookback is not None and not use_baostock:
        raise click.UsageError("--lookback 需要启用 --baostock")
    primary_error = None
    try:
        symbols, rows = update_from_quotes(
            target, limit, async_mode=async_mode, workers=workers
        )
        click.echo(f"报价检查完成：{symbols} 个标的，写入变化日线 {rows} 行并刷新基本面快照")
    except (ValueError, OSError, TdxError) as exc:
        if not use_baostock or "09:00 至 15:30" in str(exc):
            raise click.ClickException(str(exc)) from exc
        primary_error = exc
        click.echo(f"通达信阶段未全部完成：{exc}；继续执行 BaoStock 补齐。", err=True)
    _show_maintenance_report(target)
    if use_baostock:
        report, path = supplement_daily(target, lookback=lookback, limit=limit)
        _show_baostock_report(report, path)
        if report["status"] != "ok":
            raise click.ClickException(f"BaoStock 补齐未全部完成：{path}")
        if isinstance(primary_error, QuoteUpdateError):
            from .pool import pool_lock
            from .security_facts import lifecycle_map

            with pool_lock(target):
                metadata = lifecycle_map(target)
            day = primary_error.trade_date
            omitted = [code for code in primary_error.report["missing"] if not any(
                row.get("delisting_date") is not None and row["delisting_date"] <= day
                for symbol, row in metadata.items() if symbol.startswith(code + ".")
            )]
            if not (omitted or primary_error.report["failed"] or primary_error.report["rejected"]
                    or primary_error.report.get("limit_events", {}).get("status") == "failed"):
                primary_error = None
    if primary_error is not None:
        raise click.ClickException(str(primary_error))


@cli.command("fundamentals", cls=AspoolCommand)
@click.option("--async", "async_mode", is_flag=True, help="使用 tdxman 异步 MAC 客户端。")
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1), help="仅维护前 N 个标的，用于验证。")
def fundamentals(async_mode: bool, root: Path | None, limit: int | None) -> None:
    """手动维护全市场低频基本面快照。"""
    symbols, rows = refresh_fundamentals(_root(root), async_mode=async_mode, limit=limit)
    click.echo(f"基本面快照完成：{symbols} 个标的，写入 {rows} 条")


@cli.command(cls=AspoolCommand)
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
                + (" where source not like 'tdxman:etf%'" if period == "daily" else "")
            ).fetchone()
            for period, table in [("daily", "coverage"), ("minutes", "coverage_minutes")]
        }
        latest = conn.execute(
            "select command, status, started_at, finished_at, symbols, rows_written "
            "from sync_runs order by started_at desc limit 1"
        ).fetchone()
        try:
            index_summary = conn.execute(
                "select count(*), min(start_date), max(end_date), "
                "sum(row_count) from index_coverage"
            ).fetchone()
        except duckdb.CatalogException:
            index_summary = (0, None, None, 0)
        etf_summary = conn.execute(
            "select count(*), min(start_date), max(end_date), sum(row_count) "
            "from coverage where source like 'tdxman:etf%'"
        ).fetchone()
    finally:
        conn.close()
    click.echo(f"root: {target}")
    for period, summary in summaries.items():
        click.echo(
            f"{period}: symbols={summary[0]}, range={summary[1]} ~ {summary[2]}, "
            f"rows={summary[3] or 0}"
        )
    click.echo(
        f"indices: symbols={index_summary[0]}, range={index_summary[1]} ~ {index_summary[2]}, "
        f"rows={index_summary[3] or 0}"
    )
    click.echo(
        f"etf: symbols={etf_summary[0]}, range={etf_summary[1]} ~ {etf_summary[2]}, "
        f"rows={etf_summary[3] or 0}"
    )
    if latest:
        click.echo(f"last import: {latest}")
    maintenance = target / "reports/maintenance/latest.json"
    if maintenance.exists():
        report = json.loads(maintenance.read_text())
        click.echo(
            f"last maintenance: {report['command']} {report['status']} {report['finished_at']}"
        )
        _show_maintenance_report(target)


@cli.command("universe", cls=AspoolCommand)
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--type", "asset_type", type=click.Choice(["stock", "etf"]), default="stock")
def universe(root: Path | None, asset_type: str) -> None:
    """刷新股票或 ETF 证券目录，展示待初始化与非活跃代码。"""
    from .universe import refresh_etf_universe, refresh_stock_universe

    try:
        report = (refresh_etf_universe if asset_type == "etf" else refresh_stock_universe)(
            _root(root)
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"证券目录：{report['listed']} 个；待初始化 {len(report['added'])} 个；"
        f"标记非活跃 {len(report['inactive'])} 个；重新活跃 {len(report['reactivated'])} 个"
    )


@cli.command(cls=AspoolCommand)
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
    symbol = symbol.upper()
    if not re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", symbol):
        raise click.BadParameter("SYMBOL 应为市场加六位代码，如 SZ000001 或 SH600519")

    target = _root(root)
    key = "trade_date" if period == "daily" else "timestamp"
    bars_root = target / "lake" / "bars" / period
    matches = list(bars_root.glob(f"market=*/symbol={symbol}/bars.parquet"))
    if not matches:
        raise click.ClickException(f"本地数据池未找到 {symbol} 的 {period} 数据")

    conn = duckdb.connect()
    try:
        sql = f"select * exclude (symbol) from read_parquet('{matches[0]}')"
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
        try:
            table = conn.execute(sql + f" order by {key}", params).fetch_arrow_table()
        except duckdb.Error as exc:
            raise click.ClickException(f"读取本地数据失败：{exc}") from exc
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
