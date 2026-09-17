"""Separate daily index pool with longest available history and breadth counts."""

import asyncio
import math
import struct
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from tdxman.client import AsyncTdxClient, TdxClient
from tdxman.config import get_known_hosts
from tdxman.exceptions import TdxError
from tdxman.models.enums import KlineCategory, Market
from tdxman.offline.daily_bar import find_daily_bar_file

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


async def online_records(client, item, asynchronous=False):
    records = []
    oldest = None
    offset = 0
    while offset < 64000:
        if asynchronous:
            try:
                frame = await client.get_index_bars(
                    Market[item["market"]], item["code"], KlineCategory.DAY, offset, 800
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
                                800,
                            )
                        break
                    except (TdxError, OSError, ValueError) as exc:
                        last_error = exc
                else:
                    raise last_error
        else:
            try:
                frame = client.get_index_bars(
                    Market[item["market"]], item["code"], KlineCategory.DAY, offset, 800
                )
            except (TdxError, OSError, ValueError) as original:
                last_error = original
                for host in get_known_hosts():
                    try:
                        with TdxClient(host, timeout=5, heartbeat_interval=0) as other:
                            frame = other.get_index_bars(
                                Market[item["market"]], item["code"], KlineCategory.DAY, offset, 800
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
        records.extend(page)
        offset += len(page)
        # Ask for the next page even if this server returned fewer than requested.
    raise ValueError("指数历史超过分页上限，未标为完整")


def save_index(root, item, records, mode):
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
    path = (
        root
        / "lake/indices/daily"
        / f"market={item['market']}"
        / (f"symbol={item['code']}")
        / "bars.parquet"
    )
    prior = pq.ParquetFile(path).read().to_pylist() if path.exists() else []
    before = {r["trade_date"]: r for r in prior}
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    tmp.replace(path)
    with catalog(root) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO index_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                item["market"],
                item["code"],
                item["name"],
                rows[0]["trade_date"],
                rows[-1]["trade_date"],
                len(rows),
                mode,
                datetime.now(),
            ],
        )
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
        "rows": len(rows),
        "start": str(rows[0]["trade_date"]),
        "end": str(rows[-1]["trade_date"]),
        "zero_breadth_rows": sum(r["up_count"] == r["down_count"] == 0 for r in rows),
    }


@writer
def sync_indices(root, mode="online", asynchronous=False, limit=None, items=None):
    root = Path(root)
    items = load_indices() if items is None else items
    items = items[:limit] if limit else items
    with catalog(root) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS index_coverage (
            market VARCHAR, code VARCHAR, name VARCHAR, start_date DATE, end_date DATE,
            row_count BIGINT, source VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY (market, code))""")
    report = {
        "run_id": uuid4().hex,
        "mode": mode,
        "requested": len(items),
        "started_at": datetime.now().isoformat(),
        "success": [],
        "failed": [],
    }

    async def process(client=None):
        for item in items:
            try:
                records = (
                    offline_records(item)
                    if mode == "offline"
                    else await online_records(client, item, asynchronous)
                )
                report["success"].append(save_index(root, item, records, mode))
            except Exception as exc:
                report["failed"].append({**item, "error": f"{type(exc).__name__}: {exc}"})

    async def run():
        if mode == "offline":
            await process()
        elif asynchronous:
            async with AsyncTdxClient.from_best_host() as client:
                await process(client)
        else:
            with TdxClient.from_best_host() as client:
                await process(client)

    asyncio.run(run())
    report["finished_at"] = datetime.now().isoformat()
    path = root / "reports/index-sync" / f"{report['run_id']}.json"
    atomic_json(path, report)
    return report, path
