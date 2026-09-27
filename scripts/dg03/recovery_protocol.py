#!/usr/bin/env python3
"""Controlled real-process lock overlap and joint-transaction crash evidence."""

import argparse
import fcntl
import json
import os
import select
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from bench import LAB, clone, connect, emit, file_sha256, measured, provenance

from aspool.dg03_candidate import CandidatePool, build
from aspool.pool import pool_lock


def signal(event, **values):
    print(json.dumps({"event": event, "monotonic_ns": time.monotonic_ns(), **values}), flush=True)


def release_wait():
    if not select.select([sys.stdin], [], [], 30)[0] or sys.stdin.readline().strip() != "release":
        raise TimeoutError("parent did not release child within 30 seconds")


def run_child(mode, root):
    if mode.startswith("joint_"):
        changes = json.loads((root / "joint-changes.json").read_text())

        def fault(point):
            if point == mode.removeprefix("joint_"):
                signal("fault", point=point)
                release_wait()

        CandidatePool(root).apply(
            changes, source="dg03:joint", reason="joint crash", _fault_hook=fault
        )
        return
    write = mode.endswith("write")
    if mode.startswith("native_"):
        try:
            with connect(root, read_only=not write):
                pass
        except duckdb.IOException as exc:
            signal("native_rejected", error=str(exc), write=write)
        else:
            raise AssertionError("native conflicting connection unexpectedly succeeded")
        return
    if mode.startswith("contender_"):
        fd = os.open(root, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                blocked = False
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                blocked = True
        finally:
            os.close(fd)
        signal("probe", blocked=blocked)
    signal("attempt", write=write)
    with pool_lock(root, write=write), connect(root, read_only=not write) as c:
        signal("locked", write=write)
        if write:
            c.execute("CREATE TABLE IF NOT EXISTS concurrency_probe(n INTEGER)")
            c.execute("INSERT INTO concurrency_probe VALUES (1)")
        else:
            c.execute("SELECT count(*) FROM dg03_daily").fetchone()
        release_wait()
    signal("released", write=write)


def child(mode, root):
    return subprocess.Popen(
        [sys.executable, __file__, "--child", mode, "--root", str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def receive(process, expected, timeout=20):
    deadline = time.monotonic() + timeout
    buffer = getattr(process, "_dg03_buffer", b"")
    while b"\n" not in buffer:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
            raise TimeoutError(f"waiting for {expected}")
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk:
            raise AssertionError(f"child exited before {expected}: {process.stderr.read()}")
        buffer += chunk
    line, process._dg03_buffer = buffer.split(b"\n", 1)
    message = json.loads(line)
    assert message["event"] == expected, message
    return message


def release(process):
    sent = time.monotonic_ns()
    process.stdin.write("release\n")
    process.stdin.flush()
    message = receive(process, "released")
    _, error = process.communicate(timeout=10)
    assert process.returncode == 0, error
    message["release_sent_ns"] = sent
    return message


def cleanup(processes):
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def process_concurrency(root, record=emit):
    with connect(root, True) as c:
        tables = {row[0] for row in c.execute("SHOW TABLES").fetchall()}
        before_commits = (
            c.execute("SELECT count(*) FROM concurrency_probe").fetchone()[0]
            if "concurrency_probe" in tables
            else 0
        )
    for native_mode in ("native_read", "native_write"):
        processes = []
        try:
            holder = child("holder_write", root)
            processes.append(holder)
            receive(holder, "attempt")
            locked = receive(holder, "locked")
            contender = child(native_mode, root)
            processes.append(contender)
            rejected = receive(contender, "native_rejected")
            contender.communicate(timeout=10)
            assert contender.returncode == 0
            released = release(holder)
            record(
                "controlled_" + native_mode,
                holder_locked=locked,
                rejected=rejected,
                holder_released=released,
                native_conflict_proven=True,
            )
        finally:
            cleanup(processes)
    for first, second in (
        ("write", "read"),
        ("read", "read"),
        ("write", "write"),
        ("read", "write"),
    ):
        processes = []
        try:
            holder = child("holder_" + first, root)
            processes.append(holder)
            receive(holder, "attempt")
            holder_locked = receive(holder, "locked")
            contender = child("contender_" + second, root)
            processes.append(contender)
            probe = receive(contender, "probe")
            attempt = receive(contender, "attempt")
            must_wait = first == "write" or second == "write"
            assert probe["blocked"] == must_wait
            if must_wait:
                assert not getattr(contender, "_dg03_buffer", b"")
                assert not select.select([contender.stdout], [], [], 0.2)[0]
                holder_released = release(holder)
                contender_locked = receive(contender, "locked")
                assert contender_locked["monotonic_ns"] >= holder_released["release_sent_ns"]
                contender_released = release(contender)
            else:
                contender_locked = receive(contender, "locked")
                assert holder.poll() is None
                contender_released = release(contender)
                holder_released = release(holder)
                assert contender_released["monotonic_ns"] < holder_released["monotonic_ns"]
            record(
                "controlled_" + first + "_" + second,
                probe=probe,
                holder_locked=holder_locked,
                contender_attempt=attempt,
                contender_locked=contender_locked,
                holder_released=holder_released,
                contender_released=contender_released,
                shared_overlap=not must_wait,
                protocol_verified=True,
            )
        finally:
            cleanup(processes)

    with connect(root, True) as c:
        after_commits = c.execute("SELECT count(*) FROM concurrency_probe").fetchone()[0]
    assert after_commits - before_commits == 6
    record(
        "controlled_writer_commits",
        before=before_commits,
        after=after_commits,
        committed_without_loss=6,
    )


def state(root):
    with connect(root, True) as c:
        return {
            table: c.execute(f'SELECT * FROM "{table}" ORDER BY ALL').fetchall()
            for table in (
                "dg03_daily",
                "security_daily_facts",
                "coverage",
                "daily_limit_staleness",
                "dg03_revision",
                "dg03_changes",
            )
        }


def joint_recovery(lab, record=emit):
    source = lab / "joint-source"
    source.mkdir()
    with duckdb.connect(str(source / "catalog.duckdb")) as c:
        c.execute(
            "CREATE TABLE coverage(symbol VARCHAR PRIMARY KEY,market VARCHAR,start_date DATE,"
            "end_date DATE,row_count BIGINT,source VARCHAR,updated_at TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO coverage VALUES ('000001','SZ','2025-01-02','2025-01-03',2,"
            "'baseline','2025-01-04')"
        )
        c.execute(
            "CREATE TABLE security_daily_facts(symbol VARCHAR,trade_date DATE,pre_close DOUBLE,"
            "is_st BOOLEAN,trading_status VARCHAR,source VARCHAR,fetched_at TIMESTAMP,"
            "PRIMARY KEY(symbol,trade_date))"
        )
        c.execute(
            "INSERT INTO security_daily_facts VALUES ('000001.SZ','2025-01-06',9.,false,"
            "'trading','baseline','2025-01-04')"
        )
        c.execute("CREATE TABLE daily_limit_publication(trade_date DATE PRIMARY KEY)")
        c.execute(
            "INSERT INTO daily_limit_publication VALUES "
            "('2025-01-02'),('2025-01-06'),('2025-01-07')"
        )
        c.execute(
            "CREATE TABLE daily_limit_staleness(trade_date DATE PRIMARY KEY,"
            "reason VARCHAR,marked_at TIMESTAMP)"
        )
        c.execute("INSERT INTO daily_limit_staleness VALUES ('2025-01-02','baseline','2025-01-04')")
    path = source / "lake/bars/daily/market=SZ/symbol=000001/bars.parquet"
    path.parent.mkdir(parents=True)
    values = dict(open=10.0, high=11.0, low=9.0, close=10.0, volume=100.0, amount=1000.0)
    pq.write_table(
        pa.Table.from_pylist(
            [dict(symbol="raw", trade_date=date(2025, 1, d), **values) for d in (2, 3)]
        ),
        path,
    )
    base = lab / "joint-base"
    build(source, base)
    changes = [
        dict(market="SZ", code="000001", trade_date="2025-01-06", values=values),
        dict(
            domain="facts",
            market="SZ",
            code="000001",
            trade_date="2025-01-06",
            values=dict(pre_close=11.0, is_st=True, source="joint:new", fetched_at="2025-01-06"),
        ),
    ]
    for point in ("before_commit", "after_commit"):
        root = lab / ("joint-" + point)
        root.mkdir()
        clone(base, root)
        (root / "joint-changes.json").write_text(json.dumps(changes))
        before = state(root)
        process = child("joint_" + point, root)
        try:
            ready = receive(process, "fault")
            wal = root / "catalog.duckdb.wal"
            wal_bytes = wal.stat().st_size if wal.exists() else 0
            process.kill()
            process.wait(timeout=10)
            after = state(root)
            if point == "before_commit":
                assert after == before
            else:
                assert len(after["dg03_daily"]) == len(before["dg03_daily"]) + 1
                assert set(before["dg03_daily"]) <= set(after["dg03_daily"])
                with connect(root, True) as c:
                    assert c.execute(
                        "SELECT open,high,low,close,volume,amount FROM dg03_daily "
                        "WHERE trade_date='2025-01-06'"
                    ).fetchone() == tuple(values.values())
                assert after["security_daily_facts"] == [
                    (
                        "000001.SZ",
                        date(2025, 1, 6),
                        11.0,
                        True,
                        "trading",
                        "joint:new",
                        datetime(2025, 1, 6),
                    )
                ]
                coverage = after["coverage"][0]
                assert coverage[:6] == (
                    "000001",
                    "SZ",
                    date(2025, 1, 2),
                    date(2025, 1, 6),
                    3,
                    "dg03:joint",
                )
                assert coverage[6] > before["coverage"][0][6]
                assert after["dg03_revision"] == [(1, 1)]
                assert len(after["dg03_changes"]) == 2
                assert {r[4] for r in after["dg03_changes"]} == {"merge", "facts:merge"}
                for audit in after["dg03_changes"]:
                    assert audit[:4] == (1, "SZ", "000001", date(2025, 1, 6))
                    assert audit[5:7] == ("dg03:joint", "joint crash")
                    old, new = json.loads(audit[7]), json.loads(audit[8])
                    if audit[4] == "merge":
                        assert old is None and new["amount"] == 1000.0
                    else:
                        assert old["pre_close"] == 9.0 and new["pre_close"] == 11.0
                        assert old["source"] == "baseline" and new["source"] == "joint:new"
                assert after["daily_limit_staleness"][0] == before["daily_limit_staleness"][0]
                assert [r[:2] for r in after["daily_limit_staleness"]][1:] == [
                    (date(2025, 1, 6), "joint crash"),
                    (date(2025, 1, 7), "joint crash"),
                ]
            record(
                "joint_crash_" + point,
                ready=ready,
                returncode=process.returncode,
                wal_before_kill=wal_bytes,
                all_six_domains_exact=True,
                before=before,
                after=after,
            )
        finally:
            cleanup([process])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.child:
        run_child(args.child, args.root)
    else:
        provenance()
        emit("recovery_protocol_source", path=__file__, sha256=file_sha256(Path(__file__)))
        measured("phase_joint_recovery", lambda: joint_recovery(LAB), LAB)
