"""ETF sync keeps the existing CLI and MAC daily-bar units."""

import asyncio
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb

from tdxman.codec.bitmap import FieldBit, PresetField
from tdxman.mac.client import AsyncMacClient, MacClient
from tdxman.models.enums import Market

from .etf_lists import collect, load_etfs
from .fetch import client_factory, fetch_async, fetch_sync
from .index_lists import atomic_json
from .pool import pool_lock
from .source_retry import EmptySourceResponse
from .sqlite_etf_store import connection, save_rows
from .sqlite_update_cli import SourceSession
from .tdx_online import _stock_records, _stock_records_async


def online_items(*, retries, retry_delay, events):
    session = SourceSession(
        lambda: MacClient.from_best_host(heartbeat_interval=0, auto_reconnect=False, timeout=10),
        retries=retries,
        delay=retry_delay,
        events=events,
    )
    try:
        return session.read(lambda client: collect(client)[0]["etfs"], label="etf:directory")
    finally:
        session.close()


def publish_directory(root, items, *, observed_at=None, source="tdx:etf-list"):
    """Publish a complete ETF observation to catalog.securities."""
    observed_at = observed_at or datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    normalized = []
    seen = set()
    for item in items:
        code = str(item.get("code", ""))
        market = item.get("market")
        name = item.get("name")
        if (
            len(code) != 6
            or not code.isascii()
            or not code.isdigit()
            or market not in {"SH", "SZ"}
            or not isinstance(name, str)
            or not name.strip()
            or code in seen
        ):
            raise ValueError("Invalid or duplicate ETF directory entry")
        seen.add(code)
        normalized.append(dict(symbol=f"{code}.{market}", code=code, market=market,
                               name=name.strip()))
    if not normalized:
        raise EmptySourceResponse("Empty ETF directory")

    from .securities import publish_directory as publish

    return publish(
        Path(root).resolve(),
        normalized,
        asset_type="etf",
        complete_markets={"SH", "SZ"},
        observed_at=observed_at,
    )


def no_trade_quote(frame, item, expected):
    for q in frame.to_dict("records"):
        if str(q.get("code")) != item["code"] or q.get("market") != (
            1 if item["market"] == "SH" else 0
        ):
            continue
        if str(q.get("server_update_date")) != expected.replace("-", ""):
            continue
        if all(q.get(k) == 0 for k in ("open", "high", "low", "vol", "amount")):
            return dict(
                **item,
                date=expected,
                status="NO_TRADE",
                evidence_source="tdx:dated_quote",
                reason="No current K line and dated quote has zero trading activity",
            )
    raise EmptySourceResponse("No ETF K lines and no dated no-trade quote")


