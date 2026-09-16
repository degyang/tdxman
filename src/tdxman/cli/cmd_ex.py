"""扩展市场命令（期货/港股/美股）。"""

from __future__ import annotations

from pathlib import Path

import click

from .output import output_options


@click.group()
def ex() -> None:
    """扩展市场命令（期货/港股/美股）。

    示例：

      tdxman ex kline HK_MAIN_BOARD 00700 --count 30

      tdxman ex quote US_STOCK AAPL

      tdxman ex markets
    """
    pass


@ex.command()
@click.argument("market")
@click.argument("code")
@click.option("--period", default="DAILY", help="K线周期: DAILY/5MIN/15MIN/30MIN/60MIN/1MIN")
@click.option("--count", default=800, type=int, help="K线数量")
@click.option("--start", default=0, type=int, help="起始偏移")
@click.option("--adjust", default="NONE", help="复权: NONE/QFQ/HFQ")
@output_options
def kline(
    market: str,
    code: str,
    period: str,
    count: int,
    start: int,
    adjust: str,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取扩展市场 K 线数据。

    MARKET: 扩展市场代码（如 HK_MAIN_BOARD, US_STOCK, SH_FUTURES）

    示例：

      tdxman ex kline HK_MAIN_BOARD 00700

      tdxman ex kline US_STOCK AAPL --count 30 --format table
    """
    from .conn import get_mac_ex_client
    from .output import print_output
    from .parsers import parse_adjust, parse_ex_market, parse_period

    fmt = output_fmt
    mkt = parse_ex_market(market)
    with get_mac_ex_client() as client:
        df = client.goods_kline(
            mkt,
            code,
            period=parse_period(period),
            start=start,
            count=count,
            adjust=parse_adjust(adjust),
        )
    print_output(df, fmt, output_path, filename=code)


@ex.command()
@click.argument("market")
@click.argument("code")
@output_options
def quote(
    market: str, code: str, output_fmt: str, output_path: Path | None
) -> None:
    """获取扩展市场报价。

    MARKET: 扩展市场代码

    示例：

      tdxman ex quote HK_MAIN_BOARD 00700

      tdxman ex quote US_STOCK AAPL --format table
    """
    from .conn import get_mac_ex_client
    from .output import print_output
    from .parsers import parse_ex_market

    fmt = output_fmt
    mkt = parse_ex_market(market)
    with get_mac_ex_client() as client:
        df = client.goods_quotes([(mkt, code)])
    print_output(df, fmt, output_path, filename=code)


@ex.command("quote-list")
@click.argument("market")
@click.option("--count", default=600, type=int, help="请求数量")
@click.option("--start", default=0, type=int, help="起始偏移")
@output_options
def quote_list(
    market: str,
    count: int,
    start: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取扩展市场商品列表。

    MARKET: 扩展市场代码（如 HK_MAIN_BOARD, US_STOCK, SH_FUTURES）

    示例：

      tdxman ex quote-list HK_MAIN_BOARD --format table

      tdxman ex quote-list SH_FUTURES --count 100
    """
    from .conn import get_mac_ex_client
    from .output import print_output
    from .parsers import parse_ex_market

    fmt = output_fmt
    mkt = parse_ex_market(market)
    with get_mac_ex_client() as client:
        df = client.goods_list(mkt, start=start, count=count)
    print_output(df, fmt, output_path, filename=market.lower())


@ex.command()
@click.argument("market")
@click.argument("code")
@click.option("--date", default=None, type=int, help="日期 YYYYMMDD（默认今天）")
@output_options
def tick(
    market: str,
    code: str,
    date: int | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取扩展市场分时数据。

    示例：

      tdxman ex tick HK_MAIN_BOARD 00700

      tdxman ex tick US_STOCK AAPL --format table
    """
    from .conn import get_mac_ex_client
    from .output import print_output
    from .parsers import parse_ex_market

    fmt = output_fmt
    mkt = parse_ex_market(market)
    with get_mac_ex_client() as client:
        df = client.goods_tick_chart(mkt, code, query_date=date)  # type: ignore[arg-type]
    print_output(df, fmt, output_path, filename=code)


@ex.command("markets")
@output_options
def markets(output_fmt: str, output_path: Path | None) -> None:
    """列出可用的扩展市场代码。"""
    import pandas as pd

    from ..mac.enums import ExMarket
    from .output import print_output

    rows: list[dict[str, int | str]] = []
    for m in ExMarket:
        rows.append({"code": m.value, "name": m.name})

    df = pd.DataFrame(rows)
    print_output(df, output_fmt, output_path, filename="markets")
