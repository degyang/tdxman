"""Legacy references retire only after business facts are accounted for."""

import importlib.util
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aspool.fundamental_catalog import available, read, write
from aspool.store import initialize


def operation():
    path = Path(__file__).parents[2] / "scripts/ops/retire_legacy_reference_files.py"
    spec = importlib.util.spec_from_file_location("retire_refs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


def fixture(root):
    root.mkdir()
    with sqlite3.connect(root / "stocks.sqlite") as c:
        c.execute(
            "CREATE TABLE corporate_actions(symbol,source_key,effective_date,"
            "source_cumulative_factor,record_kind,source,payload_json)"
        )
        c.execute(
            "INSERT INTO corporate_actions VALUES ('000001.SZ','000001.SZ:cumulative',"
            "'2026-09-24',2.,'factor_anchor','legacy:adjustments/factors.parquet',NULL)"
        )
        event = dict(date="2026-09-24", category=1, fenhong=1.0)
        c.execute(
            "INSERT INTO corporate_actions VALUES ('000001.SZ','category=1:slot=1',"
            "'2026-09-24',NULL,'event','tdx:xdxr',?)",
            (json.dumps(event),),
        )
    with sqlite3.connect(root / "etfs.sqlite") as c:
        c.execute("CREATE TABLE adjustment_factors(symbol,trade_date,cumulative_factor)")
    (root / "indices.sqlite").touch()
    with duckdb.connect(str(root / "catalog.duckdb")) as c:
        c.execute("CREATE TABLE fixture(value INTEGER)")
    factors = root / "lake/adjustments/factors.parquet"
    factors.parent.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    symbol="000001",
                    market="SZ",
                    trade_date=date(2026, 9, 24),
                    cumulative_factor=2.0,
                )
            ]
        ),
        factors,
    )
    folder = root / "lake/fundamentals/dated_inputs"
    folder.mkdir(parents=True)
    (folder / "000001.SZ.json").write_text(json.dumps(dict(events=[event], finance={})))
    schema = pa.schema(
        [(k, pa.string()) for k in ("symbol", "market", "code", "name")]
        + [
            (k, pa.float64())
            for k in ("total_share", "float_share", "eps", "ttm_eps", "net_assets")
        ]
        + [("refreshed_at", pa.timestamp("us")), ("source", pa.string())]
    )
    row = dict(
        symbol="000001",
        market="SZ",
        code="000001",
        name="Test",
        total_share=100.0,
        float_share=50.0,
        eps=1.0,
        ttm_eps=2.0,
        net_assets=3.0,
        refreshed_at=datetime(2026, 9, 24),
        source="test",
    )
    pq.write_table(pa.Table.from_pylist([row], schema=schema), folder.parent / "snapshots.parquet")


def test_retire_preserves_refs_snapshot_and_future_writes(tmp_path):
    root = tmp_path / "data"
    fixture(root)
    report = operation()(root, workdir=tmp_path / "recovery")
    assert (
        report["status"] == "retired"
        and report["factors"] == report["events"] == report["snapshots"] == 1
    )
    assert not (root / "lake").exists()
    assert available(root)
    row = read(root).to_dict("records")[0]
    assert not write(root, [dict(row, refreshed_at=datetime(2026, 9, 28))])
    assert read(root).refreshed_at.iloc[0] == datetime(2026, 9, 24)
    assert write(root, [dict(row, total_share=110.0)])
    assert read(root).total_share.tolist() == [110.0]
    from aspool.fundamentals import _publish_snapshots

    assert not _publish_snapshots(root, [dict(row, total_share=110.0)])
    assert not (root / "reports").exists()
    assert not (root / "change-state").exists()
    with pytest.raises(duckdb.Error):
        write(
            root, [dict(row, total_share=120.0), dict(row, symbol="000002", total_share="invalid")]
        )
    assert read(root).total_share.tolist() == [110.0]
    initialize(root)
    assert not (root / "lake").exists()


def test_unmigrated_event_blocks_retirement(tmp_path):
    root = tmp_path / "data"
    fixture(root)
    with sqlite3.connect(root / "stocks.sqlite") as c:
        c.execute("DELETE FROM corporate_actions WHERE record_kind='event'")
    with pytest.raises(ValueError, match="Unmigrated event"):
        operation()(root, workdir=tmp_path / "recovery")
    assert (root / "lake/fundamentals/snapshots.parquet").exists()
    assert not available(root)


def test_retired_pool_rejects_old_file_writers_before_creating_artifacts(tmp_path):
    from aspool.enrichment import enrich_daily
    from aspool.free_stockdb import import_adjustments, import_daily
    from aspool.tdx_online import update_daily, update_daily_offline

    (tmp_path / "stocks.sqlite").touch()
    for operation in (
        lambda: import_adjustments(tmp_path, tmp_path),
        lambda: import_daily(tmp_path, tmp_path, False),
        lambda: enrich_daily(tmp_path),
        lambda: update_daily(tmp_path),
        lambda: update_daily_offline(tmp_path),
    ):
        with pytest.raises(ValueError, match="SQLite"):
            operation()
    assert {p.name for p in tmp_path.iterdir()} == {"stocks.sqlite"}
