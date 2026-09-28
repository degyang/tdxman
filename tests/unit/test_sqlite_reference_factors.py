import json
import sqlite3
from pathlib import Path

import pytest

from aspool.sqlite_reference_factors import (
    advance_factor_coverage,
    build_selected_factors,
    normalize_actions,
    select_is_st,
    select_reference_pre_close,
    update_reference_factors,
)
from aspool.sqlite_stock_store import stock_connection


def test_cash_bonus_rights_and_same_day_combination():
    cash = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "cash_dividend_per_share": 1.0,
            "bonus_shares_per_share": 0.0,
            "rights_shares_per_share": 0.0,
        }
    ]
    assert select_reference_pre_close(
        previous_close=10, actions=cash, actions_covered=True
    ) == pytest.approx((9.0, "derived:previous_close+corporate_actions"))
    bonus = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "fenhong": 0,
            "songzhuangu": 1,
            "peigu": 0,
        }
    ]
    assert select_reference_pre_close(previous_close=10, actions=bonus, actions_covered=True)[
        0
    ] == pytest.approx(10 / 1.1)
    rights = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "fenhong": 0,
            "songzhuangu": 0,
            "peigu": 1,
            "peigujia": 5,
        }
    ]
    assert select_reference_pre_close(previous_close=10, actions=rights, actions_covered=True)[
        0
    ] == pytest.approx(10.5 / 1.1)

    combined = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "fenhong": 1,
            "songzhuangu": 0,
            "peigu": 0,
        },
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "fenhong": 0,
            "songzhuangu": 1,
            "peigu": 1,
            "peigujia": 5,
        },
    ]
    grouped = normalize_actions(combined)
    assert len(grouped) == 1 and len(grouped[0]["events"]) == 2
    assert grouped[0]["events"][0]["cash_dividend_per_share"] == pytest.approx(0.1)
    assert grouped[0]["events"][0]["rights_shares_per_share"] == 0
    one_row = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "fenhong": 1,
            "songzhuangu": 1,
            "peigu": 1,
            "peigujia": 5,
        }
    ]
    assert select_reference_pre_close(previous_close=10, actions=combined, actions_covered=True)[
        0
    ] == pytest.approx(
        select_reference_pre_close(previous_close=10, actions=one_row, actions_covered=True)[0]
    )
    two_dates = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "cash_dividend_per_share": 1,
            "bonus_shares_per_share": 0,
            "rights_shares_per_share": 0,
        },
        {
            "date": "2024-01-03",
            "category": 1,
            "source": "tdx:xdxr",
            "cash_dividend_per_share": 1,
            "bonus_shares_per_share": 0,
            "rights_shares_per_share": 0,
        },
    ]
    assert select_reference_pre_close(previous_close=10, actions=two_dates, actions_covered=True)[
        0
    ] == pytest.approx(8)


def test_source_cumulative_anchors_are_not_cumprod_and_intervals_are_bounded():
    anchors = [
        {"effective_date": "2024-01-02", "source": "vendor", "source_cumulative_factor": 2},
        {"effective_date": "2024-01-05", "source": "vendor", "source_cumulative_factor": 3},
    ]
    result = build_selected_factors(anchors=anchors, through="2024-01-10")
    assert [row["cumulative_factor"] for row in result] == [2, 3]
    assert [row["event_factor"] for row in result] == [None, 1.5]
    assert [(row["valid_from"], row["valid_through"]) for row in result] == [
        ("2024-01-02", "2024-01-04"),
        ("2024-01-05", "2024-01-10"),
    ]
    event = {
        "date": "2024-01-03",
        "category": 1,
        "source": "tdx:xdxr",
        "source_key": "cash",
        "fenhong": 10,
        "songzhuangu": 0,
        "peigu": 0,
    }
    assert (
        build_selected_factors(
            anchors=anchors,
            events=[event],
            prior_closes={"2024-01-03": 10},
            through="2024-01-10",
        )
        == result
    )
    with pytest.raises(ValueError, match="reliable source"):
        build_selected_factors(
            anchors=[
                dict(effective_date="2024-01-01", source="a", source_cumulative_factor=2),
                dict(effective_date="2024-01-02", source="b", source_cumulative_factor=100),
            ],
            through="2024-01-10",
        )
    with pytest.raises(ValueError, match="reliable source"):
        build_selected_factors(
            anchors=[
                dict(effective_date="2024-01-01", source="unknown", source_cumulative_factor=2)
            ],
            through="2024-01-10",
        )
    with pytest.raises(ValueError, match="reliable source"):
        build_selected_factors(
            events=[
                dict(date="2024-01-01", category=2, source="source:a"),
                dict(date="2024-01-02", category=2, source="source:b"),
            ],
            through="2024-01-10",
        )
    with pytest.raises(ValueError, match="Missing prior close"):
        build_selected_factors(
            events=[dict(date="2024-01-02", category=7, source="tdx:xdxr")],
            prior_closes={},
            through="2024-01-10",
        )


