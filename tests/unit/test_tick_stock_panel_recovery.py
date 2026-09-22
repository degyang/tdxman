from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import pytest

from tdxman.adapters.tick_stock_panel import ProviderError
from tdxman.adapters.tick_stock_panel.normalize import empty_minute, normalize_minute
from tdxman.adapters.tick_stock_panel.transport import ManagedClient
from tdxman.exceptions import TdxConnectionError
from tdxman.integrations.tick_stock_panel.plugin.provider import TdxmanProvider, availability


def bars(count=5):
    return pd.DataFrame({
        "datetime": pd.date_range("2026-09-21 09:31", periods=count, freq="min"),
        "open": [10.] * count, "high": [11.] * count,
        "low": [9.] * count, "close": [10.5] * count,
        "vol": [1000.] * count, "amount": [10000.] * count,
    })


class PagedClient:
    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def get_stock_kline(self, market, code, period, start=0, count=700, adjust=None):
        self.calls.append((market, code, period, start, count, adjust))
        end = len(self.frame) - start
        return self.frame.iloc[max(0, end-count):max(0, end)].copy()


@pytest.mark.parametrize("freq,period", [("1m", 7), ("5MIN", 0), ("15m", 1),
                                       ("30m", 2), ("60m", 3)])
def test_all_periods_units_symbols_and_preview(freq, period):
    client = PagedClient(bars())
    plugin = TdxmanProvider({"mac_client": client})
    result = plugin.get_minute(["SH.600519"], freq=freq)
    assert result["symbol"].to_list() == ["600519.SH"] * 5
    assert result["volume"].to_list() == [10.] * 5
    assert client.calls[0][2] == period
    assert int(client.calls[0][-1]) == 0
    json.dumps(plugin.test_dataset("minute", ["600519.SH"]), allow_nan=False)


def test_minute_pagination_boundary_and_truncation():
    client = PagedClient(bars(705))
    plugin = TdxmanProvider({"mac_client": client})
    result = plugin.get_minute(["600519.SH"], "2026-09-21 01:31:00Z")
    assert result.height == 705
    assert [call[3] for call in client.calls] == [0, 700]
    assert max(call[4] for call in client.calls) == 700
    truncated = TdxmanProvider({"mac_client": client, "online_max_bars_per_symbol": 700})
    with pytest.raises(ProviderError, match="configured limit"):
        truncated.get_minute(["600519.SH"], "2026-09-21 09:31")


def test_no_progress_and_partial_failure_are_not_success():
    class Stuck(PagedClient):
        def get_stock_kline(self, *args, **kwargs):
            return bars(700)

    with pytest.raises(ProviderError, match="no progress"):
        TdxmanProvider({"mac_client": Stuck(bars())}).get_minute(
            ["600519.SH"], "2026-09-01"
        )

    class Broken(PagedClient):
        def get_stock_kline(self, market, code, *args, **kwargs):
            if code == "000001":
                raise OSError("offline")
            return bars()

    plugin = TdxmanProvider({"mac_client": Broken(bars())})
    with pytest.raises(ProviderError, match="000001"):
        plugin.get_minute(["600519.SH", "000001.SZ"])
    assert "error" in plugin.test_dataset("minute", ["000001.SZ"])


def test_empty_and_optional_amount_and_index_unit_gate():
    plugin = TdxmanProvider({"mac_client": PagedClient(bars(0))})
    assert plugin.get_minute([]).schema == empty_minute().schema
    assert plugin.get_minute(["600519.SH"]).schema == empty_minute().schema
    frame = normalize_minute(bars().drop(columns="amount"), "stock",
                             symbol="600519.SH", start=None, end=None)
    assert frame["amount"].null_count() == 5
    with pytest.raises(ProviderError, match="not been verified"):
        plugin.get_minute(["000001.SH"], asset_type="index")


def test_current_day_count_uses_small_request_and_filters_old_day():
    today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    frame = bars()
    frame["datetime"] = pd.date_range(today + pd.Timedelta(hours=9, minutes=31),
                                      periods=5, freq="min")
    client = PagedClient(frame)
    plugin = TdxmanProvider({"mac_client": client})
    assert plugin.get_intraday_batch(["600519.SH"], count=3).height == 3
    assert client.calls[0][4] == 3
    frame["datetime"] -= pd.Timedelta(days=1)
    assert plugin.get_intraday_batch(["600519.SH"]).is_empty()
    assert not callable(plugin.get_intraday_latest)


