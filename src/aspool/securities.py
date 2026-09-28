"""Canonical stock and ETF directory stored in catalog.duckdb."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS securities (
    symbol VARCHAR PRIMARY KEY,
    code VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL,
    name VARCHAR,
    active BOOLEAN NOT NULL,
    listing_date DATE,
    delisting_date DATE,
    updated_at TIMESTAMP NOT NULL,
    CHECK (market IN ('SH','SZ','BJ')),
    CHECK (asset_type IN ('stock','etf')),
    CHECK (symbol = code || '.' || market)
)
"""


def _tables(conn) -> set[str]:
    return {row[0] for row in conn.execute("SHOW TABLES").fetchall()}


def ensure_securities(root: Path) -> dict[str, int]:
    """Create the canonical table and migrate legacy directory rows idempotently."""
    path = Path(root).resolve() / "catalog.duckdb"
    stamp = datetime.now(UTC).replace(tzinfo=None)
    with duckdb.connect(str(path)) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(DDL)
            before = conn.execute("SELECT count(*) FROM securities").fetchone()[0]
            tables = _tables(conn)
            if before == 0 and "universe" in tables:
                conn.execute(
                    """INSERT INTO securities
                    SELECT symbol || '.' || market, symbol, market,
                           coalesce(asset_type,'stock'), name, active,
                           NULL, NULL, ?
                    FROM universe
                    WHERE market IN ('SH','SZ','BJ')
                      AND length(symbol)=6
                      AND coalesce(asset_type,'stock') IN ('stock','etf')
                    ON CONFLICT(symbol) DO UPDATE SET
                      code=excluded.code, market=excluded.market,
                      asset_type=excluded.asset_type,
                      name=coalesce(excluded.name,securities.name),
                      active=excluded.active
                    WHERE securities.code IS DISTINCT FROM excluded.code
                       OR securities.market IS DISTINCT FROM excluded.market
                       OR securities.asset_type IS DISTINCT FROM excluded.asset_type
                       OR securities.name IS DISTINCT FROM coalesce(excluded.name,securities.name)
                       OR securities.active IS DISTINCT FROM excluded.active""",
                    [stamp],
                )
            if before == 0 and "security_lifecycle" in tables:
                # Lifecycle can contain rows not present in a current directory.
                lifecycle_columns = {
                    row[0] for row in conn.execute("DESCRIBE security_lifecycle").fetchall()
                }
                lifecycle_name = "l.name" if "name" in lifecycle_columns else "NULL"
                conn.execute(
                    f"""INSERT INTO securities
                    SELECT l.symbol, split_part(l.symbol,'.',1), split_part(l.symbol,'.',2),
                           'stock', {lifecycle_name}, false, l.listing_date, l.delisting_date, ?
                    FROM security_lifecycle l
                    WHERE length(l.symbol)=9 AND substr(l.symbol,7,1)='.'
                      AND split_part(l.symbol,'.',2) IN ('SH','SZ','BJ')
                    ON CONFLICT(symbol) DO UPDATE SET
                      name=coalesce(securities.name,excluded.name),
                      listing_date=coalesce(securities.listing_date,excluded.listing_date),
                      delisting_date=coalesce(securities.delisting_date,excluded.delisting_date)
                    WHERE (securities.name IS NULL AND excluded.name IS NOT NULL)
                       OR (securities.listing_date IS NULL AND excluded.listing_date IS NOT NULL)
                       OR (securities.delisting_date IS NULL
                           AND excluded.delisting_date IS NOT NULL)""",
                    [stamp],
                )
            after = conn.execute("SELECT count(*) FROM securities").fetchone()[0]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return {"before": int(before), "after": int(after), "added": int(after - before)}


def publish_directory(
    root: Path,
    entries: list[dict[str, object]],
    *,
    asset_type: str,
    complete_markets: set[str] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, int]:
    """Publish complete market observations; only actual business changes touch updated_at."""
    if asset_type not in {"stock", "etf"}:
        raise ValueError("Unsupported asset type")
    observed_at = observed_at or datetime.now(UTC).replace(tzinfo=None)
    normalized: dict[str, tuple[str, str, str]] = {}
    for entry in entries:
        code = str(entry["code"])
        market = str(entry["market"])
        name = str(entry["name"]).strip()
        symbol = str(entry.get("symbol") or f"{code}.{market}")
        if symbol != f"{code}.{market}" or len(code) != 6 or not code.isdigit():
            raise ValueError(f"Invalid directory identity: {symbol}")
        if market not in {"SH", "SZ", "BJ"} or not name or symbol in normalized:
            raise ValueError(f"Invalid or duplicate directory entry: {symbol}")
        normalized[symbol] = (code, market, name)
    if not normalized:
        raise ValueError("Empty directory cannot be published")
    ensure_securities(root)
    complete_markets = complete_markets or {row[1] for row in normalized.values()}
    with duckdb.connect(str(Path(root).resolve() / "catalog.duckdb")) as conn:
        conn.execute("BEGIN")
        try:
            previous = {
                r[0]: r[1:]
                for r in conn.execute(
                    "SELECT symbol,code,market,name,active FROM securities WHERE asset_type=?",
                    [asset_type],
                ).fetchall()
            }
            added = changed = unchanged = 0
            for symbol, (code, market, name) in normalized.items():
                old = previous.get(symbol)
                current = (code, market, name, True)
                if old is None:
                    added += 1
                elif old == current:
                    unchanged += 1
                    continue
                else:
                    changed += 1
                conn.execute(
                    """INSERT INTO securities
                    (symbol,code,market,asset_type,name,active,updated_at)
                    VALUES (?,?,?,?,?,true,?)
                    ON CONFLICT(symbol) DO UPDATE SET code=excluded.code,
                      market=excluded.market,asset_type=excluded.asset_type,
                      name=excluded.name,active=true,updated_at=excluded.updated_at""",
                    [symbol, code, market, asset_type, name, observed_at],
                )
            inactive = 0
            for symbol, old in previous.items():
                if old[1] in complete_markets and symbol not in normalized and old[3]:
                    conn.execute(
                        "UPDATE securities SET active=false,updated_at=? WHERE symbol=?",
                        [observed_at, symbol],
                    )
                    inactive += 1
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return dict(listed=len(normalized), added=added, changed=changed,
                unchanged=unchanged, inactive=inactive)


def active_securities(root: Path, asset_type: str, markets: set[str] | None = None):
    ensure_securities(root)
    sql = (
        "SELECT symbol,code,market,name,listing_date,delisting_date "
        "FROM securities WHERE active AND asset_type=?"
    )
    params: list[object] = [asset_type]
    if markets:
        sql += " AND market IN (" + ",".join("?" for _ in markets) + ")"
        params.extend(sorted(markets))
    sql += " ORDER BY symbol"
    with duckdb.connect(str(Path(root).resolve() / "catalog.duckdb"), read_only=True) as conn:
        return conn.execute(sql, params).fetchall()
