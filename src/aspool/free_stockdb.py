from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from .pool import writer
from .store import bars_path, catalog, daily_path, initialize, last_date, record_coverage


@dataclass(frozen=True)
class ImportStats:
    symbols: int = 0
    rows: int = 0


def _market(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


class FreeStockDb:
    """Read-only adapter for an unpacked free-stockdb release."""

    def __init__(self, root: Path, start_server: bool = True) -> None:
        self.root = root.resolve()
        self._process: subprocess.Popen[bytes] | None = None
        self._client = None
        self._start_server = start_server

    def __enter__(self) -> FreeStockDb:
        if not (self.root / "data").is_dir() or not (self.root / "pybao").is_dir():
            raise ValueError(f"不是 free-stockdb 发布目录: {self.root}")
        self._connect()
        return self

    def _connect(self) -> None:
        if self._start_server:
            self._process = subprocess.Popen(
                [str(self.root / "stockdb"), str(self.root / "stockdb.conf"), "-s", "start"],
                cwd=self.root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
        sys.path.insert(0, str(self.root / "pybao"))
        sdk = importlib.import_module("stock_sdk")
        self._client = sdk.StockDBClient()

    def _restart(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=10)
        self._connect()

    def __exit__(self, *_: object) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=10)

    def symbols(self) -> list[str]:
        assert self._client is not None
        grouped = self._client.rd.get("股票代码")
        # QueryResult exposes the aggregate response as a Python-literal string;
        # its chaining API only materializes the first group for this endpoint.
        values = ast.literal_eval(str(grouped))
        return sorted(
            {str(code) for group in ("0", "3", "6", "9") for code in values.get(group, [])}
        )

    def daily_bars(self, symbol: str, start: date | None = None) -> list[dict[str, object]]:
        assert self._client is not None
        rows = self._client.get_data(
            symbol,
            start=start.strftime("%Y%m%d") if start else None,
            frequency="1d",
            fq=None,
        )
        return [dict(row) for row in rows]

    def daily_bars_many(
        self, symbols: list[str], start: date | None = None
    ) -> Iterator[tuple[str, list[dict[str, object]]]]:
        """Fetch a bounded batch through the SDK pipeline rather than one socket call per symbol."""
        assert self._client is not None
        for offset in range(0, len(symbols), 100):
            # The stockdb binary closes long-lived pipeline sessions after a
            # small number of large scans. Reconnect before it starts silently
            # yielding empty batches.
            if offset and offset % 1000 == 0:
                self._restart()
            batch = symbols[offset : offset + 100]
            data = self._client.get_data(
                batch, start=start.strftime("%Y%m%d") if start else None, frequency="1d", fq=None
            )
            for symbol in batch:
                yield symbol, [dict(row) for row in data.get(symbol, [])]


def _normalize(rows: list[dict[str, object]], symbol: str) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        raw_date = str(row.get("date") or row.get("trade_date") or "")
        raw_date = raw_date[:10].replace("-", "")
        if len(raw_date) != 8 or not raw_date.isdigit():
            continue
        normalized.append(
            {
                **row,
                "symbol": symbol,
                "trade_date": date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])),
            }
        )
    normalized.sort(key=lambda row: row["trade_date"])
    return normalized


def _write_daily(root: Path, market: str, symbol: str, rows: list[dict[str, object]]) -> None:
    target = daily_path(root, market, symbol)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid4().hex}.part")
    keys = dict.fromkeys(key for row in rows for key in row)
    table = pa.Table.from_pylist([{key: row.get(key) for key in keys} for row in rows])
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(target)


