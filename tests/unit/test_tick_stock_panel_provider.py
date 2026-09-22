from __future__ import annotations

import pandas as pd
import polars as pl
import pytest

from tdxman.adapters.tick_stock_panel import ProviderError, TdxmanProvider
from tdxman.integrations.tick_stock_panel.plugin.provider import availability


class FakePool:
    def __init__(self, stock: pd.DataFrame, etf: pd.DataFrame | None = None):
        self.stock = stock
        self.etf = etf if etf is not None else stock
        self.calls: list[tuple[str, dict]] = []

    def read_daily(self, **kwargs):
        self.calls.append(("stock", kwargs))
        return self.stock

    def read_etf_daily(self, **kwargs):
        self.calls.append(("etf", kwargs))
        return self.etf

    def read_index_daily(self, **kwargs):
        self.calls.append(("index", kwargs))
        return self.stock


class FakeStandardClient:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames
        self.calls: list[tuple[object, str, object, int, int]] = []
        self.closed = False

    def get_security_bars(self, market, code, category, start, count):
        self.calls.append((market, code, category, start, count))
        return self.frames[code].iloc[start : start + count].copy()

    def get_index_bars(self, market, code, category, start, count):
        return self.get_security_bars(market, code, category, start, count)

    def close(self):
        self.closed = True


class FakeMacClient:
    def __init__(self):
        self.closed = False

    def get_stock_quotes_list(self, category, **kwargs):
        from tdxman.mac.enums import Category

        rows = {
            Category.A: [{"market": 1, "code": "600519", "name": "贵州茅台"}],
            Category.BJ: [{"market": 2, "code": "920002", "name": "万达轴承"}],
            Category.ETF: [{"market": 0, "code": "159919", "name": "沪深300ETF"}],
            Category.ZS: [{"market": 1, "code": "000001", "name": "上证指数"}],
        }
        return pd.DataFrame(rows[category])

    def get_stock_quotes(self, stocks, fields=None):
        names = {
            "600519": "贵州茅台",
            "920002": "万达轴承",
            "159919": "沪深300ETF",
            "000001": "上证指数",
        }
        return pd.DataFrame(
            [
                {
                    "market": market,
                    "code": code,
                    "name": names[code],
                    "pre_close": 100.0,
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                    "vol": 1234,
                    "amount": 12_340_000.0,
                    "turnover": 0.43,
                    "server_update_date": 20260921,
                    "server_update_time": 145959,
                    "bid_price": 100.9,
                    "bid2_price": 100.8,
                    "bid3_price": 100.7,
                    "bid4_price": 100.6,
                    "bid5_price": 100.5,
                    "ask_price": 101.1,
                    "ask2_price": 101.2,
                    "ask3_price": 101.3,
                    "ask4_price": 101.4,
                    "ask5_price": 101.5,
                    "bid_volume": 10,
                    "bid2_volume": 20,
                    "limit_up_count": 20,
                    "bid3_volume": 30,
                    "bid4_volume": 40,
                    "bid5_volume": 50,
                    "up_count": 50,
                    "ask_volume": 11,
                    "ask2_volume": 21,
                    "limit_down_count": 21,
                    "ask3_volume": 31,
                    "ask4_volume": 41,
                    "ask5_volume": 51,
                    "down_count": 51,
                }
                for market, code in stocks
            ]
        )

    def get_stock_kline(self, market, code, period, start=0, count=800, adjust=None):
        return pd.DataFrame(
            {
                "datetime": ["2026-09-21 09:30:00", "2026-09-21 09:31:00"],
                "open": [100.0, 101.0],
                "high": [101.0, 102.0],
                "low": [99.0, 100.0],
                "close": [101.0, 101.5],
                "vol": [1000.0, 2000.0],
                "amount": [100_000.0, 203_000.0],
            }
        )

    def close(self):
        self.closed = True


@pytest.fixture
def daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["SH.600519", "600519.SH", "000001.SZ"],
            "date": ["2026-09-16", "2026-09-15", "2026-09-15"],
            "open": [100.0, 99.0, 10.0],
            "high": [102.0, 101.0, 11.0],
            "low": [98.0, 97.0, 9.0],
            "close": [101.0, 100.0, 10.5],
            "volume": [123400.0, 100.0, 200.0],
            "amount": [12340000.0, 10000.0, 2100.0],
        }
    )


def test_local_stock_daily_has_consumer_schema_units_and_order(daily_frame):
    progress: list[tuple[int, int]] = []
    provider = TdxmanProvider({"pool": FakePool(daily_frame)})

    result = provider.get_daily(
        ["600519.SH", "SH.600519", "000001.SZ"],
        "2026-09-01",
        "2026-09-30",
        on_chunk_done=lambda cur, total: progress.append((cur, total)),
    )

    assert result.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    assert result.schema["date"] == pl.Date
    assert result.to_dicts()[0]["symbol"] == "000001.SZ"
    maotai = result.filter(pl.col("symbol") == "600519.SH").sort("date")
    assert maotai[0, "volume"] == 1.0
    assert progress == [(1, 1)]
    assert "daily" in provider.config.datasets
    assert "minute" not in provider.config.datasets


