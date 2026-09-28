"""Quality partitions, bounded missing-row dependencies and optional schema compatibility."""

import json

import pytest

from aspool import DataPool
from aspool.api_contract import DataPoolError
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_stock_store import stock_connection
from aspool.sqlite_summary_quality import upgrade_quality_columns
from tests.unit.test_sqlite_market_summary import observation, summary


def test_reason_partition_changes_even_when_unknown_total_is_unchanged(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1, limit="UNKNOWN", st=None, streak=None)
        observation(conn, 2, limit="UNKNOWN", pre=None, streak=None)
        observation(conn, 3, status="INVALID", close=0)
        conn.execute(
            "UPDATE daily_features SET limit_reason=CASE symbol WHEN '000001.SZ' "
            "THEN 'missing_st' WHEN '000002.SZ' THEN 'missing_reference' ELSE 'invalid_ohlc' END"
        )
        recompute_daily_summary(conn, trade_date="2024-01-02")
        before = summary(conn)
        reasons = json.loads(before["limit_reason_counts_json"])
        assert reasons["UNKNOWN"] == {"missing_st": 1, "missing_reference": 1}
        assert reasons["INVALID"] == {"invalid_ohlc": 1}
        conn.execute(
            "UPDATE daily_features SET limit_reason='missing_reference' WHERE symbol='000001.SZ'"
        )
        recompute_daily_summary(conn, trade_date="2024-01-02")
        after = summary(conn)
        assert after["limit_unknown_count"] == before["limit_unknown_count"] == 2
        assert after["updated_at"] > before["updated_at"]
        assert json.loads(after["limit_reason_counts_json"])["UNKNOWN"]["missing_st"] == 0
        assert recompute_daily_summary(conn, trade_date="2024-01-02")["changed_rows"] == 0
        assert summary(conn)["updated_at"] == after["updated_at"]


