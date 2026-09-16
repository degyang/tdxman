from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .free_stockdb import _market, _normalize, _write_daily
from .pool import writer
from .store import bars_path, catalog, last_marker, record_coverage


def _fundamentals(root: Path) -> dict[str, dict[str, object]]:
    """Read manual fundamentals snapshots for daily-bar enrichment, if present."""
    path = root / "lake/fundamentals/snapshots.parquet"
    if not path.exists():
        return {}
    return {row["symbol"]: row for row in pq.read_table(path).to_pylist()}


def _enrich_daily(rows: list[dict[str, object]], snapshot: dict[str, object] | None):
    """Add free-stockdb-compatible valuation fields to online daily bars."""
    if snapshot is None:
        return rows
    total_share = snapshot.get("total_share")
    float_share = snapshot.get("float_share")
    ttm_eps = snapshot.get("ttm_eps")
    net_assets = snapshot.get("net_assets")
    enriched: list[dict[str, object]] = []
    for row in rows:
        close = row.get("close")
        volume = row.get("volume")
        values: dict[str, object] = {
            "total_share": total_share,
            "float_share": float_share,
        }
        if close is not None and total_share and float_share:
            values["total_mv"] = float(close) * float(total_share)
            values["float_mv"] = float(close) * float(float_share)
        if close is not None and ttm_eps and float(ttm_eps) > 0:
            values["pe_ttm"] = float(close) / float(ttm_eps)
        if close is not None and net_assets and float(net_assets) > 0:
            values["pb"] = float(close) / float(net_assets)
        if volume is not None and float_share and float(float_share) > 0:
            values["turnover"] = float(volume) / float(float_share) * 100
        enriched.append({**row, **values})
    return enriched


def _load_tdxman() -> tuple[Any, Any, Any, Any]:
    from tdxman.mac.client import AsyncMacClient, MacClient
    from tdxman.mac.enums import Adjust, Period
    from tdxman.models.enums import Market

    return MacClient, AsyncMacClient, (Adjust, Period), Market


def _tdx_market(symbol: str, market_type: Any) -> Any:
    if symbol.startswith(("4", "8")):
        return market_type.BJ
    return market_type.SH if symbol.startswith(("5", "6", "9")) else market_type.SZ


def _daily_rows(frame: Any, symbol: str, start: date | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        value = row.get("datetime") or row.get("date")
        parsed = value.date() if hasattr(value, "date") else date.fromisoformat(str(value)[:10])
        if not start or parsed >= start:
            rows.append(
                {**row, "trade_date": parsed, "symbol": symbol, "volume": float(row["vol"])}
            )
    return rows


def _minute_rows(frame: Any, symbol: str, start: datetime | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        value = row.get("datetime") or row.get("date")
        stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if not isinstance(stamp, datetime):
            stamp = datetime.fromisoformat(str(stamp))
        if not start or stamp >= start:
            rows.append({**row, "timestamp": stamp.replace(tzinfo=None), "symbol": symbol})
    return rows


def _merge_rows(
    prior: list[dict[str, object]], incoming: list[dict[str, object]], key: str
) -> list[dict[str, object]]:
    merged: dict[object, dict[str, object]] = {}
    for row in prior + incoming:
        previous = merged.get(row[key], {})
        merged[row[key]] = {
            **previous,
            **{field: value for field, value in row.items() if value is not None},
        }
    return [merged[value] for value in sorted(merged)]


def _symbols(root: Path, period: str, limit: int | None) -> list[str]:
    table = "coverage" if period == "daily" else "coverage_minutes"
    with catalog(root) as conn:
        values = [
            row[0] for row in conn.execute(f"select symbol from {table} order by symbol").fetchall()
        ]
    return values[:limit] if limit else values


def update_daily(root: Path, limit: int | None = None) -> tuple[int, int]:
    """Update all imported daily symbols with one reusable synchronous MAC connection."""
    MacClient, _, enums, Market = _load_tdxman()
    Adjust, Period = enums
    count = rows_written = 0
    fundamentals = _fundamentals(root)
    with MacClient.from_best_host() as client:
        for symbol in _symbols(root, "daily", limit):
            start = None  # Refresh the overlap, including previously missing online volume.
            frame = client.get_stock_kline(
                _tdx_market(symbol, Market),
                symbol,
                Period.DAILY,
                start=0,
                count=30,
                adjust=Adjust.NONE,
            )
            incoming = _normalize(
                _enrich_daily(_daily_rows(frame, symbol, start), fundamentals.get(symbol)), symbol
            )
            if not incoming:
                continue
            path = bars_path(root, "daily", _market(symbol), symbol)
            prior = _normalize(pq.read_table(path).to_pylist(), symbol) if path.exists() else []
            identity = {}
            for prior_row in reversed(prior):
                for field in ("code", "name", "market"):
                    if field not in identity and prior_row.get(field) is not None:
                        identity[field] = prior_row[field]
            incoming = [{**identity, **row} for row in incoming]
            rows = _merge_rows(prior, incoming, "trade_date")
            _write_daily(root, _market(symbol), symbol, rows)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["trade_date"],
                rows[-1]["trade_date"],
                len(rows),
                "tdxman",
                "daily",
            )
            count += 1
            rows_written += len(incoming)
    return count, rows_written


async def update_daily_async(root: Path, limit: int | None = None) -> tuple[int, int]:
    """Update daily data through tdxman's asynchronous MAC client."""
    _, AsyncMacClient, enums, Market = _load_tdxman()
    Adjust, Period = enums
    count = rows_written = 0
    fundamentals = _fundamentals(root)
    async with AsyncMacClient.from_best_host() as client:
        for symbol in _symbols(root, "daily", limit):
            start = None  # Refresh the overlap, including previously missing online volume.
            frame = await client.get_stock_kline(
                _tdx_market(symbol, Market), symbol, Period.DAILY, 0, 30, 1, Adjust.NONE
            )
            incoming = _normalize(
                _enrich_daily(_daily_rows(frame, symbol, start), fundamentals.get(symbol)), symbol
            )
            if not incoming:
                continue
            path = bars_path(root, "daily", _market(symbol), symbol)
            prior = _normalize(pq.read_table(path).to_pylist(), symbol) if path.exists() else []
            identity = {}
            for prior_row in reversed(prior):
                for field in ("code", "name", "market"):
                    if field not in identity and prior_row.get(field) is not None:
                        identity[field] = prior_row[field]
            incoming = [{**identity, **row} for row in incoming]
            rows = _merge_rows(prior, incoming, "trade_date")
            _write_daily(root, _market(symbol), symbol, rows)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["trade_date"],
                rows[-1]["trade_date"],
                len(rows),
                "tdxman",
                "daily",
            )
            count += 1
            rows_written += len(incoming)
    return count, rows_written


