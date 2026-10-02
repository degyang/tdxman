"""Behavioral checks for logical base-data export."""

import json
import sqlite3
from pathlib import Path

import pytest

from aspool.base_delta import export_delta
from aspool.sqlite_publication import pending_path


def make_pool(root, rows):
    root.mkdir()
    with sqlite3.connect(root / "stocks.sqlite") as c:
        c.execute(
            "CREATE TABLE daily_bars(symbol TEXT,trade_date TEXT,close REAL,"
            "updated_at INTEGER,PRIMARY KEY(symbol,trade_date))"
        )
        c.executemany("INSERT INTO daily_bars VALUES (?,?,?,?)", rows)


def run(tmp_path, **kwargs):
    return export_delta(
        tmp_path / "source",
        tmp_path / "target",
        datasets=["stock-bars"],
        start="2026-09-29",
        end="2026-09-30",
        symbols=["000001.SZ"],
        output=tmp_path / "delta.json",
        **kwargs,
    )


def test_export_changes_preserves_receiver_and_does_not_infer_deletion(tmp_path):
    source = [("000001.SZ", "2026-09-30", 11, 2)]
    target = [("000001.SZ", "2026-09-29", 9, 1), ("000001.SZ", "2026-09-30", 10, 1)]
    make_pool(tmp_path / "source", source)
    make_pool(tmp_path / "target", target)
    result = run(tmp_path)["datasets"][0]
    assert result["changes"][0]["before"]["close"] == 10
    assert result["changes"][0]["after"]["close"] == 11
    assert result["source_absent_keys"] == [["000001.SZ", "2026-09-29"]]
    with sqlite3.connect(tmp_path / "target/stocks.sqlite") as c:
        assert c.execute("SELECT * FROM daily_bars ORDER BY trade_date").fetchall() == target
    assert json.loads((tmp_path / "delta.json").read_text())["status"] == "exported_not_applied"


def test_metadata_only_and_outside_scope_do_not_generate_changes(tmp_path):
    make_pool(
        tmp_path / "source",
        [("000001.SZ", "2026-09-30", 10, 2), ("000002.SZ", "2026-09-30", 20, 2)],
    )
    make_pool(tmp_path / "target", [("000001.SZ", "2026-09-30", 10, 1)])
    assert run(tmp_path)["datasets"][0]["changes"] == []


def test_budget_and_incomplete_publication_leave_no_package(tmp_path):
    for name in ["source", "target"]:
        make_pool(tmp_path / name, [("000001.SZ", "2026-09-30", 10, 1)])
    with pytest.raises(ValueError, match="Aggregate"):
        run(tmp_path, max_rows=1)
    assert not (tmp_path / "delta.json").exists()
    pending = pending_path(tmp_path / "target")
    pending.parent.mkdir(parents=True)
    pending.write_text("{}")
    with pytest.raises(Exception, match="publication is incomplete"):
        run(tmp_path)
    assert not (tmp_path / "delta.json").exists()


def test_apply_stock_recomputes_and_replays_without_changes(canonical, tmp_path):  # noqa: F811
    from aspool.base_delta import apply_stock_delta
    from aspool.sqlite_stock_store import stock_connection

    # Build a source row from the actual canonical receiver schema.
    with stock_connection(canonical) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_bars)")]
        raw = conn.execute(
            "SELECT * FROM daily_bars WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        ).fetchone()
    from aspool.base_delta import OBSERVATION_METADATA

    fields = [c for c in cols if c not in OBSERVATION_METADATA]
    before = {k: v for k, v in zip(cols, raw) if k in fields}
    after = dict(before, close=10.5)
    package = tmp_path / "apply.json"
    package.write_text(
        json.dumps(
            {
                "format": "aspool-base-delta-v1",
                "target": str(canonical),
                "window": {"start": "2026-09-28", "end": "2026-09-28", "symbols": ["000001.SZ"]},
                "datasets": [
                    {
                        "name": "stock-bars",
                        "fields": fields,
                        "keys": ["symbol", "trade_date"],
                        "changes": [
                            {"key": ["000001.SZ", "2026-09-28"], "before": before, "after": after}
                        ],
                    }
                ],
            }
        )
    )
    from aspool.base_delta import _checksum

    value = json.loads(package.read_text())
    value["sha256"] = _checksum(value)
    package.write_text(json.dumps(value))
    result = apply_stock_delta(canonical, package)
    assert result["changed_rows"] == 1
    assert result["summary_rows"] > 0
    assert apply_stock_delta(canonical, package)["changed_rows"] == 0
    with stock_connection(canonical) as conn:
        assert (
            conn.execute(
                "SELECT close FROM daily_bars WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
            ).fetchone()[0]
            == 10.5
        )
    broken = json.loads(package.read_text())
    broken["datasets"][0]["changes"][0]["after"]["close"] = 10.6
    broken.pop("sha256")
    broken["sha256"] = _checksum(broken)
    package.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="Receiver conflict"):
        apply_stock_delta(canonical, package)


