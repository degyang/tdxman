import sqlite3

import duckdb

from aspool import DataPool
from aspool.platform_v2 import (
    activate_platform_v2,
    mirror_platform_v2,
    platform_status,
    prepare_platform_v2,
    reconcile_platform_v2,
    rollback_platform_v2,
    verify_platform_v2,
)
from aspool.sqlite_etf_store import DDL as ETF_DDL
from aspool.sqlite_index_store import DDL as INDEX_DDL
from aspool.sqlite_stock_store import stock_connection


def seed(root):
    with stock_connection(root, create=True, read_only=False) as conn:
        conn.execute(
            "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,volume,amount,"
            "updated_at) VALUES ('000001.SZ','2026-09-27',9,10,9,10,100,900,1)"
        )
        conn.execute(
            "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,volume,amount,"
            "updated_at) VALUES ('000001.SZ','2026-09-28',10,11,10,11,100,1000,1)"
        )
        conn.execute(
            "INSERT INTO daily_features(symbol,trade_date,calc_status,limit_status,"
            "pre_close,is_st,limit_up_price,limit_down_price,touch_limit_up,close_limit_up,"
            "touch_limit_down,close_limit_down,consecutive_up,prior_consecutive_up,"
            "streak_known,updated_at) VALUES ('000001.SZ','2026-09-27','TRADED','KNOWN',"
            "9,0,9.9,8.1,1,1,0,0,1,0,1,2)"
        )
        conn.execute(
            "INSERT INTO daily_features(symbol,trade_date,calc_status,limit_status,"
            "pre_close,is_st,limit_up_price,limit_down_price,touch_limit_up,close_limit_up,"
            "touch_limit_down,close_limit_down,consecutive_up,prior_consecutive_up,"
            "streak_known,updated_at) VALUES ('000001.SZ','2026-09-28','TRADED','KNOWN',"
            "10,0,11,9,1,1,0,0,1,0,1,2)"
        )
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) VALUES "
            "('000001.SZ','2026-09-27','factor','derived:test','selected',2,"
            "'2026-09-27','2026-09-27','fixture',3)"
        )
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) VALUES "
            "('000001.SZ','2026-09-28','factor','derived:test','selected',1,"
            "'2026-09-28','2026-09-28','fixture',3)"
        )
        conn.execute(
            "INSERT INTO market_daily_summary(frequency,period_key,scope,period_start,period_end,"
            "as_of,session_count,trading_count,updated_at) VALUES "
            "('D','2026-09-28','all_stocks','2026-09-28','2026-09-28','2026-09-28',1,1,4)"
        )
        conn.commit()
    with sqlite3.connect(root / "indices.sqlite") as conn:
        conn.executescript(INDEX_DDL)
    with sqlite3.connect(root / "etfs.sqlite") as conn:
        conn.executescript(ETF_DDL)
        conn.execute("INSERT INTO adjustment_factors VALUES ('510300.SH','2026-09-28',1,'fixture')")
    with duckdb.connect(str(root / "catalog.duckdb")) as conn:
        conn.execute("CREATE TABLE securities(symbol VARCHAR PRIMARY KEY)")


