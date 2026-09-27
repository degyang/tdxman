import asyncio
import json
import threading
import time
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import pyarrow.parquet as pq
import pytest

from aspool.daily_storage import build_field_quality_report, merge_daily
from aspool.fetch import client_factory, fetch_async, fetch_sync
from aspool.free_stockdb import _write_daily
from aspool.fundamentals import _quote_bar, update_from_quotes
from aspool.store import bars_path, catalog, catalog_session, initialize, record_coverage
from aspool.tdx_online import _stock_records, _stock_records_async
from aspool.universe import _publish_universe


def bar(day, close=10):
    return dict(
        symbol="000001",
        trade_date=day,
        open=10.0,
        high=11.0,
        low=9.0,
        close=float(close),
        volume=100.0,
        amount=1000.0,
        vol_ratio=1.0,
    )


def setup_pool(root):
    initialize(root)
    rows = [bar(date(2026, 9, d)) for d in range(1, 18)]
    for r in rows:
        r["pe_ttm"] = 8.25
        r["is_st"] = None
        r["pb"] = None
    _write_daily(root, "SZ", "000001", rows)
    record_coverage(
        root, "000001", "SZ", rows[0]["trade_date"], rows[-1]["trade_date"], len(rows), "test"
    )
    return bars_path(root, "daily", "SZ", "000001")


def test_tail_merge_preserves_history_and_skips_unchanged_file(tmp_path):
    path = setup_pool(tmp_path)
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert merge_daily(tmp_path, "SZ", "000001", [bar(date(2026, 9, 17))], "test") == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert (
        merge_daily(
            tmp_path, "SZ", "000001", [bar(date(2026, 9, 17), 11), bar(date(2026, 9, 18))], "test"
        )
        == 2
    )
    rows = pq.ParquetFile(path).read().to_pylist()
    assert len(rows) == 18 and rows[0]["close"] == 10 and rows[-2]["close"] == 11
    assert rows[0]["pe_ttm"] == 8.25 and rows[-2]["pe_ttm"] == 8.25
    assert rows[-1]["pe_ttm"] is None


def test_quote_missing_scalars_reach_merge_entry_without_erasing_reliable_fields(tmp_path):
    path = setup_pool(tmp_path)
    rows = pq.ParquetFile(path).read().to_pylist()
    rows[-1]["pre_close"] = 10.0
    rows[-1]["is_st"] = False
    _write_daily(tmp_path, "SZ", "000001", rows)

    quote_source = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "test",
                "pre_close": pd.NA,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "vol": 1.0,
                "amount": 1000.0,
            }
        ]
    ).to_dict(orient="records")[0]
    quote = _quote_bar(
        quote_source,
        date(2026, 9, 17),
    )
    quality = []
    assert merge_daily(
        tmp_path,
        "SZ",
        "000001",
        [quote | {"is_st": float("nan")}],
        "test",
        quality_rows=quality,
    ) == 1
    stored = pq.ParquetFile(path).read().to_pylist()[-1]
    assert stored["pre_close"] == 10.0 and stored["is_st"] is False
    assert quality[0]["quality"] == {
        "pre_close": "valid",
        "is_st": "valid",
        "joint_valid": True,
    }


def test_daily_merge_quality_distinguishes_missing_invalid_and_correction(tmp_path):
    path = setup_pool(tmp_path)
    rows = pq.ParquetFile(path).read().to_pylist()
    rows[-1]["pre_close"] = 10.0
    rows[-1]["is_st"] = False
    _write_daily(tmp_path, "SZ", "000001", rows)
    quality = []

    incoming = bar(date(2026, 9, 18), 11.0)
    incoming.update(pre_close=float("nan"), is_st=pd.NA)
    assert merge_daily(tmp_path, "SZ", "000001", [incoming], "test", quality_rows=quality) == 1
    assert quality[-1]["quality"] == {
        "pre_close": "missing",
        "is_st": "missing",
        "joint_valid": False,
    }

    invalid = bar(date(2026, 9, 19), 11.0)
    invalid.update(pre_close=-1.0, is_st="False")
    assert merge_daily(tmp_path, "SZ", "000001", [invalid], "test", quality_rows=quality) == 1
    assert quality[-1]["quality"] == {
        "pre_close": "missing",
        "is_st": "missing",
        "joint_valid": False,
    }
    assert quality[-1]["incoming_invalid"] == {"pre_close": 1, "is_st": 1}

    old_value_invalid = bar(date(2026, 9, 17), 10.0)
    old_value_invalid.update(pre_close=-1.0, is_st="False")
    assert (
        merge_daily(
            tmp_path, "SZ", "000001", [old_value_invalid], "test", quality_rows=quality
        )
        == 0
    )
    assert quality[-1]["quality"] == {
        "pre_close": "valid",
        "is_st": "valid",
        "joint_valid": True,
    }
    assert quality[-1]["incoming_invalid"] == {"pre_close": 1, "is_st": 1}

    report = build_field_quality_report(quality, "stock", "test")
    by_date = {row["trade_date"]: row for row in report["rows"]}
    assert by_date["2026-09-17"]["joint_valid"] == 1
    assert by_date["2026-09-17"]["incoming_invalid"] == {"pre_close": 1, "is_st": 1}
    assert by_date["2026-09-19"]["pre_close"]["missing"] == 1
    assert by_date["2026-09-19"]["incoming_invalid"] == {"pre_close": 1, "is_st": 1}

    correction = bar(date(2026, 9, 17), 12.0)
    correction.update(pre_close=11.0, is_st=True)
    assert merge_daily(tmp_path, "SZ", "000001", [correction], "test") == 1
    corrected = next(
        row
        for row in pq.ParquetFile(path).read().to_pylist()
        if row["trade_date"] == date(2026, 9, 17)
    )
    assert corrected["pre_close"] == 11.0 and corrected["is_st"] is True
    assert merge_daily(tmp_path, "SZ", "000001", [correction], "test") == 0


