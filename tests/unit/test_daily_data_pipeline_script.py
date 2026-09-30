import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import duckdb

SCRIPT = Path(__file__).parents[2] / "scripts/ops/run_daily_data_pipeline.py"
SPEC = importlib.util.spec_from_file_location("daily_data_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_pipeline_keeps_data_dependencies_in_operational_order(tmp_path):
    stages = MODULE.pipeline_stages(tmp_path / "data", workers=4)
    assert [stage.name for stage in stages] == [
        "directory",
        "stock_update",
        "index_update",
        "etf_update",
        "fundamentals",
    ]
    assert "除权" in stages[1].purpose


def test_fundamentals_runs_in_scheduled_market_runs(tmp_path):
    stages = MODULE.pipeline_stages(tmp_path / "data", workers=4)
    assert [stage.name for stage in stages][-1] == "fundamentals"


def test_audit_requires_all_contract_datasets_and_closed_stock_features(tmp_path):
    features = tmp_path / "features.sqlite"
    with sqlite3.connect(features) as conn:
        conn.execute(
            "CREATE TABLE stock_daily_features("
            "symbol TEXT,trade_date TEXT,calc_status TEXT,trading_status TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_daily_features VALUES (?,?,?,?)",
            [
                ("000001.SZ", "2026-09-29", "TRADED", "TRADED"),
                ("600000.SH", "2026-09-29", "NO_TRADE", "NO_TRADE"),
            ],
        )

    required = [
        "securities",
        "calendar",
        "fundamentals",
        "shareholder-counts",
        "stock-bars",
        "corporate-actions",
        "stock-factors",
        "stock-features",
        "market-summary",
        "index-bars",
        "etf-bars",
        "etf-factors",
    ]

    class Completed:
        def __init__(self, value):
            self.stdout = json.dumps(value)

    def fake_aspool(command, *, capture):
        if command[0] == "contract":
            return Completed([{"name": f"block-{i}"} for i in range(12)])
        rows = [{"dataset": dataset} for dataset in required]
        rows[0]["symbols"] = 2
        return Completed(rows)

    audit = MODULE.collect_audit(tmp_path, fake_aspool)
    assert audit["ready"] is True
    assert audit["stock_feature_closure"]["calc_status"] == {"TRADED": 1, "NO_TRADE": 1}


def test_feature_closure_uses_target_session_exact_symbols_and_factor_coverage(tmp_path):
    with duckdb.connect(str(tmp_path / "catalog.duckdb")) as catalog:
        catalog.execute(
            "CREATE TABLE security_calendar(trade_date DATE PRIMARY KEY,is_open BOOLEAN)"
        )
        catalog.execute("INSERT INTO security_calendar VALUES ('2026-09-29',true)")
        catalog.execute(
            "CREATE TABLE securities(symbol VARCHAR,asset_type VARCHAR,active BOOLEAN,"
            "listing_date DATE,delisting_date DATE)"
        )
        catalog.executemany(
            "INSERT INTO securities VALUES (?,'stock',true,'2020-01-01',NULL)",
            [("000001.SZ",), ("600000.SH",)],
        )
    with sqlite3.connect(tmp_path / "features.sqlite") as features:
        features.execute(
            "CREATE TABLE stock_daily_features("
            "symbol TEXT,trade_date TEXT,calc_status TEXT,trading_status TEXT)"
        )
        features.execute(
            "INSERT INTO stock_daily_features VALUES ('000001.SZ','2026-09-29','TRADED','TRADING')"
        )
        features.execute("CREATE TABLE market_regime_features(frequency TEXT,period_key TEXT)")
        features.execute("INSERT INTO market_regime_features VALUES ('D','2026-09-29')")
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as factors:
        factors.execute(
            "CREATE TABLE stock_adjustment_factors(symbol TEXT,valid_from TEXT,valid_through TEXT)"
        )
        factors.execute(
            "INSERT INTO stock_adjustment_factors VALUES ('000001.SZ','2020-01-01','2026-09-29')"
        )

    incomplete = MODULE._feature_closure(tmp_path, None)
    assert incomplete["closed"] is False
    assert incomplete["missing_symbol_sample"] == ["600000.SH"]

    with sqlite3.connect(tmp_path / "features.sqlite") as features:
        features.execute(
            "INSERT INTO stock_daily_features VALUES ('600000.SH','2026-09-29','TRADED','TRADING')"
        )
    without_factor = MODULE._feature_closure(tmp_path, None)
    assert without_factor["closed"] is True
    assert without_factor["factor_ready"] is False
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as factors:
        factors.execute(
            "INSERT INTO stock_adjustment_factors VALUES ('600000.SH','2020-01-01','2026-09-29')"
        )
    assert MODULE._feature_closure(tmp_path, None)["closed"] is True


def test_pipeline_preserves_partial_receipts_and_runs_independent_stages(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(MODULE, "__file__", str(tmp_path / "scripts/ops/pipeline.py"))
    monkeypatch.setattr(MODULE, "collect_audit", lambda *a: {"ready": True})
    calls = []

    def run(executable, command, **kwargs):
        calls.append(command[:3])
        if command[0] == "directory":
            return subprocess.CompletedProcess(command, 0, "directory ok")
        partial = command[2] in ("stock", "index")
        path = tmp_path / f"{command[2]}.json"
        path.write_text(
            json.dumps(
                {
                    "status": "completed_with_missing" if partial else "ok",
                    "factor_failed": [{"symbol": "000001.SZ"}] if command[2] == "stock" else [],
                }
            )
        )
        return subprocess.CompletedProcess(
            command,
            int(command[2] == "index"),
            f"报告：{path}\n",
        )

    monkeypatch.setattr(MODULE, "run_command", run)
    assert MODULE.main(["--root", str(tmp_path / "data")]) == 1
    assert [c[2] for c in calls] == ["all", "stock", "index", "etf", "--root"]
    report = json.loads(
        next((tmp_path / ".local/reports/daily-pipeline").glob("*.json")).read_text()
    )
    assert report["status"] == "partial"
    assert [s["status"] for s in report["stages"]] == ["ok", "partial", "partial", "ok", "ok"]
    assert report["stages"][1]["source_counts"]["factor_failed"] == 1
    assert report["elapsed_ms"] >= 0
    assert report["audit_elapsed_ms"] >= 0
    assert all(stage["elapsed_ms"] >= 0 for stage in report["stages"])


def test_pipeline_stops_on_source_circuit_breaker(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(MODULE, "__file__", str(tmp_path / "scripts/ops/pipeline.py"))
    calls = []

    def run(executable, command, **kwargs):
        calls.append(command[2])
        if command[0] == "directory":
            return subprocess.CompletedProcess(command, 0, "directory ok")
        path = tmp_path / "failed.json"
        path.write_text(json.dumps({"status": "aborted_source_failure", "remaining_block": ["x"]}))
        return subprocess.CompletedProcess(command, 1, f"报告：{path}\n")

    monkeypatch.setattr(MODULE, "run_command", run)
    assert MODULE.main(["--root", str(tmp_path / "data")]) == 1
    assert calls == ["all", "stock"]


def test_daily_script_root_is_independent_of_cron_cwd(tmp_path, monkeypatch):
    import os
    import subprocess

    env = os.environ.copy()
    env.pop("ASPOOL_ROOT", None)
    shell = SCRIPT.with_suffix(".sh")
    result = subprocess.run(
        ["bash", str(shell), "--dry-run"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    stages = json.loads(result.stdout.split("\n", 1)[1])
    command = stages[0]["command"]
    assert Path(command[command.index("--root") + 1]) == SCRIPT.parents[2] / "data"
    monkeypatch.setenv("ASPOOL_ROOT", str(tmp_path / "explicit"))
    assert MODULE.parse_args([]).root == tmp_path / "explicit"


def test_known_factor_limitation_is_separate_from_a_failed_factor(tmp_path):
    import subprocess

    from aspool.operation_report import operation_outcome

    path = tmp_path / "operation.json"
    path.write_text(
        json.dumps(
            {
                "status": "ok",
                "factor_unavailable": [{"symbol": "x"}],
                "performance": {"writer_elapsed_ms": 10, "mirror_elapsed_ms": 2},
            }
        )
    )
    outcome = operation_outcome(subprocess.CompletedProcess([], 0, f"报告：{path}"))
    assert outcome["status"] == "ok"
    assert outcome["source_counts"]["factor_unavailable"] == 1
    assert outcome["source_performance"] == {"writer_elapsed_ms": 10, "mirror_elapsed_ms": 2}
    path.write_text(json.dumps({"status": "ok", "factor_failed": [{"symbol": "x"}]}))
    assert (
        operation_outcome(subprocess.CompletedProcess([], 0, f"报告：{path}"))["status"]
        == "partial"
    )


def test_pipeline_lock_rejects_same_root_before_any_command(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)

    def unexpected(*args, **kwargs):
        raise AssertionError("A competing run must not start public commands")

    monkeypatch.setattr(MODULE, "run_command", unexpected)
    with MODULE.pipeline_lock(root):
        assert MODULE.main(["--root", str(alias)]) == 75
        # Different data roots do not share a workflow lock.
        with MODULE.pipeline_lock(tmp_path / "another"):
            pass


def test_pipeline_child_keeps_lock_after_orchestrator_descriptor_closes(tmp_path):
    import subprocess

    import pytest

    from aspool.operation_report import run_command

    root = tmp_path / "data"
    child = None
    try:
        with MODULE.pipeline_lock(root) as fd:
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.read()"],
                pass_fds=(fd,),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            assert child.stdout.readline().strip() == "ready"
            # Check the actual public-command runner also preserves the fd.
            completed = run_command(
                sys.executable,
                ("-c", f"import os; os.fstat({fd})"),
                cwd=tmp_path,
                env=None,
                capture=True,
                pass_fds=(fd,),
            )
            assert completed.returncode == 0
        with pytest.raises(BlockingIOError), MODULE.pipeline_lock(root):
            pass
    finally:
        if child is not None:
            child.communicate(timeout=5)
    with MODULE.pipeline_lock(root):
        pass
