from datetime import date

import pandas as pd
import pytest

from aspool import DataPool
from aspool.free_stockdb import _write_daily
from aspool.fundamentals import _snapshot_rows, _write
from aspool.tdx_online import _daily_rows, _enrich_daily


def put(root, code, rows):
    _write_daily(root, "SZ", code, rows)


def bar(day, **extra):
    return dict(
        trade_date=date(2026, 9, day),
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.0,
        volume=1000.0,
        amount=10000.0,
        turnover=0.43,
        **extra,
    )


def test_read_contract_and_date_before_window(tmp_path):
    put(tmp_path, "000001", [bar(d) for d in (10, 11, 14, 15, 16)])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    frame = DataPool(tmp_path).read_daily(end="2026-09-14", lookback=2)
    assert frame.date.dt.day.tolist() == [11, 14]
    assert frame.symbol.tolist() == ["SZ.000001"] * 2
    assert frame.turnover_rate.tolist() == [0.43] * 2
    assert frame.amount.tolist() == [10000.0] * 2
    assert DataPool(tmp_path).status()["row_count"] == 5
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert DataPool(tmp_path).read_daily(symbols=["SH.000001"]).empty


def test_online_volume_is_shares_and_new_columns_survive(tmp_path):
    frame = pd.DataFrame(
        [
            dict(
                datetime=pd.Timestamp("2026-09-16"),
                vol=83246080.0,
                open=11.82,
                high=11.86,
                low=11.71,
                close=11.74,
                amount=979990528.0,
            )
        ]
    )
    online = _daily_rows(frame, "000001")[0]
    assert online["volume"] == 83246080.0
    put(tmp_path, "000001", [bar(11), online])
    result = DataPool(tmp_path).read_daily()
    assert result.volume.iloc[-1] == 83246080.0
    assert pd.isna(result.turnover_rate.iloc[-1])


def test_missing_volume_is_error_not_no_signal(tmp_path):
    row = bar(16)
    row["volume"] = None
    put(tmp_path, "000001", [row])
    with pytest.raises(ValueError, match="incomplete"):
        DataPool(tmp_path).read_daily()


def test_fundamentals_snapshot_uses_current_bar_for_valuation(tmp_path):
    put(tmp_path, "000001", [bar(16)])
    quotes = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "平安银行",
                "total_shares": 100.0,
                "float_shares": 80.0,
                "eps": 1.2,
                "net_assets": 20.0,
                "close": 10.0,
                "pe_ttm": 5.0,
            }
        ]
    )
    _write(tmp_path, _snapshot_rows(quotes, pd.Timestamp("2026-09-16").to_pydatetime()))
    result = DataPool(tmp_path).read_fundamentals(as_of="2026-09-16")
    row = result.iloc[0]
    assert row.total_share == 1_000_000
    assert row.float_share == 800_000
    assert row.ttm_eps == 2.0
    assert row.total_mv == 10_000_000
    assert row.float_mv == 8_000_000
    assert row.pe_ttm == 5.0
    assert row.pb == 0.5


def test_online_daily_rows_use_manual_snapshot_with_free_stockdb_field_names():
    rows = _enrich_daily(
        [{"close": 10.0, "volume": 100_000.0}],
        {"total_share": 1_000_000.0, "float_share": 800_000.0, "ttm_eps": 2.0, "net_assets": 20.0},
    )
    assert rows == [
        {
            "close": 10.0,
            "volume": 100_000.0,
            "total_share": 1_000_000.0,
            "float_share": 800_000.0,
            "total_mv": 10_000_000.0,
            "float_mv": 8_000_000.0,
            "pe_ttm": 5.0,
            "pb": 0.5,
            "turnover": 12.5,
        }
    ]


def test_quote_turnover_is_not_replaced_by_the_derived_value():
    rows = _enrich_daily(
        [{"close": 10.0, "volume": 100_000.0, "turnover": 10.0}],
        {"total_share": 1_000_000.0, "float_share": 800_000.0, "ttm_eps": 2.0, "net_assets": 20.0},
    )
    assert rows[0]["turnover"] == 10.0


def test_pool_reader_waits_for_batch_writer(tmp_path):
    import subprocess
    import sys

    from aspool.pool import pool_lock

    put(tmp_path, "000001", [bar(16)])
    with pool_lock(tmp_path, write=True):
        code = (
            "from aspool import DataPool; import sys; print('ready', flush=True); "
            "DataPool(sys.argv[1]).read_daily(); print('done', flush=True)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True
        )
        assert process.stdout.readline().strip() == "ready"
        assert process.poll() is None
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 0 and "done" in output
