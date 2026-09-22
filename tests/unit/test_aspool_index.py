import asyncio
import json
import struct
from datetime import date
from unittest.mock import patch

import pandas as pd
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from aspool.cli import cli
from aspool.index_lists import collect, difference
from aspool.index_pool import normalize, offline_records, online_records, sync_indices

ITEM = {"market": "SH", "code": "881001", "name": "煤炭", "source": ["HY"]}


def bar(day="2010-01-04", close=10):
    return {
        "date": day,
        "open": 10,
        "high": 11,
        "low": 9,
        "close": close,
        "vol": 200,
        "amount": 2000,
        "up_count": 3,
        "down_count": 4,
    }


def test_index_pool_isolated_full_history_and_idempotent(tmp_path):
    stock = tmp_path / "lake/bars/daily/keep"
    stock.parent.mkdir(parents=True)
    stock.write_text("stock untouched")
    with patch("aspool.index_pool.offline_records", return_value=[bar("2005-01-04"), bar()]):
        report, _ = sync_indices(tmp_path, mode="offline", items=[ITEM])
        again, _ = sync_indices(tmp_path, mode="offline", items=[ITEM])
    assert report["success"][0]["added"] == 2
    assert again["success"][0]["unchanged"] == 2
    assert again["success"][0]["added"] == 0
    assert stock.read_text() == "stock untouched"
    assert report["success"][0]["start"] == "2005-01-04"
    path = next((tmp_path / "lake/indices").rglob("*.parquet"))
    assert pq.ParquetFile(path).read()["volume"].to_pylist() == [200, 200]


def test_merge_revisions_and_failure_preserve_prior(tmp_path):
    with patch("aspool.index_pool.offline_records", return_value=[bar()]):
        sync_indices(tmp_path, mode="offline", items=[ITEM])
    with patch("aspool.index_pool.offline_records", return_value=[bar(close=11)]):
        report, _ = sync_indices(tmp_path, mode="offline", items=[ITEM])
    assert report["success"][0]["changed"] == 1
    with patch("aspool.index_pool.offline_records", return_value=[bar(), bar()]):
        report, path = sync_indices(tmp_path, mode="offline", items=[ITEM])
    assert len(report["failed"]) == 1
    assert len(json.loads(path.read_text())["failed"]) == 1
    stored = next((tmp_path / "lake/indices").rglob("*.parquet"))
    assert pq.ParquetFile(stored).read()["close"].to_pylist() == [11]


def test_offline_breadth_bytes(tmp_path):
    path = tmp_path / "sh881001.day"
    path.write_bytes(struct.pack("<IIIIIfIHH", 20100104, 1000, 1100, 900, 1000, 2000, 200, 3, 4))
    with patch("aspool.index_pool.find_daily_bar_file", return_value=path):
        result = normalize(offline_records(ITEM), ITEM)[0]
    assert result["trade_date"] == date(2010, 1, 4)
    assert result["volume"] == 200
    assert (result["up_count"], result["down_count"]) == (3, 4)
    path.write_bytes(b"invalid")
    with patch("aspool.index_pool.find_daily_bar_file", return_value=path):
        with pytest.raises(ValueError):
            offline_records(ITEM)


def test_online_paginates_short_pages_and_rejects_repeating_page():
    class Client:
        def get_index_bars(self, market, code, category, start, count):
            assert count == 800
            return pd.DataFrame(
                [bar("2010-01-04" if start == 0 else "2005-01-04")] if start < 2 else []
            )

    assert len(asyncio.run(online_records(Client(), ITEM))) == 2

    class Repeating:
        def get_index_bars(self, *args):
            return pd.DataFrame([bar()])

    with pytest.raises(ValueError, match="分页"):
        asyncio.run(online_records(Repeating(), ITEM))


def test_async_pagination():
    class Client:
        async def get_index_bars(self, market, code, category, start, count):
            return pd.DataFrame([bar()] if start == 0 else [])

    assert len(asyncio.run(online_records(Client(), ITEM, True))) == 1


def test_missing_breadth_zero():
    record = bar()
    record.pop("up_count")
    record["down_count"] = None
    result = normalize([record], ITEM)[0]
    assert result["up_count"] == result["down_count"] == 0


@pytest.mark.parametrize("args", [["--source", "free-stockdb"], ["--period", "minutes"]])
def test_index_rejects_source_and_period_before_creating_pool(tmp_path, args):
    root = tmp_path / "unused"
    result = CliRunner().invoke(cli, ["sync", "--type", "index", "--root", str(root), *args])
    assert result.exit_code == 2
    assert not root.exists()