def test_event_deduplication_and_verified_no_event_baseline():
    event = {
        "date": "2024-01-02",
        "category": 1,
        "source": "tdx:xdxr",
        "source_key": "stable-event-1",
        "fenhong": 10,
        "songzhuangu": 0,
        "peigu": 0,
    }
    single = build_selected_factors(
        events=[event], prior_closes={"2024-01-02": 10}, through="2024-01-10"
    )
    duplicate = build_selected_factors(
        events=[event, dict(event)], prior_closes={"2024-01-02": 10}, through="2024-01-10"
    )
    assert duplicate == single
    second_source_copy = dict(
        event,
        source="baostock:action-copy",
        source_key="vendor-copy-key",
        business_key="business-event-1",
    )
    primary_with_business_key = dict(event, business_key="business-event-1")
    copied = build_selected_factors(
        events=[primary_with_business_key, second_source_copy],
        prior_closes={"2024-01-02": 10},
        through="2024-01-10",
    )
    assert copied[0]["cumulative_factor"] == pytest.approx(10 / 9)
    assert single[0]["cumulative_factor"] == pytest.approx(10 / 9)
    with pytest.raises(ValueError, match="Conflicting corporate action"):
        build_selected_factors(
            events=[event, dict(event, fenhong=20)],
            prior_closes={"2024-01-02": 10},
            through="2024-01-10",
        )

    baseline = build_selected_factors(
        verified_no_event_range=("2024-01-02", "2024-01-05", "vendor:complete-actions"),
        through="2024-01-10",
    )
    assert baseline == [
        {
            "effective_date": "2024-01-02",
            "event_factor": None,
            "cumulative_factor": 1.0,
            "valid_from": "2024-01-02",
            "valid_through": "2024-01-05",
            "factor_basis": "event_chain:verified_no_events:vendor:complete-actions",
        }
    ]


def test_reference_precedence_and_unknown_source():
    action = [
        {
            "date": "2024-01-02",
            "category": 1,
            "source": "tdx:xdxr",
            "cash_dividend_per_share": 1,
            "bonus_shares_per_share": 0,
            "rights_shares_per_share": 0,
        }
    ]
    assert select_reference_pre_close(
        dated=[(8.5, "baostock:dated")],
        previous_close=10,
        actions=action,
        actions_covered=True,
        raw_candidate=(8.0, "raw_fallback:legacy"),
    ) == (8.5, "baostock:dated")
    assert (
        select_reference_pre_close(
            previous_close=10,
            actions=action,
            actions_covered=True,
            raw_candidate=(8.0, "raw_fallback:legacy"),
        )[0]
        == 9
    )
    assert select_reference_pre_close(raw_candidate=(8.0, "raw_fallback:legacy")) == (
        8.0,
        "raw_fallback:legacy",
    )
    assert select_reference_pre_close(
        previous_close=10, raw_candidate=(8.0, "raw_fallback:legacy")
    ) == (8.0, "raw_fallback:legacy")
    assert select_reference_pre_close(
        previous_close=10, actions_covered=True, raw_candidate=(8.0, "raw_fallback:legacy")
    ) == (10, "derived:previous_close+corporate_actions")
    assert select_reference_pre_close() == (None, None)
    with pytest.raises(ValueError, match="Conflicting"):
        select_reference_pre_close(dated=[(8, "source:a"), (9, "source:b")])
    assert select_reference_pre_close(dated=[(8, "unknown")]) == (None, None)
    with pytest.raises(ValueError, match="explicit, reliable source"):
        select_reference_pre_close(
            previous_close=10,
            actions=[dict(action[0], source="unknown")],
            actions_covered=True,
        )


def test_st_date_precedence_and_name_must_be_for_same_date():
    assert select_is_st(
        dated=[(False, "baostock")], name="ST甲", name_as_of="2024-01-02", trade_date="2024-01-02"
    ) == (False, "baostock", None)
    assert select_is_st(name="*ST甲", name_as_of="2024-01-02", trade_date="2024-01-02") == (
        True,
        "dated_name",
        "2024-01-02",
    )
    assert select_is_st(name="ST甲", name_as_of="2023-01-02", trade_date="2024-01-02") == (
        None,
        None,
        None,
    )
    assert select_is_st(
        dated=[(False, "raw_fallback:legacy_unspecified")],
        name=None,
        name_as_of=None,
        trade_date="2024-01-02",
    ) == (None, None, None)
    assert select_is_st(
        dated=[(False, "unknown")],
        trade_date="2024-01-02",
    ) == (None, None, None)
    assert select_is_st(name=None, name_as_of=None, trade_date="2024-01-02") == (None, None, None)
    with pytest.raises(ValueError, match="Conflicting"):
        select_is_st(dated=[(True, "a"), (False, "b")], trade_date="2024-01-02")


