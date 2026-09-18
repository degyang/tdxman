"""CLI 参数解析工具。"""

from __future__ import annotations

import click

from ..mac.enums import (
    Adjust,
    BoardType,
    Category,
    ExMarket,
    Period,
    SortOrder,
    SortType,
)
from ..models.enums import Market
from ..symbol import SymbolError, parse_symbol

_MARKET_MAP: dict[str, Market] = {
    "SZ": Market.SZ,
    "SH": Market.SH,
    "BJ": Market.BJ,
    "0": Market.SZ,
    "1": Market.SH,
    "2": Market.BJ,
}


def parse_market(s: str) -> int:
    """Parse market string to int value. Accepts 'SZ', 'SH', 'BJ', '0', '1', '2'."""
    s_upper = s.upper()
    if s_upper in _MARKET_MAP:
        return _MARKET_MAP[s_upper]
    try:
        value = int(s)
    except ValueError as exc:
        raise click.BadParameter("市场代码应为 SH/SZ/BJ 或 0/1/2") from exc
    if value not in {market.value for market in Market}:
        raise click.BadParameter("市场代码应为 SH/SZ/BJ 或 0/1/2")
    return value


_PERIOD_MAP: dict[str, Period] = {
    "1MIN": Period.MIN_1,
    "1": Period.MIN_1,
    "5MIN": Period.MIN_5,
    "5": Period.MIN_5,
    "15MIN": Period.MIN_15,
    "15": Period.MIN_15,
    "30MIN": Period.MIN_30,
    "30": Period.MIN_30,
    "60MIN": Period.MIN_60,
    "60": Period.MIN_60,
    "DAILY": Period.DAILY,
    "D": Period.DAILY,
    "WEEKLY": Period.WEEKLY,
    "W": Period.WEEKLY,
    "MONTHLY": Period.MONTHLY,
    "M": Period.MONTHLY,
    "YEARLY": Period.YEARLY,
    "Y": Period.YEARLY,
}


def parse_period(s: str) -> Period:
    """Parse period string."""
    s_upper = s.upper()
    if s_upper in _PERIOD_MAP:
        return _PERIOD_MAP[s_upper]
    try:
        return Period(int(s))
    except ValueError as exc:
        raise click.BadParameter(
            "K 线周期无效；请使用 DAILY、WEEKLY、MONTHLY、1MIN、5MIN、15MIN、30MIN 或 60MIN"
        ) from exc


_ADJUST_MAP: dict[str, Adjust] = {
    "NONE": Adjust.NONE,
    "0": Adjust.NONE,
    "QFQ": Adjust.QFQ,
    "1": Adjust.QFQ,
    "FQ": Adjust.QFQ,
    "HFQ": Adjust.HFQ,
    "2": Adjust.HFQ,
}


def parse_adjust(s: str) -> Adjust:
    """Parse adjust string."""
    s_upper = s.upper()
    if s_upper in _ADJUST_MAP:
        return _ADJUST_MAP[s_upper]
    try:
        return Adjust(int(s))
    except ValueError as exc:
        raise click.BadParameter("复权类型应为 NONE、QFQ 或 HFQ") from exc


_BOARD_TYPE_MAP: dict[str, BoardType] = {
    "HY": BoardType.HY,
    "INDUSTRY": BoardType.HY,
    "HY2": BoardType.HY2,
    "INDUSTRY2": BoardType.HY2,
    "GN": BoardType.GN,
    "CONCEPT": BoardType.GN,
    "FG": BoardType.FG,
    "STYLE": BoardType.FG,
    "DQ": BoardType.DQ,
    "REGION": BoardType.DQ,
    "OTHER": BoardType.OTHER,
    "YJ_LEVEL1": BoardType.YJ_LEVEL1,
    "YJ1": BoardType.YJ_LEVEL1,
    "YJ_LEVEL2": BoardType.YJ_LEVEL2,
    "YJ2": BoardType.YJ_LEVEL2,
    "YJ_LEVEL3": BoardType.YJ_LEVEL3,
    "YJ3": BoardType.YJ_LEVEL3,
    "ALL": BoardType.ALL,
}


def parse_board_type(s: str) -> BoardType:
    """Parse board type string."""
    s_upper = s.upper()
    if s_upper in _BOARD_TYPE_MAP:
        return _BOARD_TYPE_MAP[s_upper]
    try:
        return BoardType(int(s))
    except ValueError as exc:
        raise click.BadParameter(
            "板块类型应为 ALL/HY/HY2/GN/FG/DQ/OTHER/YJ_LEVEL1/YJ_LEVEL2/YJ_LEVEL3；"
            "标准指数请使用 ZS"
        ) from exc


