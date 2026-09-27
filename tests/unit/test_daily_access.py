"""DG-02 consumer compatibility, exact projections and physical access bounds."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from aspool import DataPool, DataPoolError
from aspool.baostock_source import _rows
from aspool.cli import cli
from aspool.daily_access import DailyStorage
from aspool.daily_storage import merge_daily
from aspool.enrichment import _publish
from aspool.free_stockdb import _write_daily, validate_daily
from aspool.limit_events import _read_symbol_bars, initialize_limits, load_scope
from aspool.security_facts import initialize_facts
from aspool.store import catalog, initialize


def bar(day, code="000001", **extra):
    return (
        dict(
            trade_date=day,
            symbol=code,
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.0,
            volume=100.0,
            amount=1000.0,
            pre_close=10.0,
            is_st=False,
            name="测试",
            turnover_rate=0.43,
            extension="preserved",
        )
        | extra
    )


def write(root, rows, code="000001", yearly=False):
    storage = DailyStorage(root)
    years = sorted({r["trade_date"].year for r in rows}) if yearly else [None]
    for year in years:
        path = storage.target("SZ", code, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        subset = [r for r in rows if year is None or r["trade_date"].year == year]
        pq.write_table(pa.Table.from_pylist(subset), path)


@pytest.fixture(params=[False, True], ids=["legacy", "yearly"])
def pool(tmp_path, request):
    initialize(tmp_path)
    initialize_facts(tmp_path)
    initialize_limits(tmp_path)
    days = [date(2025, 12, 29) + timedelta(days=i) for i in range(10)]
    write(tmp_path, [bar(d) for d in days], yearly=request.param)
    write(tmp_path, [bar(d, "159915", asset_type="etf") for d in days], "159915", request.param)
    return tmp_path, days, request.param


def test_public_research_etf_status_units_fields_and_index_separation(pool):
    root, days, _ = pool
    path = root / "lake/indices/daily/market=SH/symbol=000001/bars.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([bar(days[0])]), path)
    api = DataPool(root)
    full = api.read_daily(symbols="SZ.000001")
    research = api.read_research_daily(symbols="000001.SZ", start=days[2], end=days[-2])
    assert len(full) == len(days) and len(research) == len(days) - 3
    assert full.volume.tolist() == [100.0] * len(days)
    assert full.amount.tolist() == [1000.0] * len(days)
    assert full.turnover_rate.tolist() == [0.43] * len(days)
    assert full.attrs["price_adjustment"] == "raw"
    assert full.attrs["point_in_time"] is False
    etf = api.read_etf_daily(symbols="159915.SZ", end=days[5], lookback=2)
    assert etf.date.dt.date.tolist() == days[4:6]
    assert etf.attrs["volume_unit"] == "share"
    assert api.list_etfs().row_count.tolist() == [len(days)]
    status = api.status()
    assert status["row_count"] == status["etf_row_count"] == len(days)
    assert status["symbol_count"] == status["etf_symbol_count"] == 1
    assert {e.symbol for e in load_scope(root)} == {"000001.SZ"}
    assert len(_read_symbol_bars(root, "SZ", "000001")) == len(days)


def test_single_symbol_uses_no_pool_inventory_and_exact_field_range(pool, monkeypatch):
    root, days, _ = pool

    def forbidden(*args, **kwargs):
        pytest.fail("a known single-symbol read must not enumerate pool identities")

    monkeypatch.setattr(DailyStorage, "identities", forbidden)
    monkeypatch.setattr(DailyStorage, "has_data", forbidden)
    api = DataPool(root)
    assert len(api.read_daily(symbols="000001.SZ", fields=["amount"])) == len(days)
    assert len(api.read_etf_daily(symbols="159915.SZ")) == len(days)
    assert api.read_daily(symbols="000001.SZ", start="2030-01-01").empty
    storage = DailyStorage(root)
    table = storage.read("SZ", "000001", days[2], days[4], fields=["amount"])
    assert table.column_names == ["amount"] and table.num_rows == 3
    batches = list(storage.batches("SZ", "000001", ["amount"], days[2], days[4]))
    assert sum(len(b) for b in batches) == 3
    assert all(b.schema.names == ["amount"] for b in batches)
    assert storage.revisions("SZ", "000001")


def test_absent_symbols_empty_selection_and_outside_ranges_keep_contract(pool):
    root, _, _ = pool
    api = DataPool(root)
    for symbols in ([], ["000099.SZ"]):
        result = api.read_daily(symbols=symbols)
        assert result.empty and {"symbol", "date", "amount"} <= set(result)
    assert api.read_etf_daily(symbols=[]).empty
    assert api.read_daily(start="2030-01-01").empty
    assert api.read_etf_daily(start="2030-01-01").empty


def test_dated_fact_overlay_wins_in_yearly_and_legacy(pool):
    root, days, _ = pool
    with catalog(root) as conn:
        conn.execute(
            "INSERT INTO security_daily_facts "
            "(symbol, trade_date, pre_close, is_st, source) VALUES (?, ?, ?, ?, ?)",
            ["000001.SZ", days[3], 9.5, True, "baostock"],
        )
    frame = DataPool(root).read_daily(symbols="000001.SZ", start=days[3], end=days[3])
    assert frame.pre_close.tolist() == [9.5]
    assert frame.is_st.tolist() == [True]
    assert frame.pre_close_source.tolist() == ["baostock"]


def test_sparse_event_amount_missing_rows_and_nulls(pool):
    root, days, _ = pool
    with catalog(root) as conn:
        for day in (days[0], days[-1], days[-1] + timedelta(days=1)):
            batch = day.isoformat()
            conn.execute(
                "INSERT INTO daily_limit_batches "
                "(batch_id,trade_date,scope_id,rule_version,computed_at,status,"
                "processed_count,known_count,unknown_count,no_limit_count) "
                "VALUES (?,?,'stock','v8',?,'published',1,1,0,0)",
                [batch, day, datetime.now()],
            )
            conn.execute(
                "INSERT INTO daily_limit_publication VALUES (?,?,?)", [day, batch, datetime.now()]
            )
            conn.execute(
                "INSERT INTO daily_limit_events (batch_id,trade_date,symbol) "
                "VALUES (?,?,'000001.SZ')",
                [batch, day],
            )
    with DataPool(root).iter_limit_events_with_amount(
        start=days[0], end=days[-1] + timedelta(days=1), batch_days=3
    ) as reader:
        frame = pd.concat(list(reader))
    assert reader.completed
    assert frame.amount.iloc[:2].tolist() == [1000.0, 1000.0]
    assert pd.isna(frame.amount.iloc[-1])
    assert not frame.duplicated(["symbol", "trade_date"]).any()


def test_import_repair_enrichment_and_cli_preserve_extension(pool):
    root, days, _ = pool
    stored = DailyStorage(root).read("SZ", "000001").to_pylist()
    stored[-1]["amount"] = 1200.0
    _write_daily(root, "SZ", "000001", stored)
    assert (
        _rows(root, "SZ", "000001", days[-1], days[-1], planning=True)[days[-1]]["amount"] == 1200.0
    )
    result = _publish(root, [("000001", "SZ", None)], days, days[0], days[-1])
    assert "error" not in result[0]
    table = DailyStorage(root).read("SZ", "000001")
    assert table["extension"].to_pylist() == ["preserved"] * len(days)
    assert validate_daily(root)["duplicates"] == 0
    response = CliRunner().invoke(
        cli,
        ["query", "SZ000001", "--root", str(root), "--format", "json", "--start", str(days[-1])],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)[0]["amount"] == 1200.0


def test_mixed_layout_rejected_by_public_and_internal_consumers(pool):
    root, days, _ = pool
    storage = DailyStorage(root)
    # Deliberately inject the alternate layout; no migration is performed by writers.
    path = storage.target("SZ", "000001", 2025 if not pool[2] else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([bar(days[0])]), path)
    for call in (lambda: DataPool(root).read_daily(), lambda: DataPool(root).status()):
        with pytest.raises(DataPoolError) as error:
            call()
        assert error.value.code == "DAILY_INVALID"
    with pytest.raises(ValueError, match="mixed legacy"):
        merge_daily(root, "SZ", "000001", [bar(days[-1])], "test")
    with pytest.raises(ValueError, match="mixed legacy"):
        _rows(root, "SZ", "000001", days[0], days[-1])


def test_duplicate_key_before_lookback_and_invalid_projected_values(pool):
    root, days, yearly = pool
    write(root, [bar(days[0]), bar(days[0]), bar(days[-1])], yearly=yearly)
    with pytest.raises(DataPoolError) as error:
        DataPool(root).read_daily(symbols="000001.SZ", lookback=1, fields=["symbol"])
    assert error.value.code == "DAILY_INVALID"
    write(root, [bar(days[0], amount=-1)], yearly=yearly)
    with pytest.raises(DataPoolError, match="Invalid"):
        DataPool(root).read_daily(
            symbols="000001.SZ", start=days[0], end=days[0], fields=["symbol"]
        )


def test_latest_year_merge_and_coverage_repair_do_not_decode_cold_years(tmp_path, monkeypatch):
    initialize(tmp_path)
    rows = [bar(date(2010, 1, 1)), bar(date(2018, 1, 1))]
    recent = [date(2026, 1, 1) + timedelta(days=i) for i in range(8)]
    rows += [bar(d) for d in recent]
    write(tmp_path, rows, yearly=True)
    cold = {DailyStorage(tmp_path).target("SZ", "000001", y) for y in (2010, 2018)}
    original = {p: p.read_bytes() for p in cold}
    real_parquet = pq.ParquetFile

    class GuardedParquet:
        def __init__(self, path, *args, **kwargs):
            self.path = Path(path)
            self.parquet = real_parquet(path, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.parquet, name)

        def read(self, *args, **kwargs):
            assert self.path not in cold, "cold year was decoded"
            return self.parquet.read(*args, **kwargs)

        def iter_batches(self, *args, **kwargs):
            assert self.path not in cold, "cold year was decoded"
            return self.parquet.iter_batches(*args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", GuardedParquet)
    assert merge_daily(tmp_path, "SZ", "000001", [bar(recent[-1], amount=1200)], "test") == 1
    with catalog(tmp_path) as conn:
        assert conn.execute("SELECT row_count FROM coverage").fetchone()[0] == len(rows)
        conn.execute("UPDATE coverage SET row_count=1")
    assert merge_daily(tmp_path, "SZ", "000001", [bar(recent[-1], amount=1200)], "test") == 0
    with catalog(tmp_path) as conn:
        assert conn.execute("SELECT row_count FROM coverage").fetchone()[0] == len(rows)
    assert {p: p.read_bytes() for p in cold} == original


def test_sparse_cross_year_dependencies_match_full_reference(tmp_path):
    root, reference = tmp_path / "yearly", tmp_path / "legacy"
    initialize(root)
    initialize(reference)
    days = [date(2022, 12, 31), date(2023, 12, 31), date(2024, 12, 30), date(2024, 12, 31)] + [
        date(2025, 12, 29) + timedelta(days=i) for i in range(10)
    ]
    rows = [bar(d, vol_ratio=1.0) for d in days]
    write(root, rows, yearly=True)
    write(reference, rows)
    update = [bar(date(2025, 12, 31), volume=200.0)]
    assert merge_daily(root, "SZ", "000001", update, "test") == merge_daily(
        reference, "SZ", "000001", update, "test"
    )
    assert (
        DailyStorage(root).read("SZ", "000001").to_pylist()
        == DailyStorage(reference).read("SZ", "000001").to_pylist()
    )


def test_wrong_partition_year_is_rejected_before_date_pruning(tmp_path):
    path = DailyStorage(tmp_path).target("SZ", "000001", 2020)
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([bar(date(2026, 1, 1))]), path)
    with pytest.raises(DataPoolError, match="outside partition"):
        DataPool(tmp_path).read_daily(start="2026-01-01")


def test_footer_cache_tracks_replacement_and_missing_stats_fallback(tmp_path):
    path = DailyStorage(tmp_path).target("SZ", "000001", 2026)
    path.parent.mkdir(parents=True)
    storage = DailyStorage(tmp_path)
    pq.write_table(pa.Table.from_pylist([bar(date(2026, 1, 1))]), path, write_statistics=False)
    assert storage.extent("SZ", "000001") == (date(2026, 1, 1), date(2026, 1, 1), 1)
    pq.write_table(pa.Table.from_pylist([bar(date(2026, 1, 2))]), path)
    assert storage.extent("SZ", "000001") == (date(2026, 1, 2), date(2026, 1, 2), 1)


def test_validate_daily_retains_duplicate_counter(tmp_path):
    write(tmp_path, [bar(date(2026, 1, 1))] * 2, yearly=True)
    assert validate_daily(tmp_path) == {"files": 1, "rows": 2, "duplicates": 1, "invalid": 0}


@pytest.mark.parametrize("yearly", [False, True])
def test_pre_etf_schema_error_survives_absent_keys_and_pruned_years(tmp_path, yearly):
    write(tmp_path, [bar(date(2026, 1, 1))], yearly=yearly)
    api = DataPool(tmp_path)
    for kwargs in (
        {"symbols": "159999.SZ"},
        {"start": "2030-01-01"},
        {"symbols": []},
        {"symbols": "000001.SZ"},
    ):
        with pytest.raises(DataPoolError) as error:
            api.read_etf_daily(**kwargs)
        assert error.value.code == "ETF_NOT_FOUND"
    write(tmp_path, [bar(date(2026, 1, 1), "159915", asset_type="etf")], "159915", yearly)
    for kwargs in (
        {"symbols": "159999.SZ"},
        {"start": "2030-01-01"},
        {"symbols": []},
        {"symbols": "000001.SZ"},
    ):
        assert api.read_etf_daily(**kwargs).empty


def test_offline_and_free_stockdb_import_replay_with_existing_layout(pool, monkeypatch):
    from types import SimpleNamespace

    from aspool.free_stockdb import import_daily
    from aspool.tdx_online import update_daily_offline

    root, days, _ = pool
    merge_daily(root, "SZ", "000001", [bar(days[-1])], "test")
    with catalog(root) as conn:
        conn.execute("CREATE TABLE universe (symbol VARCHAR, asset_type VARCHAR, active BOOLEAN)")
        conn.execute("INSERT INTO universe VALUES ('000001','stock',true)")
    item = SimpleNamespace(
        year=days[-1].year,
        month=days[-1].month,
        day=days[-1].day,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.0,
        vol=1.0,
        amount=1400.0,
    )
    monkeypatch.setattr("tdxman.offline.find_daily_bar_file", lambda *args: "unused")
    monkeypatch.setattr("tdxman.offline.read_daily_bars", lambda *args: [item])
    assert update_daily_offline(root, limit=1) == (1, 1)
    assert update_daily_offline(root, limit=1) == (1, 0)

    class Source:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def symbols(self):
            return ["000001"]

        def daily_bars_many(self, *args):
            yield "000001", [bar(days[-1], amount=1500.0)]

    monkeypatch.setattr("aspool.free_stockdb.FreeStockDb", Source)
    assert import_daily(root, root / "source", False).rows == 1
    paths = DailyStorage(root).paths("SZ", "000001")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    assert import_daily(root, root / "source", False).rows == 0
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    frame = DataPool(root).read_daily(symbols="000001.SZ")
    assert len(frame) == len(days) and frame.amount.iloc[-1] == 1500.0


def test_tail_planning_uses_five_actual_dates_across_sparse_years(tmp_path):
    days = [
        date(2022, 12, 31),
        date(2023, 12, 31),
        date(2024, 12, 30),
        date(2024, 12, 31),
        date(2025, 12, 31),
        date(2026, 1, 1),
    ]
    write(tmp_path, [bar(day) for day in days], yearly=True)
    assert DailyStorage(tmp_path).tail_dates("SZ", "000001") == days[-5:]