def test_shadow_migration_is_verified_resumable_and_not_activated(tmp_path):
    seed(tmp_path)
    first = prepare_platform_v2(tmp_path)
    assert first["verification"]["ready"]
    assert not first["activated"] and first["layout_version"] == 1
    assert first["features"]["stock_daily_features"]["rows"] == 2
    assert first["adjustments"] == {
        "stock_adjustment_factors": 2,
        "stock_factor_anchors": 0,
        "etf_adjustment_factors": 1,
    }
    second = prepare_platform_v2(tmp_path)
    assert second["features"]["stock_daily_features"]["reused"]
    mrf = verify_platform_v2(tmp_path)["counts"]["market_regime_features"]
    assert mrf["source"] == 1
    assert mrf["target"] == 1
    assert mrf["count_equal"]
    assert mrf["content_equal"] is None
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as adjustments:
        adjustments.execute(
            "UPDATE stock_adjustment_factors SET cumulative_factor=9 "
            "WHERE symbol='000001.SZ' AND effective_date='2026-09-27'"
        )
    stale = verify_platform_v2(tmp_path)
    assert stale["ready"] is False
    assert stale["counts"]["stock_adjustment_factors"]["content_equal"] is False
    reconciled = reconcile_platform_v2(tmp_path)
    assert reconciled["verification"]["ready"] is True
    assert reconciled["verification"]["counts"]["stock_adjustment_factors"] == {
        "source": 2,
        "target": 2,
        "content_equal": True,
    }
    status = platform_status(tmp_path)
    assert status["files"]["features.sqlite"]["bytes"] > 0
    with duckdb.connect(str(tmp_path / "catalog.duckdb"), read_only=True) as conn:
        assert conn.execute(
            "SELECT value FROM pool_metadata WHERE key='layout_version'"
        ).fetchone() == ("1",)

    activated = activate_platform_v2(tmp_path)
    assert activated["activated"] and activated["layout_version"] == 2
    repeated = prepare_platform_v2(tmp_path)
    assert repeated["activated"] and repeated["layout_version"] == 2
    assert repeated["verification"]["event_index_covering"]
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute("UPDATE daily_features SET is_st=1 WHERE symbol='000001.SZ'")
        conn.commit()
    frame = DataPool(tmp_path).read_research_daily(
        symbols="000001.SZ",
        start="2026-09-28",
        end="2026-09-28",
        fields=["symbol", "date", "is_st"],
    )
    assert frame.is_st.tolist() == [False]
    qfq = DataPool(tmp_path).read_daily(
        symbols="000001.SZ",
        start="2026-09-27",
        end="2026-09-28",
        fields=["date", "close"],
        adjust="qfq",
    )
    assert qfq.close.tolist() == [20.0, 11.0]
    assert qfq.attrs["adjustment"] == "qfq"
    assert qfq.attrs["price_adjustment"] == "qfq"
    hfq = DataPool(tmp_path).read_daily(
        symbols="000001.SZ",
        start="2026-09-27",
        end="2026-09-28",
        fields=["date", "close"],
        adjust="hfq",
    )
    assert hfq.close.tolist() == [10.0, 5.5]
    assert DataPool(tmp_path).read_market_daily(
        start="2026-09-28", end="2026-09-28", fields=["period_key", "trading_count"]
    ).trading_count.tolist() == [1]
    with stock_connection(tmp_path, read_only=False) as conn:
        revision = (
            conn.execute("SELECT revision FROM dataset_state WHERE dataset='stock_raw'").fetchone()[
                0
            ]
            + 1
        )
        conn.execute("UPDATE daily_features SET is_st=1,updated_at=10 WHERE symbol='000001.SZ'")
        conn.execute("UPDATE dataset_state SET revision=? WHERE dataset='stock_raw'", (revision,))
        conn.commit()
    mirrored = mirror_platform_v2(
        tmp_path,
        symbols=["000001.SZ"],
        dates=["2026-09-28"],
        raw_revision=revision,
    )
    assert mirrored == {
        "mirrored": True,
        "feature_rows": 1,
        "summary_rows": 1,
        "factor_rows": 2,
        "anchor_rows": 0,
    }
    frame = DataPool(tmp_path).read_research_daily(
        symbols="000001.SZ",
        start="2026-09-28",
        end="2026-09-28",
        fields=["is_st"],
    )
    assert frame.is_st.tolist() == [True]
    assert mirror_platform_v2(tmp_path, symbols=["000001.SZ"], dates=[], raw_revision=revision) == {
        "mirrored": True,
        "feature_rows": 0,
        "summary_rows": 0,
        "factor_rows": 2,
        "anchor_rows": 0,
    }
    rolled_back = rollback_platform_v2(tmp_path)
    assert rolled_back["layout_version"] == 1 and not rolled_back["activated"]


