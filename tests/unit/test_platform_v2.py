import sqlite3

import duckdb

from aspool import DataPool
from aspool.platform_v2 import (
    activate_platform_v2,
    mirror_platform_v2,
    platform_status,
    prepare_platform_v2,
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
        conn.execute(
            "INSERT INTO adjustment_factors VALUES "
            "('510300.SH','2026-09-28',1,'fixture')"
        )
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
    assert verify_platform_v2(tmp_path)["counts"]["market_regime_features"] == {
        "source": 1,
        "target": 1,
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
        conn.execute(
            "UPDATE daily_features SET is_st=1 WHERE symbol='000001.SZ'"
        )
        conn.commit()
    frame = DataPool(tmp_path).read_research_daily(
        symbols="000001.SZ", start="2026-09-28", end="2026-09-28",
        fields=["symbol", "date", "is_st"],
    )
    assert frame.is_st.tolist() == [False]
    qfq = DataPool(tmp_path).read_daily(
        symbols="000001.SZ", start="2026-09-27", end="2026-09-28",
        fields=["date", "close"], adjust="qfq",
    )
    assert qfq.close.tolist() == [20.0, 11.0]
    assert qfq.attrs["adjustment"] == "qfq"
    assert qfq.attrs["price_adjustment"] == "qfq"
    hfq = DataPool(tmp_path).read_daily(
        symbols="000001.SZ", start="2026-09-27", end="2026-09-28",
        fields=["date", "close"], adjust="hfq",
    )
    assert hfq.close.tolist() == [10.0, 5.5]
    assert DataPool(tmp_path).read_market_daily(
        start="2026-09-28", end="2026-09-28", fields=["period_key", "trading_count"]
    ).trading_count.tolist() == [1]
    with stock_connection(tmp_path, read_only=False) as conn:
        revision = conn.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()[0] + 1
        conn.execute(
            "UPDATE daily_features SET is_st=1,updated_at=10 WHERE symbol='000001.SZ'"
        )
        conn.execute(
            "UPDATE dataset_state SET revision=? WHERE dataset='stock_raw'", (revision,)
        )
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
        symbols="000001.SZ", start="2026-09-28", end="2026-09-28",
        fields=["is_st"],
    )
    assert frame.is_st.tolist() == [True]
    assert mirror_platform_v2(
        tmp_path, symbols=["000001.SZ"], dates=[], raw_revision=revision
    ) == {"mirrored": False, "reason": "revisions are unchanged"}
    rolled_back = rollback_platform_v2(tmp_path)
    assert rolled_back["layout_version"] == 1 and not rolled_back["activated"]
