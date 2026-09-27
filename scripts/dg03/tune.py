#!/usr/bin/env python3
"""Test month/security clustering after the measured date-only layout regression."""

from bench import LAB, clone, compare_call, connect, measured, physical_bounds, profile, provenance

from aspool.dg03_candidate import CandidatePool


def run():
    root = LAB / "month-clustered"
    root.mkdir()
    clone(LAB / "candidate", root)
    with connect(root) as c:
        measured(
            "month_cluster_rebuild",
            lambda: c.execute("""CREATE TABLE reordered AS
            SELECT * FROM dg03_daily
            ORDER BY date_trunc('month',trade_date),__market,__code,trade_date""").fetchone(),
            root,
        )
        c.execute("DROP TABLE dg03_daily; ALTER TABLE reordered RENAME TO dg03_daily")
        c.execute("ALTER TABLE dg03_daily ADD PRIMARY KEY(__market,__code,trade_date)")
        c.execute("CREATE INDEX dg03_security ON dg03_daily(__code)")
        c.execute("CHECKPOINT")
        assert c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0] == 17862387
        # Reordering uses SELECT * without casts or filters; the original full
        # source parity is reused. Recheck only affected public workloads below.
        physical_bounds(c, "month_cluster_bounds")
        profile(
            c,
            "SELECT * FROM dg03_daily WHERE __market='SZ' AND __code='000001'",
            "month_cluster_single_plan",
        )
        profile(
            c,
            "SELECT * FROM dg03_daily WHERE trade_date=DATE '2026-09-24'",
            "month_cluster_day_plan",
        )
    before, after = CandidatePool(LAB / "candidate"), CandidatePool(root)
    for name, kwargs in [
        ("day", dict(start="2026-09-24", end="2026-09-24")),
        ("60days", dict(start="2026-07-27", end="2026-09-24")),
        ("single", dict(symbols="000001.SZ")),
    ]:
        compare_call(
            "month_cluster_" + name,
            lambda: before.read_daily(**kwargs),
            lambda: after.read_daily(**kwargs),
        )


if __name__ == "__main__":
    provenance()
    measured("phase_month_cluster", run, LAB)
