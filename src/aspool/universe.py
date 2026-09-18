"""Maintain the current A-share universe without deleting historical bars."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .pool import writer
from .store import catalog


def _is_a_share(market: str, code: str) -> bool:
    prefixes = {
        "SH": ("600", "601", "603", "605", "688"),
        "SZ": ("000", "001", "002", "003", "300", "301"),
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
    """Load the reviewed ETF whitelist; live discovery remains script-owned."""
    from .etf_lists import load_etfs

    try:
        return [
            {
                "symbol": row["code"],
                "market": row["market"],
                "name": row["name"],
                "asset_type": "etf",
            }
            for row in load_etfs()
        ]
    except FileNotFoundError:
        pass

    # A source checkout without the generated whitelist can still bootstrap it
    # once; the maintenance script should then be used for reviewed updates.
    from tdxman.mac.client import MacClient
    from tdxman.mac.enums import Category, SortOrder, SortType

    with MacClient(timeout=5, auto_reconnect=False, heartbeat_interval=0) as client:
        frame = client.get_stock_quotes_list(
            Category.ETF, count=6000, sort_type=SortType.CODE, sort_order=SortOrder.ASC
        )
    if frame.empty:
        raise ValueError("ETF 证券目录为空")
    markets = {0: "SZ", 1: "SH"}
    result = []
    for row in frame.to_dict("records"):
        market = markets.get(row.get("market"))
        code = str(row.get("code", ""))
        if market in {"SH", "SZ"} and code.isdigit() and len(code) == 6:
            result.append(_entry(market, row, "etf"))
    if not result:
        raise ValueError("ETF 证券目录没有可用的沪深代码")
    return sorted(result, key=lambda row: (row["market"], row["symbol"]))


@writer
def _publish_universe(root, entries):
    now = datetime.now()
    current = {entry["symbol"]: entry for entry in entries}
    with catalog(root) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS universe (
            symbol VARCHAR PRIMARY KEY, market VARCHAR NOT NULL, name VARCHAR,
            active BOOLEAN NOT NULL, first_seen TIMESTAMP NOT NULL,
            last_seen TIMESTAMP NOT NULL, source VARCHAR NOT NULL,
            asset_type VARCHAR NOT NULL DEFAULT 'stock')""")
        conn.execute(
            "ALTER TABLE universe ADD COLUMN IF NOT EXISTS asset_type VARCHAR DEFAULT 'stock'"
        )
        previous = {
            row[0]: {
                "market": row[1],
                "name": row[2],
                "active": row[3],
                "asset_type": row[4] or "stock",
            }
            for row in conn.execute(
                "SELECT symbol, market, name, active, asset_type FROM universe"
            ).fetchall()
        }
        conn.execute("BEGIN")
        try:
            for entry in entries:
                conn.execute(
                    """INSERT INTO universe VALUES (?, ?, ?, true, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET market=excluded.market, name=excluded.name,
                    active=true, last_seen=excluded.last_seen, source=excluded.source,
                    asset_type=excluded.asset_type""",
                    [
                        entry["symbol"],
                        entry["market"],
                        entry["name"],
                        now,
                        now,
                        f"tdx:{entry.get('asset_type', 'stock')}-list",
                        entry.get("asset_type", "stock"),
                    ],
                )
            asset_types = {entry.get("asset_type", "stock") for entry in entries}
            missing = sorted(
                symbol
                for symbol, prior in previous.items()
                if prior["asset_type"] in asset_types and symbol not in current
            )
            if missing:
                conn.execute(
                    "UPDATE universe SET active=false WHERE symbol IN (SELECT unnest(?))", [missing]
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        covered = {row[0] for row in conn.execute("SELECT symbol FROM coverage").fetchall()}
    added = [entry for entry in entries if entry["symbol"] not in covered]
    return {
        "listed": len(entries),
        "added": added,
        "inactive": missing,
        "reactivated": sorted(
            entry["symbol"]
            for entry in entries
            if previous.get(entry["symbol"], {}).get("active") is False
        ),
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


def universe_is_stale(root: Path, days: int = 7, asset_type: str | None = None) -> bool:
    """Avoid a full directory scan on every normal daily K-line repair."""
    with catalog(root) as conn:
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info('universe')").fetchall()}
            if "asset_type" not in columns:
                return True
            query = "SELECT max(last_seen) FROM universe"
            params = []
            if asset_type:
                query += " WHERE asset_type = ?"
                params.append(asset_type)
            latest = conn.execute(query, params).fetchone()[0]
        except Exception:
            return True
    return latest is None or (datetime.now() - latest).days >= days


def pending_universe_symbols(root: Path, asset_type: str = "stock"):
    """Return active directory entries not yet initialized in the daily lake."""
    with catalog(root) as conn:
        try:
            rows = conn.execute(
                """SELECT u.symbol, u.market, u.name, u.asset_type FROM universe u
                LEFT JOIN coverage c ON c.symbol = u.symbol
                WHERE u.active AND u.asset_type = ? AND c.symbol IS NULL ORDER BY u.symbol""",
                [asset_type],
            ).fetchall()
        except Exception:
            return []
    return [
        {"symbol": row[0], "market": row[1], "name": row[2], "asset_type": row[3]} for row in rows
    ]