def test_list_blacklist_sources_and_changes():
    class Client:
        def get_board_list(self, *args, **kwargs):
            return pd.DataFrame(
                [
                    {"market": 1, "code": "881001", "name": "煤炭"},
                    {"market": 1, "code": "880000", "name": "昨日涨停"},
                ]
            )

        def get_stock_quotes_list(self, *args, **kwargs):
            return pd.DataFrame([{"market": 1, "code": "000300", "name": "沪深300"}])

    with patch("aspool.index_lists.COMMON_INDICES", {("SH", "000300")}):
        result, excluded = collect(Client())
    assert len(result["indices"]) == 2
    assert len(excluded) == 4
    coal = next(r for r in result["indices"] if r["code"] == "881001")
    assert coal["source"] == ["HY", "HY2", "GN", "FG"]
    assert difference(result, result)["changed"] == []
    renamed = json.loads(json.dumps(result))
    renamed["indices"][0]["name"] = "renamed"
    assert len(difference(result, renamed)["changed"]) == 1


def test_fundwise_index_api_isolated_read_only_filters_and_projection(tmp_path):
    from aspool import DataPool, DataPoolError

    other = {**ITEM, "market": "SZ"}
    with patch("aspool.index_pool.offline_records", return_value=[bar("2005-01-04"), bar()]):
        sync_indices(tmp_path, mode="offline", items=[ITEM, other])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    pool = DataPool(tmp_path)
    frame = pool.read_index_daily(
        symbols=["SH.881001"],
        end="2009-12-31",
        lookback=1,
        fields=["symbol", "date", "close", "up_count"],
    )
    # 旧点号输入仍被接受，但输出统一为规范格式。
    assert frame.symbol.tolist() == ["881001.SH"]
    assert frame.date.dt.year.tolist() == [2005]
    # 规范输入与旧输入返回相同记录。
    canonical = pool.read_index_daily(
        symbols=["881001.SH"],
        end="2009-12-31",
        lookback=1,
        fields=["symbol", "date", "close", "up_count"],
    )
    assert canonical.symbol.tolist() == ["881001.SH"]
    assert canonical.close.tolist() == frame.close.tolist()
    assert frame.attrs["volume_unit"] == "tdx_index_volume"
    assert len(pool.list_indices()) == 2
    assert pool.read_index_daily(symbols=[]).empty
    assert pool.describe()["capabilities"]["index_daily"]
    assert pool.status()["status"] == "empty"
    with pytest.raises(DataPoolError, match="No daily"):
        pool.read_daily()
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_index_duplicates_cannot_be_hidden_by_lookback(tmp_path):
    from aspool import DataPool, DataPoolError

    with patch("aspool.index_pool.offline_records", return_value=[bar()]):
        sync_indices(tmp_path, mode="offline", items=[ITEM])
    path = next((tmp_path / "lake/indices").rglob("*.parquet"))
    table = pq.ParquetFile(path).read()
    import pyarrow as pa

    pq.write_table(pa.concat_tables([table, table]), path)
    with pytest.raises(DataPoolError) as exc:
        DataPool(tmp_path).read_index_daily(lookback=1, fields=["close"])
    assert exc.value.code == "INDEX_INVALID"


def test_stock_default_still_uses_existing_pipeline(tmp_path):
    from aspool.store import initialize, record_coverage

    initialize(tmp_path)
    record_coverage(tmp_path, "000001", "SZ", date(2010, 1, 4), date(2010, 1, 4), 1, "test")
    with (
        patch("aspool.cli.update_online", return_value=(1, 30)) as stock,
        patch("aspool.index_pool.sync_indices") as index,
    ):
        result = CliRunner().invoke(cli, ["sync", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    stock.assert_called_once_with(tmp_path, "daily", False, None, workers=4)
    index.assert_not_called()


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"lookback": 0}, "INVALID_ARGUMENT"),
        ({"symbols": "000300"}, "INVALID_ARGUMENT"),
        ({"start": "bad-date"}, "INVALID_ARGUMENT"),
        ({"fields": ["close", "close"]}, "FIELD_UNSUPPORTED"),
        ({"fields": ["eps"]}, "FIELD_UNSUPPORTED"),
    ],
)
def test_index_api_invalid_parameters(tmp_path, kwargs, code):
    from aspool import DataPool, DataPoolError

    with pytest.raises(DataPoolError) as exc:
        DataPool(tmp_path).read_index_daily(**kwargs)
    assert exc.value.code == code


