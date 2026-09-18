from datetime import date

import pandas as pd
import pyarrow.parquet as pq
import pytest

from aspool.daily_storage import build_field_quality_report, merge_daily
from aspool.free_stockdb import _write_daily
from aspool.fundamentals import _quote_bar
from aspool.store import bars_path, initialize, record_coverage


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
