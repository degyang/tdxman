"""Source qfq persistence, affine references, fallback and atomic publication."""

import json

import pytest

from aspool import DataPool
from aspool.qfq_audit_repair import assess_day, repair_day
from aspool.sqlite_qfq import ensure_qfq_schema, paired_reference, valid_prices
from aspool.sqlite_stock_store import stock_connection
from tests.unit.test_storage_single_authority import canonical  # noqa: F401


def prices(op, high, low, close, **extra):
    return dict(open=op, high=high, low=low, close=close, **extra)


def test_affine_cash_dividend_reference_is_not_close_ratio():
    # Future cash deductions shift both streams. On the current ex-date, the
    # prior raw close 20 becomes an exchange reference 18, not a close ratio.
    raw = prices(18, 19, 17, 18.5)
    qfq = prices(15, 16, 14, 15.5, source_as_of="2026-09-30")
    prior = prices(20, 21, 19, 20)
    prior_qfq = prices(15, 16, 14, 15, source_as_of="2026-09-30")
    reference, basis = paired_reference(raw, qfq, prior, prior_qfq)
    assert reference == 18
    assert basis == "derived:paired_qfq_affine"
    assert 18.5 * 15 / 15.5 != reference


def test_star_depositary_receipt_has_twenty_percent_limit():
    from datetime import date

    from tdxman.codec.price_rules import resolve_limit_rule
    from tdxman.models.enums import Market

    rule = resolve_limit_rule(
        Market.SH, "689009", "", date(2022, 5, 18), st_status=False, observed_sessions=200
    )
    assert rule.rule.limit_pct == 0.20


def test_ordinary_day_uses_exact_prior_close_and_negative_qfq_is_valid():
    raw = prices(2, 3, 1, 2)
    qfq = prices(-8, -7, -9, -8, source_as_of="2026-09-30")
    prior = prices(2, 3, 1, 2.2)
    prior_qfq = prices(-8, -7, -9, -7.8, source_as_of="2026-09-30")
    assert valid_prices(qfq)
    assert paired_reference(raw, qfq, prior, prior_qfq) == (2.2, "derived:paired_qfq_no_action")


def test_flat_or_missing_qfq_is_a_labelled_fallback():
    raw = prices(10, 10, 10, 10)
    prior = prices(9, 10, 8, 9)
    assert paired_reference(raw, None, prior, None) == (9, "estimated:previous_raw_close")
    assert paired_reference(raw, raw, None, None) == (None, None)


def test_rounding_does_not_invent_an_ex_date():
    raw = prices(60.08, 60.08, 58.98, 58.98)
    prior = prices(54.61, 65.53, 54.61, 65.53)

    def adjusted(r):
        return {**{k: round(r[k] * 0.73 - 4.01, 2) for k in PRICES}, "source_as_of": "2026-10-02"}

    from aspool.sqlite_qfq import PRICES

    assert paired_reference(raw, adjusted(raw), prior, adjusted(prior)) == (
        65.53,
        "derived:paired_qfq_no_action",
    )


def test_qfq_repair_is_base_input_and_second_pass_is_noop(canonical):  # noqa: F811
    ensure_qfq_schema(canonical)
    day = "2026-09-28"
    observation = dict(
        symbol="000001.SZ",
        trade_date=day,
        source="test:source",
        source_as_of=day,
        raw=prices(10, 11, 10, 11, vol=100, amount=1000),
        qfq=prices(10, 11, 10, 11),
    )
    with stock_connection(canonical, read_only=False) as conn:
        previous = {"000001.SZ": (prices(10, 10, 10, 10), prices(10, 10, 10, 10, source_as_of=day))}
        result = repair_day(
            conn,
            day=day,
            observations=[observation],
            previous=previous,
            streaks={"000001.SZ": 0},
            ages={"000001.SZ": 10},
            listing_dates={},
        )
        assert result["qfq_rows"] == 1
        replay = repair_day(
            conn,
            day=day,
            observations=[observation],
            previous={
                "000001.SZ": (prices(10, 10, 10, 10), prices(10, 10, 10, 10, source_as_of=day))
            },
            streaks={"000001.SZ": 0},
            ages={"000001.SZ": 10},
            listing_dates={},
        )
        assert all(
            replay[k] == 0
            for k in ("qfq_rows", "raw_rows", "feature_rows", "summary_rows", "board_rows")
        )
        summary, issues = assess_day(conn, day)
        assert not issues
        assert summary["uncomputable"] == 0
    qfq = DataPool(canonical).read_downloaded_qfq(start=day, end=day)
    assert qfq.attrs["layer"] == "base"
    assert qfq.iloc[0]["close"] == 11


