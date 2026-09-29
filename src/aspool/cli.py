from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import duckdb
import pyarrow.csv as pacsv

from .config import free_stockdb_root
from .free_stockdb import import_adjustments, import_daily, import_minutes, validate_period
from .help import AspoolCommand, AspoolExGroup, AspoolGroup
from .store import default_root, initialize
from .tdx_online import update_daily_offline, update_online

PERIODS = ["daily", "minutes"]


class _AssetType(click.Choice):
    """Asset choices used by the shared sync command."""


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
    type=_AssetType(["stock", "index", "ex", "etf", "all"]),
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
@click.option("--count", type=click.IntRange(min=1, max=60), default=None,
              show_default="10", help="未指定日期范围时检查最近 N 个已完成交易日。")
@click.option("--lookback", type=click.IntRange(min=1),
              help="字段补齐交易日数；缺省读取配置（30）。")
@click.option("--baostock/--no-baostock", default=None,
              help="以 BaoStock 只读校对冲突样本；默认读取配置，不覆盖冲突数据。")
@click.option("--enrich/--no-enrich", default=True,
              help="补齐日期股本、参考价及量价指标，再重算涨跌停和连板。")
@click.option("--enrich-only", is_flag=True,
              help="跳过主源 K 线同步，仅执行股票日线字段补齐和派生重算。")
@click.option("--retries", type=click.IntRange(0, 5), default=2, show_default=True)
@click.option("--retry-delay", type=click.FloatRange(0, 30), default=1.0, show_default=True)
@click.option("--status", "status_filter", type=click.Choice(["missing", "invalid"]),
              help="只修复指定未解决状态。")
@click.option("--max-consecutive-failures", type=click.IntRange(min=1), default=3,
              show_default=True, help="单标的重试耗尽后连续失败熔断阈值。")
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
    count: int | None = None,
    lookback: int | None = None,
    baostock: bool | None = None,
    enrich: bool = True,
    enrich_only: bool = False,
    retries: int = 2,
    retry_delay: float = 1.0,
    status_filter: str | None = None,
    max_consecutive_failures: int = 3,
) -> None:
    """从指定源校准历史数据，并补齐至最新在线行情。"""
    if (start_date is None) != (end_date is None):
        raise click.UsageError("--start 与 --end 必须同时指定")
    if count is not None and start_date is not None:
        raise click.UsageError("--count 与 --start/--end 互斥")
    count = count or 10
    target = _root(root) if root else (
        Path("data").resolve() if Path("data/stocks.sqlite").is_file() else _root(None)
    )
    if asset_type == "all":
        if (
            source != "tdx"
            or period != "daily"
            or category
            or tdx_mode != "online"
            or status_filter
        ):
            raise click.UsageError("--type all 仅支持在线 TDX 日线")
        _run_all(
            target, mode="sync", limit=limit, workers=workers,
            start=start_date.date().isoformat() if start_date else None,
            end=end_date.date().isoformat() if end_date else None,
            count=count,
            retries=retries, retry_delay=retry_delay,
            max_consecutive_failures=max_consecutive_failures,
        )
        return
    stock_daily = asset_type == "stock" and period == "daily"
    if status_filter and not stock_daily:
        raise click.UsageError("--status 仅支持股票日线")
    if stock_daily and (target / "stocks.sqlite").is_file():
        if source not in ("tdx", "baostock") or tdx_mode != "online" or async_mode:
            raise click.UsageError("SQLite 日线 sync 支持在线 tdx/baostock；不支持 --async")
        if not enrich or enrich_only or baostock is not None or lookback is not None:
            raise click.UsageError("SQLite sync 自动补算缺失指标；请用 --source、--start、--end")
        _sqlite_command(target, source=source, limit=limit, mode="sync", workers=workers,
                        start=start_date.date().isoformat() if start_date else None,
                        end=end_date.date().isoformat() if end_date else None,
                        count=count,
                        retries=retries, retry_delay=retry_delay,
                        status_filter=status_filter,
                        max_consecutive_failures=max_consecutive_failures)
        return
    if not stock_daily and (lookback is not None or baostock is not None or enrich_only):
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

        from .securities import active_indices
        from .sqlite_index_sync import sync_indices
        from .universe import refresh_index_universe

        try:
            if tdx_mode == "online":
                refresh_index_universe(target)
            items = active_indices(target)
            report, path = sync_indices(target, tdx_mode, async_mode, limit, workers=workers,
                                        retries=retries, retry_delay=retry_delay,
                                        start=start_date.date().isoformat() if start_date else None,
                                        end=end_date.date().isoformat() if end_date else None,
                                        count=count, items=items)
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
        if report["failed"] or report.get("status") in ("partial", "failed"):
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
        if (target / "etfs.sqlite").exists():
            from .sqlite_etf_sync import sync_etfs

            try:
                report, path = sync_etfs(target, mode=tdx_mode, asynchronous=async_mode,
                                        limit=limit, workers=workers, retries=retries,
                                        retry_delay=retry_delay,
                                        start=start_date.date().isoformat() if start_date else None,
                                        end=end_date.date().isoformat() if end_date else None,
                                        count=count)
            except (OSError, ValueError) as exc:
                raise click.ClickException(str(exc)) from exc
            click.echo(f"ETF SQLite：成功 {len(report['success'])}，失败 {len(report['failed'])}；"
                       f"当日无交易 {len(report['no_trade'])}；"
                       f"新增 {sum(r['added'] for r in report['success'])}，"
                       f"修改 {sum(r['changed'] for r in report['success'])}；报告：{path}")
            if report["status"] != "ok":
                raise click.ClickException("部分 ETF 未完成，请检查报告")
            return
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
@click.option("--type", "asset_type", type=click.Choice(["stock", "index", "etf", "all"]),
              default="stock", show_default=True)
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1))
@click.option("--source", type=click.Choice(["tdx", "baostock"]), default="tdx", show_default=True)
@click.option("--symbol", "symbols", multiple=True, help="指定股票，如 000001.SZ；可重复。")
@click.option(
    "--workers", type=click.IntRange(1, 8), default=4, show_default=True, help="独立报价连接数。"
)
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--retries", type=click.IntRange(0, 5), default=2, show_default=True)
@click.option("--retry-delay", type=click.FloatRange(0, 30), default=1.0, show_default=True)
@click.option("--status", "status_filter", type=click.Choice(["missing", "invalid"]),
              help="只修复指定未解决状态。")
