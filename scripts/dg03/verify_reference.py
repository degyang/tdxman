#!/usr/bin/env python3
"""Small real-reference correctness check; not a performance benchmark."""

import pandas as pd
from bench import LAB, SNAPSHOT, connect, emit, provenance

import aspool.dg03_candidate as candidate
from aspool.pool import DataPool


def run():
    day = "2021-09-23"
    with connect(SNAPSHOT, True) as c:
        symbols = [
            r[0]
            for r in c.execute(
                """SELECT r.symbol FROM daily_limit_references r
            JOIN daily_limit_publication p USING(trade_date,batch_id)
            WHERE trade_date=? ORDER BY r.symbol LIMIT 30""",
                [day],
            ).fetchall()
        ]
    fallback = []
    original = candidate._overlay_dated_fields

    def observed(*args, **kwargs):
        fallback.append(True)
        return original(*args, **kwargs)

    candidate._overlay_dated_fields = observed
    try:
        expected = DataPool(SNAPSHOT).read_daily(symbols=symbols, start=day, end=day)
        actual = candidate.CandidatePool(LAB / "month-clustered").read_daily(
            symbols=symbols, start=day, end=day
        )
    finally:
        candidate._overlay_dated_fields = original
    pd.testing.assert_frame_equal(expected, actual)
    assert actual.pre_close_source.str.startswith("limit_derived:").any()
    emit(
        "actual_reference_overlay",
        rows=len(actual),
        day=day,
        fields=len(actual.columns),
        exact=True,
        reference_sourced_rows=int(actual.pre_close_source.str.startswith("limit_derived:").sum()),
        pandas_compatibility_fallback=bool(fallback),
    )


if __name__ == "__main__":
    provenance()
    run()