def test_atomic_writer_preserves_other_fields_and_repeat_is_noop(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        stamp = 100
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,name,name_as_of,updated_at)
            VALUES ('000001.SZ','2024-01-01',10,10,10,10,'甲','2024-01-01',?)""",
            (stamp,),
        )
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,name,name_as_of,updated_at)
            VALUES ('000001.SZ','2024-01-02',9,9,9,9,'ST甲','2024-01-02',?)""",
            (stamp,),
        )
        conn.execute(
            """INSERT INTO daily_features
            (symbol,trade_date,source_pre_close,source_pre_close_source,source_is_st,
             source_is_st_source,pre_close,is_st,calc_status,limit_status,
             consecutive_up,streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-01',NULL,NULL,NULL,NULL,NULL,NULL,
                    'TRADED','UNKNOWN',NULL,0,?)""",
            (stamp,),
        )
        conn.execute(
            """INSERT INTO daily_features
            (symbol,trade_date,source_pre_close,source_pre_close_source,source_is_st,
             source_is_st_source,pre_close,is_st,calc_status,limit_status,
             consecutive_up,streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-02',8,'raw_fallback:test',NULL,NULL,NULL,NULL,
                    'TRADED','UNKNOWN',NULL,0,?)""",
            (stamp,),
        )
        action = {
            "effective_date": "2024-01-02",
            "category": 1,
            "fenhong": 10,
            "songzhuangu": 0,
            "peigu": 0,
            "source_key": "cash-1",
        }
        result = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions=[action, dict(action)],
            factor_through="2024-01-02",
            actions_covered_dates=["2024-01-02"],
        )
        assert result["actions"] == 1 and result["features"] == 1
        feature = conn.execute("""SELECT pre_close,pre_close_source,is_st,is_st_source,
            source_pre_close,source_pre_close_source,limit_status,updated_at
            FROM daily_features WHERE trade_date='2024-01-02'""").fetchone()
        assert feature[:6] == (
            9,
            "derived:previous_close+corporate_actions",
            True,
            "dated_name",
            8,
            "raw_fallback:test",
        )
        assert feature[6] == "UNKNOWN" and feature[7] != stamp
        previous_updated_at = feature[7]
        initial_factor_snapshot = conn.execute(
            """SELECT effective_date,source,event_factor,cumulative_factor,valid_from,
            valid_through,factor_basis,updated_at FROM corporate_actions
            WHERE record_kind='factor' ORDER BY effective_date"""
        ).fetchall()
        changes = conn.total_changes
        repeated = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions=[action],
            factor_through="2024-01-02",
            actions_covered_dates=["2024-01-02"],
        )
        assert conn.total_changes == changes
        assert repeated["factors"] == 0
        assert repeated["features"] == 0
        assert (
            conn.execute(
                "SELECT updated_at FROM daily_features WHERE trade_date='2024-01-02'"
            ).fetchone()[0]
            == previous_updated_at
        )
        assert (
            conn.execute(
                """SELECT effective_date,source,event_factor,cumulative_factor,valid_from,
                valid_through,factor_basis,updated_at FROM corporate_actions
                WHERE record_kind='factor' ORDER BY effective_date"""
            ).fetchall()
            == initial_factor_snapshot
        )
        direct = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions=[action],
            factor_through="2024-01-02",
            dated_pre_close={"2024-01-02": [(8.5, "baostock:dated")]},
            dated_st={"2024-01-02": [(False, "baostock:dated")]},
            actions_covered_dates=["2024-01-02"],
        )
        assert direct["features"] == 1
        stored = conn.execute(
            """SELECT source_pre_close,source_pre_close_source,pre_close,is_st,is_st_source
            FROM daily_features WHERE trade_date='2024-01-02'"""
        ).fetchone()
        assert stored == (8.5, "baostock:dated", 8.5, False, "baostock:dated")
        changes = conn.total_changes
        update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions=[action],
            factor_through="2024-01-02",
            dated_pre_close={"2024-01-02": [(8.5, "baostock:dated")]},
            dated_st={"2024-01-02": [(False, "baostock:dated")]},
            actions_covered_dates=["2024-01-02"],
        )
        assert conn.total_changes == changes
        factor_snapshot = conn.execute(
            """SELECT effective_date,source,event_factor,cumulative_factor,valid_from,
            valid_through,factor_basis,updated_at FROM corporate_actions
            WHERE record_kind='factor' ORDER BY effective_date"""
        ).fetchall()
        ref_only = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions_covered_dates=["2024-01-02"],
        )
        assert ref_only["factors"] == 0
        assert conn.execute(
            """SELECT source_pre_close,source_pre_close_source,pre_close
            FROM daily_features WHERE trade_date='2024-01-02'"""
        ).fetchone() == (8.5, "baostock:dated", 8.5)
        assert (
            conn.execute(
                """SELECT effective_date,source,event_factor,cumulative_factor,valid_from,
            valid_through,factor_basis,updated_at FROM corporate_actions
            WHERE record_kind='factor' ORDER BY effective_date"""
            ).fetchall()
            == factor_snapshot
        )
        replaced = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions=[],
            actions_complete_range=("tdx:xdxr", "2024-01-02", "2024-01-02"),
            factors_complete_range=("2024-01-02", "2024-01-02"),
            factor_through="2024-01-02",
            actions_covered_dates=["2024-01-02"],
        )
        assert replaced["actions"] == 1 and replaced["factors"] == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM corporate_actions WHERE record_kind IN ('event','factor')"
            ).fetchone()[0]
            == 0
        )
        conn.commit()