@click.option("--max-consecutive-failures", type=click.IntRange(min=1), default=3,
              show_default=True)
def update(
    asset_type, root, limit, source, symbols, workers, start, end, retries, retry_delay,
    status_filter, max_consecutive_failures,
) -> None:
    """收盘报价更新 SQLite 日线及派生；备用源用 --source baostock 显式选择。"""
    target = _root(root) if root else Path("data").resolve()
    if asset_type == "all":
        if source != "tdx" or symbols or status_filter:
            raise click.UsageError("--type all 仅支持完整 TDX 更新，不接受 --symbol/--status")
        _run_all(target, mode="update", limit=limit, workers=workers,
                 retries=retries, retry_delay=retry_delay,
                 max_consecutive_failures=max_consecutive_failures)
        return
    if asset_type == "index":
        if source != "tdx" or symbols or start or end or status_filter:
            raise click.UsageError("指数 update 仅支持 TDX K 线")
        from .securities import active_indices
        from .sqlite_index_sync import sync_indices
        from .universe import refresh_index_universe

        refresh_index_universe(target)
        report, path = sync_indices(target, workers=workers, limit=limit, retries=retries,
                                    retry_delay=retry_delay, items=active_indices(target))
        click.echo(f"指数 update：{report['status']}；报告：{path}")
        if report["status"] != "ok":
            raise click.ClickException("指数数据未完整更新")
        return
    if asset_type == "etf":
        if source != "tdx" or symbols or start or end or status_filter:
            raise click.UsageError("ETF update 仅支持 TDX")
        from .sqlite_etf_sync import sync_etfs

        report, path = sync_etfs(target, workers=workers, limit=limit, retries=retries,
                                 retry_delay=retry_delay)
        click.echo(f"ETF update：{report['status']}；报告：{path}")
        if report["status"] != "ok":
            raise click.ClickException("ETF 数据未完整更新")
        return
    _sqlite_command(target, source=source, symbols=symbols, limit=limit, workers=workers,
                    start=start.date().isoformat() if start else None,
                    end=end.date().isoformat() if end else None,
                    retries=retries, retry_delay=retry_delay, status_filter=status_filter,
                    max_consecutive_failures=max_consecutive_failures)


