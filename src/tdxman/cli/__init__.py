"""tdxman CLI -- Agent 友好的通达信行情命令行工具。"""

from __future__ import annotations

import click

from .cmd_admin import ping, version
from .cmd_auction import auction
from .cmd_board import belong_board, board_list, board_members
from .cmd_capital import capital_flow
from .cmd_ex import ex
from .cmd_finance import finance, fund_flow
from .cmd_info import markets, server_info, symbol_info
from .cmd_kline import kline
from .cmd_monitor import market_stat, unusual
from .cmd_offline import offline
from .cmd_quote import quote, quote_list
from .cmd_tick import tick
from .cmd_transaction import transaction


class TdxmanGroup(click.Group):
    """Render root help in the order used by the command-line manual."""

    _COMMAND_GROUPS = (
        (
            "实时行情",
            (
                "auction",
                "capital-flow",
                "market-stat",
                "quote",
                "quote-list",
                "symbol-info",
                "tick",
                "transaction",
                "unusual",
            ),
        ),
        ("历史与财务", ("finance", "fund-flow", "kline", "offline")),
        (
            "市场资料与板块",
            ("belong-board", "board-list", "board-members", "server-info"),
        ),
        ("扩展市场", ("ex",)),
        ("工具", ("markets", "ping", "version")),
    )

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Render commands by data freshness and operational purpose."""
        with formatter.section("Commands"):
            for index, (title, names) in enumerate(self._COMMAND_GROUPS):
                if index:
                    formatter.write_paragraph()
                formatter.write_text(title)
                rows: list[tuple[str, str]] = []
                for name in names:
                    command = self.get_command(ctx, name)
                    if command is not None and not command.hidden:
                        rows.append((name, command.get_short_help_str()))
                with formatter.indentation():
                    formatter.write_dl(rows)

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        formatter.write_paragraph()
        formatter.write_text("tdxman -- 通达信行情数据命令行工具。")
        self.format_commands(ctx, formatter)
        click.Command.format_options(self, ctx, formatter)
        formatter.write_paragraph()
        formatter.write_text(
            "所有数据命令默认将 JSON 输出到标准输出。使用 --format 选择 json、table 或 csv；"
            "使用 --output 写入文件或目录。"
        )
        formatter.write_paragraph()
        formatter.write("示例：\n")
        with formatter.indentation():
            formatter.write('tdxman quote "SZ 000001,SH 600519"\n')
            formatter.write("tdxman kline SH 600519 --format table\n")
            formatter.write("tdxman finance SZ 006324 --format json --output data/006324.json\n")


@click.group(cls=TdxmanGroup)
@click.version_option(version="1.1.0", prog_name="tdxman")
def cli() -> None:
    """通达信行情数据命令行工具。"""
    pass


cli.add_command(ping)
cli.add_command(version)
cli.add_command(kline)
cli.add_command(quote)
cli.add_command(quote_list)
cli.add_command(tick)
cli.add_command(transaction)
cli.add_command(auction)
cli.add_command(board_list)
cli.add_command(board_members)
cli.add_command(belong_board)
cli.add_command(capital_flow)
cli.add_command(unusual)
cli.add_command(market_stat)
cli.add_command(server_info)
cli.add_command(symbol_info)
cli.add_command(markets)
cli.add_command(finance)
cli.add_command(fund_flow)
cli.add_command(offline)
cli.add_command(ex)