def test_incremental_anchor_update_preserves_factor_keys_and_counts_changes(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        first = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-01"],
            anchors=[
                dict(effective_date="2024-01-01", source="vendor", source_cumulative_factor=2)
            ],
            factor_through="2024-01-10",
        )
        assert first["anchors"] == 1 and first["factors"] == 1
        first_stamp = conn.execute(
            "SELECT updated_at FROM corporate_actions WHERE record_kind='factor'"
        ).fetchone()[0]
        second = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-05"],
            anchors=[
                dict(effective_date="2024-01-05", source="vendor", source_cumulative_factor=3)
            ],
            factor_through="2024-01-10",
        )
        assert second["anchors"] == 1 and second["factors"] == 2
        factors = conn.execute(
            """SELECT effective_date,cumulative_factor,valid_through
            FROM corporate_actions WHERE record_kind='factor' ORDER BY effective_date"""
        ).fetchall()
        assert factors == [
            ("2024-01-01", 2, "2024-01-04"),
            ("2024-01-05", 3, "2024-01-10"),
        ]
        assert (
            conn.execute(
                """SELECT updated_at FROM corporate_actions WHERE record_kind='factor'
            AND effective_date='2024-01-01'"""
            ).fetchone()[0]
            != first_stamp
        )
        updated = conn.execute(
            """SELECT effective_date,updated_at FROM corporate_actions
            WHERE record_kind='factor' ORDER BY effective_date"""
        ).fetchall()
        noop = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-05"],
            anchors=[
                dict(effective_date="2024-01-05", source="vendor", source_cumulative_factor=3)
            ],
            factor_through="2024-01-10",
        )
        assert noop == {"actions": 0, "anchors": 0, "factors": 0, "features": 0}
        assert (
            conn.execute(
                """SELECT effective_date,updated_at FROM corporate_actions
            WHERE record_kind='factor' ORDER BY effective_date"""
            ).fetchall()
            == updated
        )
        conn.commit()


def test_verified_empty_action_range_creates_factor_one_with_start(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        result = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            verified_no_event_range=("2024-01-02", "2024-01-05", "vendor:complete-actions"),
        )
        assert result["factors"] == 1
        assert conn.execute(
            """SELECT effective_date,event_factor,cumulative_factor,valid_from,valid_through
            FROM corporate_actions WHERE record_kind='factor'"""
        ).fetchone() == ("2024-01-02", None, 1.0, "2024-01-02", "2024-01-05")
        unchanged = update_reference_factors(conn, symbol="000001.SZ", dates=["2024-01-02"])
        assert unchanged["factors"] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM corporate_actions WHERE record_kind='factor'"
            ).fetchone()[0]
            == 1
        )
        conn.commit()


def test_stored_reliable_reference_is_retained_and_conflicts_fail(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,updated_at)
            VALUES ('000001.SZ','2024-01-01',10,10,10,10,100)"""
        )
        conn.execute(
            """INSERT INTO daily_features(symbol,trade_date,calc_status,updated_at)
            VALUES ('000001.SZ','2024-01-01','TRADED',100)"""
        )
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,updated_at)
            VALUES ('000001.SZ','2024-01-02',9,9,9,9,100)"""
        )
        conn.execute(
            """INSERT INTO daily_features
            (symbol,trade_date,source_pre_close,source_pre_close_source,calc_status,
             limit_status,streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-02',8.5,'baostock:dated','TRADED','UNKNOWN',0,100)"""
        )
        result = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-02"],
            actions_covered_dates=["2024-01-02"],
        )
        assert result["features"] == 1
        assert conn.execute(
            """SELECT source_pre_close,source_pre_close_source,pre_close,pre_close_source
            FROM daily_features WHERE trade_date='2024-01-02'"""
        ).fetchone() == (8.5, "baostock:dated", 8.5, "baostock:dated")
        with pytest.raises(ValueError, match="Conflicting reliable dated reference"):
            update_reference_factors(
                conn,
                symbol="000001.SZ",
                dates=["2024-01-02"],
                dated_pre_close={"2024-01-02": [(9.0, "other:dated")]},
            )
        assert (
            conn.execute(
                "SELECT pre_close FROM daily_features WHERE trade_date='2024-01-02'"
            ).fetchone()[0]
            == 8.5
        )
        conn.commit()