from tests.unit.test_storage_single_authority import canonical  # noqa: E402,F401


def test_multi_domain_apply_and_interrupted_recovery(tmp_path, monkeypatch):
    import duckdb

    from aspool import base_delta as delta
    from aspool.sqlite_publication import assert_published

    source, target = tmp_path / "source", tmp_path / "target"
    for root in [source, target]:
        root.mkdir()
        with duckdb.connect(str(root / "catalog.duckdb")) as c:
            c.execute(
                "CREATE TABLE securities(symbol VARCHAR PRIMARY KEY,name "
                "VARCHAR,updated_at TIMESTAMP)"
            )
            c.execute(
                "INSERT INTO securities VALUES ('000001.SZ',?,current_timestamp)",
                ["new" if root == source else "old"],
            )
            c.execute(
                "CREATE TABLE security_calendar(trade_date DATE PRIMARY "
                "KEY,is_open BOOLEAN,source VARCHAR)"
            )
            c.execute("INSERT INTO security_calendar VALUES ('2026-09-30',true,'test')")
        with sqlite3.connect(root / "indices.sqlite") as c:
            c.execute(
                "CREATE TABLE daily_bars(symbol TEXT,trade_date TEXT,close "
                "REAL,updated_at INTEGER,PRIMARY KEY(symbol,trade_date))"
            )
            c.execute(
                "INSERT INTO daily_bars VALUES ('000001.SZ','2026-09-30',?,1)",
                (11 if root == source else 10,),
            )
    package = tmp_path / "multi.json"
    delta.export_delta(
        source,
        target,
        datasets=["securities", "calendar", "index-bars"],
        start="2026-09-30",
        end="2026-09-30",
        symbols=["000001.SZ"],
        output=package,
    )

    def crash(phase):
        if phase == "after_index-bars":
            raise RuntimeError("injected interrupt")

    monkeypatch.setattr(delta, "_fault", crash)
    with pytest.raises(RuntimeError, match="injected"):
        delta.apply_delta(target, package)
    with pytest.raises(Exception, match="replica incomplete"):
        assert_published(target)
    from aspool.pool import pool_lock

    with pytest.raises(Exception, match="replica incomplete"):
        with pool_lock(target, write=True):
            pass
    monkeypatch.setattr(delta, "_fault", lambda phase: None)
    result = delta.apply_delta(target, recover=True)
    assert result["applied"]
    assert_published(target)
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT name FROM securities").fetchone()[0] == "new"
    assert all(v == 0 for v in delta.apply_delta(target, package)["results"].values())


def test_corrupt_package_never_creates_pending_intent(tmp_path):
    from aspool.base_delta import apply_delta, replica_path

    for name in ["source", "target"]:
        make_pool(tmp_path / name, [("000001.SZ", "2026-09-30", 10, 1)])
    run(tmp_path)
    package = tmp_path / "delta.json"
    value = json.loads(package.read_text())
    value["window"]["start"] = "1900-01-01"
    package.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checksum"):
        apply_delta(tmp_path / "target", package)
    assert not replica_path(tmp_path / "target").exists()


