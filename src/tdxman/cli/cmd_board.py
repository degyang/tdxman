"""板块命令：board-list, board-members, belong-board。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


class BoardListCommand(click.Command):
    """Render board-list help with the type reference before examples."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_options(ctx, formatter)
        formatter.write_paragraph()
        with formatter.section("--type 类型对照"):
            formatter.write_dl(
                [
                    ("ALL", "全部板块"),
                    ("HY / HY2", "一级行业 / 二级行业"),
                    ("GN / FG / DQ", "概念 / 风格 / 地域板块"),
                    ("OTHER（--type 6）", "其他板块"),
                    ("YJ_LEVEL1/2/3（--type 7/8/9）", "业绩一级 / 二级 / 三级板块"),
                    ("ZS", "沪深标准指数目录"),
                ]
            )
        formatter.write_paragraph()
        with formatter.section("示例"):
            formatter.write("  tdxman board-list --format table\n")
            formatter.write("  tdxman board-list --type GN --count 200 --format table\n")
            formatter.write("  tdxman board-list --type ZS --count 600 --format table\n")


@click.command("board-list", cls=BoardListCommand)
@click.option(
    "--type",
    "board_type",
    default="ALL",
    help="板块类型（见下方对照表）",
)
@click.option("--count", default=10000, type=int, help="请求数量")
@output_options
def board_list(
    board_type: str,
    count: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取板块或沪深标准指数目录。"""
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_board_type

    fmt = output_fmt
    with get_mac_client() as client:
        if board_type.upper() == "ZS":
            from ..mac.enums import Category, SortOrder, SortType

            df = client.get_stock_quotes_list(
                category=Category.ZS,
                count=count,
                sort_type=SortType.CODE,
                sort_order=SortOrder.ASC,
            )
        else:
            bt = parse_board_type(board_type)
            df = client.get_board_list(board_type=bt, count=count)
    print_output(df, fmt, output_path, filename=f"board-{board_type.lower()}")


@click.command("board-members", cls=StandardHelpCommand)
@click.argument("board_symbol")
@click.option("--count", default=100000, type=int, help="请求数量")
@click.option(
    "--sort", "sort_field", default="CHANGE_PCT", help="排序字段: CHANGE_PCT/CODE/PRICE/VOLUME"
)
@click.option("--order", "sort_order", default="DESC", help="排序方向: DESC/ASC")
@output_options
def board_members(
    board_symbol: str,
    count: int,
    sort_field: str,
    sort_order: str,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取板块成分股报价。

    BOARD_SYMBOL: 通达信板块代码或支持的标准指数代码（如 881001、000699）

    示例：

      tdxman board-members 881001 --format table

      tdxman board-members 000699 --count 20 --format table

      tdxman board-members 881001 --sort VOLUME --count 20
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_sort_order, parse_sort_type

    fmt = output_fmt
    st = parse_sort_type(sort_field)
    so = parse_sort_order(sort_order)
    with get_mac_client() as client:
        df = client.get_board_members(
            board_symbol,
            count=count,
            sort_type=st,
            sort_order=so,
        )
    print_output(df, fmt, output_path, filename=board_symbol)


@click.command("belong-board", cls=StandardHelpCommand)
@click.argument("market")
@click.argument("code")
@output_options
def belong_board(market: str, code: str, output_fmt: str, output_path: Path | None) -> None:
    """获取个股所属板块列表。

    示例：

      tdxman belong-board SZ 000001

      tdxman belong-board SH 600519 --format table
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_market

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_belong_board(mkt, code)
    print_output(df, fmt, output_path, filename=code)
