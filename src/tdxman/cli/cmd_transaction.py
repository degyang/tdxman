"""逐笔成交命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("market")
@click.argument("code")
@click.option("--count", default=2000, type=int, help="请求数量")
@click.option("--start", default=0, type=int, help="起始偏移")
@click.option("--date", default=None, type=int, help="日期 YYYYMMDD（默认今天）")
@output_options
def transaction(
    market: str,
    code: str,
    count: int,
    start: int,
    date: int | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取逐笔成交数据。

    示例：

      tdxman transaction SZ 000001

      tdxman transaction SH 600519 --count 500 --format table

      tdxman transaction SZ 000001 --date 20250115
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_market

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_transactions(mkt, code, count=count, start=start, date=date)
    print_output(df, fmt, output_path, filename=code)
