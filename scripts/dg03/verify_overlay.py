#!/usr/bin/env python3
"""Only the public windows affected by the SQL overlay and key-access repair."""

from bench import LAB, SNAPSHOT, compare_call, connect, emit, measured, provenance

from aspool.dg03_candidate import CandidatePool, copy_ancillary
from aspool.pool import DataPool


def run():
    root = LAB / "month-clustered"
    if not (root / "lake").exists():
        measured("copy_unchanged_non_daily", lambda: copy_ancillary(SNAPSHOT, root), root)
    old, new = DataPool(SNAPSHOT), CandidatePool(root)
    for start, end in [
        ("2026-07-27", "2026-09-24"),
        ("2021-09-24", "2021-11-22"),
        ("2024-05-12", "2024-07-10"),
    ]:
        with connect(root, True) as c:
            emit(
                "overlay_window_population",
                start=start,
                end=end,
                facts=c.execute(
                    "SELECT count(*) FROM security_daily_facts WHERE trade_date BETWEEN ? AND ?",
                    [start, end],
                ).fetchone()[0],
                references=c.execute(
                    "SELECT count(*) FROM daily_limit_references r "
                    "JOIN daily_limit_publication p USING(trade_date,batch_id) "
                    "WHERE trade_date BETWEEN ? AND ?",
                    [start, end],
                ).fetchone()[0],
                stale=c.execute(
                    "SELECT count(*) FROM daily_limit_staleness WHERE trade_date BETWEEN ? AND ?",
                    [start, end],
                ).fetchone()[0],
            )
        compare_call(
            "sql_overlay_window",
            lambda: old.read_daily(start=start, end=end),
            lambda: new.read_daily(start=start, end=end),
            repeats=1,
        )
    compare_call(
        "final_single_history",
        lambda: old.read_daily(symbols="000001.SZ"),
        lambda: new.read_daily(symbols="000001.SZ"),
    )
    compare_call(
        "final_etf_window",
        lambda: old.read_etf_daily(start="2026-07-27", end="2026-09-24"),
        lambda: new.read_etf_daily(start="2026-07-27", end="2026-09-24"),
        repeats=1,
    )
    compare_call(
        "unchanged_index",
        lambda: old.read_index_daily(symbols="000001.SH", lookback=5),
        lambda: new.read_index_daily(symbols="000001.SH", lookback=5),
        repeats=1,
    )
    with connect(root, True) as c:
        emit(
            "final_database_size",
            details=c.execute("PRAGMA database_size").fetchdf().to_dict("records"),
        )


if __name__ == "__main__":
    provenance()
    measured("phase_overlay_verification", run, LAB)
