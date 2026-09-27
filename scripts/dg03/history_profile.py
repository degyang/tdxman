#!/usr/bin/env python3
"""Profile the real wide history materialization statements inside the public API."""

import json
from pathlib import Path

import duckdb
from bench import LAB, emit, file_sha256, literal, measured, provenance

import aspool.dg03_candidate as candidate


class Connection:
    def __init__(self, conn, owner):
        self.conn, self.owner = conn, owner

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, sql, params=None):
        traced = sql.startswith("INSERT INTO _dg03_requested")
        if traced:
            path = LAB / f"history_5x_decade_{len(self.owner.records)}.json"
            self.conn.execute("PRAGMA enable_profiling='json'")
            self.conn.execute(f"PRAGMA profiling_output={literal(path)}")
        self.conn.execute(sql, params or [])
        if traced:
            inserted = self.conn.fetchall()[0][0]
            self.conn.execute("PRAGMA disable_profiling")
            data = json.loads(path.read_text())
            scans = []

            def walk(node):
                if "SCAN" in node.get("operator_name", ""):
                    scans.append(
                        {
                            k: node.get(k)
                            for k in (
                                "operator_name",
                                "operator_rows_scanned",
                                "operator_cardinality",
                                "extra_info",
                            )
                        }
                    )
                for child in node.get("children", []):
                    walk(child)

            walk(data)
            record = dict(
                year_bounds=params,
                inserted=inserted,
                scans=scans,
                peak_buffer=data.get("system_peak_buffer_memory"),
                profile=str(path),
            )
            self.owner.records.append(record)
            emit("history_bounded_statement", **record)
        return self


class Duck:
    def __init__(self):
        self.records = []

    def __getattr__(self, name):
        return getattr(duckdb, name)

    def connect(self, *args, **kwargs):
        return Connection(duckdb.connect(*args, **kwargs), self)


def run():
    proxy = Duck()
    candidate.duckdb = proxy
    try:
        frame = candidate.CandidatePool(LAB / "prepared-5x").read_daily(symbols="000001.SZ")
    finally:
        candidate.duckdb = duckdb
    assert len(frame) == 31590 and len(frame.columns) == 35
    assert sum(record["inserted"] for record in proxy.records) == len(frame)
    emit(
        "history_bounded_full_api",
        rows=len(frame),
        fields=len(frame.columns),
        statements=len(proxy.records),
        raw_scan_counter_sum=sum(
            scan["operator_rows_scanned"] for record in proxy.records for scan in record["scans"]
        ),
        sql_memory_limit="512MB",
        note="counters are not unique physical rows/device bytes",
    )


if __name__ == "__main__":
    provenance()
    emit("history_profile_source", path=__file__, sha256=file_sha256(Path(__file__)))
    measured("phase_history_profile", run, LAB)
