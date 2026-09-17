"""Shared CLI help rendering and command-specific reference material."""

from __future__ import annotations

from collections.abc import Sequence

import click

HelpRows = Sequence[tuple[str, str]]


_COMMAND_REFERENCES: dict[str, HelpRows] = {
    "tdxman auction": (("MARKET", "SH、SZ 或 BJ"), ("CODE", "六位证券代码")),
    "tdxman capital-flow": (("MARKET", "SH、SZ 或 BJ"), ("CODE", "六位证券代码")),
    "tdxman finance": (("MARKET", "SH、SZ 或 BJ"), ("CODE", "六位证券代码")),
    "tdxman fund-flow": (
        ("MARKET", "SH、SZ 或 BJ"),
        ("CODE", "六位证券代码；指数不支持历史资金流向"),
        ("--start", "距最新交易日的起始偏移，0 为最新记录"),
        ("--closed-only", "排除当天可能尚未完整的记录"),
    ),
    "tdxman kline": (
        ("MARKET", "SH、SZ 或 BJ"),
        ("CODE", "证券或标准指数代码"),
        ("--period", "DAILY、WEEKLY、MONTHLY、YEARLY、1MIN、5MIN、15MIN、30MIN、60MIN"),
        ("--adjust", "NONE、QFQ（前复权）或 HFQ（后复权）；指数仅支持 NONE"),
        ("--start", "距最新 K 线的起始偏移，0 为最新"),
    ),
    "tdxman markets": (("市场代码", "SH=上海、SZ=深圳、BJ=北京证券交易所"),),
    "tdxman offline": (
        ("MARKET", "SH、SZ 或 BJ"),
        ("CODE", "六位证券或指数代码"),
        ("--period", "DAILY、1MIN 或 5MIN"),
        ("--start", "距本地最新记录的起始偏移，0 为最新"),
        ("--vipdoc", "本地 vipdoc 目录；缺省读取 settings/config.yaml"),
    ),
    "tdxman ping": (("--timeout", "单个服务器测速超时秒数"),),
    "tdxman quote": (("STOCKS", '一个或多个标的，格式："SZ 000001,SH 600519"'),),
    "tdxman tick": (
        ("MARKET", "SH、SZ 或 BJ"),
        ("CODE", "六位证券或指数代码"),
        ("--date", "YYYYMMDD；缺省为今天"),
        ("--days", "1 或 5 个交易日"),
    ),
    "tdxman transaction": (
        ("MARKET", "SH、SZ 或 BJ"),
        ("CODE", "六位证券或指数代码"),
        ("--start", "距最新逐笔记录的起始偏移，0 为最新"),
        ("--date", "YYYYMMDD；缺省为今天"),
    ),
    "tdxman unusual": (("MARKET", "SH、SZ 或 BJ"), ("--start", "请求记录的起始偏移")),
    "tdxman market-stat": (("数据范围", "当前 A 股全市场涨跌统计快照，不提供历史 --count"),),
    "tdxman server-info": (("数据范围", "当前服务器配置的交易时段"),),
    "tdxman symbol-info": (("MARKET", "SH、SZ 或 BJ"), ("CODE", "六位证券代码")),
    "tdxman board-members": (
        ("BOARD_SYMBOL", "通达信板块代码或支持的标准指数代码，如 881001、000699"),
    ),
    "tdxman belong-board": (("MARKET", "SH、SZ 或 BJ"), ("CODE", "六位证券代码")),
    "tdxman ex kline": (
        ("MARKET", "扩展市场代码；用 tdxman ex markets 查看"),
        ("CODE", "该扩展市场中的证券或指数代码"),
        ("--period", "DAILY、1MIN、5MIN、15MIN、30MIN 或 60MIN"),
        ("--adjust", "NONE、QFQ 或 HFQ"),
    ),
    "tdxman ex quote": (
        ("MARKET", "扩展市场代码；用 tdxman ex markets 查看"),
        ("CODE", "商品代码"),
    ),
    "tdxman ex quote-list": (
        ("MARKET", "扩展市场代码；用 tdxman ex markets 查看"),
        ("--start", "请求列表的起始偏移"),
    ),
    "tdxman ex tick": (
        ("MARKET", "扩展市场代码；用 tdxman ex markets 查看"),
        ("CODE", "商品代码"),
        ("--date", "YYYYMMDD；缺省为今天"),
    ),
    "tdxman ex markets": (("数据范围", "全部可用扩展市场名称和数值代码"),),
}