def test_local_daily_defensively_applies_requested_symbols_and_dates(daily_frame):
    provider = TdxmanProvider({"pool": FakePool(daily_frame)})

    result = provider.get_daily(["600519.SH"], "2026-09-16", "2026-09-16")

    assert result.to_dicts() == [
        {
            "symbol": "600519.SH",
            "date": pd.Timestamp("2026-09-16").date(),
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volume": 1234.0,
            "amount": 12340000.0,
        }
    ]


@pytest.mark.parametrize(
    "column, value",
    [("open", 103.0), ("volume", None), ("amount", float("inf"))],
)
def test_local_daily_rejects_invalid_ohlcv(column, value, daily_frame):
    invalid = daily_frame.iloc[:1].copy()
    invalid.loc[invalid.index[0], column] = value
    provider = TdxmanProvider({"pool": FakePool(invalid)})

    with pytest.raises(ProviderError, match="non-finite or negative|invalid") as excinfo:
        provider.get_daily(["600519.SH"])

    assert excinfo.value.code == "BAD_DATA"


def test_local_etf_uses_etf_reader_and_empty_result_has_stable_schema(daily_frame):
    pool = FakePool(pd.DataFrame(), etf=daily_frame.iloc[0:0])
    provider = TdxmanProvider({"pool": pool})

    result = provider.get_daily(["510300.SH"], asset_type="etf")

    assert pool.calls[0][0] == "etf"
    assert result.is_empty()
    assert result.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]


def test_index_daily_keeps_tdx_volume_in_lots(daily_frame):
    pool = FakePool(daily_frame)
    provider = TdxmanProvider({"pool": pool})

    result = provider.get_daily(["600519.SH"], asset_type="index")

    assert pool.calls[0][0] == "index"
    day = result.filter(pl.col("date") == pd.Timestamp("2026-09-16").date())
    assert day[0, "volume"] == 123_400.0


def test_invalid_date_range_and_closed_provider_are_explicit(daily_frame):
    provider = TdxmanProvider({"pool": FakePool(daily_frame)})
    with pytest.raises(ProviderError, match="must not be after"):
        provider.get_daily(["600519.SH"], "2026-09-02", "2026-09-01")

    provider.close()
    with pytest.raises(ProviderError, match="closed"):
        provider.get_daily(["600519.SH"])


def test_online_daily_pages_and_collects_every_symbol():
    frames = {
        "600519": pd.DataFrame(
            {
                "date": ["2026-09-18", "2026-09-17", "2026-09-16"],
                "open": [100.0, 99.0, 98.0],
                "high": [102.0, 101.0, 100.0],
                "low": [98.0, 97.0, 96.0],
                "close": [101.0, 100.0, 99.0],
                "vol": [1000.0, 2000.0, 3000.0],
                "amount": [10000.0, 20000.0, 30000.0],
            }
        ),
        "000001": pd.DataFrame(
            {
                "date": ["2026-09-18"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.5],
                "vol": [400.0],
                "amount": [4200.0],
            }
        ),
    }
    client = FakeStandardClient(frames)
    progress: list[tuple[int, int]] = []
    provider = TdxmanProvider(
        {
            "daily_mode": "online",
            "standard_client": client,
            "online_page_size": 2,
            "online_max_bars_per_symbol": 6,
        }
    )

    result = provider.get_daily(
        ["600519.SH", "000001.SZ"],
        "2026-09-16",
        on_chunk_done=lambda cur, total: progress.append((cur, total)),
    )

    assert result.get_column("symbol").unique().sort().to_list() == ["000001.SZ", "600519.SH"]
    assert result.height == 4
    assert result.filter(pl.col("symbol") == "600519.SH")[0, "volume"] == 30.0
    assert [call[3:] for call in client.calls] == [(0, 2), (2, 2), (0, 2)]
    assert progress == [(1, 2), (2, 2)]


def test_online_daily_reports_truncation_and_owns_client_lifecycle():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-18", "2026-09-17"],
            "open": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.0, 9.0],
            "close": [10.5, 10.5],
            "vol": [100.0, 100.0],
            "amount": [1000.0, 1000.0],
        }
    )
    client = FakeStandardClient({"600519": frame})
    provider = TdxmanProvider(
        {
            "daily_mode": "online",
            "standard_client": client,
            "close_standard_client": True,
            "online_page_size": 2,
            "online_max_bars_per_symbol": 2,
        }
    )

    with pytest.raises(ProviderError, match="configured limit") as excinfo:
        provider.get_daily(["600519.SH"])
    assert excinfo.value.code == "INCOMPLETE_COVERAGE"

    provider.close()
    provider.close()
    assert client.closed is True


