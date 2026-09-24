"""日终涨跌停派生结果的公开只读接口。

沿用 DataPool 的参数与错误风格。
- 只读：不创建目录、不创建 catalog.duckdb、不建表。
- 只暴露已发布批次；派生失败或修正未完成的日期标记为 stale 并可见。
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from .api_contract import DataPoolError, public_read
from .pool import _normalize_symbol, pool_lock
from .store import existing_tables, read_only_catalog

# 公开读取所需的派生表；缺任一即视为未就绪。
REQUIRED_TABLES = frozenset(
    {
        "daily_limit_events",
        "daily_limit_batches",
        "daily_limit_exceptions",
        "daily_limit_summary",
        "daily_limit_publication",
        "daily_limit_scope",
        "daily_limit_staleness",
    }
)

SUMMARY_FIELDS = {
    "trade_date": ("DATE", None),
    "symbol_count": ("BIGINT", "count"),
    "known_count": ("BIGINT", "count"),
    "unknown_count": ("BIGINT", "count"),
    "no_limit_count": ("BIGINT", "count"),
    "invalid_count": ("BIGINT", "count"),
    "close_limit_up_count": ("BIGINT", "count"),
    "close_limit_down_count": ("BIGINT", "count"),
    "touched_limit_up_count": ("BIGINT", "count"),
    "touched_limit_down_count": ("BIGINT", "count"),
    "touched_unsealed_up_count": ("BIGINT", "count"),
    "touched_unsealed_down_count": ("BIGINT", "count"),
    "max_consecutive_up": ("INTEGER", "count"),
    "max_consecutive_up_note": ("VARCHAR", None),
    "sealed_ratio": ("DOUBLE", "ratio"),
    "batch_id": ("VARCHAR", None),
    "stale": ("BOOLEAN", None),
    "stale_reason": ("VARCHAR", None),
}

EVENT_FIELDS = {
    "trade_date": ("DATE", None),
    "symbol": ("VARCHAR", None),
    "close_limit_up": ("BOOLEAN", None),
    "close_limit_down": ("BOOLEAN", None),
    "touched_limit_up": ("BOOLEAN", None),
    "touched_limit_down": ("BOOLEAN", None),
    "limit_up_price": ("DOUBLE", "CNY/share"),
    "limit_down_price": ("DOUBLE", "CNY/share"),
    "consecutive_up": ("INTEGER", "count"),
    "consecutive_gap_sessions": ("INTEGER", "count"),
    "batch_id": ("VARCHAR", None),
    "stale": ("BOOLEAN", None),
    "stale_reason": ("VARCHAR", None),
}

EXCEPTION_FIELDS = {
    "trade_date": ("DATE", None),
    "symbol": ("VARCHAR", None),
    "kind": ("VARCHAR", None),
    "reason": ("VARCHAR", None),
    "affected_fields": ("VARCHAR", None),
    "batch_id": ("VARCHAR", None),
}

COVERAGE_FIELDS = {
    "trade_date": ("DATE", None),
    "batch_id": ("VARCHAR", None),
    "scope_id": ("VARCHAR", None),
    "rule_version": ("VARCHAR", None),
    "computed_at": ("TIMESTAMP", None),
    "processed_count": ("BIGINT", "count"),
    "known_count": ("BIGINT", "count"),
    "unknown_count": ("BIGINT", "count"),
    "no_limit_count": ("BIGINT", "count"),
    "invalid_count": ("BIGINT", "count"),
    "status": ("VARCHAR", None),
    "stale": ("BOOLEAN", None),
    "stale_reason": ("VARCHAR", None),
}

SCOPE_FIELDS = {
    "trade_date": ("DATE", None),
    "batch_id": ("VARCHAR", None),
    "symbol": ("VARCHAR", None),
}


def _bounds(start, end) -> tuple[date | None, date | None]:
    try:
        lo = pd.Timestamp(start).date() if start is not None else None
        hi = pd.Timestamp(end).date() if end is not None else None
        if (lo is not None and pd.isna(lo)) or (hi is not None and pd.isna(hi)):
            raise ValueError("Missing date boundary")
    except (TypeError, ValueError) as exc:
        raise DataPoolError("INVALID_ARGUMENT", "Invalid date bounds") from exc
    if lo and hi and lo > hi:
        raise DataPoolError("INVALID_ARGUMENT", "start must not be after end")
    return lo, hi


def _date_clause(column: str, lo: date | None, hi: date | None, clauses, params) -> None:
    if lo:
        clauses.append(f"{column} >= ?")
        params.append(lo)
    if hi:
        clauses.append(f"{column} <= ?")
        params.append(hi)


def _normalize_requested_symbols(symbols):
    """规范化为公开输出格式 code.market。"""
    if symbols is None:
        return None
    values = [symbols] if isinstance(symbols, str) else list(symbols)
    out = []
    for value in values:
        if not isinstance(value, str):
            raise DataPoolError("INVALID_ARGUMENT", f"Symbol must be a string: {value}")
        market_part, code_part = _normalize_symbol(value).split(".", 1)
        out.append(f"{code_part}.{market_part}")
    return out


def _require_ready(root) -> None:
    """派生表未就绪则报明确错误；不创建任何文件。"""
    try:
        with pool_lock(root):
            tables = existing_tables(root)
    except FileNotFoundError:
        tables = set()
    missing = REQUIRED_TABLES - tables
    if missing:
        raise DataPoolError(
            "LIMIT_NOT_READY",
            "涨跌停派生表未就绪：" + ", ".join(sorted(missing)),
        )


def _query(root, sql: str, params: list) -> pd.DataFrame:
    """只读执行。目录缺失/表缺失都抛明确错误。"""
    try:
        with pool_lock(root):
            with read_only_catalog(root) as conn:
                return conn.execute(sql, params).fetchdf()
    except FileNotFoundError as exc:
        raise DataPoolError("LIMIT_NOT_READY", "aspool 目录或 catalog 不存在") from exc
    except duckdb.CatalogException as exc:
        raise DataPoolError("LIMIT_NOT_READY", f"派生表未就绪：{exc}") from exc
    except duckdb.Error as exc:
        raise DataPoolError("LIMIT_INVALID", str(exc)) from exc


@public_read
def read_limit_references(root, *, start=None, end=None, symbols=None) -> pd.DataFrame:
    """Read published reference-price corrections/rejections with their evidence."""
    _require_ready(root)
    lo, hi = _bounds(start, end)
    clauses, params = [], []
    _date_clause("r.trade_date", lo, hi, clauses, params)
    if symbols is not None:
        clauses.append("r.symbol IN (SELECT unnest(?))")
        params.append(_normalize_requested_symbols(symbols))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return _query(root, f"""
        select r.*, st.trade_date is not null as stale, st.reason as stale_reason
        from daily_limit_references r
        join daily_limit_publication p using (trade_date, batch_id)
        left join daily_limit_staleness st on st.trade_date=r.trade_date
        {where} order by r.trade_date, r.symbol
        """, params)


@public_read
def read_limit_summary(root, *, start=None, end=None) -> pd.DataFrame:
    """读取已发布的日级涨跌停汇总；含 stale 标记。"""
    _require_ready(root)
    lo, hi = _bounds(start, end)
    clauses, params = [], []
    _date_clause("s.trade_date", lo, hi, clauses, params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        select s.trade_date, b.processed_count as symbol_count, s.known_count,
               s.unknown_count, s.no_limit_count, s.invalid_count,
               s.close_limit_up_count, s.close_limit_down_count,
               s.touched_limit_up_count, s.touched_limit_down_count,
               s.touched_unsealed_up_count, s.touched_unsealed_down_count,
               s.max_consecutive_up, s.max_consecutive_up_note, s.sealed_ratio,
               s.batch_id,
               (st.trade_date is not null) as stale,
               st.reason as stale_reason
        from daily_limit_summary s
        join daily_limit_publication p on p.trade_date = s.trade_date and p.batch_id = s.batch_id
        join daily_limit_batches b on b.batch_id = s.batch_id
        left join daily_limit_staleness st on st.trade_date = s.trade_date
        {where}
        order by s.trade_date
    """
    return _query(root, sql, params)