def update_minutes(root: Path, limit: int | None = None) -> tuple[int, int]:
    MacClient, _, enums, Market = _load_tdxman()
    Adjust, Period = enums
    count = rows_written = 0
    with MacClient.from_best_host() as client:
        for symbol in _symbols(root, "minutes", limit):
            start = last_marker(root, symbol, "minutes")
            frame = client.get_stock_kline(
                _tdx_market(symbol, Market),
                symbol,
                Period.MIN_1,
                start=0,
                count=800,
                adjust=Adjust.NONE,
            )
            incoming = _minute_rows(frame, symbol, start if isinstance(start, datetime) else None)
            if not incoming:
                continue
            path = bars_path(root, "minutes", _market(symbol), symbol)
            prior = pq.read_table(path).to_pylist() if path.exists() else []
            rows = _merge_rows(prior, incoming, "timestamp")
            _write_period(path, rows)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["timestamp"],
                rows[-1]["timestamp"],
                len(rows),
                "tdxman",
                "minutes",
            )
            count += 1
            rows_written += len(incoming)
    return count, rows_written


async def update_minutes_async(root: Path, limit: int | None = None) -> tuple[int, int]:
    _, AsyncMacClient, enums, Market = _load_tdxman()
    Adjust, Period = enums
    count = rows_written = 0
    async with AsyncMacClient.from_best_host() as client:
        for symbol in _symbols(root, "minutes", limit):
            start = last_marker(root, symbol, "minutes")
            frame = await client.get_stock_kline(
                _tdx_market(symbol, Market), symbol, Period.MIN_1, 0, 800, 1, Adjust.NONE
            )
            incoming = _minute_rows(frame, symbol, start if isinstance(start, datetime) else None)
            if not incoming:
                continue
            path = bars_path(root, "minutes", _market(symbol), symbol)
            prior = pq.read_table(path).to_pylist() if path.exists() else []
            rows = _merge_rows(prior, incoming, "timestamp")
            _write_period(path, rows)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["timestamp"],
                rows[-1]["timestamp"],
                len(rows),
                "tdxman",
                "minutes",
            )
            count += 1
            rows_written += len(incoming)
    return count, rows_written


def _write_period(path: Path, rows: list[dict[str, object]]) -> None:
    from uuid import uuid4

    import pyarrow as pa

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.part")
    pq.write_table(pa.Table.from_pylist(rows), temporary, compression="zstd")
    temporary.replace(path)


@writer
def update_online(
    root: Path, period: str, async_mode: bool, limit: int | None = None
) -> tuple[int, int]:
    if period == "daily":
        return (
            asyncio.run(update_daily_async(root, limit))
            if async_mode
            else update_daily(root, limit)
        )
    return (
        asyncio.run(update_minutes_async(root, limit))
        if async_mode
        else update_minutes(root, limit)
    )
