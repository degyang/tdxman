"""日终稀疏涨跌停事件：计算、幂等发布、历史更正传播。

逻辑契约见 Fundwise `docs/northstar/limit-data-pipeline.md`。
- 原始日线保持现有存储；本模块只写派生事件、批次、异常、范围与日汇总。
- 每证券每日最多一条事件行；四个事件标志**至少一个为 true** 才入表。
  四个全为 null（无法确认）只进异常与 scope，不进事件表。
- 读取只见唯一已发布批次；派生失败/更正未完成的日期标记为 stale。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

from .api_contract import DataPoolError
from .pool import writer
from .store import catalog

RULE_VERSION = "cn-a-share-limit-v8"

# 事件标志；至少一个为 true 才入事件表。
_EVENT_FLAGS = ("close_limit_up", "close_limit_down", "touched_limit_up", "touched_limit_down")

SUPPORTED_SCOPES = {"stock"}
# 首包 scope_id 与 asset_type 都限定为 stock，避免不同范围覆写同一日期键。
SUPPORTED_SCOPE_IDS = {"stock"}

INITIALIZE_SQL = (
    """
    create table if not exists daily_limit_streak_boundaries (
        batch_id varchar not null,
        trade_date date not null,
        symbol varchar not null,
        basis varchar not null,
        primary key (trade_date, symbol, batch_id)
    )
    """,
    """
    create table if not exists daily_limit_references (
        batch_id varchar not null,
        trade_date date not null,
        symbol varchar not null,
        stored_pre_close double,
        reference_pre_close double,
        close double,
        pct_chg double,
        candidate_min double,
        candidate_max double,
        basis varchar not null,
        primary key (trade_date, symbol, batch_id)
    )
    """,
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
        consecutive_gap_sessions integer,
        primary key (trade_date, symbol, batch_id)
    )
    """,
    """
    create table if not exists daily_limit_gap_states (
        batch_id varchar not null,
        trade_date date not null,
        symbol varchar not null,
        consecutive_up integer not null,
        consecutive_gap_sessions integer not null,
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
        conn.execute("alter table daily_limit_events add column if not exists "
                     "consecutive_gap_sessions integer")


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
    delisting_date: date | None = None


def _read_asset_types(root: Path, market: str, code: str) -> str | None:
    """读取该证券日线的 asset_type；无该列返回 None。"""
    import pyarrow.parquet as pq

    from .store import daily_paths

    for path in daily_paths(root, market, code):
        names = pq.ParquetFile(path).schema_arrow.names
        if "asset_type" in names:
            values = pq.ParquetFile(path).read(columns=["asset_type"])["asset_type"].to_pylist()
            from .change_protocol import note_range

            note_range("scope_read", rows=len(values), path=path,
                       bytes_proxy=path.stat().st_size)
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
    from .security_facts import lifecycle_map

    lifecycle = lifecycle_map(root)
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
            basic = lifecycle.get(f"{code}.{market}", {})
            out.append(
                ScopeEntry(
                    f"{code}.{market}",
                    market,
                    code,
                    names.get(code),
                    row_asset,
                    basic.get("listing_date") or listings.get(code),
                    basic.get("delisting_date"),
                )
            )
    return out


def _listed_days(
    entry: ScopeEntry, session_axis: list[date], trade_date: date,
    calendar: dict[date, bool] | None = None,
) -> int | None:
    """该证券截至 trade_date 的已上市交易日数；无可靠依据返回 None。

    只有覆盖上市日至目标日全部自然日的已保存日历才能用于精确计数。
    日历缺日时仍只用 observed_sessions 作为排除窗口的下界。
    """
    if entry.listing_date is not None and trade_date == entry.listing_date:
        return 1
    if entry.market != "BJ" and entry.listing_date is not None and calendar:
        if entry.listing_date not in calendar or trade_date not in calendar:
            return None
        days = [entry.listing_date + timedelta(days=i)
                for i in range((trade_date - entry.listing_date).days + 1)]
        if days and all(day in calendar for day in days):
            return sum(calendar[day] for day in days)
    return None


def _read_symbol_bars(root: Path, market: str, code: str) -> list[dict]:
    import pyarrow.parquet as pq

    from .store import daily_paths

    required = {"trade_date", "open", "high", "low", "close", "pre_close", "pre_close_source",
                "pct_chg", "is_st", "trading_status"}
    rows = []
    for path in daily_paths(root, market, code):
        parquet = pq.ParquetFile(path)
        columns = sorted(required.intersection(parquet.schema_arrow.names))
        from .change_protocol import note_range

        note_range("compute_read_files", path=path, bytes_proxy=path.stat().st_size)
        for batch in parquet.iter_batches(columns=columns):
            records = batch.to_pylist()
            note_range("compute_read", rows=len(records),
                       start=records[0]["trade_date"] if records else None,
                       end=records[-1]["trade_date"] if records else None)
            rows.extend(records)
    from .daily_storage import is_missing_value
    from .security_facts import daily_facts

    by_day = {row["trade_date"]: row for row in rows}
    for fact in daily_facts(root, f"{code}.{market}"):
        row = by_day.setdefault(fact["trade_date"], {"trade_date": fact["trade_date"]})
        for field in ("pre_close", "is_st", "trading_status"):
            if (fact.get(field) is not None
                    and (is_missing_value(row.get(field)) or fact.get("source") == "baostock")):
                row[field] = fact[field]
                if field == "pre_close":
                    row["pre_close_source"] = fact.get("source")
    ordered = [by_day[day] for day in sorted(by_day)]
    for previous, current in zip(ordered, ordered[1:]):
        current["previous_trade_date"] = previous["trade_date"]
        current["previous_close"] = previous.get("close")
    return ordered


class _SymbolBars:
    """Compact, date-indexed bars for one security and one calculation year."""

    def __init__(self, rows: list[dict], start: date, end: date, listing_date: date | None):
        import numpy as np
        import pyarrow as pa

        window = []
        self.prior_count = 0
        for row in rows:
            day = row["trade_date"]
            if day < start:
                if listing_date is None or day >= listing_date:
                    self.prior_count += 1
            elif day <= end:
                window.append(row)

        # Facts-only rows may come before traded bars. Preserve every field,
        # including those absent from the first row in this window.
        columns = sorted({key for row in window for key in row})
        self.table = (
            pa.Table.from_pylist([{key: row.get(key) for key in columns} for row in window])
            if window else None
        )
        if self.table is None:
            self.days = np.array([], dtype=np.int32)
        else:
            self.days = (
                self.table["trade_date"]
                .cast(pa.int32())
                .combine_chunks()
                .to_numpy(zero_copy_only=False)
            )
        self.listing_index = (
            int(np.searchsorted(self.days, (listing_date - date(1970, 1, 1)).days))
            if listing_date is not None and listing_date >= start
            else 0
        )

    def on(self, day: date) -> dict | None:
        import numpy as np

        ordinal = (day - date(1970, 1, 1)).days
        index = int(np.searchsorted(self.days, ordinal, side="left"))
        if index == len(self.days) or self.days[index] != ordinal:
            return None
        assert self.table is not None
        return self.table.slice(index, 1).to_pylist()[0]

    def observed_sessions(self, day: date) -> int:
        import numpy as np

        ordinal = (day - date(1970, 1, 1)).days
        through = int(np.searchsorted(self.days, ordinal, side="right"))
        return self.prior_count + max(0, through - self.listing_index)


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


def _reference_price(row: dict, previous_session: date | None = None):
    """Recover only a unique cent price from the SAME day's rounded return.

    Legacy imported raw OHLC and pre_close can use different adjustment bases.
    Use a two-decimal percentage interval, including for legacy three-decimal
    returns. Source rounding may be coarser than the displayed precision.
    Never choose a price if that interval permits zero or multiple cent prices.
    The original bar is unchanged; every correction/rejection is audited.
    """
    stored = row.get("pre_close")
    # A share price must be on the cent grid, allowing only float32 transport
    # noise. Adjusted OHLC cannot be combined with an unadjusted daily return.
    for field in ("open", "high", "low", "close"):
        value = row.get(field)
        if _is_number(value) and abs(float(value) - float(
                Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        )) > max(.00001, abs(float(value)) * 1.5e-7):
            return None, {
                "stored_pre_close": float(stored) if _is_number(stored) else None,
                "reference_pre_close": None,
                "close": float(row["close"]) if _is_number(row.get("close")) else None,
                "pct_chg": float(row["pct_chg"]) if _is_number(row.get("pct_chg")) else None,
                "candidate_min": None, "candidate_max": None,
                "basis": "ohlc_not_on_price_grid",
            }
    if row.get("pre_close_source") in {"baostock", "derived:tdx_xdxr"}:
        return (float(Decimal(str(stored)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
                if _is_number(stored) and stored > 0 else None), None
    pct = row.get("pct_chg")
    if not _is_number(pct) or not _is_number(row.get("close")):
        return (float(Decimal(str(stored)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
                if _is_number(stored) and stored > 0 else None), None
    close = Decimal(str(row["close"])).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    base = Decimal(100) + Decimal(str(pct))
    error = Decimal(".005000001")
    if close <= 0 or base <= error:
        return stored, None
    lower = (close * 10000 / (base + error)).to_integral_value(rounding=ROUND_CEILING)
    upper = (close * 10000 / (base - error)).to_integral_value(rounding=ROUND_FLOOR)
    if _is_number(stored) and stored > 0:
        cents = (Decimal(str(stored)) * 100).to_integral_value(rounding=ROUND_HALF_UP)
        if lower <= cents <= upper:
            reference = float(cents / 100)
            if abs(reference - float(stored)) < 1e-8:
                return reference, None
            return reference, {
                "stored_pre_close": float(stored), "reference_pre_close": reference,
                "close": float(close), "pct_chg": float(pct),
                "candidate_min": float(lower / 100), "candidate_max": float(upper / 100),
                "basis": "validated_reference_cent_normalization",
            }
    reference = float(lower / 100) if lower == upper and lower > 0 else None
    basis = ("same_day_return_unique_cent" if reference is not None
             else "same_day_return_no_unique_cent")
    if (reference is not None and previous_session is not None
            and row.get("previous_trade_date") == previous_session
            and _is_number(row.get("previous_close"))
            and Decimal(str(row["previous_close"])).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP) == lower / 100):
        basis = "previous_session_close_confirmed_by_return"
    return reference, {
        "stored_pre_close": float(stored) if _is_number(stored) else None,
        "reference_pre_close": reference,
        "close": float(close), "pct_chg": float(pct),
        "candidate_min": float(lower / 100), "candidate_max": float(upper / 100),
        "basis": basis,
    }


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
    references: list[dict] = dataclass_field(default_factory=list)
    gap_states: list[dict] = dataclass_field(default_factory=list)
    streak_boundaries: list[dict] = dataclass_field(default_factory=list)


def _gap_sessions(state) -> int:
    """Accept earlier three-item states while carrying the v7 gap provenance."""
    return int(state[3] or 0) if state is not None and len(state) > 3 else 0


def _exclude_legacy_ipo_from_streak(entry: ScopeEntry, trade_date: date) -> bool:
    """Exclude a confirmed legacy main-board IPO date from streak counting only."""
    from tdxman.codec.price_rules import (
        MAIN_BOARD_IPO_WINDOW_EFFECTIVE,
        MAIN_BOARD_LEGACY_IPO_EFFECTIVE,
    )

    main_board = ((entry.market == "SH" and entry.code.startswith("60"))
                  or (entry.market == "SZ" and entry.code.startswith("00")))
    return (main_board and entry.listing_date == trade_date
            and MAIN_BOARD_LEGACY_IPO_EFFECTIVE <= trade_date < MAIN_BOARD_IPO_WINDOW_EFFECTIVE)


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
    prior: dict[str, tuple],
    calendar: dict[date, bool] | None = None,
) -> DateResult:
    """计算单个交易日的事件、异常、范围与汇总。

    prior: symbol -> (状态所属会话日, 是否收盘涨停, 当时连板数, 累计跳过缺行会话数)。
    """
    from tdxman.codec.price_rules import resolve_limit_rule
    from tdxman.models.enums import Market

    events: list[dict] = []
    exceptions: list[dict] = []
    processed_symbols: list[str] = []
    references: list[dict] = []
    gap_states: list[dict] = []
    streak_boundaries: list[dict] = []
    known = unknown = no_limit = invalid = out_of_scope = 0
    close_up = close_down = touched_up = touched_down = unsealed_up = unsealed_down = 0
    max_consec: int | None = None
    max_consec_unknown = False
    has_gap_streak = False

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
        if ((entry.listing_date is not None and trade_date < entry.listing_date)
                or (entry.delisting_date is not None and trade_date >= entry.delisting_date)):
            out_of_scope += 1
            prior[symbol] = (trade_date, None, None)
            continue
        row = (
            rows.on(trade_date)
            if isinstance(rows, _SymbolBars)
            else next((r for r in rows if r["trade_date"] == trade_date), None)
        )
        if row is not None and row.get("trading_status") == "SUSPENDED":
            # No trading events or breadth denominator on a confirmed suspension.
            # Pause the streak, advancing its session stamp without incrementing it.
            tracked = prior.get(symbol)
            prior[symbol] = (
                (trade_date, tracked[1], tracked[2], _gap_sessions(tracked))
                if tracked is not None and tracked[0] == prev_session
                else (trade_date, None, None)
            )
            out_of_scope += 1
            continue
        processed_symbols.append(symbol)
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
            tracked = prior.get(symbol)
            if (tracked is not None and tracked[0] == prev_session
                    and tracked[1] is True and tracked[2] is not None and tracked[2] > 0):
                # User-defined continuity: absent bars pause an already known
                # up streak. The absent session remains UNKNOWN, not suspended.
                gaps = _gap_sessions(tracked) + 1
                prior[symbol] = (trade_date, True, tracked[2], gaps)
                gap_states.append({"trade_date": trade_date, "symbol": symbol,
                                   "consecutive_up": tracked[2],
                                   "consecutive_gap_sessions": gaps})
            else:
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

        observed = (
            rows.observed_sessions(trade_date)
            if isinstance(rows, _SymbolBars)
            else sum(
                1
                for r in rows
                if r["trade_date"] <= trade_date
                and (entry.listing_date is None or r["trade_date"] >= entry.listing_date)
            )
        )
        resolved = resolve_limit_rule(
            Market[entry.market],
            entry.code,
            entry.name or "",
            trade_date,
            st_status,
            listed_days=(None if observed > 5 else
                         _listed_days(entry, session_axis, trade_date, calendar)),
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
            prior[symbol] = (trade_date, False, 0)
            continue

        if not resolved.is_known:
            unknown += 1
            exclude_ipo = _exclude_legacy_ipo_from_streak(entry, trade_date)
            reason = resolved.reason or "无可靠规则依据"
            affected = ("close_limit_up,close_limit_down,touched_limit_up,"
                        "touched_limit_down,limit_up_price,limit_down_price")
            if exclude_ipo:
                reason += "；连板口径排除已确认的上市首日，次日起计数"
                streak_boundaries.append({"trade_date": trade_date, "symbol": symbol,
                                          "basis": "legacy_ipo_first_day_excluded"})
            else:
                affected += ",consecutive_up"
            exceptions.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "kind": "UNKNOWN",
                    "reason": reason,
                    "affected_fields": affected,
                }
            )
            events.append(_null_event(trade_date, symbol))
            prior[symbol] = (trade_date, False, 0, 0) if exclude_ipo else (trade_date, None, None)
            max_consec_unknown = True
            continue

        # Same-day evidence only; never substitute the previous row's close.
        pre_close, reference_audit = _reference_price(row, prev_session)
        if reference_audit is not None:
            references.append({"trade_date": trade_date, "symbol": symbol, **reference_audit})
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

        def cents(value):
            return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        pct = Decimal(str(resolved.rule.limit_pct))
        reference = cents(pre_close)
        limit_up = float(cents(reference * (1 + pct)))
        limit_down = float(cents(reference * (1 - pct)))

        close = float(cents(row["close"]))
        high = float(cents(row["high"]))
        low = float(cents(row["low"]))

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
        consecutive_gaps = 0
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
                if consecutive is not None:
                    consecutive_gaps = _gap_sessions(tracked)
                if consecutive is None:
                    max_consec_unknown = True

        if consecutive is not None:
            max_consec = consecutive if max_consec is None else max(max_consec, consecutive)

        prior[symbol] = (trade_date, c_up, consecutive, consecutive_gaps)
        has_gap_streak = has_gap_streak or consecutive_gaps > 0

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
                "consecutive_gap_sessions": consecutive_gaps if consecutive is not None else None,
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
    if has_gap_streak:
        note = (note + "；" if note else "") + "含跨缺行接续连板，缺行会话不计天数"

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
        references=references,
        gap_states=gap_states,
        streak_boundaries=streak_boundaries,
    )


def _is_index_like(market, code: str, name: str) -> bool:
    from tdxman.codec.price_rules import _is_index_like as inner

    return inner(market, code, name)


def _previous_session_states(
    root: Path, trade_date: date, symbols: list[str]
) -> dict[str, tuple]:
    """上一条已发布批次中各证券的收盘状态。"""
    prior: dict[str, tuple] = {}
    with catalog(root) as conn:
        # stale 的前日状态不可作为可靠递推前史。
        prev = conn.execute(
            """
            select max(p.trade_date) from daily_limit_publication p
            join daily_limit_batches b on b.batch_id = p.batch_id
            where p.trade_date < ? and b.rule_version = ?
              and not exists (
                  select 1 from daily_limit_staleness st where st.trade_date = p.trade_date
              )
            """,
            [trade_date, RULE_VERSION],
        ).fetchone()[0]
        if prev is None:
            return prior
        rows = conn.execute(
            """
            select e.symbol, e.close_limit_up, e.consecutive_up, e.consecutive_gap_sessions
            from daily_limit_events e
            join daily_limit_publication p on p.trade_date = e.trade_date
            where e.trade_date = ?
            """,
            [prev],
        ).fetchall()
        with_events = {row[0]: (row[1], row[2], row[3] or 0) for row in rows}
        gap_states = {
            row[0]: (prev, True, row[1], row[2])
            for row in conn.execute(
                """select g.symbol, g.consecutive_up, g.consecutive_gap_sessions
                   from daily_limit_gap_states g
                   join daily_limit_publication p using (trade_date, batch_id)
                   where g.trade_date = ?""", [prev]).fetchall()
        }
        streak_boundaries = {
            row[0] for row in conn.execute(
                """select z.symbol from daily_limit_streak_boundaries z
                   join daily_limit_publication p using (trade_date, batch_id)
                   where z.trade_date = ? and z.basis = 'legacy_ipo_first_day_excluded'""",
                [prev]).fetchall()
        }
        unknown = {
            row[0]
            for row in conn.execute(
                """
                select distinct x.symbol from daily_limit_exceptions x
                join daily_limit_publication p on p.trade_date = x.trade_date
                where x.trade_date = ? and x.kind in ('UNKNOWN', 'INVALID')
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
        if symbol in streak_boundaries:
            prior[symbol] = (prev, False, 0, 0)
        elif symbol in gap_states:
            prior[symbol] = gap_states[symbol]
        elif symbol in unknown:
            prior[symbol] = (prev, None, None)
        elif symbol in with_events:
            state, consec, gaps = with_events[symbol]
            prior[symbol] = (prev, state, consec, gaps)
        elif symbol in in_scope:
            prior[symbol] = (prev, False, 0)
    # A previous published suspension was outside that day's traded scope.
    # Recover its earlier state across confirmed suspended sessions. An earlier
    # missing-bar carry is restored from daily_limit_gap_states by the same reader.
    from .security_facts import calendar_days, daily_facts

    calendar = calendar_days(root)
    for symbol in symbols:
        if symbol in prior or not calendar:
            continue
        facts = {r["trade_date"]: r for r in daily_facts(root, symbol, end=prev)}
        cursor = prev
        suspended = []
        while facts.get(cursor, {}).get("trading_status") == "SUSPENDED":
            suspended.append(cursor)
            cursor -= timedelta(days=1)
            while cursor in calendar and not calendar[cursor]:
                cursor -= timedelta(days=1)
            if cursor not in calendar:
                break
        if suspended and cursor in calendar:
            earlier = _previous_session_states(root, min(suspended), [symbol]).get(symbol)
            if earlier is not None and earlier[0] == cursor:
                prior[symbol] = (prev, earlier[1], earlier[2], _gap_sessions(earlier))
    return prior