def test_previous_close_skips_no_trade_and_invalid_bars(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,updated_at)
            VALUES ('000001.SZ','2024-01-01',10,10,10,10,100)"""
        )
        conn.execute(
            """INSERT INTO daily_features(symbol,trade_date,calc_status,updated_at)
            VALUES ('000001.SZ','2024-01-01','TRADED',100)"""
        )
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,updated_at)
            VALUES ('000001.SZ','2024-01-02',20,20,0,20,100)"""
        )
        conn.execute(
            """INSERT INTO daily_features(symbol,trade_date,calc_status,updated_at)
            VALUES ('000001.SZ','2024-01-02','NO_TRADE',100)"""
        )
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,updated_at)
            VALUES ('000001.SZ','2024-01-03',10,9,8,10,100)"""
        )
        conn.execute(
            """INSERT INTO daily_features
            (symbol,trade_date,calc_status,limit_status,streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-03','INVALID','INVALID',0,100)"""
        )
        conn.execute(
            """INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,updated_at)
            VALUES ('000001.SZ','2024-01-04',10,10,10,10,100)"""
        )
        conn.execute(
            """INSERT INTO daily_features
            (symbol,trade_date,calc_status,limit_status,streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-04','TRADED','UNKNOWN',0,100)"""
        )
        update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-01-04"],
            actions_covered_dates=["2024-01-04"],
        )
        assert (
            conn.execute(
                "SELECT pre_close FROM daily_features WHERE trade_date='2024-01-04'"
            ).fetchone()[0]
            == 10
        )
        conn.commit()


def test_failure_rolls_back_action_and_factor_writes(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        conn.execute(
            """INSERT INTO daily_features(symbol,trade_date,calc_status,limit_status,
            streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-02','TRADED','UNKNOWN',0,100)"""
        )
        with pytest.raises(ValueError, match="Conflicting reliable dated ST"):
            update_reference_factors(
                conn,
                symbol="000001.SZ",
                dates=["2024-01-02"],
                anchors=[
                    dict(
                        effective_date="2024-01-02",
                        source="vendor",
                        source_cumulative_factor=2,
                    )
                ],
                actions=[
                    dict(
                        date="2024-01-02",
                        category=1,
                        fenhong=10,
                        songzhuangu=0,
                        peigu=0,
                    )
                ],
                factor_through="2024-01-02",
                dated_st={"2024-01-02": [(True, "source:a"), (False, "source:b")]},
                actions_covered_dates=["2024-01-02"],
            )
        assert conn.execute("SELECT count(*) FROM corporate_actions").fetchone()[0] == 0


@pytest.fixture
def advance_conn():
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            (Path(__file__).resolve().parents[2] / "src/aspool/stocks_schema.sql").read_text()
        )
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _advance_seed_factor(conn, first="2024-01-01", last="2024-01-02", cumulative=2, stamp=100):
    conn.execute(
        """INSERT INTO corporate_actions
        (symbol,effective_date,record_kind,source,source_key,cumulative_factor,
         valid_from,valid_through,factor_basis,updated_at)
        VALUES ('000001.SZ',?,'factor','selected:source_cumulative_factor','selected',
                ?,?,?,'source_cumulative_factor:source_anchor:vendor',?)""",
        (first, cumulative, first, last, stamp),
    )


def _advance_bar(conn, day, close=10, volume=100, status="TRADING", calc="NO_TRADE"):
    conn.execute(
        """INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,volume,updated_at)
        VALUES ('000001.SZ',?,?,?,?,?,?,100)""",
        (day, close, close, close, close, volume),
    )
    conn.execute(
        """INSERT INTO daily_features
        (symbol,trade_date,trading_status,calc_status,limit_status,streak_known,updated_at)
        VALUES ('000001.SZ',?,?,?, ?,?,100)""",
        (
            day,
            status,
            calc,
            "INVALID" if calc == "INVALID" else None,
            0 if calc == "INVALID" else None,
        ),
    )


def _advance_event(day, *, cash=0, bonus=0, key="cash"):
    return dict(
        effective_date=day,
        category=1,
        source_key=key,
        fenhong=cash * 10,
        songzhuangu=bonus * 10,
        peigu=0,
    )


def _advance_snapshot(conn):
    return conn.execute(
        "SELECT * FROM corporate_actions "
        "ORDER BY symbol,effective_date,record_kind,source,source_key"
    ).fetchall()


