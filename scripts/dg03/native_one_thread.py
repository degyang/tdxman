#!/usr/bin/env python3
"""Close the native write-thread configuration counterfactual at the same 1GB cap."""

import hashlib
import json
import sys
from pathlib import Path

import duckdb
from bench import LAB, connect, emit, file_sha256, measured, provenance
from monthly_bench import changes_for_day, rss_guard

from aspool.dg03_candidate import CandidatePool, literal


class OneThread(CandidatePool):
    write_threads = 1


def state(root):
    result = {}
    with connect(root, True) as c:
        result["raw_count"] = c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0]
        result["new_date_count"] = c.execute(
            "SELECT count(*) FROM dg03_daily WHERE trade_date='2026-09-25'"
        ).fetchone()[0]
        for table in [
            "security_daily_facts",
            "coverage",
            "daily_limit_staleness",
            "dg03_revision",
            "dg03_changes",
        ]:
            h = hashlib.sha256()
            cursor = c.execute(f"SELECT * FROM {table} ORDER BY ALL")
            count = 0
            while batch := cursor.fetchmany(4096):
                h.update(json.dumps(batch, default=str, separators=(",", ":")).encode())
                count += len(batch)
            result[table] = dict(rows=count, sha256=h.hexdigest())
    return result


def run():
    root = LAB / "growth-5x"
    before = state(root)
    emit("native_5x_one_thread_before", state=before)
    assert before["new_date_count"] == 0
    changes = changes_for_day()
    try:
        result = measured(
            "native_5x_one_thread_fullmarket",
            lambda: OneThread(root).apply(
                changes, source="synthetic", reason="whole market one-thread counterfactual"
            ),
            root,
        )
    except (duckdb.OutOfMemoryException, duckdb.TransactionException) as exc:
        if "failed to pin" not in str(exc) and "Out of Memory" not in str(exc):
            raise
        after = state(root)
        assert before == after
        emit(
            "native_5x_one_thread_verdict",
            passed=False,
            error=str(exc),
            six_domains_unchanged=True,
            before=before,
            after=after,
            threads=1,
            memory_limit="1GB",
        )
    else:
        after = state(root)
        assert result["changed"] == 7266 and after["new_date_count"] == 7266
        emit(
            "native_5x_one_thread_verdict",
            passed=True,
            result=result,
            before=before,
            after=after,
            threads=1,
            memory_limit="1GB",
        )


def verify_existing_failure():
    """Audit the completed failed trial without rerunning its write workload."""
    root = LAB / "growth-5x"
    with connect(root, True) as c:
        c.execute(f"ATTACH {literal(LAB / 'prepared-5x/catalog.duckdb')} AS base (READ_ONLY)")
        assert c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0] == 89311935
        assert (
            c.execute("SELECT count(*) FROM dg03_daily WHERE trade_date='2026-09-25'").fetchone()[0]
            == 0
        )
        for table in ("security_daily_facts", "coverage"):
            for left, right in [(table, f"base.{table}"), (f"base.{table}", table)]:
                assert (
                    c.execute(
                        f"SELECT count(*) FROM (SELECT * FROM {left} "
                        f"EXCEPT ALL SELECT * FROM {right})"
                    ).fetchone()[0]
                    == 0
                )
        assert c.execute("SELECT revision FROM dg03_revision").fetchone()[0] == 12
        audits = c.execute("SELECT * FROM dg03_changes ORDER BY revision").fetchall()
        assert len(audits) == 12
        original = c.execute(
            "SELECT amount FROM base.dg03_daily WHERE __market='SZ' AND __code='000001' "
            "AND trade_date='2026-09-24'"
        ).fetchone()[0]
        for i, audit in enumerate(audits):
            assert audit[:3] == (i + 1, "SZ", "000001")
            assert str(audit[3]) == "2026-09-24"
            assert audit[4:7] == ("merge", "synthetic", "remaining point correction")
            assert json.loads(audit[7])["amount"] == original + i
            assert json.loads(audit[8])["amount"] == original + i + 1
        assert (
            c.execute(
                "SELECT amount FROM dg03_daily WHERE __market='SZ' AND __code='000001' "
                "AND trade_date='2026-09-24'"
            ).fetchone()[0]
            == original + 12
        )
        assert (
            c.execute(
                "SELECT count(*) FROM (SELECT * FROM base.daily_limit_staleness "
                "EXCEPT ALL "
                "SELECT * FROM daily_limit_staleness)"
            ).fetchone()[0]
            == 0
        )
        extra = c.execute(
            "SELECT trade_date,reason FROM daily_limit_staleness "
            "WHERE trade_date NOT IN (SELECT trade_date FROM base.daily_limit_staleness)"
        ).fetchall()
        assert all(
            str(day) == "2026-09-24" and reason == "remaining point correction"
            for day, reason in extra
        )
    emit(
        "native_one_thread_failure_state_verified",
        state=state(root),
        prior_point_corrections_exact=12,
        new_market_day_absent=True,
        facts_coverage_equal_prepared=True,
        stale_only_prior_point_suffix=True,
        scope="post-trial logical state; no saved pre-trial hash from first harness version",
    )


if __name__ == "__main__":
    provenance()
    emit("native_one_thread_source", sha256=file_sha256(Path(__file__)))
    with rss_guard():
        measured(
            "phase_native_one_thread_counterfactual",
            verify_existing_failure if "--verify-only" in sys.argv else run,
            LAB,
        )
