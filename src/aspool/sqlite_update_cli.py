"""Quote-only daily update orchestration for the four-table stock store."""

import math
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from tdxman.codec.bitmap import FieldBit, PresetField
from tdxman.exceptions import TdxError
from tdxman.models.enums import Market

from .fundamentals import _quote_bar, _quote_date, quote_update_allowed
from .source_retry import EmptySourceResponse, read_with_retry
from .sqlite_daily_sync import (
    fetch_tdx_action_interval,
    refresh_source_calendar,
    sync_daily_source,
)
from .sqlite_stock_store import stock_connection

_HOST_SELECTION_LOCK = Lock()


class SourceSession:
    """Reconnect after failed reads; never put a database transaction in this retry loop."""

    def __init__(self, factory, *, retries, delay, events):
        self.factory, self.retries, self.delay = factory, retries, delay
        self.events = events
        self.manager = self.client = None

    def close(self):
        manager, self.manager, self.client = self.manager, None, None
        if manager is not None:
            with suppress(OSError, TdxError):
                manager.__exit__(None, None, None)

    def read(self, operation, *, label):
        def attempt():
            if self.client is None:
                # Host selection persists a shared config file. Serialize that
                # short step; the worker connections and reads remain independent.
                with _HOST_SELECTION_LOCK:
                    manager = self.factory()
                try:
                    client = manager.__enter__()
                except Exception:
                    # Failed __enter__ is not followed by __exit__; BaoStock
                    # already releases its global session lock on login failure.
                    if hasattr(manager, "close"):
                        with suppress(OSError, TdxError):
                            manager.close()
                    raise
                self.manager, self.client = manager, client
            return operation(self.client)

        def retry(number, error):
            self.events.append(dict(operation=label, retry=number, error=str(error)))
            self.close()

        try:
            return read_with_retry(attempt, retries=self.retries, delay=self.delay, on_retry=retry)
        except Exception:
            self.close()
            raise


class QuoteSource:
    """Fetch at most 80 quotes per request; retry only missing keys in partial replies."""

    def __init__(self, session, day, workers=1, names=None):
        self.session, self.day = session, day
        self.workers = workers
        self.rows, self.errors = {}, {}
        self.names = names or {}

    def fetch(self, symbols):
        if self.workers > 1 and len(symbols) > 80:

            def fetch_part(part):
                worker_session = SourceSession(
                    self.session.factory,
                    retries=self.session.retries,
                    delay=self.session.delay,
                    events=self.session.events,
                )
                worker = QuoteSource(worker_session, self.day)
                try:
                    worker.fetch(part)
                    return worker.rows, worker.errors
                finally:
                    worker_session.close()

            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                for rows, errors in executor.map(
                    fetch_part, [symbols[i : i + 80] for i in range(0, len(symbols), 80)]
                ):
                    self.rows.update(rows)
                    self.errors.update(errors)
            return
        fields = PresetField.COMMON + FieldBit.SERVER_UPDATE_DATE + FieldBit.SERVER_UPDATE_TIME
        for offset in range(0, len(symbols), 80):
            pending = set(symbols[offset : offset + 80])

            def request(client):
                keys = sorted(pending)
                frame = client.get_stock_quotes(
                    [(Market[s.split(".")[1]], s.split(".")[0]) for s in keys], fields
                )
                by_code = {s.split(".")[0]: s for s in keys}
                seen = set()
                for quote in frame.to_dict("records"):
                    code = str(quote.get("code", ""))
                    if code not in by_code or code in seen:
                        raise ValueError("Quote response contains an unexpected or duplicate code")
                    seen.add(code)
                    # Stale/missing dates are retried, never assigned today's date.
                    if _quote_date(quote.get("server_update_date")) != self.day:
                        continue
                    symbol = by_code[code]
                    row = _quote_bar(quote, self.day)
                    numeric = [
                        row.get(k) for k in ("open", "high", "low", "close", "volume", "amount")
                    ]
                    if any(v is None or not math.isfinite(float(v)) for v in numeric):
                        continue
                    if not all(float(v) >= 0 for v in numeric):
                        self.errors[symbol] = "Negative quote OHLCV"
                        pending.remove(symbol)
                        continue
                    no_trade = not row["volume"] and not row["amount"]
                    if not no_trade and (
                        not 0
                        < row["low"]
                        <= min(row["open"], row["close"])
                        <= max(row["open"], row["close"])
                        <= row["high"]
                    ):
                        self.errors[symbol] = "Invalid quote OHLC"
                        pending.remove(symbol)
                        continue
                    row.update(
                        symbol=symbol,
                        date=self.day,
                        name_as_of=self.day.isoformat(),
                        turnover_rate=quote.get("turnover"),
                        trading_status="NO_TRADE" if no_trade else "TRADING",
                    )
                    self.rows[symbol] = row
                    pending.remove(symbol)
                if pending:
                    raise EmptySourceResponse(
                        "Missing/current-date quotes: " + ",".join(sorted(pending))
                    )

            try:
                self.session.read(request, label="quotes:" + str(offset))
            except (OSError, ValueError) as exc:
                for symbol in pending:
                    self.errors[symbol] = str(exc)
            except TdxError as exc:
                for symbol in pending:
                    self.errors[symbol] = str(exc)

    def get_daily(self, market, code, *, start, end, count):
        symbol = f"{code}.{Market(market).name}"
        if symbol in self.errors and symbol not in self.names:
            raise EmptySourceResponse(self.errors[symbol])
        row = self.rows.get(symbol)
        if symbol in self.names:
            from .st_source import classify_st_name

            row = dict(row) if row else dict(symbol=symbol, date=self.day, trading_status="MISSING")
            row.update(
                name=self.names[symbol],
                name_source="tdxman:directory",
                name_as_of=self.day.isoformat(),
                is_st=classify_st_name(self.names[symbol]),
                is_st_source="tdxman:directory",
            )
        return pd.DataFrame([row] if row and start <= self.day <= end else [])


