"""Quote/K-line separation, finite source retries and metric dependency updates."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest
from click.testing import CliRunner

from aspool.cli import cli
from aspool.source_retry import read_with_retry
from aspool.sqlite_daily_sync import sync_daily_source
from aspool.sqlite_daily_update import apply_daily_changes
from aspool.sqlite_stock_store import stock_connection
from aspool.sqlite_update_cli import QuoteSource, SourceSession, _sync_candidates, run_update
from aspool.sqlite_volume_metrics import TURNOVER_SOURCE, VOLUME_SOURCE
from tdxman.mac.client import MacClient

DAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 17, tzinfo=ZoneInfo("Asia/Shanghai"))


def store(root):
    with stock_connection(root, create=True, read_only=False) as conn:
        days = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
        for day in days:
            conn.execute(
                "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,"
                "volume,amount,updated_at) "
                "VALUES ('000001.SZ',?,10,10,10,10,100,1000,1)",
                (day,),
            )
            conn.execute(
                "INSERT INTO daily_features(symbol,trade_date,pre_close,source_pre_close,"
                "source_pre_close_source,"
                "is_st,calc_status,limit_status,streak_known,updated_at) "
                "VALUES ('000001.SZ',?,10,10,'fixture',0,'TRADED','UNKNOWN',0,1)",
                (day,),
            )
        conn.commit()
    with duckdb.connect(str(root / "catalog.duckdb")) as conn:
        conn.execute(
            "CREATE TABLE security_calendar(trade_date DATE PRIMARY KEY,"
            "is_open BOOLEAN,source VARCHAR)"
        )
        conn.execute(
            "CREATE TABLE security_lifecycle(symbol VARCHAR PRIMARY KEY,"
            "listing_date DATE,delisting_date DATE)"
        )
        conn.executemany(
            "INSERT INTO security_lifecycle VALUES (?,'2020-01-01',NULL)",
            [(s,) for s in ["000001.SZ", "600001.SH", "920011.BJ"]],
        )
        conn.executemany(
            "INSERT INTO security_calendar VALUES (?,true,'fixture')", [(d,) for d in days]
        )
    return days


def quote(code, **changes):
    return dict(
        code=code,
        server_update_date=20260928,
        name="测试股份",
        open=10,
        high=11,
        low=10,
        close=11,
        pre_close=10,
        vol=2,
        amount=2200,
        turnover=3.25,
        vol_ratio=7.25,
        **changes,
    )


class FakeMac:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_stock_quotes(self, stocks, fields):
        return pd.DataFrame([quote(code) for market, code in stocks])

    def get_stock_quotes_list(self, category, **kwargs):
        from tdxman.mac.enums import Category

        market, code = {
            Category.SH: (1, "600001"),
            Category.SZ: (0, "000001"),
            Category.BJ: (2, "920011"),
        }[category]
        return pd.DataFrame([dict(market=market, code=code, name="测试股份")])

    def get_stock_kline(self, *args, **kwargs):
        raise AssertionError("update must not call K-line API")


def test_update_quotes_keep_vendor_metrics_and_compute_limits(tmp_path, monkeypatch):
    store(tmp_path)
    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: FakeMac())
    result = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert result["status"] == "ok"
    assert result["factor_unavailable"] == [{"symbol": "000001.SZ", "reason": "no_verified_anchor"}]
    assert result["factor_quality"] == {"ready": False, "unavailable": 1}
    with stock_connection(tmp_path) as conn:
        row = conn.execute(
            "SELECT volume,vol_ratio,turnover_rate,vol_ratio_source,turnover_rate_source "
            "FROM daily_bars WHERE trade_date='2026-09-28'"
        ).fetchone()
        assert row == (200, 7.25, 3.25, "tdxman:quote", "tdxman:quote")
        assert (
            conn.execute(
                "SELECT close_limit_up FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone()[0]
            == 1
        )
        assert conn.execute(
            "SELECT close_limit_up_count FROM market_daily_summary WHERE period_key='2026-09-28'"
        ).fetchall() == [(1,), (1,)]
    repeated = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert repeated["success"][0]["changed_rows"] == 0
    assert repeated["success"][0]["changed_feature_rows"] == 0


def test_quote_pre_close_extends_factor_without_full_event_request(tmp_path, monkeypatch):
    from tdxman.client import TdxClient

    store(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES ('000001.SZ','2026-09-18','factor','selected:source_cumulative_factor',"
            "'selected',1,'2026-09-18','2026-09-25',"
            "'source_cumulative_factor:source_anchor:vendor',1)"
        )
        conn.commit()

    class NoActions:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_finance_info(self, *args, **kwargs):
            raise AssertionError("unchanged pre-close must not fetch corporate actions")

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: FakeMac())
    monkeypatch.setattr(TdxClient, "from_best_host", lambda **kwargs: NoActions())
    report = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert report["status"] == "ok"
    assert "factor_quality" not in report
    with stock_connection(tmp_path) as conn:
        assert (
            conn.execute(
                "SELECT max(valid_through) FROM corporate_actions "
                "WHERE symbol='000001.SZ' AND record_kind='factor'"
            ).fetchone()[0]
            == "2026-09-28"
        )
        payload = conn.execute(
            "SELECT payload_json FROM corporate_actions WHERE symbol='000001.SZ' "
            "AND record_kind='factor' AND valid_through='2026-09-28'"
        ).fetchone()[0]
    import json

    assert json.loads(payload)["fw03_advance"]["source"] == "tdx:xdxr"


def test_quote_partial_retry_only_requests_missing_and_supports_bj():
    calls = []

    class Partial(FakeMac):
        def get_stock_quotes(self, stocks, fields):
            calls.append([code for market, code in stocks])
            keys = stocks[:1] if len(calls) == 1 else stocks
            return pd.DataFrame([quote(code) for market, code in keys])

    events = []
    session = SourceSession(Partial, retries=2, delay=0, events=events)
    source = QuoteSource(session, DAY)
    source.fetch(["000001.SZ", "920011.BJ"])
    session.close()
    assert calls == [["000001", "920011"], ["920011"]]
    assert set(source.rows) == {"000001.SZ", "920011.BJ"} and not source.errors
    assert len(events) == 1


def test_bj_quote_is_stored_but_excluded_from_limit_statistics(tmp_path, monkeypatch):
    store(tmp_path)
    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: FakeMac())
    report = run_update(tmp_path, symbols=["920011.BJ"], now=NOW, retry_delay=0)
    assert report["status"] == "ok"
    assert report["factor_quality"]["unavailable"] == 1
    with stock_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT calc_status,limit_status,close_limit_up,consecutive_up "
            "FROM daily_features WHERE symbol='920011.BJ' AND trade_date='2026-09-28'"
        ).fetchone() == ("TRADED", "NO_LIMIT", 0, 0)


def test_quote_exhaustion_retains_old_data_and_reports_failure(tmp_path, monkeypatch):
    store(tmp_path)
    calls = []

    class Failed(FakeMac):
        def get_stock_quotes(self, stocks, fields):
            if stocks[0][0].name == "SH":
                return super().get_stock_quotes(stocks, fields)
            calls.append(1)
            raise OSError("read timed out")

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: Failed())
    report = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert len(calls) == 3 and report["status"] == "completed_with_missing"
    assert report["missing"] == ["000001.SZ"]
    with stock_connection(tmp_path) as conn:
        assert conn.execute("select count(*) from daily_bars").fetchone()[0] == 6
        assert conn.execute(
            "SELECT calc_status,source_is_st FROM daily_features WHERE trade_date='2026-09-28'"
        ).fetchone() == ("NO_TRADE", 0)


def test_three_consecutive_source_failures_persist_missing_and_abort_remaining(tmp_path):
    days = store(tmp_path)

    class Failed:
        def get_daily(self, *args, **kwargs):
            raise OSError("connection reset")

    symbols = ["000001.SZ", "600001.SH", "920011.BJ", "000002.SZ"]
    report = sync_daily_source(
        tmp_path,
        client=Failed(),
        symbols=symbols,
        market_sessions=[*days, "2026-09-28"],
        as_of="2026-09-28",
        start="2026-09-28",
        end="2026-09-28",
        source="tdxman:kline",
        max_consecutive_failures=3,
    )
    assert report["aborted"] is True
    assert report["remaining_block"] == ["000002.SZ"]
    with stock_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT symbol,trading_status,calc_status FROM daily_features "
            "WHERE trade_date='2026-09-28' ORDER BY symbol"
        ).fetchall() == [
            ("000001.SZ", "MISSING", "NO_TRADE"),
            ("600001.SH", "MISSING", "NO_TRADE"),
            ("920011.BJ", "MISSING", "NO_TRADE"),
        ]


def test_sync_uses_kline_and_fills_missing_metrics(tmp_path, monkeypatch):
    store(tmp_path)

    class Klines(FakeMac):
        def get_stock_quotes(self, *args):
            raise AssertionError("sync must not call quotes")

        def get_stock_kline(self, market, code, *args, **kwargs):
            return pd.DataFrame(
                [
                    dict(
                        datetime=datetime(2026, 9, 28),
                        open=10,
                        high=11,
                        low=10,
                        close=11,
                        vol=200,
                        amount=2200,
                        float_shares=2,
                    )
                ]
            )

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: Klines())
    result = run_update(
        tmp_path,
        mode="sync",
        symbols=["000001.SZ"],
        start="2026-09-28",
        end="2026-09-28",
        now=NOW,
        retry_delay=0,
    )
    assert result["status"] == "ok"
    assert result["factor_quality"]["unavailable"] == 1
    with stock_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT volume,float_share,vol_ratio,turnover_rate FROM daily_bars "
            "WHERE trade_date='2026-09-28'"
        ).fetchone() == (200, 20000, 2, 1)
        assert (
            conn.execute(
                "SELECT count(*) FROM market_daily_summary WHERE period_key='2026-09-28'"
            ).fetchone()[0]
            == 2
        )


def test_successful_kline_window_marks_absent_session_as_no_trade(tmp_path):
    days = store(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES ('000001.SZ',?,'factor','fixture','selected',1,?,?,"
            "'source_cumulative_factor:source_anchor:fixture',1)",
            (days[0], days[0], "2026-09-29"),
        )
        conn.commit()

    class OneDay:
        def get_daily(self, market, code, **kwargs):
            return pd.DataFrame(
                [
                    dict(
                        symbol="000001.SZ",
                        date=date(2026, 9, 28),
                        open=10,
                        high=10,
                        low=10,
                        close=10,
                        volume=100,
                        amount=1000,
                    )
                ]
            )

    sync_daily_source(
        tmp_path,
        client=OneDay(),
        symbols=["000001.SZ"],
        market_sessions=[*days, "2026-09-28", "2026-09-29"],
        as_of="2026-09-29",
        start="2026-09-28",
        end="2026-09-29",
        source="tdxman:kline",
    )
    with stock_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT trading_status,calc_status FROM daily_features "
            "WHERE symbol='000001.SZ' AND trade_date='2026-09-29'"
        ).fetchone() == ("NO_TRADE", "NO_TRADE")


def test_volume_repair_updates_five_successors_and_rolls_back(tmp_path, monkeypatch):
    days = store(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute(
            "UPDATE daily_bars SET vol_ratio=1,vol_ratio_source=?,float_share=10000,"
            "float_share_source='tdxman:kline',turnover_rate=1,turnover_rate_source=?",
            (VOLUME_SOURCE, TURNOVER_SOURCE),
        )
        conn.commit()
        apply_daily_changes(
            conn,
            bars=[dict(symbol="000001.SZ", trade_date=days[0], volume=200)],
            market_sessions=days,
        )
        assert conn.execute(
            "SELECT vol_ratio FROM daily_bars WHERE trade_date=?", (days[-1],)
        ).fetchone()[0] == pytest.approx(100 / 120)
        assert (
            conn.execute(
                "SELECT turnover_rate FROM daily_bars WHERE trade_date=?", (days[0],)
            ).fetchone()[0]
            == 2
        )
        before = list(conn.iterdump())
        import aspool.sqlite_daily_update as writer

        def fail(*args, **kwargs):
            raise RuntimeError("summary failure")

        monkeypatch.setattr(writer, "recompute_daily_summary", fail)
        with pytest.raises(RuntimeError, match="summary failure"):
            apply_daily_changes(
                conn,
                bars=[dict(symbol="000001.SZ", trade_date=days[0], volume=300)],
                market_sessions=days,
            )
        assert list(conn.iterdump()) == before


def test_cli_routes_sources_and_sync_keeps_enrichment(tmp_path, monkeypatch):
    (tmp_path / "stocks.sqlite").touch()
    calls = []

    def run(root, *, report, **kwargs):
        calls.append(kwargs)
        report.update(status="ok")

    monkeypatch.setattr("aspool.sqlite_update_cli.run_update", run)
    runner = CliRunner()
    assert runner.invoke(cli, ["update", "--root", str(tmp_path)]).exit_code == 0
    assert calls[-1]["source"] == "tdx"
    assert (
        runner.invoke(
            cli, ["update", "--root", str(tmp_path), "--source", "baostock", "--retries", "3"]
        ).exit_code
        == 0
    )
    assert calls[-1]["source"] == "baostock" and calls[-1]["retries"] == 3
    assert (
        runner.invoke(
            cli, ["sync", "--root", str(tmp_path), "--start", "2026-09-23", "--end", "2026-09-24"]
        ).exit_code
        == 0
    )
    assert calls[-1]["mode"] == "sync"
    assert calls[-1]["count"] is None
    assert runner.invoke(cli, ["sync", "--root", str(tmp_path), "--no-enrich"]).exit_code == 2


def test_all_cli_refreshes_directories_before_index_stock_and_etf(tmp_path, monkeypatch):
    order = []

    def read_directory(session, *, report):
        order.append("stock-directory")
        report.update(markets={"SH": 1, "SZ": 0, "BJ": 0}, failed=[])
        return {"600001.SH": "测试"}

    def publish_stock(*args, **kwargs):
        order.append("publish-stock-directory")

    def online_items(**kwargs):
        order.append("etf-directory")
        return [dict(code="510010", market="SH", name="ETF")]

    def publish_etf(*args, **kwargs):
        order.append("publish-etf-directory")
        return {"listed": 1}

    def indices(*args, **kwargs):
        order.append("index")
        return {"status": "ok"}, tmp_path / "index.json"

    def stock(root, *, report, **kwargs):
        order.append("stock")
        assert kwargs["directory_rows"] == {"600001.SH": "测试"}
        report.update(status="ok")

    def etfs(*args, **kwargs):
        order.append("etf")
        assert kwargs["items"][0]["code"] == "510010"
        return {"status": "ok"}, tmp_path / "etf.json"

    monkeypatch.setattr("aspool.sqlite_directory.read_directory", read_directory)
    monkeypatch.setattr("aspool.sqlite_directory.publish_directory", publish_stock)
    monkeypatch.setattr("aspool.sqlite_etf_sync.online_items", online_items)
    monkeypatch.setattr("aspool.sqlite_etf_sync.publish_directory", publish_etf)
    monkeypatch.setattr("aspool.sqlite_index_sync.sync_indices", indices)
    monkeypatch.setattr("aspool.sqlite_update_cli.run_update", stock)
    monkeypatch.setattr("aspool.sqlite_etf_sync.sync_etfs", etfs)
    result = CliRunner().invoke(cli, ["update", "--type", "all", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert order == [
        "stock-directory",
        "publish-stock-directory",
        "etf-directory",
        "publish-etf-directory",
        "index",
        "stock",
        "etf",
    ]


def test_sync_count_defaults_to_ten_and_conflicts_with_explicit_range(tmp_path, monkeypatch):
    (tmp_path / "stocks.sqlite").touch()
    calls = []

    def run(root, *, report, **kwargs):
        calls.append(kwargs)
        report.update(status="ok")

    monkeypatch.setattr("aspool.sqlite_update_cli.run_update", run)
    runner = CliRunner()
    result = runner.invoke(cli, ["sync", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls[-1]["count"] == 10
    result = runner.invoke(
        cli,
        ["sync", "--root", str(tmp_path), "--count", "30"],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1]["count"] == 30
    result = runner.invoke(
        cli,
        [
            "sync",
            "--root",
            str(tmp_path),
            "--count",
            "30",
            "--start",
            "2026-09-01",
            "--end",
            "2026-09-28",
        ],
    )
    assert result.exit_code == 2
    assert "互斥" in result.output


def test_sync_candidate_scan_skips_complete_symbols_and_selects_real_gaps(tmp_path):
    days = store(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES ('000001.SZ',?,'factor','fixture','selected',1,?,?,"
            "'source_cumulative_factor:source_anchor:fixture',1)",
            (days[0], days[0], days[-1]),
        )
        conn.commit()
    lifecycle = {"000001.SZ": (date(2020, 1, 1), None)}
    assert _sync_candidates(tmp_path, ["000001.SZ"], days[-3:], lifecycle) == []
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute(
            "DELETE FROM daily_bars WHERE symbol='000001.SZ' AND trade_date=?", (days[-2],)
        )
        conn.execute(
            "DELETE FROM daily_features WHERE symbol='000001.SZ' AND trade_date=?", (days[-2],)
        )
        conn.commit()
    assert _sync_candidates(tmp_path, ["000001.SZ"], days[-3:], lifecycle) == ["000001.SZ"]


def test_sync_with_no_candidates_is_a_successful_noop(tmp_path, monkeypatch):
    store(tmp_path)
    monkeypatch.setattr("aspool.sqlite_update_cli._sync_candidates", lambda *args: [])
    report = run_update(tmp_path, mode="sync", now=NOW, retry_delay=0)
    assert report["status"] == "ok"
    assert report["requested"] == 0
    assert report["reason"] == "requested window is already complete"


def test_market_hours_block_quote_update_but_allow_historical_sync(tmp_path, monkeypatch):
    store(tmp_path)
    market_hours = datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("aspool.sqlite_update_cli._sync_candidates", lambda *args: [])
    report = run_update(tmp_path, mode="sync", now=market_hours, retry_delay=0)
    assert report["status"] == "ok"
    with pytest.raises(ValueError, match="aspool update"):
        run_update(tmp_path, mode="update", now=market_hours, retry_delay=0)


def test_retry_is_bounded_and_validation_errors_are_not_retried(monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr("aspool.source_retry.time.sleep", waits.append)

    def fail():
        calls.append(1)
        raise OSError("failed")

    with pytest.raises(OSError):
        read_with_retry(fail, retries=2, delay=1)
    assert len(calls) == 3 and waits == [1, 2]

    def invalid():
        raise ValueError("bad data")

    with pytest.raises(ValueError):
        read_with_retry(invalid, retries=5)
    assert waits == [1, 2]


def test_unchanged_sync_input_bootstraps_missing_metrics_then_becomes_noop(tmp_path):
    days = store(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        payload = [dict(symbol="000001.SZ", trade_date=days[-1], volume=100)]
        first = apply_daily_changes(
            conn, bars=payload, market_sessions=days, fill_missing_metrics=True
        )
        assert first["changed_rows"] == 0 and first["changed_metric_rows"] == 1
        assert conn.execute(
            "SELECT vol_ratio,turnover_rate FROM daily_bars WHERE trade_date=?", (days[-1],)
        ).fetchone() == (1, None)
        before = list(conn.iterdump())
        second = apply_daily_changes(
            conn, bars=payload, market_sessions=days, fill_missing_metrics=True
        )
        assert second["changed_metric_rows"] == 0
        assert list(conn.iterdump()) == before


def test_baostock_read_failure_reconnects_and_never_calls_quotes(tmp_path, monkeypatch):
    from aspool.sqlite_update_cli import BaoSource

    calls, events = [], []

    class Provider:
        def __enter__(self):
            calls.append("open")
            return self

        def __exit__(self, *args):
            calls.append("close")

        def get_daily(self, *args, **kwargs):
            calls.append("read")
            if calls.count("read") < 3:
                raise OSError("connection reset")
            return pd.DataFrame([{"value": 1}])

    session = SourceSession(Provider, retries=2, delay=0, events=events)
    assert not BaoSource(session).get_daily(0, "000001").empty
    session.close()
    assert calls.count("open") == calls.count("close") == 3
    assert len(events) == 2


def test_update_rejects_history_and_sync_infers_recent_window(tmp_path, monkeypatch):
    store(tmp_path)
    with pytest.raises(ValueError, match="historical K lines"):
        run_update(tmp_path, now=NOW, start="2026-09-23", end="2026-09-24")

    class EmptyKline(FakeMac):
        def get_stock_kline(self, *args, **kwargs):
            return pd.DataFrame()

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: EmptyKline())
    with pytest.raises(Exception, match="Empty TDX K-line response"):
        run_update(tmp_path, mode="sync", now=NOW, retry_delay=0)


def test_login_failure_retries_without_exiting_unentered_context():
    calls, events = [], []

    class Login:
        def __enter__(self):
            calls.append("enter")
            if len(calls) == 1:
                from tdxman.baostock import BaostockError

                raise BaostockError("login failed")
            return self

        def __exit__(self, *args):
            calls.append("exit")

    session = SourceSession(Login, retries=2, delay=0, events=events)
    assert session.read(lambda client: 42, label="login") == 42
    session.close()
    assert calls == ["enter", "enter", "exit"] and len(events) == 1


def test_parallel_quote_groups_have_separate_connections():
    opened, closed = [], []

    class Connection(FakeMac):
        def __enter__(self):
            opened.append(self)
            return self

        def __exit__(self, *args):
            closed.append(self)

    session = SourceSession(Connection, retries=2, delay=0, events=[])
    source = QuoteSource(session, DAY, workers=2)
    source.fetch([f"{i:06d}.SZ" for i in range(1, 82)])
    assert len(source.rows) == 81 and not source.errors
    assert len(opened) == 2 and opened[0] is not opened[1]
    assert set(opened) == set(closed)


def test_directory_st_changes_without_fabricating_no_trade_bars(tmp_path, monkeypatch):
    store(tmp_path)
    current_name = ["*ST测试"]

    class NoTrade(FakeMac):
        def get_stock_quotes_list(self, category, **kwargs):
            frame = super().get_stock_quotes_list(category, **kwargs)
            frame["name"] = current_name[0]
            return frame

        def get_stock_quotes(self, stocks, fields):
            if stocks == [(1, "000001")]:
                return super().get_stock_quotes(stocks, fields)
            return pd.DataFrame(
                [
                    {**quote(code), "open": 0, "high": 0, "low": 0, "vol": 0, "amount": 0}
                    for market, code in stocks
                ]
            )

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: NoTrade())
    for name, st in [("*ST测试", 1), ("测试股份", 0)]:
        current_name[0] = name
        report = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
        assert report["status"] == "ok"
        assert report["factor_quality"]["unavailable"] == 1
        assert report["no_trade"] == ["000001.SZ"]
        assert not report["retries"]
        with stock_connection(tmp_path) as conn:
            assert conn.execute("SELECT count(*) FROM daily_bars").fetchone()[0] == 6
            assert conn.execute(
                "SELECT calc_status,source_is_st,source_is_st_source,trading_status "
                "FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone() == ("NO_TRADE", st, "tdxman:directory", "NO_TRADE")
    repeated = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert repeated["success"][0]["changed_feature_rows"] == 0


def test_directory_new_listing_and_future_listing_filter(tmp_path, monkeypatch):
    from tdxman.client import TdxClient
    from tdxman.mac.enums import Category

    store(tmp_path)

    class NewDirectory(FakeMac):
        def get_stock_quotes_list(self, category, **kwargs):
            frame = super().get_stock_quotes_list(category, **kwargs)
            if category == Category.SZ:
                frame = pd.concat(
                    [
                        frame,
                        pd.DataFrame(
                            [
                                dict(market=0, code="301686", name="新股"),
                                dict(market=0, code="301999", name="未来上市"),
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
            return frame

    class Finance(FakeMac):
        def get_finance_info(self, market, code):
            return pd.DataFrame(
                [dict(code=code, ipo_date=20260922 if code == "301686" else 20261008)]
            )

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: NewDirectory())
    monkeypatch.setattr(TdxClient, "from_best_host", lambda **kwargs: Finance())
    report = run_update(tmp_path, now=NOW, retry_delay=0)
    assert report["status"] == "ok"
    assert not report["factor_quality"]["ready"]
    assert report["not_listed"] == ["301999.SZ"]
    with stock_connection(tmp_path) as conn:
        assert conn.execute(
            "SELECT limit_status FROM daily_features WHERE symbol='301686.SZ'"
        ).fetchone() == ("NO_LIMIT",)
        assert (
            conn.execute("SELECT count(*) FROM daily_bars WHERE symbol='301999.SZ'").fetchone()[0]
            == 0
        )


def test_failed_directory_market_does_not_publish_partial_pages():
    from aspool.sqlite_directory import read_directory
    from tdxman.mac.enums import Category

    class BrokenDirectory(FakeMac):
        def get_stock_quotes_list(self, category, **kwargs):
            if category == Category.SZ:
                if kwargs["start"]:
                    raise OSError("lost next page")
                return pd.DataFrame(
                    [dict(market=0, code=f"{i:06d}", name="股份") for i in range(80)]
                )
            return super().get_stock_quotes_list(category, **kwargs)

    events, report = [], {}
    session = SourceSession(BrokenDirectory, retries=2, delay=0, events=events)
    try:
        names = read_directory(session, report=report)
    finally:
        session.close()
    assert set(names) == {"600001.SH", "920011.BJ"}
    assert report["failed"][0]["market"] == "SZ" and len(events) == 2


def test_status_etf_factors_fallback_survives_missing_table(tmp_path):
    """Pre-layered ETF fallback must not crash when adjustment_factors is absent."""
    import sqlite3

    import duckdb

    with duckdb.connect(str(tmp_path / "catalog.duckdb")) as conn:
        conn.execute(
            "CREATE TABLE securities(symbol VARCHAR, asset_type VARCHAR, "
            "listing_date DATE, delisting_date DATE, active BOOLEAN)"
        )
        conn.execute("CREATE TABLE security_calendar(trade_date DATE, is_open BOOLEAN)")
        conn.execute("INSERT INTO security_calendar VALUES ('2026-09-29', true)")
    with sqlite3.connect(tmp_path / "etfs.sqlite") as conn:
        conn.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT)")
        conn.execute("INSERT INTO daily_bars VALUES ('510000.SH', '2026-09-29')")

    from aspool.cli import _dataset_status

    rows = _dataset_status(tmp_path)
    assert "etf-factors" not in {row["dataset"] for row in rows}
