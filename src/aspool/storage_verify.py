"""Verify canonical ownership and coherent publication without old-table mirrors."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .sqlite_publication import migration_path, pending_path

REQUIRED = {
    "stocks.sqlite": {"daily_bars", "corporate_actions", "dataset_state"},
    "features.sqlite": {"stock_daily_features", "market_regime_features", "feature_state"},
    "adjustments.sqlite": {
        "stock_adjustment_factors",
        "stock_factor_anchors",
        "etf_adjustment_factors",
        "adjustment_state",
    },
    "etfs.sqlite": {"daily_bars"},
}
RETIRED = {
    "stocks.sqlite": {"daily_features", "market_daily_summary", "_retired_actions"},
    "etfs.sqlite": {"adjustment_factors"},
}


def assert_coherent(conn):
    from .api_contract import DataPoolError

    raw = conn.execute("SELECT revision FROM dataset_state WHERE dataset='stock_raw'").fetchone()
    factors = conn.execute(
        "SELECT revision FROM adjustments.adjustment_state WHERE dataset='stock_adjustment_factors'"
    ).fetchone()
    states = conn.execute(
        "SELECT dataset,raw_revision,factor_revision,status FROM features.feature_state "
        "WHERE scope_key='all'"
    ).fetchall()
    if (
        raw is None
        or factors is None
        or {r[0] for r in states} != {"stock_daily_features", "market_regime_features"}
        or any(r[1:] != (raw[0], factors[0], "READY") for r in states)
    ):
        raise DataPoolError("DERIVED_NOT_READY", "Canonical publication versions are incomplete")


def verify_canonical(root, *, deep=False, allow_migration=False):
    root = Path(root).resolve()
    pending = pending_path(root).exists() or (migration_path(root).exists() and not allow_migration)
    result = {
        "layout_version": 3,
        "activated": True,
        "ready": not pending,
        "verification_level": "deep" if deep else "shallow",
        "publication_pending": pending,
        "ownership": {},
    }
    if pending:
        return result
    for file, required in REQUIRED.items():
        path = root / file
        if not path.is_file():
            result["ownership"][file] = {"missing_file": True}
            result["ready"] = False
            continue
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing = sorted(required - tables)
            retired = sorted(RETIRED.get(file, set()) & tables)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            entry = {
                "missing_tables": missing,
                "retired_tables_present": retired,
                "schema": version,
            }
            expected_version = 1 if file == "etfs.sqlite" else 3
            valid = not missing and not retired and version == expected_version
            if deep:
                entry["quick_check"] = conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
                valid = valid and entry["quick_check"]
            result["ownership"][file] = entry
            result["ready"] = result["ready"] and valid
    if not result["ready"]:
        return result
    with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        for alias, file in (("features", "features.sqlite"), ("adjustments", "adjustments.sqlite")):
            conn.execute(f'ATTACH DATABASE ? AS "{alias}"', ((root / file).as_uri() + "?mode=ro",))
        try:
            assert_coherent(conn)
        except Exception as exc:
            result["ready"] = False
            result["version_error"] = str(exc)
        result["source_events_only"] = not conn.execute(
            "SELECT 1 FROM corporate_actions WHERE record_kind<>'event' LIMIT 1"
        ).fetchone()
        result["ready"] = result["ready"] and result["source_events_only"]
    return result
