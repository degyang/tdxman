"""CLI for detailed TQLEX company and financial pages."""

from __future__ import annotations

from pathlib import Path

import click

from ..f10 import F10_SECTIONS
from .help import StandardHelpCommand


@click.command("f10", cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@click.option(
    "--section",
    type=click.Choice(F10_SECTIONS),
    default="profile",
    show_default=True,
    help="资料栏目",
)
@click.option("--report-type", default="zcfzb", show_default=True, help="report 栏目的报表类型")
@click.option("--report-date", help="business 栏目的报告期；缺省使用最新可用报告期")
@click.option("--timeout", type=click.FloatRange(min=0.1), default=8.0, show_default=True)
@click.option(
    "-f",
    "--format",
    "output_fmt",
    type=click.Choice(["json", "table", "csv"]),
    default="json",
    show_default=True,
)
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path))
def f10(
    symbol_or_market: str,
    code: str | None,
    section: str,
    report_type: str,
    report_date: str | None,
    timeout: float,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取个股详细财务和公司资料（TDX 7615 F10）。"""
    from ..exceptions import TdxError
    from ..f10 import F10Client
    from ..models.enums import Market
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    if report_date is not None and section != "business":
        raise click.UsageError("--report-date 仅适用于 --section business")
    market, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    try:
        result = F10Client(timeout=timeout).query(
            Market(market),
            parsed_code,
            section,
            report_type=report_type,
            report_date=report_date,
        )
    except (TdxError, ValueError, OSError) as exc:
        raise click.ClickException(f"获取 F10 资料失败：{exc}") from exc
    frame = result.to_frame()
    if not frame.empty:
        frame.insert(0, "entry", result.entry)
        frame.insert(0, "symbol", f"{parsed_code}.{Market(market).name}")
    print_output(frame, output_fmt, output_path, filename=f"{parsed_code}-{section}")
