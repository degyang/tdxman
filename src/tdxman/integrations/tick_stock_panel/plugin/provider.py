"""Thin, host-independent entry point for the complete tdxman adapter."""

from __future__ import annotations

import os
from pathlib import Path

from tdxman.adapters.tick_stock_panel.contracts import ProviderConfig, ProviderError
from tdxman.adapters.tick_stock_panel.provider import TdxmanProvider as Adapter
from tdxman.adapters.tick_stock_panel.transport import ManagedClient


def _settings():
    mode = os.getenv("TDXMAN_TICK_STOCK_PANEL_MODE", "online").lower()
    if mode not in {"online", "local"}:
        raise ProviderError("CONFIG_INVALID", "Mode must be online or local")
    root = Path(os.getenv("TDXMAN_ASPOOL_ROOT", "~/.aspool")).expanduser()
    if mode == "local" and not (root / "catalog.duckdb").is_file():
        raise ProviderError("CONFIG_INVALID", "未找到 aspool catalog.duckdb")
    return mode, root


def availability() -> tuple[bool, str]:
    """Check configuration without connecting or creating a data pool."""
    try:
        from tdxman.adapters.tick_stock_panel.normalize import polars
        from tdxman.client import TdxClient
        from tdxman.mac.client import MacClient

        polars()
        mode, _ = _settings()
        if not callable(TdxClient) or not callable(MacClient):
            raise ProviderError("DEPENDENCY_MISSING", "tdxman clients unavailable")
        return True, f"ready ({mode}; network checked on request)"
    except (ImportError, ProviderError) as exc:
        return False, str(exc)


class TdxmanProvider(Adapter):
    # The consumer uses repair rounds until market-wide incremental cost is verified.
    get_intraday_latest = None

    def __init__(self, config=None):
        if config is None:
            from aspool import DataPool
            from tdxman.client import TdxClient
            from tdxman.mac.client import MacClient

            mode, root = _settings()
            config = ProviderConfig(
                daily_mode=mode,
                pool=DataPool(root) if mode == "local" else None,
                standard_client=ManagedClient(TdxClient) if mode == "online" else None,
                mac_client=ManagedClient(MacClient),
                close_standard_client=True,
                close_mac_client=True,
            )
        super().__init__(config)
