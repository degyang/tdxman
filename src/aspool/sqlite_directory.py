"""Current TDX directories and dated listing metadata for SQLite daily updates."""

from bisect import bisect_right
from datetime import datetime

import duckdb

from tdxman.codec.bitmap import PresetField
from tdxman.exceptions import TdxError
from tdxman.mac.enums import Category, SortOrder, SortType
from tdxman.models.enums import Market

from .source_retry import EmptySourceResponse


def read_directory(session, *, report):
    """Stage all markets before publishing; a failed market is never a deletion."""
    rows = {}
    report.update(markets={}, failed=[])
    for market in (Market.SH, Market.SZ, Market.BJ):

        def request(client):
            found = {}
            for offset in range(0, 10000, 80):
                frame = client.get_stock_quotes_list(
                    Category[market.name],
                    start=offset,
                    count=80,
                    sort_type=SortType.CODE,
                    sort_order=SortOrder.ASC,
                    fields=PresetField.NONE,
                )
                if frame.empty:
                    if not found:
                        raise EmptySourceResponse(f"Empty {market.name} directory")
                    return found
                for row in frame.to_dict("records"):
                    code = str(row.get("code", ""))
                    name = row.get("name")
                    if (
                        row.get("market") != int(market)
                        or len(code) != 6
                        or not code.isascii()
                        or not code.isdigit()
                        or not isinstance(name, str)
                        or not name.strip()
                    ):
                        raise ValueError("Invalid directory identity/name")
                    symbol = f"{code}.{market.name}"
                    if symbol in found:
                        raise ValueError("Directory pagination repeated a security")
                    found[symbol] = name.strip()
                if len(frame) < 80:
                    return found
            raise ValueError("Directory exceeded 10,000 securities per market")

        try:
            entries = session.read(request, label=f"directory:{market.name}")
        except (OSError, ValueError, TdxError) as exc:
            report["failed"].append(dict(market=market.name, error=str(exc)))
            continue
        report["markets"][market.name] = len(entries)
        rows.update(entries)
    return rows


def publish_directory(root, rows, *, day):
    """Publish the successfully read markets to the canonical securities table."""
    from .securities import publish_directory as publish

    entries = [
        dict(symbol=symbol, code=symbol.split(".")[0], market=symbol.split(".")[1], name=name)
        for symbol, name in rows.items()
    ]
    return publish(
        root,
        entries,
        asset_type="stock",
        complete_markets={entry["market"] for entry in entries},
        observed_at=datetime.combine(day, datetime.min.time()),
    )


def listing_metadata(root, symbols, session, *, day, report):
    """Fetch only missing IPO dates; never overwrite a known date with an empty value."""
    from .securities import ensure_securities

    ensure_securities(root)
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
        lifecycle = {
            s: (a, b)
            for s, a, b in conn.execute(
                "SELECT symbol,listing_date,delisting_date FROM securities WHERE asset_type='stock'"
            ).fetchall()
        }
    report.update(fetched=[], failed=[])
    updates = []
    for symbol in symbols:
        if lifecycle.get(symbol, (None, None))[0] is not None:
            continue
        code, market = symbol.split(".")

        def fetch(client):
            frame = client.get_finance_info(Market[market], code)
            if frame.empty:
                raise EmptySourceResponse("Empty listing metadata")
            row = frame.iloc[0]
            if str(row.get("code")) != code:
                raise ValueError("Wrong listing metadata identity")
            try:
                return datetime.strptime(str(int(row["ipo_date"])), "%Y%m%d").date()
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                raise ValueError("IPO date unavailable") from exc

        try:
            listed = session.read(fetch, label="listing:" + symbol)
        except (OSError, ValueError, TdxError) as exc:
            report["failed"].append(dict(symbol=symbol, error=str(exc)))
            continue
        lifecycle[symbol] = (listed, lifecycle.get(symbol, (None, None))[1])
        updates.append((listed, datetime.combine(day, datetime.min.time()), symbol))
        report["fetched"].append(symbol)
    if updates:
        with duckdb.connect(str(root / "catalog.duckdb")) as conn:
            conn.execute("BEGIN")
            conn.executemany(
                "UPDATE securities SET listing_date=?,updated_at=? "
                "WHERE symbol=? AND listing_date IS NULL",
                updates,
            )
            conn.execute("COMMIT")
    return lifecycle


def listing_ages(lifecycle, sessions, *, start, end):
    """Exact session counts only when the observed calendar includes the IPO session."""
    days = sorted(sessions)
    known = set(days)
    targets = [d for d in days if start <= d <= end]
    result = {}
    for symbol, (listing, _) in lifecycle.items():
        if listing is None or listing.isoformat() not in known:
            continue
        first = listing.isoformat()
        offset = bisect_right(days, first) - 1
        result[symbol] = {d: bisect_right(days, d) - offset for d in targets if d >= first}
    return result
