"""Verify an initial derived migration, or its byte-identical replica.

This checks persisted calculation completeness/invariants, not Fundwise API
readiness or the availability of every historical source fact.
"""

from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from verify_stock_migration import BUSINESS_TABLES, INDEXES, TABLES, digest

METRICS = {
    "trading_count": "calc_status='TRADED'",
    "limit_invalid_count": "calc_status='INVALID'",
    "limit_known_count": "limit_status='KNOWN'",
    "no_limit_count": "limit_status='NO_LIMIT'",
    "limit_unknown_count": "limit_status='UNKNOWN'",
    "close_limit_up_count": "close_limit_up IS 1",
    "touch_limit_up_count": "touch_limit_up IS 1",
    "close_limit_down_count": "close_limit_down IS 1",
    "touch_limit_down_count": "touch_limit_down IS 1",
    "ma20_valid_count": "ma20 IS NOT NULL",
    "above_ma20_count": "above_ma20 IS 1",
    "st_unknown_count": "calc_status='TRADED' AND is_st IS NULL",
    "valid_return_count": "calc_status='TRADED' AND pre_close>0",
    "invalid_return_count": "calc_status='TRADED' AND (pre_close IS NULL OR pre_close<=0)",
}


def verify(root, reuse_source_integrity=False, evidence_root=None):
    root = Path(root).resolve()
    started = time.monotonic()
    path = root / "stocks.sqlite"
    wal = root / "stocks.sqlite-wal"
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Checkpoint and close the writer first")
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    sha = digest(path)
    evidence_root = Path(evidence_root) if evidence_root else root / "_reports"
    baseline = json.loads((evidence_root / "facts-result.json").read_text())
    prior_path = evidence_root / "derived-result.json"
    if reuse_source_integrity:
        prior = json.loads(prior_path.read_text())
        if (
            prior.get("database_sha256") != sha
            or prior.get("database_bytes") != before[0]
            or prior.get("integrity_check") != "ok"
            or not prior.get("daily_calculation_complete")
        ):
            raise ValueError("Derived replica differs from the verified source artifact")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA cache_size=-262144")
        conn.execute("BEGIN")
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Wrong schema version")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        if tables != TABLES or not INDEXES <= indexes:
            raise ValueError("Stock schema or indexes differ")
        if reuse_source_integrity:
            result = dict(prior, integrity_reused_by_exact_hash=True)
        else:
            integrity = conn.execute("PRAGMA integrity_check").fetchall()
            if integrity != [("ok",)]:
                raise ValueError(f"Integrity check failed: {integrity[:5]}")
            counts = {
                table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in BUSINESS_TABLES
            }
            for table in ("daily_bars", "daily_features"):
                if counts[table] != baseline["rows"][table]:
                    raise ValueError(f"Unexpected source row count change: {table}")
            kinds = dict(
                conn.execute(
                    "SELECT record_kind,count(*) FROM corporate_actions GROUP BY record_kind"
                )
            )
            if (
                kinds.get("event", 0) + kinds.get("factor_anchor", 0)
                != baseline["rows"]["corporate_actions"]
            ):
                raise ValueError("Original corporate action records changed")
            incomplete = conn.execute(
                "SELECT count(*) FROM daily_features WHERE calc_status='TRADED' "
                "AND limit_status IS NULL"
            ).fetchone()[0]
            if incomplete:
                raise ValueError(f"{incomplete} traded rows have no limit calculation")
            scopes = {"all_stocks": "1", "exclude_known_st": "is_st IS NOT 1"}
            conditions = [
                f"coalesce(sum(({scope}) AND ({condition})),0)"
                for scope in scopes.values()
                for condition in METRICS.values()
            ]
            totals = conn.execute(
                "SELECT " + ",".join(conditions) + " FROM daily_features"
            ).fetchone()
            scope_counts = {}
            for index, scope in enumerate(scopes):
                expected = totals[index * len(METRICS) : (index + 1) * len(METRICS)]
                actual = conn.execute(
                    "SELECT "
                    + ",".join("coalesce(sum(" + name + "),0)" for name in METRICS)
                    + " FROM market_daily_summary WHERE frequency='D' AND scope=?",
                    (scope,),
                ).fetchone()
                if tuple(expected) != actual:
                    raise ValueError(f"Daily summary totals disagree for {scope}")
                scope_counts[scope] = dict(zip(METRICS, expected))
            dates = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM daily_features")}
            for scope in scopes:
                actual = {
                    r[0]
                    for r in conn.execute(
                        "SELECT period_key FROM market_daily_summary "
                        "WHERE frequency='D' AND scope=?",
                        (scope,),
                    )
                }
                if actual != dates:
                    raise ValueError(f"Date coverage mismatch for {scope}")
            for valid, up, down, payload in conn.execute(
                "SELECT valid_return_count,close_limit_up_count,close_limit_down_count,"
                "return_distribution_json FROM market_daily_summary WHERE frequency='D'"
            ):
                buckets = json.loads(payload)
                if (
                    sum(buckets.values()) != valid
                    or buckets["limit_up"] != up
                    or buckets["limit_down"] != down
                ):
                    raise ValueError("Return bucket totals disagree")
            overlap = conn.execute(
                "SELECT count(*) FROM (SELECT valid_through,lead(valid_from) "
                "OVER (PARTITION BY symbol ORDER BY effective_date) AS next_start "
                "FROM corporate_actions WHERE record_kind='factor') "
                "WHERE valid_through>=next_start"
            ).fetchone()[0]
            if overlap:
                raise ValueError("Selected factor intervals overlap")
            result = dict(
                database_bytes=before[0],
                database_sha256=sha,
                integrity_check="ok",
                daily_calculation_complete=True,
                production_ready=False,
                rows=counts,
                corporate_action_kinds=kinds,
                market_sessions=len(dates),
                scopes=scope_counts,
                source_manifest_sha256=baseline["source_manifest_sha256"],
                unknowns_retained=True,
            )
    if (
        before != (path.stat().st_size, path.stat().st_mtime_ns)
        or wal.exists()
        and wal.stat().st_size
    ):
        raise ValueError("Database changed during closed-artifact verification")
    result.update(
        verification_seconds=round(time.monotonic() - started, 3),
        verification_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reuse-source-integrity", action="store_true")
    args = parser.parse_args()
    result = verify(args.root, args.reuse_source_integrity, args.evidence_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