_COMMAND_EXAMPLES: dict[str, tuple[str, ...]] = {
    "tdxman auction": ("tdxman auction SZ 000001 --format table",),
    "tdxman capital-flow": ("tdxman capital-flow SH 600519 --format table",),
    "tdxman finance": ("tdxman finance SH 600519 --format table",),
    "tdxman fund-flow": ("tdxman fund-flow SH 600519 --count 20 --closed-only --format table",),
    "tdxman kline": (
        "tdxman kline SH 600519 --period DAILY --count 30 --format table",
        "tdxman kline SH 000300 --period DAILY --count 120 --format table",
    ),
    "tdxman markets": ("tdxman markets --format table",),
    "tdxman offline": ("tdxman offline SZ 000001 --period DAILY --count 30 --format table",),
    "tdxman ping": ("tdxman ping --timeout 3 --format table",),
    "tdxman quote": ('tdxman quote "SZ 000001,SH 600519" --format table',),
    "tdxman tick": ("tdxman tick SH 600519 --days 5 --format table",),
    "tdxman transaction": ("tdxman transaction SH 600519 --count 100 --format table",),
    "tdxman unusual": ("tdxman unusual SH --count 100 --format table",),
    "tdxman market-stat": ("tdxman market-stat --format table",),
    "tdxman server-info": ("tdxman server-info --format table",),
    "tdxman symbol-info": ("tdxman symbol-info SH 600519 --format table",),
    "tdxman board-members": (
        "tdxman board-members 881001 --format table",
        "tdxman board-members 000699 --count 20 --format table",
    ),
    "tdxman belong-board": ("tdxman belong-board SZ 000001 --format table",),
    "tdxman version": ("tdxman version",),
    "tdxman ex": (
        "tdxman ex markets --format table",
        "tdxman ex kline HK_MAIN_BOARD 00700 --count 30 --format table",
    ),
    "tdxman ex kline": ("tdxman ex kline HK_MAIN_BOARD 00700 --count 30 --format table",),
    "tdxman ex quote": ("tdxman ex quote US_STOCK AAPL --format table",),
    "tdxman ex quote-list": ("tdxman ex quote-list CSI_INDEX --count 600 --format table",),
    "tdxman ex tick": ("tdxman ex tick HK_MAIN_BOARD 00700 --format table",),
    "tdxman ex markets": ("tdxman ex markets --format table",),
}


def _write_reference(ctx: click.Context, formatter: click.HelpFormatter) -> None:
    rows = _COMMAND_REFERENCES.get(ctx.command_path)
    if rows:
        formatter.write_paragraph()
        with formatter.section("参数说明"):
            formatter.write_dl(rows)


def _write_examples(ctx: click.Context, formatter: click.HelpFormatter) -> None:
    examples = _COMMAND_EXAMPLES.get(ctx.command_path)
    if examples:
        formatter.write_paragraph()
        with formatter.section("示例"):
            for example in examples:
                formatter.write(f"  {example}\n")


class StandardHelpCommand(click.Command):
    """Leaf command format: Usage, Options, parameter reference, examples."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_options(ctx, formatter)
        _write_reference(ctx, formatter)
        _write_examples(ctx, formatter)


class StandardHelpGroup(click.Group):
    """Command-group format matching the root CLI command style."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        formatter.write_paragraph()
        formatter.write_text("扩展市场行情命令。")
        self.format_commands(ctx, formatter)
        click.Command.format_options(self, ctx, formatter)
        _write_examples(ctx, formatter)
