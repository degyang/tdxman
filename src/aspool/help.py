"""Shared rendering for the aspool command-line help."""

from __future__ import annotations

from collections.abc import Sequence

import click

HelpRows = Sequence[tuple[str, str]]

_REFERENCES: dict[str, HelpRows] = {
    "aspool init": (("--root", "数据池目录；缺省为 aspool 默认数据目录"),),
    "aspool import": (
        ("--source", "完整历史数据源；当前为 free-stockdb"),
        ("--period", "daily（未复权日线）或 minutes（分钟线）；与 --factor 二选一"),
        ("--factor", "导入日线和分钟线共用的复权因子；与 --period 二选一"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool sync": (
        (
            "--type",
            "stock（默认）：股票；index：独立指数池；ex：跨市场扩展资产；etf：ETF 兼容入口",
        ),
        ("--category", "type=ex 时的扩展资产类别；当前已交付 ETF，其余类别按配置规划"),
        (
            "--source",
            "tdx：股票修补已导入标的，指数读取配置清单；free-stockdb：首次导入或完整历史校准后补齐尾部",
        ),
        ("--period", "daily 或 minutes；stock + source=tdx 要求该周期已有导入标的"),
        ("--source baostock", "串行补齐已有沪深股票的历史空缺；保留有效主源值，报告冲突"),
        ("--start / --end / --lookback", "股票日线字段补齐区间；YYYY-MM-DD，默认最近 30 个交易日"),
        ("--baostock / --no-baostock", "只读校对冲突样本；默认读取配置，不以补源覆盖冲突"),
        ("--enrich / --no-enrich", "默认补参考价、日期股本和量价指标，最后重算涨跌停及连板"),
        ("--enrich-only", "跳过主源 K 线同步，仅补字段和重算派生"),
        ("--tdx-mode", "仅 source=tdx 时有效；online 使用在线 K 线，offline 读取 vipdoc 日线"),
        ("--async", "仅在线 tdx 同步时使用异步客户端"),
        ("--workers", "日线独立连接数，默认4，范围1～8；同步/异步均支持"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool update": (
        ("--async / --workers", "异步报价及独立连接数；默认同步、4连接，--workers 1为串行"),
        ("--root", "目录不存在时自动初始化；但只更新已存在的日线标的，不导入历史"),
        ("数据范围", "更新当日报价和低频字段，再补齐沪深股票最近 30 个交易日的空缺"),
        ("--baostock / --no-baostock", "覆盖补齐开关；默认启用，通达信部分失败仍执行第二轮"),
        ("--lookback", "补齐交易日数；更早历史使用 sync --source baostock --start"),
        ("执行限制", "中国工作日 09:00 至 15:30（含）拒绝执行"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool fundamentals": (
        ("数据范围", "全市场低频基本面快照，不回填历史日线"),
        ("--async", "使用 tdxman 异步 quote 客户端"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool status": (("数据范围", "日线、分钟线、指数覆盖范围和最近一次同步状态"),),
    "aspool universe": (
        ("--type", "stock（默认）或 etf；刷新对应的证券目录"),
        ("数据范围", "当前证券目录、待初始化和非活跃代码"),
    ),
    "aspool query": (
        ("SYMBOL", "市场加代码，如 SZ000001、SH600519"),
        ("--period", "daily 或 minutes"),
        ("--start / --end", "daily 使用 YYYY-MM-DD；minutes 也可使用 YYYY-MM-DDTHH:MM:SS"),
        ("--format", "table、csv 或 json；缺省为 table"),
    ),
    "aspool ex categories": (("数据范围", "ETF、港股、美股和大宗期货等扩展资产类别及交付状态"),),
}

_EXAMPLES: dict[str, tuple[str, ...]] = {
    "aspool": ("aspool status", "aspool sync --source tdx --period daily"),
    "aspool init": ("aspool init",),
    "aspool import": ("aspool import --period daily", "aspool import --factor"),
    "aspool sync": (
        "aspool sync --source tdx --tdx-mode online --period daily",
        "aspool sync --type index --source tdx --period daily",
        "aspool sync --type ex --category ETF --source tdx --period daily",
        "aspool sync --source free-stockdb --period daily",
        "aspool sync --source baostock --start 2026-09-01 --end 2026-09-18",
        "aspool sync --enrich-only --start 2021-09-24 --end 2026-09-24",
    ),
    "aspool update": ("aspool update",),
    "aspool fundamentals": ("aspool fundamentals --limit 20",),
    "aspool status": ("aspool status",),
    "aspool query": ("aspool query SZ000001 --period daily --format table",),
    "aspool ex categories": ("aspool ex categories",),
}


def _write_reference(ctx: click.Context, formatter: click.HelpFormatter) -> None:
    rows = _REFERENCES.get(ctx.command_path)
    if rows:
        formatter.write_paragraph()
        with formatter.section("参数说明"):
            formatter.write_dl(rows)


def _write_examples(ctx: click.Context, formatter: click.HelpFormatter) -> None:
    examples = _EXAMPLES.get(ctx.command_path)
    if examples:
        formatter.write_paragraph()
        with formatter.section("示例"):
            for example in examples:
                formatter.write(f"  {example}\n")


class AspoolCommand(click.Command):
    """Leaf command format: Usage, Options, parameter reference, examples."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_options(ctx, formatter)
        _write_reference(ctx, formatter)
        _write_examples(ctx, formatter)


class AspoolGroup(click.Group):
    """Root command format aligned with the tdxman CLI group help."""

    _COMMAND_GROUPS = (
        ("初始化与导入", ("init", "import")),
        ("同步与维护", ("sync", "update", "fundamentals")),
        ("读取与检查", ("query", "status", "universe", "ex")),
    )

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        with formatter.section("Commands"):
            for index, (title, names) in enumerate(self._COMMAND_GROUPS):
                if index:
                    formatter.write_paragraph()
                formatter.write_text(title)
                rows: list[tuple[str, str]] = []
                for name in names:
                    command = self.get_command(ctx, name)
                    if command is not None and not command.hidden:
                        rows.append((name, command.get_short_help_str()))
                with formatter.indentation():
                    formatter.write_dl(rows)

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        formatter.write_paragraph()
        formatter.write_text("aspool -- A 股长期历史数据池维护工具。")
        self.format_commands(ctx, formatter)
        click.Command.format_options(self, ctx, formatter)
        _write_examples(ctx, formatter)


class AspoolExGroup(AspoolGroup):
    """Help layout for the cross-market asset namespace."""

    _COMMAND_GROUPS = (("扩展资产", ("categories",)),)
