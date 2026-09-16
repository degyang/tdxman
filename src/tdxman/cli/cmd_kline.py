"""K 线命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .output import output_options


@click.command()
@click.argument("market")
@click.argument("code")
@click.option(
    "--period", default="DAILY", help="K线周期: DAILY/5MIN/15MIN/30MIN/60MIN/1MIN/WEEKLY/MONTHLY"
)
@click.option("--count", default=800, type=int, help="K线数量")
@click.option("--start", default=0, type=int, help="起始偏移（0=最新）")
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
    """获取 K 线数据。

    示例：

      tdxman kline SZ 000001

      tdxman kline SH 600519 --adjust QFQ --count 30

      tdxman kline SZ 000001 --period 5MIN --format table
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_adjust, parse_market, parse_period

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_stock_kline(
            mkt,
            code,
            period=parse_period(period),
            start=start,
            count=count,
            adjust=parse_adjust(adjust),
        )
    print_output(df, fmt, output_path, filename=code)
