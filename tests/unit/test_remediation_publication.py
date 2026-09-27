"""DG04 fixed-plan resume, source gates, and full-reference comparison."""

import json
from datetime import date, timedelta

import pytest

from aspool import limit_events as limits
from aspool.daily_storage import merge_daily
from aspool.remediation import execute, output_version, plan
from aspool.security_facts import initialize_facts
from aspool.store import catalog, initialize


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "pool"
    initialize(root)
    initialize_facts(root)
    limits.initialize_limits(root)
    days = [date(2026, 5, 4) + timedelta(days=i) for i in range(5)]
    rows = [
        dict(
            trade_date=d,
            symbol="000001",
            open=10.0,
            high=11.0,
            low=10.0,
            close=11.0,
            pre_close=10.0,
            is_st=False,
            volume=100.0,
            amount=1000.0,
        )
        for d in days
    ]
    merge_daily(root, "SZ", "000001", rows, "test")
    with catalog(root) as conn:
        conn.executemany(
            "INSERT INTO security_calendar VALUES (?, true, 'test')", [(d,) for d in days]
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    target, state = tmp_path / "plan.json", tmp_path / "state.json"
    spec = plan(root, days[0], days[-1], manifest, target)
    return root, days, target, state, spec


def test_bounded_resume_and_finished_noop(setup):
    root, days, target, state, spec = setup
    result = execute(target, state, max_days=2)
    assert result["next"] == 2
    with catalog(root) as conn:
        assert conn.execute("SELECT count(*) FROM daily_limit_staleness").fetchone()[0] == 3
    execute(target, state, max_days=2)
    result = execute(target, state, max_days=2)
    assert result["next"] == 5
    versions = [output_version(root, d) for d in days]
    assert execute(target, state, max_days=2) == result
    assert versions == [output_version(root, d) for d in days]
    assert json.loads(target.read_text())["plan_id"] == spec["plan_id"]


def test_input_and_plan_drift_refused(setup):
    root, days, target, state, _ = setup
    execute(target, state, max_days=1)
    with catalog(root) as conn:
        conn.execute("UPDATE security_calendar SET source='changed'")
    with pytest.raises(ValueError, match="Source changed"):
        execute(target, state, max_days=1)
    doc = json.loads(target.read_text())
    doc["dates"].pop()
    target.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="integrity"):
        execute(target, state, max_days=1)


def test_crash_after_publish_before_checkpoint_retries_only_pending(setup, monkeypatch):
    root, days, target, state, _ = setup
    original = limits._publish_batch
    calls = []

    def crash(*args):
        original(*args)
        calls.append(args[2].trade_date)
        if len(calls) == 2:
            raise RuntimeError("crash after daily commit")

    monkeypatch.setattr(limits, "_publish_batch", crash)
    with pytest.raises(RuntimeError, match="daily commit"):
        execute(target, state, max_days=3)
    assert json.loads(state.read_text())["next"] == 1
    assert json.loads(state.read_text())["pending"] == str(days[1])
    monkeypatch.setattr(limits, "_publish_batch", original)
    execute(target, state, max_days=5)
    assert json.loads(state.read_text())["next"] == 5
    assert calls == days[:2]


def test_reference_equivalence_and_output_drift(setup, tmp_path):
    import shutil

    root, days, target, state, _ = setup
    reference = tmp_path / "reference"
    shutil.copytree(root, reference)
    limits.compute_limit_events(reference, [days[0], days[-1]])
    execute(target, state, max_days=1)
    execute(target, state, max_days=4)
    for table in (
        "daily_limit_events",
        "daily_limit_exceptions",
        "daily_limit_scope",
        "daily_limit_references",
        "daily_limit_gap_states",
        "daily_limit_streak_boundaries",
    ):
        with catalog(root) as a, catalog(reference) as b:
            assert (
                a.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
                == b.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
            )
    with catalog(root) as conn:
        conn.execute("DELETE FROM daily_limit_scope WHERE trade_date=?", [days[0]])
    with pytest.raises(ValueError, match="changed externally"):
        execute(target, state, max_days=1)


@pytest.mark.parametrize(
    "field,value", [("next", 99), ("initialized", False), ("pending", "2099-01-01")]
)
def test_forged_state_is_rejected(setup, field, value):
    _, _, target, state, _ = setup
    execute(target, state, max_days=1)
    doc = json.loads(state.read_text())
    doc[field] = value
    state.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="checkpoint"):
        execute(target, state, max_days=1)


def test_missing_external_checkpoint_restored_and_external_stale_rejected(setup):
    root, days, target, state, _ = setup
    execute(target, state, max_days=1)
    state.unlink()
    assert execute(target, state, max_days=1)["next"] == 2
    with catalog(root) as conn:
        conn.execute("INSERT INTO daily_limit_staleness VALUES (?, 'external', now())", [days[0]])
    with pytest.raises(ValueError, match="changed externally"):
        execute(target, state, max_days=1)


def test_recovery_and_implementation_gates(setup):
    _, _, target, state, _ = setup
    doc = json.loads(target.read_text())
    from aspool.remediation import signature

    doc["implementation"]["dependencies"]["duckdb"] = "unknown"
    doc.pop("plan_id")
    doc["plan_id"] = signature(doc)
    target.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="Implementation"):
        execute(target, state, max_days=1)