@pytest.mark.parametrize(
    "incoming",
    [[], [bar(date(2026, 9, 17))] * 2, [{**bar(date(2026, 9, 17)), "volume": float("nan")}]],
)
def test_bad_tail_never_replaces_file(tmp_path, incoming):
    path = setup_pool(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        merge_daily(tmp_path, "SZ", "000001", incoming, "test")
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "server_date,now,reason",
    [
        (20260916, datetime(2026, 9, 17, 17), "stale_quote"),
        (20260917, datetime(2026, 9, 18, 17), "quote_name_date_unconfirmed"),
        (None, datetime(2026, 9, 18, 8), "missing_or_invalid_date"),
        (20260919, datetime(2026, 9, 17, 17), "invalid_trading_date"),
    ],
)
def test_invalid_quote_date_reported_without_writing(tmp_path, server_date, now, reason):
    path = setup_pool(tmp_path)
    before = path.read_bytes()
    quote = {
        "code": "000001",
        "market": 0,
        "name": "test",
        "open": 8.0,
        "high": 9.0,
        "low": 7.0,
        "close": 8.0,
        "pre_close": 7.0,
        "vol": 1,
        "amount": 800.0,
        "server_update_date": server_date,
    }

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_stock_quotes(self, *args):
            return pd.DataFrame([quote])

    with patch("tdxman.mac.client.MacClient.from_best_host", return_value=Client()):
        with pytest.raises(ValueError, match="部分报价"):
            update_from_quotes(tmp_path, now=now)
    assert path.read_bytes() == before
    report = json.loads((tmp_path / "reports/maintenance/latest.json").read_text())
    assert report["rejected"][0]["reason"] == reason


def test_connection_session_reuses_connection(tmp_path):
    initialize(tmp_path)
    with catalog_session(tmp_path) as first:
        with catalog(tmp_path) as second:
            assert second is first
        assert first.execute("SELECT 1").fetchone() == (1,)


def test_cached_host_is_used_first_and_refreshed_once_after_failure():
    calls = []

    class Client:
        @classmethod
        def from_best_host(cls, **kwargs):
            calls.append(kwargs)
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    cached, retry = client_factory(Client, 4)
    with cached():
        pass
    with retry():
        pass
    with retry():
        pass
    assert calls == [
        {"refresh": False, "heartbeat_interval": 0},
        {"heartbeat_interval": 0},
        {"refresh": False, "heartbeat_interval": 0},
    ]


def test_coverage_batch_writes_all_entries_in_one_catalog(tmp_path):
    from aspool.store import record_coverages

    initialize(tmp_path)
    record_coverages(
        tmp_path,
        [
            ("000001", "SZ", date(2026, 9, 16), date(2026, 9, 17), 2, "test", "daily"),
            ("600519", "SH", date(2026, 9, 16), date(2026, 9, 17), 2, "test", "daily"),
        ],
    )
    with catalog(tmp_path) as conn:
        assert conn.execute("select count(*) from coverage").fetchone() == (2,)