def _sqlite_command(target, **options):
    from datetime import datetime, timezone

    from .sqlite_update_cli import run_update

    # Reports never become part of the runtime data replica.
    folder = target.parent / ".local" / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("sqlite-" + options.get("mode", "update") + "-" +
                     datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    report = {"status": "running", "root": str(target)}
    path.write_text(json.dumps(report) + "\n")
    try:
        run_update(target, report=report, **options)
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        raise click.ClickException(f"{exc}；报告：{path}") from exc
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    click.echo(f"SQLite {options.get('mode', 'update')}：{report['status']}；报告：{path}")
    if report["status"] not in ("ok", "completed_with_missing"):
        raise click.ClickException("部分数据未完成，旧数据已保留；详见报告")


def _run_all(target, *, mode, limit, workers, retries, retry_delay,
             max_consecutive_failures, start=None, end=None, count=10):
    """Run the complete operational dataset and keep one inspectable summary."""
    from datetime import datetime, timezone

    from tdxman.mac.client import MacClient

    from .securities import active_indices
    from .sqlite_directory import publish_directory as publish_stock_directory
    from .sqlite_directory import read_directory
    from .sqlite_etf_sync import online_items, sync_etfs
    from .sqlite_etf_sync import publish_directory as publish_etf_directory
    from .sqlite_index_sync import sync_indices
    from .sqlite_update_cli import SourceSession, run_update
    from .universe import refresh_index_universe

    folder = target.parent / ".local" / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (
        "all-" + mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    summary = {"status": "running", "mode": mode, "root": str(target), "blocks": {}}
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    try:
        directory_events = []
        directory_session = SourceSession(
            lambda: MacClient.from_best_host(
                heartbeat_interval=0, auto_reconnect=False, timeout=10
            ),
            retries=retries,
            delay=retry_delay,
            events=directory_events,
        )
        directory_report = {}
        try:
            stock_names = read_directory(directory_session, report=directory_report)
        finally:
            directory_session.close()
        if directory_report["failed"]:
            raise ValueError(f"股票目录读取不完整：{directory_report['failed']}")
        publish_stock_directory(
            target,
            stock_names,
            day=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        )
        etf_items = online_items(
            retries=retries, retry_delay=retry_delay, events=directory_events
        )
        etf_directory = publish_etf_directory(target, etf_items)
        index_directory = refresh_index_universe(target)
        summary["blocks"]["directory"] = {
            "status": "ok",
            "stocks": len(stock_names),
            "etfs": len(etf_items),
            "etf_changes": etf_directory,
            "index_changes": index_directory,
            "retries": directory_events,
        }
        window = dict(start=start, end=end, count=count) if mode == "sync" else {}
        index, index_path = sync_indices(target, workers=workers, limit=limit,
                                         retries=retries, retry_delay=retry_delay,
                                         items=active_indices(target), **window)
        summary["blocks"]["index"] = {"status": index["status"], "report": str(index_path)}
        stock = {}
        run_update(target, report=stock, mode=mode, limit=limit, workers=workers,
                   start=start, end=end, retries=retries, retry_delay=retry_delay,
                   count=count,
                   max_consecutive_failures=max_consecutive_failures,
                   directory_rows=stock_names)
        summary["blocks"]["stock"] = stock
        etf, etf_path = sync_etfs(target, workers=workers, limit=limit,
                                  retries=retries, retry_delay=retry_delay, items=etf_items,
                                  **window)
        summary["blocks"]["etf"] = {"status": etf["status"], "report": str(etf_path)}
        states = [block.get("status") for block in summary["blocks"].values()]
        summary["status"] = (
            "failed" if any(s in ("failed", "partial", "aborted_source_failure") for s in states)
            else "completed_with_missing" if "completed_with_missing" in states else "ok"
        )
    except Exception as exc:
        summary.update(status="failed", error=str(exc))
        raise click.ClickException(f"全量数据编排失败：{exc}；报告：{path}") from exc
    finally:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    click.echo(f"全部数据 {mode}：{summary['status']}；报告：{path}")
    if summary["status"] == "failed":
        raise click.ClickException("存在未完成数据块")


@cli.command("fundamentals", cls=AspoolCommand)
@click.argument("action", type=click.Choice(["update", "status"]), required=False,
                default="update")
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--limit", type=click.IntRange(min=1), help="仅维护前 N 个标的，用于验证。")
@click.option("--source", type=click.Choice(["tdx"]), default="tdx", show_default=True)
@click.option("--symbol", "symbols", multiple=True, help="仅更新指定规范证券代码。")
@click.option("--retries", type=click.IntRange(0, 5), default=2, show_default=True)
@click.option("--retry-delay", type=click.FloatRange(0, 30), default=1.0, show_default=True)
@click.option("--max-consecutive-failures", type=click.IntRange(min=1), default=3,
              show_default=True)
def fundamentals(action, root, limit, source, symbols, retries, retry_delay,
                 max_consecutive_failures) -> None:
    """手动维护或检查按报告日期保存的低频基本面。"""
    target = _root(root) if root else Path("data").resolve()
    if action == "status":
        if symbols or limit:
            raise click.UsageError("fundamentals status 不接受 --symbol/--limit")
        from .fundamentals_store import fundamentals_status

        click.echo(json.dumps(fundamentals_status(target), ensure_ascii=False, indent=2))
        return
    from .fundamentals_update import update_fundamentals

    report = update_fundamentals(
        target,
        symbols=symbols,
        limit=limit,
        retries=retries,
        retry_delay=retry_delay,
        max_consecutive_failures=max_consecutive_failures,
    )
    click.echo(
        f"基本面：状态 {report['status']}；成功 {len(report['success'])}，"
        f"失败 {len(report['failed'])}；财报变化 {report['changed']['financial_reports']}，"
        f"股东人数变化 {report['changed']['shareholder_counts']}"
    )
    if report["status"] != "ok":
        raise click.ClickException("部分基本面未更新，请重试失败证券")


@cli.command("platform", cls=AspoolCommand)
@click.argument(
    "action", type=click.Choice(["prepare", "verify", "status", "activate", "rollback"])
)
@click.option("--root", type=click.Path(path_type=Path))
def platform(action: str, root: Path | None) -> None:
    """准备、核验或检查分层数据布局；prepare 不切换公开读取。"""
    from .platform_v2 import (
        activate_platform_v2,
        platform_status,
        prepare_platform_v2,
        rollback_platform_v2,
        verify_platform_v2,
    )

    target = _root(root) if root else Path("data").resolve()
    try:
        result = {
            "prepare": prepare_platform_v2,
            "verify": verify_platform_v2,
            "status": platform_status,
            "activate": activate_platform_v2,
            "rollback": rollback_platform_v2,
        }[action](target)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if action == "verify" and not result["ready"]:
        raise click.ClickException("分层数据核验未通过")


@cli.command(cls=AspoolCommand)
@click.option("--root", type=click.Path(path_type=Path))
@click.option("--dataset", type=click.Choice(
    ["all", "securities", "calendar", "fundamentals", "shareholder-counts",
     "stock-bars", "corporate-actions", "stock-features", "market-summary",
     "index-bars", "etf-bars", "etf-factors"]),
    default="all", show_default=True)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def status(root: Path | None, dataset: str, fmt: str) -> None:
    """只读检查全部正式数据块的覆盖范围。"""
    target = _root(root)
    rows = _dataset_status(target)
    if dataset != "all":
        rows = [row for row in rows if row["dataset"] == dataset]
    if fmt == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    else:
        click.echo("dataset | rows | symbols | range | unresolved")
        click.echo("--------+------+---------+-------+-----------")
        for row in rows:
            click.echo(
                f"{row['dataset']} | {row.get('rows') if row.get('rows') is not None else '?'} | "
                f"{row.get('symbols') if row.get('symbols') is not None else '?'} | "
                f"{row.get('start') or '-'} ~ {row.get('end') or '-'} | "
                f"{row.get('unresolved', 0)}"
            )
    return


def _dataset_status(target: Path) -> list[dict[str, object]]:
    """Read-only, bounded metadata checks for every production data block."""
    rows: list[dict[str, object]] = []
    catalog_path = target / "catalog.duckdb"
    if catalog_path.is_file():
        with duckdb.connect(str(catalog_path), read_only=True) as conn:
            tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
            if "securities" in tables:
                count, active = conn.execute(
                    "SELECT count(*),count(*) FILTER (WHERE active) FROM securities"
                ).fetchone()
                rows.append(dict(dataset="securities", rows=count, symbols=active))
            if "security_calendar" in tables:
                count, first, last = conn.execute(
                    "SELECT count(*),min(trade_date),max(trade_date) "
                    "FROM security_calendar WHERE is_open"
                ).fetchone()
                rows.append(dict(dataset="calendar", rows=count, symbols=1, start=first, end=last))
            if "fundamental_snapshots" in tables and not (target / "fundamentals.sqlite").is_file():
                count, last = conn.execute(
                    "SELECT count(*),max(refreshed_at) FROM fundamental_snapshots"
                ).fetchone()
                rows.append(dict(dataset="fundamentals", rows=count, symbols=count, end=last))
    fundamentals_path = target / "fundamentals.sqlite"
    if fundamentals_path.is_file():
        from .fundamentals_store import fundamentals_status

        state = fundamentals_status(target)["datasets"]
        for dataset, source in (
            ("fundamentals", "stock_financial_reports"),
            ("shareholder-counts", "stock_shareholder_counts"),
        ):
            item = state[source]
            rows.append(dict(dataset=dataset, **item))
    if (target / "stocks.sqlite").is_file():
        from .pool import DataPool

        with DataPool(target).stock_snapshot() as reader:
            for dataset, table, date_field in (
                ("stock-bars", "daily_bars", "trade_date"),
                ("corporate-actions", "corporate_actions", "effective_date"),
                ("stock-features", reader.feature_table, "trade_date"),
                ("market-summary", reader.summary_table, "period_end"),
            ):
                first = reader.conn.execute(
                    f"SELECT min({date_field}) FROM {table}"
                ).fetchone()[0]
                last = reader.conn.execute(
                    f"SELECT max({date_field}) FROM {table}"
                ).fetchone()[0]
                row = dict(dataset=dataset, rows=None, symbols=None, start=first, end=last)
                rows.append(row)
    for dataset, filename, tables in (
        ("index-bars", "indices.sqlite", [("daily_bars", "trade_date")]),
        ("etf-bars", "etfs.sqlite", [("daily_bars", "trade_date")]),
        ("etf-factors", "etfs.sqlite", [("adjustment_factors", "trade_date")]),
    ):
        path = target / filename
        if not path.is_file():
            continue
        import sqlite3

        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            table, date_field = tables[0]
            count, symbols, first, last = conn.execute(
                f"SELECT count(*),count(DISTINCT symbol),min({date_field}),"
                f"max({date_field}) FROM {table}"
            ).fetchone()
            rows.append(dict(dataset=dataset, rows=count, symbols=symbols, start=first, end=last))
    return rows


def _legacy_status(root: Path | None) -> None:
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
    if (target / "stocks.sqlite").is_file():
        from .sqlite_stock_store import stock_connection

        with stock_connection(target) as stocks:
            summaries["daily"] = stocks.execute(
                "SELECT count(DISTINCT symbol),min(trade_date),max(trade_date),count(*) "
                "FROM daily_bars"
            ).fetchone()
    if (target / "indices.sqlite").is_file():
        from .sqlite_index_store import index_connection

        with index_connection(target) as indices:
            index_summary = indices.execute(
                "SELECT count(DISTINCT symbol),min(trade_date),max(trade_date),count(*) "
                "FROM daily_bars"
            ).fetchone()
    if (target / "etfs.sqlite").is_file():
        from .sqlite_etf_store import connection

        with connection(target) as etfs:
            etf_summary = etfs.execute(
                "SELECT count(DISTINCT symbol),min(trade_date),max(trade_date),count(*) "
                "FROM daily_bars"
            ).fetchone()
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


@cli.command("directory", cls=AspoolCommand)
@click.option("--root", type=click.Path(path_type=Path))
@click.option(
    "--type", "asset_type", type=click.Choice(["stock", "etf", "index", "all"]), default="stock"
)
def directory(root: Path | None, asset_type: str) -> None:
    """刷新在线证券目录，展示新增与非活跃代码。"""
    from .universe import refresh_etf_universe, refresh_index_universe, refresh_stock_universe

    try:
        target = _root(root)
        if asset_type == "index":
            report = refresh_index_universe(target)
            click.echo(
                f"指数目录：{report['listed']} 个；新增 {report['added']} 个；"
                f"标记非活跃 {report['inactive']} 个；分类 {report['categories']}；"
                f"排除昨日风格 {len(report['excluded'])} 个"
            )
            return
        refreshers = (
            [("stock", refresh_stock_universe), ("etf", refresh_etf_universe),
             ("index", refresh_index_universe)]
            if asset_type == "all"
            else [
                (
                    asset_type,
                    refresh_etf_universe if asset_type == "etf" else refresh_stock_universe,
                )
            ]
        )
        reports = [(kind, refresh(target)) for kind, refresh in refreshers]
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    for kind, report in reports:
        if kind == "index":
            click.echo(
                f"指数目录：{report['listed']} 个；新增 {report['added']} 个；"
                f"标记非活跃 {report['inactive']} 个；分类 {report['categories']}；"
                f"排除昨日风格 {len(report['excluded'])} 个"
            )
            continue
        click.echo(
            f"{kind} 目录：{report['listed']} 个；待初始化 {len(report['added'])} 个；"
            f"标记非活跃 {report['inactive']} 个；重新活跃 {len(report['reactivated'])} 个"
        )


@cli.command("universe", cls=AspoolCommand, hidden=True)
@click.option("--root", type=click.Path(path_type=Path))
@click.option(
    "--type", "asset_type", type=click.Choice(["stock", "etf", "index", "all"]), default="stock"
)
def universe_alias(root: Path | None, asset_type: str) -> None:
    """Compatibility alias for directory."""
    from click import Context

    ctx = Context(directory)
    ctx.invoke(directory, root=root, asset_type=asset_type)


@cli.command("contract", cls=AspoolCommand)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def contract(fmt: str) -> None:
    """列出每块生产数据的输入、落库、输出和维护入口。"""
    from .data_flow_contract import data_flows, validate_data_flows

    validate_data_flows()
    rows = data_flows()
    if fmt == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    columns = ("name", "storage", "write_cli", "outputs", "read_cli")
    widths = {
        column: max(len(column), *(len(row[column]) for row in rows)) for column in columns
    }
    click.echo(" | ".join(column.ljust(widths[column]) for column in columns))
    click.echo("-+-".join("-" * widths[column] for column in columns))
    for row in rows:
        click.echo(" | ".join(row[column].ljust(widths[column]) for column in columns))


@cli.command(cls=AspoolCommand)
@click.argument("symbol", required=False)
@click.option(
    "--dataset",
    type=click.Choice(
        ["securities", "calendar", "fundamentals", "stock-bars", "corporate-actions",
         "shareholder-counts", "stock-features", "limit-events", "market-summary",
         "index-bars", "etf-bars", "etf-factors", "stock", "index", "etf",
         "security", "market"]
    ),
    default="stock-bars",
    show_default=True,
)
@click.option("--period", type=click.Choice(PERIODS), default="daily", show_default=True)
@click.option("--frequency", type=click.Choice(["D", "W", "M"]), default="D",
              show_default=True, help="市场汇总频率。")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]))
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]))
@click.option("--format", "fmt", type=click.Choice(["table", "csv", "json"]), default="table")
@click.option("--status", "status_filter",
              type=click.Choice(["traded", "no-trade", "missing", "invalid"]))
