#!/usr/bin/env python3
"""Finish only native 5x maintenance left after rejected whole-market commit."""

from bench import LAB, connect, emit, measured, physical_bounds, profile, provenance

from aspool.dg03_candidate import CandidatePool


def run():
    root = LAB / "growth-5x"
    pool = CandidatePool(root)
    with connect(root, True) as c:
        assert c.execute("SELECT revision FROM dg03_revision").fetchone()[0] == 0
        seed = c.execute(
            "SELECT amount FROM dg03_daily WHERE __code='000001' AND __market='SZ' "
            "AND trade_date='2026-09-24'"
        ).fetchone()[0]
    for cycle in range(12):
        changes = [
            dict(
                market="SZ",
                code="000001",
                trade_date="2026-09-24",
                values={"amount": seed + cycle + 1},
            )
        ]
        for kind in ["update", "noop"]:
            result = measured(
                f"growth_5x_{kind}_{cycle}",
                lambda: pool.apply(
                    changes, source="synthetic", reason="remaining point correction"
                ),
                root,
            )
            assert result["changed"] == int(kind == "update")
    with connect(root) as c:
        physical_bounds(c, "growth_5x_physical_after_point_updates")
        measured("growth_5x_checkpoint", lambda: c.execute("CHECKPOINT").fetchall(), root)
        c.execute("BEGIN")
        try:
            profile(
                c,
                "UPDATE dg03_daily SET amount=amount+1 WHERE __market='SZ' AND __code='000001' "
                "AND trade_date='2026-09-24'",
                "growth_5x_point_correction_rolled_back",
            )
        finally:
            c.execute("ROLLBACK")
    emit(
        "native_5x_remaining_complete",
        fullmarket_insert="rejected original OOM retained",
        point_cycles=12,
    )


if __name__ == "__main__":
    provenance()
    measured("phase_native_5x_remaining", run, LAB)
