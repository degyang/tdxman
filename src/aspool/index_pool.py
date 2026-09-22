"""Separate daily index pool with longest available history and breadth counts."""

import asyncio
import math
import struct
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from tdxman.client import AsyncTdxClient, TdxClient
from tdxman.config import get_known_hosts
from tdxman.exceptions import TdxError
from tdxman.models.enums import KlineCategory, Market
from tdxman.offline.daily_bar import find_daily_bar_file

from .fetch import client_factory, fetch_async, fetch_sync
from .index_lists import atomic_json, load_indices
from .pool import writer
from .store import catalog

SCHEMA = pa.schema(
    [
        ("market", pa.string()),
        ("code", pa.string()),
        ("name", pa.string()),
        ("trade_date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("amount", pa.float64()),
        ("up_count", pa.int64()),
        ("down_count", pa.int64()),
    ]
)


def normalize(records, item):
    rows = {}
    for record in records:
        raw = record.get("date", record.get("trade_date"))
        day = date.fromisoformat(str(raw)[:10])
        row = {k: item[k] for k in ("market", "code", "name")}
        row["trade_date"] = day
        for field in ("open", "high", "low", "close", "amount"):
            row[field] = float(record[field])
        row["volume"] = float(record.get("vol", record.get("volume")))
        for field in ("up_count", "down_count"):
            value = record.get(field, 0)
            row[field] = 0 if value is None or not math.isfinite(float(value)) else int(value)
            if row[field] < 0 or (
                value is not None and math.isfinite(float(value)) and float(value) != row[field]
            ):
                raise ValueError(f"{day}: invalid {field}")
        for field in ("open", "high", "low", "close", "volume", "amount"):
            if not math.isfinite(row[field]) or row[field] < 0:
                raise ValueError(f"{day}: invalid {field}")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(
            row["open"], row["close"]
        ):
            raise ValueError(f"{day}: invalid OHLC")
        if day in rows:
            raise ValueError(f"{day}: duplicate date")
        rows[day] = row
    return [rows[k] for k in sorted(rows)]


def offline_records(item):
    path = find_daily_bar_file(Market[item["market"]], item["code"])
    data = path.read_bytes()
    if not data or len(data) % 32:
        raise ValueError(f"{path.name}: empty or truncated day file")
    records = []
    for ymd, op, hi, lo, cl, amount, vol, up, down in struct.iter_unpack("<IIIIIfIHH", data):
        records.append(
            {
                "date": f"{ymd // 10000:04}-{ymd // 100 % 100:02}-{ymd % 100:02}",
                "open": op / 100,
                "high": hi / 100,
                "low": lo / 100,
                "close": cl / 100,
                "amount": amount,
                "vol": vol,
                "up_count": up,
                "down_count": down,
            }
        )
    return records


def index_path(root, item):
    return (
        Path(root)
        / "lake/indices/daily"
        / f"market={item['market']}"
        / f"symbol={item['code']}"
        / "bars.parquet"
    )


def overlap_start(root, item):
    path = index_path(root, item)
    if not path.exists():
        return None
    days = sorted(set(pq.ParquetFile(path).read(columns=["trade_date"])["trade_date"].to_pylist()))
    return days[max(0, len(days) - 5)] if days else None


async def online_records(client, item, asynchronous=False, since=None):
    records = []
    oldest = None
    offset = 0
    page_size = 30 if since is not None else 800
    while offset < 64000:
        if asynchronous:
            try:
                frame = await client.get_index_bars(
                    Market[item["market"]], item["code"], KlineCategory.DAY, offset, page_size
                )
            except (TdxError, OSError, ValueError) as original:
                last_error = original
                for host in get_known_hosts():
                    try:
                        async with AsyncTdxClient(host, timeout=5, heartbeat_interval=0) as other:
                            frame = await other.get_index_bars(
                                Market[item["market"]],
                                item["code"],
                                KlineCategory.DAY,
                                offset,
                                page_size,
                            )
                        break
                    except (TdxError, OSError, ValueError) as exc:
                        last_error = exc
                else:
                    raise last_error
        else:
            try:
                frame = client.get_index_bars(
                    Market[item["market"]], item["code"], KlineCategory.DAY, offset, page_size
                )
            except (TdxError, OSError, ValueError) as original:
                last_error = original
                for host in get_known_hosts():
                    try:
                        with TdxClient(host, timeout=5, heartbeat_interval=0) as other:
                            frame = other.get_index_bars(
                                Market[item["market"]],
                                item["code"],
                                KlineCategory.DAY,
                                offset,
                                page_size,
                            )
                        break
                    except (TdxError, OSError, ValueError) as exc:
                        last_error = exc
                else:
                    raise last_error
        if frame.empty:
            return records
        page = frame.to_dict("records")
        minimum = min(str(row["date"])[:10] for row in page)
        if oldest is not None and minimum >= oldest:
            raise ValueError("指数分页未向更早日期推进")
        oldest = minimum
        records.extend(
            row for row in page if since is None or str(row["date"])[:10] >= since.isoformat()
        )
        if since is not None and minimum <= since.isoformat():
            return records
        offset += len(page)
        # Ask for the next page even if this server returned fewer than requested.
    raise ValueError("指数历史超过分页上限，未标为完整")


def save_index(root, item, records, mode, coverage=None):
    rejected = []
    accepted = []
    for record in records:
        try:
            accepted.extend(normalize([record], item))
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append(
                {"date": str(record.get("date", record.get("trade_date"))), "error": str(exc)}
            )
    incoming = normalize(accepted, item)
    if not incoming:
        raise ValueError("数据源无日线记录")
    path = index_path(root, item)
    table = pq.ParquetFile(path).read() if path.exists() else None
    days = table["trade_date"].to_pylist() if table is not None else []
    if days != sorted(set(days)):
        raise ValueError("已存指数日期重复或无序")
    # Incremental requests overlap recent bars. Keep older history in Arrow,
    # rather than expanding every historical index row into Python objects.
    start = min(row["trade_date"] for row in incoming)
    offset = max(0, next((i for i, day in enumerate(days) if day >= start), len(days)))
    prior = table.slice(offset).to_pylist() if table is not None else []
    before = {row["trade_date"]: row for row in prior}
    added = changed = unchanged = 0
    for row in incoming:
        day = row["trade_date"]
        if day not in before:
            added += 1
        elif row != before[day]:
            changed += 1
        else:
            unchanged += 1
        before[day] = row
    rows = [before[k] for k in sorted(before)]
    result = (
        pa.concat_tables(
            [table.slice(0, offset), pa.Table.from_pylist(rows, schema=SCHEMA)],
            promote_options="permissive",
        )
        if table is not None and offset
        else pa.Table.from_pylist(rows, schema=SCHEMA)
    )
    if added or changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        pq.write_table(result, tmp, compression="zstd")
        tmp.replace(path)
    all_rows = len(result)
    start_day, end_day = result["trade_date"][0].as_py(), result["trade_date"][-1].as_py()
    coverage_entry = (
        item["market"],
        item["code"],
        item["name"],
        start_day,
        end_day,
        all_rows,
        mode,
        datetime.now(),
    )
    if coverage is None:
        with catalog(root) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO index_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                coverage_entry,
            )
    else:
        coverage.append(coverage_entry)
    return {
        "market": item["market"],
        "code": item["code"],
        "name": item["name"],
        "fetched": len(records),
        "accepted": len(incoming),
        "rejected": rejected,
        "added": added,
        "changed": changed,
        "unchanged": unchanged,
        "rows": all_rows,
        "start": str(start_day),
        "end": str(end_day),
        "zero_breadth_rows": sum(r["up_count"] == r["down_count"] == 0 for r in rows),
    }


