"""Published event amount reads, bounded batches, and revision safety."""

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from aspool import DataPool, DataPoolError
from aspool.free_stockdb import _write_daily
from aspool.limit_events import initialize_limits
from aspool.store import catalog, initialize


def _seed(root, *, duplicate=False):
    initialize(root)
    initialize_limits(root)
    rows = [dict(trade_date=date(2026, 9, 23), open=10., high=11., low=9.,
                 close=10., volume=1000., amount=12000.)]
    _write_daily(root, "SZ", "000001", rows)
    if duplicate:
        # Inject an invalid existing file to test the public reader's guard.
        import pyarrow as pa
        import pyarrow.parquet as pq

        from aspool.store import daily_path

        pq.write_table(pa.Table.from_pylist(rows * 2), daily_path(root, "SZ", "000001"))
    with catalog(root) as conn:
        for day, suffix in ((date(2026, 9, 23), "a"), (date(2026, 9, 24), "b")):
            conn.execute(
                "insert into daily_limit_batches (batch_id, trade_date, scope_id, rule_version, "
                "computed_at, processed_count, known_count, unknown_count, no_limit_count, status) "
                "values (?, ?, 'stock', 'v7', ?, 1, 1, 0, 0, 'published')",
                [suffix, day, datetime(2026, 9, 25)],
            )
            conn.execute(
                "insert into daily_limit_publication values (?, ?, ?)",
                [day, suffix, datetime(2026, 9, 25)],
            )
            conn.execute(
                "insert into daily_limit_events "
                "(batch_id, trade_date, symbol, close_limit_up, consecutive_up) "
                "values (?, ?, '000001.SZ', true, ?)",
                [suffix, day, 1 if suffix == "a" else None],
            )


def test_amount_join_keeps_missing_event_and_filters_explicitly(tmp_path):
    _seed(tmp_path)
    api = DataPool(tmp_path)
    with api.iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", batch_days=1,
    ) as batches:
        result = list(batches)
    assert batches.completed
    assert len(result) == 2
    assert result[0].amount.tolist() == [12000.0]
    assert pd.isna(result[1].amount.iloc[0])
    assert result[0].batch_id.tolist() == ["a"]
    assert result[0].rule_version.tolist() == ["v7"]
    assert result[0].attrs["amount_unit"] == "CNY"
    assert pd.isna(result[1].consecutive_up.iloc[0])
    with api.iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", close_limit_up=True,
        min_consecutive_up=1, fields=["symbol", "trade_date", "amount"],
    ) as batches:
        selected = list(batches)
    assert len(selected) == 1 and len(selected[0]) == 1
    assert list(selected[0]) == ["symbol", "trade_date", "amount"]


def test_duplicate_bar_key_is_error(tmp_path):
    _seed(tmp_path, duplicate=True)
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-23", temp_directory=tmp_path,
    ) as batches:
        with pytest.raises(DataPoolError, match="Duplicate daily key"):
            next(batches)
    assert batches.closed and not list(tmp_path.glob("aspool-read-*"))
    with catalog(tmp_path) as conn:
        assert conn.execute("select count(*) from daily_limit_publication").fetchone()[0] == 2


def test_publication_change_fails_and_closes(tmp_path):
    _seed(tmp_path)
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", batch_days=1,
        temp_directory=tmp_path,
    ) as batches:
        next(batches)
        with catalog(tmp_path) as conn:
            conn.execute(
                "insert into daily_limit_staleness values (?, 'revised', ?)",
                [date(2026, 9, 24), datetime(2026, 9, 25, 1)],
            )
        with pytest.raises(DataPoolError) as error:
            next(batches)
        assert error.value.code == "LIMIT_REVISION_CHANGED"
    assert not list(tmp_path.glob("aspool-read-*"))


def test_revision_after_last_batch_is_detected_on_exit(tmp_path):
    _seed(tmp_path)
    with pytest.raises(DataPoolError) as error:
        with DataPool(tmp_path).iter_limit_events_with_amount(
            start="2026-09-23", end="2026-09-23", temp_directory=tmp_path,
        ) as batches:
            next(batches)
            with catalog(tmp_path) as conn:
                conn.execute(
                    "insert into daily_limit_staleness values (?, 'late revision', ?)",
                    [date(2026, 9, 23), datetime(2026, 9, 25, 2)],
                )
    assert error.value.code == "LIMIT_REVISION_CHANGED"
    assert batches.closed and not list(tmp_path.glob("aspool-read-*"))