def test_pure_factor_mirror_does_not_advance_feature_status(tmp_path):
    """Dates=[] means only factors changed; stale features must not become READY."""
    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    # Set features to DIRTY to simulate a partial update
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        for dataset in ("stock_daily_features", "market_regime_features"):
            conn.execute("UPDATE feature_state SET status='DIRTY' WHERE dataset=?", (dataset,))
        conn.commit()
    result = mirror_platform_v2(
        tmp_path,
        symbols=["000001.SZ"],
        dates=[],
        raw_revision=99,
    )
    assert result["mirrored"] is True
    assert result["feature_rows"] == 0 and result["summary_rows"] == 0
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        for dataset in ("stock_daily_features", "market_regime_features"):
            row = conn.execute(
                "SELECT status,raw_revision FROM feature_state WHERE dataset=?",
                (dataset,),
            ).fetchone()
            assert row[0] == "DIRTY", f"{dataset} status changed to {row[0]}"
            assert row[1] != 99, f"{dataset} raw_revision advanced to {row[1]}"
    # Verify rejects DIRTY state — pool cannot be activated
    assert verify_platform_v2(tmp_path)["ready"] is False


class _CrashConnection:
    """Thin wrapper that delegates everything but can block commit() after N calls."""

    def __init__(self, conn, *, crash_after):
        self._conn = conn
        self._crash_after = crash_after
        self._commit_count = 0

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)

    def executemany(self, *a, **kw):
        return self._conn.executemany(*a, **kw)

    def executescript(self, *a, **kw):
        return self._conn.executescript(*a, **kw)

    def commit(self):
        self._commit_count += 1
        if self._commit_count > self._crash_after:
            raise RuntimeError("simulated crash before READY update")
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    @property
    def in_transaction(self):
        return self._conn.in_transaction

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_crash_before_ready_update_leaves_dirty(tmp_path):
    """Crash on READY commit → DIRTY, verify rejects, activate fails, reconcile restores."""
    import pytest

    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    activate_platform_v2(tmp_path)
    import aspool.platform_v2 as pv2

    original_connect = pv2._connect

    def crashing_connect(path, *, create=False):
        conn = original_connect(path, create=create)
        if str(path).endswith("features.sqlite"):
            return _CrashConnection(conn, crash_after=1)
        return conn

    with sqlite3.connect(tmp_path / "stocks.sqlite") as stocks:
        previous = stocks.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()[0]
        stocks.execute("UPDATE dataset_state SET revision=99 WHERE dataset='stock_raw'")
        stocks.commit()

    pv2._connect = crashing_connect
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            mirror_platform_v2(
                tmp_path,
                symbols=["000001.SZ"],
                dates=["2026-09-28"],
                raw_revision=99,
                previous_raw_revision=previous,
            )
    finally:
        pv2._connect = original_connect

    # DIRTY state preserved
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        for dataset in ("stock_daily_features", "market_regime_features"):
            assert (
                conn.execute(
                    "SELECT status FROM feature_state WHERE dataset=?", (dataset,)
                ).fetchone()[0]
                == "DIRTY"
            )

    # verify rejects DIRTY → ready=False
    assert verify_platform_v2(tmp_path)["ready"] is False
    # activate fails because verify not passed
    with pytest.raises(ValueError, match="verification"):
        activate_platform_v2(tmp_path)

    # adjustments committed before the crash
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as conn:
        assert conn.execute("SELECT count(*) FROM stock_adjustment_factors").fetchone()[0] == 2

    # reconcile restores everything
    reconciled = reconcile_platform_v2(tmp_path)
    assert reconciled["verification"]["ready"]
    assert reconciled["verification"]["feature_state"]["all_ready"]