@public_read
def read_limit_events(root, *, trade_date=None, start=None, end=None, symbols=None) -> pd.DataFrame:
    """读取已发布的逐股事件；只含至少一个 true 的行。输出规范 symbol。"""
    _require_ready(root)
    with pool_lock(root), read_only_catalog(root) as conn:
        columns = {row[0] for row in conn.execute("describe daily_limit_events").fetchall()}
    gap_column = ("e.consecutive_gap_sessions" if "consecutive_gap_sessions" in columns
                  else "cast(null as integer)")
    lo, hi = _bounds(start, end)
    if trade_date is not None:
        lo = hi = pd.Timestamp(trade_date).date()
    clauses, params = [], []
    _date_clause("e.trade_date", lo, hi, clauses, params)
    requested = _normalize_requested_symbols(symbols)
    if requested is not None:
        clauses.append("e.symbol IN (SELECT unnest(?))")
        params.append(requested)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        select e.trade_date, e.symbol, e.close_limit_up, e.close_limit_down,
               e.touched_limit_up, e.touched_limit_down, e.limit_up_price,
               e.limit_down_price, e.consecutive_up,
               {gap_column} as consecutive_gap_sessions, e.batch_id,
               (st.trade_date is not null) as stale,
               st.reason as stale_reason
        from daily_limit_events e
        join daily_limit_publication p on p.trade_date = e.trade_date and p.batch_id = e.batch_id
        left join daily_limit_staleness st on st.trade_date = e.trade_date
        {where}
        order by e.trade_date, e.symbol
    """
    return _query(root, sql, params)


@public_read
def read_limit_exceptions(
    root, *, trade_date=None, start=None, end=None, symbols=None
) -> pd.DataFrame:
    """读取已发布的 UNKNOWN / NO_LIMIT / INVALID 记录。"""
    _require_ready(root)
    lo, hi = _bounds(start, end)
    if trade_date is not None:
        lo = hi = pd.Timestamp(trade_date).date()
    clauses, params = [], []
    _date_clause("x.trade_date", lo, hi, clauses, params)
    requested = _normalize_requested_symbols(symbols)
    if requested is not None:
        clauses.append("x.symbol IN (SELECT unnest(?))")
        params.append(requested)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        select x.trade_date, x.symbol, x.kind, x.reason, x.affected_fields, x.batch_id
        from daily_limit_exceptions x
        join daily_limit_publication p on p.trade_date = x.trade_date and p.batch_id = x.batch_id
        {where}
        order by x.trade_date, x.symbol, x.kind
    """
    return _query(root, sql, params)


