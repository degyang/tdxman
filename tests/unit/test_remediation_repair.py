"""DG04 supplements replay the exact isolated rehearsal and resume partial commits."""

import json
import shutil
from datetime import date

import pytest

from aspool.daily_storage import merge_daily
from aspool.limit_events import initialize_limits
from aspool.remediation import file_hash, save
from aspool.remediation_repair import execute_repair, prepare
from aspool.security_facts import initialize_facts
from aspool.store import catalog, initialize


@pytest.fixture
def repair(tmp_path):
    target = tmp_path / "target"
    initialize(target)
    initialize_facts(target)
    initialize_limits(target)
    with catalog(target) as conn:
        conn.execute("CREATE TABLE universe(symbol VARCHAR, name VARCHAR)")
    merge_daily(
        target,
        "SZ",
        "000001",
        [
            dict(
                symbol="000001",
                trade_date=date(2026, 5, 4),
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                volume=100.0,
                amount=1000.0,
            )
        ],
        "test",
    )
    lab = tmp_path / "lab"
    shutil.copytree(target, lab)
    evidence = tmp_path / "source.json"
    evidence.write_text("independent raw source")
    payload = tmp_path / "payload.json"
    save(
        payload,
        [
            dict(
                symbol="000001.SZ",
                evidence=str(evidence),
                evidence_sha256=file_hash(evidence),
                row=dict(
                    date="2026-05-05",
                    open=11.0,
                    high=11.0,
                    low=11.0,
                    close=11.0,
                    volume=200.0,
                    amount=2200.0,
                    pre_close=10.0,
                    is_st=False,
                    trading_status="TRADING",
                    turnover_rate=1.0,
                    fetched_at="2026-09-27T10:00:00",
                ),
            )
        ],
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    plan_path = tmp_path / "repair-plan.json"
    spec = prepare(lab, target, payload, manifest, plan_path)
    return target, lab, plan_path, tmp_path / "state.json", spec


def test_exact_replay_and_noop(repair):
    target, lab, plan_path, state, spec = repair
    result = execute_repair(plan_path, state, max_symbols=1)
    assert result["next"] == 1
    assert result["completed"][0]["changed_rows"] == 1
    assert execute_repair(plan_path, state, max_symbols=1) == result
    from aspool.remediation_repair import inventory

    assert inventory(target) == inventory(lab)
    assert spec["steps"][0]["rehearsal"]["changed_facts"] == 1


def test_partial_daily_commit_then_fact_resume(repair, monkeypatch):
    import aspool.remediation_repair as module

    _, _, plan_path, state, _ = repair
    original = module.catalog_rows

    def fail(*args, **kwargs):
        raise RuntimeError("between daily and facts")

    monkeypatch.setattr(module, "catalog_rows", fail)
    with pytest.raises(RuntimeError, match="between daily"):
        execute_repair(plan_path, state, max_symbols=1)
    assert json.loads(state.read_text())["pending"] == "000001.SZ"
    monkeypatch.setattr(module, "catalog_rows", original)
    assert execute_repair(plan_path, state, max_symbols=1)["next"] == 1


def test_source_drift_and_external_state_forgery(repair):
    target, _, plan_path, state, _ = repair
    execute_repair(plan_path, state, max_symbols=1)
    doc = json.loads(state.read_text())
    doc["next"] = 0
    state.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="External state"):
        execute_repair(plan_path, state, max_symbols=1)
    state.unlink()
    with catalog(target) as conn:
        conn.execute("INSERT INTO security_calendar VALUES ('2026-05-05',true,'drift')")
    with pytest.raises(ValueError, match="Source differs"):
        execute_repair(plan_path, state, max_symbols=1)