def test_early_close_releases_temp_dir(tmp_path):
    _seed(tmp_path)
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", batch_days=1,
        temp_directory=tmp_path,
    ) as batches:
        next(batches)
    assert batches.closed and not batches.completed
    assert not list(tmp_path.glob("aspool-read-*"))


def test_stale_event_remains_visible(tmp_path):
    _seed(tmp_path)
    with catalog(tmp_path) as conn:
        conn.execute(
            "insert into daily_limit_staleness values (?, 'revised', ?)",
            [date(2026, 9, 23), datetime(2026, 9, 25, 1)],
        )
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-23", close_limit_up=True,
        min_consecutive_up=1,
    ) as batches:
        rows = list(batches)
    assert len(rows) == 1 and bool(rows[0].stale.iloc[0])
    assert rows[0].stale_reason.tolist() == ["revised"]


def test_published_empty_day_yields_no_phantom_event(tmp_path):
    _seed(tmp_path)
    day = date(2026, 9, 25)
    with catalog(tmp_path) as conn:
        conn.execute(
            "insert into daily_limit_batches (batch_id, trade_date, scope_id, rule_version, "
            "computed_at, processed_count, known_count, unknown_count, no_limit_count, status) "
            "values ('c', ?, 'stock', 'v7', ?, 1, 1, 0, 0, 'published')",
            [day, datetime(2026, 9, 25)],
        )
        conn.execute(
            "insert into daily_limit_publication values (?, 'c', ?)",
            [day, datetime(2026, 9, 25)],
        )
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-25", batch_days=1,
    ) as batches:
        rows = list(batches)
    assert len(rows) == 2 and batches.completed


def test_daily_narrow_projection_keeps_published_reference_priority(tmp_path):
    _seed(tmp_path)
    with catalog(tmp_path) as conn:
        conn.execute(
            "insert into daily_limit_references "
            "(batch_id, trade_date, symbol, reference_pre_close, basis) "
            "values ('a', ?, '000001.SZ', 9.0, 'test_basis')",
            [date(2026, 9, 23)],
        )
    api = DataPool(tmp_path)
    assert api.read_research_daily(
        symbols="000001.SZ", start="2026-09-23", end="2026-09-23",
        fields=["pre_close"],
    ).pre_close.tolist() == [9.0]
    assert api.read_research_daily(
        symbols="000001.SZ", start="2026-09-23", end="2026-09-23",
        fields=["pre_close_source"],
    ).pre_close_source.tolist() == ["limit_derived:test_basis"]
    assert api.read_research_daily(
        symbols="000001.SZ", start="2026-09-23", end="2026-09-23",
        fields=["amount"],
    ).amount.tolist() == [12000.0]


@pytest.mark.parametrize("missing", ["column", "value", "mixed_schema"])
def test_missing_amount_preserves_event_and_nullable_schema(tmp_path, missing):
    from aspool.store import daily_paths

    _seed(tmp_path)
    path = daily_paths(tmp_path, "SZ", "000001")[0]
    bars = pd.read_parquet(path)
    if missing == "value":
        bars["amount"] = None
    else:
        bars = bars.drop(columns="amount")
    bars.to_parquet(path, index=False)
    if missing == "mixed_schema":
        _write_daily(tmp_path, "SZ", "000002", [{
            "trade_date": date(2026, 9, 23), "open": 10., "high": 11.,
            "low": 9., "close": 10., "volume": 1000., "amount": 9000.,
        }])
        with catalog(tmp_path) as conn:
            conn.execute(
                "insert into daily_limit_events "
                "(batch_id, trade_date, symbol, close_limit_up, consecutive_up) "
                "values ('a', ?, '000002.SZ', true, 1)", [date(2026, 9, 23)],
            )
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-23",
    ) as batches:
        frame = next(batches).set_index("symbol")
    assert pd.isna(frame.loc["000001.SZ", "amount"])
    assert frame.loc["000001.SZ", "batch_id"] == "a"
    assert len(frame) == (2 if missing == "mixed_schema" else 1)
    if missing == "mixed_schema":
        assert frame.loc["000002.SZ", "amount"] == 9000.


