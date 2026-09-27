#!/usr/bin/env python3
"""Diagnose bounded wide-row fetch after a narrow indexed row-id lookup."""

from bench import LAB, connect, emit, measured, profile, provenance


def run():
    root = LAB / "growth-5x"
    with connect(root, True) as c:
        c.execute("SET memory_limit='512MB'")
        ids = measured(
            "single_5x_narrow_rowids",
            lambda: (
                c.execute("SELECT rowid FROM dg03_daily WHERE __code='000001' ORDER BY rowid")
                .fetchnumpy()["rowid"]
                .tolist()
            ),
        )
        emit("single_5x_narrow_population", rows=len(ids))
        for size in (256, 2048):
            profile(
                c,
                "SELECT * FROM dg03_daily WHERE rowid BETWEEN ? AND ? AND __code='000001'",
                f"single_5x_rowid_chunk_{size}",
                [ids[0], ids[size - 1]],
            )


if __name__ == "__main__":
    provenance()
    measured("phase_single_memory", run, LAB)