def test_nonempty_reference_ipo_and_gap_boundaries_survive_resume(tmp_path):
    import shutil

    root = tmp_path / "boundary-pool"
    initialize(root)
    initialize_facts(root)
    limits.initialize_limits(root)
    days = [date(2022, 1, 4), date(2022, 1, 5), date(2022, 1, 6), date(2022, 1, 7)]

    def bar(day, close, pre, pct=None):
        return dict(
            trade_date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            pre_close=pre,
            pct_chg=pct,
            is_st=False,
            volume=100.0,
            amount=1000.0,
        )

    merge_daily(
        root,
        "SZ",
        "001236",
        [bar(days[0], 10.0, 10.0), bar(days[1], 11.0, 10.0), bar(days[3], 12.1, 11.0)],
        "test",
    )
    merge_daily(
        root,
        "SZ",
        "300001",
        [
            *[bar(days[0] - timedelta(days=i), 10.0, 10.0, 0.0) for i in range(20, 0, -1)],
            bar(days[0], 10.0, 8.0, 0.0),
            bar(days[1], 12.0, 8.0, 20.0),
            bar(days[2], 14.4, 8.0, 20.0),
            bar(days[3], 17.28, 8.0, 20.0),
        ],
        "test",
    )
    with catalog(root) as conn:
        conn.executemany(
            "INSERT INTO security_calendar VALUES (?,true,'test')", [(d,) for d in days]
        )
        conn.executemany(
            "INSERT INTO security_calendar VALUES (?,true,'test')",
            [(days[0] - timedelta(days=i),) for i in range(20, 0, -1)],
        )
        conn.execute(
            "INSERT INTO security_lifecycle VALUES ('001236.SZ',?,NULL,'IPO','test',now())",
            [days[0]],
        )
        conn.execute(
            "INSERT INTO security_lifecycle VALUES "
            "('300001.SZ','2010-01-01',NULL,'REF','test',now())"
        )
    ref = tmp_path / "boundary-reference"
    shutil.copytree(root, ref)
    manifest = tmp_path / "recovery.json"
    manifest.write_text("{}")
    target, state = tmp_path / "boundary-plan.json", tmp_path / "boundary-state.json"
    plan(root, days[0], days[-1], manifest, target)
    for _ in days:
        execute(target, state, max_days=1)
    limits.compute_limit_events(ref, [days[0], days[-1]])
    for table in (
        "daily_limit_references",
        "daily_limit_streak_boundaries",
        "daily_limit_gap_states",
        "daily_limit_events",
        "daily_limit_exceptions",
    ):
        with catalog(root) as a, catalog(ref) as b:
            actual = a.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
            assert actual == b.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
            if table not in ("daily_limit_events", "daily_limit_exceptions"):
                assert actual, table
    with catalog(root) as conn:
        assert (
            conn.execute(
                "SELECT consecutive_up FROM daily_limit_events "
                "WHERE symbol='001236.SZ' AND trade_date=?",
                [days[-1]],
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("mutation", ["height", "stale", "gap", "suspended_height"])
def test_frozen_initial_predecessor_refuses_drift_before_any_write(setup, tmp_path, mutation):
    root, days, _, _, _ = setup
    limits.compute_limit_events(root, [days[0], days[1]])
    # Force a real nonempty up predecessor independent of fixture IPO rules.
    with catalog(root) as conn:
        predecessor = days[0] if mutation == "suspended_height" else days[1]
        conn.execute(
            "INSERT INTO daily_limit_events VALUES "
            "(?, ?, '000001.SZ', true,false,true,false,11,9,1,0)",
            [f"daily-limit-{predecessor:%Y%m%d}-stock", predecessor],
        )
        conn.execute("DELETE FROM daily_limit_exceptions WHERE trade_date=?", [predecessor])
        if mutation == "suspended_height":
            conn.execute("DELETE FROM daily_limit_exceptions WHERE trade_date=?", [days[1]])
            conn.execute("DELETE FROM daily_limit_scope WHERE trade_date=?", [days[1]])
            conn.execute(
                "INSERT INTO security_daily_facts VALUES "
                "('000001.SZ',?,10,false,'SUSPENDED','test',now())",
                [days[1]],
            )
    manifest = tmp_path / "new-recovery.json"
    manifest.write_text("{}")
    target, state = tmp_path / "suffix.json", tmp_path / "suffix-state.json"
    plan(root, days[2], days[-1], manifest, target)
    with catalog(root) as conn:
        if mutation in ("height", "suspended_height"):
            conn.execute(
                "UPDATE daily_limit_events SET consecutive_up=99 WHERE trade_date=?", [predecessor]
            )
        elif mutation == "stale":
            conn.execute("INSERT INTO daily_limit_staleness VALUES (?, 'drift',now())", [days[1]])
        else:
            conn.execute(
                "UPDATE daily_limit_events SET consecutive_gap_sessions=99 WHERE trade_date=?",
                [days[1]],
            )
        before = conn.execute("SELECT * FROM daily_limit_staleness ORDER BY ALL").fetchall()
        publications = conn.execute("SELECT * FROM daily_limit_publication ORDER BY ALL").fetchall()
    with pytest.raises(ValueError, match="predecessor state changed"):
        execute(target, state, max_days=1)
    assert not state.exists()
    with catalog(root) as conn:
        assert before == conn.execute("SELECT * FROM daily_limit_staleness ORDER BY ALL").fetchall()
        assert (
            publications
            == conn.execute("SELECT * FROM daily_limit_publication ORDER BY ALL").fetchall()
        )
