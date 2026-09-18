"""证券标识格式转换工具。

规范格式: 000001.SH, 000001.SZ, 000001.BJ
兼容格式: SH.000001, SZ.000001, SH 000001, SZ 000001
"""

from __future__ import annotations

import re

# 规范格式: 000001.SH, 000001.SZ, 000001.BJ
_CANONICAL = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")

# 兼容格式: SH.000001, SZ.000001
_LEGACY_DOT = re.compile(r"^(SH|SZ|BJ)\.(\d{6})$")

# 兼容格式: SH 000001, SZ 000001
_LEGACY_SPACE = re.compile(r"^(SH|SZ|BJ)\s+(\d{6})$")


class SymbolError(ValueError):
    """证券标识格式错误。"""


def parse_symbol(s: str) -> tuple[str, str]:
    """解析证券标识，返回 (code, market)。

    支持格式:
    - 规范: 000001.SH, 000001.SZ, 000001.BJ
    - 兼容: SH.000001, SZ.000001
    - 兼容: SH 000001, SZ 000001

    Returns:
        (code, market) 如 ('000001', 'SZ')

    Raises:
        SymbolError: 格式无效
    """
    s = s.strip().upper()

    # 规范格式: 000001.SH
    m = _CANONICAL.match(s)
    if m:
        return m.group(1), m.group(2)

    # 兼容格式: SH.000001
    m = _LEGACY_DOT.match(s)
    if m:
        return m.group(2), m.group(1)

    # 兼容格式: SH 000001
    m = _LEGACY_SPACE.match(s)
    if m:
        return m.group(2), m.group(1)

    # 纯六位代码
    if s.isdigit() and len(s) == 6:
        raise SymbolError(
            f"纯六位代码需指定市场: {s} -> 请使用 {s}.SH 或 {s}.SZ"
        )

    raise SymbolError(f"无效的证券标识格式: {s}")


def format_symbol(code: str, market: str) -> str:
    """格式化证券标识为规范格式。

    Args:
        code: 6位代码，如 '000001'
        market: 市场代码，如 'SH', 'SZ', 'BJ'

    Returns:
        规范格式，如 '000001.SZ'
    """
    return f"{code}.{market}"


def parse_symbols(s: str) -> list[tuple[str, str]]:
    """解析多个证券标识。

    支持格式:
    - 逗号分隔: 000001.SH,600519.SH
    - 混合格式: SZ 000001,600519.SH

    Returns:
        [(code, market), ...]
    """
    results = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        results.append(parse_symbol(part))
    return results


def format_symbols(pairs: list[tuple[str, str]]) -> str:
    """格式化多个证券标识为规范格式。"""
    return ",".join(format_symbol(code, market) for code, market in pairs)


def guess_market(code: str) -> str:
    """根据代码推断市场（仅用于无歧义情况）。

    注意：这不是所有情况都准确，建议显式指定市场。
    """
    if code.startswith(("4", "8", "920")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def is_valid_symbol(s: str) -> bool:
    """检查是否为有效的证券标识（规范或兼容格式）。"""
    try:
        parse_symbol(s)
        return True
    except SymbolError:
        return False