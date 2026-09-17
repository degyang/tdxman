"""K 线命令。"""

from __future__ import annotations

from pathlib import Path

import click

from ..mac.enums import Adjust, Period
from ..models.enums import KlineCategory, Market
from .help import StandardHelpCommand
from .output import output_options


def _is_standard_index(market: int, code: str) -> bool:
    return (market == Market.SH and code.startswith(("000", "880", "999"))) or (
        market == Market.SZ and code.startswith("399")
    )


def _index_category(period: Period) -> KlineCategory:
    categories = {
        Period.MIN_1: KlineCategory.MIN_1,
        Period.MIN_5: KlineCategory.MIN_5,
        Period.MIN_15: KlineCategory.MIN_15,
        Period.MIN_30: KlineCategory.MIN_30,
        Period.MIN_60: KlineCategory.MIN_60,
        Period.DAILY: KlineCategory.DAY,
        Period.WEEKLY: KlineCategory.WEEK,
        Period.MONTHLY: KlineCategory.MONTH,
        Period.YEARLY: KlineCategory.YEAR,
    }
    try:
        return categories[period]
    except KeyError as exc:
        raise click.UsageError(f"指数不支持的 K 线周期: {period.name}") from exc


@click.command(cls=StandardHelpCommand)
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
    from .conn import get_mac_client, get_tdx_client
    from .output import print_output
    from .parsers import parse_adjust, parse_market, parse_period

    fmt = output_fmt
    mkt = parse_market(market)
    parsed_period = parse_period(period)
    parsed_adjust = parse_adjust(adjust)
    if _is_standard_index(mkt, code):
        if parsed_adjust != Adjust.NONE:
            raise click.UsageError("指数 K 线不支持复权")
        with get_tdx_client() as client:
            df = client.get_index_bars(
                Market(mkt), code, _index_category(parsed_period), start=start, count=count
            )
    else:
        with get_mac_client() as client:
            df = client.get_stock_kline(
                mkt,
                code,
                period=parsed_period,
                start=start,
                count=count,
                adjust=parsed_adjust,
            )
    print_output(df, fmt, output_path, filename=code)
