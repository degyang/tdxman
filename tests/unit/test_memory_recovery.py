"""Bounded resource use and failed-write derivation regression tests."""

import json
from datetime import date, datetime

import pytest

from aspool.fetch import fetch_sync
from aspool.fundamentals import _publish_quote_rows
from aspool.store import initialize


def test_retry_closes_replaced_connection_before_opening_another():
    live = set()
    peak = 0

    class Connection:
        def __enter__(self):
            nonlocal peak
            live.add(self)
            peak = max(peak, len(live))
            return self

        def __exit__(self, *_):
            live.remove(self)

    def fail(*_):
        raise RuntimeError("simulated transport failure")

    results = list(fetch_sync(range(30), Connection, fail, retry_factory=Connection))
    assert len(results) == 30
    assert peak == 1
    assert not live


def test_failed_quote_write_does_not_schedule_derivation(tmp_path):
    initialize(tmp_path)
    result = _publish_quote_rows(
        tmp_path, [("600519", {"trade_date": date(2026, 9, 18)}, {})]
    )
    assert result[0] == 0
    assert len(result[3]) == 1
    assert result[-1] == []


def test_derivation_failure_keeps_durable_progress_and_reports_partial(tmp_path, monkeypatch):
    from aspool import fundamentals, limit_events

    initialize(tmp_path)
    monkeypatch.setattr(fundamentals, "_symbols", lambda *_: [])
    monkeypatch.setattr(
        fundamentals, "_publish_quote_rows",
        lambda *_: (1, 1, 0, [], [], [date(2026, 9, 18)]),
    )

    def fail(*args, **kwargs):
        progress = json.loads((tmp_path / "reports/maintenance/latest.json").read_text())
        assert progress["status"] == "deriving"
        assert progress["success"] == 1
        raise RuntimeError("injected derived failure")

    monkeypatch.setattr(limit_events, "compute_limit_events", fail)
    with pytest.raises(ValueError, match="部分报价"):
        fundamentals.update_from_quotes(tmp_path, now=datetime(2026, 9, 18, 17))
    report = json.loads((tmp_path / "reports/maintenance/latest.json").read_text())
    assert report["status"] == "partial"
    assert report["limit_events"]["status"] == "failed"
