import sqlite3
from pathlib import Path


def test_status_reads_factor_coverage_from_layered_adjustments(tmp_path):
    from aspool.cli import _dataset_status

    path = Path(tmp_path) / "adjustments.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE stock_adjustment_factors("
            "symbol TEXT,effective_date TEXT,cumulative_factor REAL)"
        )
        conn.execute(
            "CREATE TABLE etf_adjustment_factors("
            "symbol TEXT,effective_date TEXT,cumulative_factor REAL)"
        )
        conn.execute("INSERT INTO stock_adjustment_factors VALUES ('000001.SZ','2026-09-28',1)")
        conn.execute("INSERT INTO etf_adjustment_factors VALUES ('510300.SH','2026-09-27',1)")

    rows = {row["dataset"]: row for row in _dataset_status(tmp_path)}
    assert rows["stock-factors"] == {
        "dataset": "stock-factors",
        "rows": 1,
        "symbols": 1,
        "start": "2026-09-28",
        "end": "2026-09-28",
    }
    assert rows["etf-factors"]["end"] == "2026-09-27"
