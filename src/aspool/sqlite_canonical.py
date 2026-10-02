"""Map calculator relations to the sole physical stores in layout 3.

This is an internal storage adapter, not a second set of tables. DML goes to
native canonical tables; a read-only union relates source events to references.
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from . import sqlite_publication as publication
from .api_contract import DataPoolError

ACTION_COLUMNS = (
    "symbol",
    "effective_date",
    "record_kind",
    "source",
    "source_key",
    "category",
    "payload_json",
    "source_cumulative_factor",
    "event_factor",
    "cumulative_factor",
    "valid_from",
    "valid_through",
    "factor_basis",
    "updated_at",
)
OPTIONAL_TABLES = {
    "market_sessions",
    "board_snapshots",
    "board_snapshot_sets",
    "board_sync_state",
    "board_daily",
    "board_daily_status",
}

RELATIONS = {
    **{name: "features." + name for name in OPTIONAL_TABLES},
    "daily_features": "features.stock_daily_features",
    "market_daily_summary": "features.market_regime_features",
}
ACTION_WRITES = {
    "event": "main.corporate_actions",
    "factor": "adjustments.stock_adjustment_factors",
    "factor_anchor": "adjustments.stock_factor_anchors",
}
_TOKENS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z_0-9]*|\.")


@contextmanager
def canonical_operation(conn, operation):
    if not isinstance(conn, CanonicalConnection):
        yield
        return
    if operation not in {
        "daily_update",
        "summary_repair",
        "summary_recompute",
        "factor_bootstrap",
        "board_update",
        "six_dimension_init",
        "six_dimension_extend",
        "st_default_repair",
        "daily_state_normalize",
    }:
        raise ValueError("Unknown canonical writer")
    previous = getattr(conn, "_operation", None)
    conn._operation = operation
    try:
        yield
    finally:
        conn._operation = previous


def canonical_writer(operation):
    def decorate(function):
        @wraps(function)
        def wrapped(conn, *args, **kwargs):
            with canonical_operation(conn, operation):
                return function(conn, *args, **kwargs)

        return wrapped

    return decorate


def physical_sql(sql):
    """Route fixed internal SQL; never infer a writable action kind."""
    operation = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    mutable = operation in {"INSERT", "UPDATE", "DELETE", "REPLACE"}
    action = "temp.aspool_actions"
    if mutable and re.search(r"\bcorporate_actions\b", sql):
        kinds = set(re.findall(r"'(event|factor|factor_anchor)'", sql))
        if len(kinds) == 1:
            action = ACTION_WRITES[kinds.pop()]
        elif operation == "UPDATE" and re.search(r"\bWHERE\s+0\s*$", sql, re.I):
            action = "main.corporate_actions"
        else:
            raise DataPoolError(
                "WRITE_SCOPE_REQUIRED", "An explicit reference record kind is required"
            )
    if re.match(r"\s*PRAGMA\s+table_info\s*\(", sql, re.I):
        match = re.search(r"table_info\s*\(\s*[\"']?([A-Za-z_]+)", sql, re.I)
        if match and match[1] in RELATIONS:
            alias, table = RELATIONS[match[1]].split(".")
            return f'PRAGMA "{alias}".table_info("{table}")'
        if match and match[1] == "corporate_actions":
            return 'PRAGMA temp.table_info("aspool_actions")'
    previous = None

    def replace(match):
        nonlocal previous
        token = match[0]
        name = token.strip('"')
        qualified = previous == "."
        previous = token
        if token.startswith("'") or qualified:
            return token
        if name == "corporate_actions":
            return action
        return RELATIONS.get(name, token)

    return _TOKENS.sub(replace, sql)


class NativeView:
    """Keep publication checks independent of logical SQL relation routing."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, parameters=()):
        return sqlite3.Connection.execute(self.conn, sql, parameters)


class CanonicalCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        return self.connection._execute(sql, parameters, self)

    def executemany(self, sql, parameters):
        for values in parameters:
            self.execute(sql, values)
        return self