@click.option("--limit", "row_limit", type=click.IntRange(min=1), default=1000,
              show_default=True)
@click.option("--root", type=click.Path(path_type=Path))
def query(
    symbol: str | None,
    dataset: str,
    period: str,
    frequency: str,
    start: object,
    end: object,
    fmt: str,
    status_filter: str | None,
    row_limit: int,
    root: Path | None,
) -> None:
    """从统一数据根读取原始或派生日级数据。"""
    dataset = {"stock": "stock-bars", "index": "index-bars", "etf": "etf-bars",
               "security": "stock-features", "market": "market-summary"}.get(dataset, dataset)
    symbol_required = dataset in {"stock-bars", "index-bars", "etf-bars"}
    if symbol_required and not symbol:
        raise click.UsageError(f"--dataset {dataset} 需要 SYMBOL")
    symbol = symbol.upper() if symbol else None
    if symbol and re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", symbol):
        canonical = symbol
        symbol = symbol[-2:] + symbol[:6]
    elif symbol and re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", symbol):
        canonical = symbol[2:] + "." + symbol[:2]
    elif symbol:
        raise click.BadParameter("SYMBOL 应为市场加六位代码，如 000001.SZ 或 SZ000001")
    else:
        canonical = None

    target = _root(root)
    key = "trade_date" if period == "daily" else "timestamp"
    if dataset != "stock-bars" and period != "daily":
        raise click.UsageError("stock 以外的数据集只支持 daily")
    if dataset != "market-summary" and frequency != "D":
        raise click.UsageError("--frequency 仅适用于 --dataset market-summary")

    if dataset in {
        "securities", "fundamentals", "shareholder-counts", "corporate-actions", "etf-factors"
    }:
        import pandas as pd
        import pyarrow as pa

        params: list[object] = []
        clauses: list[str] = []
        if dataset in {"fundamentals", "shareholder-counts"} and (
            target / "fundamentals.sqlite"
        ).is_file():
            from .fundamentals_store import read_financial_reports, read_shareholder_counts

            reader = (
                read_financial_reports if dataset == "fundamentals" else read_shareholder_counts
            )
            frame = reader(
                target,
                symbols=canonical,
                start=start.date() if start else None,
                end=end.date() if end else None,
            ).head(row_limit)
        elif dataset in {"securities", "fundamentals"}:
            table_name = "securities" if dataset == "securities" else "fundamental_snapshots"
            if canonical:
                key = "symbol" if dataset == "securities" else "code"
                clauses.append(f"{key}=?")
                params.append(canonical if dataset == "securities" else canonical[:6])
            sql = f"SELECT * FROM {table_name}"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += f" ORDER BY symbol LIMIT {row_limit}"
            with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as conn:
                frame = conn.execute(sql, params).fetchdf()
        else:
            from .sqlite_etf_store import connection as etf_connection
            from .sqlite_stock_store import stock_connection

            db = stock_connection if dataset == "corporate-actions" else etf_connection
            table_name = (
                "corporate_actions" if dataset == "corporate-actions" else "adjustment_factors"
            )
            date_field = "effective_date" if dataset == "corporate-actions" else "trade_date"
            if canonical:
                clauses.append("symbol=?")
                params.append(canonical)
            if start:
                clauses.append(f"{date_field}>=?")
                params.append(start.date().isoformat())
            if end:
                clauses.append(f"{date_field}<=?")
                params.append(end.date().isoformat())
            sql = f"SELECT * FROM {table_name}"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += f" ORDER BY {date_field},symbol LIMIT {row_limit}"
            with db(target) as conn:
                frame = pd.read_sql_query(sql, conn, params=params)
        _render_query(pa.Table.from_pandas(frame, preserve_index=False), fmt)
        return

    if dataset == "stock-features":
        import pandas as pd
        import pyarrow as pa

        from .pool import DataPool

        if not any((canonical, start, end, status_filter)):
            raise click.UsageError("stock-features 需要 SYMBOL、日期边界或 --status")
        clauses, params = [], []
        if canonical:
            clauses.append("symbol=?")
            params.append(canonical)
        if start:
            clauses.append("trade_date>=?")
            params.append(start.date().isoformat())
        if end:
            clauses.append("trade_date<=?")
            params.append(end.date().isoformat())
        if status_filter == "missing":
            clauses.append("trading_status='MISSING'")
        elif status_filter == "invalid":
            clauses.append("(trading_status='INVALID' OR calc_status='INVALID')")
        elif status_filter:
            clauses.append("calc_status=?")
            params.append({"traded": "TRADED", "no-trade": "NO_TRADE"}[status_filter])
        with DataPool(target).stock_snapshot() as reader:
            sql = f"SELECT * FROM {reader.feature_table} WHERE " + " AND ".join(clauses)
            sql += f" ORDER BY trade_date,symbol LIMIT {row_limit}"
            frame = pd.read_sql_query(sql, reader.conn, params=params)
        _render_query(pa.Table.from_pandas(frame, preserve_index=False), fmt)
        return

    if dataset in {"market-summary", "limit-events", "calendar"}:
        import pyarrow as pa

        from .api_contract import DataPoolError
        from .pool import DataPool

        if dataset in {"market-summary", "limit-events"} and not (start and end):
            raise click.UsageError(f"--dataset {dataset} 必须同时指定 --start 和 --end")
        pool = DataPool(target)
        try:
            if dataset == "market-summary":
                frame = pool.read_market_summary(
                    start=start.date(), end=end.date(), frequency=frequency
                )
            elif dataset == "limit-events":
                frame = pool.read_limit_events(
                    start=start.date(), end=end.date(), symbols=canonical
                )
            else:
                frame = pool.read_trading_calendar(
                    start=start.date() if start else None,
                    end=end.date() if end else None,
                )
        except (DataPoolError, FileNotFoundError, ValueError) as exc:
            raise click.ClickException(f"读取本地数据失败：{exc}") from exc
        table = pa.Table.from_pandas(frame.head(row_limit), preserve_index=False)
        _render_query(table, fmt)
        return

    if period == "daily" and (
        (dataset == "stock-bars" and (target / "stocks.sqlite").is_file())
        or dataset == "index-bars"
        or dataset == "etf-bars"
    ):
        import pyarrow as pa

        from .api_contract import DataPoolError
        from .pool import DataPool

        reader = {
            "stock-bars": DataPool(target).read_daily,
            "index-bars": DataPool(target).read_index_daily,
            "etf-bars": DataPool(target).read_etf_daily,
        }[dataset]
        try:
            frame = reader(
                symbols=canonical,
                start=start.date() if start else None,
                end=end.date() if end else None,
            )
        except (DataPoolError, FileNotFoundError, ValueError) as exc:
            raise click.ClickException(f"读取本地数据失败：{exc}") from exc
        if frame.empty:
            raise click.ClickException(f"本地数据池未找到 {symbol} 的 {dataset} daily 数据")
        table = pa.Table.from_pandas(frame, preserve_index=False)
        _render_query(table, fmt)
        return

    from .daily_access import DailyStorage
    from .pool import pool_lock

    conn = duckdb.connect()
    try:
        with pool_lock(target):
            if period == "daily":
                from .change_protocol import assert_readable

                assert_readable(target)
                files = DailyStorage(target).bind(
                    conn, "query_bars", symbols=[f"{symbol[:2]}.{symbol[2:]}"]
                )
                if not files:
                    raise click.ClickException(f"本地数据池未找到 {symbol} 的 {period} 数据")
                columns = set(conn.sql("SELECT * FROM query_bars LIMIT 0").columns)
                excluded = "symbol, year" if "year" in columns else "symbol"
                sql = f"select * exclude ({excluded}) from query_bars"
            else:
                bars_root = target / "lake" / "bars" / period
                matches = list(bars_root.glob(f"market=*/symbol={symbol}/bars.parquet"))
                if not matches:
                    raise click.ClickException(f"本地数据池未找到 {symbol} 的 {period} 数据")
                conn.read_parquet([str(matches[0])]).create_view("query_bars")
                sql = "select * exclude (symbol) from query_bars"
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
                if period == "daily":
                    assert_readable(target)
                table = conn.execute(sql + f" order by {key}", params).fetch_arrow_table()
            except duckdb.Error as exc:
                raise click.ClickException(f"读取本地数据失败：{exc}") from exc
        _render_query(table, fmt)
    finally:
        conn.close()


def _render_query(table, fmt: str) -> None:
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
