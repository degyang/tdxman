#!/usr/bin/env python3
"""Remeasure only affected public reads after an overlay change, without writes."""

import argparse
from pathlib import Path

from bench import LAB, emit, file_sha256, measured, provenance

from aspool.dg03_candidate import CandidatePool


def run(factor, label, only=None):
    root = LAB / f"prepared-{factor}x"
    pool = CandidatePool(root)
    for workload, expected, kwargs in [
        ("day", 5570, dict(start="2026-09-24", end="2026-09-24")),
        ("60_natural_days", 243936, dict(start="2026-07-27", end="2026-09-24")),
        ("single_history", 6318 * factor, dict(symbols="000001.SZ")),
    ]:
        if only and workload != only:
            continue
        for repeat in range(3):

            def read():
                frame = pool.read_daily(**kwargs)
                assert len(frame) == expected and len(frame.columns) == 35
                return {"rows": len(frame), "fields": len(frame.columns), "attrs": frame.attrs}

            measured(f"growth_{factor}x_{label}_public_{workload}_{repeat}", read, root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor", type=int, choices=[1, 2, 5], required=True)
    parser.add_argument("--label", default="bounded")
    parser.add_argument("--workload", choices=["day", "60_natural_days", "single_history"])
    args = parser.parse_args()
    provenance()
    emit("read_samples_source", path=__file__, sha256=file_sha256(Path(__file__)))
    measured("phase_affected_reads", lambda: run(args.factor, args.label, args.workload), LAB)