class CanonicalConnection(sqlite3.Connection):
    def configure(self, root, *, read_only):
        self.root = Path(root).resolve()
        self.read_only = read_only
        self._guard = None
        self._changes = []
        self._savepoints = []
        self._shapes = {}
        self.native = NativeView(self)
        mode = "ro" if read_only else "rw"
        for alias, file in publication.FILES.items():
            if alias != "main":
                self.native.execute(
                    f'ATTACH DATABASE ? AS "{alias}"',
                    ((self.root / file).as_uri() + "?mode=" + mode,),
                )
                version = self.native.execute(f'PRAGMA "{alias}".user_version').fetchone()[0]
                if version != 3:
                    raise DataPoolError("LAYOUT_INVALID", "Canonical store schema is incomplete")
        projections = []
        for kind, table in ACTION_WRITES.items():
            alias, name = table.split(".")
            actual = {r[1] for r in self.native.execute(f'PRAGMA "{alias}".table_info("{name}")')}
            projection = ",".join(
                f'"{column}"' if column in actual else f'NULL AS "{column}"'
                for column in ACTION_COLUMNS
            )
            projections.append(f'SELECT {projection} FROM "{alias}"."{name}"')
        self.native.execute("CREATE TEMP VIEW aspool_actions AS " + " UNION ALL ".join(projections))
        if not read_only:
            self.create_function("aspool_changed", -1, self._record_change)
            for alias, tables in publication.TABLES.items():
                for table in sorted(tables):
                    if (
                        table in OPTIONAL_TABLES
                        and not self.native.execute(
                            "SELECT 1 FROM features.sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone()
                    ):
                        continue
                    columns, keys = publication.table_shape(self.native, alias, table)
                    self._shapes[(alias, table)] = (columns, keys)
                    old = ",".join(f'OLD."{name}"' for name in columns)
                    new = ",".join(f'NEW."{name}"' for name in columns)
                    nulls = ",".join("NULL" for _ in columns)
                    differs = " OR ".join(f'OLD."{name}" IS NOT NEW."{name}"' for name in columns)
                    for operation in ("INSERT", "UPDATE", "DELETE"):
                        before = old if operation != "INSERT" else nulls
                        after = new if operation != "DELETE" else nulls
                        condition = " WHEN " + differs if operation == "UPDATE" else ""
                        self.native.execute(
                            f'CREATE TEMP TRIGGER "aspool_{alias}_{table}_{operation}" '
                            f'AFTER {operation} ON "{alias}"."{table}"{condition} BEGIN '
                            f"SELECT aspool_changed('{alias}','{table}','{operation}',"
                            f"{before},{after}); END"
                        )

    def _record_change(self, alias, table, operation, *values):
        columns, keys = self._shapes[(alias, table)]
        length = len(columns)
        before = tuple(values[:length]) if operation != "INSERT" else None
        after = tuple(values[length:]) if operation != "DELETE" else None
        if getattr(self, "_operation", None) == "daily_state_normalize":
            allowed = {
                "stock_daily_features": {"trading_status", "trading_status_source", "updated_at"},
                "market_regime_features": {"updated_at"},
                "board_daily": {"updated_at"},
                "feature_state": {"updated_at"},
            }
            if alias != "features" or table not in allowed or operation != "UPDATE":
                raise ValueError("State normalization must update only existing feature rows")
            if table == "stock_daily_features":
                state = columns.index("trading_status")
                source = columns.index("trading_status_source")
                calc = columns.index("calc_status")
                if (
                    before[state] not in (None, "")
                    or after[state] != "TRADING"
                    or after[source] != "derived:stored_ohlcv"
                    or before[calc] != "TRADED"
                ):
                    raise ValueError("State normalization accepts only equivalent missing states")
            if any(
                old != new and name not in allowed[table]
                for name, old, new in zip(columns, before, after)
            ):
                raise ValueError("State normalization cannot change numeric dependencies")
        positions = [columns.index(key) for key in keys]
        if before is not None:
            key = tuple(before[position] for position in positions)
            self._changes.append(((alias, table, key), before))
        if after is not None:
            key = tuple(after[position] for position in positions)
            if before is None or key != tuple(before[position] for position in positions):
                self._changes.append(((alias, table, key), None))
        if (
            getattr(self, "_operation", None) == "daily_state_normalize"
            and len(self._changes) > 25000
        ):
            raise ValueError("State normalization exceeds publication row budget")
        return 0

    def _begin_guard(self):
        from .pool import pool_lock

        if self._guard is not None:
            return
        if getattr(self, "_operation", None) is None:
            raise DataPoolError(
                "WRITER_ENTRY_REQUIRED", "Use the canonical daily or summary writer"
            )
        self._guard = pool_lock(self.root, write=True)
        try:
            self._guard.__enter__()
            publication.assert_published(self.root)
            from .storage_verify import assert_coherent

            assert_coherent(self.native)
            self._baseline_revision = self.native.execute(
                "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
            ).fetchone()
        except BaseException:
            self._end_guard()
            raise

    def _end_guard(self):
        guard, self._guard = self._guard, None
        if guard is not None:
            guard.__exit__(None, None, None)

    def _execute(self, sql, parameters, cursor=None):
        operation = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if operation in {"COMMIT", "END"}:
            self.commit()
            return self.native.execute("SELECT 1 WHERE 0")
        if operation == "ROLLBACK" and not re.match(r"\s*ROLLBACK\s+TO", sql, re.I):
            self.rollback()
            return self.native.execute("SELECT 1 WHERE 0")
        if not self.read_only:
            if operation in {"CREATE", "ALTER", "DROP", "ATTACH", "DETACH"}:
                raise DataPoolError(
                    "SCHEMA_MAINTENANCE_REQUIRED", "Use the explicit storage migration"
                )
            if operation == "BEGIN":
                self._begin_guard()
            elif operation == "SAVEPOINT" and not self.in_transaction:
                raise DataPoolError(
                    "WRITER_TRANSACTION_REQUIRED", "Begin the write transaction first"
                )
            elif operation == "PRAGMA" and re.search(
                r"user_version\s*=|writable_schema", sql, re.I
            ):
                raise DataPoolError(
                    "SCHEMA_MAINTENANCE_REQUIRED", "Use the explicit storage migration"
                )
            elif operation in {"INSERT", "UPDATE", "DELETE", "REPLACE"} and not self.in_transaction:
                raise DataPoolError(
                    "WRITER_TRANSACTION_REQUIRED", "An explicit write transaction is required"
                )
        try:
            routed = physical_sql(sql)
            result = (
                sqlite3.Cursor.execute(cursor, routed, parameters)
                if cursor is not None
                else sqlite3.Connection.execute(self, routed, parameters)
            )
        except BaseException:
            if operation == "BEGIN":
                self._end_guard()
            raise
        if not self.read_only:
            savepoint = re.match(r"\s*SAVEPOINT\s+(\w+)", sql, re.I)
            rollback = re.match(r"\s*ROLLBACK\s+TO(?:\s+SAVEPOINT)?\s+(\w+)", sql, re.I)
            release = re.match(r"\s*RELEASE(?:\s+SAVEPOINT)?\s+(\w+)", sql, re.I)
            if savepoint:
                self._savepoints.append((savepoint[1], len(self._changes)))
            elif rollback or release:
                name = (rollback or release)[1]
                index = next(
                    i
                    for i in range(len(self._savepoints) - 1, -1, -1)
                    if self._savepoints[i][0] == name
                )
                if rollback:
                    del self._changes[self._savepoints[index][1] :]
                    del self._savepoints[index + 1 :]
                else:
                    del self._savepoints[index:]
        return result

    def execute(self, sql, parameters=()):
        return self._execute(sql, parameters)

    def executemany(self, sql, parameters):
        cursor = self.cursor()
        return cursor.executemany(sql, parameters)

    def cursor(self, factory=CanonicalCursor):
        return super().cursor(factory)

    def executescript(self, sql):
        raise DataPoolError("SCHEMA_MAINTENANCE_REQUIRED", "Use the explicit storage migration")

    def _changed_rows(self):
        before = {}
        for identity, values in self._changes:
            before.setdefault(identity, values)
        rows, shapes = [], {}
        for (alias, table, key), old in before.items():
            columns, keys = self._shapes[(alias, table)]
            current = publication.read_row(self.native, alias, table, columns, keys, key)
            current = tuple(current) if current is not None else None
            if current == old:
                continue
            shapes[alias + "." + table] = {"columns": columns, "keys": keys}
            rows.append({"alias": alias, "table": table, "key": key, "value": current})
        return rows, shapes

    def _coherent_states(self, rows):
        business = [r for r in rows if not r["table"].endswith("state")]
        if not business:
            return
        if self._operation in {"summary_repair", "summary_recompute"} and any(
            (r["alias"], r["table"]) != ("features", "market_regime_features") for r in business
        ):
            raise DataPoolError(
                "WRITE_SCOPE_REQUIRED", "Summary maintenance changed another data domain"
            )
        state = self.native.execute(
            "SELECT revision,max_date FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()
        baseline = self._baseline_revision[0] if self._baseline_revision else 0
        raw_changed = any(
            (r["alias"], r["table"])
            in {
                ("main", "daily_bars"),
                ("main", "corporate_actions"),
                ("adjustments", "stock_factor_anchors"),
            }
            for r in business
        )
        revision = max(state[0] if state else 0, baseline + int(raw_changed))
        stamp = time.time_ns() // 1000
        if state is None or revision != state[0]:
            maximum = self.native.execute("SELECT max(trade_date) FROM daily_bars").fetchone()[0]
            self.native.execute(
                "INSERT INTO dataset_state VALUES ('stock_raw',?,?,?) "
                "ON CONFLICT(dataset) DO UPDATE SET revision=excluded.revision,"
                "max_date=excluded.max_date,updated_at=excluded.updated_at",
                (revision, maximum, stamp),
            )
        factor = self.native.execute(
            "SELECT revision FROM adjustments.adjustment_state "
            "WHERE dataset='stock_adjustment_factors'"
        ).fetchone()
        if factor is None:
            raise DataPoolError("LAYOUT_INVALID", "Canonical factor state is absent")
        factor_revision = factor[0]
        if any(
            (r["alias"], r["table"]) == ("adjustments", "stock_adjustment_factors")
            for r in business
        ):
            factor_revision += 1
            maximum = self.native.execute(
                "SELECT max(valid_through) FROM adjustments.stock_adjustment_factors"
            ).fetchone()[0]
            self.native.execute(
                "UPDATE adjustments.adjustment_state SET revision=?,max_date=?,updated_at=? "
                "WHERE dataset='stock_adjustment_factors'",
                (factor_revision, maximum, stamp),
            )
        self.native.execute(
            "UPDATE features.feature_state SET raw_revision=?,factor_revision=?,"
            "status='READY',updated_at=? WHERE scope_key='all'",
            (revision, factor_revision, stamp),
        )

    def commit(self):
        if self.read_only or not self.in_transaction:
            return sqlite3.Connection.commit(self)
        try:
            if self._operation in {
                "six_dimension_extend",
                "st_default_repair",
                "daily_state_normalize",
            }:
                # Explicit historical initialization changes only this one WAL file.
                # Its single-file transaction is atomic without a cross-file intent.
                permitted = {
                    "market_regime_features",
                    "market_sessions",
                    "board_daily",
                    "board_daily_status",
                }
                if self._operation == "st_default_repair":
                    permitted = {
                        "stock_daily_features",
                        "market_regime_features",
                        "board_daily",
                        "board_daily_status",
                    }
                if self._operation == "daily_state_normalize":
                    permitted = {
                        "stock_daily_features",
                        "market_regime_features",
                        "board_daily",
                        "feature_state",
                    }
                if any(
                    alias != "features" or table not in permitted
                    for (alias, table, _), _old in self._changes
                ):
                    raise DataPoolError(
                        "WRITE_SCOPE_REQUIRED", "Historical extension must change only features"
                    )
                sqlite3.Connection.commit(self)
                return
            rows, _ = self._changed_rows()
            from .factor_lineage import refresh_factor_lineage

            refresh_factor_lineage(self.native, rows, root=self.root)
            self._coherent_states(rows)
            rows, shapes = self._changed_rows()
            if rows:
                value = publication.prepare_intent(self.root, rows, shapes)
                sqlite3.Connection.commit(self)
                publication._fault("after_sqlite_commit")
                publication.complete_intent(self.root, self.native, value)
            else:
                sqlite3.Connection.commit(self)
        except BaseException as exc:
            sqlite3.Connection.rollback(self)
            if publication.pending_path(self.root).exists():
                exc.publication_recovery_required = True
            raise
        finally:
            self._changes.clear()
            self._savepoints.clear()
            self._end_guard()

    def rollback(self):
        try:
            sqlite3.Connection.rollback(self)
        finally:
            self._changes.clear()
            self._savepoints.clear()
            self._end_guard()

    def close(self):
        try:
            if self.in_transaction:
                self.rollback()
            sqlite3.Connection.close(self)
        finally:
            self._end_guard()

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.commit()
        else:
            self.rollback()
        return False