class BaoSource:
    def __init__(self, session):
        self.session = session

    def get_daily(self, *args, **kwargs):
        def request(client):
            frame = client.get_daily(*args, **kwargs)
            if frame.empty:
                raise EmptySourceResponse("Empty BaoStock daily response")
            return frame

        try:
            return self.session.read(request, label="baostock:" + str(args[1]))
        except EmptySourceResponse:
            return pd.DataFrame()

    def get_trade_calendar(self, start, end):
        def request(client):
            frame = client.get_trade_calendar(start, end)
            if len(frame) != (end - start).days + 1:
                raise EmptySourceResponse("Incomplete BaoStock calendar")
            return frame

        return self.session.read(request, label="calendar")


class KlineSource:
    """Bounded raw historical K-line reads; never use current quote names for history."""

    def __init__(self, session):
        self.session = session

    def get_daily(self, market, code, *, start, end, count):
        from tdxman.mac.enums import Adjust, Period

        def request(client):
            rows = {}
            previous_oldest = None
            for offset in range(0, 1024, 128):
                frame = client.get_stock_kline(
                    market,
                    code,
                    Period.DAILY,
                    start=offset,
                    count=128,
                    adjust=Adjust.NONE,
                )
                if frame.empty:
                    if offset == 0:
                        raise EmptySourceResponse("Empty TDX K-line response")
                    break
                days = []
                for raw in frame.to_dict("records"):
                    day = pd.Timestamp(raw["datetime"]).date()
                    days.append(day)
                    if not start <= day <= end:
                        continue
                    row = dict(symbol=f"{code}.{Market(market).name}", date=day)
                    for field in ("open", "high", "low", "close", "amount"):
                        if pd.notna(raw.get(field)):
                            row[field] = raw[field]
                    # MAC K-line volume is shares; quote volume is lots.
                    row["volume"] = raw["vol"]
                    floating = raw.get("float_shares")
                    if floating is not None and pd.notna(floating) and floating > 0:
                        row.update(
                            float_share=float(floating) * 10000, float_share_source="tdxman:kline"
                        )
                    rows[day] = row
                oldest = min(days)
                if previous_oldest is not None and oldest >= previous_oldest:
                    raise ValueError("K-line pagination did not advance")
                if oldest <= start or len(frame) < 128:
                    break
                previous_oldest = oldest
            else:
                raise ValueError("K-line read budget exceeded; use explicit offline maintenance")
            return pd.DataFrame([rows[d] for d in sorted(rows)])

        return self.session.read(request, label="kline:" + code)


def _quote_calendar(root, quotes, day):
    """A dated Shanghai index quote confirms only its own session, not missing dates."""
    quotes.fetch(["000001.SH"])
    if "000001.SH" not in quotes.rows:
        raise EmptySourceResponse("No current Shanghai index quote to confirm the trading session")
    with duckdb.connect(str(root / "catalog.duckdb")) as conn:
        existing = conn.execute(
            "SELECT is_open FROM security_calendar WHERE trade_date=?", [day]
        ).fetchone()
        if existing is not None and existing[0] is False:
            raise ValueError("Trading calendar conflict; explicit maintenance required")
        if existing is None or existing[0] is None:
            conn.execute(
                "INSERT INTO security_calendar(trade_date,is_open,source) "
                "VALUES (?,true,'tdxman:quote') "
                "ON CONFLICT(trade_date) DO UPDATE SET is_open=true,source=excluded.source",
                [day],
            )


