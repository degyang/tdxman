"""证券标识格式端到端测试 - DataPool 公开接口。

用临时 parquet fixture 验证股票 / 指数 / ETF 的读取与目录：
新旧输入返回相同非空记录，输出为规范 symbol。
不联网。

股票用 600519.SH（贵州茅台）与 000001.SZ（平安银行）。
000001.SH 是上证指数，仅用于指数用例。
"""

import pandas as pd
import pytest

from aspool.api_contract import DataPoolError
from aspool.pool import DataPool, _normalize_symbol, _normalize_symbols

STOCK_SH = "600519"  # 贵州茅台
STOCK_SZ = "000001"  # 平安银行
INDEX_SH = "000001"  # 上证指数


def _write_bars(directory, rows):
    """在 directory 下写入 bars.parquet，目录不存在则创建。"""
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(directory / "bars.parquet", index=False)


@pytest.fixture
def temp_pool(tmp_path):
    """临时 DataPool：两只股票、一只指数、一只 ETF。"""
    # 股票：600519.SH
    _write_bars(
        tmp_path / "lake" / "bars" / "daily" / "market=SH" / f"symbol={STOCK_SH}",
        {
            "trade_date": pd.to_datetime(["2026-09-15", "2026-09-16"]),
            "market": ["SH", "SH"],
            "symbol": [STOCK_SH, STOCK_SH],
            "name": ["贵州茅台", "贵州茅台"],
            "open": [1800.0, 1810.0],
            "high": [1980.0, 1990.0],
            "low": [1790.0, 1800.0],
            "close": [1980.0, 1985.0],
            "volume": [1000000, 1200000],
            "amount": [1.8e9, 2.0e9],
            "turnover_rate": [0.5, 0.6],
            "pe_ttm": [30.0, 30.5],
        },
    )
    # 股票：000001.SZ
    _write_bars(
        tmp_path / "lake" / "bars" / "daily" / "market=SZ" / f"symbol={STOCK_SZ}",
        {
            "trade_date": pd.to_datetime(["2026-09-15", "2026-09-16"]),
            "market": ["SZ", "SZ"],
            "symbol": [STOCK_SZ, STOCK_SZ],
            "name": ["平安银行", "平安银行"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [1000000, 1200000],
            "amount": [1.0e7, 1.2e7],
            "turnover_rate": [0.5, 0.6],
            "pe_ttm": [5.0, 5.1],
        },
    )
    # 指数：000001.SH（上证指数）
    _write_bars(
        tmp_path / "lake" / "indices" / "daily" / "market=SH" / f"symbol={INDEX_SH}",
        {
            "trade_date": pd.to_datetime(["2026-09-15", "2026-09-16"]),
            "market": ["SH", "SH"],
            "code": [INDEX_SH, INDEX_SH],
            "name": ["上证指数", "上证指数"],
            "open": [3000.0, 3100.0],
            "high": [3100.0, 3200.0],
            "low": [2950.0, 3050.0],
            "close": [3050.0, 3150.0],
            "volume": [100000000, 120000000],
            "amount": [1.0e9, 1.2e9],
            "up_count": [2000, 2500],
            "down_count": [1500, 1000],
        },
    )
    # ETF：510050.SH（与股票共用 bars/daily，靠 asset_type 区分）
    _write_bars(
        tmp_path / "lake" / "bars" / "daily" / "market=SH" / "symbol=510050",
        {
            "trade_date": pd.to_datetime(["2026-09-15", "2026-09-16"]),
            "market": ["SH", "SH"],
            "symbol": ["510050", "510050"],
            "name": ["50ETF", "50ETF"],
            "asset_type": ["etf", "etf"],
            "open": [3.0, 3.05],
            "high": [3.1, 3.15],
            "low": [2.95, 3.0],
            "close": [3.05, 3.1],
            "volume": [5000000, 6000000],
            "amount": [1.5e7, 1.8e7],
            "turnover_rate": [1.0, 1.2],
        },
    )
    # 基本面快照：两只股票，用于 _read_fundamentals 的 symbols 筛选分支。
    fundamentals = tmp_path / "lake" / "fundamentals"
    fundamentals.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "market": "SH",
                "code": STOCK_SH,
                "total_shares": 1_000_000.0,
                "float_shares": 800_000.0,
                "ttm_eps": 60.0,
                "net_assets": 400.0,
            },
            {
                "market": "SZ",
                "code": STOCK_SZ,
                "total_shares": 2_000_000.0,
                "float_shares": 1_500_000.0,
                "ttm_eps": 2.0,
                "net_assets": 20.0,
            },
        ]
    ).to_parquet(fundamentals / "snapshots.parquet", index=False)
    return tmp_path


