from datetime import datetime

import duckdb
import pandas as pd

from aspool import DataPool
from aspool.fundamentals_store import (
    fundamentals_connection,
    fundamentals_status,
    normalize_finance,
    write_finance_rows,
)
from aspool.fundamentals_update import _selected
from aspool.securities import ensure_securities


def finance(updated_date=20260928, shareholders=1234):
    return {
        "code": "000001",
        "market": "SZ",
        "updated_date": updated_date,
        "ipo_date": 19910403,
        "liutong_guben": 100.0,
        "zong_guben": 200.0,
        "gudong_renshu": shareholders,
        "jing_lirun": 30.0,
        "meigujing_zichan": 4.5,
    }


def test_finance_and_shareholder_history_are_keyed_by_source_date(tmp_path):
    with fundamentals_connection(tmp_path, create=True, read_only=False) as conn:
        first = normalize_finance(
            "000001.SZ", finance(), fetched_at="2026-09-29T00:00:00"
        )
        assert write_finance_rows(conn, [first]) == {
            "financial_reports": 1,
            "shareholder_counts": 1,
        }
        assert write_finance_rows(conn, [first]) == {
            "financial_reports": 0,
            "shareholder_counts": 0,
        }
        second = normalize_finance(
            "000001.SZ",
            finance(updated_date=20261231, shareholders=1200),
            fetched_at="2027-01-02T00:00:00",
        )
        assert write_finance_rows(conn, [second]) == {
            "financial_reports": 1,
            "shareholder_counts": 1,
        }

    reports = DataPool(tmp_path).read_fundamental_reports(symbols="000001.SZ")
    counts = DataPool(tmp_path).read_shareholder_counts(symbols="000001.SZ")
    assert reports.period_end.dt.strftime("%Y-%m-%d").tolist() == [
        "2026-09-28",
        "2026-12-31",
    ]
    assert counts.shareholder_count.tolist() == [1234, 1200]
    assert reports.published_at.isna().all()
    assert reports.attrs["source"] == "aspool.fundamentals.sqlite"
    status = fundamentals_status(tmp_path)
    assert status["ready"] and status["datasets"]["stock_financial_reports"]["rows"] == 2


def test_invalid_source_date_and_identity_are_rejected():
    row = finance()
    row["code"] = "000002"
    try:
        normalize_finance("000001.SZ", row, fetched_at="2026-09-29T00:00:00")
    except ValueError as exc:
        assert "identity" in str(exc)
    else:
        raise AssertionError("identity mismatch was accepted")
    row = finance(updated_date=0)
    try:
        normalize_finance("000001.SZ", row, fetched_at="2026-09-29T00:00:00")
    except ValueError as exc:
        assert "updated_date" in str(exc)
    else:
        raise AssertionError("invalid report date was accepted")


def test_normalize_accepts_tdx_market_enum_shape():
    class MarketValue:
        name = "SZ"

    row = finance()
    row["market"] = MarketValue()
    normalized = normalize_finance(
        "000001.SZ", row, fetched_at=datetime(2026, 9, 29).isoformat()
    )
    assert normalized["period_end"] == "2026-09-28"
    assert pd.isna(normalized["published_at"])

    row = finance()
    row["market"] = 0
    assert normalize_finance(
        "000001.SZ", row, fetched_at=datetime(2026, 9, 29).isoformat()
    )["period_end"] == "2026-09-28"


def test_fundamental_selection_uses_canonical_symbols(tmp_path):
    ensure_securities(tmp_path)
    with duckdb.connect(str(tmp_path / "catalog.duckdb")) as conn:
        conn.execute(
            "INSERT INTO securities VALUES "
            "('000001.SZ','000001','SZ','stock','测试',true,NULL,NULL,current_timestamp)"
        )
    assert _selected(tmp_path, (), None) == ["000001.SZ"]