def _mark_stale(root: Path, dates: list[date], reason: str) -> None:
    if not dates:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(root) as conn:
        conn.execute(
            "insert or replace into daily_limit_staleness "
            "select unnest(?), ?, ?", [dates, reason, now],
        )
        from .change_protocol import note_range

        note_range("compute_stale_requested", rows=len(dates), start=min(dates), end=max(dates))


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


def _insert_rows(conn, table: str, rows: list[dict]) -> None:
    """Insert a daily batch without one SQL round trip per security."""
    if not rows:
        return
    import pyarrow as pa

    conn.register("_limit_batch_rows", pa.Table.from_pylist(rows))
    try:
        conn.execute(f"insert into {table} by name select * from _limit_batch_rows")
    finally:
        conn.unregister("_limit_batch_rows")


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
                "daily_limit_references",
                "daily_limit_gap_states",
                "daily_limit_streak_boundaries",
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
            _insert_rows(conn, "daily_limit_events", [
                {"batch_id": batch_id, **event} for event in result.events
            ])
            _insert_rows(conn, "daily_limit_exceptions", [
                {"batch_id": batch_id, **exc} for exc in result.exceptions
            ])
            _insert_rows(conn, "daily_limit_scope", [
                {"batch_id": batch_id, "trade_date": result.trade_date, "symbol": symbol}
                for symbol in result.scope
            ])
            _insert_rows(conn, "daily_limit_references", [
                {"batch_id": batch_id, **reference} for reference in result.references
            ])
            _insert_rows(conn, "daily_limit_gap_states", [
                {"batch_id": batch_id, **state} for state in result.gap_states
            ])
            _insert_rows(conn, "daily_limit_streak_boundaries", [
                {"batch_id": batch_id, **boundary} for boundary in result.streak_boundaries
            ])
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
            from .change_protocol import note_range

            note_range("recompute_published", rows=result.processed,
                       start=result.trade_date, end=result.trade_date)
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
    cache_years: int = 1,
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
    if isinstance(cache_years, bool) or not isinstance(cache_years, int) or cache_years < 1:
        raise DataPoolError("INVALID_ARGUMENT", "cache_years must be a positive integer")
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

    # Keep only the market calendar globally. Full Python histories for all
    # securities can exceed RAM even when requesting a single session.
    import pyarrow.parquet as pq

    from .store import daily_paths

    market_dates: set[date] = set()
    for entry in scope:
        for path in daily_paths(root, entry.market, entry.code):
            from .change_protocol import note_range

            note_range("axis_read_files", path=path, bytes_proxy=path.stat().st_size)
            for batch in pq.ParquetFile(path).iter_batches(columns=["trade_date"]):
                days = batch.column(0).to_pylist()
                note_range("axis_read", rows=len(batch), start=min(days) if days else None,
                           end=max(days) if days else None)
                market_dates.update(days)

    from .security_facts import calendar_days

    calendar = calendar_days(root)
    if market_dates:
        first_market_date = min(market_dates)
        last_market_date = max(ordered[-1], max(market_dates))
        market_dates.update(day for day, opened in calendar.items()
                            if opened and first_market_date <= day <= last_market_date)
    axis = sorted(market_dates)
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
    scope_by_symbol = {entry.symbol: entry for entry in scope}
    years = sorted({day.year for day in plan})
    for offset in range(0, len(years), cache_years):
        group = set(years[offset:offset + cache_years])
        year_plan = [day for day in plan if day.year in group]

        class SymbolRows:
            """Cache compact rows only for the selected window, not full histories."""

            def __init__(self):
                self.cache: dict[str, _SymbolBars] = {}

            def get(self, symbol):
                if symbol not in self.cache:
                    code, market = _split_symbol(symbol)
                    entry = scope_by_symbol[symbol]
                    rows = _read_symbol_bars(root, market, code)
                    self.cache[symbol] = _SymbolBars(
                        rows, year_plan[0], year_plan[-1], entry.listing_date
                    )
                return self.cache[symbol]

        bars_by_symbol = SymbolRows()
        for trade_date in year_plan:
            result = _compute_date(trade_date, scope, bars_by_symbol, axis, prior, calendar)
            batch_id = f"daily-limit-{trade_date:%Y%m%d}-{scope_id}"
            _publish_batch(root, batch_id, result, scope_id)
            batch_ids.append(batch_id)
            logging.getLogger(__name__).info(
                "Published %s (%d/%d): known=%d unknown=%d invalid=%d limit_up=%d",
                trade_date, len(batch_ids), len(plan), result.known, result.unknown,
                result.invalid, result.summary["close_limit_up_count"],
            )
    return batch_ids


def _is_published(root: Path, trade_date: date) -> bool:
    with catalog(root) as conn:
        row = conn.execute(
            "select 1 from daily_limit_publication where trade_date = ?", [trade_date]
        ).fetchone()
    return row is not None