def test_cli_help_index_options_and_examples():
    result = CliRunner().invoke(cli, ["sync", "--help"], prog_name="aspool")
    assert result.exit_code == 0
    assert "--type [stock|index]" in result.output
    assert "aspool sync --type index --source tdx --period daily" in result.output
    assert "('--type'" not in result.output


def test_async_decode_error_retries_another_host():
    from tdxman.exceptions import TdxDecodeError

    class Broken:
        async def get_index_bars(self, *args):
            raise TdxDecodeError("bad server response")

    class Alternative:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_index_bars(self, market, code, category, start, count):
            return pd.DataFrame([bar()] if start == 0 else [])

    with (
        patch("aspool.index_pool.get_known_hosts", return_value=["test"]),
        patch("aspool.index_pool.AsyncTdxClient", return_value=Alternative()),
    ):
        rows = asyncio.run(online_records(Broken(), ITEM, True))
    assert len(rows) == 1


def test_bad_source_row_quarantined_while_good_history_kept(tmp_path):
    with patch("aspool.index_pool.offline_records", return_value=[bar(), bar("1995-03-01", 8)]):
        report, _ = sync_indices(tmp_path, mode="offline", items=[ITEM])
    result = report["success"][0]
    assert result["accepted"] == 1
    assert result["fetched"] == 2
    assert result["rejected"][0]["date"] == "1995-03-01"
    assert result["rows"] == 1


def test_sync_invalid_server_date_retries_another_host():
    class Broken:
        def get_index_bars(self, *args):
            raise ValueError("month must be in 1..12: 0-00-00")

    class Alternative:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_index_bars(self, market, code, category, start, count):
            return pd.DataFrame([bar()] if start == 0 else [])

    with (
        patch("aspool.index_pool.get_known_hosts", return_value=["test"]),
        patch("aspool.index_pool.TdxClient", return_value=Alternative()),
    ):
        rows = asyncio.run(online_records(Broken(), ITEM))
    assert len(rows) == 1


def test_incremental_stops_at_overlap_and_pages_across_long_gap():
    calls = []
    dates = pd.date_range("2026-07-01", periods=80, freq="D")[::-1]

    class Client:
        def get_index_bars(self, market, code, category, start, count):
            calls.append((start, count))
            return pd.DataFrame([bar(str(d.date())) for d in dates[start : start + count]])

    since = dates[44].date()
    rows = asyncio.run(online_records(Client(), ITEM, since=since))
    assert calls == [(0, 30), (30, 30)]
    assert len(rows) == 45
    assert min(r["date"] for r in rows) == str(since)


def test_existing_index_only_refreshes_tail_and_preserves_old_history(tmp_path):
    old = [bar("2005-01-04")] + [bar(f"2026-09-{day:02}") for day in range(1, 11)]
    with patch("aspool.index_pool.offline_records", return_value=old):
        first, _ = sync_indices(tmp_path, mode="offline", items=[ITEM])
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_index_bars(self, market, code, category, start, count):
            calls.append((start, count))
            return pd.DataFrame([bar(f"2026-09-{day:02}", close=11) for day in range(1, 12)])

    with patch("aspool.index_pool.TdxClient.from_best_host", return_value=Client()):
        report, _ = sync_indices(tmp_path, items=[ITEM])
    result = report["success"][0]
    assert first["success"][0]["sync_scope"] == "bootstrap"
    assert calls == [(0, 30)]
    assert result["sync_scope"] == "incremental"
    assert result["overlap_start"] == "2026-09-06"
    assert result["added"] == 1 and result["changed"] == 5
    assert result["fetched"] == 6 and result["rows"] == 12
    stored = pq.ParquetFile(next((tmp_path / "lake/indices").rglob("*.parquet"))).read().to_pylist()
    assert stored[0]["trade_date"] == date(2005, 1, 4)
    assert stored[1]["close"] == 10


def test_async_incremental_stops_after_recent_page():
    calls = []

    class Client:
        async def get_index_bars(self, market, code, category, start, count):
            calls.append((start, count))
            return pd.DataFrame([bar("2026-09-16"), bar("2026-09-17")])

    rows = asyncio.run(online_records(Client(), ITEM, True, since=date(2026, 9, 16)))
    assert len(rows) == 2 and calls == [(0, 30)]