def test_failed_summary_rolls_back_base_qfq(canonical, monkeypatch):  # noqa: F811
    from aspool import sqlite_market_summary

    ensure_qfq_schema(canonical)

    def fail(*args, **kwargs):
        raise RuntimeError("injected aggregation failure")

    monkeypatch.setattr(sqlite_market_summary, "recompute_daily_summary", fail)
    with stock_connection(canonical, read_only=False) as conn:
        with pytest.raises(RuntimeError, match="injected"):
            repair_day(
                conn,
                day="2026-09-28",
                observations=[
                    dict(
                        symbol="000001.SZ",
                        trade_date="2026-09-28",
                        source="fixture",
                        source_as_of="2026-09-28",
                        raw=prices(10, 11, 10, 10.5, vol=100, amount=1000),
                        qfq=prices(10, 11, 10, 10.5),
                    )
                ],
                previous={},
                streaks={},
                ages={"000001.SZ": 6},
                listing_dates={},
            )
        assert conn.execute("SELECT count(*) FROM stock_qfq_bars").fetchone()[0] == 0


def test_confirmed_suspension_is_not_a_missing_trade(canonical):  # noqa: F811
    ensure_qfq_schema(canonical)
    with stock_connection(canonical, read_only=False) as conn:
        repair_day(
            conn,
            day="2026-09-28",
            observations=[
                dict(
                    symbol="000001.SZ",
                    trade_date="2026-09-28",
                    source="fixture",
                    source_as_of="2026-09-28",
                    raw=prices(10, 10, 10, 10, vol=0, amount=0),
                    qfq=prices(10, 10, 10, 10),
                    facts=dict(trading_status="SUSPENDED"),
                )
            ],
            previous={},
            streaks={},
            ages={},
            listing_dates={},
        )
        row = conn.execute(
            "SELECT trading_status,calc_status,close_limit_up FROM daily_features "
            "WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        ).fetchone()
        assert row[0:2] == ("SUSPENDED", "NO_TRADE")
        assert not row[2]
        summary, _ = assess_day(conn, "2026-09-28")
        assert summary["uncomputable"] == 0


def test_base_replication_carries_downloaded_qfq(canonical, tmp_path):  # noqa: F811
    from aspool.base_delta import _checksum, apply_delta

    fields = ["symbol", "trade_date", "open", "high", "low", "close", "source", "source_as_of"]
    row = dict(
        symbol="000001.SZ",
        trade_date="2026-09-28",
        **prices(10, 11, 10, 11),
        source="fixture",
        source_as_of="2026-09-28",
    )
    value = dict(
        format="aspool-base-delta-v1",
        target=str(canonical),
        window=dict(start="2026-09-28", end="2026-09-28", symbols=["000001.SZ"]),
        datasets=[
            dict(
                name="stock-qfq-bars",
                fields=fields,
                keys=["symbol", "trade_date"],
                changes=[dict(key=["000001.SZ", "2026-09-28"], before=None, after=row)],
            )
        ],
    )
    value["sha256"] = _checksum(value)
    package = tmp_path / "qfq-base.json"
    package.write_text(json.dumps(value))
    apply_delta(canonical, package)
    qfq = DataPool(canonical).read_downloaded_qfq(start="2026-09-28", end="2026-09-28")
    assert qfq.iloc[0]["close"] == 11
    with stock_connection(canonical) as c:
        revision = c.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()
    apply_delta(canonical, package)
    with stock_connection(canonical) as c:
        assert (
            c.execute("SELECT revision FROM dataset_state WHERE dataset='stock_raw'").fetchone()
            == revision
        )


def test_qfq_base_delta_rebuilds_a_missing_reference(canonical, tmp_path):  # noqa: F811
    import sqlite3

    from aspool.base_delta import _checksum, apply_delta
    from aspool.sqlite_qfq import PRICES

    with sqlite3.connect(canonical / "features.sqlite") as conn:
        conn.execute(
            "UPDATE stock_daily_features SET source_pre_close=NULL,"
            "source_pre_close_source=NULL,pre_close=NULL,pre_close_source=NULL,"
            "limit_status='UNKNOWN',limit_reason='missing_reference',limit_up_price=NULL,"
            "limit_down_price=NULL,touch_limit_up=NULL,close_limit_up=NULL,"
            "touch_limit_down=NULL,close_limit_down=NULL"
        )
    with stock_connection(canonical) as conn:
        bars = conn.execute(
            "SELECT trade_date,open,high,low,close FROM daily_bars "
            "WHERE symbol='000001.SZ' ORDER BY trade_date"
        ).fetchall()
    changes = []
    for day, *values in bars:
        row = dict(
            symbol="000001.SZ",
            trade_date=day,
            source="fixture:qfq",
            source_as_of="2026-09-28",
            **{k: v - (1 if day == "2026-09-27" else 0) for k, v in zip(PRICES, values)},
        )
        changes.append(dict(key=[row["symbol"], day], before=None, after=row))
    value = dict(
        format="aspool-base-delta-v1",
        target=str(canonical),
        window=dict(start="2026-09-27", end="2026-09-28", symbols=["000001.SZ"]),
        datasets=[
            dict(
                name="stock-qfq-bars",
                keys=["symbol", "trade_date"],
                fields=["symbol", "trade_date", *PRICES, "source", "source_as_of"],
                changes=changes,
            )
        ],
    )
    value["sha256"] = _checksum(value)
    package = tmp_path / "base-only-qfq.json"
    package.write_text(json.dumps(value))
    apply_delta(canonical, package)
    with stock_connection(canonical) as conn:
        ref = conn.execute(
            "SELECT pre_close,pre_close_source FROM daily_features "
            "WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        ).fetchone()
        assert ref[0] == pytest.approx(bars[0][-1] - 1)
        assert ref[1] == "derived:paired_qfq_affine"


def test_full_repair_releases_maintenance_gate_and_replays(canonical, tmp_path):  # noqa: F811
    from aspool.qfq_audit import stage_connection
    from aspool.qfq_audit_repair import run
    from aspool.sqlite_publication import migration_path
    from aspool.sqlite_state_audit import prepare_recovery

    out, recovery = tmp_path / "report", tmp_path / "verified-recovery"
    out.mkdir()
    from tests.unit.test_storage_single_authority import update

    update(canonical)
    prepare_recovery(canonical, recovery)
    observation = dict(
        symbol="000001.SZ",
        trade_date="2026-09-28",
        source="fixture",
        source_as_of="2026-09-28",
        raw=prices(10, 11, 10, 10.5, vol=100, amount=1000),
        qfq=prices(10, 11, 10, 10.5),
    )
    with stage_connection(out) as stage:
        stage.execute(
            "INSERT INTO observations VALUES (?,?,?)",
            (observation["symbol"], observation["trade_date"], json.dumps(observation)),
        )
    result = run(canonical, out, "2026-09-28", "2026-09-28", repair=True, recovery=recovery)
    assert result["totals"]["qfq_rows"] == 1
    assert not migration_path(canonical).exists()
    again = run(canonical, out, "2026-09-28", "2026-09-28", repair=True, recovery=recovery)
    assert all(value == 0 for value in again["totals"].values())


def test_history_gate_allows_its_own_publication_recovery(canonical, tmp_path, monkeypatch):  # noqa: F811
    from aspool import sqlite_publication as publication
    from aspool.qfq_audit import stage_connection
    from aspool.qfq_audit_repair import run
    from aspool.sqlite_state_audit import prepare_recovery
    from tests.unit.test_storage_single_authority import update

    update(canonical)
    out, recovery = tmp_path / "interrupted", tmp_path / "backup"
    out.mkdir()
    prepare_recovery(canonical, recovery)
    with stage_connection(out):
        pass

    def fail(point):
        if point == "after_sqlite_commit":
            raise RuntimeError("simulated publication interruption")

    monkeypatch.setattr(publication, "_fault", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        run(canonical, out, "2026-09-28", "2026-09-28", repair=True, recovery=recovery)
    assert publication.migration_path(canonical).exists()
    assert publication.pending_path(canonical).exists()
    monkeypatch.setattr(publication, "_fault", lambda _: None)
    run(canonical, out, "2026-09-28", "2026-09-28", repair=True, recovery=recovery)
    assert not publication.migration_path(canonical).exists()
    assert not publication.pending_path(canonical).exists()


def test_conflict_resolution_replays_dated_source_facts(canonical, tmp_path, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    import baostock

    from aspool.qfq_audit_resolve import resolve
    from aspool.sqlite_state_audit import prepare_recovery
    from tests.unit.test_storage_single_authority import update

    update(canonical, close=11)
    out, backup = tmp_path / "conflicts", tmp_path / "recovery-point"
    out.mkdir()
    prepare_recovery(canonical, backup)
    (out / "anomalies.json").write_text(
        json.dumps([dict(symbol="000001.SZ", date="2026-09-28", reason="price_outside_limits")])
    )
    with stock_connection(canonical) as conn:
        row = conn.execute(
            "SELECT open,high,low,close FROM daily_bars WHERE symbol='000001.SZ' "
            "AND trade_date='2026-09-28'"
        ).fetchone()
    from aspool.sqlite_qfq import PRICES

    (out / "conflict-sources.json").write_text(
        json.dumps(
            [
                dict(
                    symbol="000001.SZ",
                    date="2026-09-28",
                    code="sz.000001",
                    **dict(zip(PRICES, map(str, row))),
                    preclose="10.1",
                    isST="0",
                )
            ]
        )
    )
    monkeypatch.setattr(baostock, "login", lambda: SimpleNamespace(error_code="0"))
    monkeypatch.setattr(baostock, "logout", lambda: None)
    result = resolve(canonical, out, "2026-09-28", "2026-09-28", backup)
    assert result["changed_facts"] == 1
    with stock_connection(canonical) as conn:
        reference = conn.execute(
            "SELECT pre_close,source_pre_close_source FROM daily_features "
            "WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        ).fetchone()
        assert reference == (10.1, "baostock:history")
