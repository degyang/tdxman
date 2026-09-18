"""证券标识格式转换测试。"""

import pytest

from tdxman.symbol import (
    SymbolError,
    format_symbol,
    format_symbols,
    guess_market,
    is_valid_symbol,
    parse_symbol,
    parse_symbols,
)


class TestParseSymbol:
    """测试 parse_symbol 函数。"""

    def test_canonical_format_sh(self):
        """规范格式: 000001.SH"""
        code, market = parse_symbol("000001.SH")
        assert code == "000001"
        assert market == "SH"

    def test_canonical_format_sz(self):
        """规范格式: 000001.SZ"""
        code, market = parse_symbol("000001.SZ")
        assert code == "000001"
        assert market == "SZ"

    def test_canonical_format_bj(self):
        """规范格式: 830001.BJ"""
        code, market = parse_symbol("830001.BJ")
        assert code == "830001"
        assert market == "BJ"

    def test_legacy_dot_format(self):
        """兼容格式: SH.000001"""
        code, market = parse_symbol("SH.000001")
        assert code == "000001"
        assert market == "SH"

    def test_legacy_space_format(self):
        """兼容格式: SH 000001"""
        code, market = parse_symbol("SH 000001")
        assert code == "000001"
        assert market == "SH"

    def test_case_insensitive(self):
        """大小写不敏感"""
        code, market = parse_symbol("000001.sh")
        assert code == "000001"
        assert market == "SH"

    def test_whitespace_trimmed(self):
        """去除首尾空格"""
        code, market = parse_symbol("  000001.SH  ")
        assert code == "000001"
        assert market == "SH"

    def test_pure_code_raises(self):
        """纯六位代码应抛出错误"""
        with pytest.raises(SymbolError, match="纯六位代码需指定市场"):
            parse_symbol("000001")

    def test_invalid_format_raises(self):
        """无效格式应抛出错误"""
        with pytest.raises(SymbolError, match="无效的证券标识格式"):
            parse_symbol("abc")

    def test_short_code_raises(self):
        """短代码应抛出错误"""
        with pytest.raises(SymbolError, match="无效的证券标识格式"):
            parse_symbol("00001.SH")

    def test_long_code_raises(self):
        """长代码应抛出错误"""
        with pytest.raises(SymbolError, match="无效的证券标识格式"):
            parse_symbol("0000001.SH")


class TestFormatSymbol:
    """测试 format_symbol 函数。"""

    def test_format_sh(self):
        """格式化 SH 市场"""
        result = format_symbol("000001", "SH")
        assert result == "000001.SH"

    def test_format_sz(self):
        """格式化 SZ 市场"""
        result = format_symbol("000001", "SZ")
        assert result == "000001.SZ"

    def test_format_bj(self):
        """格式化 BJ 市场"""
        result = format_symbol("830001", "BJ")
        assert result == "830001.BJ"


class TestParseSymbols:
    """测试 parse_symbols 函数。"""

    def test_multiple_canonical(self):
        """多个规范格式"""
        result = parse_symbols("600519.SH,000001.SZ")
        assert result == [("600519", "SH"), ("000001", "SZ")]

    def test_multiple_mixed(self):
        """混合格式"""
        result = parse_symbols("SZ 000001,600519.SH")
        assert result == [("000001", "SZ"), ("600519", "SH")]

    def test_single_symbol(self):
        """单个标识"""
        result = parse_symbols("000001.SH")
        assert result == [("000001", "SH")]

    def test_empty_string(self):
        """空字符串"""
        result = parse_symbols("")
        assert result == []

    def test_whitespace_handling(self):
        """空格处理"""
        result = parse_symbols(" 600519.SH , 000001.SZ ")
        assert result == [("600519", "SH"), ("000001", "SZ")]


class TestFormatSymbols:
    """测试 format_symbols 函数。"""

    def test_format_multiple(self):
        """格式化多个标识"""
        result = format_symbols([("600519", "SH"), ("000001", "SZ")])
        assert result == "600519.SH,000001.SZ"

    def test_format_single(self):
        """格式化单个标识"""
        result = format_symbols([("000001", "SH")])
        assert result == "000001.SH"

    def test_format_empty(self):
        """格式化空列表"""
        result = format_symbols([])
        assert result == ""


class TestGuessMarket:
    """测试 guess_market 函数。"""

    def test_sz_codes(self):
        """深圳代码"""
        assert guess_market("000001") == "SZ"
        assert guess_market("300001") == "SZ"

    def test_sh_codes(self):
        """上海代码"""
        assert guess_market("600000") == "SH"
        assert guess_market("688001") == "SH"
        assert guess_market("510050") == "SH"

    def test_bj_codes(self):
        """北交所代码"""
        assert guess_market("830001") == "BJ"
        assert guess_market("430047") == "BJ"
        assert guess_market("920001") == "BJ"


class TestIsValidSymbol:
    """测试 is_valid_symbol 函数。"""

    def test_valid_canonical(self):
        """有效规范格式"""
        assert is_valid_symbol("000001.SH") is True

    def test_valid_legacy(self):
        """有效兼容格式"""
        assert is_valid_symbol("SH.000001") is True

    def test_invalid(self):
        """无效格式"""
        assert is_valid_symbol("abc") is False

    def test_pure_code(self):
        """纯六位代码"""
        assert is_valid_symbol("000001") is False


class TestRoundTrip:
    """测试往返转换。"""

    def test_parse_then_format(self):
        """解析后格式化应返回规范格式"""
        code, market = parse_symbol("SH.000001")
        result = format_symbol(code, market)
        assert result == "000001.SH"

    def test_format_then_parse(self):
        """格式化后解析应返回原始数据"""
        symbol = format_symbol("000001", "SH")
        code, market = parse_symbol(symbol)
        assert code == "000001"
        assert market == "SH"

    def test_legacy_to_canonical(self):
        """旧格式转换为规范格式"""
        test_cases = [
            ("SH.000001", "000001.SH"),
            ("SH.600519", "600519.SH"),
            ("BJ.830001", "830001.BJ"),
            ("SH 000001", "000001.SH"),
            ("SH 600519", "600519.SH"),
        ]
        for input_val, expected in test_cases:
            code, market = parse_symbol(input_val)
            assert format_symbol(code, market) == expected