def test_recent_event_revision_rebuilds_suffix_and_repeat_is_noop(advance_conn):
    from aspool.sqlite_event_update import merge_recent_events

    conn = advance_conn
    _advance_seed_factor(conn, last="2024-01-03")
    _advance_bar(conn, "2024-01-02", close=10)
    args = dict(symbol="000001.SZ", verified_start="2024-01-03", verified_end="2024-01-05")
    _advance_seed_factor(conn, first="2024-01-04", last="2024-01-05", cumulative=2.2)
    merge_recent_events(
        conn,
        **args,
        events=[
            _advance_event("2024-01-04", cash=1),
            dict(effective_date="2024-01-04", category=9, source_key="shares", panhou_liutong=100),
        ],
    )
    prefix = conn.execute(
        "SELECT * FROM corporate_actions WHERE record_kind='factor' AND effective_date='2024-01-01'"
    ).fetchone()
    revised = merge_recent_events(conn, **args, events=[_advance_event("2024-01-04", cash=2)])
    assert revised["affected_from"] == "2024-01-04"
    assert conn.execute(
        "SELECT source,factor_basis FROM corporate_actions WHERE record_kind='factor' "
        "AND effective_date='2024-01-04'"
    ).fetchone() == ("derived:event_chain", "anchor_continuation:daily_revision")
    assert conn.execute(
        "SELECT cumulative_factor FROM corporate_actions WHERE record_kind='factor' "
        "AND effective_date='2024-01-04'"
    ).fetchone()[0] == pytest.approx(2.5)
    assert (
        conn.execute(
            "SELECT * FROM corporate_actions WHERE record_kind='factor' "
            "AND effective_date='2024-01-01'"
        ).fetchone()
        == prefix
    )
    snapshot = _advance_snapshot(conn)
    assert (
        merge_recent_events(conn, **args, events=[_advance_event("2024-01-04", cash=2)])[
            "changed_rows"
        ]
        == 0
    )
    assert merge_recent_events(conn, **args, events=[])["changed_rows"] == 0
    assert _advance_snapshot(conn) == snapshot


@pytest.mark.parametrize("category", range(2, 11))
def test_recent_non_price_event_revision_does_not_touch_factors(advance_conn, category):
    from aspool.sqlite_event_update import merge_recent_events

    conn = advance_conn
    _advance_seed_factor(conn, last="2024-01-05")
    args = dict(symbol="000001.SZ", verified_start="2024-01-03", verified_end="2024-01-05")
    event = dict(
        effective_date="2024-01-04", category=category, source_key="shares", panhou_liutong=100
    )
    merge_recent_events(conn, **args, events=[event])
    factors = conn.execute("SELECT * FROM corporate_actions WHERE record_kind='factor'").fetchall()
    result = merge_recent_events(conn, **args, events=[dict(event, panhou_liutong=200)])
    assert result["changed_event_rows"] == 1 and result["affected_from"] is None
    assert (
        conn.execute("SELECT * FROM corporate_actions WHERE record_kind='factor'").fetchall()
        == factors
    )


def test_advance_empty_interval_extends_only_tail_and_joins_outer_transaction(advance_conn):
    conn = advance_conn
    future_stamp = 10**18
    _advance_seed_factor(conn, last="2024-01-01", stamp=future_stamp)
    _advance_seed_factor(
        conn, first="2024-01-02", last="2024-01-02", cumulative=3, stamp=future_stamp
    )
    prefix = _advance_snapshot(conn)[0]
    result = advance_factor_coverage(
        conn, symbol="000001.SZ", verified_start="2024-01-03", verified_end="2024-01-06", events=[]
    )
    assert result == dict(
        changed_factor_dates=["2024-01-02"],
        changed_rows=1,
        changed_event_rows=0,
        affected_from="2024-01-03",
        affected_through="2024-01-06",
        valid_through="2024-01-06",
    )
    assert _advance_snapshot(conn)[0] == prefix
    assert conn.execute(
        "SELECT valid_through,cumulative_factor,updated_at FROM corporate_actions "
        "WHERE effective_date='2024-01-02'"
    ).fetchone() == ("2024-01-06", 3, future_stamp + 2)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM corporate_actions").fetchone()[0] == 0
    with pytest.raises(ValueError, match="outer write transaction"):
        advance_factor_coverage(
            conn,
            symbol="000001.SZ",
            verified_start="2024-01-03",
            verified_end="2024-01-04",
            events=[],
        )


def test_advance_migrated_sdk_events_keep_identity_and_per_share_units(advance_conn, monkeypatch):
    from aspool import enrichment
    from aspool.sqlite_daily_sync import fetch_tdx_action_interval

    conn = advance_conn
    _advance_seed_factor(conn)
    _advance_bar(conn, "2024-01-02", close=10)
    raw = dict(
        date="2024-01-03T00:00:00.000",
        category=1,
        fenhong=0.164,
        songzhuangu=0.0,
        peigu=0.0,
        peigujia=0.0,
        code="000001",
        market=0,
    )
    capital = dict(date=raw["date"], category=5, panhou_liutong=10000.0)
    for event in (raw, capital):
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,"
            "source_key,category,payload_json,updated_at) VALUES "
            "('000001.SZ','2024-01-03','event','tdx:xdxr',?,?,?,1)",
            (f"category={event['category']}:slot=1", event["category"], json.dumps(event)),
        )
    monkeypatch.setattr(
        enrichment, "_fetch", lambda *args: dict(events=[raw, capital], fetched_date="2024-01-04")
    )
    events = fetch_tdx_action_interval(None, "000001.SZ", "2024-01-03", "2024-01-04")
    assert [e["source_key"] for e in events] == ["category=1:slot=1", "category=5:slot=1"]
    result = advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-04",
        events=events,
    )
    assert result["changed_event_rows"] == 0
    assert conn.execute(
        "SELECT cumulative_factor FROM corporate_actions "
        "WHERE record_kind='factor' AND effective_date='2024-01-03'"
    ).fetchone()[0] == pytest.approx(2 * 10 / (10 - 0.164))
    snapshot = _advance_snapshot(conn)
    with pytest.raises(ValueError, match="Historical event set changed"):
        advance_factor_coverage(
            conn,
            symbol="000001.SZ",
            verified_start="2024-01-03",
            verified_end="2024-01-04",
            events=[dict(events[0], cash_dividend_per_share=0.2), events[1]],
        )
    assert _advance_snapshot(conn) == snapshot