def run_update(
    root,
    *,
    source="tdx",
    symbols=(),
    limit=None,
    start=None,
    end=None,
    retries=2,
    retry_delay=1.0,
    now=None,
    report=None,
    mode="update",
    workers=4,
    status_filter=None,
    max_consecutive_failures=3,
    directory_rows=None,
    count=10,
):
    """Use quotes for TDX update; historical K-line maintenance belongs to sync."""
    from tdxman.baostock import BaostockClient
    from tdxman.client import TdxClient
    from tdxman.mac.client import MacClient

    root = Path(root).expanduser().resolve()
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if source not in ("tdx", "baostock"):
        raise ValueError("Unsupported source")
    if mode not in ("update", "sync") or not 1 <= workers <= 8:
        raise ValueError("Invalid mode or workers")
    if not 0 <= retries <= 5 or not 0 <= retry_delay <= 30:
        raise ValueError("Invalid retry limits")
    if mode == "update" and not quote_update_allowed(now):
        raise ValueError("工作日 09:00 至 15:30 不允许运行 aspool update")
    if not (root / "stocks.sqlite").is_file():
        raise ValueError("aspool update requires the migrated stocks.sqlite; specify --root")
    if source == "tdx" and mode == "update" and (start is not None or end is not None):
        raise ValueError("TDX update uses today's quote; historical K lines belong to sync")
    if source == "baostock" and ((start is None) != (end is None)):
        raise ValueError("Specify both --start and --end")
    if status_filter not in (None, "missing", "invalid"):
        raise ValueError("Unsupported status filter")
    if max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures must be positive")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 60:
        raise ValueError("count must be between 1 and 60")
    if mode == "sync" and ((start is None) != (end is None)):
        raise ValueError("Specify both --start and --end")
    from .securities import ensure_securities

    ensure_securities(root)
    if mode == "sync" and start is None:
        with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as calendar:
            recent = [
                row[0]
                for row in calendar.execute(
                    "SELECT trade_date FROM security_calendar WHERE is_open AND trade_date<=? "
                    "ORDER BY trade_date DESC LIMIT ?", [day, count]
                ).fetchall()
            ]
        if not recent:
            raise ValueError("No trading sessions available for automatic sync")
        start, end = min(recent).isoformat(), max(recent).isoformat()
    if start and (
        date.fromisoformat(start) > date.fromisoformat(end) or date.fromisoformat(end) > day
    ):
        raise ValueError("Invalid source date window")
    if start and (date.fromisoformat(end) - date.fromisoformat(start)).days >= 370:
        raise ValueError("Source window must fit within 370 natural days")
    with stock_connection(root) as conn:
        selected = list(dict.fromkeys(symbols))
        if not selected and status_filter:
            where = (
                "trading_status='MISSING'" if status_filter == "missing"
                else "(trading_status='INVALID' OR calc_status='INVALID')"
            )
            selected = [
                row[0] for row in conn.execute(
                    f"SELECT DISTINCT symbol FROM daily_features WHERE {where} "
                    "AND trade_date BETWEEN ? AND ? ORDER BY symbol",
                    (start or day.isoformat(), end or day.isoformat()),
                ).fetchall()
            ]
        if not selected and not (source == "tdx" and mode == "update") and not status_filter:
            # Indexed key seeks avoid scanning all daily history to enumerate stocks.
            last = ""
            while True:
                row = conn.execute(
                    "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1", (last,)
                ).fetchone()
                if row is None:
                    break
                last = row[0]
                selected.append(last)
        if limit:
            selected = selected[:limit]
    if directory_rows is not None and not symbols and source == "tdx" and mode == "sync":
        selected = sorted(directory_rows)
        if limit:
            selected = selected[:limit]
    if any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) for s in selected):
        raise ValueError("Use canonical symbols such as 000001.SZ")
    if not selected and not (source == "tdx" and mode == "update"):
        raise ValueError("No existing stock symbols selected")
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
        lifecycle = {
            s: (a, b)
            for s, a, b in conn.execute(
                "SELECT symbol,listing_date,delisting_date FROM securities "
                "WHERE asset_type='stock'"
            ).fetchall()
        }
    target_day = date.fromisoformat(end) if end else day
    first_day = date.fromisoformat(start) if start else target_day
    selected = [
        s
        for s in selected
        if s not in lifecycle
        or (
            (lifecycle[s][0] is None or lifecycle[s][0] <= target_day)
            and (lifecycle[s][1] is None or lifecycle[s][1] > first_day)
        )
    ]
    gap_filtered = source == "tdx" and mode == "sync" and not symbols and not status_filter
    if gap_filtered:
        with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as calendar:
            requested_sessions = [
                row[0].isoformat()
                for row in calendar.execute(
                    "SELECT trade_date FROM security_calendar WHERE is_open "
                    "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                    [date.fromisoformat(start), date.fromisoformat(end)],
                ).fetchall()
            ]
        selected = _sync_candidates(root, selected, requested_sessions, lifecycle)
        if not selected:
            report = report if report is not None else {}
            report.update(
                source=source,
                requested=0,
                retries=[],
                success=[],
                empty=[],
                failed=[],
                unsupported=[],
                factor_unavailable=[],
                traded=[],
                no_trade=[],
                missing=[],
                invalid=[],
                aborted=False,
                remaining_block=[],
                status="ok",
                reason="requested window is already complete",
            )
            return report
    report = report if report is not None else {}
    report.update(source=source, requested=len(selected), retries=[])
    status_selected = set(selected) if status_filter else None
    with ExitStack() as stack:

        def session(factory):
            obj = SourceSession(
                factory, retries=retries, delay=retry_delay, events=report["retries"]
            )
            stack.callback(obj.close)
            return obj

        actions = session(
            lambda: TdxClient.from_best_host(heartbeat_interval=0, auto_reconnect=False, timeout=10)
        )
        if source == "tdx" and mode == "update":
            from .sqlite_directory import listing_metadata, publish_directory, read_directory

            mac = session(
                lambda: MacClient.from_best_host(
                    heartbeat_interval=0, auto_reconnect=False, timeout=10
                )
            )
            report["directory"] = {}
            if directory_rows is None:
                fresh_names = read_directory(mac, report=report["directory"])
                publish_directory(root, fresh_names, day=day)
            else:
                fresh_names = dict(directory_rows)
                report["directory"] = {
                    "markets": {
                        market: sum(symbol.endswith("." + market) for symbol in fresh_names)
                        for market in ("SH", "SZ", "BJ")
                    },
                    "failed": [],
                    "reused": True,
                }
            names = dict(fresh_names)
            if report["directory"]["failed"]:
                with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
                    for failure in report["directory"]["failed"]:
                        names.update(
                            {
                                f"{code}.{market}": name
                                for code, market, name in conn.execute(
                                    "SELECT code,market,name FROM securities WHERE market=? "
                                    "AND active AND asset_type='stock'",
                                    [failure["market"]],
                                ).fetchall()
                            }
                        )
            selected = sorted(
                set(symbols)
                if symbols
                else (status_selected.intersection(names) if status_selected is not None else names)
            )
            if limit:
                selected = selected[:limit]
            report["metadata"] = {}
            lifecycle = listing_metadata(
                root, selected, actions, day=day, report=report["metadata"]
            )
            report["not_listed"] = [
                s for s in selected if lifecycle.get(s, (None, None))[0] and lifecycle[s][0] > day
            ]
            report["delisted"] = [
                s for s in selected if lifecycle.get(s, (None, None))[1] and lifecycle[s][1] <= day
            ]
            selected = [
                s for s in selected if s not in set(report["not_listed"] + report["delisted"])
            ]
            report["requested"] = len(selected)
            quote = QuoteSource(
                mac,
                day,
                workers=workers,
                names=fresh_names,
            )
            _quote_calendar(root, quote, day)
            quote.fetch(selected)
            client = quote
            start = end = day.isoformat()
        elif source == "baostock":
            client = BaoSource(session(lambda: BaostockClient(timeout=10)))
            start = start or day.isoformat()
            end = end or day.isoformat()
            refresh_source_calendar(root, client=client, start=start, end=end)
        else:
            client = KlineSource(
                session(
                    lambda: MacClient.from_best_host(
                        heartbeat_interval=0,
                        auto_reconnect=False,
                        timeout=10,
                    )
                )
            )
            # Index K-line dates establish positive sessions only; no inferred holidays.
            index = client.get_daily(
                Market.SH,
                "000001",
                start=date.fromisoformat(start),
                end=date.fromisoformat(end),
                count=None,
            )
            if index.empty:
                raise EmptySourceResponse("No index sessions in the requested sync window")
            if len(index) > 60:
                raise ValueError("Expected at most 60 input sessions")
            with duckdb.connect(str(root / "catalog.duckdb")) as conn:
                conn.execute("BEGIN")
                for d in index["date"]:
                    old = conn.execute(
                        "SELECT is_open FROM security_calendar WHERE trade_date=?", [d]
                    ).fetchone()
                    if old is not None and old[0] is False:
                        raise ValueError("Index/calendar conflict; explicit maintenance required")
                    if old is None or old[0] is None:
                        conn.execute(
                            "INSERT INTO security_calendar(trade_date,is_open,source) "
                            "VALUES (?,true,'tdxman:kline') "
                            "ON CONFLICT(trade_date) DO UPDATE SET "
                            "is_open=true,source=excluded.source",
                            [d],
                        )
                conn.execute("COMMIT")
        with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
            sessions = [
                d.isoformat()
                for (d,) in conn.execute(
                    "SELECT trade_date FROM security_calendar WHERE is_open ORDER BY trade_date"
                ).fetchall()
            ]
        from .sqlite_directory import listing_ages

        event_start = (date.fromisoformat(start) - timedelta(days=14)).isoformat()
        ages = listing_ages(lifecycle, sessions, start=event_start, end=end)

        def action_fetcher(symbol, first, last):
            def fetch(connection):
                try:
                    return fetch_tdx_action_interval(connection, symbol, first, last)
                except ValueError as exc:
                    if str(exc).startswith("Empty finance response"):
                        raise EmptySourceResponse(str(exc)) from exc
                    raise

            return actions.read(
                fetch,
                label="actions:" + symbol,
            )

        result = sync_daily_source(
            root,
            client=client,
            symbols=selected,
            market_sessions=sessions,
            as_of=day.isoformat(),
            start=start,
            end=end,
            source=("tdxman:quote" if mode == "update" else "tdxman:kline")
            if source == "tdx"
            else "baostock",
            action_fetcher=action_fetcher,
            report=report,
            listed_days=ages,
            event_refresh_start=event_start if source == "tdx" and mode == "sync" else None,
            max_consecutive_failures=max_consecutive_failures,
        )
        report.update(result)
        if source == "tdx" and mode == "update":
            failed = {item["symbol"] for item in report["failed"]}
            report["failed"].extend(
                dict(symbol=s, error=quote.errors[s])
                for s in selected
                if s in quote.errors and s not in failed
            )
    if report.get("aborted"):
        report["status"] = "aborted_source_failure"
    elif any(report.get(key) for key in ("missing", "invalid", "failed", "empty")):
        report["status"] = "completed_with_missing"
    elif report.get("directory", {}).get("failed"):
        report["status"] = "completed_with_missing"
    else:
        report["status"] = "ok"
    return report