def test_crash_on_dirty_commit_rolls_back_features(tmp_path):
    """Crash on DIRTY commit → rolled back to READY, reconcile restores."""
    import pytest

    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    activate_platform_v2(tmp_path)
    import aspool.platform_v2 as pv2

    original_connect = pv2._connect

    def crashing_connect(path, *, create=False):
        conn = original_connect(path, create=create)
        if str(path).endswith("features.sqlite"):
            return _CrashConnection(conn, crash_after=0)
        return conn

    with sqlite3.connect(tmp_path / "stocks.sqlite") as stocks:
        previous = stocks.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()[0]
        stocks.execute("UPDATE dataset_state SET revision=99 WHERE dataset='stock_raw'")
        stocks.commit()

    pv2._connect = crashing_connect
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            mirror_platform_v2(
                tmp_path,
                symbols=["000001.SZ"],
                dates=["2026-09-28"],
                raw_revision=99,
                previous_raw_revision=previous,
            )
    finally:
        pv2._connect = original_connect

    # DIRTY commit failed → rolled back, original READY state preserved
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        for dataset in ("stock_daily_features", "market_regime_features"):
            row = conn.execute(
                "SELECT status,raw_revision FROM feature_state WHERE dataset=?",
                (dataset,),
            ).fetchone()
            assert row[0] == "READY"  # original state preserved
            assert row[1] != 99  # raw_revision not advanced

    # verify detects raw_revision mismatch (stocks=99, features=old)
    result = verify_platform_v2(tmp_path)
    assert result["ready"] is False
    assert result["feature_state"]["raw_revision_match"] is False

    reconciled = reconcile_platform_v2(tmp_path)
    assert reconciled["verification"]["ready"]
    assert reconciled["verification"]["feature_state"]["all_ready"]


def test_reconcile_rebuilds_features_and_adjustments(tmp_path):
    """Corrupted features.sqlite (deleted + stale rows) is fully repaired."""
    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        # Delete one real row
        conn.execute("DELETE FROM stock_daily_features WHERE trade_date='2026-09-27'")
        # Insert a stale row that doesn't exist in source (simulates leftover)
        conn.execute(
            "INSERT INTO stock_daily_features(symbol,trade_date,calc_status,limit_status,"
            "pre_close,is_st,limit_up_price,limit_down_price,touch_limit_up,close_limit_up,"
            "touch_limit_down,close_limit_down,consecutive_up,prior_consecutive_up,"
            "streak_known,updated_at) VALUES ('STALE.SZ','2026-09-20','TRADED','KNOWN',"
            "9,0,9.9,8.1,0,0,0,0,0,0,1,1)"
        )
        conn.commit()
    verify = verify_platform_v2(tmp_path, deep=True)
    assert verify["ready"] is False
    # EXCEPT detects both the missing row and the extra stale row
    sdf = verify["counts"]["stock_daily_features"]
    assert sdf["extra"] > 0 or sdf["missing"] > 0
    assert not sdf["content_equal"]

    reconciled = reconcile_platform_v2(tmp_path)
    assert reconciled["features"]["stock_daily_features"]["rows"] == 2
    assert reconciled["verification"]["ready"]
    # Stale row is gone
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM stock_daily_features WHERE symbol='STALE.SZ'"
            ).fetchone()[0]
            == 0
        )


def test_verify_detects_adjustment_content_mismatch(tmp_path):
    """Content change in adjustments detected even when row count is identical."""
    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as conn:
        conn.execute(
            "UPDATE stock_adjustment_factors SET cumulative_factor=999 "
            "WHERE symbol='000001.SZ' AND effective_date='2026-09-27'"
        )
        conn.commit()
    result = verify_platform_v2(tmp_path)
    assert result["ready"] is False
    assert result["counts"]["stock_adjustment_factors"]["content_equal"] is False


