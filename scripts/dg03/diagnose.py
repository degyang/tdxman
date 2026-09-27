#!/usr/bin/env python3
"""Time binding, each validation query, Pandas conversion, and the dated overlay.

Proxies exist only inside this one diagnostic process; production files and
benchmark processes are untouched. These samples are explanatory, not the main
un-instrumented timing results.
"""

import time

import duckdb
from bench import LAB, SNAPSHOT, connect, emit, profile, provenance

import aspool.dg03_candidate as candidate
import aspool.pool as original


class Connection:
    def __init__(self, conn, label):
        self.conn, self.label = conn, label

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def __getattr__(self, name):
        attribute = getattr(self.conn, name)
        if name not in {"execute", "read_parquet", "fetchdf", "fetchall", "fetchone"}:
            return attribute

        def call(*args, **kwargs):
            tick = time.perf_counter()
            result = attribute(*args, **kwargs)
            emit(
                "diagnostic_stage",
                backend=self.label,
                operation=name,
                sql=args[0] if name == "execute" else None,
                seconds=time.perf_counter() - tick,
            )
            return self if name == "execute" else result

        return call


class Duck:
    def __init__(self, label):
        self.label = label

    def __getattr__(self, name):
        return getattr(duckdb, name)

    def connect(self, *args, **kwargs):
        tick = time.perf_counter()
        conn = duckdb.connect(*args, **kwargs)
        emit(
            "diagnostic_stage",
            backend=self.label,
            operation="connect",
            seconds=time.perf_counter() - tick,
        )
        return Connection(conn, self.label)


def run():
    overlay = original._overlay_dated_fields
    for module, root, label in [
        (original, SNAPSHOT, "parquet"),
        (candidate, LAB / "candidate", "duckdb"),
    ]:
        module.duckdb = Duck(label)

        def timed_overlay(*args, **kwargs):
            tick = time.perf_counter()
            result = overlay(*args, **kwargs)
            emit(
                "diagnostic_stage",
                backend=label,
                operation="overlay",
                seconds=time.perf_counter() - tick,
            )
            return result

        module._overlay_dated_fields = timed_overlay
        pool = module.DataPool(root) if label == "parquet" else module.CandidatePool(root)
        for name, kwargs in [
            ("single", dict(symbols="000001.SZ")),
            ("60days", dict(start="2026-07-27", end="2026-09-24")),
        ]:
            emit("diagnostic_begin", backend=label, workload=name)
            pool.read_daily(**kwargs)
        module.duckdb = duckdb
        module._overlay_dated_fields = overlay
    with connect(LAB / "candidate", True) as c:
        profile(
            c,
            "SELECT * FROM dg03_daily WHERE __market='SZ' AND __code='000001'",
            "baseline_single_plan",
        )
        profile(
            c, "SELECT * FROM dg03_daily WHERE trade_date=DATE '2026-09-24'", "baseline_day_plan"
        )


if __name__ == "__main__":
    provenance()
    run()
