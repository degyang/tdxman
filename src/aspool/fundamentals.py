"""Low-frequency fundamental snapshots sourced from tdxman MAC quotes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .free_stockdb import _market
from .pool import writer
from .store import catalog, initialize

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
