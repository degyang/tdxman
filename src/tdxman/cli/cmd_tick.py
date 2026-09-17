"""分时图命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("market")
@click.argument("code")
@click.option("--date", default=None, type=int, help="日期 YYYYMMDD（默认今天）")
@click.option("--days", default=1, type=click.Choice([1, 5]), help="交易日数量：1 或 5")
@output_options
def tick(
    market: str,
    code: str,
    date: int | None,
    days: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取分时图数据。

    示例：

      tdxman tick SZ 000001

      tdxman tick SH 600519 --days 5 --format table

      tdxman tick SZ 000001 --date 20250115
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_market

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        if days > 1:
            df = client.get_tick_charts(mkt, code, date=date, days=days)
        else:
            df = client.get_tick_chart(mkt, code, date=date)
    print_output(df, fmt, output_path, filename=code)