def test_verify_detects_feature_content_mismatch(tmp_path):
    """EXCEPT detects single-row is_st change (same row count)."""
    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        conn.execute(
            "UPDATE stock_daily_features SET is_st=1 "
            "WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        )
        conn.commit()
    result = verify_platform_v2(tmp_path, deep=True)
    assert result["ready"] is False
    sdf = result["counts"]["stock_daily_features"]
    assert sdf["source"] == sdf["target"]  # same row count
    assert not sdf["content_equal"]
    assert sdf["extra"] > 0 or sdf["missing"] > 0


def test_verify_rejects_dirty_feature_state(tmp_path):
    """verify returns ready=False when feature_state is DIRTY even if content matches."""
    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        conn.execute("UPDATE feature_state SET status='DIRTY' WHERE dataset='stock_daily_features'")
        conn.commit()
    result = verify_platform_v2(tmp_path)
    assert result["ready"] is False
    assert result["feature_state"]["all_ready"] is False


def test_verify_rejects_revision_mismatch(tmp_path):
    """verify returns ready=False when feature_state revision doesn't match source."""
    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        # raw_revision mismatch
        conn.execute(
            "UPDATE feature_state SET raw_revision=9999 WHERE dataset='stock_daily_features'"
        )
        conn.commit()
    result = verify_platform_v2(tmp_path)
    assert result["ready"] is False
    assert result["feature_state"]["raw_revision_match"] is False


def test_later_partial_update_cannot_hide_an_unmirrored_commit(tmp_path):
    import pytest

    from aspool.api_contract import DataPoolError
    from aspool.platform_v2 import require_current_mirror

    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    activate_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "stocks.sqlite") as conn:
        previous = conn.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()[0]
        conn.execute("UPDATE daily_features SET is_st=1 WHERE trade_date='2026-09-27'")
        conn.execute("UPDATE dataset_state SET revision=revision+1")
    # A source commit survived but its mirror never ran. The next writer must stop.
    with pytest.raises(DataPoolError) as error:
        require_current_mirror(tmp_path)
    assert error.value.code == "DERIVED_NOT_READY"
    with sqlite3.connect(tmp_path / "stocks.sqlite") as conn:
        conn.execute("UPDATE daily_features SET is_st=1 WHERE trade_date='2026-09-28'")
        conn.execute("UPDATE dataset_state SET revision=revision+1")
    with pytest.raises(DataPoolError, match="predecessor"):
        mirror_platform_v2(
            tmp_path,
            symbols=["000001.SZ"],
            dates=["2026-09-28"],
            raw_revision=previous + 2,
        )
    with pytest.raises(DataPoolError) as error:
        DataPool(tmp_path).read_research_daily(
            symbols="000001.SZ",
            start="2026-09-27",
            end="2026-09-27",
            fields=["is_st"],
        )
    assert error.value.code == "DERIVED_NOT_READY"
    assert reconcile_platform_v2(tmp_path)["verification"]["ready"]
    require_current_mirror(tmp_path)
    frame = DataPool(tmp_path).read_research_daily(
        symbols="000001.SZ",
        start="2026-09-27",
        end="2026-09-28",
        fields=["is_st"],
    )
    assert frame.is_st.tolist() == [True, True]


def test_cli_deep_verification_detects_equal_count_corruption(tmp_path):
    import json

    from click.testing import CliRunner

    from aspool.cli import cli

    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        conn.execute("UPDATE stock_daily_features SET is_st=1")
    runner = CliRunner()
    shallow = runner.invoke(cli, ["platform", "verify", "--root", str(tmp_path)])
    assert shallow.exit_code == 0
    receipt = json.loads(shallow.output)
    assert receipt["verification_level"] == "shallow"
    assert receipt["counts"]["stock_daily_features"]["content_equal"] is None
    deep = runner.invoke(cli, ["platform", "verify", "--deep", "--root", str(tmp_path)])
    assert deep.exit_code == 1
    assert '"content_equal": false' in deep.output


