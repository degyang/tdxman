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
    monkeypatch.setattr("sys.argv", [
        "repair_st_defaults", "--root", str(root), "--start", "2026-09-27",
        "--end", "2026-09-28", "--report", str(report_path),
    ])
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
        assert conn.execute(
            "SELECT COUNT(*) FROM daily_features WHERE is_st IS NULL"
        ).fetchone()[0] == 0
    monkeypatch.setattr(repair_st_defaults, "recompute_daily_summary", real_summary)
    repair_st_defaults.main()
    completed = json.loads(report_path.read_text())
    assert completed["status"] == "completed"
    assert completed["selected_rows"] == 0
    assert completed["affected_dates"] == failed["affected_dates"]
    assert completed["summary_rows"] > 0