def sync_etfs(
    root,
    *,
    mode="online",
    asynchronous=False,
    limit=None,
    workers=4,
    retries=2,
    retry_delay=1,
    items=None,
):
    if mode not in ("online", "offline") or not 1 <= workers <= 8:
        raise ValueError("Invalid ETF sync mode/workers")
    if not 0 <= retries <= 5 or not 0 <= retry_delay <= 30:
        raise ValueError("Invalid retry policy")
    root = Path(root).resolve()
    directory_retries = []
    if items is None:
        items = (
            online_items(retries=retries, retry_delay=retry_delay, events=directory_retries)
            if mode == "online"
            else load_etfs()
        )
    directory = publish_directory(
        root,
        items,
        source="tdx:etf-list" if mode == "online" else "tdx:etf-reviewed-list",
    )
    items = items[:limit] if limit else items
    expected = None
    if (root / "catalog.duckdb").exists():
        with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as c:
            if "security_calendar" in {r[0] for r in c.execute("SHOW TABLES").fetchall()}:
                expected = c.execute(
                    "SELECT max(trade_date) FROM security_calendar WHERE is_open AND trade_date<=?",
                    [datetime.now(ZoneInfo("Asia/Shanghai")).date()],
                ).fetchone()[0]
    report = dict(
        run_id=uuid4().hex,
        backend="sqlite",
        status="running",
        requested=len(items),
        expected_end=str(expected) if expected else None,
        success=[],
        failed=[],
        stale=[],
        retries=directory_retries,
        no_trade=[],
        directory=directory,
        started_at=datetime.now().isoformat(),
    )
    path = root.parent / ".local/reports/etf-sync" / (report["run_id"] + ".json")
    atomic_json(path, report)
    started = perf_counter()
    with connection(root, read_only=False) as conn:
        jobs = []
        for item in items:
            days = [
                r[0]
                for r in conn.execute(
                    "SELECT trade_date FROM daily_bars WHERE symbol=? "
                    "ORDER BY trade_date DESC LIMIT 5",
                    (f"{item['code']}.{item['market']}",),
                )
            ]
            jobs.append((item, date.fromisoformat(min(days)) if days else None))

        def consume(entry):
            (item, since), rows, error, _ = entry
            if error:
                report["failed"].append(dict(**item, error=str(error)))
            elif isinstance(rows, dict) and "no_trade" in rows:
                report["no_trade"].append(rows["no_trade"])
            else:
                try:
                    with pool_lock(root, write=True):
                        result = save_rows(conn, item, rows, source="tdxman:etf:" + mode)
                    report["success"].append(result)
                except ValueError as exc:
                    report["failed"].append(dict(**item, error=str(exc)))
            if (len(report["success"]) + len(report["failed"]) + len(report["no_trade"])) % 50 == 0:
                atomic_json(path, report)

        @contextmanager
        def factory():
            session = SourceSession(
                lambda: MacClient.from_best_host(
                    heartbeat_interval=0, auto_reconnect=False, timeout=10
                ),
                retries=retries,
                delay=retry_delay,
                events=report["retries"],
            )
            try:
                yield session
            finally:
                session.close()

        def read(client, job):
            item, since = job
            rows = _stock_records(client, (item["code"], since, "etf"))
            if not rows:
                frame = client.get_stock_quotes(
                    [(Market[item["market"]], item["code"])],
                    PresetField.COMMON + FieldBit.SERVER_UPDATE_DATE,
                )
                return {
                    "no_trade": no_trade_quote(
                        frame, item, str(expected or datetime.now(ZoneInfo("Asia/Shanghai")).date())
                    )
                }
            return rows

        def request(session, job):
            return session.read(lambda client: read(client, job), label="etf:" + job[0]["code"])

        async def run_async():
            make, retry = client_factory(AsyncMacClient, workers)

            async def read_async(client, job):
                item, since = job
                rows = await _stock_records_async(client, (item["code"], since, "etf"))
                if not rows:
                    frame = await client.get_stock_quotes(
                        [(Market[item["market"]], item["code"])],
                        PresetField.COMMON + FieldBit.SERVER_UPDATE_DATE,
                    )
                    return {
                        "no_trade": no_trade_quote(
                            frame,
                            item,
                            str(expected or datetime.now(ZoneInfo("Asia/Shanghai")).date()),
                        )
                    }
                return rows

            async for entry in fetch_async(
                jobs,
                make,
                read_async,
                workers,
                retry,
                max_retries=retries,
                retry_backoff=retry_delay,
            ):
                consume(entry)

        try:
            if mode == "offline":
                from tdxman.offline import find_daily_bar_file, read_daily_bars

                for job in jobs:
                    item, since = job
                    try:
                        bars = read_daily_bars(
                            find_daily_bar_file(Market[item["market"]], item["code"])
                        )
                        rows = [
                            dict(
                                trade_date=date(b.year, b.month, b.day),
                                open=b.open,
                                high=b.high,
                                low=b.low,
                                close=b.close,
                                volume=b.vol * 100,
                                amount=b.amount,
                            )
                            for b in bars
                            if date(b.year, b.month, b.day) >= (since or date(2010, 1, 1))
                        ]
                        consume((job, rows, None, 0))
                    except (OSError, ValueError) as exc:
                        consume((job, None, exc, 0))
            elif asynchronous:
                asyncio.run(run_async())
            else:
                for entry in fetch_sync(jobs, factory, request, workers):
                    consume(entry)
            report["stale"] = [
                r for r in report["success"] if expected and r["source_end"] < str(expected)
            ]
            report["status"] = "partial" if report["failed"] or report["stale"] else "ok"
        except BaseException as exc:
            report.update(status="failed", error=str(exc))
            raise
        finally:
            report.update(seconds=perf_counter() - started, finished_at=datetime.now().isoformat())
            atomic_json(path, report)
    return report, path