class TestNormalizeSymbol:
    """_normalize_symbol 单元测试。"""

    def test_canonical_to_storage(self):
        assert _normalize_symbol("000001.SH") == "SH.000001"

    def test_legacy_to_storage(self):
        assert _normalize_symbol("SH.000001") == "SH.000001"

    def test_case_insensitive(self):
        assert _normalize_symbol("000001.sh") == "SH.000001"
        assert _normalize_symbol("sh.000001") == "SH.000001"

    def test_invalid_format_raises(self):
        with pytest.raises(DataPoolError):
            _normalize_symbol("000001")

    def test_bj_market(self):
        assert _normalize_symbol("830001.BJ") == "BJ.830001"


class TestNormalizeSymbols:
    """_normalize_symbols 单元测试。"""

    def test_single_string(self):
        assert _normalize_symbols("000001.SH") == ["SH.000001"]

    def test_list(self):
        assert _normalize_symbols(["000001.SH", "600519.SZ"]) == ["SH.000001", "SZ.600519"]

    def test_none(self):
        assert _normalize_symbols(None) is None

    def test_mixed_formats(self):
        assert _normalize_symbols(["000001.SH", "SH.600519"]) == ["SH.000001", "SH.600519"]


class TestStockReadE2E:
    """股票 read_daily / read_research_daily 端到端。"""

    def test_new_format_nonempty(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols="600519.SH", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "600519.SH"

    def test_legacy_format_nonempty(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols="SH.600519", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "600519.SH"

    def test_new_old_same_rows(self, temp_pool):
        pool = DataPool(temp_pool)
        new = pool.read_daily(symbols="600519.SH", lookback=1)
        old = pool.read_daily(symbols="SH.600519", lookback=1)
        assert len(new) == len(old) == 1
        assert new.iloc[0]["close"] == old.iloc[0]["close"]
        assert list(new["symbol"]) == list(old["symbol"])

    def test_case_insensitive_real_normalization(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols="600519.sh", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "600519.SH"

    def test_sz_stock(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols="000001.SZ", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SZ"

    def test_market_and_code_columns_unchanged(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols="600519.SH", lookback=1)
        assert frame.iloc[0]["market"] == "SH"
        assert frame.iloc[0]["code"] == "600519"

    def test_list_input(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols=["600519.SH", "000001.SZ"], lookback=1)
        assert len(frame) == 2
        assert set(frame["symbol"]) == {"600519.SH", "000001.SZ"}

    def test_unmatched_returns_empty(self, temp_pool):
        frame = DataPool(temp_pool).read_daily(symbols="600000.SH", lookback=1)
        assert frame.empty

    def test_invalid_symbol_raises(self, temp_pool):
        with pytest.raises(DataPoolError):
            DataPool(temp_pool).read_daily(symbols="600519", lookback=1)

    def test_research_daily_optional_field_projection(self, temp_pool):
        """read_research_daily 保留可选财务字段投影，不因规范化丢失。"""
        frame = DataPool(temp_pool).read_research_daily(
            symbols="600519.SH", lookback=1, fields=["symbol", "date", "close", "pe_ttm"]
        )
        assert list(frame.columns) == ["symbol", "date", "close", "pe_ttm"]
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "600519.SH"
        assert frame.iloc[0]["pe_ttm"] == 30.5  # 2026-09-16

    def test_research_daily_legacy_input(self, temp_pool):
        frame = DataPool(temp_pool).read_research_daily(symbols="SH.600519", lookback=1)
        assert len(frame) == 1


class TestFundamentalsFilterE2E:
    """_read_fundamentals 的 symbols 筛选分支（估值联结）。"""

    def test_no_filter_returns_all_with_valuation(self, temp_pool):
        frame = DataPool(temp_pool)._read_fundamentals()
        assert len(frame) == 2
        assert frame["total_mv"].notna().all()
        assert frame["pe_ttm"].notna().all()

    def test_canonical_filter_is_nonempty(self, temp_pool):
        frame = DataPool(temp_pool)._read_fundamentals(symbols="600519.SH")
        assert len(frame) == 1
        assert frame.iloc[0]["code"] == STOCK_SH

    def test_legacy_filter_is_nonempty(self, temp_pool):
        frame = DataPool(temp_pool)._read_fundamentals(symbols="SH.600519")
        assert len(frame) == 1
        assert frame.iloc[0]["code"] == STOCK_SH

    def test_filtered_valuation_matches_unfiltered(self, temp_pool):
        """筛选后的估值字段与无筛选时同一标的的结果一致。"""
        pool = DataPool(temp_pool)
        everything = pool._read_fundamentals()
        filtered = pool._read_fundamentals(symbols="600519.SH")
        expected = everything[everything["code"] == STOCK_SH].iloc[0]
        assert filtered.iloc[0]["total_mv"] == expected["total_mv"]
        assert filtered.iloc[0]["pe_ttm"] == expected["pe_ttm"]
        assert filtered.iloc[0]["float_mv"] == expected["float_mv"]

    def test_filter_new_old_same_result(self, temp_pool):
        pool = DataPool(temp_pool)
        new = pool._read_fundamentals(symbols=["600519.SH", "000001.SZ"])
        old = pool._read_fundamentals(symbols=["SH.600519", "SZ.000001"])
        assert len(new) == len(old) == 2
        assert set(new["code"]) == set(old["code"])
        assert list(new["total_mv"]) == list(old["total_mv"])

    def test_filter_single_sz(self, temp_pool):
        frame = DataPool(temp_pool)._read_fundamentals(symbols="000001.SZ")
        assert len(frame) == 1
        assert frame.iloc[0]["code"] == STOCK_SZ
        assert frame.iloc[0]["total_mv"] == 11.0 * 2_000_000.0

    def test_filter_unmatched_is_empty(self, temp_pool):
        frame = DataPool(temp_pool)._read_fundamentals(symbols="600000.SH")
        assert frame.empty


class TestIndexReadE2E:
    """指数 read_index_daily / list_indices 端到端。"""

    def test_new_format(self, temp_pool):
        frame = DataPool(temp_pool).read_index_daily(symbols="000001.SH", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SH"

    def test_legacy_format(self, temp_pool):
        frame = DataPool(temp_pool).read_index_daily(symbols="SH.000001", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SH"

    def test_new_old_same_rows(self, temp_pool):
        pool = DataPool(temp_pool)
        new = pool.read_index_daily(symbols="000001.SH", lookback=1)
        old = pool.read_index_daily(symbols="SH.000001", lookback=1)
        assert len(new) == len(old) == 1
        assert new.iloc[0]["close"] == old.iloc[0]["close"]

    def test_case_insensitive(self, temp_pool):
        frame = DataPool(temp_pool).read_index_daily(symbols="sh.000001", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SH"

    def test_index_list_spec_symbol(self, temp_pool):
        frame = DataPool(temp_pool).list_indices()
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SH"

    def test_index_list_legacy_input(self, temp_pool):
        frame = DataPool(temp_pool).list_indices(symbols="SH.000001")
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SH"

    def test_index_list_new_input(self, temp_pool):
        frame = DataPool(temp_pool).list_indices(symbols="000001.SH")
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "000001.SH"


class TestEtfReadE2E:
    """ETF read_etf_daily / list_etfs 端到端。"""

    def test_new_format(self, temp_pool):
        frame = DataPool(temp_pool).read_etf_daily(symbols="510050.SH", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510050.SH"

    def test_legacy_format(self, temp_pool):
        frame = DataPool(temp_pool).read_etf_daily(symbols="SH.510050", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510050.SH"

    def test_new_old_same_rows(self, temp_pool):
        pool = DataPool(temp_pool)
        new = pool.read_etf_daily(symbols="510050.SH", lookback=1)
        old = pool.read_etf_daily(symbols="SH.510050", lookback=1)
        assert len(new) == len(old) == 1
        assert new.iloc[0]["close"] == old.iloc[0]["close"]

    def test_case_insensitive(self, temp_pool):
        frame = DataPool(temp_pool).read_etf_daily(symbols="sh.510050", lookback=1)
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510050.SH"

    def test_etf_list_spec_symbol(self, temp_pool):
        frame = DataPool(temp_pool).list_etfs()
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510050.SH"

    def test_etf_list_legacy_input(self, temp_pool):
        frame = DataPool(temp_pool).list_etfs(symbols="SH.510050")
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510050.SH"

    def test_etf_list_new_input(self, temp_pool):
        frame = DataPool(temp_pool).list_etfs(symbols="510050.SH")
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510050.SH"

    def test_stock_read_excludes_etf(self, temp_pool):
        """股票接口不返回 ETF。"""
        frame = DataPool(temp_pool).read_daily(lookback=1)
        assert "510050.SH" not in set(frame["symbol"])


class TestSharedEntryParse:
    """单标的 CLI 共享解析入口（非 Click 端到端）。"""

    def test_parse_new_format(self):
        from tdxman.cli.parsers import parse_symbol_or_market_code

        assert parse_symbol_or_market_code("600519.SH", None) == (1, "600519")

    def test_parse_old_format(self):
        from tdxman.cli.parsers import parse_symbol_or_market_code

        assert parse_symbol_or_market_code("SZ", "000001") == (0, "000001")

    def test_parse_legacy_dot_format(self):
        from tdxman.cli.parsers import parse_symbol_or_market_code

        assert parse_symbol_or_market_code("SH.600519", None) == (1, "600519")

    def test_parse_invalid_raises(self):
        from tdxman.cli.parsers import parse_symbol_or_market_code

        with pytest.raises(Exception):
            parse_symbol_or_market_code("abc", None)