def test_promotion_candidates_partition_missing_suspended_unknown_and_scope(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        for code in range(1, 9):
            observation(conn, code, close=11, up=1, touch=1, streak=None if code == 6 else 1)
        conn.execute("UPDATE daily_bars SET trade_date='2024-01-01'")
        conn.execute("UPDATE daily_features SET trade_date='2024-01-01'")
        conn.execute(
            "UPDATE daily_features SET close_limit_up=NULL,touch_limit_up=NULL,"
            "close_limit_down=NULL,touch_limit_down=NULL,limit_up_price=NULL,limit_down_price=NULL,"
            "consecutive_up=NULL,streak_known=0,limit_status='UNKNOWN' WHERE symbol='000007.SZ'"
        )
        observation(conn, 1, close=11, up=1, touch=1, previous=1, streak=2)
        observation(conn, 2, status="NO_TRADE")
        # Stock 3 has no row today: same exclusion as stock 2.
        observation(conn, 4, status="INVALID", close=0)
        observation(conn, 5, limit="UNKNOWN", previous=1, streak=None)
        observation(conn, 6, previous=None)
        observation(conn, 7, previous=None)
        observation(conn, 8, previous=1, st=1)
        recompute_daily_summary(conn, trade_date="2024-01-02")
        full, scoped = summary(conn), summary(conn, "exclude_known_st")
        quality = json.loads(full["promotion_quality_json"])
        assert quality == dict(
            candidate_count=7,
            excluded_no_trade_count=2,
            excluded_invalid_count=1,
            excluded_limit_unknown_count=1,
            excluded_predecessor_unknown_count=1,
            unresolved_predecessor_count=1,
        )
        assert full["promotion_eligible_count"] == 2
        assert full["promotion_success_count"] == 1 and full["promotion_ratio"] == 0.5
        assert scoped["promotion_eligible_count"] == 1
        assert json.loads(scoped["promotion_quality_json"])["candidate_count"] == 6


def test_optional_upgrade_is_explicit_nullable_and_metadata_is_honest(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        conn.execute("ALTER TABLE market_daily_summary DROP COLUMN limit_reason_counts_json")
        conn.execute("ALTER TABLE market_daily_summary DROP COLUMN promotion_quality_json")
        conn.execute("BEGIN")
        recompute_daily_summary(conn, trade_date="2024-01-02")
        conn.commit()
    pool = DataPool(tmp_path)
    with pytest.raises(DataPoolError) as error:
        pool.describe_market_fields(fields=["promotion_quality_json"])
    assert error.value.code == "FIELD_UNSUPPORTED"
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute("BEGIN")
        upgrade_quality_columns(conn)
        conn.commit()
    assert (
        pool.read_market_daily(start="2024-01-02", end="2024-01-02")["promotion_quality_json"].iloc[
            0
        ]
        is None
    )
    metadata = pool.describe_market_fields(
        fields=["promotion_quality_json", "above_ma20_pct", "up_pct"]
    )
    assert metadata["promotion_quality_json"]["null_meaning"] == "not_computed"
    assert metadata["above_ma20_pct"]["unit"] == "ratio"
    assert metadata["up_pct"]["unit"] == "percent_points"
    with pytest.raises(DataPoolError):
        pool.describe_market_fields(frequency="W")
    with pytest.raises(DataPoolError):
        pool.describe_market_fields(fields=["imagined_count"])
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute("BEGIN")
        recompute_daily_summary(conn, trade_date="2024-01-02")
        conn.commit()
    frame = pool.read_market_daily(start="2024-01-02", end="2024-01-02")
    assert json.loads(frame.promotion_quality_json.iloc[0])["candidate_count"] == 0
    assert frame.promotion_ratio.iloc[0] is None


def test_writer_repairs_missing_reasons_and_rolls_back_json_with_source(tmp_path, monkeypatch):
    from aspool import sqlite_daily_update as writer
    from tests.unit.test_sqlite_daily_update import AGES, DAYS, SYMBOL, row

    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        day = DAYS[25]
        kwargs = dict(market_sessions=DAYS, listed_days=AGES)
        writer.apply_daily_changes(conn, bars=[row(day)], **kwargs)
        assert (
            json.loads(summary(conn, day=day)["limit_reason_counts_json"])["UNKNOWN"]["missing_st"]
            == 1
        )
        writer.apply_daily_changes(
            conn,
            dated_facts=[
                dict(symbol=SYMBOL, trade_date=day, source_is_st=0, source_is_st_source="provider")
            ],
            **kwargs,
        )
        reasons = json.loads(summary(conn, day=day)["limit_reason_counts_json"])
        assert (
            reasons["UNKNOWN"]["missing_st"] == 0 and reasons["UNKNOWN"]["missing_reference"] == 1
        )
        fix = dict(
            symbol=SYMBOL, trade_date=day, source_pre_close=10, source_pre_close_source="provider"
        )
        before = list(conn.iterdump())
        original = writer.recompute_daily_summary

        def fail_after_summary(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected failure after quality write")

        monkeypatch.setattr(writer, "recompute_daily_summary", fail_after_summary)
        with pytest.raises(RuntimeError, match="injected failure"):
            writer.apply_daily_changes(conn, dated_facts=[fix], **kwargs)
        assert list(conn.iterdump()) == before
        monkeypatch.setattr(writer, "recompute_daily_summary", original)
        writer.apply_daily_changes(conn, dated_facts=[fix], **kwargs)
        assert summary(conn, day=day)["limit_unknown_count"] == 0
        after = list(conn.iterdump())
        result = writer.apply_daily_changes(conn, dated_facts=[fix], **kwargs)
        assert result["changed_rows"] == result["summary_rows"] == 0
        assert list(conn.iterdump()) == after


def test_repair_propagates_candidate_exclusion_through_missing_sessions(tmp_path):
    from aspool.sqlite_daily_update import apply_daily_changes
    from tests.unit.test_sqlite_daily_update import AGES, DAYS, SYMBOL, row

    days = DAYS[:4]
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        apply_daily_changes(
            conn,
            bars=[row(days[0], close=11), row(days[3], close=10)],
            dated_facts=[
                dict(
                    symbol=SYMBOL,
                    trade_date=d,
                    source_is_st=0,
                    source_is_st_source="provider",
                    source_pre_close=10,
                    source_pre_close_source="provider",
                )
                for d in (days[0], days[3])
            ],
            market_sessions=days,
            listed_days=AGES,
        )
        conn.execute("BEGIN")
        for day in days[1:3]:
            recompute_daily_summary(conn, trade_date=day)
            assert (
                json.loads(summary(conn, day=day)["promotion_quality_json"])[
                    "excluded_no_trade_count"
                ]
                == 1
            )
        conn.commit()
        result = apply_daily_changes(
            conn, bars=[row(days[0], close=10)], market_sessions=days, listed_days=AGES
        )
        assert set(days[1:3]) <= set(result["affected_sessions"])
        for day in days[1:3]:
            value = json.loads(summary(conn, day=day)["promotion_quality_json"])
            assert value["candidate_count"] == value["excluded_no_trade_count"] == 0


def test_quality_budget_and_schema_upgrade_rollback(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1)
        before = list(conn.iterdump())
        with pytest.raises(DataPoolError) as error:
            recompute_daily_summary(conn, trade_date="2024-01-02", max_rows=1)
        assert error.value.code == "LOCAL_UPDATE_BUDGET_EXCEEDED"
        assert list(conn.iterdump()) == before
        conn.rollback()
        for field in ("limit_reason_counts_json", "promotion_quality_json"):
            conn.execute(f"ALTER TABLE market_daily_summary DROP COLUMN {field}")
        conn.execute("BEGIN")
        upgrade_quality_columns(conn)
        conn.rollback()
        assert "promotion_quality_json" not in {
            r[1] for r in conn.execute("PRAGMA table_info(market_daily_summary)")
        }
