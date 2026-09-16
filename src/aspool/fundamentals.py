"""Low-frequency fundamental snapshots sourced from tdxman MAC quotes."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .free_stockdb import _market
from .pool import writer
from .store import bars_path, catalog, initialize, record_coverage

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
        symbols = [
            row[0] for row in conn.execute("select symbol from coverage order by symbol").fetchall()
        ]
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


def _write(root: Path, incoming: list[dict[str, object]]) -> None:
    path = root / SNAPSHOT_FILE
    prior = pq.read_table(path).to_pylist() if path.exists() else []
    # Accept snapshots written by the short-lived plural field spelling during
    # the initial rollout, then rewrite them with free-stockdb field names.
    for row in prior:
        row.setdefault("total_share", row.pop("total_shares", None))
        row.setdefault("float_share", row.pop("float_shares", None))
    merged = {row["symbol"]: row for row in prior}
    merged.update({row["symbol"]: row for row in incoming})
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


def _markets(symbols: list[str], market_type: Any) -> list[tuple[Any, str]]:
    markets = []
    for code in symbols:
        market = (
            market_type.BJ
            if code.startswith(("4", "8"))
            else market_type.SH
            if code.startswith(("5", "6", "9"))
            else market_type.SZ
        )
        markets.append((market, code))
    return markets


def quote_update_allowed(now: datetime) -> bool:
    """Quote updates are forbidden during the mainland continuous session."""
    return not (now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30))


def _quote_date(value: object, fallback: date) -> date:
    raw = str(value or "")[:8]
    if len(raw) == 8 and raw.isdigit():
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            pass
    return fallback


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
    if close is not None and pre_close and float(pre_close) != 0:
        row["pct_chg"] = (float(close) / float(pre_close) - 1) * 100
        if high is not None and low is not None:
            row["amplitude"] = (float(high) - float(low)) / float(pre_close) * 100
    return row


@writer
def update_from_quotes(
    root: Path, limit: int | None = None, now: datetime | None = None
) -> tuple[int, int]:
    """Refresh the latest daily bar and fundamentals from full quote records."""
    now = now or datetime.now().astimezone()
    if not quote_update_allowed(now):
        raise ValueError("工作日 09:00 至 15:30 不允许运行 aspool update")
    initialize(root)
    MacClient, _, PresetField, FieldBit, Market = _load_tdxman()
    from .free_stockdb import _normalize, _write_daily
    from .tdx_online import _enrich_daily, _fill_close_vol_ratio, _merge_rows

    symbols = _symbols(root, limit)
    snapshots: list[dict[str, object]] = []
    bars: dict[str, dict[str, object]] = {}
    today = now.date()
    with MacClient.from_best_host() as client:
        for offset in range(0, len(symbols), 80):
            frame = client.get_stock_quotes(
                _markets(symbols[offset : offset + 80], Market),
                PresetField.COMMON + FieldBit.SERVER_UPDATE_DATE + FieldBit.SERVER_UPDATE_TIME,
            )
            records = frame.to_dict(orient="records")
            snapshots.extend(_snapshot_rows(frame, now.replace(tzinfo=None)))
            for quote in records:
                code = str(quote.get("code", ""))
                if code:
                    quote_day = _quote_date(quote.get("server_update_date"), today)
                    bars[code] = _quote_bar(quote, quote_day)
    _write(root, snapshots)
    snapshot_by_symbol = {row["symbol"]: row for row in snapshots}
    written = count = 0
    for symbol in symbols:
        path = bars_path(root, "daily", _market(symbol), symbol)
        if not path.exists() or symbol not in bars:
            continue
        prior = _normalize(pq.read_table(path).to_pylist(), symbol)
        if not prior:
            continue
        quote = bars[symbol]
        last_date = prior[-1]["trade_date"]
        # A weekend/non-trading server timestamp must not create a phantom bar.
        if quote["trade_date"].weekday() >= 5 or quote["trade_date"] < last_date:
            quote["trade_date"] = last_date
        incoming = _normalize(_enrich_daily([quote], snapshot_by_symbol.get(symbol)), symbol)
        rows = _fill_close_vol_ratio(_merge_rows(prior, incoming, "trade_date"))
        _write_daily(root, _market(symbol), symbol, rows)
        record_coverage(
            root,
            symbol,
            _market(symbol),
            rows[0]["trade_date"],
            rows[-1]["trade_date"],
            len(rows),
            "tdxman:quote",
            "daily",
        )
        count += 1
        written += len(incoming)
    return count, written


def _refresh_sync(root: Path, symbols: list[str]) -> tuple[int, int]:
    MacClient, _, PresetField, FieldBit, Market = _load_tdxman()
    rows: list[dict[str, object]] = []
    refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with MacClient.from_best_host() as client:
        for offset in range(0, len(symbols), 80):
            frame = client.get_stock_quotes(
                _markets(symbols[offset : offset + 80], Market),
                PresetField.FUNDAMENTAL + FieldBit.CLOSE + FieldBit.PE_TTM,
            )
            rows.extend(_snapshot_rows(frame, refreshed_at))
    _write(root, rows)
    return len(symbols), len(rows)


async def _refresh_async(root: Path, symbols: list[str]) -> tuple[int, int]:
    _, AsyncMacClient, PresetField, FieldBit, Market = _load_tdxman()
    rows: list[dict[str, object]] = []
    refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncMacClient.from_best_host() as client:
        for offset in range(0, len(symbols), 80):
            frame = await client.get_stock_quotes(
                _markets(symbols[offset : offset + 80], Market),
                PresetField.FUNDAMENTAL + FieldBit.CLOSE + FieldBit.PE_TTM,
            )
            rows.extend(_snapshot_rows(frame, refreshed_at))
    _write(root, rows)
    return len(symbols), len(rows)


@writer
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