def test_universe_preserves_omitted_securities_and_coverage(tmp_path):
    initialize(tmp_path)
    record_coverage(tmp_path, "000001", "SZ", date(2026, 9, 16), date(2026, 9, 17), 2, "test")
    first = _publish_universe(
        tmp_path,
        [
            {"symbol": "000001", "market": "SZ", "name": "甲"},
            {"symbol": "600519", "market": "SH", "name": "乙"},
        ],
    )
    assert [item["symbol"] for item in first["added"]] == ["600519"]
    second = _publish_universe(tmp_path, [{"symbol": "000001", "market": "SZ", "name": "甲"}])
    assert second["inactive"] == []
    with catalog(tmp_path) as conn:
        assert conn.execute("SELECT active FROM universe WHERE symbol='600519'").fetchone() == (
            True,)
    with catalog(tmp_path) as conn:
        assert conn.execute("select count(*) from coverage").fetchone() == (1,)


def test_sync_workers_are_bounded_and_connections_never_shared():
    lock = threading.Lock()
    active = maximum = 0
    owners = []

    class Client:
        def __enter__(self):
            self.owner = threading.get_ident()
            owners.append(self.owner)
            return self

        def __exit__(self, *args):
            pass

    def request(client, item):
        nonlocal active, maximum
        assert client.owner == threading.get_ident()
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return item * 2

    result = list(fetch_sync(range(12), Client, request, 3))
    assert sorted((i, r) for i, r, e, t in result if e is None) == [(i, i * 2) for i in range(12)]
    assert 1 < maximum <= 3 and len(set(owners)) <= 3


def test_async_workers_and_cancellation_close_all_connections():
    async def exercise():
        active = 0
        opened = closed = 0

        class Client:
            async def __aenter__(self):
                nonlocal opened
                opened += 1
                self.busy = False
                return self

            async def __aexit__(self, *args):
                nonlocal closed
                closed += 1

        async def request(client, item):
            nonlocal active
            assert not client.busy
            client.busy = True
            active += 1
            await asyncio.sleep(0.005)
            active -= 1
            client.busy = False
            return item

        stream = fetch_async(list(range(100)), Client, request, 3)
        async for entry in stream:
            assert entry[2] is None
            break
        await stream.aclose()
        assert opened == closed == 3

    asyncio.run(asyncio.wait_for(exercise(), 2))


def test_async_fetch_times_out_and_retries_without_blocking():
    async def exercise():
        opened = 0

        class Client:
            async def __aenter__(self):
                nonlocal opened
                opened += 1
                self.number = opened
                return self

            async def __aexit__(self, *args):
                return None

        async def request(client, _item):
            await asyncio.sleep(1)
            return client.number

        stream = fetch_async(
            [1],
            Client,
            request,
            workers=1,
            retry_factory=Client,
            request_timeout=0.01,
            max_retries=2,
            retry_backoff=0,
        )
        started = time.monotonic()
        entries = [entry async for entry in stream]
        assert time.monotonic() - started < 0.5
        assert len(entries) == 1
        assert entries[0][1] is None
        assert "异步请求超时" in str(entries[0][2])
        assert opened == 3

    asyncio.run(asyncio.wait_for(exercise(), 2))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_stock_sync_pages_across_gap(asynchronous):
    days = pd.date_range("2026-07-01", periods=80)[::-1]
    calls = []

    def frame(start, count):
        calls.append((start, count))
        return pd.DataFrame(
            [
                dict(datetime=d, vol=100, open=10, high=11, low=9, close=10, amount=1000)
                for d in days[start : start + count]
            ]
        )

    class Sync:
        def get_stock_kline(self, *args, **kw):
            return frame(kw["start"], kw["count"])

    class Async:
        async def get_stock_kline(self, *args, **kw):
            return frame(kw["start"], kw["count"])

    since = days[44].date()
    rows = (
        asyncio.run(_stock_records_async(Async(), ("000001", since)))
        if asynchronous
        else _stock_records(Sync(), ("000001", since))
    )
    assert len(rows) == 45 and calls == [(0, 30), (30, 30)]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_new_stock_bootstrap_uses_longest_available_history(asynchronous):
    days = pd.date_range("2024-01-01", periods=900)[::-1]
    calls = []

    def frame(start, count):
        calls.append((start, count))
        return pd.DataFrame(
            [
                dict(datetime=d, vol=100, open=10, high=11, low=9, close=10, amount=1000)
                for d in days[start : start + count]
            ]
        )

    class Sync:
        def get_stock_kline(self, *args, **kwargs):
            return frame(kwargs["start"], kwargs["count"])

    class Async:
        async def get_stock_kline(self, *args, **kwargs):
            return frame(kwargs["start"], kwargs["count"])

    rows = (
        asyncio.run(_stock_records_async(Async(), ("000001", None)))
        if asynchronous
        else _stock_records(Sync(), ("000001", None))
    )
    assert len(rows) == 900 and calls == [(0, 800), (800, 800)]
