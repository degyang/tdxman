"""Offline HY2/GN member-return aggregates with immutable snapshot provenance."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

from .api_contract import DataPoolError
from .sqlite_canonical import canonical_writer
from .sqlite_daily_derived import finite
from .sqlite_market_summary import SCOPES

KINDS = ("industry", "concept")
CLASSIFICATION = "tdx_hy2_gn"
MAX_BOARDS = 10_000
MAX_MEMBERS = 1_000_000
FIELDS = (
    "date",
    "scope",
    "classification",
    "kind",
    "board_id",
    "board_name",
    "member_count",
    "trading_member_count",
    "valid_return_count",
    "avg_return",
    "membership_as_of",
    "membership_basis",
    "snapshot_id",
    "updated_at",
)


def available(conn):
    return bool(conn.execute("PRAGMA table_info(board_daily)").fetchall())


def _stamp(old=0):
    return max(time.time_ns() // 1000, old + 1)


def _put(conn, table, keys, values):
    names = list(values)
    where = " AND ".join(name + "=?" for name in keys)
    prior = conn.execute(
        "SELECT " + ",".join(names) + ",updated_at FROM " + table + " WHERE " + where,
        tuple(values[name] for name in keys),
    ).fetchone()
    if prior is not None and tuple(values.values()) == prior[:-1]:
        return 0
    complete = dict(values, updated_at=_stamp(prior[-1] if prior else 0))
    conn.execute(
        "INSERT INTO "
        + table
        + "("
        + ",".join(complete)
        + ") VALUES ("
        + ",".join("?" for _ in complete)
        + ") ON CONFLICT("
        + ",".join(keys)
        + ") DO UPDATE SET "
        + ",".join(name + "=excluded." + name for name in complete if name not in keys),
        tuple(complete.values()),
    )
    return 1


@canonical_writer("board_update")
def publish_board_snapshot(conn, *, kind, boards, as_of, basis="current_snapshot"):
    """Publish one fully fetched category. A failure must never replace its members."""
    if conn.in_transaction or kind not in KINDS or basis != "current_snapshot":
        raise ValueError("Invalid snapshot publication")
    normalized = []
    total_members = 0
    for board in boards:
        members = sorted(set(board["members"]))
        total_members += len(members)
        if len(normalized) >= MAX_BOARDS or total_members > MAX_MEMBERS:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Board snapshot budget exceeded")
        if any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) for symbol in members):
            raise ValueError("Invalid member symbol")
        normalized.append(
            dict(
                board_id=str(board["board_id"]),
                board_name=str(board["board_name"]),
                members=members,
            )
        )
    normalized.sort(key=lambda item: item["board_id"])
    if len({item["board_id"] for item in normalized}) != len(normalized):
        raise ValueError("Duplicate board or excessive board count")
    identity = hashlib.sha256(
        json.dumps([kind, basis, normalized], sort_keys=True).encode()
    ).hexdigest()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT snapshot_id,status FROM board_sync_state WHERE kind=?", (kind,)
        ).fetchone()
        if not conn.execute(
            "SELECT 1 FROM board_snapshot_sets WHERE snapshot_id=? AND kind=?", (identity, kind)
        ).fetchone():
            for board in normalized:
                conn.execute(
                    "INSERT INTO board_snapshots VALUES (?,?,?,?,?)",
                    (
                        identity,
                        kind,
                        board["board_id"],
                        board["board_name"],
                        json.dumps(board["members"], separators=(",", ":")),
                    ),
                )
            conn.execute(
                "INSERT INTO board_snapshot_sets VALUES (?,?,?,?,?,?)",
                (identity, kind, as_of, basis, len(normalized), _stamp()),
            )
        # Identical successful snapshots are true no-ops; preserve collection evidence.
        if prior != (identity, "ready"):
            _put(
                conn,
                "board_sync_state",
                ("kind",),
                dict(
                    kind=kind, status="ready", snapshot_id=identity, attempted_at=as_of, error=None
                ),
            )
    return identity


@canonical_writer("board_update")
def record_board_failure(conn, *, kind, as_of, error):
    if conn.in_transaction or kind not in KINDS:
        raise ValueError("Invalid failure publication")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT snapshot_id FROM board_sync_state WHERE kind=?", (kind,)
        ).fetchone()
        _put(
            conn,
            "board_sync_state",
            ("kind",),
            dict(
                kind=kind,
                status="failed",
                snapshot_id=prior[0] if prior else None,
                attempted_at=as_of,
                error=str(error)[:500],
            ),
        )


def fetch_board_category(client, kind):
    """Use stable CODE pagination without quote-dependent exclusion filters."""
    from tdxman.mac.enums import BoardType, SortOrder, SortType

    from .source_retry import EmptySourceResponse
    from .universe import _is_a_share

    if kind not in KINDS:
        raise ValueError("Unsupported board kind")
    listing = client.get_board_list(BoardType.HY2 if kind == "industry" else BoardType.GN)
    if listing.empty:
        raise EmptySourceResponse("Empty board directory")
    if len(listing) >= MAX_BOARDS:
        raise ValueError("Truncated board directory")
    result, seen = [], set()
    total_members = 0
    for row in listing.to_dict("records"):
        board_id = str(row.get("code", row.get("symbol", "")))
        if not board_id or board_id in seen:
            raise ValueError("Missing or duplicated board identity")
        seen.add(board_id)
        members = client.get_board_members(
            board_id, sort_type=SortType.CODE, sort_order=SortOrder.ASC
        )
        if members.empty:
            raise EmptySourceResponse("Empty board membership: " + board_id)
        if len(members) >= 100000:
            raise ValueError("Truncated board membership: " + board_id)
        symbols = []
        for member in members.to_dict("records"):
            code = str(member.get("code", member.get("symbol", "")))
            if "." in code:
                match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", code)
                if match and _is_a_share(match[2], match[1]):
                    symbols.append(code)
            elif re.fullmatch(r"\d{6}", code):
                exchange = next(
                    (item for item in ("SH", "SZ", "BJ") if _is_a_share(item, code)), None
                )
                if exchange:
                    symbols.append(code + "." + exchange)
        symbols = sorted(set(symbols))
        total_members += len(symbols)
        if total_members > MAX_MEMBERS:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Board member budget exceeded")
        result.append(
            dict(board_id=board_id, board_name=row.get("name", board_id), members=symbols)
        )
    return result


def refresh_board_snapshots(root, source_session):
    """Reuse the existing daily source session; each category can fail independently."""
    from .pool import pool_lock
    from .sqlite_stock_store import stock_connection

    result = {}
    as_of = datetime.now(timezone.utc).isoformat()
    with stock_connection(root) as conn:
        if not available(conn):
            return dict(status="not_provided", reason="explicit schema maintenance required")
    for kind in KINDS:
        try:
            boards = source_session.read(
                lambda client: fetch_board_category(client, kind), label="boards:" + kind
            )
        except Exception as exc:
            with pool_lock(root, write=True), stock_connection(root, read_only=False) as conn:
                record_board_failure(conn, kind=kind, as_of=as_of, error=exc)
            result[kind] = dict(status="failed", error=str(exc))
            continue
        with pool_lock(root, write=True), stock_connection(root, read_only=False) as conn:
            identity = publish_board_snapshot(conn, kind=kind, boards=boards, as_of=as_of)
        result[kind] = dict(status="ready", board_count=len(boards), snapshot_id=identity)
    return result


def recompute_board_daily(conn, *, day, changed_symbols=None, replace_snapshot=False):
    """Calculate one date, keeping its prior snapshot unless explicitly replaced."""
    if not available(conn) or not conn.execute("SELECT 1 FROM board_sync_state LIMIT 1").fetchone():
        return 0
    if not conn.in_transaction:
        raise ValueError("Outer publication transaction required")
    current = {}
    for symbol, status, st, close, pre, limit_status in conn.execute(
        "SELECT f.symbol,f.calc_status,f.is_st,b.close,f.pre_close,f.limit_status "
        "FROM daily_features f "
        "LEFT JOIN daily_bars b USING(symbol,trade_date) WHERE f.trade_date=?",
        (day,),
    ):
        if (
            status == "TRADED"
            and limit_status in ("KNOWN", "NO_LIMIT", "UNKNOWN")
            and finite(close, positive=True)
        ):
            value = (
                float((Decimal(str(close)) - Decimal(str(pre))) / Decimal(str(pre)))
                if finite(close, positive=True) and finite(pre, positive=True)
                else None
            )
            current[symbol] = (st, value if finite(value) else None)
    changed = 0
    for scope in SCOPES:
        universe = {
            symbol: value
            for symbol, (st, value) in current.items()
            if scope == "all_stocks" or st != 1
        }
        for kind in KINDS:
            old = conn.execute(
                "SELECT snapshot_id FROM board_daily_status WHERE date=? AND scope=? AND kind=?",
                (day, scope, kind),
            ).fetchone()
            sync = conn.execute(
                "SELECT snapshot_id,status FROM board_sync_state WHERE kind=?", (kind,)
            ).fetchone()
            identity = (
                old[0] if old and old[0] and not replace_snapshot else sync[0] if sync else None
            )
            status = "not_provided" if not sync else "failed" if identity is None else "ready"
            mapped, processed, expected = set(), 0, None
            if identity:
                meta = conn.execute(
                    "SELECT membership_as_of,membership_basis,expected_board_count FROM "
                    "board_snapshot_sets "
                    "WHERE snapshot_id=? AND kind=?",
                    (identity, kind),
                ).fetchone()
                expected = meta[2]
                if replace_snapshot and old and old[0] != identity:
                    conn.execute(
                        "DELETE FROM board_daily WHERE date=? AND scope=? AND kind=?",
                        (day, scope, kind),
                    )
                for board_id, name, encoded in conn.execute(
                    "SELECT board_id,board_name,members_json FROM board_snapshots WHERE "
                    "snapshot_id=? AND kind=? ORDER BY board_id",
                    (identity, kind),
                ):
                    members = set(json.loads(encoded))
                    traded = members & universe.keys()
                    mapped.update(traded)
                    processed += 1
                    if (
                        changed_symbols is not None
                        and old
                        and old[0] == identity
                        and not members.intersection(changed_symbols)
                    ):
                        continue
                    returns = [
                        universe[symbol]
                        for symbol in sorted(traded)
                        if universe[symbol] is not None
                    ]
                    changed += _put(
                        conn,
                        "board_daily",
                        ("date", "scope", "classification", "kind", "board_id"),
                        dict(
                            date=day,
                            scope=scope,
                            classification=CLASSIFICATION,
                            kind=kind,
                            board_id=board_id,
                            board_name=name,
                            member_count=len(members),
                            trading_member_count=len(traded),
                            valid_return_count=len(returns),
                            avg_return=math.fsum(returns) / len(returns) if returns else None,
                            membership_as_of=meta[0],
                            membership_basis=meta[1],
                            snapshot_id=identity,
                            input_digest=hashlib.sha256(
                                json.dumps(
                                    [(symbol, universe[symbol]) for symbol in sorted(traded)]
                                ).encode()
                            ).hexdigest(),
                        ),
                    )
                # Zero valid groups is computed_empty, distinct from missing/failure.
                valid = conn.execute(
                    "SELECT 1 FROM board_daily WHERE date=? AND scope=? AND kind=? AND "
                    "valid_return_count>0 LIMIT 1",
                    (day, scope, kind),
                ).fetchone()
                status = "ready" if valid else "computed_empty"
            changed += _put(
                conn,
                "board_daily_status",
                ("date", "scope", "kind"),
                dict(
                    date=day,
                    scope=scope,
                    kind=kind,
                    status=status,
                    processed_board_count=processed,
                    expected_board_count=expected,
                    mapped_trading_count=len(mapped),
                    snapshot_id=identity,
                ),
            )
    return changed


def read_board_daily(
    reader, *, start, end, kind=None, scope="all_stocks", fields=None, limit=100000, offset=0
):
    from .sqlite_read_api import _bounds, _fields, _frame

    reader._require_open()
    lo, hi = _bounds(start, end, required=True)
    if (
        kind not in (*KINDS, None)
        or scope not in SCOPES
        or not isinstance(limit, int)
        or not 1 <= limit <= 100000
        or not isinstance(offset, int)
        or offset < 0
    ):
        raise DataPoolError("INVALID_ARGUMENT", "Invalid board range, kind, scope or page")
    chosen = _fields(fields, FIELDS)
    conn = reader.conn
    kinds = (kind,) if kind else KINDS
    statuses, sync = [], {}
    if not available(conn):
        frame = _frame([], chosen)
        frame.attrs.update(
            classification=CLASSIFICATION,
            availability="not_provided",
            categories={item: {"status": "not_provided"} for item in kinds},
        )
        return frame
    clause = " AND kind=?" if kind else ""
    params = (scope, lo.isoformat(), hi.isoformat()) + ((kind,) if kind else ())
    rows = conn.execute(
        "SELECT "
        + ",".join(chosen)
        + " FROM board_daily WHERE scope=? AND date BETWEEN ? AND ?"
        + clause
        + " ORDER BY date,kind,board_id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    cursor = conn.execute(
        "SELECT * FROM board_daily_status WHERE scope=? AND date BETWEEN ? AND ?"
        + clause
        + " ORDER BY date,kind",
        params,
    )
    names = [item[0] for item in cursor.description]
    statuses = [dict(zip(names, row)) for row in cursor]
    for item in kinds:
        cursor = conn.execute("SELECT * FROM board_sync_state WHERE kind=?", (item,))
        row = cursor.fetchone()
        sync[item] = (
            dict(zip([col[0] for col in cursor.description], row))
            if row
            else dict(status="not_provided")
        )
    frame = _frame(rows, chosen)
    frame.attrs.update(
        classification=CLASSIFICATION,
        industry_level=2,
        category_status=statuses,
        categories=sync,
        uncomputed_date_status="not_computed",
        limit=limit,
        offset=offset,
        next_offset=offset + limit if len(rows) == limit else None,
    )
    return frame
