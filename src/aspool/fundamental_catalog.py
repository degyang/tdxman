"""Low-frequency snapshots in the existing shared catalog, after lake retirement."""

from pathlib import Path

import duckdb
import pandas as pd

FIELDS = (
    "symbol",
    "market",
    "code",
    "name",
    "total_share",
    "float_share",
    "eps",
    "ttm_eps",
    "net_assets",
    "refreshed_at",
    "source",
)


def available(root):
    path = Path(root) / "catalog.duckdb"
    if not path.exists():
        return False
    with duckdb.connect(str(path), read_only=True) as conn:
        return bool(
            conn.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name='fundamental_snapshots'"
            ).fetchone()[0]
        )


def read(root):
    with duckdb.connect(str(Path(root) / "catalog.duckdb"), read_only=True) as conn:
        return conn.execute("SELECT * FROM fundamental_snapshots ORDER BY market,code").fetchdf()


def write(root, incoming):
    from .fundamentals import _same_snapshot
    from .pool import pool_lock

    changed = False
    with pool_lock(root, write=True), duckdb.connect(str(Path(root) / "catalog.duckdb")) as conn:
        conn.execute("BEGIN")
        try:
            for row in incoming:
                if row.get("operation", "update") not in {"insert", "update"}:
                    raise ValueError("Snapshot deletions are unsupported")
                old = conn.execute(
                    "SELECT * FROM fundamental_snapshots WHERE symbol=?", [row["symbol"]]
                ).fetchone()
                prior = dict(zip(FIELDS, old)) if old else {}
                merged = {
                    **prior,
                    **{
                        k: v
                        for k, v in row.items()
                        if k in FIELDS and v is not None and not pd.isna(v)
                    },
                }
                if _same_snapshot(prior or None, merged):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO fundamental_snapshots VALUES ("
                    + ",".join("?" for _ in FIELDS)
                    + ")",
                    [merged.get(k) for k in FIELDS],
                )
                changed = True
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return changed
