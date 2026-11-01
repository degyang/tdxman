import pytest

from aspool.baseline_factors import calculate, reference_price
from tests.unit.test_storage_single_authority import canonical  # noqa: F401


def event(cash=0, bonus=0, rights=0, price=0):
    return dict(
        dividend_per_share=cash,
        per_share_bonus=bonus,
        allotment_ratio=rights,
        allotment_price=price,
        currency="CNY",
    )


def test_cash_bonus_rights_and_share_contraction():
    assert reference_price(10, 1, 0, 0, 0) == 9
    assert reference_price(10, 0, 1, 0, 0) == 5
    assert reference_price(10, 0, 0, 1, 2) == 6
    assert reference_price(10, 0, -0.5, 0, 0) == 20
    with pytest.raises(ValueError):
        reference_price(10, 0, -1, 0, 0)


def test_duplicates_future_and_multiple_actions_during_suspension():
    records = [
        ("2020-01-02", event(cash=1)),
        ("2020-01-02", event(cash=1)),
        ("2020-01-03", event(cash=1)),
        ("2030-01-01", event(bonus=1)),
    ]
    rows, issue = calculate(records, "2020-01-01", "2020-12-31", lambda day: ("2020-01-01", 10))
    assert issue is None
    assert len(rows) == 3
    assert rows[-1]["cumulative"] == pytest.approx(1.25)
    assert rows[0]["through"] == "2020-01-01"


def test_conflict_stops_certified_coverage():
    rows, issue = calculate(
        [("2020-01-02", event(cash=1)), ("2020-01-02", event(cash=2))],
        "2020-01-01",
        "2020-12-31",
        lambda day: ("2020-01-01", 10),
    )
    assert issue["date"] == "2020-01-02"
    assert rows[-1]["through"] == "2020-01-01"


def test_rebuild_publishes_to_canonical_factor_store(canonical, tmp_path):  # noqa: F811
    import json
    import sqlite3

    from aspool.baseline_factors import rebuild
    from aspool.sqlite_publication import migration_path, pending_path

    with sqlite3.connect(canonical / "event_baseline.sqlite") as conn:
        conn.execute("CREATE TABLE metadata(payload TEXT)")
        conn.execute("INSERT INTO metadata VALUES (?)", (json.dumps({"sha256": "test-proof"}),))
        conn.execute("CREATE TABLE events(symbol TEXT,effective_date TEXT,payload TEXT)")
        conn.execute(
            "INSERT INTO events VALUES (?,?,?)",
            ("000001.SZ", "2026-09-28", json.dumps(event(cash=1))),
        )
    marker = migration_path(canonical)
    marker.parent.mkdir(exist_ok=True, parents=True)
    marker.write_text(json.dumps({"operation": "qfq-history-audit"}))
    result = rebuild(canonical, tmp_path / "report", "2026-09-28", apply=True)
    assert result["unresolved"] == []
    with sqlite3.connect(canonical / "adjustments.sqlite") as conn:
        values = conn.execute(
            "SELECT source,cumulative_factor,input_hash FROM "
            "stock_adjustment_factors WHERE symbol=? ORDER BY effective_date",
            ("000001.SZ",),
        ).fetchall()
    assert len(values) == 2
    assert all(row[0] == "derived:baseline_event_chain" and len(row[2]) == 64 for row in values)
    assert values[-1][1] == pytest.approx(10 / 9)
    assert not pending_path(canonical).exists()
    assert marker.exists()


def test_baseline_references_use_events_and_bound_dates(tmp_path):
    import json
    import sqlite3

    from aspool.event_baseline import BaselineReferences

    with sqlite3.connect(tmp_path / "event_baseline.sqlite") as conn:
        conn.execute("CREATE TABLE events(symbol TEXT,effective_date TEXT,payload TEXT)")
        conn.executemany(
            "INSERT INTO events VALUES (?,?,?)",
            [
                ("000001.SZ", "2020-01-02", json.dumps(event(cash=1))),
                ("000001.SZ", "2020-01-03", json.dumps(event(bonus=1))),
                ("000001.SZ", "2030-01-03", json.dumps(event(cash=1))),
            ],
        )
    baseline = BaselineReferences(tmp_path)
    prior = dict(trade_date="2020-01-01", close=10)
    assert baseline.reference("000001.SZ", prior, "2020-01-03") == (4.5, "derived:baseline_events")
    assert baseline.reference("000002.SZ", prior, "2020-01-03") == (
        10,
        "derived:baseline_no_action",
    )