def test_factor_commits_and_mirrors_hold_one_writer_lock(tmp_path, monkeypatch):
    from datetime import date

    import pandas as pd

    import aspool.platform_v2 as platform
    from aspool.pool import _holds_write_lock
    from aspool.sqlite_daily_sync import sync_daily_source

    seed(tmp_path)
    with sqlite3.connect(tmp_path / "stocks.sqlite") as conn:
        conn.execute(
            "UPDATE corporate_actions "
            "SET factor_basis='source_cumulative_factor:source_anchor:test'"
        )
    prepare_platform_v2(tmp_path)
    calls = []
    actual = platform.mirror_platform_v2

    def checked_mirror(root, **kwargs):
        calls.append(_holds_write_lock(root))
        return actual(root, **kwargs)

    monkeypatch.setattr(platform, "mirror_platform_v2", checked_mirror)

    class Quote:
        def get_daily(self, *args, **kwargs):
            return pd.DataFrame(
                [
                    dict(
                        symbol="000001.SZ",
                        date=date(2026, 9, 29),
                        open=11,
                        high=12,
                        low=11,
                        close=12,
                        volume=100,
                        amount=1200,
                        pre_close=11,
                        is_st=False,
                        trading_status="TRADING",
                    )
                ]
            )

    result = sync_daily_source(
        tmp_path,
        client=Quote(),
        symbols=["000001.SZ"],
        source="tdxman:quote",
        market_sessions=["2026-09-27", "2026-09-28", "2026-09-29"],
        as_of="2026-09-29",
        start="2026-09-29",
        end="2026-09-29",
        action_fetcher=lambda *a: [],
    )
    assert not result["failed"]
    assert calls == [True, True]
    assert verify_platform_v2(tmp_path, deep=True)["ready"]


def test_missing_summary_is_repaired_locally_and_idempotently(tmp_path):
    from aspool.sqlite_summary_repair import repair_missing_market_summaries

    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    activate_platform_v2(tmp_path)
    with sqlite3.connect(tmp_path / "stocks.sqlite") as conn:
        original_bars = conn.execute("SELECT * FROM daily_bars").fetchall()
        conn.execute("DELETE FROM market_daily_summary")
    with sqlite3.connect(tmp_path / "features.sqlite") as conn:
        conn.execute("DELETE FROM market_regime_features")
    result = repair_missing_market_summaries(tmp_path, ["2026-09-28"])
    assert result == {"dates": ["2026-09-28"], "changed_rows": 2}
    assert verify_platform_v2(tmp_path, deep=True)["ready"]
    frame = DataPool(tmp_path).read_market_daily(start="2026-09-28", end="2026-09-28")
    assert not frame.empty
    assert repair_missing_market_summaries(tmp_path, ["2026-09-28"]) == {
        "dates": [],
        "changed_rows": 0,
    }
    with sqlite3.connect(tmp_path / "stocks.sqlite") as conn:
        assert conn.execute("SELECT * FROM daily_bars").fetchall() == original_bars


def test_non_price_event_commit_preserves_readiness_without_rebuilding_features(tmp_path):
    from aspool.platform_v2 import require_current_mirror
    from aspool.pool import pool_lock
    from aspool.sqlite_daily_update import apply_daily_changes

    seed(tmp_path)
    prepare_platform_v2(tmp_path)
    with pool_lock(tmp_path, write=True), stock_connection(tmp_path, read_only=False) as conn:
        require_current_mirror(tmp_path)
        before = conn.execute("SELECT * FROM daily_features").fetchall()
        applied = apply_daily_changes(
            conn,
            market_sessions=["2026-09-27", "2026-09-28"],
            merge_event_revisions=True,
            factor_extensions=[
                dict(
                    symbol="000001.SZ",
                    verified_start="2026-09-27",
                    verified_end="2026-09-28",
                    source="tdx:xdxr",
                    events=[
                        dict(
                            effective_date="2026-09-28",
                            category=9,
                            source_key="shares",
                            panhou_liutong=100,
                        )
                    ],
                )
            ],
        )
        assert applied["affected_sessions"] == []
        assert applied["raw_revision"] == applied["previous_raw_revision"] + 1
        mirror_platform_v2(
            tmp_path,
            symbols=["000001.SZ"],
            dates=[],
            raw_revision=applied["raw_revision"],
            previous_raw_revision=applied["previous_raw_revision"],
        )
        assert conn.execute("SELECT * FROM daily_features").fetchall() == before
        require_current_mirror(tmp_path)
    assert verify_platform_v2(tmp_path, deep=True)["ready"]