@public_read
def read_limit_coverage(root, *, start=None, end=None) -> pd.DataFrame:
    """读取已发布批次的范围与完成状态；含 stale 标记。"""
    _require_ready(root)
    lo, hi = _bounds(start, end)
    clauses, params = [], []
    _date_clause("b.trade_date", lo, hi, clauses, params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        select b.trade_date, b.batch_id, b.scope_id, b.rule_version, b.computed_at,
               b.processed_count, b.known_count, b.unknown_count, b.no_limit_count,
               b.invalid_count, b.status,
               (st.trade_date is not null) as stale,
               st.reason as stale_reason
        from daily_limit_batches b
        join daily_limit_publication p on p.trade_date = b.trade_date and p.batch_id = b.batch_id
        left join daily_limit_staleness st on st.trade_date = b.trade_date
        {where}
        order by b.trade_date
    """
    return _query(root, sql, params)


@public_read
def read_limit_scope(root, *, trade_date=None, start=None, end=None) -> pd.DataFrame:
    """读取已发布批次实际处理的证券名单。

    消费者据此判断某证券是否被处理：在名单内且无事件无异常 => 确定无事件。
    """
    _require_ready(root)
    lo, hi = _bounds(start, end)
    if trade_date is not None:
        lo = hi = pd.Timestamp(trade_date).date()
    clauses, params = [], []
    _date_clause("s.trade_date", lo, hi, clauses, params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        select s.trade_date, s.batch_id, s.symbol
        from daily_limit_scope s
        join daily_limit_publication p on p.trade_date = s.trade_date and p.batch_id = s.batch_id
        {where}
        order by s.trade_date, s.symbol
    """
    return _query(root, sql, params)


@public_read
def read_limit_staleness(root, *, start=None, end=None) -> pd.DataFrame:
    """读取被标记为陈旧/失败的日期，供消费者拒绝或降级使用。"""
    _require_ready(root)
    lo, hi = _bounds(start, end)
    clauses, params = [], []
    _date_clause("trade_date", lo, hi, clauses, params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        select trade_date, reason, marked_at from daily_limit_staleness
        {where}
        order by trade_date
    """
    return _query(root, sql, params)


def describe_limits(root) -> dict:
    """声明已实现的涨跌停派生能力与字段单位。只读。"""
    from .limit_events import RULE_VERSION

    available = REQUIRED_TABLES <= existing_tables(root)
    return {
        "contract_version": 2,
        "rule_version": RULE_VERSION,
        "ready": available,
        "capabilities": {
            "daily_limit_events": available,
            "daily_limit_event_amount_batches": available,
            "daily_limit_summary": available,
            "daily_limit_coverage": available,
            "daily_limit_scope": available,
            "daily_limit_exceptions": available,
            "daily_limit_staleness": available,
            "daily_limit_references": "daily_limit_references" in existing_tables(root),
            "touched_count": False,
            "open_board_count": False,
            "realtime": False,
            "auto_schedule": False,
        },
        "fields": {
            "summary": {k: {"type": t, "unit": u} for k, (t, u) in SUMMARY_FIELDS.items()},
            "events": {k: {"type": t, "unit": u} for k, (t, u) in EVENT_FIELDS.items()},
            "exceptions": {k: {"type": t, "unit": u} for k, (t, u) in EXCEPTION_FIELDS.items()},
            "coverage": {k: {"type": t, "unit": u} for k, (t, u) in COVERAGE_FIELDS.items()},
            "scope": {k: {"type": t, "unit": u} for k, (t, u) in SCOPE_FIELDS.items()},
        },
        "semantics": {
            "event_row": "每证券每交易日最多一行；四个事件标志至少一个为 true 才入表",
            "false": "确定没有该事件",
            "null": "无法确认（规则/参考价/会话/上市依据不足）",
            "absent": "在已发布 scope 名单内、无异常且无事件行时，才是确定无事件",
            "all_unknown": "四个标志全为 null 的证券只进异常与 scope，不进事件表",
            "consecutive_up": (
                "确定当日未涨停为 0；可靠停牌暂停计数；"
                "缺行前已知连板时跳过缺行并接续涨停，前史不足仍为 null；"
                "v8 排除已确认的旧主板 IPO 首日，次日起从首板计数"),
            "consecutive_gap_sessions": (
                "当前连板累计跳过的缺行市场会话数，不含已证实停牌；"
                "大于 0 表示采用跨缺行连续性假设，旧批次或连板未知为 null"),
            "sealed_ratio": "收盘涨停家数/(收盘涨停家数+触涨停未封家数)；分母为 0 时为 null",
            "max_consecutive_up": "已知样本最大值；note 说明是否有连板未知的个股",
            "reference_price": (
                "优先同口径当日参考价；历史冲突仅在当日收益的保守精度区间内"
                "存在唯一分币参考价时恢复；修正及无法唯一恢复的记录可追溯"
            ),
            "stale": "该日期结果已过期或派生失败，消费者应拒绝或降级使用",
            "stale_consumption": (
                "事件/汇总/覆盖三种读取都带 stale 与 stale_reason；"
                "单独读取事件时也必须检查 stale，或与 read_limit_coverage 联合消费"
            ),
        },
        "rule_basis": {
            "main_board": (
                "主板±10%；风险警示在2026-07-06前为±5%（需当日 ST 依据），此后±10%；"
                "主板注册制新股前5个交易日窗口自2023-04-10首批主板注册制企业上市起，"
                "2014-06-13至2023-04-09在能排除上市首日后使用常规限幅；"
                "首日发行价特殊限幅依据不足时为 UNKNOWN"
            ),
            "star": "科创板±20%（2019-07-22 起）",
            "gem": "创业板注册制后±20%（2020-08-24 起）；之前±10%/ST±5%",
            "bj": "北交所±30%（2021-11-15 起）",
            "no_limit": "上市无涨跌幅窗口需可靠上市依据；依据不足记 UNKNOWN 而非按常规限价",
        },
    }