def _sync_candidates(root, symbols, sessions, lifecycle):
    """Select securities with a raw/terminal-state gap or incomplete factor tail."""
    if not sessions:
        return []
    candidates = []
    target = max(sessions)
    terminal = {"SUSPENDED", "NO_TRADE", "NOT_LISTED"}
    with stock_connection(root) as conn:
        for symbol in symbols:
            listed, delisted = lifecycle.get(symbol, (None, None))
            listed = str(listed)[:10] if listed is not None else None
            delisted = str(delisted)[:10] if delisted is not None else None
            expected = [
                day
                for day in sessions
                if (listed is None or listed <= day) and (delisted is None or delisted > day)
            ]
            if not expected:
                continue
            marks = ",".join("?" for _ in expected)
            rows = conn.execute(
                "SELECT f.trade_date,b.trade_date,f.trading_status,f.calc_status,f.limit_status "
                "FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date) "
                f"WHERE f.symbol=? AND f.trade_date IN ({marks})",
                [symbol, *expected],
            ).fetchall()
            observed = {row[0]: row[1:] for row in rows}
            gap = False
            for day in expected:
                state = observed.get(day)
                if state is None:
                    gap = True
                    break
                bar_date, trading_status, calc_status, limit_status = state
                if (
                    trading_status in {"MISSING", "INVALID"}
                    or calc_status == "INVALID"
                    or (bar_date is not None and limit_status is None)
                    or (bar_date is None and trading_status not in terminal)
                ):
                    gap = True
                    break
            factor_end = conn.execute(
                "SELECT max(valid_through) FROM corporate_actions "
                "WHERE symbol=? AND record_kind='factor'",
                (symbol,),
            ).fetchone()[0]
            if gap or factor_end is None or factor_end < target:
                candidates.append(symbol)
    return candidates
