"""Meaningful smoke checks for the independent full-schema profiling harness."""

import importlib
import json
from pathlib import Path

import duckdb


def test_streamed_full_schema_profiles_keep_date_windows_bounded(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts/dg03"
    monkeypatch.syspath_prepend(str(scripts))
    bench = importlib.import_module("bench")
    bounds = importlib.import_module("growth_bounds")
    monkeypatch.setattr(bench, "LAB", tmp_path)
    monkeypatch.setattr(bounds, "LAB", tmp_path)
    for factor in (1, 2, 5):
        root = tmp_path / f"prepared-{factor}x"
        root.mkdir()
        with duckdb.connect(str(root / "catalog.duckdb")) as c:
            columns = ",".join(f"{i} AS field_{i}" for i in range(41))
            c.execute(
                "CREATE TABLE dg03_daily AS SELECT trade_date,"
                + columns
                + " FROM (VALUES (DATE '2026-09-24'),(DATE '2026-08-01'),"
                "(DATE '2022-02-02')) t(trade_date)"
            )
            for i in range(1, factor):
                c.execute(
                    "INSERT INTO dg03_daily SELECT * REPLACE "
                    f"((trade_date-INTERVAL '{40 * i} years')::DATE AS trade_date) "
                    "FROM dg03_daily WHERE trade_date >= DATE '2021-09-25'"
                )
    bounds.run()
    samples = [json.loads(line) for line in (tmp_path / "samples.jsonl").read_text().splitlines()]
    scans = [sample for sample in samples if sample["name"] == "full_schema_scan_bound"]
    assert len(scans) == 9
    assert [sample["returned_rows"] for sample in scans] == [1, 2, 3] * 3
    assert all(sample["columns"] == 42 and sample["scans"] for sample in scans)


def test_joint_sigkill_atomicity_and_controlled_process_overlap(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts/dg03"
    monkeypatch.syspath_prepend(str(scripts))
    protocol = importlib.import_module("recovery_protocol")
    events = []

    def record(name, **values):
        events.append({"name": name, **values})

    protocol.joint_recovery(tmp_path, record)
    protocol.process_concurrency(tmp_path / "joint-base", record)
    crashes = [event for event in events if event["name"].startswith("joint_crash_")]
    assert len(crashes) == 2 and all(event["all_six_domains_exact"] for event in crashes)
    overlapping = next(event for event in events if event["name"] == "controlled_read_read")
    assert overlapping["shared_overlap"] and overlapping["protocol_verified"]
    assert len(events) == 9
    assert events[-1]["committed_without_loss"] == 6
