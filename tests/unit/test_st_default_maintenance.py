"""An interrupted explicit repair retains dates needed by its summary phase."""

import json

import pytest

from aspool.sqlite_canonical import canonical_operation
from aspool.sqlite_stock_store import stock_connection
from scripts.ops import repair_st_defaults
from tests.unit.test_storage_single_authority import canonical

canonical_pool = canonical


def test_resume_repairs_summaries_after_features_committed(canonical_pool, monkeypatch, tmp_path):
    root = canonical_pool
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "repair_st_defaults",
            "--root",
            str(root),
            "--start",
            "2026-09-27",
            "--end",
            "2026-09-28",
            "--report",
            str(report_path),
        ],
    )
    with stock_connection(root, read_only=False) as conn:
        with canonical_operation(conn, "st_default_repair"):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE daily_features SET is_st=NULL,limit_reason='missing_st'")
            conn.commit()
    real_summary = repair_st_defaults.recompute_daily_summary

    def fail_summary(*args, **kwargs):
        raise RuntimeError("interrupted summary phase")

    monkeypatch.setattr(repair_st_defaults, "recompute_daily_summary", fail_summary)
    with pytest.raises(RuntimeError, match="interrupted"):
        repair_st_defaults.main()
    failed = json.loads(report_path.read_text())
    assert failed["status"] == "failed"
    assert failed["affected_dates"] == ["2026-09-27", "2026-09-28"]
    with stock_connection(root) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM daily_features WHERE is_st IS NULL").fetchone()[0]
            == 0
        )
    monkeypatch.setattr(repair_st_defaults, "recompute_daily_summary", real_summary)
    repair_st_defaults.main()
    completed = json.loads(report_path.read_text())
    assert completed["status"] == "completed"
    assert completed["selected_rows"] == 0
    assert completed["affected_dates"] == failed["affected_dates"]
    assert completed["summary_rows"] > 0


def test_all_existing_mode_is_idempotent_after_completed_run(canonical_pool, monkeypatch, tmp_path):
    root = canonical_pool
    report_path = tmp_path / "all-report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "repair_st_defaults",
            "--root",
            str(root),
            "--start",
            "2026-09-27",
            "--end",
            "2026-09-28",
            "--report",
            str(report_path),
            "--all-existing",
        ],
    )
    repair_st_defaults.main()
    initial = json.loads(report_path.read_text())
    assert initial["all_existing"] and initial["processed_rows"] == 2
    with stock_connection(root) as conn:
        before = conn.execute(
            "SELECT * FROM market_daily_summary ORDER BY period_key,scope"
        ).fetchall()
    repair_st_defaults.main()
    repeated = json.loads(report_path.read_text())
    assert repeated["processed_rows"] == 2
    assert repeated["selected_rows"] == repeated["feature_rows"] == repeated["summary_rows"] == 0
    with stock_connection(root) as conn:
        assert (
            conn.execute("SELECT * FROM market_daily_summary ORDER BY period_key,scope").fetchall()
            == before
        )


def test_history_driver_repairs_and_verifies_all_existing_years(
    canonical_pool, monkeypatch, tmp_path
):
    from scripts.ops import repair_st_history

    reports = tmp_path / "history"
    monkeypatch.setattr(
        "sys.argv",
        [
            "repair_st_history",
            "--root",
            str(canonical_pool),
            "--reports",
            str(reports),
        ],
    )
    repair_st_history.main()
    result = json.loads((reports / "history.json").read_text())
    assert result["status"] == "completed"
    assert result["raw_and_adjustment_states_unchanged"]
    assert result["completed_year_count"] == 1
    year = result["completed_years"]["2026"]
    assert year["result"]["processed_rows"] == 2
    assert year["verification"]["null_st"] == 0
    assert all(row["days"] == 2 for row in year["verification"]["summary"])
