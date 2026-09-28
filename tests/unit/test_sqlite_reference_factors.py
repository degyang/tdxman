import pytest

from aspool.sqlite_reference_factors import (
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