def test_advance_cash_and_bonus_keep_anchor_scale_and_preserve_prefix(advance_conn):
    conn = advance_conn
    _advance_seed_factor(conn)
    _advance_bar(conn, "2024-01-02", close=10, calc="INVALID")
    _advance_bar(conn, "2024-01-03", close=50, volume=0, status="SUSPENDED")
    cash = dict(
        effective_date="2024-01-04",
        category=1,
        source_key="cash",
        cash_dividend_per_share=1,
        bonus_shares_per_share=0,
        rights_shares_per_share=0,
    )
    first = advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-05",
        events=[cash, dict(cash)],
    )
    assert first["affected_from"] == "2024-01-03"
    assert first["changed_event_rows"] == 1
    prefix = conn.execute(
        "SELECT * FROM corporate_actions WHERE record_kind='factor' AND effective_date='2024-01-01'"
    ).fetchone()
    _advance_bar(conn, "2024-01-05", close=9, volume=100, status=None, calc="NO_TRADE")
    second = advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-06",
        verified_end="2024-01-09",
        events=[_advance_event("2024-01-07", bonus=1, key="bonus")],
    )
    assert second["affected_from"] == "2024-01-06"
    assert second["changed_factor_dates"] == ["2024-01-04", "2024-01-07"]
    assert (
        conn.execute(
            "SELECT * FROM corporate_actions WHERE record_kind='factor' "
            "AND effective_date='2024-01-01'"
        ).fetchone()
        == prefix
    )
    factors = conn.execute(
        """SELECT effective_date,event_factor,cumulative_factor,valid_through,source,factor_basis
        FROM corporate_actions WHERE record_kind='factor' ORDER BY effective_date"""
    ).fetchall()
    assert len(factors) == 3
    assert factors[1][1:3] == pytest.approx((10 / 9, 2 * 10 / 9))
    assert factors[2][1:3] == pytest.approx((2, 4 * 10 / 9))
    assert factors[2][3] == "2024-01-09"
    assert factors[2][4] == "derived:anchor_continuation:tdx:xdxr"
    assert factors[2][5].startswith("anchor_continuation:")
    assert (
        conn.execute(
            "SELECT count(*) FROM corporate_actions WHERE record_kind='factor_anchor'"
        ).fetchone()[0]
        == 0
    )


def test_advance_repeat_and_verified_overlap_are_zero_writes(advance_conn):
    conn = advance_conn
    _advance_seed_factor(conn)
    _advance_bar(conn, "2024-01-02")
    event = _advance_event("2024-01-04", cash=1)
    advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-06",
        events=[event],
    )
    snapshot, changes = _advance_snapshot(conn), conn.total_changes
    repeat = advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-06",
        events=[event, dict(event)],
    )
    assert repeat["changed_rows"] == 0 and repeat["affected_from"] is None
    assert conn.total_changes == changes and _advance_snapshot(conn) == snapshot
    extend = advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-04",
        verified_end="2024-01-08",
        events=[event],
    )
    assert extend["affected_from"] == "2024-01-07"
    assert extend["changed_rows"] == 1
    changes = conn.total_changes
    advance_factor_coverage(
        conn, symbol="000001.SZ", verified_start="2024-01-07", verified_end="2024-01-08", events=[]
    )
    assert conn.total_changes == changes


