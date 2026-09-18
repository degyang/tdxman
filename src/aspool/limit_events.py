"""日终稀疏涨跌停事件：计算、幂等发布、历史更正传播。

逻辑契约见 Fundwise `docs/northstar/limit-data-pipeline.md`。
- 原始日线保持现有存储；本模块只写派生事件、批次、异常、范围与日汇总。
- 每证券每日最多一条事件行；四个事件标志**至少一个为 true** 才入表。
  四个全为 null（无法确认）只进异常与 scope，不进事件表。
- 读取只见唯一已发布批次；派生失败/更正未完成的日期标记为 stale。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .api_contract import DataPoolError
from .pool import writer
from .store import catalog

RULE_VERSION = "cn-a-share-limit-v2"

# 事件标志；至少一个为 true 才入事件表。
_EVENT_FLAGS = ("close_limit_up", "close_limit_down", "touched_limit_up", "touched_limit_down")

SUPPORTED_SCOPES = {"stock"}
# 首包 scope_id 与 asset_type 都限定为 stock，避免不同范围覆写同一日期键。
SUPPORTED_SCOPE_IDS = {"stock"}

INITIALIZE_SQL = (
    """
    create table if not exists daily_limit_batches (
        batch_id varchar primary key,
        trade_date date not null,
        scope_id varchar not null,
        rule_version varchar not null,
        computed_at timestamp not null,
        processed_count bigint not null,
        known_count bigint not null,
        unknown_count bigint not null,
        no_limit_count bigint not null,
        invalid_count bigint not null default 0,
        out_of_scope_count bigint not null default 0,
        status varchar not null,
        error varchar
    )
    """,
    """
    create table if not exists daily_limit_events (
        batch_id varchar not null,
        trade_date date not null,
        symbol varchar not null,
        close_limit_up boolean,
        close_limit_down boolean,
        touched_limit_up boolean,
        touched_limit_down boolean,
        limit_up_price double,
        limit_down_price double,
        consecutive_up integer,
        primary key (trade_date, symbol, batch_id)
    )
    """,
    """
    create table if not exists daily_limit_exceptions (
        batch_id varchar not null,
        trade_date date not null,
        symbol varchar not null,
        kind varchar not null,
        reason varchar not null,
        affected_fields varchar,
        primary key (trade_date, symbol, batch_id, kind)
    )
    """,
    """
    create table if not exists daily_limit_summary (
        batch_id varchar primary key,
        trade_date date not null,
        scope_id varchar not null,
        close_limit_up_count bigint not null,
        close_limit_down_count bigint not null,
        touched_limit_up_count bigint not null,
        touched_limit_down_count bigint not null,
        touched_unsealed_up_count bigint not null,
        touched_unsealed_down_count bigint not null,
        max_consecutive_up integer,
        max_consecutive_up_note varchar,
        sealed_ratio double,
        known_count bigint not null,
        unknown_count bigint not null,
        no_limit_count bigint not null,
        invalid_count bigint not null default 0
    )
    """,
    """
    create table if not exists daily_limit_publication (
        trade_date date primary key,
        batch_id varchar not null,
        published_at timestamp not null
    )
    """,
    """
    create table if not exists daily_limit_scope (
        batch_id varchar not null,
        trade_date date not null,
        symbol varchar not null,
        primary key (trade_date, symbol)
    )
    """,
    """
    create table if not exists daily_limit_staleness (
        trade_date date primary key,
        reason varchar not null,
        marked_at timestamp not null
    )
    """,
)

_SYMBOL_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")


def initialize_limits(root: Path) -> None:
    """建立派生表。仅写入路径可调用。"""
    with catalog(root) as conn:
        for statement in INITIALIZE_SQL:
            conn.execute(statement)


def _split_symbol(symbol: str) -> tuple[str, str]:
    m = _SYMBOL_RE.match(symbol)
    if not m:
        raise DataPoolError("INVALID_ARGUMENT", f"symbol must be 000001.SH: {symbol}")
    return m.group(1), m.group(2)


@dataclass(frozen=True)
class ScopeEntry:
    """批次内的一个证券身份。"""

    symbol: str
    market: str
    code: str
    name: str | None
    asset_type: str | None
    listing_date: date | None = None


def _read_asset_types(root: Path, market: str, code: str) -> str | None:
    """读取该证券日线的 asset_type；无该列返回 None。"""
    import pyarrow.parquet as pq

    from .store import daily_paths

    for path in daily_paths(root, market, code):
        names = pq.ParquetFile(path).schema_arrow.names
        if "asset_type" in names:
            values = pq.ParquetFile(path).read(columns=["asset_type"])["asset_type"].to_pylist()
            for value in values:
                if value:
                    return str(value)
    return None


def load_scope(root: Path, asset_type: str = "stock") -> list[ScopeEntry]:
    """返回批次处理范围内的证券。

    范围以池内日线目录为准；用日线自身的 asset_type 列过滤，不依赖 universe，
    也不靠捕获异常退化。
    """
    from .store import daily_paths

    if asset_type not in SUPPORTED_SCOPES:
        raise DataPoolError("SCOPE_UNSUPPORTED", f"unsupported scope: {asset_type}")

    names: dict[str, str] = {}
    listings: dict[str, date] = {}
    try:
        with catalog(root) as conn:
            columns = {
                str(row[0])
                for row in conn.execute("describe universe").fetchall()
            }
            selection = "symbol, name" + (", listing_date" if "listing_date" in columns else "")
            rows = conn.execute(
                f"select {selection} from universe "
                "where coalesce(asset_type, 'stock') = ?",
                [asset_type],
            ).fetchall()
        for row in rows:
            names[str(row[0])] = row[1]
            if len(row) > 2 and row[2] is not None:
                listings[str(row[0])] = row[2]
    except Exception:
        names = {}
        listings = {}

    out: list[ScopeEntry] = []
    base = root / "lake" / "bars" / "daily"
    if not base.is_dir():
        return out
    for market_dir in sorted(base.glob("market=*")):
        market = market_dir.name.split("=", 1)[1]
        for symbol_dir in sorted(market_dir.glob("symbol=*")):
            code = symbol_dir.name.split("=", 1)[1]
            if not daily_paths(root, market, code):
                continue
            row_asset = _read_asset_types(root, market, code)
            if (row_asset or "stock") != asset_type:
                continue
            out.append(
                ScopeEntry(
                    f"{code}.{market}",
                    market,
                    code,
                    names.get(code),
                    row_asset,
                    listings.get(code),
                )
            )
    return out


def _listed_days(
    entry: ScopeEntry, session_axis: list[date], trade_date: date
) -> int | None:
    """该证券截至 trade_date 的已上市交易日数；无可靠依据返回 None。

    观测到的市场日期并不等于完整交易日历：池可能缺市场会话，个股也可能
    缺行。因此不把会话轴行数当作精确上市日龄。上市首日由可靠 listing_date
    直接确认；其余日期交给 observed_sessions 作为排除窗口的下界，无法确认时
    由规则解析返回 UNKNOWN。
    """
    if entry.listing_date is not None and trade_date == entry.listing_date:
        return 1
    return None


def _read_symbol_bars(root: Path, market: str, code: str) -> list[dict]:
    from .store import read_daily_table

    table = read_daily_table(root, market, code)
    if table is None:
        return []
    rows = table.to_pylist()
    rows.sort(key=lambda r: r["trade_date"])
    return rows


def _session_axis(all_dates: dict[str, set[date]], limit: date | None = None) -> list[date]:
    """市场会话轴：以池内实际出现的交易日为准。"""
    merged: set[date] = set()
    for dates in all_dates.values():
        merged |= dates
    axis = sorted(merged)
    if limit is not None:
        axis = [d for d in axis if d <= limit]
    return axis


def _is_number(value) -> bool:
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _validate_ohlc(row: dict) -> str | None:
    """返回异常原因；None 表示 OHLC 合法。布尔、零价、非有限值都算异常。"""
    for key in ("open", "high", "low", "close"):
        value = row.get(key)
        if not _is_number(value):
            return f"{key} 缺失、非数值或非有限值"
        if float(value) <= 0:
            return f"{key} 非正价格"
    if float(row["high"]) < float(row["low"]):
        return "high < low"
    if not (float(row["low"]) <= float(row["open"]) <= float(row["high"])):
        return "open 超出 high/low"
    if not (float(row["low"]) <= float(row["close"]) <= float(row["high"])):
        return "close 超出 high/low"
    return None


@dataclass
class DateResult:
    """单个交易日的计算结果（尚未发布）。"""

    trade_date: date
    events: list[dict]
    exceptions: list[dict]
    scope: list[str]
    summary: dict
    processed: int
    known: int
    unknown: int
    no_limit: int
    invalid: int
    out_of_scope: int = 0


def _null_event(trade_date: date, symbol: str) -> dict:
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "close_limit_up": None,
        "close_limit_down": None,
        "touched_limit_up": None,
        "touched_limit_down": None,
        "limit_up_price": None,
        "limit_down_price": None,
        "consecutive_up": None,
    }


def _compute_date(
    trade_date: date,
    scope: list[ScopeEntry],
    bars_by_symbol: dict[str, list[dict]],
    session_axis: list[date],
    prior: dict[str, tuple[date, bool | None, int | None]],
) -> DateResult:
    """计算单个交易日的事件、异常、范围与汇总。

    prior: symbol -> (状态所属会话日, 是否收盘涨停, 当时连板数)。
    """
    from tdxman.codec.price_rules import resolve_limit_rule
    from tdxman.models.enums import Market

    events: list[dict] = []
    exceptions: list[dict] = []
    processed_symbols: list[str] = []
    known = unknown = no_limit = invalid = out_of_scope = 0
    close_up = close_down = touched_up = touched_down = unsealed_up = unsealed_down = 0
    max_consec: int | None = None
    max_consec_unknown = False

    axis_index = {d: i for i, d in enumerate(session_axis)}
    idx = axis_index.get(trade_date)
    prev_session = session_axis[idx - 1] if (idx is not None and idx > 0) else None

    for entry in scope:
        symbol = entry.symbol
        rows = bars_by_symbol.get(symbol)
        if rows is None:
            continue
        # 指数/板块类不属于个股涨跌停范围：排除出 scope，不记为 UNKNOWN。
        if _is_index_like(Market[entry.market], entry.code, entry.name or ""):
            out_of_scope += 1
            prior[symbol] = (trade_date, None, None)
            continue
        processed_symbols.append(symbol)

        row = next((r for r in rows if r["trade_date"] == trade_date), None)
        if row is None:
            unknown += 1
            max_consec_unknown = True
            exceptions.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "kind": "UNKNOWN",
                    "reason": "该会话无日线记录（停牌或缺失），无法判定事件",
                    "affected_fields": "close_limit_up,close_limit_down,touched_limit_up,"
                    "touched_limit_down,consecutive_up",
                }
            )
            events.append(_null_event(trade_date, symbol))
            prior[symbol] = (trade_date, None, None)
            continue

        invalid_reason = _validate_ohlc(row)
        if invalid_reason:
            invalid += 1
            exceptions.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "kind": "INVALID",
                    "reason": invalid_reason,
                    "affected_fields": "close_limit_up,close_limit_down,touched_limit_up,"
                    "touched_limit_down,consecutive_up",
                }
            )
            events.append(_null_event(trade_date, symbol))
            prior[symbol] = (trade_date, None, None)
            max_consec_unknown = True
            continue

        # ST 三态：只接受真正的布尔；其他类型视为无依据。
        raw_st = row.get("is_st")
        st_status = raw_st if isinstance(raw_st, bool) else None

        observed = sum(
            1
            for r in rows
            if r["trade_date"] <= trade_date
            and (entry.listing_date is None or r["trade_date"] >= entry.listing_date)
        )
        resolved = resolve_limit_rule(
            Market[entry.market],
            entry.code,
            entry.name or "",
            trade_date,
            st_status,
            listed_days=_listed_days(entry, session_axis, trade_date),
            observed_sessions=observed,
        )

        if resolved.is_no_limit:
            no_limit += 1
            exceptions.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "kind": "NO_LIMIT",
                    "reason": resolved.no_limit_basis or "无涨跌幅限制",
                    "affected_fields": "close_limit_up,close_limit_down,touched_limit_up,"
                    "touched_limit_down,limit_up_price,limit_down_price,consecutive_up",
                }
            )
            events.append(_null_event(trade_date, symbol))
            prior[symbol] = (trade_date, None, None)
            max_consec_unknown = True
            continue

        if not resolved.is_known:
            unknown += 1
            exceptions.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "kind": "UNKNOWN",
                    "reason": resolved.reason or "无可靠规则依据",
                    "affected_fields": "close_limit_up,close_limit_down,touched_limit_up,"
                    "touched_limit_down,limit_up_price,limit_down_price,consecutive_up",
                }
            )
            events.append(_null_event(trade_date, symbol))
            prior[symbol] = (trade_date, None, None)
            max_consec_unknown = True
            continue

        # 参考价：只接受当日自带的 pre_close。不做跨日收盘替代（除权等日期不可直接替代）。
        pre_close = row.get("pre_close")
        if not _is_number(pre_close) or float(pre_close) <= 0:
            unknown += 1
            exceptions.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "kind": "UNKNOWN",
                    "reason": "当日无可靠参考前收盘价（不做跨日收盘替代）",
                    "affected_fields": "limit_up_price,limit_down_price,close_limit_up,"
                    "close_limit_down,touched_limit_up,touched_limit_down,consecutive_up",
                }
            )
            events.append(_null_event(trade_date, symbol))
            prior[symbol] = (trade_date, None, None)
            max_consec_unknown = True
            continue

        pct = resolved.rule.limit_pct
        limit_up = round(float(pre_close) * (1 + pct) + 0.00001, 2)
        limit_down = round(float(pre_close) * (1 - pct) + 0.00001, 2)

        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])

        c_up = close >= limit_up
        c_down = close <= limit_down
        t_up = high >= limit_up
        t_down = low <= limit_down

        known += 1
        close_up += int(c_up)
        close_down += int(c_down)
        touched_up += int(t_up)
        touched_down += int(t_down)
        unsealed_up += int(t_up and not c_up)
        unsealed_down += int(t_down and not c_down)

        consecutive: int | None = None
        if not c_up:
            consecutive = 0
        else:
            tracked = prior.get(symbol)
            prev_state = tracked[1] if tracked is not None and tracked[0] == prev_session else None
            if prev_session is None or prev_state is None:
                consecutive = None
                max_consec_unknown = True
            elif prev_state is False:
                consecutive = 1
            else:
                prev_n = tracked[2] if tracked is not None else None
                consecutive = None if prev_n is None else prev_n + 1
                if consecutive is None:
                    max_consec_unknown = True

        if consecutive is not None:
            max_consec = consecutive if max_consec is None else max(max_consec, consecutive)

        prior[symbol] = (trade_date, c_up, consecutive)

        events.append(
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "close_limit_up": c_up,
                "close_limit_down": c_down,
                "touched_limit_up": t_up,
                "touched_limit_down": t_down,
                "limit_up_price": limit_up,
                "limit_down_price": limit_down,
                "consecutive_up": consecutive,
            }
        )

    denominator = close_up + unsealed_up
    sealed_ratio = (close_up / denominator) if denominator else None
    if max_consec is not None:
        note = "已知样本最大值；存在连板未知的个股" if max_consec_unknown else "全覆盖范围内确定值"
    elif max_consec_unknown:
        note = "存在连板未知的个股，无法给出市场最高连板"
    else:
        note = None

    summary = {
        "trade_date": trade_date,
        "close_limit_up_count": close_up,
        "close_limit_down_count": close_down,
        "touched_limit_up_count": touched_up,
        "touched_limit_down_count": touched_down,
        "touched_unsealed_up_count": unsealed_up,
        "touched_unsealed_down_count": unsealed_down,
        "max_consecutive_up": max_consec,
        "max_consecutive_up_note": note,
        "sealed_ratio": sealed_ratio,
        "known_count": known,
        "unknown_count": unknown,
        "no_limit_count": no_limit,
        "invalid_count": invalid,
    }
    # 事件表只保留至少一个标志为 true 的行。
    kept = [e for e in events if any(e[k] is True for k in _EVENT_FLAGS)]
    return DateResult(
        trade_date=trade_date,
        events=kept,
        exceptions=exceptions,
        scope=processed_symbols,
        summary=summary,
        processed=len(processed_symbols),
        known=known,
        unknown=unknown,
        no_limit=no_limit,
        invalid=invalid,
        out_of_scope=out_of_scope,
    )


def _is_index_like(market, code: str, name: str) -> bool:
    from tdxman.codec.price_rules import _is_index_like as inner

    return inner(market, code, name)


def _previous_session_states(
    root: Path, trade_date: date, symbols: list[str]
) -> dict[str, tuple[date, bool | None, int | None]]:
    """上一条已发布批次中各证券的收盘状态。"""
    prior: dict[str, tuple[date, bool | None, int | None]] = {}
    with catalog(root) as conn:
        # stale 的前日状态不可作为可靠递推前史。
        prev = conn.execute(
            """
            select max(p.trade_date) from daily_limit_publication p
            where p.trade_date < ?
              and not exists (
                  select 1 from daily_limit_staleness st where st.trade_date = p.trade_date
              )
            """,
            [trade_date],
        ).fetchone()[0]
        if prev is None:
            return prior
        rows = conn.execute(
            """
            select e.symbol, e.close_limit_up, e.consecutive_up
            from daily_limit_events e
            join daily_limit_publication p on p.trade_date = e.trade_date
            where e.trade_date = ?
            """,
            [prev],
        ).fetchall()
        with_events = {row[0]: (row[1], row[2]) for row in rows}
        unknown = {
            row[0]
            for row in conn.execute(
                """
                select distinct x.symbol from daily_limit_exceptions x
                join daily_limit_publication p on p.trade_date = x.trade_date
                where x.trade_date = ? and x.kind in ('UNKNOWN', 'INVALID', 'NO_LIMIT')
                """,
                [prev],
            ).fetchall()
        }
        in_scope = {
            row[0]
            for row in conn.execute(
                """
                select s.symbol from daily_limit_scope s
                join daily_limit_publication p on p.trade_date = s.trade_date
                where s.trade_date = ?
                """,
                [prev],
            ).fetchall()
        }
    for symbol in symbols:
        if symbol in unknown:
            prior[symbol] = (prev, None, None)
        elif symbol in with_events:
            state, consec = with_events[symbol]
            prior[symbol] = (prev, state, consec)
        elif symbol in in_scope:
            prior[symbol] = (prev, False, 0)
    return prior


def _mark_stale(root: Path, dates: list[date], reason: str) -> None:
    if not dates:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(root) as conn:
        for day in dates:
            conn.execute(
                """
                insert into daily_limit_staleness values (?, ?, ?)
                on conflict(trade_date) do update set
                    reason = excluded.reason, marked_at = excluded.marked_at
                """,
                [day, reason, now],
            )


def _published_dates_from(root: Path, start: date) -> list[date]:
    """返回从 start 起已有发布结果的日期，不依赖当前源数据会话轴。"""
    with catalog(root) as conn:
        rows = conn.execute(
            "select trade_date from daily_limit_publication where trade_date >= ?",
            [start],
        ).fetchall()
    return [row[0] for row in rows]


def _clear_stale(conn, dates: list[date]) -> None:
    for day in dates:
        conn.execute("delete from daily_limit_staleness where trade_date = ?", [day])


def _publish_batch(root: Path, batch_id: str, result: DateResult, scope_id: str) -> None:
    """原子发布一个批次：写事件/异常/范围/汇总并切换发布指针。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(root) as conn:
        conn.execute("BEGIN")
        try:
            for table in (
                "daily_limit_events",
                "daily_limit_exceptions",
                "daily_limit_summary",
                "daily_limit_batches",
                "daily_limit_scope",
            ):
                conn.execute(f"delete from {table} where trade_date = ?", [result.trade_date])
            conn.execute(
                "insert into daily_limit_batches values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    batch_id,
                    result.trade_date,
                    scope_id,
                    RULE_VERSION,
                    now,
                    result.processed,
                    result.known,
                    result.unknown,
                    result.no_limit,
                    result.invalid,
                    result.out_of_scope,
                    "published",
                    None,
                ],
            )
            for event in result.events:
                conn.execute(
                    "insert into daily_limit_events values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        batch_id,
                        event["trade_date"],
                        event["symbol"],
                        event["close_limit_up"],
                        event["close_limit_down"],
                        event["touched_limit_up"],
                        event["touched_limit_down"],
                        event["limit_up_price"],
                        event["limit_down_price"],
                        event["consecutive_up"],
                    ],
                )
            for exc in result.exceptions:
                conn.execute(
                    "insert into daily_limit_exceptions values (?, ?, ?, ?, ?, ?)",
                    [
                        batch_id,
                        exc["trade_date"],
                        exc["symbol"],
                        exc["kind"],
                        exc["reason"],
                        exc["affected_fields"],
                    ],
                )
            for symbol in result.scope:
                conn.execute(
                    "insert into daily_limit_scope values (?, ?, ?)",
                    [batch_id, result.trade_date, symbol],
                )
            s = result.summary
            conn.execute(
                "insert into daily_limit_summary values "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    batch_id,
                    s["trade_date"],
                    scope_id,
                    s["close_limit_up_count"],
                    s["close_limit_down_count"],
                    s["touched_limit_up_count"],
                    s["touched_limit_down_count"],
                    s["touched_unsealed_up_count"],
                    s["touched_unsealed_down_count"],
                    s["max_consecutive_up"],
                    s["max_consecutive_up_note"],
                    s["sealed_ratio"],
                    s["known_count"],
                    s["unknown_count"],
                    s["no_limit_count"],
                    s["invalid_count"],
                ],
            )
            conn.execute(
                """
                insert into daily_limit_publication values (?, ?, ?)
                on conflict(trade_date) do update set
                    batch_id = excluded.batch_id, published_at = excluded.published_at
                """,
                [result.trade_date, batch_id, now],
            )
            _clear_stale(conn, [result.trade_date])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _board_label(market: str, code: str) -> str:
    """Use the production rule engine's board identity for report grouping."""
    from tdxman.codec.price_rules import is_bj_board, is_gem_board, is_star_board

    if is_star_board(code):
        return "科创板"
    if is_gem_board(code):
        return "创业板"
    if is_bj_board(code):
        return "北交所"
    if (market == "SH" and code.startswith("60")) or (
        market == "SZ" and code.startswith("00")
    ):
        return "主板"
    return market


