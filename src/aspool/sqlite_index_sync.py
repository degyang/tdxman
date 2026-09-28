"""Incremental index sync through MAC K lines, including industry indices."""

import asyncio
import struct
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb

from tdxman.mac.client import AsyncMacClient, MacClient
from tdxman.mac.enums import Adjust, Period
from tdxman.models.enums import Market

from .fetch import client_factory, fetch_async, fetch_sync
from .index_lists import atomic_json, load_indices
from .pool import pool_lock
from .source_retry import EmptySourceResponse
from .sqlite_index_store import index_connection, last_dates, save_rows
from .sqlite_update_cli import SourceSession


def index_record(raw):
    # For an index, the final 4 bytes are two uint16 breadth counts, not shares.
    up, down = struct.unpack("<HH", struct.pack("<f", float(raw["float_shares"])))
    return dict(
        date=raw["datetime"].date().isoformat(),
        **{k: raw[k] for k in ("open", "high", "low", "close", "vol", "amount")},
        up_count=up,
        down_count=down,
    )


async def online_records(client, item, since, *, asynchronous=False):
    records, oldest, offset = [], None, 0
    size = 30 if since else 700
    while offset < 64000:
        operation = client.get_stock_kline(
            Market[item["market"]],
            item["code"],
            Period.DAILY,
            start=offset,
            count=min(size, 64000 - offset),
            adjust=Adjust.NONE,
        )
        frame = await operation if asynchronous else operation
        if frame.empty:
            if not records:
                raise EmptySourceResponse("No index K lines")
            return records
        page = [index_record(row) for row in frame.to_dict("records")]
        minimum = min(row["date"] for row in page)
        if oldest is not None and minimum >= oldest:
            raise ValueError("Index pagination did not move backwards")
        oldest = minimum
        records.extend(row for row in page if since is None or row["date"] >= since)
        if since is not None and minimum <= since:
            if not records:
                raise EmptySourceResponse("Index source older than the local overlap")
            return records
        offset += len(page)
    raise ValueError("Index source exceeds 64000-row paging budget")


def sync_indices(
    root,
    mode="online",
    asynchronous=False,
    limit=None,
    items=None,
    workers=1,
    retries=2,
    retry_delay=1,
):
    if mode not in ("online", "offline") or not 1 <= workers <= 8:
        raise ValueError("Invalid index sync mode/workers")
    if not 0 <= retries <= 5 or not 0 <= retry_delay <= 30:
        raise ValueError("Invalid index retry policy")
    root = Path(root).resolve()
    expected_end = None
    if (root / "catalog.duckdb").exists():
        with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as catalog:
            if "security_calendar" in {row[0] for row in catalog.execute("SHOW TABLES").fetchall()}:
                expected_end = catalog.execute(
                    "SELECT max(trade_date) FROM security_calendar WHERE is_open AND trade_date<=?",
                    [datetime.now(ZoneInfo("Asia/Shanghai")).date()],
                ).fetchone()[0]
    started = perf_counter()
    items = load_indices() if items is None else items
    items = items[:limit] if limit else items
    report = dict(
        run_id=uuid4().hex,
        backend="sqlite",
        mode=mode,
        requested=len(items),
        asynchronous=asynchronous,
        workers=workers,
        success=[],
        failed=[],
        retries=[],
        started_at=datetime.now().isoformat(),
        status="running",
    )
    report.update(expected_end=str(expected_end) if expected_end else None, stale=[])
    path = root.parent / ".local/reports/index-sync" / (report["run_id"] + ".json")
    atomic_json(path, report)
    with index_connection(root, read_only=False) as conn:
        jobs = [
            (
                item,
                min(days)
                if (days := last_dates(conn, f"{item['code']}.{item['market']}"))
                else None,
            )
            for item in items
        ]

        def consume(entry):
            (item, since), rows, error, _ = entry
            if error:
                report["failed"].append(dict(**item, error=str(error)))
                return
            if mode == "offline" and since:
                rows = [r for r in rows if str(r["date"])[:10] >= since]
            try:
                with pool_lock(root, write=True):
                    result = save_rows(conn, item, rows, source="tdxman:index:" + mode)
                result.update(
                    overlap_start=since, sync_scope="incremental" if since else "bootstrap"
                )
                report["success"].append(result)
                if item["market"] == "SH" and item["code"] == "000001":
                    days = sorted({str(row["date"])[:10] for row in rows})
                    with duckdb.connect(str(root / "catalog.duckdb")) as catalog:
                        catalog.execute(
                            "CREATE TABLE IF NOT EXISTS security_calendar(" 
                            "trade_date DATE PRIMARY KEY,is_open BOOLEAN,source VARCHAR)"
                        )
                        catalog.execute(
                            "ALTER TABLE security_calendar ADD COLUMN IF NOT EXISTS source VARCHAR"
                        )
                        catalog.execute("BEGIN")
                        try:
                            for day in days:
                                known = catalog.execute(
                                    "SELECT is_open FROM security_calendar WHERE trade_date=?",
                                    [day],
                                ).fetchone()
                                if known is not None and known[0] is False:
                                    raise ValueError("Index/calendar conflict requires maintenance")
                                catalog.execute(
                                    "INSERT INTO security_calendar "
                                    "VALUES (?,true,'tdxman:index:kline') "
                                    "ON CONFLICT(trade_date) DO UPDATE SET "
                                    "is_open=true,source=excluded.source",
                                    [day],
                                )
                            catalog.execute("COMMIT")
                        except BaseException:
                            catalog.execute("ROLLBACK")
                            raise
                    report["calendar_dates"] = len(days)
            except ValueError as exc:
                report["failed"].append(dict(**item, error=str(exc)))
            # Database/IO failures propagate; do not report them as source validation errors.
            if (len(report["success"]) + len(report["failed"])) % 25 == 0:
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

        def request(session, job):
            item, since = job
            return session.read(
                lambda c: asyncio.run(online_records(c, item, since)),
                label="index:" + item["market"] + item["code"],
            )

        async def run_async():
            make, retry = client_factory(AsyncMacClient, workers)

            async def read(client, job):
                return await online_records(client, *job, asynchronous=True)

            async for entry in fetch_async(
                jobs, make, read, workers, retry, max_retries=retries, retry_backoff=retry_delay
            ):
                consume(entry)

        try:
            if mode == "offline":
                from .index_pool import offline_records

                for job in jobs:
                    try:
                        rows = offline_records(job[0])
                    except (OSError, ValueError) as exc:
                        consume((job, None, exc, 0))
                    else:
                        consume((job, rows, None, 0))
            elif asynchronous:
                asyncio.run(run_async())
            else:
                for entry in fetch_sync(jobs, factory, request, workers):
                    consume(entry)
            if expected_end:
                report["stale"] = [
                    dict(market=r["market"], code=r["code"], source_end=r["source_end"])
                    for r in report["success"]
                    if r["source_end"] < str(expected_end)
                ]
            report["status"] = (
                "partial"
                if report["failed"]
                or report["stale"]
                or any(row["rejected"] for row in report["success"])
                else "ok"
            )
        except BaseException as exc:
            report.update(status="failed", error=str(exc))
            raise
        finally:
            report.update(
                total_seconds=perf_counter() - started, finished_at=datetime.now().isoformat()
            )
            report["success"].sort(key=lambda r: (r["market"], r["code"]))
            atomic_json(path, report)
    return report, path
