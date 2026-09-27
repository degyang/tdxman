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
            "cash_dividend_per_share": 1.0,
            "bonus_shares_per_share": 0.0,
            "rights_shares_per_share": 0.0,
        }
    ]
    assert select_reference_pre_close(
        previous_close=10, actions=cash, actions_covered=True
    ) == pytest.approx((9.0, "derived:previous_close+corporate_actions"))
    bonus = [{"date": "2024-01-02", "category": 1, "fenhong": 0, "songzhuangu": 1, "peigu": 0}]
    assert select_reference_pre_close(previous_close=10, actions=bonus, actions_covered=True)[
        0
    ] == pytest.approx(10 / 1.1)
    rights = [
        {
            "date": "2024-01-02",
            "category": 1,
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
        {"date": "2024-01-02", "category": 1, "fenhong": 1, "songzhuangu": 0, "peigu": 0},
        {
            "date": "2024-01-02",
            "category": 1,
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
            "cash_dividend_per_share": 1,
            "bonus_shares_per_share": 0,
            "rights_shares_per_share": 0,
        },
        {
            "date": "2024-01-03",
            "category": 1,
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
    with pytest.raises(ValueError, match="Conflicting"):
        build_selected_factors(
            anchors=[
                dict(effective_date="2024-01-02", source="a", source_cumulative_factor=2),
                dict(effective_date="2024-01-02", source="b", source_cumulative_factor=3),
            ],
            through="2024-01-10",
        )
    with pytest.raises(ValueError, match="Missing prior close"):
        build_selected_factors(
            events=[dict(date="2024-01-02", category=7)], prior_closes={}, through="2024-01-10"
        )


def test_reference_precedence_and_unknown_source():
    action = [
        {
            "date": "2024-01-02",
            "category": 1,
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
    assert select_is_st(name=None, name_as_of=None, trade_date="2024-01-02") == (None, None, None)
    with pytest.raises(ValueError, match="Conflicting"):
        select_is_st(dated=[(True, "a"), (False, "b")], trade_date="2024-01-02")


def test_atomic_writer_preserves_other_fields_and_repeat_is_noop(tmp_path):
    root = tmp_path / "db"
    with stock_connection(root, create=True, read_only=False) as conn:
        stamp = 100
        conn.execute(
            """INSERT INTO daily_bars(symbol,trade_date,close,name,name_as_of,updated_at)
            VALUES ('000001.SZ','2024-01-01',10,'甲','2024-01-01',?)""",
            (stamp,),
        )
        conn.execute(
            """INSERT INTO daily_bars(symbol,trade_date,close,name,name_as_of,updated_at)
            VALUES ('000001.SZ','2024-01-02',9,'ST甲','2024-01-02',?)""",
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
            actions=[action],
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
                "SELECT count(*) FROM corporate_actions WHERE record_kind='factor'"
            ).fetchone()[0]
            == 1
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
