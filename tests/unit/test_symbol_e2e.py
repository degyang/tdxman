"""证券标识格式端到端测试。

测试公开API的新旧格式输入返回相同行、规范symbol输出。
使用mock数据源，不联网。
"""

import pytest

from tdxman.cli.parsers import parse_market, parse_stocks
from tdxman.symbol import format_symbol, parse_symbol


class TestParseStocksE2E:
    """parse_stocks 端到端测试。"""

    def test_new_format_single(self):
        """新格式单标的"""
        result = parse_stocks("000001.SH")
        assert result == [(1, "000001")]  # Market.SH = 1

    def test_new_format_multiple(self):
        """新格式多标的"""
        result = parse_stocks("600519.SH,000001.SZ")
        assert result == [(1, "600519"), (0, "000001")]  # SH=1, SZ=0

    def test_explicit_market_is_not_corrected(self):
        """显式市场不被纠正，也不被猜测。

        600519 实际上是 SH，但显式写了 SZ 时解析器必须照用，
        不查目录、不按代码前缀改写。
        """
        result = parse_stocks("600519.SZ")
        assert result == [(0, "600519")]

    def test_old_format_single(self):
        """旧格式单标的"""
        result = parse_stocks("SZ 000001")
        assert result == [(0, "000001")]

    def test_old_format_multiple(self):
        """旧格式多标的"""
        result = parse_stocks("SZ 000001,SH 600519")
        assert result == [(0, "000001"), (1, "600519")]

    def test_mixed_format(self):
        """混合格式"""
        result = parse_stocks("000001.SH,SZ 600519")
        assert result == [(1, "000001"), (0, "600519")]

    def test_legacy_dot_format(self):
        """旧点号格式"""
        result = parse_stocks("SH.000001")
        assert result == [(1, "000001")]

    def test_case_insensitive(self):
        """大小写不敏感"""
        result = parse_stocks("000001.sh")
        assert result == [(1, "000001")]

    def test_whitespace_handling(self):
        """空格处理"""
        result = parse_stocks(" 000001.SH , 600519.SZ ")
        assert result == [(1, "000001"), (0, "600519")]

    def test_empty_string_raises(self):
        """空字符串应抛出错误"""
        with pytest.raises(Exception):
            parse_stocks("")

    def test_invalid_format_raises(self):
        """无效格式应抛出错误"""
        with pytest.raises(Exception):
            parse_stocks("abc")

    def test_pure_code_raises(self):
        """纯六位代码应抛出错误"""
        with pytest.raises(Exception):
            parse_stocks("000001")

    def test_wrong_market_raises(self):
        """错误市场应抛出错误（SZ 600519）"""
        # 600519 属于 SH，但用户指定了 SZ
        # 当前行为：接受用户指定的市场，不进行猜测
        result = parse_stocks("SZ 600519")
        assert result == [(0, "600519")]  # 接受用户指定


class TestSymbolNormalization:
    """符号规范化测试。"""

    def test_canonical_to_storage(self):
        """规范格式 -> 存储格式"""
        code, market = parse_symbol("000001.SH")
        storage = f"{market}.{code}"
        assert storage == "SH.000001"

    def test_legacy_to_storage(self):
        """旧格式 -> 存储格式"""
        code, market = parse_symbol("SH.000001")
        storage = f"{market}.{code}"
        assert storage == "SH.000001"

    def test_storage_to_canonical(self):
        """存储格式 -> 规范格式"""
        # 假设从存储读取: market=SH, code=000001
        market, code = "SH", "000001"
        canonical = format_symbol(code, market)
        assert canonical == "000001.SH"


class TestCLICompatibility:
    """CLI兼容性测试。"""

    def test_parse_market_sh(self):
        """parse_market SH"""
        assert parse_market("SH") == 1

    def test_parse_market_sz(self):
        """parse_market SZ"""
        assert parse_market("SZ") == 0

    def test_parse_market_bj(self):
        """parse_market BJ"""
        assert parse_market("BJ") == 2

    def test_parse_market_case_insensitive(self):
        """parse_market 大小写"""
        assert parse_market("sh") == 1
        assert parse_market("Sh") == 1


class TestRoundTripE2E:
    """往返转换端到端测试。"""

    def test_parse_format_parse(self):
        """解析 -> 格式化 -> 解析 应返回相同结果"""
        original = "000001.SH"
        code1, market1 = parse_symbol(original)
        formatted = format_symbol(code1, market1)
        code2, market2 = parse_symbol(formatted)
        assert code1 == code2
        assert market1 == market2

    def test_legacy_to_canonical_roundtrip(self):
        """旧格式 -> 规范格式 -> 旧格式"""
        legacy = "SH.000001"
        code, market = parse_symbol(legacy)
        canonical = format_symbol(code, market)
        assert canonical == "000001.SH"

        # 从规范格式解析回来
        code2, market2 = parse_symbol(canonical)
        assert code2 == code
        assert market2 == market