def test_real_process_exit_recovers_stock_and_publication(canonical, tmp_path):  # noqa: F811
    import os
    import shutil
    import subprocess
    import sys

    from aspool.base_delta import apply_delta, export_delta
    from aspool.sqlite_publication import assert_published
    from aspool.sqlite_stock_store import stock_connection

    source = tmp_path / "source"
    shutil.copytree(canonical, source)
    with sqlite3.connect(source / "stocks.sqlite") as conn:
        conn.execute(
            "UPDATE daily_bars SET close=10.5 WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        )
    package = tmp_path / "crash.json"
    export_delta(
        source,
        canonical,
        datasets=["stock-bars"],
        start="2026-09-28",
        end="2026-09-28",
        symbols=["000001.SZ"],
        output=package,
    )
    code = """import os,sys
from aspool.base_delta import apply_delta
from aspool import sqlite_publication
sqlite_publication._fault=lambda phase: os._exit(73) if phase=='after_sqlite_commit' else None
apply_delta(sys.argv[1],sys.argv[2])
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    result = subprocess.run([sys.executable, "-c", code, str(canonical), str(package)], env=env)
    assert result.returncode == 73
    with pytest.raises(Exception, match="incomplete"):
        assert_published(canonical)
    apply_delta(canonical, recover=True)
    assert_published(canonical)
    with stock_connection(canonical) as conn:
        assert (
            conn.execute("SELECT close FROM daily_bars WHERE trade_date='2026-09-28'").fetchone()[0]
            == 10.5
        )
        assert (
            conn.execute(
                "SELECT calc_status FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone()[0]
            == "TRADED"
        )


def test_source_evidence_is_preserved_and_replicated(tmp_path):
    import hashlib

    from aspool.base_delta import apply_delta, evidence_directory, export_delta

    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = {
        "symbol": "000001.SZ",
        "as_of": "2026-09-30",
        "dates": ["2026-09-30"],
        "prices": {"NONE": [[10, 11, 9, 10]], "QFQ": [[10, 11, 9, 10]], "HFQ": [[10, 11, 9, 10]]},
        "events": [],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    directory = evidence_directory(source)
    directory.mkdir(parents=True)
    (directory / (digest + ".json")).write_text(json.dumps(payload))
    package = tmp_path / "evidence.json"
    export_delta(
        source,
        target,
        datasets=["factor-source-evidence"],
        start="2026-09-30",
        end="2026-09-30",
        symbols=["000001.SZ"],
        output=package,
    )
    apply_delta(target, package)
    assert json.loads((evidence_directory(target) / (digest + ".json")).read_text()) == payload


def test_anchor_revision_recomputes_factors_with_lineage(canonical, tmp_path):  # noqa: F811
    import shutil

    from aspool.base_delta import apply_delta, export_delta
    from aspool.sqlite_reference_factors import _ANCHOR_SOURCE
    from aspool.sqlite_stock_store import stock_connection

    with sqlite3.connect(canonical / "adjustments.sqlite") as c:
        c.execute("UPDATE stock_adjustment_factors SET source=?", (_ANCHOR_SOURCE,))
        for day, value in [("2026-09-27", 2.0), ("2026-09-28", 1.0)]:
            c.execute(
                "INSERT INTO stock_factor_anchors(symbol,effective_date,"
                "record_kind,source,source_key,source_cumulative_factor,"
                "updated_at) "
                "VALUES (?,?,'factor_anchor','test:anchor','source',?,1)",
                ("000001.SZ", day, value),
            )
    source = tmp_path / "source"
    shutil.copytree(canonical, source)
    with sqlite3.connect(source / "adjustments.sqlite") as c:
        c.execute(
            "UPDATE stock_factor_anchors SET "
            "source_cumulative_factor=1.8 WHERE "
            "effective_date='2026-09-27'"
        )
    package = tmp_path / "anchors.json"
    export_delta(
        source,
        canonical,
        datasets=["factor-anchors"],
        start="2026-09-27",
        end="2026-09-28",
        symbols=["000001.SZ"],
        output=package,
    )
    apply_delta(canonical, package)
    with stock_connection(canonical) as c:
        row = c.native.execute(
            "SELECT cumulative_factor,input_hash,algorithm_version "
            "FROM adjustments.stock_adjustment_factors WHERE "
            "effective_date='2026-09-27'"
        ).fetchone()
        assert row[0] == 1.8
        assert len(row[1]) == 64
        assert row[2] == "selected-factor-v2"


def test_financial_and_shareholder_datasets_apply(tmp_path):
    from aspool.base_delta import apply_delta, export_delta
    from aspool.fundamentals_store import DDL

    for name in ["source", "target"]:
        root = tmp_path / name
        root.mkdir()
        with sqlite3.connect(root / "fundamentals.sqlite") as c:
            c.executescript(DDL)
            if name == "source":
                c.execute(
                    "INSERT INTO stock_financial_reports(symbol,period_end,"
                    "source,source_hash,payload_json,fetched_at,updated_at) "
                    "VALUES ('000001.SZ','2026-06-30','test','hash','{}',"
                    "'2026-09-30T00:00:00+00:00',1)"
                )
                c.execute(
                    "INSERT INTO stock_shareholder_counts(symbol,as_of_date,"
                    "source,shareholder_count,source_report_date,fetched_at,"
                    "updated_at) VALUES ('000001.SZ','2026-06-30','test',100,"
                    "'2026-06-30','2026-09-30T00:00:00+00:00',1)"
                )
    package = tmp_path / "finance.json"
    export_delta(
        tmp_path / "source",
        tmp_path / "target",
        datasets=["financial-reports", "shareholder-counts"],
        start="2026-06-30",
        end="2026-06-30",
        symbols=["000001.SZ"],
        output=package,
    )
    apply_delta(tmp_path / "target", package)
    with sqlite3.connect(tmp_path / "target/fundamentals.sqlite") as c:
        assert c.execute("SELECT count(*) FROM stock_financial_reports").fetchone()[0] == 1
        assert "T" in c.execute("SELECT fetched_at FROM stock_financial_reports").fetchone()[0]
        assert (
            c.execute("SELECT shareholder_count FROM stock_shareholder_counts").fetchone()[0] == 100
        )


def test_unknown_trading_fact_is_excluded_from_market(canonical, tmp_path):  # noqa: F811
    import shutil

    from aspool.base_delta import apply_delta, export_delta
    from aspool.sqlite_stock_store import stock_connection

    source = tmp_path / "source"
    shutil.copytree(canonical, source)
    with sqlite3.connect(source / "features.sqlite") as c:
        c.execute(
            "UPDATE stock_daily_features SET trading_status='UNKNOWN',"
            "trading_status_source='test:dated' WHERE "
            "trade_date='2026-09-28'"
        )
    package = tmp_path / "status.json"
    export_delta(
        source,
        canonical,
        datasets=["stock-source-facts"],
        start="2026-09-28",
        end="2026-09-28",
        symbols=["000001.SZ"],
        output=package,
    )
    apply_delta(canonical, package)
    with stock_connection(canonical) as c:
        assert (
            c.execute(
                "SELECT calc_status FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone()[0]
            == "NO_TRADE"
        )
        assert (
            c.execute(
                "SELECT trading_count FROM market_daily_summary WHERE "
                "period_key='2026-09-28' AND scope='all_stocks'"
            ).fetchone()[0]
            == 0
        )


def test_etf_raw_increment_rebuilds_volume_metric(tmp_path):
    from aspool.base_delta import apply_delta, export_delta
    from aspool.sqlite_etf_store import DDL, connection, save_rows

    for name in ["source", "target"]:
        root = tmp_path / name
        root.mkdir()
        with sqlite3.connect(root / "etfs.sqlite") as c:
            c.executescript(DDL)
    item = {"code": "510300", "market": "SH", "name": "ETF"}
    rows = [
        dict(
            symbol="510300.SH",
            trade_date=f"2026-09-{day:02}",
            open=10,
            high=10,
            low=10,
            close=10,
            volume=5 if day == 30 else 1,
            amount=100,
            asset_type="etf",
        )
        for day in range(25, 31)
    ]
    with connection(tmp_path / "source", read_only=False) as c:
        c.row_factory = sqlite3.Row
        save_rows(c, item, rows, source="test:etf")
    package = tmp_path / "etf.json"
    export_delta(
        tmp_path / "source",
        tmp_path / "target",
        datasets=["etf-bars"],
        start="2026-09-25",
        end="2026-09-30",
        symbols=["510300.SH"],
        output=package,
    )
    apply_delta(tmp_path / "target", package)
    with sqlite3.connect(tmp_path / "target/etfs.sqlite") as c:
        value, source = c.execute(
            "SELECT vol_ratio,vol_ratio_source FROM daily_bars WHERE trade_date='2026-09-30'"
        ).fetchone()
        assert value == 5
        assert source.startswith("derived:")
    assert apply_delta(tmp_path / "target", package)["results"]["etf-bars"] == 0


def test_downloaded_evidence_initializes_missing_factors(canonical, tmp_path):  # noqa: F811
    import shutil

    from aspool.base_delta import apply_delta, export_delta, store_source_evidence
    from aspool.sqlite_stock_store import stock_connection

    with sqlite3.connect(canonical / "adjustments.sqlite") as c:
        c.execute("DELETE FROM stock_adjustment_factors")
    with sqlite3.connect(canonical / "stocks.sqlite") as c:
        raw = c.execute(
            "SELECT trade_date,open,high,low,close FROM daily_bars ORDER BY trade_date"
        ).fetchall()
    prices = [list(r[1:]) for r in raw]
    proof = {
        "method": "tdx-listing-episode-affine-v1",
        "symbol": "000001.SZ",
        "listing_date": raw[0][0],
        "as_of": raw[-1][0],
        "dates": [r[0] for r in raw],
        "prices": dict(NONE=prices, QFQ=prices, HFQ=prices),
        "events": [],
        "rows": len(raw),
    }
    source = tmp_path / "source"
    shutil.copytree(canonical, source)
    store_source_evidence(source, proof)
    package = tmp_path / "bootstrap.json"
    export_delta(
        source,
        canonical,
        datasets=["factor-source-evidence"],
        start=raw[-1][0],
        end=raw[-1][0],
        symbols=["000001.SZ"],
        output=package,
    )
    apply_delta(canonical, package)
    with stock_connection(canonical) as c:
        rows = c.native.execute(
            "SELECT cumulative_factor,input_hash FROM adjustments.stock_adjustment_factors"
        ).fetchall()
        assert rows and rows[0][0] == 1.0
        assert len(rows[0][1]) == 64
        features = c.execute(
            "SELECT trade_date,calc_status FROM daily_features ORDER BY trade_date"
        ).fetchall()
        assert [r[0] for r in features] == [r[0] for r in raw]
        assert all(r[1] == "TRADED" for r in features)


@pytest.mark.parametrize("include_bars", [True, False])
def test_new_day_empty_event_download_extends_factor_coverage(canonical, tmp_path, include_bars):  # noqa: F811
    import shutil

    from aspool.base_delta import apply_delta, export_delta, store_event_coverage
    from aspool.sqlite_stock_store import stock_connection

    source = tmp_path / "source"
    shutil.copytree(canonical, source)
    with sqlite3.connect(source / "stocks.sqlite") as c:
        columns = [r[1] for r in c.execute("PRAGMA table_info(daily_bars)")]
        row = dict(
            zip(
                columns,
                c.execute("SELECT * FROM daily_bars WHERE trade_date='2026-09-28'").fetchone(),
            )
        )
        row["trade_date"] = "2026-09-29"
        c.execute(
            "INSERT INTO daily_bars("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            list(row.values()),
        )
    if not include_bars:
        with sqlite3.connect(canonical / "stocks.sqlite") as c:
            c.execute(
                "INSERT INTO daily_bars("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                list(row.values()),
            )
    store_event_coverage(
        source,
        symbol="000001.SZ",
        verified_start="2026-09-29",
        verified_end="2026-09-29",
        source="tdx:xdxr",
        events=[],
    )
    package = tmp_path / "new-day.json"
    export_delta(
        source,
        canonical,
        datasets=["stock-bars", "event-coverage"] if include_bars else ["event-coverage"],
        start="2026-09-29",
        end="2026-09-29",
        symbols=["000001.SZ"],
        output=package,
    )
    apply_delta(canonical, package)
    with stock_connection(canonical) as c:
        assert (
            c.native.execute(
                "SELECT max(valid_through) FROM adjustments.stock_adjustment_factors"
            ).fetchone()[0]
            == "2026-09-29"
        )
        assert (
            c.execute(
                "SELECT calc_status FROM daily_features WHERE trade_date='2026-09-29'"
            ).fetchone()[0]
            == "TRADED"
        )
    assert apply_delta(canonical, package)["results"]["event-coverage"] == 0