def test_plugin_availability_checks_local_pool_without_creating_it(tmp_path, monkeypatch):
    root = tmp_path / "pool"
    monkeypatch.setenv("TDXMAN_ASPOOL_ROOT", str(root))
    monkeypatch.setenv("TDXMAN_TICK_STOCK_PANEL_MODE", "local")

    ok, reason = availability()

    assert ok is False
    assert "未找到" in reason
    assert not root.exists()

    from aspool.store import initialize

    initialize(root)
    ok, reason = availability()
    assert ok is True
    assert reason.startswith("ready")


def test_mac_instruments_and_realtime_follow_consumer_contract():
    client = FakeMacClient()
    provider = TdxmanProvider({"pool": FakePool(pd.DataFrame()), "mac_client": client})

    instruments = provider.get_instruments("stock")
    realtime = provider.get_realtime()

    assert [row["symbol"] for row in instruments] == ["600519.SH", "920002.BJ"]
    assert instruments[0]["ext"]["listing_date"] is None
    assert "realtime" in provider.config.datasets
    assert {row["symbol"] for row in realtime} == {"600519.SH", "920002.BJ", "159919.SZ"}
    maotai = next(row for row in realtime if row["symbol"] == "600519.SH")
    assert maotai["volume"] == 1234.0
    assert maotai["change_pct"] == pytest.approx(0.01)
    assert maotai["turnover_rate"] == pytest.approx(0.0043)
    assert maotai["timestamp"] == 1789973999000
    indices = provider.get_realtime_indices(["000001.SH"])
    assert indices is not None
    assert indices[0]["symbol"] == "000001.SH"


def test_realtime_soft_fails_when_a_batch_is_incomplete():
    client = FakeMacClient()
    client.get_stock_quotes = lambda stocks, fields=None: pd.DataFrame()
    provider = TdxmanProvider({"pool": FakePool(pd.DataFrame()), "mac_client": client})

    assert provider.get_realtime() == []
    assert provider.get_realtime_indices(["000001.SH"]) is None


def test_minute_and_intraday_use_mac_bars_with_wall_clock_and_lot_conversion():
    provider = TdxmanProvider({"pool": FakePool(pd.DataFrame()), "mac_client": FakeMacClient()})
    progress: list[tuple[int, int]] = []

    minute = provider.get_minute(
        ["600519.SH"],
        "2026-09-21 09:30:00+08:00",
        "2026-09-21 09:30:00+08:00",
        freq="5m",
        on_chunk_done=lambda current, total: progress.append((current, total)),
    )

    assert minute.columns == [
        "symbol", "datetime", "open", "high", "low", "close", "volume", "amount"
    ]
    assert minute.height == 1
    assert minute[0, "datetime"] == pd.Timestamp("2026-09-21 09:30:00")
    assert minute[0, "volume"] == 10.0
    assert progress == [(1, 1)]
    assert "minute" in provider.config.datasets
    assert "full_minute" in provider.config.datasets

    with pytest.raises(ProviderError, match="Unsupported frequency"):
        provider.get_minute(["600519.SH"], freq="2m")


def test_depth_batch_returns_complete_five_level_books_with_server_time():
    provider = TdxmanProvider({"pool": FakePool(pd.DataFrame()), "mac_client": FakeMacClient()})

    result = provider.get_depth_batch(["600519.SH"])

    assert "depth5" in provider.config.datasets
    assert result["600519.SH"] == {
        "bid_prices": [100.9, 100.8, 100.7, 100.6, 100.5],
        "bid_volumes": [10.0, 20.0, 30.0, 40.0, 50.0],
        "ask_prices": [101.1, 101.2, 101.3, 101.4, 101.5],
        "ask_volumes": [11.0, 21.0, 31.0, 41.0, 51.0],
        "timestamp": 1789973999000,
    }


def test_dataset_covers_every_declared_dataset_without_market_wide_probe():
    provider = TdxmanProvider({"pool": FakePool(pd.DataFrame()), "mac_client": FakeMacClient()})

    minute = provider.test_dataset("minute", ["600519.SH"])
    realtime = provider.test_dataset("realtime", ["600519.SH"])
    depth = provider.test_dataset("depth5", ["600519.SH"])
    rejected = provider.test_dataset("realtime")

    assert minute["rows"] == 2
    assert minute["columns"][0:2] == ["symbol", "datetime"]
    assert realtime["rows"] == 1
    assert realtime["preview"][0]["symbol"] == "600519.SH"
    assert depth["rows"] == 1
    assert len(depth["preview"][0]["bid_prices"]) == 5
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"
