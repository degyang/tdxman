"""Explicit chronological repair of every existing stock-feature history row."""

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def read_database(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA cache_size=-524288")
    return conn


def inventory(root):
    """Visit the covering date index, retaining only one record per existing date."""
    with read_database(root / "features.sqlite") as conn:
        dates = conn.execute(
            "SELECT trade_date,COUNT(*) rows FROM stock_daily_features "
            "INDEXED BY stock_daily_features_by_date GROUP BY trade_date ORDER BY trade_date"
        ).fetchall()
    years = {}
    for row in dates:
        year = row["trade_date"][:4]
        item = years.setdefault(
            year, dict(start=row["trade_date"], end=row["trade_date"], days=0, rows=0)
        )
        item["end"] = row["trade_date"]
        item["days"] += 1
        item["rows"] += row["rows"]
    return years


def states(root):
    result = {}
    for filename, table in (
        ("stocks.sqlite", "dataset_state"),
        ("adjustments.sqlite", "adjustment_state"),
    ):
        with read_database(root / filename) as conn:
            result[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    return result


def verify_year(root, window):
    with read_database(root / "features.sqlite") as conn:
        rows = conn.execute(
            "SELECT scope,COUNT(*) days,AVG(close_limit_up_count) avg_limit_up,"
            "AVG(limit_known_count) avg_known,AVG(limit_unknown_count) avg_unknown,"
            "SUM(CASE WHEN trading_count IS NOT "
            "(limit_known_count+no_limit_count+limit_unknown_count+limit_invalid_count) "
            "OR close_limit_up_count>limit_known_count THEN 1 ELSE 0 END) errors "
            "FROM market_regime_features WHERE frequency='D' AND period_key BETWEEN ? AND ? "
            "GROUP BY scope",
            (window["start"], window["end"]),
        ).fetchall()
        assert {r["scope"] for r in rows} == {"all_stocks", "exclude_known_st"}
        assert all(r["days"] == window["days"] and r["errors"] == 0 for r in rows)
        reasons = conn.execute(
            "SELECT calc_status,limit_status,limit_reason,COUNT(*) rows FROM stock_daily_features "
            "WHERE trade_date BETWEEN ? AND ? GROUP BY calc_status,limit_status,limit_reason",
            (window["start"], window["end"]),
        ).fetchall()
        assert sum(r["rows"] for r in reasons) == window["rows"]
        assert not any(r["limit_reason"] == "missing_st" for r in reasons)
        null_st = conn.execute(
            "SELECT COUNT(*) FROM stock_daily_features WHERE trade_date BETWEEN ? AND ? "
            "AND is_st IS NULL",
            (window["start"], window["end"]),
        ).fetchone()[0]
        assert null_st == 0
        return dict(
            summary=[dict(r) for r in rows],
            reasons=[dict(r) for r in reasons],
            missing_st=0,
            null_st=0,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--reports", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    args.reports.mkdir(parents=True, exist_ok=True)
    path = args.reports / "history.json"
    years = inventory(root)
    previous = json.loads(path.read_text()) if path.exists() else {}
    if previous and previous.get("root") != str(root):
        raise ValueError("The report directory belongs to another data root")
    report = dict(
        root=str(root),
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        inventory=years,
        original_states=previous.get("original_states", states(root)),
        completed_years=previous.get("completed_years", {}),
    )
    started = time.monotonic()

    def save():
        report["seconds"] = round(time.monotonic() - started, 3)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(path)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k
                    not in {
                        "inventory",
                        "original_states",
                        "completed_years",
                    }
                }
            ),
            flush=True,
        )

    try:
        save()
        for year, window in years.items():
            if year in report["completed_years"]:
                continue
            report["active_year"] = year
            report["completed_year_count"] = len(report["completed_years"])
            save()
            yearly_path = args.reports / f"{year}.json"
            with (args.reports / f"{year}.log").open("a") as log:
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("repair_st_defaults.py")),
                        "--root",
                        str(root),
                        "--start",
                        window["start"],
                        "--end",
                        window["end"],
                        "--report",
                        str(yearly_path),
                        "--all-existing",
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            result = json.loads(yearly_path.read_text())
            assert result["status"] == "completed" and result["all_existing"]
            assert result["processed_rows"] == window["rows"]
            verification = verify_year(root, window)
            report["completed_years"][year] = dict(result=result, verification=verification)
            save()
        assert states(root) == report["original_states"]
        assert len(report["completed_years"]) == len(years)
        report.update(
            status="completed",
            completed_year_count=len(years),
            raw_and_adjustment_states_unchanged=True,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        save()
    except BaseException as exc:
        report.update(status="failed", error=repr(exc))
        save()
        raise


if __name__ == "__main__":
    main()