@writer
def import_daily(
    root: Path, source_root: Path, incremental: bool, limit: int | None = None
) -> ImportStats:
    initialize(root)
    source_name = f"free-stockdb:{source_root.resolve()}"
    run_id = uuid4().hex
    with catalog(root) as conn:
        conn.execute(
            "insert into sync_runs values (?, ?, ?, current_timestamp, null, 0, 0, "
            "'running', null)",
            [run_id, "update" if incremental else "sync", source_name],
        )

    stats = ImportStats()
    try:
        with FreeStockDb(source_root) as source:
            symbols = source.symbols()
            if limit is not None:
                symbols = symbols[:limit]
            if incremental:
                grouped: dict[date | None, list[str]] = {}
                for symbol in symbols:
                    grouped.setdefault(last_date(root, symbol), []).append(symbol)
                records = (
                    item
                    for start, group in grouped.items()
                    for item in source.daily_bars_many(group, start)
                )
            else:
                records = source.daily_bars_many(symbols)
            for symbol, raw_rows in records:
                start = last_date(root, symbol) if incremental else None
                rows = _normalize(raw_rows, symbol)
                if not rows:
                    continue
                market = _market(symbol)
                if incremental and start is not None:
                    existing = daily_path(root, market, symbol)
                    if existing.exists():
                        prior = pq.read_table(existing).to_pylist()
                        rows = _normalize(prior + rows, symbol)
                deduplicated = {row["trade_date"]: row for row in rows}
                rows = [deduplicated[key] for key in sorted(deduplicated)]
                _write_daily(root, market, symbol, rows)
                record_coverage(
                    root,
                    symbol,
                    market,
                    rows[0]["trade_date"],
                    rows[-1]["trade_date"],
                    len(rows),
                    source_name,
                )
                stats = ImportStats(stats.symbols + 1, stats.rows + len(rows))
        with catalog(root) as conn:
            conn.execute(
                "update sync_runs set finished_at=current_timestamp, symbols=?, "
                "rows_written=?, status='ok' where run_id=?",
                [stats.symbols, stats.rows, run_id],
            )
    except Exception as exc:
        with catalog(root) as conn:
            conn.execute(
                "update sync_runs set finished_at=current_timestamp, status='failed', "
                "error=? where run_id=?",
                [str(exc), run_id],
            )
        raise
    return stats


@writer
def import_adjustments(root: Path, source_root: Path) -> int:
    """Import free-stockdb cumulative adjustment factors as a separate dataset."""
    initialize(root)
    rows: list[dict[str, object]] = []
    with FreeStockDb(source_root) as source:
        assert source._client is not None
        for symbol, dates in source._client._fq_dates.items():
            values = source._client._fq_cums.get(symbol, [])
            for factor_date, cumulative_factor in zip(dates, values):
                rows.append(
                    {
                        "symbol": symbol,
                        "market": _market(symbol),
                        "trade_date": date(
                            int(factor_date[:4]), int(factor_date[4:6]), int(factor_date[6:8])
                        ),
                        "cumulative_factor": float(cumulative_factor),
                    }
                )
    target = root / "lake" / "adjustments" / "factors.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid4().hex}.part")
    keys = dict.fromkeys(key for row in rows for key in row)
    table = pa.Table.from_pylist([{key: row.get(key) for key in keys} for row in rows])
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(target)
    return len(rows)


def import_minutes(root: Path, source_root: Path, limit: int | None = None) -> ImportStats:
    """One-time import of raw 1-minute bars from free-stockdb."""
    initialize(root)
    stats = ImportStats()
    with FreeStockDb(source_root) as source:
        assert source._client is not None
        symbols = source.symbols()[:limit] if limit else source.symbols()
        for symbol in symbols:
            raw_rows = []
            for attempt in range(3):
                try:
                    raw_rows = source._client.get_data(symbol, frequency="1m", fq=None)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    source._restart()
            rows = []
            for row in raw_rows:
                value = row.get("date")
                if not value:
                    continue
                raw_timestamp = str(value)
                try:
                    timestamp = datetime.strptime(raw_timestamp[:14], "%Y%m%d%H%M%S")
                except ValueError:
                    continue
                rows.append({**dict(row), "symbol": symbol, "timestamp": timestamp})
            if not rows:
                continue
            path = bars_path(root, "minutes", _market(symbol), symbol)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".{uuid4().hex}.part")
            pq.write_table(pa.Table.from_pylist(rows), temp, compression="zstd")
            temp.replace(path)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["timestamp"],
                rows[-1]["timestamp"],
                len(rows),
                f"free-stockdb:{source_root.resolve()}",
                "minutes",
            )
            stats = ImportStats(stats.symbols + 1, stats.rows + len(rows))
    return stats


def validate_period(root: Path, period: str) -> dict[str, int]:
    """Check imported files for duplicate keys and basic OHLC invariants."""
    key = "trade_date" if period == "daily" else "timestamp"
    files = list((root / "lake" / "bars" / period).glob("market=*/symbol=*/bars.parquet"))
    invalid = duplicates = rows = 0
    for path in files:
        data = pq.read_table(path).to_pylist()
        seen = set()
        for row in data:
            rows += 1
            value = row.get(key)
            if value in seen:
                duplicates += 1
            seen.add(value)
            values = [row.get(field) for field in ("open", "high", "low", "close")]
            if any(value is not None and value < 0 for value in values) or (
                row.get("high") is not None
                and row.get("low") is not None
                and row["high"] < row["low"]
            ):
                invalid += 1
    return {"files": len(files), "rows": rows, "duplicates": duplicates, "invalid": invalid}


def validate_daily(root: Path) -> dict[str, int]:
    return validate_period(root, "daily")
