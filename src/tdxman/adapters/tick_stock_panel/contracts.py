"""Stable contracts shared by the Tick Stock Panel adapter modules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


class ProviderError(ValueError):
    """Provider error with a stable code suitable for plugin diagnostics."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


ChunkDone = Callable[[int, int], None]
AssetType = Literal["stock", "etf", "index"]


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for the provider's first, local daily-data slice.

    ``pool`` is deliberately injected.  It keeps the adapter independent of a
    user-specific aspool path and lets the plugin construct the pool from its
    own settings later.
    """

    pool: Any | None = None
    daily_mode: Literal["local", "online"] = "local"
    standard_client: Any | None = None
    close_standard_client: bool = False
    mac_client: Any | None = None
    close_mac_client: bool = False
    online_page_size: int = 800
    online_max_bars_per_symbol: int = 8000
    universe_limit: int = 10000
    request_timeout: float = 300.0
    max_result_rows: int = 2000000


DAILY_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "amount")
MINUTE_COLUMNS = (
    "symbol",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Minimal config shape consumed by Tick Stock Panel's plugin loader."""

    datasets: dict[str, dict[str, bool]]
