"""Calendar ratios, retained HY2/GN provenance and canonical recovery contracts."""

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from aspool import DataPool
from aspool.api_contract import DataPoolError
from aspool.sqlite_board_daily import (
    fetch_board_category,
    publish_board_snapshot,
    recompute_board_daily,
    record_board_failure,
)
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_six_dimension import save_sessions, upgrade_six_dimension_schema, volume_inputs
from aspool.sqlite_stock_store import stock_connection
from tests.unit.test_sqlite_market_summary import observation, summary
from tests.unit.test_storage_single_authority import canonical

canonical_pool = canonical

DAYS = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(12)]


def volume_row(
    conn,
    day,
    *,
    code=1,
    volume=100,
    status="TRADING",
    source="test:daily",
    calc="TRADED",
    quote=999,
):
    symbol = f"{code:06d}.SZ"
    conn.execute(
        "INSERT INTO "
        "daily_bars(symbol,trade_date,open,high,low,close,volume,amount,vol_ratio,"
        "vol_ratio_source,updated_at) "
        'VALUES (?,?,10,10,10,10,?,100,?,"tdxman:quote",1)',
        (symbol, day, volume, quote),
    )
    conn.execute(
        "INSERT INTO "
        "daily_features(symbol,trade_date,calc_status,trading_status,"
        "trading_status_source,updated_at) "
        "VALUES (?,?,?,?,?,1)",
        (symbol, day, calc, status, source),
    )


@pytest.mark.parametrize(
    "volumes,expected",
    [
        ([100, 100, 100, 100, 100, 150], 1.5),
        ([100, 100, 100, 100, 100, 0], 0),
        ([0, 0, 0, 0, 0, 150], None),
        ([100, 100, 100, 100, None, 150], None),
        ([100, 100, 100, 100, float("inf"), 150], None),
        ([100, 100, 100, 100, -1, 150], None),
    ],
)
def test_calendar_ratio_excludes_today_and_quote(tmp_path, volumes, expected):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        save_sessions(conn, DAYS)
        for day, volume in zip(DAYS, volumes):
            volume_row(conn, day, volume=volume)
        ratios, reads, ready, _ = volume_inputs(conn, DAYS[5], 100)
        assert ready and reads == 6
        assert ratios.get("000001.SZ") == expected


