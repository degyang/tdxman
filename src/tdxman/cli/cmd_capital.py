"""资金流向命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command("capital-flow", cls=StandardHelpCommand)
@click.argument("market")
@click.argument("code")
@output_options
def capital_flow(market: str, code: str, output_fmt: str, output_path: Path | None) -> None:
    """获取当日资金流向快照。

    示例：

      tdxman capital-flow SZ 000001

      tdxman capital-flow SH 600519 --format table
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_market

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_capital_flow(mkt, code)
    print_output(df, fmt, output_path, filename=code)
