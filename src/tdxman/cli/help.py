"""Shared CLI help rendering and command-specific reference material."""

from __future__ import annotations

from collections.abc import Sequence

import click

HelpRows = Sequence[tuple[str, str]]


_COMMAND_REFERENCES: dict[str, HelpRows] = {
    "tdxman abnormal-list": (
        ("MARKET / --scope", "默认 ALL/all；SH/SZ/BJ 与板别范围同 stock-codes"),
        ("--type", "all=ST/*ST 与停牌并集；st=只查 ST；suspended=只查停牌"),
        ("--code / --name", "代码可重复或逗号分隔；名称可重复且精确匹配"),
        ("状态口径", "ST 按简称前缀；停牌按标准报价 0x053e 状态位 0x20"),
        ("checked_at", "本次检查时间；不是行情交易日或异常起始日"),
    ),
    "tdxman auction": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码"),
    ),
    "tdxman capital-flow": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码"),
    ),
    "tdxman finance": (("MARKET", "SH、SZ 或 BJ"), ("CODE", "六位证券代码")),
    "tdxman xdxr": (
        ("SYMBOL_OR_MARKET", '规范标识 "600519.SH"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码"),
        ("数据范围", "通达信返回的全部除权除息和股本变动事件；不是逐日复权因子"),
    ),
    "tdxman f10": (
        ("SYMBOL_OR_MARKET", '规范标识 "600519.SH"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码"),
        (
            "--section",
            "profile、report、business、dividends、shareholders、topics、"
            "announcements、news、governance、forecast",
        ),
        ("--report-type", "report 栏目的报表类型；默认 zcfzb（资产负债表）"),
        ("--report-date", "business 栏目的报告期；不传则读取最新可用报告期"),
    ),
    "tdxman fund-flow": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码；指数不支持历史资金流向"),
        ("--start", "距最新交易日的起始偏移，0 为最新记录"),
        ("--closed-only", "排除当天可能尚未完整的记录"),
    ),
    "tdxman kline": (
        ("SYMBOL_OR_MARKET", '规范标识 "600519.SH"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传证券或标准指数代码"),
        ("--period", "DAILY、WEEKLY、MONTHLY、YEARLY、1MIN、5MIN、15MIN、30MIN、60MIN"),
        ("--adjust", "NONE、QFQ（前复权）或 HFQ（后复权）；指数仅支持 NONE"),
        ("--start", "距最新 K 线的起始偏移，0 为最新"),
        ("--source", "tdx（默认）或 baostock；baostock 只支持沪深股票 DAILY、NONE"),
    ),
    "tdxman stock-codes": (
        ("MARKET", "ALL（默认）、SH、SZ 或 BJ；获取当前股票清单，含 CDR，不入库"),
        ("--scope", "all/main/star/chinext/bse/cdr；主板、科创板、创业板、北交所或 CDR"),
        ("--code / --name", "代码、名称筛选取交集；同类多值取并集，名称精确匹配"),
        ("来源", "沪深标准证券目录；北交所 MAC 分类目录；不提供历史证券全集"),
        ("--output", "指定目录时文件名为 stock-codes，按输出格式添加扩展名"),
    ),
    "tdxman markets": (("市场代码", "SH=上海、SZ=深圳、BJ=北京证券交易所"),),
    "tdxman offline": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券或指数代码"),
        ("--period", "DAILY、1MIN 或 5MIN"),
        ("--start", "距本地最新记录的起始偏移，0 为最新"),
        ("--vipdoc", "本地 vipdoc 目录；缺省读取 settings/config.yaml"),
    ),
    "tdxman ping": (("--timeout", "单个服务器测速超时秒数"),),
    "tdxman quote": (
        ("STOCKS", '一个或多个标的，格式："000001.SZ,600519.SH"；兼容旧格式 "SZ 000001"'),
        ("--source", "tdx 为实时报价；baostock 为沪深股票历史未复权日线，含 ST 和交易状态"),
        ("--count", "仅 baostock 使用；每只股票默认最近 30 条，含明确标记的停牌日"),
        ("--start-date / --end-date", "仅 baostock 使用；YYYY-MM-DD，返回区间内最近 count 条"),
    ),
    "tdxman tick": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券或指数代码"),
        ("--date", "YYYYMMDD；缺省为今天"),
        ("--days", "1 或 5 个交易日"),
    ),
    "tdxman transaction": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券或指数代码"),
        ("--start", "距最新逐笔记录的起始偏移，0 为最新"),
        ("--date", "YYYYMMDD；缺省为今天"),
    ),
    "tdxman workdays": (
        ("--date", "查询单个自然日，返回交易状态及前后已观察到的交易日；默认今天"),
        ("--start / --end", "查询日期范围内逐日交易状态，必须同时提供"),
        ("--max-bars", "最多读取的上证指数日 K 根数；超出覆盖范围不推断"),
    ),
    "tdxman unusual": (("MARKET", "SH、SZ 或 BJ"), ("--start", "请求记录的起始偏移")),
    "tdxman market-stat": (("数据范围", "当前 A 股全市场涨跌统计快照，不提供历史 --count"),),
    "tdxman server-info": (("数据范围", "当前服务器配置的交易时段"),),
    "tdxman symbol-info": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码"),
        ("--source", "baostock 返回沪深股票上市日、代码退出日及当前名称；默认 tdx"),
    ),
    "tdxman board-members": (
        ("BOARD_SYMBOL", "通达信板块代码或支持的标准指数代码，如 881001、000699"),
    ),
    "tdxman belong-board": (
        ("SYMBOL_OR_MARKET", '规范标识 "000001.SZ"，或旧格式的市场参数 SH/SZ/BJ'),
        ("CODE", "可选；使用旧格式时传六位证券代码"),
    ),
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
    "tdxman abnormal-list": (
        "tdxman abnormal-list SH --format table",
        "tdxman abnormal-list --type st --format table",
        "tdxman abnormal-list --type suspended --format json",
    ),
    "tdxman stock-codes": (
        "tdxman stock-codes SH --format table --fields all",
        "tdxman stock-codes --scope bse --format json",
        "tdxman stock-codes SZ --code 000001.SZ --name 平安银行",
    ),
    "tdxman auction": ("tdxman auction 000001.SZ --format table",),
    "tdxman capital-flow": ("tdxman capital-flow 600519.SH --format table",),
    "tdxman finance": ("tdxman finance SH 600519 --format table",),
    "tdxman xdxr": ("tdxman xdxr 600519.SH --format table",),
    "tdxman f10": (
        "tdxman f10 600519.SH --section profile --format table",
        "tdxman f10 600519.SH --section report --report-type zcfzb --format json",
    ),
    "tdxman fund-flow": ("tdxman fund-flow SH 600519 --count 20 --closed-only --format table",),
    "tdxman kline": (
        "tdxman kline SH 600519 --period DAILY --count 30 --format table",
        "tdxman kline SH 000300 --period DAILY --count 120 --format table",
    ),
    "tdxman markets": ("tdxman markets --format table",),
    "tdxman offline": ("tdxman offline SZ 000001 --period DAILY --count 30 --format table",),
    "tdxman ping": ("tdxman ping --timeout 3 --format table",),
    "tdxman quote": (
        'tdxman quote "SZ 000001,SH 600519" --format table',
        "tdxman quote 600519.SH --count 30 --source baostock --format table",
    ),
    "tdxman tick": ("tdxman tick SH 600519 --days 5 --format table",),
    "tdxman transaction": ("tdxman transaction SH 600519 --count 100 --format table",),
    "tdxman workdays": (
        "tdxman workdays --date 2026-09-29 --format table",
        "tdxman workdays --start 2026-09-01 --end 2026-09-30 --format csv",
    ),
    "tdxman unusual": ("tdxman unusual SH --count 100 --format table",),
    "tdxman market-stat": ("tdxman market-stat --format table",),
    "tdxman server-info": ("tdxman server-info --format table",),
    "tdxman symbol-info": (
        "tdxman symbol-info SH 600519 --format table",
        "tdxman symbol-info 601091.SH --source baostock --format json",
    ),
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