def test_overlay_is_bounded_and_preserves_fact_priority(tmp_path, monkeypatch):
    import aspool.store as store
    from aspool.security_facts import initialize_facts

    _seed(tmp_path)
    initialize_facts(tmp_path)
    with catalog(tmp_path) as conn:
        conn.execute(
            "insert into security_daily_facts values "
            "('000001.SZ', ?, 9.5, true, 'TRADING', 'dated_fact', ?)",
            [date(2026, 9, 23), datetime(2026, 9, 25)],
        )
    observed = []
    original = store.read_only_catalog

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, key):
            return getattr(self.conn, key)

        def execute(self, sql, *args):
            if sql.startswith("select b.* replace"):
                observed.append(self.conn.execute(
                    "select current_setting('memory_limit'), current_setting('threads'), "
                    "current_setting('temp_directory')"
                ).fetchone())
            return self.conn.execute(sql, *args)

    @contextmanager
    def watched(root):
        with original(root) as conn:
            yield Connection(conn)

    monkeypatch.setattr(store, "read_only_catalog", watched)
    api = DataPool(tmp_path)
    for field, expected in (
        ("pre_close", 9.5), ("pre_close_source", "dated_fact"),
        ("is_st", True), ("is_st_source", "dated_fact"),
        ("trading_status", "TRADING"), ("trading_status_source", "dated_fact"),
    ):
        result = api.read_daily(fields=[field])
        assert result[field].tolist() == [expected]
    assert len(observed) == 6
    for memory, threads, temp in observed:
        assert memory == "488.2 MiB" and threads == 2
        assert not Path(temp).exists()
    api.read_daily(fields=["amount"])
    assert len(observed) == 6


@pytest.mark.parametrize("start,end", [("NaT", "2026-09-24"), ("2026-09-23", "NaT")])
def test_missing_date_boundary_is_public_error(tmp_path, start, end):
    with pytest.raises(DataPoolError) as error:
        DataPool(tmp_path).iter_limit_events_with_amount(start=start, end=end)
    assert error.value.code == "INVALID_ARGUMENT"


def test_oversized_batch_fails_without_truncating_and_releases(tmp_path):
    _seed(tmp_path)
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", max_rows=1,
        temp_directory=tmp_path,
    ) as batches:
        with pytest.raises(DataPoolError) as error:
            next(batches)
    assert error.value.code == "LIMIT_TOO_LARGE"
    assert batches.closed and not batches.completed
    assert not list(tmp_path.glob("aspool-read-*"))
    with catalog(tmp_path) as conn:
        assert conn.execute("select count(*) from daily_limit_events").fetchone()[0] == 2


def test_revision_on_empty_publication_day_is_detected(tmp_path):
    _seed(tmp_path)
    with catalog(tmp_path) as conn:
        conn.execute("delete from daily_limit_events where batch_id='b'")
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", batch_days=1,
        temp_directory=tmp_path,
    ) as batches:
        next(batches)
        with catalog(tmp_path) as conn:
            conn.execute(
                "update daily_limit_publication set published_at=? where batch_id='b'",
                [datetime(2026, 9, 25, 3)],
            )
        with pytest.raises(DataPoolError) as error:
            next(batches)
    assert error.value.code == "LIMIT_REVISION_CHANGED"
    assert batches.closed and not list(tmp_path.glob("aspool-read-*"))


def test_omitting_amount_skips_daily_association(tmp_path, monkeypatch):
    from aspool.limit_amount import EventAmountBatches

    _seed(tmp_path)

    def unexpected(*args):
        pytest.fail("Daily association must be skipped when amount is not requested")

    monkeypatch.setattr(EventAmountBatches, "_attach_amount", unexpected)
    with DataPool(tmp_path).iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", fields=["symbol", "batch_id"],
    ) as batches:
        frame = next(batches)
    assert list(frame) == ["symbol", "batch_id"]
    assert frame.batch_id.tolist() == ["a", "b"]