def test_advance_rejects_missing_coverage_gaps_and_unverified_bounds(advance_conn):
    conn = advance_conn
    call = dict(
        symbol="000001.SZ", verified_start="2024-01-03", verified_end="2024-01-05", events=[]
    )
    with pytest.raises(ValueError, match="Missing selected factor anchor"):
        advance_factor_coverage(conn, **call)
    _advance_seed_factor(conn)
    for override, message in [
        ({"verified_start": "2024-01-04"}, "coverage gap"),
        ({"verified_start": "2023-12-31"}, "outside existing coverage"),
        ({"verified_end": "2024-01-01"}, "Invalid verified"),
        ({"verified_start": "2024-01-02"}, "Historical overlap"),
        ({"source": "unknown"}, "reliable event source"),
        ({"events": [dict(date="2024-01-04", category=7)]}, "Unsupported"),
        ({"events": [_advance_event("2024-01-06", cash=1)]}, "outside verified interval"),
        ({"events": [_advance_event("2024-01-04", cash=1)]}, "Missing prior effective"),
    ]:
        snapshot = _advance_snapshot(conn)
        with pytest.raises(ValueError, match=message):
            advance_factor_coverage(conn, **dict(call, **override))
        assert _advance_snapshot(conn) == snapshot
    _advance_seed_factor(conn, first="2024-01-04", last="2024-01-05")
    with pytest.raises(ValueError, match="coverage gap"):
        advance_factor_coverage(
            conn, **dict(call, verified_start="2024-01-06", verified_end="2024-01-07")
        )


def test_advance_historical_conflicts_and_write_failure_roll_back_savepoint(advance_conn):
    conn = advance_conn
    _advance_seed_factor(conn)
    _advance_bar(conn, "2024-01-02")
    event = _advance_event("2024-01-04", cash=1)
    advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-05",
        events=[event],
    )
    snapshot = _advance_snapshot(conn)
    for events in (
        [],
        [_advance_event("2024-01-04", cash=2)],
        [event, _advance_event("2024-01-03", cash=1, key="retro")],
    ):
        with pytest.raises(ValueError, match="Historical event|Retrospective event"):
            advance_factor_coverage(
                conn,
                symbol="000001.SZ",
                verified_start="2024-01-03",
                verified_end="2024-01-06",
                events=events,
            )
        assert _advance_snapshot(conn) == snapshot
    # A source updater may already have replaced its stored payload. Compare
    # against the original coverage receipt, not only that mutable event row.
    payload = conn.execute(
        "SELECT payload_json FROM corporate_actions WHERE record_kind='event'"
    ).fetchone()[0]
    revised = json.loads(payload)
    revised.update(fenhong=20, cash_dividend_per_share=2)
    conn.execute(
        "UPDATE corporate_actions SET payload_json=? WHERE record_kind='event'",
        (json.dumps(revised),),
    )
    mutated = _advance_snapshot(conn)
    with pytest.raises(ValueError, match="Historical event set changed"):
        advance_factor_coverage(
            conn,
            symbol="000001.SZ",
            verified_start="2024-01-03",
            verified_end="2024-01-05",
            events=[_advance_event("2024-01-04", cash=2)],
        )
    assert _advance_snapshot(conn) == mutated
    conn.execute(
        "UPDATE corporate_actions SET payload_json=? WHERE record_kind='event'", (payload,)
    )
    conn.execute("""CREATE TEMP TRIGGER fail_advance_event BEFORE INSERT ON corporate_actions
    WHEN NEW.record_kind='event' AND NEW.effective_date='2024-01-07'
    BEGIN SELECT RAISE(ABORT,'injected event write failure'); END""")
    _advance_bar(conn, "2024-01-06", close=9)
    with pytest.raises(sqlite3.IntegrityError, match="injected event write failure"):
        advance_factor_coverage(
            conn,
            symbol="000001.SZ",
            verified_start="2024-01-06",
            verified_end="2024-01-08",
            events=[_advance_event("2024-01-07", cash=1, key="later")],
        )
    assert _advance_snapshot(conn) == snapshot
    assert conn.in_transaction
    assert conn.execute(
        "SELECT close FROM daily_bars WHERE trade_date='2024-01-06'"
    ).fetchone() == (9,)


def test_advance_two_cash_dividends_during_suspension_use_prior_factor_scale(advance_conn):
    conn = advance_conn
    _advance_seed_factor(conn)
    _advance_bar(conn, "2024-01-02", close=10, calc="NO_TRADE")
    for day in ("2024-01-03", "2024-01-04", "2024-01-05"):
        _advance_bar(conn, day, close=10, volume=0, status="SUSPENDED", calc="TRADED")
    first_event = _advance_event("2024-01-03", cash=1, key="first")
    second_event = _advance_event("2024-01-05", cash=1, key="second")
    before = _advance_snapshot(conn)[0]
    advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-04",
        events=[first_event],
    )
    advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-05",
        verified_end="2024-01-06",
        events=[second_event],
    )
    assert _advance_snapshot(conn)[0] == before
    factors = conn.execute(
        "SELECT event_factor,cumulative_factor FROM corporate_actions "
        "WHERE record_kind='factor' ORDER BY effective_date"
    ).fetchall()
    assert factors[-1] == pytest.approx((9 / 8, 2 * 10 / 8))
    assert factors[-1][1] != pytest.approx(2 * (10 / 9) ** 2)
    changes = conn.total_changes
    result = advance_factor_coverage(
        conn,
        symbol="000001.SZ",
        verified_start="2024-01-03",
        verified_end="2024-01-06",
        events=[first_event, second_event],
    )
    assert result["changed_rows"] == 0 and conn.total_changes == changes
