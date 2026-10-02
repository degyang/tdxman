"""Record the input identity of newly selected or revised stock factors."""

import hashlib
import json
import sqlite3

ALGORITHM_VERSION = "selected-factor-v2"


def refresh_factor_lineage(conn, changed_rows, *, root=None):
    symbols = {
        r["key"][0]
        for r in changed_rows
        if (r["alias"], r["table"]) == ("adjustments", "stock_adjustment_factors")
    }
    for symbol in sorted(symbols):
        actions = conn.execute(
            "SELECT effective_date,source,source_key,category,payload_json "
            "FROM main.corporate_actions WHERE symbol=? ORDER BY effective_date,source,source_key",
            (symbol,),
        ).fetchall()
        anchors = conn.execute(
            "SELECT effective_date,source,source_key,source_cumulative_factor,payload_json "
            "FROM adjustments.stock_factor_anchors WHERE "
            "symbol=? ORDER BY effective_date,source,source_key",
            (symbol,),
        ).fetchall()
        if len(actions) + len(anchors) > 10000:
            raise ValueError("Factor lineage source budget exceeded")
        source = {"events": actions, "anchors": anchors}
        if root is not None:
            from .base_delta import evidence_directory

            directory = evidence_directory(root)
            for filename, query, field in (
                (
                    "index.sqlite",
                    "SELECT sha256 FROM evidence_index WHERE symbol=? ORDER BY sha256 LIMIT 10001",
                    "price_evidence",
                ),
                (
                    "coverage.sqlite",
                    "SELECT verified_start,as_of,source,payload_json "
                    "FROM event_coverage WHERE symbol=? ORDER BY verified_start,as_of,source "
                    "LIMIT 10001",
                    "event_coverage",
                ),
            ):
                path = directory / filename
                if path.exists():
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as evidence:
                        records = evidence.execute(query, (symbol,)).fetchall()
                    if len(records) > 10000:
                        raise ValueError("Factor lineage evidence budget exceeded")
                    source[field] = records
        for row in changed_rows:
            if (
                (row["alias"], row["table"]) != ("adjustments", "stock_adjustment_factors")
                or row["key"][0] != symbol
                or row["value"] is None
            ):
                continue
            day = row["key"][1]
            prior = conn.execute(
                "SELECT trade_date,close FROM main.daily_bars WHERE symbol=? AND trade_date<? "
                "AND low>0 AND low<=min(open,close) AND max(open,close)<=high "
                "ORDER BY trade_date DESC LIMIT 1",
                (symbol, day),
            ).fetchone()
            digest = hashlib.sha256(
                json.dumps(
                    dict(source, prior_close=prior, date=day), sort_keys=True, allow_nan=False
                ).encode()
            ).hexdigest()
            conn.execute(
                "UPDATE adjustments.stock_adjustment_factors SET input_hash=?,algorithm_version=? "
                "WHERE symbol=? AND effective_date=?",
                (digest, ALGORITHM_VERSION, symbol, day),
            )
