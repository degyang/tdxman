"""财务数据命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command("finance", cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@output_options
def finance(
    symbol_or_market: str,
    code: str | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取最新财务摘要。

    示例（新格式）：

      tdxman finance 000001.SZ

    示例（旧格式兼容）：

      tdxman finance SZ 000001
    """
    from ..exceptions import TdxError
    from .conn import get_tdx_client
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    fmt = output_fmt
    mkt, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    try:
        with get_tdx_client() as client:
            df = client.get_finance_info(mkt, parsed_code)
    except TdxError as exc:
        raise click.ClickException(f"获取财务摘要失败：{exc}") from exc
    print_output(df, fmt, output_path, filename=parsed_code)


@click.command("fund-flow", cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@click.option("--start", default=0, type=int, help="距最新记录的起始偏移")
@click.option("--count", default=30, type=int, help="请求数量")
@click.option("--closed-only", is_flag=True, help="排除当天可能尚未完整的数据")
@output_options
def fund_flow(
    symbol_or_market: str,
    code: str | None,
    start: int,
    count: int,
    closed_only: bool,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取按交易日统计的历史资金流向。

    示例（新格式）：

      tdxman fund-flow 000001.SZ

    示例（旧格式兼容）：

      tdxman fund-flow SZ 000001
    """
    from ..exceptions import TdxError
    from .conn import get_tdx_client
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    fmt = output_fmt
    mkt, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    try:
        with get_tdx_client() as client:
            df = client.get_history_fund_flow(mkt, parsed_code, start, count)
    except TdxError as exc:
        raise click.ClickException(f"获取历史资金流向失败：{exc}") from exc
    if closed_only and "date" in df.columns:
        import pandas as pd

        df = df[pd.to_datetime(df["date"]).dt.date < pd.Timestamp.today().date()]
    print_output(df, fmt, output_path, filename=parsed_code)