def summarize_published_limit_quality(root: Path, trade_dates: list[date]) -> list[dict]:
    """Summarize published rule statuses by board without reimplementing rules."""
    reports = []
    with catalog(root) as conn:
        for trade_date in sorted(set(trade_dates)):
            publication = conn.execute(
                "select batch_id from daily_limit_publication where trade_date = ?",
                [trade_date],
            ).fetchone()
            if publication is None:
                continue
            batch_id = publication[0]
            scope = [
                row[0]
                for row in conn.execute(
                    "select symbol from daily_limit_scope where trade_date = ? and batch_id = ?",
                    [trade_date, batch_id],
                ).fetchall()
            ]
            exceptions = conn.execute(
                "select symbol, kind, reason from daily_limit_exceptions "
                "where trade_date = ? and batch_id = ?",
                [trade_date, batch_id],
            ).fetchall()
            events = {
                row[0]
                for row in conn.execute(
                    "select symbol from daily_limit_events where trade_date = ? and batch_id = ?",
                    [trade_date, batch_id],
                ).fetchall()
            }

            statuses = {symbol: ("KNOWN", None) for symbol in scope}
            for symbol, kind, reason in exceptions:
                statuses[symbol] = (kind, reason)
            for symbol in events:
                statuses[symbol] = ("KNOWN", None)

            grouped: dict[str, dict] = {}
            for symbol, (status, reason) in statuses.items():
                code, market = _split_symbol(symbol)
                board = _board_label(market, code)
                group = grouped.setdefault(
                    board,
                    {
                        "board": board,
                        "processed_rows": 0,
                        "KNOWN": 0,
                        "UNKNOWN": 0,
                        "NO_LIMIT": 0,
                        "INVALID": 0,
                        "unknown_reasons": {},
                    },
                )
                group["processed_rows"] += 1
                group[status] = group.get(status, 0) + 1
                if status == "UNKNOWN":
                    group["unknown_reasons"][reason or "未提供原因"] = (
                        group["unknown_reasons"].get(reason or "未提供原因", 0) + 1
                    )
            reports.append(
                {
                    "trade_date": trade_date.isoformat(),
                    "scope": "stock",
                    "batch_id": batch_id,
                    "boards": [grouped[key] for key in sorted(grouped)],
                }
            )
    return reports


