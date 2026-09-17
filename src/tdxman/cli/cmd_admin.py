"""管理命令：ping, version。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.option("--timeout", default=5.0, help="测速超时（秒）")
@output_options
def ping(timeout: float, output_fmt: str, output_path: Path | None) -> None:
    """测量通达信服务器延迟。

    示例：

      tdxman ping

      tdxman ping --timeout 3 --format table
    """
    import pandas as pd

    from ..transport.sync import ping_all, ping_mac_all
    from .output import print_output

    fmt = output_fmt

    click.echo("正在测速标准服务器...", err=True)
    std_results = ping_all(timeout=timeout)
    click.echo("正在测速MAC服务器...", err=True)
    mac_results = ping_mac_all(timeout=timeout)

    rows: list[dict[str, str | float]] = []
    for host, latency in std_results:
        rows.append({"group": "standard", "host": host, "latency_ms": round(latency * 1000, 1)})
    for host, latency in mac_results:
        rows.append({"group": "mac", "host": host, "latency_ms": round(latency * 1000, 1)})

    df = pd.DataFrame(rows)
    print_output(df, fmt, output_path)


@click.command(cls=StandardHelpCommand)
def version() -> None:
    """显示版本号。"""
    click.echo("tdxman 1.1.0")
