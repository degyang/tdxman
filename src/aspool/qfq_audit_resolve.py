"""Resolve observed limit conflicts with dated source facts and canonical replay."""

import json
import sqlite3

from .qfq_audit import save
from .sqlite_qfq import PRICES, valid_prices


def resolve(root, out, start, end, recovery):
    import baostock as bs

    from .base_delta import _checksum, _read, apply_delta
    from .pool import pool_lock
    from .sqlite_publication import assert_published

    if recovery is None:
        raise ValueError("A verified recovery point is required")
    manifest = json.loads((recovery / "recovery.json").read_text())
    from pathlib import Path

    if not Path(manifest["root"]).samefile(root) or not manifest.get("public_read_verified"):
        raise ValueError("Recovery point does not match this pool")
    assert_published(root)
    issues = json.loads((out / "anomalies.json").read_text())
    targets = {(r["symbol"], r["date"]) for r in issues if r["reason"] == "price_outside_limits"}
    # Force the historical suffix of STAR CDRs through the corrected shared rule.
    with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        targets.update(
            conn.execute(
                "SELECT symbol,min(trade_date) FROM daily_bars "
                "WHERE symbol>='689000.SH' AND symbol<='689999.SH' AND trade_date BETWEEN ? AND ? "
                "GROUP BY symbol",
                (start, end),
            ).fetchall()
        )
    source_file = out / "conflict-sources.json"
    observations = json.loads(source_file.read_text()) if source_file.exists() else []
    observed = {(r["symbol"], r["date"]) for r in observations}
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(login.error_msg)
    try:
        for symbol, day in sorted(targets - observed):
            code, market = symbol.split(".")
            query = bs.query_history_k_data_plus(
                market.lower() + "." + code,
                "date,code,open,high,low,close,preclose,volume,tradestatus,isST",
                start_date=day,
                end_date=day,
                frequency="d",
                adjustflag="3",
            )
            while query.error_code == "0" and query.next():
                r = dict(zip(query.fields, query.get_row_data()))
                if r["date"] != day or r["code"] != market.lower() + "." + code:
                    raise ValueError("Source identity mismatch")
                r["symbol"] = symbol
                observations.append(r)
            if query.error_code != "0":
                raise RuntimeError(query.error_msg)
            save(source_file, observations)
    finally:
        bs.logout()
    changes = []
    rejected = []
    with pool_lock(root, write=True):
        with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
            for r in observations:
                symbol, day = r["symbol"], r["date"]
                if (symbol, day) not in targets:
                    continue
                raw = conn.execute(
                    "SELECT open,high,low,close FROM daily_bars WHERE symbol=? AND trade_date=?",
                    (symbol, day),
                ).fetchone()
                prices = {k: float(r[k]) for k in PRICES}
                if (
                    not raw
                    or not valid_prices(prices, positive=True)
                    or any(v is None or abs(v - prices[k]) > 0.0051 for k, v in zip(PRICES, raw))
                ):
                    rejected.append(
                        dict(symbol=symbol, date=day, reason="source_price_disagreement")
                    )
                    continue
                fields, keys, current = _read(root, "stock-source-facts", day, day, [symbol], 100)
                before = current.get((symbol, day))
                if before is None or not r.get("preclose") or float(r["preclose"]) <= 0:
                    rejected.append(
                        dict(symbol=symbol, date=day, reason="source_reference_unavailable")
                    )
                    continue
                after = dict(
                    before,
                    source_pre_close=float(r["preclose"]),
                    source_pre_close_source="baostock:history",
                    source_is_st=int(r["isST"]),
                    source_is_st_source="baostock:history",
                )
                if before != after:
                    changes.append(dict(key=[symbol, day], before=before, after=after))
        receipts = []
        symbols = sorted({r["key"][0] for r in changes})
        for offset in range(0, len(symbols), 40):
            batch = symbols[offset : offset + 40]
            package = dict(
                format="aspool-base-delta-v1",
                target=str(root),
                window=dict(start=start, end=end, symbols=batch),
                datasets=[
                    dict(
                        name="stock-source-facts",
                        fields=fields,
                        keys=keys,
                        changes=[r for r in changes if r["key"][0] in batch],
                    )
                ],
            )
            package["sha256"] = _checksum(package)
            path = out / f"conflict-facts-delta-{offset // 40:03d}.json"
            save(path, package)
            receipts.append(apply_delta(root, path))
            print(
                json.dumps(dict(resolved_symbols=offset + len(batch), total_symbols=len(symbols))),
                flush=True,
            )
    result = dict(
        targets=len(targets),
        source_rows=len(observations),
        changed_facts=len(changes),
        rejected=rejected,
        publications=receipts,
    )
    save(out / "conflict-resolution.json", result)
    return result
