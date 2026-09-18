"""Low-frequency fundamental snapshots sourced from tdxman MAC quotes."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, time, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from .config import read_config
from .daily_storage import build_field_quality_report, is_missing_value, merge_daily
from .fetch import client_factory, fetch_async, fetch_sync
from .free_stockdb import _market
from .index_lists import atomic_json
from .pool import writer
from .store import bars_path, catalog, initialize, record_coverages

# Like adjustment factors, this is an independent, single-file dataset rather
# than columns periodically copied into every daily bar.
SNAPSHOT_FILE = Path("lake/fundamentals/snapshots.parquet")
FIELDS = (
    "symbol",
    "market",
    "code",
    "name",
    "total_share",
    "float_share",
    "eps",
    "ttm_eps",
    "net_assets",
    "refreshed_at",
    "source",
)


def _load_tdxman() -> tuple[Any, Any, Any, Any, Any]:
    from tdxman.codec.bitmap import FieldBit, PresetField
    from tdxman.mac.client import AsyncMacClient, MacClient
    from tdxman.models.enums import Market

    return MacClient, AsyncMacClient, PresetField, FieldBit, Market


def _symbols(root: Path, limit: int | None) -> list[str]:
    with catalog(root) as conn:
        try:
            rows = conn.execute(
                """select c.symbol from coverage c
                left join universe u on u.symbol = c.symbol
                where coalesce(u.asset_type, 'stock') = 'stock' order by c.symbol"""
            ).fetchall()
        except Exception:
            rows = conn.execute("select symbol from coverage order by symbol").fetchall()
        symbols = [row[0] for row in rows]
    return symbols[:limit] if limit else symbols


def _snapshot_rows(frame: Any, refreshed_at: datetime) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for quote in frame.to_dict(orient="records"):
        code = str(quote.get("code", ""))
        if not code:
            continue
        market = _market(code)
        # The protocol reports shares in 10,000-share units. Store shares.
        total = quote.get("total_shares")
        floating = quote.get("float_shares")
        close = quote.get("close")
        pe_ttm = quote.get("pe_ttm")
        ttm_eps = (
            float(close) / float(pe_ttm)
            if close is not None and pe_ttm is not None and float(pe_ttm) > 0
            else None
        )
        rows.append(
            {
                "symbol": code,
                "market": market,
                "code": code,
                "name": quote.get("name"),
                "total_share": float(total) * 10_000 if total is not None else None,
                "float_share": float(floating) * 10_000 if floating is not None else None,
                "eps": float(quote["eps"]) if quote.get("eps") is not None else None,
                "ttm_eps": ttm_eps,
                "net_assets": float(quote["net_assets"])
                if quote.get("net_assets") is not None
                else None,
                "refreshed_at": refreshed_at,
                "source": "tdxman:mac:quote",
            }
        )
    return rows


def _same_snapshot(left: dict[str, object] | None, right: dict[str, object]) -> bool:
    if left is None:
        return False
    return all(left.get(field) == right.get(field) for field in FIELDS if field != "refreshed_at")


def _write(root: Path, incoming: list[dict[str, object]]) -> bool:
    path = root / SNAPSHOT_FILE
    prior = pq.read_table(path).to_pylist() if path.exists() else []
    # Accept snapshots written by the short-lived plural field spelling during
    # the initial rollout, then rewrite them with free-stockdb field names.
    for row in prior:
        row.setdefault("total_share", row.pop("total_shares", None))
        row.setdefault("float_share", row.pop("float_shares", None))
    merged = {row["symbol"]: row for row in prior}
    changed = not path.exists()
    for row in incoming:
        if not _same_snapshot(merged.get(row["symbol"]), row):
            merged[row["symbol"]] = row
            changed = True
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    schema = pa.schema(
        [
            pa.field("symbol", pa.string()),
            pa.field("market", pa.string()),
            pa.field("code", pa.string()),
            pa.field("name", pa.string()),
            pa.field("total_share", pa.float64()),
            pa.field("float_share", pa.float64()),
            pa.field("eps", pa.float64()),
            pa.field("ttm_eps", pa.float64()),
            pa.field("net_assets", pa.float64()),
            pa.field("refreshed_at", pa.timestamp("us")),
            pa.field("source", pa.string()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(list(merged.values()), schema=schema), temporary, compression="zstd"
    )
    temporary.replace(path)
    return True


def _quote_batch_size() -> int:
    raw = read_config().get("aspool.update.quote_batch_size", "80")
    try:
        return min(80, max(1, int(raw)))
    except ValueError as exc:
        raise ValueError("aspool.update.quote_batch_size 应为 1 至 80 的整数") from exc


def _markets(symbols: list[str], market_type: Any) -> list[tuple[Any, str]]:
    return [(getattr(market_type, _market(code)), code) for code in symbols]


def quote_update_allowed(now: datetime) -> bool:
    """Quote updates are forbidden during the mainland continuous session."""
    return not (now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30))


def _quote_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "")
    try:
        if len(raw) >= 10 and raw[4] == "-":
            return date.fromisoformat(raw[:10])
        raw = raw[:8]
        if len(raw) == 8 and raw.isdigit():
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        pass
    return None


def _quote_bar(quote: dict[str, object], trade_date: date) -> dict[str, object]:
    close = quote.get("close")
    pre_close = quote.get("pre_close")
    high, low = quote.get("high"), quote.get("low")
    row: dict[str, object] = {
        "trade_date": trade_date,
        "symbol": str(quote["code"]),
        "code": str(quote["code"]),
        "name": quote.get("name"),
        "pre_close": pre_close,
        "open": quote.get("open"),
        "high": high,
        "low": low,
        "close": close,
        # MAC quote vol is lots; the aspool/free-stockdb contract stores shares.
        "volume": float(quote["vol"]) * 100 if quote.get("vol") is not None else None,
        "amount": quote.get("amount"),
        "vol_ratio": quote.get("vol_ratio"),
        "turnover": quote.get("turnover"),
    }
    from .st_source import classify_st_name

    st_status = classify_st_name(quote.get("name"))
    if st_status is not None:
        row["is_st"] = st_status
        row["is_st_source"] = "tdxman:quote_name"
        row["is_st_name_date"] = trade_date
    if not is_missing_value(close) and not is_missing_value(pre_close):
        try:
            close_value = float(close)
            pre_close_value = float(pre_close)
        except (TypeError, ValueError):
            close_value = pre_close_value = None
        if (
            close_value is not None
            and pre_close_value is not None
            and math.isfinite(close_value)
            and math.isfinite(pre_close_value)
            and pre_close_value != 0
        ):
            row["pct_chg"] = (close_value / pre_close_value - 1) * 100
            if not is_missing_value(high) and not is_missing_value(low):
                row["amplitude"] = (float(high) - float(low)) / pre_close_value * 100
    return row


def update_from_quotes(root, limit=None, now=None, async_mode=False, workers=1):
    """Refresh dated quote records, reporting omissions and avoiding unchanged rewrites."""
    started = perf_counter()
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if not quote_update_allowed(now):
        raise ValueError("工作日 09:00 至 15:30 不允许运行 aspool update")
    initialize(root)
    MacClient, AsyncMacClient, PresetField, FieldBit, Market = _load_tdxman()
    symbols = _symbols(root, limit)
    with catalog(root) as conn:
        last_dates = {
            r[0]: r[1].date() if isinstance(r[1], datetime) else r[1]
            for r in conn.execute("SELECT symbol,end_date FROM coverage").fetchall()
        }
    report = {
        "command": "update",
        "run_id": uuid4().hex,
        "requested": len(symbols),
        "started_at": now.isoformat(),
        "async": async_mode,
        "workers": workers,
        "success": 0,
        "changed_rows": 0,
        "unchanged_symbols": 0,
        "missing": [],
        "rejected": [],
        "failed": [],
        "write_seconds": 0.0,
    }
    pending = []
    seen = set()
    failed_symbols = set()
    batch_size = _quote_batch_size()
    batches = [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]
    fields = PresetField.COMMON + FieldBit.SERVER_UPDATE_DATE + FieldBit.SERVER_UPDATE_TIME

    def request(client, batch):
        return client.get_stock_quotes(_markets(batch, Market), fields)

    async def async_request(client, batch):
        return await client.get_stock_quotes(_markets(batch, Market), fields)

    def consume(entry):
        batch, frame, error, _ = entry
        if error:
            report["failed"].append({"symbols": batch, "error": str(error)})
            failed_symbols.update(batch)
            return
        batch_snapshots = {r["symbol"]: r for r in _snapshot_rows(frame, now.replace(tzinfo=None))}
        for quote in frame.to_dict(orient="records"):
            code = str(quote.get("code", ""))
            if code not in batch or code in seen:
                continue
            seen.add(code)
            day = _quote_date(quote.get("server_update_date"))
            reason = None
            if day is None:
                reason = "missing_or_invalid_date"
            elif day > now.date() or day.weekday() >= 5:
                reason = "invalid_trading_date"
            elif day < last_dates[code]:
                reason = "stale_quote"
            row = _quote_bar(quote, day)
            for key in ("open", "high", "low", "close", "volume", "amount"):
                value = row.get(key)
                if value is None or not math.isfinite(float(value)) or float(value) < 0:
                    reason = reason or "invalid_ohlcv"
            if reason is None and row["high"] < row["low"]:
                reason = "invalid_ohlcv"
            if reason:
                report["rejected"].append({"symbol": code, "date": str(day), "reason": reason})
                continue
            snapshot = batch_snapshots[code]
            pending.append((code, row, snapshot))

    async def run_async():
        factory, retry = client_factory(AsyncMacClient, workers)
        async for entry in fetch_async(batches, factory, async_request, workers, retry):
            consume(entry)

    if batches:
        if async_mode:
            asyncio.run(run_async())
        else:
            factory, retry = client_factory(MacClient, workers)
            for entry in fetch_sync(batches, factory, request, workers, retry):
                consume(entry)
    tick = perf_counter()
    successes, changed_rows, unchanged, failures, quality_rows, quote_dates = _publish_quote_rows(
        root, pending
    )
    report["write_seconds"] = perf_counter() - tick
    report["success"] = successes
    report["changed_rows"] = changed_rows
    report["unchanged_symbols"] = unchanged
    report["failed"].extend(failures)
    report["field_quality"] = build_field_quality_report(
        quality_rows, "stock", "tdxman:quote"
    )
    # Derive from every successful dated quote, not only changed parquet rows:
    # an existing raw row may still have no published or stale derived batch.
    if quote_dates:
        try:
            from .limit_events import compute_limit_events, summarize_published_limit_quality

            report["limit_events"] = {
                "dates": [day.isoformat() for day in quote_dates],
                "batches": compute_limit_events(root, quote_dates, asset_type="stock"),
                "status": "ok",
            }
            report["limit_events"]["rule_quality"] = summarize_published_limit_quality(
                root, quote_dates
            )
        except Exception as exc:  # noqa: BLE001 - preserve raw quote success for retry
            report["limit_events"] = {
                "dates": [day.isoformat() for day in quote_dates],
                "status": "failed",
                "error": str(exc),
            }
    report["missing"] = sorted(set(symbols) - seen - failed_symbols)
    report["total_seconds"] = perf_counter() - started
    report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    report["status"] = "partial" if report["failed"] or report["rejected"] else "ok"
    report_path = Path(root) / "reports/maintenance" / f"{report['run_id']}.json"
    atomic_json(report_path, report)
    atomic_json(
        Path(root) / "reports/maintenance/latest.json", {**report, "report": str(report_path)}
    )
    if report["failed"] or report["rejected"]:
        raise ValueError(f"部分报价未写入，请查看报告：{report_path}")
    return report["success"], report["changed_rows"]


@writer
def _publish_quote_rows(root, pending):
    """Publish fetched quote rows under a short lock against the current files."""
    from .tdx_online import _enrich_daily

    with catalog(root) as conn:
        last_dates = {
            row[0]: row[1].date() if isinstance(row[1], datetime) else row[1]
            for row in conn.execute("SELECT symbol,end_date FROM coverage").fetchall()
        }
    coverage, quality_rows, snapshots, failures, changed_dates = [], [], [], [], []
    success = changed_rows = unchanged = 0
    for code, row, snapshot in pending:
        try:
            if row["trade_date"] < last_dates.get(code, row["trade_date"]):
                raise ValueError("stale_quote")
            if not bars_path(root, "daily", _market(code), code).exists():
                raise ValueError("missing daily file")
            changed = merge_daily(
                root,
                _market(code),
                code,
                _enrich_daily([row], snapshot),
                "tdxman:quote",
                coverage=coverage,
                changed_dates=changed_dates,
                quality_rows=quality_rows,
            )
            snapshots.append(snapshot)
            success += 1
            changed_rows += changed
            unchanged += changed == 0
        except Exception as exc:
            failures.append({"symbol": code, "error": str(exc)})
    record_coverages(root, coverage)
    _write(root, snapshots)
    quote_dates = sorted({row["trade_date"] for _, row, _ in pending})
    return success, changed_rows, unchanged, failures, quality_rows, quote_dates


def _refresh_sync(root: Path, symbols: list[str]) -> tuple[int, int]:
    MacClient, _, PresetField, FieldBit, Market = _load_tdxman()
    rows: list[dict[str, object]] = []
    refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with MacClient.from_best_host(refresh=False) as client:
        for offset in range(0, len(symbols), 80):
            frame = client.get_stock_quotes(
                _markets(symbols[offset : offset + 80], Market),
                PresetField.FUNDAMENTAL + FieldBit.CLOSE + FieldBit.PE_TTM,
            )
            rows.extend(_snapshot_rows(frame, refreshed_at))
    _publish_snapshots(root, rows)
    return len(symbols), len(rows)


async def _refresh_async(root: Path, symbols: list[str]) -> tuple[int, int]:
    _, AsyncMacClient, PresetField, FieldBit, Market = _load_tdxman()
    rows: list[dict[str, object]] = []
    refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncMacClient.from_best_host(refresh=False) as client:
        for offset in range(0, len(symbols), 80):
            frame = await client.get_stock_quotes(
                _markets(symbols[offset : offset + 80], Market),
                PresetField.FUNDAMENTAL + FieldBit.CLOSE + FieldBit.PE_TTM,
            )
            rows.extend(_snapshot_rows(frame, refreshed_at))
    _publish_snapshots(root, rows)
    return len(symbols), len(rows)


def refresh_fundamentals(
    root: Path, *, async_mode: bool = False, limit: int | None = None
) -> tuple[int, int]:
    """Manually refresh the complete fundamentals snapshot from MAC quotes."""
    initialize(root)
    symbols = _symbols(root, limit)
    if not symbols:
        return 0, 0
    if async_mode:
        return asyncio.run(_refresh_async(root, symbols))
    return _refresh_sync(root, symbols)


@writer
def _publish_snapshots(root: Path, rows: list[dict[str, object]]) -> bool:
    return _write(root, rows)
