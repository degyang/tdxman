"""Frozen MAC directory snapshots for Tick Stock Panel."""

from __future__ import annotations

from typing import Any

from tdxman.codec.bitmap import PresetField
from tdxman.mac.enums import Category, SortOrder, SortType

from .contracts import ProviderError
from .diagnostics import check_budget

_EXCHANGES = {0: "SZ", 1: "SH", 2: "BJ"}
_CATEGORIES = {
    "stock": (Category.A, Category.BJ),
    "etf": (Category.ETF,),
    "index": (Category.ZS,),
}


def get_mac_instruments(client: Any, asset_type: str, *, limit: int) -> list[dict[str, Any]]:
    """Fetch a code-sorted, de-duplicated directory without dynamic ranking drift."""
    categories = _CATEGORIES.get(asset_type)
    if categories is None:
        raise ProviderError("INVALID_ASSET_TYPE", f"Unsupported asset_type: {asset_type!r}")
    if limit < 1:
        raise ProviderError("CONFIG_INVALID", "universe_limit must be positive")

    records: dict[str, dict[str, Any]] = {}
    for category in categories:
        check_budget()
        pages = []
        seen = set()
        offset = 0
        try:
            while offset < limit:
                check_budget()
                size = min(80, limit - offset)
                page = client.get_stock_quotes_list(
                    category, start=offset, count=size,
                    sort_type=SortType.CODE, sort_order=SortOrder.ASC,
                    fields=PresetField.NONE,
                )
                if page.empty:
                    break
                if not {"market", "code", "name"}.issubset(page.columns):
                    raise ProviderError("SCHEMA_MISMATCH", "Instrument directory fields changed")
                identities = list(zip(page["market"], page["code"], strict=True))
                if len(set(identities)) != len(identities) or seen.intersection(identities):
                    raise ProviderError("INCOMPLETE_COVERAGE", "Directory pagination drifted")
                seen.update(identities)
                pages.append(page)
                offset += len(page)
                if len(page) < size:
                    break
            if offset >= limit:
                raise ProviderError("INCOMPLETE_COVERAGE", "Directory reached configured limit")
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "UPSTREAM_UNAVAILABLE", "Instrument directory request failed"
            ) from exc
        import pandas as pd
        frame = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()
        required = {"market", "code", "name"}
        if not frame.empty and not required.issubset(frame.columns):
            raise ProviderError("SCHEMA_MISMATCH", "Instrument directory fields changed")
        for row in frame.to_dict("records"):
            exchange = _EXCHANGES.get(int(row["market"]))
            code = str(row["code"]).strip()
            if exchange is None or not code.isdigit():
                raise ProviderError("SCHEMA_MISMATCH", "Instrument identity is invalid")
            symbol = f"{code}.{exchange}"
            records[symbol] = {
                "symbol": symbol,
                "name": str(row.get("name") or ""),
                "code": code,
                "exchange": exchange,
                "region": "CN",
                "type": asset_type,
                "ext": {
                    "listing_date": None,
                    "total_shares": None,
                    "float_shares": None,
                    "tick_size": None,
                    "limit_up": None,
                    "limit_down": None,
                },
            }
    return [records[key] for key in sorted(records)]
