#!/usr/bin/env python3
"""Portable active-month backup, revision interruption and actual publication overlap."""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from bench import LAB, emit, file_sha256, measured, provenance, tree_bytes
from recovery_protocol import cleanup, receive, release_wait, signal

from aspool.api_contract import DataPoolError
from aspool.dg03_candidate import copy_ancillary
from aspool.dg03_monthly import MonthlyPool, connection, digest, part_path
from aspool.pool import pool_lock


def portable_copy(source, target):
    target.mkdir()
    for name in ["parts", "history", "work"]:
        (target / name).mkdir()
    with pool_lock(source):
        for name in ["catalog.duckdb", "source-schema.json", "empty.parquet"]:
            shutil.copy2(source / name, target / name)
        copy_ancillary(source, target)
        with connection(source, True) as c:
            parts = c.execute("SELECT path,sha256,row_count FROM dg03_monthly_parts").fetchall()
        for relative, sha, _ in parts:
            destination = part_path(target, relative)
            shutil.copy2(part_path(source, relative), destination)
            assert digest(destination) == sha
        assert file_sha256(source / "catalog.duckdb") == file_sha256(target / "catalog.duckdb")
    return dict(
        active_files=len(parts),
        rows=sum(r[2] for r in parts),
        disk=tree_bytes(target),
        catalog_exact=True,
        all_part_hashes_exact=True,
    )


def child(mode, root):
    signal("attempt")
    pool = MonthlyPool(root)
    if mode == "reader":
        frame = pool.read_daily(symbols="000001.SZ", start="2026-09-24", end="2026-09-24")
        signal("read", amount=float(frame.iloc[0].amount))
    elif mode == "writer":

        def hook(point):
            if point == "before_publish":
                signal("prepared")
                release_wait()

        result = pool.apply(
            [
                dict(
                    market="SZ",
                    code="000001",
                    trade_date="2026-09-24",
                    values={"amount": 1234567.0},
                )
            ],
            source="dg03:recovery",
            reason="controlled publish",
            _fault_hook=hook,
        )
        signal("published", result=result)


def spawn(mode, root):
    return subprocess.Popen(
        [sys.executable, __file__, "--child", mode, "--root", str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def run():
    source, root = LAB / "monthly-1x", LAB / "monthly-restored"
    result = measured("monthly_portable_backup_restore", lambda: portable_copy(source, root), root)
    emit("monthly_restore_manifest", **result)
    pool = MonthlyPool(root)
    # Nonempty real event batch, then real publication changes the inherited token.
    with pool.iter_limit_events_with_amount(
        start="2021-09-24", end="2026-09-24", close_limit_up=True, min_consecutive_up=1, threads=1
    ) as reader:
        first = next(reader)
        assert len(first) > 0 and "amount" in first
        processes = []
        try:
            writer = spawn("writer", root)
            processes.append(writer)
            receive(writer, "attempt")
            prepared = receive(writer, "prepared", timeout=60)
            observer = spawn("reader", root)
            processes.append(observer)
            attempt = receive(observer, "attempt")
            import select

            assert not select.select([observer.stdout], [], [], 0.2)[0]
            released = time.monotonic_ns()
            writer.stdin.write("release\n")
            writer.stdin.flush()
            published = receive(writer, "published", timeout=60)
            observed = receive(observer, "read", timeout=60)
            assert observed["amount"] == 1234567.0 and observed["monotonic_ns"] >= released
            for p in processes:
                _, error = p.communicate(timeout=10)
                assert p.returncode == 0, error
            emit(
                "monthly_actual_writer_reader",
                prepared=prepared,
                reader_attempt=attempt,
                published=published,
                observed=observed,
                release_ns=released,
                serialized=True,
            )
        finally:
            cleanup(processes)
        try:
            next(reader)
        except DataPoolError as exc:
            assert exc.code == "LIMIT_REVISION_CHANGED"
            emit("monthly_nonempty_revision_gate", first_rows=len(first), error_code=exc.code)
        else:
            raise AssertionError("iterator did not reject changed revision")
    assert reader.closed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=["reader", "writer"])
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.root)
    else:
        provenance()
        emit("monthly_recovery_source", sha256=file_sha256(Path(__file__)))
        measured("phase_monthly_recovery", run, LAB)