def parse_ex_market(s: str) -> int:
    """Parse extended market string to int value."""
    s_upper = s.upper()
    for member in ExMarket:
        if member.name == s_upper:
            return member.value
    _EX_MAP: dict[str, ExMarket] = {
        "HK": ExMarket.HK_MAIN_BOARD,
        "HK_MAIN_BOARD": ExMarket.HK_MAIN_BOARD,
        "US": ExMarket.US_STOCK,
        "US_STOCK": ExMarket.US_STOCK,
        "SH_FUTURES": ExMarket.SH_FUTURES,
        "DCE": ExMarket.DL_FUTURES,
        "CZCE": ExMarket.ZZ_FUTURES,
        "CFFEX": ExMarket.CFFEX_FUTURES,
        "INE": ExMarket.SH_GOLD,
        "GFEX": ExMarket.GZ_FUTURES,
    }
    if s_upper in _EX_MAP:
        return _EX_MAP[s_upper].value
    try:
        return int(s)
    except ValueError as exc:
        raise click.BadParameter("扩展市场代码无效；请使用 tdxman ex markets 查看可用代码") from exc


_CATEGORY_MAP: dict[str, Category] = {
    "A": Category.A,
    "全A": Category.A,
    "B": Category.B,
    "KCB": Category.KCB,
    "CYB": Category.CYB,
    "BJ": Category.BJ,
    "SH": Category.SH,
    "SZ": Category.SZ,
}


def parse_category(s: str) -> Category:
    """Parse category string to Category enum."""
    s_upper = s.upper()
    for member in Category:
        if member.name == s_upper:
            return member
    if s_upper in _CATEGORY_MAP:
        return _CATEGORY_MAP[s_upper]
    try:
        return Category(int(s))
    except ValueError as exc:
        raise click.BadParameter(
            "市场分类无效；请使用 tdxman quote-list --help 查看类型对照"
        ) from exc


def parse_sort_type(s: str) -> SortType:
    """Parse sort type string."""
    s_upper = s.upper()
    for member in SortType:
        if member.name == s_upper:
            return member
    try:
        return SortType(int(s))
    except ValueError as exc:
        raise click.BadParameter("排序字段无效；请使用命令帮助列出的字段") from exc


_SORT_ORDER_MAP: dict[str, SortOrder] = {
    "ASC": SortOrder.ASC,
    "DESC": SortOrder.DESC,
    "NONE": SortOrder.NONE,
}


def parse_sort_order(s: str) -> SortOrder:
    """Parse sort order string."""
    s_upper = s.upper()
    if s_upper in _SORT_ORDER_MAP:
        return _SORT_ORDER_MAP[s_upper]
    try:
        return SortOrder(int(s))
    except ValueError as exc:
        raise click.BadParameter("排序方向应为 ASC、DESC 或 NONE") from exc


def parse_symbol_or_market_code(symbol_or_market: str, code: str | None) -> tuple[int, str]:
    """Parse a single security identity into (market, code).

    Single-security CLI commands share this entry so every command accepts the
    same inputs and reports the same errors.

    Supports:
    - 规范: 600519.SH        (symbol_or_market="600519.SH", code=None)
    - 兼容: SH 600519        (symbol_or_market="SH",        code="600519")
    - 兼容: SH.600519        (symbol_or_market="SH.600519", code=None)

    显式市场不被纠正也不被猜测：显式写了 SZ 就用 SZ，不查目录、不按代码
    前缀改写。
    """
    if code is not None:
        return parse_market(symbol_or_market), code

    try:
        parsed_code, market_str = parse_symbol(symbol_or_market)
    except SymbolError as exc:
        raise click.BadParameter(
            f'无效的证券标识: {symbol_or_market}；请使用 "600519.SH" 或 "SH 600519"'
        ) from exc
    return parse_market(market_str), parsed_code


def parse_stocks(s: str) -> list[tuple[int, str]]:
    """Parse stock list into [(market, code), ...].

    支持格式:
    - 规范: 000001.SH,600519.SZ
    - 兼容: SZ 000001,SH 600000
    - 混合: SZ 000001,600519.SH

    注意: 不允许市场猜测，SZ 600519 会被接受但不会按代码前缀改写。
    """
    from ..symbol import SymbolError, parse_symbol

    if not s or not s.strip():
        raise click.BadParameter("stocks 不能为空")

    result: list[tuple[int, str]] = []
    for pair in s.split(","):
        pair = pair.strip()
        if not pair:
            continue

        # 尝试新格式: 000001.SH 或 SH.000001
        try:
            code, market_str = parse_symbol(pair)
            market = parse_market(market_str)
            result.append((market, code))
            continue
        except SymbolError:
            pass  # 尝试旧格式

        # 旧格式: SZ 000001
        parts = pair.split()
        if len(parts) == 2:
            market = parse_market(parts[0])
            code = parts[1]
            if not code.isdigit() or len(code) != 6:
                raise click.BadParameter(f'证券代码应为六位数字: {code}')
            result.append((market, code))
        else:
            raise click.BadParameter(
                f'无效的证券标识格式: {pair}；'
                '请使用 "000001.SH" 或 "SZ 000001"'
            )

    if not result:
        raise click.BadParameter("stocks 不能为空")

    return result
