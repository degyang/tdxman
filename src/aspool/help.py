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
            "stock（默认）、index、etf 或 all；all 按数据块顺序补齐完整运行数据",
        ),
        ("--category", "type=ex 时的扩展资产类别；当前已交付 ETF，其余类别按配置规划"),
        (
            "--source",
            "tdx：股票修补已导入标的，指数读取配置清单；free-stockdb：首次导入或完整历史校准后补齐尾部",
        ),
        ("--period", "daily 或 minutes；stock + source=tdx 要求该周期已有导入标的"),
        ("--source baostock", "串行补齐已有沪深股票的历史空缺；保留有效主源值，报告冲突"),
        ("--start / --end", "明确补齐窗口；省略时按各库末端和股票最近十个交易日重叠补齐"),
        ("--count", "省略明确窗口时检查最近 N 个已完成交易日；默认10，与日期范围互斥"),
        ("--status", "missing 或 invalid；只修复相应逐股状态"),
        ("--max-consecutive-failures", "单股重试耗尽后连续失败熔断，默认3"),
        ("--baostock / --no-baostock", "只读校对冲突样本；默认读取配置，不以补源覆盖冲突"),
        ("--enrich / --no-enrich", "默认补参考价、日期股本和量价指标，最后重算涨跌停及连板"),
        ("--enrich-only", "跳过主源 K 线同步，仅补字段和重算派生"),
        ("--tdx-mode", "仅 source=tdx 时有效；online 使用在线 K 线，offline 读取 vipdoc 日线"),
        ("--async", "仅在线 tdx 同步时使用异步客户端"),
        ("--workers", "日线独立连接数，默认4，范围1～8；同步/异步均支持"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool update": (
        ("--type", "stock（默认）、index、etf 或 all"),
        ("--source", "默认 tdx 收盘报价；baostock 显式选择备用源，不自动切换"),
        ("--workers", "报价独立连接数，默认4；数据库串行提交，BaoStock串行读取"),
        ("--root", "默认当前目录下 data；要求已有新版 stocks.sqlite"),
        ("数据范围", "报价直接保存量比/换手率；原子更新日线、因子、涨跌停及市场汇总"),
        ("--start / --end", "update 仅 baostock 可指定有限日期窗口；tdx 历史 K 线使用 sync"),
        ("--retries / --retry-delay", "读取默认重试2次、退避1/2秒；重连，失败保留旧数据"),
        ("--max-consecutive-failures", "连续失败熔断阈值，默认3"),
        ("--symbol", "指定完整证券代码，可重复；缺省处理库内已有股票"),
        ("执行限制", "中国工作日 09:00 至 15:30（含）拒绝执行"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool fundamentals": (
        ("ACTION", "update（默认）按来源报告日期追加，或 status 只读检查"),
        ("数据范围", "财务报告和股东人数历史，不回填历史日线"),
        ("--symbol", "仅更新指定规范证券代码，可重复"),
        ("--limit", "只处理前 N 个标的，用于小批量验证"),
    ),
    "aspool platform": (
        (
            "ACTION",
            "prepare 创建影子库；verify/status 检查；activate 切换；rollback 回到布局1",
        ),
        ("切换规则", "prepare 不改变 layout_version，不切换 Fundwise 公开读取"),
    ),
    "aspool status": (
        ("--dataset", "检查全部或指定生产数据块；不触发写入"),
        ("--format", "table 或 json"),
    ),
    "aspool directory": (
        ("--type", "stock（默认）或 etf；刷新对应的证券目录"),
        ("数据范围", "当前证券目录、待初始化和非活跃代码"),
    ),
    "aspool query": (
        ("SYMBOL", "行情必填；其余数据集可用日期、状态和行数限制有界读取"),
        (
            "--dataset",
            "securities、calendar、fundamentals、stock-bars、corporate-actions、"
            "shareholder-counts、stock-features、limit-events、market-summary、index-bars、"
            "etf-bars 或 etf-factors",
        ),
        ("--period", "daily 或 minutes；仅 stock 支持 minutes"),
        ("--frequency", "market-summary 使用 D、W 或 M；当前生产池已发布 D"),
        ("--start / --end", "daily 使用 YYYY-MM-DD；minutes 也可使用 YYYY-MM-DDTHH:MM:SS"),
        ("--format", "table、csv 或 json；缺省为 table"),
        ("--status", "逐股事实可筛选 traded、no-trade、missing 或 invalid"),
        ("--limit", "最大返回行数，默认1000"),
    ),
    "aspool contract": (
        ("数据范围", "生产数据块的输入、物理存储、写入 CLI、输出和读取入口"),
        ("--format", "table（默认）或 json；JSON 可供自动检查"),
    ),
    "aspool ex categories": (("数据范围", "ETF、港股、美股和大宗期货等扩展资产类别及交付状态"),),
}

_EXAMPLES: dict[str, tuple[str, ...]] = {
    "aspool": ("aspool status", "aspool sync --type all --source tdx"),
    "aspool init": ("aspool init",),
    "aspool import": ("aspool import --period daily", "aspool import --factor"),
    "aspool sync": (
        "aspool sync --source tdx --tdx-mode online --period daily",
        "aspool sync --type all --source tdx",
        "aspool sync --type index --source tdx --period daily",
        "aspool sync --type ex --category ETF --source tdx --period daily",
        "aspool sync --source free-stockdb --period daily",
        "aspool sync --type stock --source baostock --status missing",
        "aspool sync --enrich-only --start 2021-09-24 --end 2026-09-24",
    ),
    "aspool update": ("aspool update --type all --root data", "aspool update --root data"),
    "aspool fundamentals": (
        "aspool fundamentals update --limit 20",
        "aspool fundamentals status",
    ),
    "aspool platform": (
        "aspool platform prepare --root data",
        "aspool platform verify --root data",
    ),
    "aspool status": ("aspool status",),
    "aspool query": (
        "aspool query 000001.SZ --dataset stock-bars --format table",
        "aspool query --dataset stock-features --status missing --limit 100",
        "aspool query 510300.SH --dataset etf-bars --format csv",
    ),
    "aspool contract": ("aspool contract", "aspool contract --format json"),
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
        ("同步与维护", ("sync", "update", "fundamentals", "platform")),
        ("读取与检查", ("query", "status", "contract", "directory", "ex")),
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