def _published_consecutive(root: Path, trade_date: date) -> dict[str, int | None]:
    """该日已发布事件行的 (symbol -> consecutive_up)。"""
    with catalog(root) as conn:
        rows = conn.execute(
            """
            select e.symbol, e.consecutive_up from daily_limit_events e
            join daily_limit_publication p
              on p.trade_date = e.trade_date and p.batch_id = e.batch_id
            where e.trade_date = ?
            """,
            [trade_date],
        ).fetchall()
    return {row[0]: row[1] for row in rows}


@writer
def compute_limit_events(
    root: Path,
    trade_dates: list[date],
    *,
    scope_id: str = "stock",
    asset_type: str = "stock",
    propagate: bool = True,
) -> list[str]:
    """计算并发布派生事件。

    计划 = 从**最早请求日**起、在市场会话轴上的**连续后缀**，直到最晚的
    请求日或已发布日期。因此两个请求日之间的依赖会话不会被跳过。
    为正确性优先，首版不做提前退出：受影响后缀整体重算，结果与完整顺序重算一致。

    陈旧：在派生开始前即把计划内日期标记 stale，逐日成功发布后再清除；
    任何早期失败（范围加载、源读取、前史读取）或中途失败都不会留下
    "未标记的旧结果"。

    Returns:
        实际发布的 batch_id 列表（升序）。
    """
    if asset_type not in SUPPORTED_SCOPES:
        raise DataPoolError("SCOPE_UNSUPPORTED", f"unsupported scope: {asset_type}")
    if scope_id not in SUPPORTED_SCOPE_IDS:
        raise DataPoolError("SCOPE_UNSUPPORTED", f"unsupported scope_id: {scope_id}")
    if not trade_dates:
        return []
    initialize_limits(root)
    ordered = sorted(set(trade_dates))

    # 先按已有发布记录标记整个依赖尾部。此处必须早于范围/源读取，且不能
    # 依赖当前会话轴：源日期可能已被删除或会话轴可能已经缩短。
    impact_dates = sorted(set(ordered) | set(_published_dates_from(root, ordered[0])))
    _mark_stale(root, impact_dates, "已请求重算，派生未完成")

    scope = load_scope(root, asset_type)
    if not scope:
        raise DataPoolError("DAILY_NOT_FOUND", "aspool 中没有可处理的日线")

    bars_by_symbol: dict[str, list[dict]] = {}
    all_dates: dict[str, set[date]] = {}
    for entry in scope:
        rows = _read_symbol_bars(root, entry.market, entry.code)
        bars_by_symbol[entry.symbol] = rows
        all_dates[entry.symbol] = {r["trade_date"] for r in rows}

    axis = _session_axis(all_dates)
    if not axis:
        raise DataPoolError("DAILY_NOT_FOUND", "aspool 会话轴为空")

    # 连续后缀：从最早请求日起，到最晚请求日与此前已发布日期中的较晚者。
    requested_span = [d for d in axis if ordered[0] <= d <= ordered[-1]]
    if not requested_span:
        raise DataPoolError("INVALID_ARGUMENT", "请求日期不在任何会话轴上")
    last = ordered[-1]
    if propagate:
        published_after = [d for d in axis if d > last and _is_published(root, d)]
        if published_after:
            last = max(last, published_after[-1])
    plan = [d for d in axis if ordered[0] <= d <= last]

    _mark_stale(root, plan, "受影响后缀待重算")

    symbols = [e.symbol for e in scope]
    prior = _previous_session_states(root, plan[0], symbols)

    batch_ids: list[str] = []
    for trade_date in plan:
        result = _compute_date(trade_date, scope, bars_by_symbol, axis, prior)
        batch_id = f"daily-limit-{trade_date:%Y%m%d}-{scope_id}"
        _publish_batch(root, batch_id, result, scope_id)
        batch_ids.append(batch_id)
    return batch_ids


def _is_published(root: Path, trade_date: date) -> bool:
    with catalog(root) as conn:
        row = conn.execute(
            "select 1 from daily_limit_publication where trade_date = ?", [trade_date]
        ).fetchone()
    return row is not None