def test_unknown_missing_suspension_and_insufficient_predecessors(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        save_sessions(conn, DAYS)
        for day in DAYS[:6]:
            volume_row(conn, day, volume=200 if day == DAYS[5] else 100)
        assert volume_inputs(conn, DAYS[4], 100)[0] == {}
        # A sourced suspension without a raw bar contributes zero.
        conn.execute("DELETE FROM daily_bars WHERE trade_date=?", (DAYS[2],))
        conn.execute(
            'UPDATE daily_features SET calc_status="NO_TRADE",trading_status="SUSPENDED" WHERE '
            "trade_date=?",
            (DAYS[2],),
        )
        assert volume_inputs(conn, DAYS[5], 100)[0]["000001.SZ"] == 2.5
        conn.execute(
            "UPDATE daily_features SET trading_status_source=NULL WHERE trade_date=?", (DAYS[2],)
        )
        assert volume_inputs(conn, DAYS[5], 100)[0] == {}
        conn.execute(
            "UPDATE daily_features SET "
            'trading_status="NOT_LISTED",trading_status_source="test:daily" WHERE trade_date=?',
            (DAYS[2],),
        )
        assert volume_inputs(conn, DAYS[5], 100)[0] == {}
        # Earlier data cannot fill a missing market slot.
        conn.execute("DELETE FROM daily_features WHERE trade_date=?", (DAYS[2],))
        volume_row(conn, "2023-12-31")
        assert volume_inputs(conn, DAYS[5], 100)[0] == {}
        with pytest.raises(DataPoolError) as error:
            volume_inputs(conn, DAYS[5], 2)
        assert error.value.code == "LOCAL_UPDATE_BUDGET_EXCEEDED"


def test_new_fields_leave_ordinary_median_and_valid_denominators_unchanged(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        save_sessions(conn, ["2024-01-02"])
        for code, close in enumerate((9.8, 9.9, 10.1, 10.2), 1):
            observation(conn, code, close=close)
        observation(conn, 5, pre=None, limit="UNKNOWN", streak=None)
        recompute_daily_summary(conn, trade_date="2024-01-02")
        row = summary(conn)
        assert row["median_return"] == 0
        assert row["upper_median_return"] == 0.01
        assert row["trading_count"] == 5 and row["valid_return_count"] == 4
        assert row["vol_ratio_5d_valid_count"] == row["high_vol_ratio_5d_count"] == 0
        assert row["avg_vol_ratio_5d"] is None
        conn.execute('DELETE FROM daily_features WHERE symbol="000004.SZ"')
        recompute_daily_summary(conn, trade_date="2024-01-02")
        assert summary(conn)["upper_median_return"] == -0.01
        assert summary(conn)["median_return"] == -0.01
    meta = DataPool(tmp_path).describe_market_fields(
        fields=["upper_median_return", "avg_vol_ratio_5d"]
    )
    assert meta["upper_median_return"]["unit"] == "ratio"
    assert "quote ratios ignored" in meta["avg_vol_ratio_5d"]["definition"]
    with pytest.raises(DataPoolError):
        DataPool(tmp_path).describe_market_fields(frequency="W")


def test_ratio_summary_scope_threshold_and_real_zero(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        save_sessions(conn, DAYS[:6])
        for code, (volume, st) in enumerate(((150, 0), (100, 1), (0, None)), 1):
            for day in DAYS[:6]:
                volume_row(conn, day, code=code, volume=volume if day == DAYS[5] else 100)
            conn.execute(
                "UPDATE daily_features SET "
                'limit_status="UNKNOWN",limit_reason="missing_reference",pre_close=10,is_st=? '
                "WHERE symbol=?",
                (st, f"{code:06d}.SZ"),
            )
        recompute_daily_summary(conn, trade_date=DAYS[5])
        all_stocks, ex_st = summary(conn, day=DAYS[5]), summary(conn, "exclude_known_st", DAYS[5])
        assert all_stocks["avg_vol_ratio_5d"] == pytest.approx(2.5 / 3)
        assert ex_st["avg_vol_ratio_5d"] == 0.75
        assert all_stocks["high_vol_ratio_5d_count"] == ex_st["high_vol_ratio_5d_count"] == 1
        assert all_stocks["vol_ratio_5d_valid_count"] == 3
        assert ex_st["vol_ratio_5d_valid_count"] == 2


def boards():
    return [
        dict(
            board_id="B", board_name="B industry", members=["000001.SZ", "000001.SZ", "000002.SZ"]
        ),
        dict(
            board_id="A", board_name="A industry", members=["000002.SZ", "000003.SZ", "000009.SZ"]
        ),
    ]


def test_board_dedup_overlap_scopes_missing_returns_and_noop(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1, close=11, up=1, touch=1)
        observation(conn, 2, close=10.2, st=1)
        observation(conn, 3, pre=None, limit="UNKNOWN", streak=None)
        conn.commit()
        identity = publish_board_snapshot(
            conn, kind="industry", boards=boards(), as_of="2026-10-01T00:00:00Z"
        )
        conn.execute("BEGIN IMMEDIATE")
        assert recompute_board_daily(conn, day="2024-01-02") > 0
        conn.commit()
        before = DataPool(tmp_path).read_board_daily(start="2024-01-02", end="2024-01-02")
        assert list(before["board_id"]) == ["A", "B"]
        a, b = before.to_dict("records")
        assert (a["member_count"], a["trading_member_count"], a["valid_return_count"]) == (3, 2, 1)
        assert b["member_count"] == 2 and b["avg_return"] == pytest.approx(0.06)
        assert before.attrs["categories"]["concept"]["status"] == "not_provided"
        assert a["membership_basis"] == "current_snapshot"
        ex_st = DataPool(tmp_path).read_board_daily(
            start="2024-01-02", end="2024-01-02", scope="exclude_known_st"
        )
        assert ex_st.iloc[0]["trading_member_count"] == 1 and pd.isna(ex_st.iloc[0]["avg_return"])
        assert (
            next(item for item in ex_st.attrs["category_status"] if item["kind"] == "industry")[
                "mapped_trading_count"
            ]
            == 2
        )
        assert (
            publish_board_snapshot(
                conn, kind="industry", boards=boards(), as_of="2026-10-02T00:00:00Z"
            )
            == identity
        )
        conn.execute("BEGIN IMMEDIATE")
        assert recompute_board_daily(conn, day="2024-01-02", replace_snapshot=True) == 0
        conn.commit()
        pd.testing.assert_frame_equal(
            DataPool(tmp_path).read_board_daily(start="2024-01-02", end="2024-01-02"), before
        )
        # Group mean is unchanged but its individual returns changed.
        conn.execute(
            'UPDATE daily_bars SET close=close + CASE symbol WHEN "000001.SZ" THEN -.1 ELSE .1 END '
            'WHERE symbol IN ("000001.SZ","000002.SZ")'
        )
        recompute_board_daily(conn, day="2024-01-02")
        conn.commit()
        changed = DataPool(tmp_path).read_board_daily(start="2024-01-02", end="2024-01-02")
        assert changed.iloc[1]["avg_return"] == pytest.approx(0.06)
        assert changed.iloc[1]["updated_at"] > b["updated_at"]


def test_snapshot_update_does_not_silently_rewrite_history_failure_retains_members(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1)
        conn.commit()
        first = publish_board_snapshot(conn, kind="concept", boards=boards(), as_of="2026-10-01")
        conn.execute("BEGIN IMMEDIATE")
        recompute_board_daily(conn, day="2024-01-02")
        conn.commit()
        publish_board_snapshot(
            conn,
            kind="concept",
            boards=[dict(board_id="C", board_name="new", members=["000001.SZ"])],
            as_of="2026-10-02",
        )
        conn.execute("BEGIN IMMEDIATE")
        recompute_board_daily(conn, day="2024-01-02")
        conn.commit()
        retained = DataPool(tmp_path).read_board_daily(start="2024-01-02", end="2024-01-02")
        assert set(retained["snapshot_id"]) == {first}
        record_board_failure(conn, kind="concept", as_of="2026-10-03", error="offline")
        conn.execute("BEGIN IMMEDIATE")
        recompute_board_daily(conn, day="2024-01-02", replace_snapshot=True)
        conn.commit()
        frame = DataPool(tmp_path).read_board_daily(start="2024-01-02", end="2024-01-02")
        assert list(frame["board_id"]) == ["C"]
        assert frame.attrs["categories"]["concept"]["status"] == "failed"
        empty = DataPool(tmp_path).read_board_daily(start="2024-01-03", end="2024-01-03")
        assert empty.empty and empty.attrs["uncomputed_date_status"] == "not_computed"


def test_computed_empty_and_source_pagination_contract(tmp_path):
    from tdxman.mac.enums import BoardType, SortOrder, SortType

    class Client:
        def get_board_list(self, kind):
            assert kind == BoardType.HY2
            return pd.DataFrame([dict(code="881001", name="HY2")])

        def get_board_members(self, code, **kwargs):
            assert kwargs == dict(sort_type=SortType.CODE, sort_order=SortOrder.ASC)
            return pd.DataFrame(
                [dict(code="000001"), dict(code="000001"), dict(code="600000"), dict(code="920001")]
            )

    result = fetch_board_category(Client(), "industry")
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        identity = publish_board_snapshot(conn, kind="industry", boards=result, as_of="2026-10-01")
        members = json.loads(
            conn.execute(
                "SELECT members_json FROM board_snapshots WHERE snapshot_id=?", (identity,)
            ).fetchone()[0]
        )
        assert members == ["000001.SZ", "600000.SH", "920001.BJ"]
        conn.execute("BEGIN IMMEDIATE")
        recompute_board_daily(conn, day="2024-01-02")
        conn.commit()
    frame = DataPool(tmp_path).read_board_daily(
        start="2024-01-02", end="2024-01-02", fields=["board_id", "avg_return"]
    )
    assert list(frame.columns) == ["board_id", "avg_return"]
    assert (
        next(item for item in frame.attrs["category_status"] if item["kind"] == "industry")[
            "status"
        ]
        == "computed_empty"
    )
    with pytest.raises(DataPoolError):
        DataPool(tmp_path).read_board_daily(start="2024-01-02", end="2024-01-02", fields=["score"])


@pytest.mark.parametrize("phase", ["after_intent", "after_sqlite_commit"])
def test_board_snapshot_recovers_with_canonical_publication(canonical_pool, monkeypatch, phase):
    canonical = canonical_pool
    from aspool import sqlite_publication as publication
    from aspool.sqlite_publication import recover_publication

    upgrade_six_dimension_schema(canonical)

    def fail(observed):
        if observed == phase:
            raise RuntimeError("injected")

    monkeypatch.setattr(publication, "_fault", fail)
    with stock_connection(canonical, read_only=False) as conn:
        with pytest.raises(RuntimeError, match="injected"):
            publish_board_snapshot(conn, kind="industry", boards=boards(), as_of="2026-10-01")
    with pytest.raises(DataPoolError) as error:
        DataPool(canonical).read_board_daily(start="2026-09-28", end="2026-09-28")
    assert error.value.code == "RECOVERY_REQUIRED"
    monkeypatch.setattr(publication, "_fault", lambda phase: None)
    assert recover_publication(canonical)["recovered"]
    with stock_connection(canonical, read_only=False) as conn:
        from aspool.sqlite_canonical import canonical_operation

        with canonical_operation(conn, "board_update"):
            conn.execute("BEGIN IMMEDIATE")
            recompute_board_daily(conn, day="2026-09-28")
            conn.commit()
    frame = DataPool(canonical).read_board_daily(start="2026-09-28", end="2026-09-28")
    assert not frame.empty and frame.attrs["categories"]["industry"]["status"] == "ready"


def test_volume_revision_uses_five_market_slots_even_with_missing_bars(tmp_path):
    from tests.unit.test_sqlite_daily_update import DAYS as sessions
    from tests.unit.test_sqlite_daily_update import SYMBOL, apply, prepare

    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, missing=[sessions[26]])
        before = dict(
            conn.execute(
                "SELECT period_key,updated_at FROM market_daily_summary WHERE scope='all_stocks'"
            )
        )
        result = apply(conn, bars=[dict(symbol=SYMBOL, trade_date=sessions[25], volume=150)])
        assert set(result["affected_sessions"]) <= set(sessions[25:31])
        after = dict(
            conn.execute(
                "SELECT period_key,updated_at FROM market_daily_summary WHERE scope='all_stocks'"
            )
        )
        assert after[sessions[31]] == before[sessions[31]]
        assert after[sessions[24]] == before[sessions[24]]
        assert summary(conn, day=sessions[25])["avg_vol_ratio_5d"] == 1.5
        assert summary(conn, day=sessions[27])["vol_ratio_5d_valid_count"] == 0
        assert summary(conn, day=sessions[31])["avg_vol_ratio_5d"] is None
        assert summary(conn, day=sessions[32])["avg_vol_ratio_5d"] == 1
        again = apply(conn, bars=[dict(symbol=SYMBOL, trade_date=sessions[25], volume=150)])
        assert again["affected_sessions"] == [] and again["summary_rows"] == 0


def test_missing_one_stock_ratio_does_not_block_other_stock_inputs(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        save_sessions(conn, DAYS[:6])
        for code in (1, 2):
            for day in DAYS[:6]:
                volume_row(
                    conn, day, code=code, volume=None if code == 1 and day == DAYS[4] else 100
                )
        conn.execute("UPDATE daily_features SET limit_status='UNKNOWN',pre_close=10,is_st=0")
        recompute_daily_summary(conn, trade_date=DAYS[5])
        row = summary(conn, day=DAYS[5])
        assert row["trading_count"] == 2 and row["valid_return_count"] == 2
        assert row["vol_ratio_5d_valid_count"] == 1 and row["avg_vol_ratio_5d"] == 1
        assert row["avg_return"] == row["median_return"] == row["upper_median_return"] == 0


def test_uninitialized_calendar_is_not_computed_not_a_zero_sample(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1)
        recompute_daily_summary(conn, trade_date="2024-01-02")
        row = summary(conn)
        assert row["upper_median_return"] == 0
        assert row["vol_ratio_5d_valid_count"] is row["high_vol_ratio_5d_count"] is None


def test_explicit_extension_preserves_old_columns_and_rejects_changed_sample(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        save_sessions(conn, ["2024-01-02"])
        observation(conn, 1, close=10.2)
        recompute_daily_summary(conn, trade_date="2024-01-02")
        conn.execute(
            "UPDATE market_daily_summary SET upper_median_return=NULL,avg_return=123 "
            "WHERE frequency='D'"
        )
        before = summary(conn)
        assert recompute_daily_summary(
            conn, trade_date="2024-01-02", extend_only=True
        )["changed_rows"] == 2
        after = summary(conn)
        excluded = {
            "upper_median_return", "avg_vol_ratio_5d", "vol_ratio_5d_valid_count",
            "high_vol_ratio_5d_count", "updated_at",
        }
        assert {k: v for k, v in before.items() if k not in excluded} == {
            k: v for k, v in after.items() if k not in excluded
        }
        assert after["upper_median_return"] == .02
        assert recompute_daily_summary(
            conn, trade_date="2024-01-02", extend_only=True
        )["changed_rows"] == 0
        observation(conn, 2, close=10.1)
        with pytest.raises(DataPoolError) as error:
            recompute_daily_summary(conn, trade_date="2024-01-02", extend_only=True)
        assert error.value.code == "SOURCE_CHANGED"


def test_historical_extension_uses_one_feature_transaction(canonical_pool, monkeypatch):
    from aspool import sqlite_publication
    from scripts.ops.backfill_six_dimension_inputs import initialize_day

    root = canonical_pool
    upgrade_six_dimension_schema(root)
    from aspool.sqlite_canonical import canonical_operation
    with stock_connection(root, read_only=False) as conn:
        with canonical_operation(conn, "summary_recompute"):
            conn.execute("BEGIN IMMEDIATE")
            recompute_daily_summary(conn, trade_date="2026-09-28")
            conn.execute("UPDATE market_daily_summary SET upper_median_return=NULL")
            conn.commit()
    with stock_connection(root) as conn:
        raw_before = conn.execute("SELECT * FROM dataset_state").fetchall()
        feature_before = conn.execute("SELECT * FROM features.feature_state").fetchall()
        adjustment_before = conn.execute("SELECT * FROM adjustments.adjustment_state").fetchall()
    def reject_cross_file(*_args, **_kwargs):
        raise AssertionError("Single-file initialization must not stage a cross-file intent")
    monkeypatch.setattr(sqlite_publication, "prepare_intent", reject_cross_file)
    with stock_connection(root, read_only=False) as conn:
        initialize_day(
            conn, day="2026-09-28", sessions=["2026-09-27", "2026-09-28"],
            extend_only=True,
        )
    with stock_connection(root) as conn:
        assert conn.execute("SELECT * FROM dataset_state").fetchall() == raw_before
        assert conn.execute("SELECT * FROM features.feature_state").fetchall() == feature_before
        assert (
            conn.execute("SELECT * FROM adjustments.adjustment_state").fetchall()
            == adjustment_before
        )
        assert summary(conn, day="2026-09-28")["upper_median_return"] is not None
    from aspool.sqlite_canonical import canonical_operation
    with stock_connection(root, read_only=False) as conn:
        with canonical_operation(conn, "six_dimension_extend"):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE daily_bars SET close=99 WHERE trade_date='2026-09-28'")
            with pytest.raises(DataPoolError) as error:
                conn.commit()
            assert error.value.code == "WRITE_SCOPE_REQUIRED"
    with stock_connection(root) as conn:
        assert conn.execute(
            "SELECT close FROM daily_bars WHERE trade_date='2026-09-28'"
        ).fetchone()[0] != 99


def test_st_default_repair_is_atomic_and_rejects_raw_writes(canonical_pool):
    from aspool.sqlite_canonical import canonical_operation

    with stock_connection(canonical_pool, read_only=False) as conn:
        before = conn.execute("SELECT * FROM dataset_state").fetchall()
        factors = conn.execute("SELECT * FROM adjustments.adjustment_state").fetchall()
        with canonical_operation(conn, "st_default_repair"):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE daily_features SET is_st=0,is_st_source='assumed:not_st'")
            conn.commit()
        assert conn.execute("SELECT * FROM dataset_state").fetchall() == before
        assert conn.execute("SELECT * FROM adjustments.adjustment_state").fetchall() == factors
        with canonical_operation(conn, "st_default_repair"):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE daily_bars SET close=99")
            with pytest.raises(DataPoolError) as error:
                conn.commit()
            assert error.value.code == "WRITE_SCOPE_REQUIRED"
        assert conn.execute("SELECT COUNT(*) FROM daily_bars WHERE close=99").fetchone()[0] == 0
