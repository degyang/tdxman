"""报价命令：quote（单/批量）, quote-list（按分类排序）。"""

from __future__ import annotations

from pathlib import Path

import click

from .output import output_options


@click.command()
@click.argument("stocks")
@output_options
def quote(stocks: str, output_fmt: str, output_path: Path | None) -> None:
    """获取实时报价（支持多只）。

    STOCKS 格式: "SZ 000001,SH 600519"

    示例：

      tdxman quote "SZ 000001"

      tdxman quote "SZ 000001,SH 600519" --format table
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_stocks

    fmt = output_fmt
    stock_list = parse_stocks(stocks)
    with get_mac_client() as client:
        df = client.get_stock_quotes(stock_list)
    print_output(df, fmt, output_path, split_rows=len(stock_list) > 1)


@click.command("quote-list")
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
    """获取市场分类报价列表（按涨幅等排序）。

    CATEGORY: SH/SZ/A/B/KCB/BJ/CYB/ETF/LOF/HGT/SGT 等

    示例：

      tdxman quote-list A --count 20 --format table

      tdxman quote-list KCB --sort TOTAL_AMOUNT --order ASC

      tdxman quote-list CYB --count 50
    """
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
