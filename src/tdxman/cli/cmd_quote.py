"""报价命令：quote（单/批量）, quote-list（按分类排序）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("stocks")
@click.option("--source", type=click.Choice(["tdx", "baostock"]), default="tdx", show_default=True)
@click.option("--count", type=click.IntRange(min=1), default=None,
              help="baostock 每只股票的历史日线数量，默认 30；tdx 为实时快照。")
@click.option("--start-date", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="baostock 历史区间起点，YYYY-MM-DD。")
@click.option("--end-date", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="baostock 历史区间终点，YYYY-MM-DD。")
@output_options
def quote(
    stocks: str, output_fmt: str, output_path: Path | None, source: str = "tdx",
    count: int | None = None, start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> None:
    """获取实时报价或 BaoStock 历史日线（支持多只）。

    STOCKS 格式: "000001.SZ,600519.SH"

    示例：

      tdxman quote "000001.SZ"

      tdxman quote "000001.SZ,600519.SH" --format table

    旧格式 "SZ 000001,SH 600519" 仍受支持。
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_stocks

    fmt = output_fmt
    stock_list = parse_stocks(stocks)
    if source == "baostock":
        import pandas as pd

        from ..baostock import BaostockClient
        from ..exceptions import TdxError

        try:
            for market, code in stock_list:
                BaostockClient.security_code(market, code)
            with BaostockClient() as client:
                frames = [client.get_daily(
                    market, code, count=count or 30,
                    start=start_date.date() if start_date else None,
                    end=end_date.date() if end_date else None,
                ) for market, code in stock_list]
        except (TdxError, ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        print_output(pd.concat(frames, ignore_index=True), fmt, output_path,
                     split_rows=len(stock_list) > 1)
        return
    if count is not None or start_date is not None or end_date is not None:
        raise click.UsageError("历史 --count / --start-date / --end-date 需要 --source baostock")
    with get_mac_client() as client:
        df = client.get_stock_quotes(stock_list)
    print_output(df, fmt, output_path, split_rows=len(stock_list) > 1)


class QuoteListCommand(click.Command):
    """Render quote-list help with the category reference before examples."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_options(ctx, formatter)
        formatter.write_paragraph()
        with formatter.section("CATEGORY 类型对照"):
            formatter.write_dl(
                [
                    ("SH / SZ / A", "上证 A 股 / 深证 A 股 / 全部 A 股"),
                    ("B", "B 股"),
                    ("KCB / CYB / BJ", "科创板 / 创业板 / 北交所"),
                    ("ETF / LOF", "交易型开放式基金 / 上市型开放式基金"),
                    ("HGT / SGT", "沪股通标的 / 深股通标的"),
                    ("FXJS", "风险警示证券"),
                    ("ZS", "沪深系列指数目录与实时报价"),
                ]
            )
        formatter.write_paragraph()
        with formatter.section("示例"):
            formatter.write("  tdxman quote-list A --count 20 --format table\n")
            formatter.write(
                "  tdxman quote-list KCB --sort TOTAL_AMOUNT --order ASC --format table\n"
            )
            formatter.write("  tdxman quote-list ZS --count 600 --format table\n")


@click.command("quote-list", cls=QuoteListCommand)
@click.argument("category", default="A")
@click.option("--count", default=80, type=int, help="请求数量")
@click.option(
    "--sort",
    "sort_field",
    default="CHANGE_PCT",
    help="排序字段: CHANGE_PCT/CODE/PRICE/VOLUME/TOTAL_AMOUNT/TURNOVER_RATE",
)
@click.option("--order", "sort_order", default="DESC", help="排序方向: DESC/ASC")
@output_options
def quote_list(
    category: str,
    count: int,
    sort_field: str,
    sort_order: str,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取市场分类报价列表（按涨幅等排序）。"""
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_category, parse_sort_order, parse_sort_type

    fmt = output_fmt
    cat = parse_category(category)
    st = parse_sort_type(sort_field)
    so = parse_sort_order(sort_order)
    with get_mac_client() as client:
        df = client.get_stock_quotes_list(
            category=cat,
            count=count,
            sort_type=st,
            sort_order=so,
        )
    print_output(df, fmt, output_path, filename=category.lower())
