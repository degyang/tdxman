#!/usr/bin/env python3
"""Independent synthetic dated-fact growth; raw bars and derived tables stay fixed."""

import json
from pathlib import Path

import duckdb
import pandas as pd
from bench import (
    LAB,
    candidate_root,
    clone,
    connect,
    emit,
    file_sha256,
    literal,
    measured,
    provenance,
)

import aspool.dg03_candidate as candidate


class ProfileConnection:
    def __init__(self, conn, path):
        self.conn, self.path = conn, path
        self.active = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, sql, params=None):
        if sql.lower().startswith("select b.* replace"):
            self.conn.execute("PRAGMA enable_profiling='json'")
            self.conn.execute(f"PRAGMA profiling_output={literal(self.path)}")
            self.active = True
        self.conn.execute(sql, params or [])
        return self

    def fetchdf(self):
        result = self.conn.fetchdf()
        if self.active:
            self.conn.execute("PRAGMA disable_profiling")
            self.active = False
        return result


class ProfileDuck:
    def __init__(self, path):
        self.path = path

    def __getattr__(self, name):
        return getattr(duckdb, name)

    def connect(self, *args, **kwargs):
        return ProfileConnection(duckdb.connect(*args, **kwargs), self.path)


def run():
    expected = candidate.CandidatePool(candidate_root()).read_daily(
        start="2026-07-27", end="2026-09-24"
    )
    for factor in (1, 2, 5):
        root = candidate_root() if factor == 1 else LAB / f"facts-growth-{factor}x"
        if factor > 1:
            root.mkdir()
            measured(f"facts_{factor}x_copy", lambda: clone(candidate_root(), root), root)
            with connect(root) as c:
                c.execute(
                    f"ATTACH {literal(candidate_root() / 'catalog.duckdb')} AS base (READ_ONLY)"
                )
                for i in range(1, factor):
                    measured(
                        f"facts_{factor}x_add_{i}",
                        lambda i=i: c.execute(
                            "INSERT INTO security_daily_facts SELECT * REPLACE "
                            f"((trade_date-INTERVAL '{40 * i} years')::DATE AS trade_date, "
                            "'dg03:synthetic-facts' AS source) FROM base.security_daily_facts"
                        ).fetchone(),
                        root,
                    )
                c.execute("CHECKPOINT")
        with connect(root, True) as c:
            count = c.execute("SELECT count(*) FROM security_daily_facts").fetchone()[0]
            assert count == factor * 410467
            emit(
                "facts_growth_population",
                factor=factor,
                facts=count,
                raw_rows=c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0],
                derived="unchanged immutable snapshot",
            )
        pool = candidate.CandidatePool(root)

        def read():
            frame = pool.read_daily(start="2026-07-27", end="2026-09-24")
            pd.testing.assert_frame_equal(frame, expected)
            assert frame.attrs == expected.attrs
            return {"rows": len(frame), "fields": len(frame.columns), "exact_recent": True}

        for repeat in range(3):
            measured(f"facts_{factor}x_public_60days_{repeat}", read, root)
        path = LAB / f"facts_{factor}x_overlay_plan.json"
        candidate.duckdb = ProfileDuck(path)
        try:
            read()
        finally:
            candidate.duckdb = duckdb
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
        emit("facts_growth_overlay_plan", factor=factor, scans=scans, profile=str(path))


if __name__ == "__main__":
    provenance()
    emit("facts_growth_source", path=__file__, sha256=file_sha256(Path(__file__)))
    measured("phase_facts_growth", run, LAB)
