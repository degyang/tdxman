#!/usr/bin/env python3
"""Close the native write-thread configuration counterfactual at the same 1GB cap."""

import hashlib
import json
from pathlib import Path

import duckdb
from bench import LAB, connect, emit, file_sha256, measured, provenance
from monthly_bench import changes_for_day, rss_guard

from aspool.dg03_candidate import CandidatePool


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


if __name__ == "__main__":
    provenance()
    emit("native_one_thread_source", sha256=file_sha256(Path(__file__)))
    with rss_guard():
        measured("phase_native_one_thread_counterfactual", run, LAB)