def test_registration_does_not_connect_and_exposes_five_datasets(monkeypatch):
    monkeypatch.setenv("TDXMAN_TICK_STOCK_PANEL_MODE", "online")
    import tdxman.client
    monkeypatch.setattr(tdxman.client.TdxClient, "connect",
                        lambda self: pytest.fail("Registration must not connect"))
    plugin = TdxmanProvider()
    assert availability()[0]
    assert set(plugin.config.datasets) == {"daily", "minute", "full_minute", "realtime", "depth5"}
    for method in ("get_daily", "iter_daily", "get_minute", "get_intraday_batch",
                   "get_realtime", "get_realtime_indices", "get_depth_batch",
                   "get_instruments", "test_dataset", "close"):
        assert callable(getattr(plugin, method))
    plugin.close()
    plugin.close()
    assert plugin.get_realtime() == []
    assert plugin.get_realtime_indices(["000001.SH"]) is None


def test_transport_retries_reuses_and_closes():
    created = []

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            created.append(self)

        def connect(self):
            if len(created) < 3:
                raise TdxConnectionError("retry")

        def quote(self):
            return 42

        def close(self):
            self.closed = True

    client = ManagedClient(Client, backoff=0)
    with ThreadPoolExecutor(max_workers=3) as executor:
        assert list(executor.map(lambda _: client.quote(), range(5))) == [42] * 5
    assert len(created) == 3
    assert all(c.closed for c in created[:2])
    client.close()
    client.close()
    assert created[-1].closed
    with pytest.raises(ProviderError, match="closed"):
        client.quote()


def test_json_preview_and_bad_depth_are_explicit():
    plugin = TdxmanProvider({"mac_client": PagedClient(bars())})
    preview = plugin._test_result("minute", [{"date": datetime(2026, 9, 21),
                                             "amount": float("nan")}], ["date", "amount"])
    assert preview["preview"][0]["date"] == "2026-09-21T00:00:00"
    assert preview["preview"][0]["amount"] is None
    json.dumps(preview, allow_nan=False)


def test_result_limit_and_request_deadline(monkeypatch):
    from tdxman.adapters.tick_stock_panel import diagnostics

    client = PagedClient(bars())
    plugin = TdxmanProvider({"mac_client": client, "max_result_rows": 2})
    with pytest.raises(ProviderError, match="row limit"):
        plugin.get_minute(["600519.SH"])
    plugin = TdxmanProvider({"mac_client": client, "request_timeout": 1})
    times = iter([0, 2, 3])
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: next(times))
    with pytest.raises(ProviderError, match="deadline"):
        plugin.get_minute(["600519.SH"])


def test_transport_exhaustion_closes_every_failed_connection():
    created = []

    class Broken:
        def __init__(self, **kwargs):
            self.closed = False
            created.append(self)

        def connect(self):
            raise TdxConnectionError("offline")

        def close(self):
            self.closed = True

    client = ManagedClient(Broken, retries=2, backoff=0)
    with pytest.raises(ProviderError, match="retries exhausted"):
        client.quote()
    assert len(created) == 3
    assert all(connection.closed for connection in created)


def test_directory_pagination_rejects_repeated_page():
    from tdxman.adapters.tick_stock_panel.universe import get_mac_instruments

    class Directory:
        def get_stock_quotes_list(self, *args, **kwargs):
            return pd.DataFrame({"code": [f"{i:06}" for i in range(80)],
                                 "market": [1] * 80, "name": ["test"] * 80})

    with pytest.raises(ProviderError, match="drifted"):
        get_mac_instruments(Directory(), "etf", limit=1000)


@pytest.mark.parametrize("bad", [float("inf"), -1, None])
def test_depth_rejects_invalid_levels(bad):
    from tests.unit.test_tick_stock_panel_provider import FakeMacClient
    client = FakeMacClient()
    raw = client.get_stock_quotes([(1, "600519")]).to_dict("records")[0]
    raw["bid_price"] = bad
    with pytest.raises(ProviderError):
        TdxmanProvider._normalize_depth_row(raw)


def test_timestamp_missing_never_becomes_current_time():
    from tdxman.adapters.tick_stock_panel.normalize import _mac_quote_timestamp
    assert _mac_quote_timestamp(None, 150000) is None
    assert _mac_quote_timestamp(20260921, float("nan")) is None
    assert _mac_quote_timestamp(20260921, 150000) == 1789974000000


def test_quote_batches_have_eighty_symbol_limit():
    class Quotes:
        def __init__(self):
            self.calls = []

        def get_stock_quotes(self, stocks, fields):
            self.calls.append(stocks)
            return pd.DataFrame([{
                "market": market, "code": code, "pre_close": 10,
                "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100,
            } for market, code in stocks])

    quotes = Quotes()
    plugin = TdxmanProvider({"mac_client": quotes})
    symbols = [f"{i:06}.SH" for i in range(81)]
    rows = plugin.get_realtime_indices(symbols)
    assert len(rows) == 81
    assert [len(batch) for batch in quotes.calls] == [80, 1]
    assert all(row["timestamp"] is None for row in rows)
