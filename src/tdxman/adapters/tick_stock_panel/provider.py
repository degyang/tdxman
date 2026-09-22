"""Tick Stock Panel data contracts backed by the existing tdxman clients."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from typing import Any, cast

import pandas as pd

from .contracts import ChunkDone, ProviderCapabilities, ProviderConfig, ProviderError
from .diagnostics import bounded_request, check_budget
from .normalize import (
    empty_daily,
    empty_minute,
    normalize_daily,
    normalize_date_bound,
    normalize_realtime_row,
    normalize_symbols,
    polars,
    validate_date_range,
)
from .routing import read_standard_daily
from .universe import get_mac_instruments

logger = logging.getLogger(__name__)
_minute_limit = ContextVar("tdxman_minute_limit", default=None)


class TdxmanProvider:
    """Expose verified tdxman data through the Tick Stock Panel duck protocol.

    Data normalization stays here; the host plugin only supplies configuration
    and owned connections. Unsupported units fail explicitly at the boundary.
    """

    name = "tdxman"
    builtin = True

    def __init__(self, config: ProviderConfig | Mapping[str, Any] | None = None):
        if config is None:
            config = ProviderConfig()
        elif isinstance(config, Mapping):
            config = ProviderConfig(**config)
        self._config = config
        if config.request_timeout <= 0 or config.max_result_rows < 1:
            raise ProviderError("CONFIG_INVALID", "Request timeout and row limit must be positive")
        datasets = {"daily": {"enabled": True}}
        if self._config.mac_client is not None:
            datasets["realtime"] = {"enabled": True}
            datasets["minute"] = {"enabled": True}
            datasets["full_minute"] = {"enabled": True}
            datasets["depth5"] = {"enabled": True}
        self.config = ProviderCapabilities(datasets=datasets)
        self._closed = False

    def close(self) -> None:
        if (
            not self._closed
            and self._config.close_standard_client
            and self._config.standard_client is not None
        ):
            self._config.standard_client.close()
        if (
            not self._closed
            and self._config.close_mac_client
            and self._config.mac_client is not None
        ):
            self._config.mac_client.close()
        self._closed = True

    def check(self) -> tuple[bool, str]:
        if self._closed:
            return False, "Provider is closed"
        if self._config.daily_mode == "local" and self._config.pool is None:
            return False, "Local daily mode requires an aspool DataPool"
        if self._config.daily_mode == "online" and self._config.standard_client is None:
            return False, "Online daily mode requires a standard TdxClient"
        return True, "ready"

    @bounded_request
    def get_daily(
        self,
        symbols: object,
        start_time: object | None = None,
        end_time: object | None = None,
        asset_type: str = "stock",
        on_chunk_done: ChunkDone | None = None,
    ) -> Any:
        frames = []
        rows = 0
        for frame in self.iter_daily(symbols, start_time, end_time, asset_type, on_chunk_done):
            rows += frame.height
            if rows > self._config.max_result_rows:
                raise ProviderError("RESULT_LIMIT", "Use iter_daily for larger results")
            frames.append(frame)
        if not frames:
            return empty_daily()
        pl = polars()
        return pl.concat(frames).unique(
            subset=["symbol", "date"], keep="last", maintain_order=True
        ).sort(["symbol", "date"])

    @bounded_request
    def iter_daily(
        self,
        symbols: object,
        start_time: object | None = None,
        end_time: object | None = None,
        asset_type: str = "stock",
        on_chunk_done: ChunkDone | None = None,
    ) -> Iterator[Any]:
        self._ensure_open()
        requested = normalize_symbols(symbols)
        start = normalize_date_bound(start_time, "start_time")
        end = normalize_date_bound(end_time, "end_time")
        validate_date_range(start, end)
        if asset_type not in {"stock", "etf", "index"}:
            raise ProviderError("INVALID_ASSET_TYPE", f"Unsupported asset_type: {asset_type!r}")
        if not requested:
            if on_chunk_done is not None:
                on_chunk_done(1, 1)
            yield empty_daily()
            return
        if self._config.daily_mode == "online":
            if self._config.standard_client is None:
                raise ProviderError(
                    "CONFIG_INVALID", "Online daily mode requires a standard TdxClient"
                )
            total = len(requested)
            for current, symbol in enumerate(requested, 1):
                yield read_standard_daily(
                    self._config.standard_client,
                    symbol,
                    asset_type=asset_type,
                    start=start,
                    end=end,
                    page_size=self._config.online_page_size,
                    max_bars=self._config.online_max_bars_per_symbol,
                )
                if on_chunk_done is not None:
                    on_chunk_done(current, total)
            return

        if self._config.pool is None:
            raise ProviderError("CONFIG_INVALID", "Local daily mode requires an aspool DataPool")

        reader_name = {
            "stock": "read_daily",
            "etf": "read_etf_daily",
            "index": "read_index_daily",
        }[asset_type]
        reader = getattr(self._config.pool, reader_name)
        total = (len(requested) + 79) // 80
        for index, offset in enumerate(range(0, len(requested), 80), 1):
            batch = requested[offset:offset + 80]
            raw = reader(symbols=batch, start=start, end=end)
            yield normalize_daily(raw, asset_type, symbols=batch, start=start, end=end)
            if on_chunk_done is not None:
                on_chunk_done(index, total)

    def test_dataset(self, dataset: str, symbols: object | None = None) -> dict[str, Any]:
        if dataset not in self.config.datasets:
            return {"provider": self.name, "dataset": dataset, "error": "Dataset is unavailable"}
        try:
            requested = symbols or []
            if dataset == "daily":
                frame = self.get_daily(requested, None, None)
            elif dataset == "minute":
                frame = self.get_minute(requested, None, None)
            elif dataset == "full_minute":
                frame = self.get_intraday_batch(requested)
            elif dataset == "realtime":
                if not requested:
                    raise ProviderError(
                        "INVALID_ARGUMENT", "Realtime test requires one or more symbols"
                    )
                rows = self._get_realtime_symbols(normalize_symbols(requested))
                return self._test_result(dataset, rows, list(rows[0]) if rows else [])
            elif dataset == "depth5":
                depth_books = self.get_depth_batch(requested)
                preview = [{"symbol": symbol, **book} for symbol, book in depth_books.items()]
                columns = list(preview[0]) if preview else []
                return self._test_result(dataset, preview, columns)
            else:  # pragma: no cover - guarded by the datasets map
                raise ProviderError("INVALID_ARGUMENT", f"Unknown dataset: {dataset}")
        except Exception as exc:
            return {
                "provider": self.name,
                "dataset": dataset,
                "rows": 0,
                "columns": [],
                "preview": [],
                "error": {"code": getattr(exc, "code", "UPSTREAM_UNAVAILABLE"),
                          "message": str(exc)},
            }
        return self._test_result(dataset, frame.to_dicts(), frame.columns, rows=frame.height)

    def _test_result(
        self,
        dataset: str,
        preview: list[dict[str, Any]],
        columns: list[str],
        *,
        rows: int | None = None,
    ) -> dict[str, Any]:
        def safe(value):
            if isinstance(value, dict):
                return {key: safe(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [safe(item) for item in value]
            if isinstance(value, float) and not math.isfinite(value):
                return None
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return value

        result = {
            "provider": self.name,
            "dataset": dataset,
            "rows": len(preview) if rows is None else rows,
            "columns": columns,
            "preview": safe(preview[:5]),
        }
        json.dumps(result, allow_nan=False)
        return result

    @bounded_request
    def get_instruments(self, asset_type: str = "stock") -> list[dict[str, Any]]:
        self._ensure_open()
        if self._config.mac_client is None:
            raise ProviderError("CONFIG_INVALID", "Instrument directory requires a MacClient")
        return get_mac_instruments(
            self._config.mac_client, asset_type, limit=self._config.universe_limit
        )

    @bounded_request
    def get_realtime(self) -> list[dict[str, Any]]:
        """Return a complete frozen stock/ETF snapshot or soft-fail with an empty list."""
        try:
            instruments = [
                *self.get_instruments("stock"),
                *self.get_instruments("etf"),
            ]
            return self._get_realtime_symbols([row["symbol"] for row in instruments])
        except Exception as exc:
            logger.warning("tdxman realtime snapshot failed: %s", exc)
            return []

    @bounded_request
    def get_realtime_indices(self, symbols: object) -> list[dict[str, Any]] | None:
        """Return requested index quotes; preserve the consumer cache on failure."""
        try:
            requested = normalize_symbols(symbols)
            if not requested:
                return []
            return self._get_realtime_symbols(requested)
        except Exception as exc:
            logger.warning("tdxman realtime index snapshot failed: %s", exc)
            return None

    @bounded_request
    def get_minute(
        self,
        symbols: object,
        start_time: object | None = None,
        end_time: object | None = None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: ChunkDone | None = None,
    ) -> Any:
        self._ensure_open()
        requested = normalize_symbols(symbols)
        start = self._minute_bound(start_time, "start_time")
        end = self._minute_bound(end_time, "end_time")
        if start is not None and end is not None and start > end:
            raise ProviderError("INVALID_ARGUMENT", "start_time must not be after end_time")
        if asset_type not in {"stock", "etf", "index"}:
            raise ProviderError("INVALID_ASSET_TYPE", f"Unsupported asset_type: {asset_type!r}")
        freq = str(freq).strip().lower().replace("min", "m")
        periods = {"1m": 7, "5m": 0, "15m": 1, "30m": 2, "60m": 3}
        if freq not in periods:
            raise ProviderError("UNSUPPORTED_FREQUENCY", f"Unsupported frequency: {freq!r}")
        if not requested:
            if on_chunk_done is not None:
                on_chunk_done(1, 1)
            return empty_minute()
        if self._config.mac_client is None:
            raise ProviderError("CONFIG_INVALID", "Minute data requires a MacClient")
        from .routing import read_mac_minute

        frames = []
        rows = 0
        for current, symbol in enumerate(requested, 1):
            self._ensure_open()
            frames.append(
                read_mac_minute(
                    self._config.mac_client, symbol, asset_type=asset_type,
                    period=periods[freq], start=start, end=end,
                    max_bars=self._config.online_max_bars_per_symbol,
                    latest_count=_minute_limit.get(),
                )
            )
            rows += frames[-1].height
            if rows > self._config.max_result_rows:
                raise ProviderError("RESULT_LIMIT", "Minute result exceeds configured row limit")
            if on_chunk_done is not None:
                on_chunk_done(current, len(requested))
        return polars().concat(frames) if frames else empty_minute()

    @bounded_request
    def get_intraday_batch(
        self, symbols: object, count: int = 300, asset_type: str = "stock"
    ) -> Any:
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ProviderError("INVALID_ARGUMENT", "count must be positive")
        today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
        end = today + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        token = _minute_limit.set(count)
        try:
            frame = self.get_minute(symbols, today, end, asset_type, "1m")
        finally:
            _minute_limit.reset(token)
        return frame.group_by("symbol", maintain_order=True).tail(count)

    @bounded_request
    def get_intraday_latest(self, symbols: object | None = None, count: int = 3) -> Any:
        """Return up to count current-day minute bars per stock/ETF.

        None selects the stock and ETF directory snapshot for this call.
        Each symbol uses a bounded latest-window request, including unchanged
        bars and the developing bar; callers merge by (symbol, datetime).
        This method does not schedule polling or guarantee a refresh cadence.
        """
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ProviderError("INVALID_ARGUMENT", "count must be a positive integer")
        if symbols is None:
            symbols = [
                *[row["symbol"] for row in self.get_instruments("stock")],
                *[row["symbol"] for row in self.get_instruments("etf")],
            ]
        return self.get_intraday_batch(symbols, count=count)

    @bounded_request
    def get_depth_batch(self, symbols: object) -> dict[str, dict[str, Any]]:
        """Fetch complete MAC five-level books in batches of at most 80 symbols."""
        self._ensure_open()
        requested = normalize_symbols(symbols)
        if not requested:
            return {}
        if self._config.mac_client is None:
            raise ProviderError("CONFIG_INVALID", "Depth data requires a MacClient")
        from tdxman.codec.bitmap import FieldBit, PresetField

        fields = PresetField.HANDICAP + FieldBit.SERVER_UPDATE_DATE + FieldBit.SERVER_UPDATE_TIME
        books: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(requested), 80):
            self._ensure_open()
            batch = requested[offset : offset + 80]
            stocks = [({"SZ": 0, "SH": 1, "BJ": 2}[s[-2:]], s.split(".")[0]) for s in batch]
            frame = self._config.mac_client.get_stock_quotes(stocks, fields=fields)
            if len(frame) != len(batch):
                raise ProviderError("INCOMPLETE_COVERAGE", "Depth batch is incomplete")
            received = set()
            for row in frame.to_dict("records"):
                exchange = {0: "SZ", 1: "SH", 2: "BJ"}.get(int(row["market"]))
                if exchange is None:
                    raise ProviderError("SCHEMA_MISMATCH", "Depth quote identity is invalid")
                symbol = normalize_symbols([f"{row['code']}.{exchange}"])[0]
                if symbol not in batch or symbol in received:
                    raise ProviderError("INCOMPLETE_COVERAGE", "Depth batch identity mismatch")
                received.add(symbol)
                books[symbol] = self._normalize_depth_row(row)
        if set(books) != set(requested):
            raise ProviderError("INCOMPLETE_COVERAGE", "Depth snapshot identity mismatch")
        return books

    def _get_realtime_symbols(self, symbols: list[str]) -> list[dict[str, Any]]:
        if self._config.mac_client is None:
            raise ProviderError("CONFIG_INVALID", "Realtime requires a MacClient")
        from tdxman.codec.bitmap import FieldBit, PresetField

        fields = (
            PresetField.BASIC
            + PresetField.VOLUME
            + FieldBit.SERVER_UPDATE_DATE
            + FieldBit.SERVER_UPDATE_TIME
        )
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(symbols), 80):
            self._ensure_open()
            batch = symbols[offset : offset + 80]
            stocks = [({"SZ": 0, "SH": 1, "BJ": 2}[s[-2:]], s.split(".")[0]) for s in batch]
            frame = self._config.mac_client.get_stock_quotes(stocks, fields=fields)
            if len(frame) != len(batch):
                raise ProviderError("INCOMPLETE_COVERAGE", "Realtime batch is incomplete")
            normalized = [normalize_realtime_row(row) for row in frame.to_dict("records")]
            if {row["symbol"] for row in normalized} != set(batch):
                raise ProviderError("INCOMPLETE_COVERAGE", "Realtime batch identity mismatch")
            rows.extend(normalized)
        if {row["symbol"] for row in rows} != set(symbols):
            raise ProviderError("INCOMPLETE_COVERAGE", "Realtime snapshot identity mismatch")
        logger.info("realtime_coverage expected=%d received=%d missing=0", len(symbols), len(rows))
        return rows

    @staticmethod
    def _minute_bound(value: object | None, name: str) -> pd.Timestamp | None:
        if value is None:
            return None
        try:
            parsed = pd.Timestamp(cast(Any, value))
        except (TypeError, ValueError) as exc:
            raise ProviderError("INVALID_ARGUMENT", f"Invalid {name}: {value!r}") from exc
        if pd.isna(parsed):
            raise ProviderError("INVALID_ARGUMENT", f"Invalid {name}: {value!r}")
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert("Asia/Shanghai").tz_localize(None)
        return cast(pd.Timestamp, parsed)

    @staticmethod
    def _normalize_depth_row(row: dict[str, Any]) -> dict[str, Any]:
        price_names = ["bid_price", "bid2_price", "bid3_price", "bid4_price", "bid5_price"]
        ask_price_names = ["ask_price", "ask2_price", "ask3_price", "ask4_price", "ask5_price"]
        volume_names = [
            "bid_volume",
            # Field bit 0x5d is parsed under its canonical board alias.
            "limit_up_count",
            "bid3_volume",
            "bid4_volume",
            # Field bit 0x88 is parsed under its canonical board alias.
            "up_count",
        ]
        ask_volume_names = [
            "ask_volume",
            # Field bit 0x5e is parsed under its canonical board alias.
            "limit_down_count",
            "ask3_volume",
            "ask4_volume",
            # Field bit 0x8b is parsed under its canonical board alias.
            "down_count",
        ]
        names = [*price_names, *ask_price_names, *volume_names, *ask_volume_names]
        if any(name not in row or row[name] is None for name in names):
            raise ProviderError("SCHEMA_MISMATCH", "Depth quote is missing a five-level field")
        try:
            bid_prices = [float(row[name]) for name in price_names]
            ask_prices = [float(row[name]) for name in ask_price_names]
            bid_volumes = [float(row[name]) for name in volume_names]
            ask_volumes = [float(row[name]) for name in ask_volume_names]
        except (TypeError, ValueError) as exc:
            raise ProviderError("BAD_DATA", "Depth quote has non-numeric levels") from exc
        values = [*bid_prices, *ask_prices, *bid_volumes, *ask_volumes]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ProviderError("BAD_DATA", "Depth quote has invalid levels")
        from .normalize import _mac_quote_timestamp

        timestamp = _mac_quote_timestamp(
            row.get("server_update_date"), row.get("server_update_time")
        )
        if timestamp is None:
            raise ProviderError("TIME_UNVERIFIED", "Depth quote has no complete server timestamp")
        return {
            "bid_prices": bid_prices,
            "bid_volumes": bid_volumes,
            "ask_prices": ask_prices,
            "ask_volumes": ask_volumes,
            "timestamp": timestamp,
        }

    def _ensure_open(self) -> None:
        check_budget()
        if self._closed:
            raise ProviderError("PROVIDER_CLOSED", "Provider is closed")
