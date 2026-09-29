"""Maintain the current A-share universe without deleting historical bars."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .pool import writer
from .store import catalog


def _is_a_share(market: str, code: str) -> bool:
    prefixes = {
        "SH": ("600", "601", "603", "605", "688"),
        "SZ": ("000", "001", "002", "003", "300", "301", "302"),
        "BJ": ("4", "8", "920"),
    }
    return code.startswith(prefixes[market])


def _entry(market: str, row: dict, asset_type: str) -> dict[str, str]:
    return {
        "symbol": str(row.get("code", "")),
        "market": market,
        "name": row.get("name"),
        "asset_type": asset_type,
    }


def fetch_a_share_universe():
    """Fetch all three A-share quote directories; never publish a partial result."""
    from tdxman.mac.client import MacClient
    from tdxman.mac.enums import Category, SortOrder, SortType

    def fetch_market(name):
        result = []
        category = getattr(Category, name)
        with MacClient(timeout=5, auto_reconnect=False, heartbeat_interval=0) as client:
            frame = client.get_stock_quotes_list(
                category, count=6000, sort_type=SortType.CODE, sort_order=SortOrder.ASC
            )
        if frame.empty:
            raise ValueError(f"{name} 证券目录为空")
        for row in frame.to_dict("records"):
            code = str(row.get("code", ""))
            if _is_a_share(name, code):
                result.append(_entry(name, row, "stock"))
        return result

    result = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch_market, name) for name in ("SH", "SZ", "BJ")]
        for future in as_completed(futures):
            result.extend(future.result())
    return sorted({row["symbol"]: row for row in result}.values(), key=lambda row: row["symbol"])


def fetch_etf_universe():
    """Fetch the current ETF directory used by the SQLite sync scope."""
    from tdxman.mac.client import MacClient

    from .etf_lists import collect

    with MacClient.from_best_host(
        timeout=10, auto_reconnect=False, heartbeat_interval=0
    ) as client:
        items = collect(client)[0]["etfs"]
    return [
        {
            "symbol": row["code"],
            "market": row["market"],
            "name": row["name"],
            "asset_type": "etf",
        }
        for row in items
    ]


def fetch_index_universe():
    """Fetch the complete live index directories required by the daily index store."""
    from tdxman.mac.client import MacClient

    from .index_lists import collect

    with MacClient.from_best_host(
        timeout=10, auto_reconnect=False, heartbeat_interval=0
    ) as client:
        return collect(client)


@writer
def _publish_universe(root, entries):
    from .securities import ensure_securities, publish_directory

    ensure_securities(root)
    kind = entries[0].get("asset_type", "stock") if entries else "stock"
    rows = [
        dict(code=e["symbol"], symbol=f"{e['symbol']}.{e['market']}",
             market=e["market"], name=e["name"])
        for e in entries
    ]
    result = publish_directory(root, rows, asset_type=kind)
    with catalog(root) as conn:
        covered = {row[0] for row in conn.execute("SELECT symbol FROM coverage").fetchall()}
    added = [entry for entry in entries if entry["symbol"] not in covered]
    return {
        "listed": len(entries),
        "added": added,
        "inactive": result["inactive"],
        "reactivated": [],
    }


def _refresh_universe(root: Path, fetcher):
    """Refresh the live directory and return new symbols needing initialization."""
    from .store import initialize

    initialize(root)
    try:
        entries = fetcher()
    except Exception:
        # A stale cached endpoint is retried after one fresh server selection.
        from tdxman.mac.client import MacClient

        MacClient.from_best_host(heartbeat_interval=0)
        entries = fetcher()
    return _publish_universe(root, entries)


def refresh_stock_universe(root: Path):
    return _refresh_universe(root, fetch_a_share_universe)


def refresh_etf_universe(root: Path):
    return _refresh_universe(root, fetch_etf_universe)


def refresh_index_universe(root: Path):
    """Publish the complete dynamic index directory only after all groups are staged."""
    from .index_lists import BENCHMARK_SYMBOLS
    from .securities import publish_index_directory

    try:
        collected, excluded = fetch_index_universe()
    except Exception:
        from tdxman.mac.client import MacClient

        MacClient.from_best_host(heartbeat_interval=0)
        collected, excluded = fetch_index_universe()
    benchmarks = {f"{code}.{market}" for market, code in BENCHMARK_SYMBOLS}
    result = publish_index_directory(Path(root), collected["indices"], benchmark_symbols=benchmarks)
    return {
        **result,
        "excluded": excluded,
        "categories": {
            category: sum(category in entry["source"] for entry in collected["indices"])
            for category in ("HY2", "GN", "FG", "ZS")
        },
    }


def universe_is_stale(root: Path, days: int = 7, asset_type: str | None = None) -> bool:
    """Avoid a full directory scan on every normal daily K-line repair."""
    with catalog(root) as conn:
        try:
            query = "SELECT max(updated_at) FROM securities"
            params = []
            if asset_type:
                query += " WHERE asset_type = ?"
                params.append(asset_type)
            latest = conn.execute(query, params).fetchone()[0]
            if "fetch_observations" in {r[0] for r in conn.execute("SHOW TABLES").fetchall()}:
                observation = conn.execute(
                    "SELECT max(observed_at) FROM fetch_observations WHERE "
                    "object_key LIKE ? AND status='ok'", [f"universe:{asset_type or '%'}"]
                ).fetchone()[0]
                if observation is not None:
                    # Observation timestamps are UTC; legacy last_seen is local time.
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    return (now - observation).days >= days
        except Exception:
            return True
    return latest is None or (datetime.now() - latest).days >= days


def pending_universe_symbols(root: Path, asset_type: str = "stock"):
    """Return active directory entries not yet initialized in the daily lake."""
    with catalog(root) as conn:
        try:
            rows = conn.execute(
                """SELECT s.code, s.market, s.name, s.asset_type FROM securities s
                LEFT JOIN coverage c ON c.symbol = s.code
                WHERE s.active AND s.asset_type = ? AND c.symbol IS NULL ORDER BY s.symbol""",
                [asset_type],
            ).fetchall()
        except Exception:
            return []
    return [
        {"symbol": row[0], "market": row[1], "name": row[2], "asset_type": row[3]} for row in rows
    ]