def _ensure_index_catalog(root):
    with catalog(root) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS index_coverage (
            market VARCHAR, code VARCHAR, name VARCHAR, start_date DATE, end_date DATE,
            row_count BIGINT, source VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY (market, code))""")


@writer
def _publish_indices(root, pending, mode):
    coverage = []
    results, failures = [], []
    for item, since, records in pending:
        try:
            if mode == "offline" and since is not None:
                records = [row for row in records if str(row["date"])[:10] >= since.isoformat()]
            result = save_index(root, item, records, mode, coverage)
            result["sync_scope"] = "bootstrap" if since is None else "incremental"
            result["overlap_start"] = str(since) if since is not None else None
            results.append(result)
        except Exception as exc:
            failures.append({**item, "error": f"{type(exc).__name__}: {exc}"})
    if coverage:
        with catalog(root) as conn:
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO index_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    coverage,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    return results, failures


def sync_indices(root, mode="online", asynchronous=False, limit=None, items=None, workers=1):
    started = perf_counter()
    root = Path(root)
    items = load_indices() if items is None else items
    items = items[:limit] if limit else items
    _ensure_index_catalog(root)
    report = {
        "run_id": uuid4().hex,
        "mode": mode,
        "requested": len(items),
        "started_at": datetime.now().isoformat(),
        "success": [],
        "failed": [],
    }

    jobs = []
    for item in items:
        try:
            jobs.append((item, overlap_start(root, item)))
        except Exception as exc:
            report["failed"].append({**item, "error": str(exc)})
    report.update(workers=workers, asynchronous=asynchronous, write_seconds=0.0)

    pending = []

    def consume(entry):
        (item, since), records, error, _ = entry
        if error:
            report["failed"].append({**item, "error": f"{type(error).__name__}: {error}"})
            return
        pending.append((item, since, records))

    def request(client, job):
        item, since = job
        return asyncio.run(online_records(client, item, since=since))

    async def async_request(client, job):
        item, since = job
        return await online_records(client, item, True, since=since)

    async def run_async():
        factory, retry = client_factory(AsyncTdxClient, workers)
        async for entry in fetch_async(jobs, factory, async_request, workers, retry):
            consume(entry)

    if mode == "offline":
        for job in jobs:
            try:
                entry = job, offline_records(job[0]), None, 0
            except Exception as exc:
                entry = job, None, exc, 0
            consume(entry)
    elif jobs:
        if asynchronous:
            asyncio.run(run_async())
        else:
            factory, retry = client_factory(TdxClient, workers)
            for entry in fetch_sync(jobs, factory, request, workers, retry):
                consume(entry)
    tick = perf_counter()
    results, failures = _publish_indices(root, pending, mode)
    report["write_seconds"] = perf_counter() - tick
    report["success"].extend(results)
    report["failed"].extend(failures)
    report["success"].sort(key=lambda r: (r["market"], r["code"]))
    report["total_seconds"] = perf_counter() - started
    report["finished_at"] = datetime.now().isoformat()
    path = root / "reports/index-sync" / f"{report['run_id']}.json"
    atomic_json(path, report)
    return report, path
