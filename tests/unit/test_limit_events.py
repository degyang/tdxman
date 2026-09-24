"""日终稀疏涨跌停事件：规则、事件、连板、传播、陈旧、发布与公开读取。

全部使用临时存储，不联网。

会话轴说明：创业板/主板/科创板上市前 5 个交易日无涨跌幅限制，
因此凡是要断言 KNOWN 的用例都必须提供足以**排除**该窗口的前史
（可观察会话数 > 5）。WARMUP 提供这段前史。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from aspool.api_contract import DataPoolError
from aspool.limit_events import (
    compute_limit_events,
    initialize_limits,
    load_scope,
    summarize_published_limit_quality,
)
from aspool.pool import DataPool
from aspool.store import catalog, daily_path, initialize, record_coverage


def _sessions(start: date, count: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


# ST-dependent cases exercise the period before the July 2026 rule change.
_ALL = _sessions(date(2026, 5, 18), 20)
WARMUP = _ALL[:14]  # 14 个会话，足以排除 5 日无涨跌幅窗口
SESSIONS = _ALL[14:]  # 6 个会话
THU, FRI, MON, TUE, WED, THU2 = SESSIONS


def _bar(day, close, *, pre_close=None, high=None, low=None, open_=None, is_st=None):
    row = {
        "trade_date": day,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": 1000.0,
        "amount": 10000.0,
    }
    if pre_close is not None:
        row["pre_close"] = pre_close
    if is_st is not None:
        row["is_st"] = is_st
    return row


def _fill(rows):
    """缺失 pre_close 按上一行收盘补齐（首行用自身收盘）。"""
    ordered = sorted(rows, key=lambda r: r["trade_date"])
    filled = []
    previous = None
    for row in ordered:
        item = dict(row)
        if item.get("pre_close") is None:
            item["pre_close"] = previous if previous is not None else item["close"]
        previous = item["close"]
        filled.append(item)
    return filled


def _put(root, market, code, rows, name=None, asset_type=None, warmup=True):
    """写入日线。warmup=True 时前置一段平价前史以排除上市窗口。"""
    rows = list(rows)
    if warmup:
        first = sorted(rows, key=lambda r: r["trade_date"])[0]
        anchor = first.get("pre_close") or first["close"]
        rows = [_bar(day, anchor) for day in WARMUP] + rows
    filled = _fill(rows)
    path = daily_path(root, market, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(filled)
    frame["market"] = market
    frame["symbol"] = code
    if name is not None:
        frame["name"] = name
    if asset_type is not None:
        frame["asset_type"] = asset_type
    frame.to_parquet(path, index=False)
    record_coverage(
        root, code, market, filled[0]["trade_date"], filled[-1]["trade_date"], len(filled), "test"
    )


def seed_listing(root, code, market, name, listing_date):
    """在 universe 写入可靠上市日期（供 NO_LIMIT 路径使用）。"""
    with catalog(root) as conn:
        conn.execute(
            "create table if not exists universe (symbol varchar, market varchar, "
            "name varchar, asset_type varchar, listing_date date)"
        )
        conn.execute("delete from universe where symbol = ?", [code])
        conn.execute(
            "insert into universe values (?, ?, ?, 'stock', ?)", [code, market, name, listing_date]
        )


@pytest.fixture
def pool(tmp_path):
    initialize(tmp_path)
    initialize_limits(tmp_path)
    with catalog(tmp_path) as conn:
        conn.execute(
            "create table if not exists universe (symbol varchar, market varchar, "
            "name varchar, asset_type varchar, listing_date date)"
        )
    return tmp_path


class TestRuleResolution:
    """规则三态与生效区间。"""

    def test_star_before_effective_is_unknown(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(Market.SH, "688001", "科创", date(2019, 7, 1))
        assert not result.is_known and not result.is_no_limit
        assert "开板日" in result.reason

    def test_star_after_effective_is_known(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SH, "688001", "科创", date(2026, 9, 10), observed_sessions=99
        )
        assert result.is_known and result.rule.limit_pct == 0.20

    def test_gem_registration_after_is_known_20pct(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        after = resolve_limit_rule(
            Market.SZ, "300001", "创业", date(2020, 8, 24), None, observed_sessions=99
        )
        assert after.is_known and after.rule.limit_pct == 0.20

    def test_gem_before_registration_window_unconfirmed_is_unknown(self):
        """注册制前创业板的上市无涨跌幅窗口规则未确认 => UNKNOWN，不套用当前窗口。"""
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        before = resolve_limit_rule(
            Market.SZ, "300001", "创业", date(2020, 8, 21), False, observed_sessions=99
        )
        assert not before.is_known and not before.is_no_limit
        assert "窗口规则未确认" in before.reason

    def test_main_board_before_registration_window_is_unknown(self):
        """全面注册制主板前的上市窗口规则未确认 => UNKNOWN。"""
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SH, "600519", "贵州茅台", date(2010, 5, 4), False, observed_sessions=99
        )
        assert not result.is_known and "窗口规则未确认" in result.reason

    def test_main_board_registration_window_is_date_aware(self):
        from tdxman.codec.price_rules import resolve_limit_rule, resolve_no_limit_window
        from tdxman.models.enums import Market

        assert resolve_no_limit_window(Market.SH, "600519", date(2023, 4, 9)) is None
        window, source = resolve_no_limit_window(Market.SH, "600519", date(2023, 4, 10))
        assert window == 5 and "2023-04-10" in source

        first_day = resolve_limit_rule(
            Market.SH, "600519", "贵州茅台", date(2023, 4, 10), False, listed_days=1
        )
        assert first_day.is_no_limit

        after_window = resolve_limit_rule(
            Market.SH, "600519", "贵州茅台", date(2023, 4, 17), False, listed_days=6
        )
        assert after_window.is_known and after_window.rule.limit_pct == 0.10

    def test_no_limit_window_is_date_aware(self):
        from tdxman.codec.price_rules import resolve_no_limit_window
        from tdxman.models.enums import Market

        assert resolve_no_limit_window(Market.SH, "688001", date(2019, 7, 1)) is None
        window, source = resolve_no_limit_window(Market.SH, "688001", date(2019, 7, 22))
        assert window == 5 and "科创板" in source
        assert resolve_no_limit_window(Market.SZ, "300001", date(2019, 1, 2)) is None
        assert resolve_no_limit_window(Market.SH, "600519", date(2010, 1, 4)) is None
        assert resolve_no_limit_window(Market.SH, "600519", date(2020, 1, 2)) is None
        assert resolve_no_limit_window(Market.SH, "600519", date(2023, 4, 10))[0] == 5

    def test_main_board_without_st_basis_is_unknown(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SH, "600519", "贵州茅台", date(2026, 6, 30), None, observed_sessions=99
        )
        assert not result.is_known and "风险警示" in result.reason

    def test_main_board_with_st_basis(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        normal = resolve_limit_rule(
            Market.SH, "600519", "贵州茅台", date(2026, 6, 30), False, observed_sessions=99
        )
        flagged = resolve_limit_rule(
            Market.SH, "600519", "ST某某", date(2026, 6, 30), True, observed_sessions=99
        )
        assert normal.rule.limit_pct == 0.10
        assert flagged.rule.limit_pct == 0.05

    @pytest.mark.parametrize("market,code", [("SH", "600001"), ("SZ", "000001")])
    def test_legacy_main_board_excludes_only_first_session(self, market, code):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        day = date(2021, 9, 24)
        for st, pct in [(True, 0.05), (False, 0.10)]:
            first = resolve_limit_rule(Market[market], code, "", day, st, observed_sessions=1)
            assert not first.is_known and not first.is_no_limit
            later = resolve_limit_rule(Market[market], code, "", day, st, observed_sessions=2)
            assert later.is_known and later.rule.limit_pct == pct
        missing_st = resolve_limit_rule(Market[market], code, "", day, observed_sessions=99)
        assert not missing_st.is_known

    @pytest.mark.parametrize("st", [None, False, True])
    def test_main_board_after_july_2026_uses_ten_percent(self, st):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SH, "600001", "", date(2026, 7, 6), st, observed_sessions=99
        )
        assert result.is_known and result.rule.limit_pct == 0.10

    def test_missing_st_only_blocks_main_board_not_registered_gem(self, pool):
        """Rule quality follows production resolution, not joint field coverage."""
        _put(pool, "SH", "600001", [_bar(MON, 12.0, pre_close=10.0)], name="主板测试")
        _put(pool, "SZ", "300001", [_bar(MON, 12.0, pre_close=10.0)], name="创业测试")
        compute_limit_events(pool, [MON])

        summary = DataPool(pool).read_limit_summary().iloc[0]
        assert summary["known_count"] == 1
        assert summary["unknown_count"] == 1
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=MON)
        assert set(exceptions["kind"]) == {"UNKNOWN"}
        assert "主板涨跌幅取决于风险警示状态" in exceptions.iloc[0]["reason"]
        quality = summarize_published_limit_quality(pool, [MON])[0]
        boards = {entry["board"]: entry for entry in quality["boards"]}
        assert boards["主板"]["UNKNOWN"] == 1
        assert boards["主板"]["unknown_reasons"] == {
            "主板涨跌幅取决于风险警示状态，无历史 ST 依据": 1
        }
        assert boards["创业板"]["KNOWN"] == 1

    def test_listing_window_is_no_limit(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SZ, "300001", "创业", date(2026, 9, 10), None, listed_days=1
        )
        assert result.is_no_limit and not result.is_known
        assert "无涨跌幅窗口" in result.no_limit_basis

    def test_insufficient_listing_basis_is_unknown(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SZ, "300001", "创业", date(2026, 9, 10), None, observed_sessions=3
        )
        assert not result.is_known and not result.is_no_limit
        assert "无法排除上市无涨跌幅窗口" in result.reason

    def test_no_listing_basis_at_all_is_unknown(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(Market.SZ, "300001", "创业", date(2026, 9, 10), None)
        assert not result.is_known and "缺上市日期依据" in result.reason

    def test_index_like_not_in_scope(self):
        from tdxman.codec.price_rules import resolve_limit_rule
        from tdxman.models.enums import Market

        result = resolve_limit_rule(
            Market.SH, "000001", "上证指数", date(2026, 9, 10), None, observed_sessions=99
        )
        assert not result.is_known and "指数" in result.reason


class TestEventComputation:
    """事件判定与精确边界。"""

    def test_close_limit_up_exact_boundary(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 12.0, high=12.0, low=10.0, open_=10.5)],
            name="创业板测试",
        )
        compute_limit_events(pool, [MON])
        frame = DataPool(pool).read_limit_events(trade_date=MON)
        row = frame[frame["symbol"] == "300001.SZ"].iloc[0]
        assert row["close_limit_up"] == True  # noqa: E712
        assert row["limit_up_price"] == pytest.approx(12.00)
        assert row["limit_down_price"] == pytest.approx(8.00)

    def test_touched_but_not_sealed(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 11.5, high=12.0, low=10.0, open_=10.5)],
            name="创业板测试",
        )
        compute_limit_events(pool, [MON])
        frame = DataPool(pool).read_limit_events(trade_date=MON)
        row = frame[frame["symbol"] == "300001.SZ"].iloc[0]
        assert row["touched_limit_up"] == True  # noqa: E712
        assert row["close_limit_up"] == False  # noqa: E712
        summary = DataPool(pool).read_limit_summary()
        assert summary.iloc[0]["touched_unsealed_up_count"] == 1
        assert summary.iloc[0]["close_limit_up_count"] == 0

    def test_one_word_board_sealed(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 12.0, high=12.0, low=12.0, open_=12.0)],
            name="创业板测试",
        )
        compute_limit_events(pool, [MON])
        row = DataPool(pool).read_limit_events(trade_date=MON).iloc[0]
        assert row["close_limit_up"] == True  # noqa: E712
        assert row["touched_limit_up"] == True  # noqa: E712

    def test_both_limits_same_day(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 10.0, high=12.0, low=8.0, open_=10.0)],
            name="创业板测试",
        )
        compute_limit_events(pool, [MON])
        row = DataPool(pool).read_limit_events(trade_date=MON).iloc[0]
        assert row["touched_limit_up"] == True  # noqa: E712
        assert row["touched_limit_down"] == True  # noqa: E712

    def test_invalid_ohlc_is_isolated(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 10.0, high=9.0, low=11.0, open_=10.0)],
            name="创业板测试",
        )
        compute_limit_events(pool, [MON])
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=MON)
        assert "INVALID" in set(exceptions["kind"])
        assert DataPool(pool).read_limit_summary().iloc[0]["invalid_count"] == 1

    def test_boolean_price_is_invalid(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 10.0)], name="创业板测试")
        path = daily_path(pool, "SZ", "300001")
        frame = pd.read_parquet(path)
        mask = frame["trade_date"] == MON
        frame["close"] = frame["close"].astype(object)
        frame.loc[mask, "close"] = True
        frame.to_parquet(path, index=False)
        compute_limit_events(pool, [MON])
        assert "INVALID" in set(DataPool(pool).read_limit_exceptions(trade_date=MON)["kind"])

    def test_zero_price_is_invalid(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 10.0)], name="创业板测试")
        path = daily_path(pool, "SZ", "300001")
        frame = pd.read_parquet(path)
        frame.loc[frame["trade_date"] == MON, "close"] = 0.0
        frame.to_parquet(path, index=False)
        compute_limit_events(pool, [MON])
        assert "INVALID" in set(DataPool(pool).read_limit_exceptions(trade_date=MON)["kind"])

    def test_missing_pre_close_is_unknown_not_substituted(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 12.0)], name="创业板测试")
        path = daily_path(pool, "SZ", "300001")
        frame = pd.read_parquet(path)
        frame.loc[frame["trade_date"] == MON, "pre_close"] = None
        frame.to_parquet(path, index=False)
        compute_limit_events(pool, [MON])
        assert DataPool(pool).read_limit_events(trade_date=MON).empty
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=MON)
        assert "参考前收盘价" in exceptions.iloc[0]["reason"]

    def test_non_boolean_st_is_not_coerced(self, pool):
        _put(pool, "SH", "600519", [_bar(FRI, 100.0), _bar(MON, 110.0)], name="贵州茅台")
        path = daily_path(pool, "SH", "600519")
        frame = pd.read_parquet(path)
        frame["is_st"] = "False"
        frame.to_parquet(path, index=False)
        compute_limit_events(pool, [MON])
        row = DataPool(pool).read_limit_exceptions(trade_date=MON).iloc[0]
        assert row["kind"] == "UNKNOWN"
        assert "风险警示" in row["reason"]

    def test_main_board_with_st_column_is_known(self, pool):
        _put(
            pool, "SH", "600519",
            [_bar(FRI, 100.0, is_st=False), _bar(MON, 110.0, is_st=False)],
            name="贵州茅台",
        )
        compute_limit_events(pool, [MON])
        row = DataPool(pool).read_limit_events(trade_date=MON).iloc[0]
        assert row["close_limit_up"] == True  # noqa: E712

    def test_no_limit_recorded_per_symbol(self, pool):
        """有可靠上市日期且处于窗口内：逐股记 NO_LIMIT，而不是只记总数。"""
        path = daily_path(pool, "SZ", "300001")
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {**_bar(MON, 10.0), "market": "SZ", "symbol": "300001", "name": "创业板新股"},
                {
                    **_bar(TUE, 12.0, pre_close=10.0),
                    "market": "SZ",
                    "symbol": "300001",
                    "name": "创业板新股",
                },
            ]
        ).to_parquet(path, index=False)
        record_coverage(pool, "300001", "SZ", MON, TUE, 2, "test")
        # 可靠上市依据：日线第一根即上市日。
        seed_listing(pool, "300001", "SZ", "创业板新股", MON)
        compute_limit_events(pool, [MON])
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=MON)
        assert "NO_LIMIT" in set(exceptions["kind"])
        assert DataPool(pool).read_limit_summary().iloc[0]["no_limit_count"] == 1


class TestConsecutive:
    """连板：首板/续板/断板/周末/缺会话/截断。"""

    def test_first_board_after_known_non_limit(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 12.0)], name="创业板测试")
        compute_limit_events(pool, [FRI, MON])
        row = DataPool(pool).read_limit_events(trade_date=MON).iloc[0]
        assert row["consecutive_up"] == 1

    def test_consecutive_across_weekend(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 14.4)],
            name="创业板测试",
        )
        compute_limit_events(pool, [THU, FRI, MON])
        row = DataPool(pool).read_limit_events(trade_date=MON).iloc[0]
        assert row["consecutive_up"] == 2

    def test_break_is_zero_and_not_in_events(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 11.0)],
            name="创业板测试",
        )
        compute_limit_events(pool, [THU, FRI, MON])
        assert DataPool(pool).read_limit_events(trade_date=MON).empty
        summary = DataPool(pool).read_limit_summary()
        row = summary[summary["trade_date"] == pd.Timestamp(MON)].iloc[0]
        assert row["close_limit_up_count"] == 0

    def test_truncated_history_is_unknown(self, pool):
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(MON, 12.0, pre_close=10.0)],
            name="创业板测试",
            warmup=False,
        )
        compute_limit_events(pool, [MON])
        assert DataPool(pool).read_limit_events(trade_date=MON).empty
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=MON)
        assert "UNKNOWN" in set(exceptions["kind"])

    def test_missing_session_does_not_bridge(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 12.0), _bar(WED, 14.4)],
            name="创业板测试",
        )
        _put(pool, "SZ", "300002", [_bar(d, 10.0) for d in SESSIONS], name="参考股票")
        compute_limit_events(pool, [FRI, MON, TUE, WED])
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=TUE)
        assert "300001.SZ" in set(exceptions["symbol"])

    def test_missing_market_session_does_not_confirm_listing_age(self, pool):
        """市场会话整体缺失时，观测到的第5根不能冒充上市第5日。"""
        rows = [
            _bar(THU, 10.0),
            _bar(FRI, 10.0),
            _bar(MON, 10.0),
            _bar(WED, 10.0),
            _bar(THU2, 10.0),
        ]
        _put(pool, "SZ", "300001", rows, name="创业板测试", warmup=False)
        seed_listing(pool, "300001", "SZ", "创业板测试", THU)

        compute_limit_events(pool, [THU2])
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=THU2)
        row = exceptions[exceptions["symbol"] == "300001.SZ"].iloc[0]
        assert row["kind"] == "UNKNOWN"
        assert "无法排除上市无涨跌幅窗口" in row["reason"]

    def test_missing_stock_row_is_only_a_lower_bound(self, pool):
        """市场轴完整但个股缺行时，也不能把个股观测行数当精确日龄。"""
        target_rows = [
            _bar(THU, 10.0),
            _bar(FRI, 10.0),
            _bar(MON, 10.0),
            _bar(WED, 10.0),
            _bar(THU2, 10.0),
        ]
        _put(pool, "SZ", "300001", target_rows, name="创业板测试", warmup=False)
        _put(
            pool,
            "SZ",
            "300002",
            [_bar(day, 10.0) for day in SESSIONS],
            name="参考股票",
            warmup=False,
        )
        seed_listing(pool, "300001", "SZ", "创业板测试", THU)

        compute_limit_events(pool, [THU2])
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=THU2)
        row = exceptions[exceptions["symbol"] == "300001.SZ"].iloc[0]
        assert row["kind"] == "UNKNOWN"
        assert "无法排除上市无涨跌幅窗口" in row["reason"]


class TestPublishAndCorrection:
    """幂等、撤销、传播、重试、发布一致性。"""

    def _three(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 14.4)],
            name="创业板测试",
        )

    def test_rerun_is_idempotent(self, pool):
        self._three(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        first = DataPool(pool).read_limit_events()
        compute_limit_events(pool, [THU, FRI, MON])
        second = DataPool(pool).read_limit_events()
        assert len(first) == len(second)
        assert list(first["consecutive_up"]) == list(second["consecutive_up"])

    def test_correction_propagates_automatically(self, pool):
        """PM 反例：只传修正日，后续连板必须自动更新。"""
        self._three(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        assert DataPool(pool).read_limit_events(trade_date=MON).iloc[0]["consecutive_up"] == 2

        _put(
            pool, "SZ", "300001",
            [_bar(THU, 10.0), _bar(FRI, 11.0), _bar(MON, 13.2)],
            name="创业板测试",
        )
        compute_limit_events(pool, [FRI])
        after = DataPool(pool).read_limit_events(trade_date=MON).iloc[0]["consecutive_up"]
        assert after == 1

    def test_correction_revokes_old_event(self, pool):
        self._three(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        assert len(DataPool(pool).read_limit_events(trade_date=FRI)) == 1

        _put(
            pool, "SZ", "300001",
            [_bar(THU, 10.0), _bar(FRI, 11.0, high=12.0, low=10.0), _bar(MON, 11.5)],
            name="创业板测试",
        )
        compute_limit_events(pool, [FRI])
        revised = DataPool(pool).read_limit_events(trade_date=FRI).iloc[0]
        assert revised["close_limit_up"] == False  # noqa: E712
        assert revised["touched_limit_up"] == True  # noqa: E712

    def test_publish_pointer_selects_single_version(self, pool):
        self._three(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        events = DataPool(pool).read_limit_events()
        assert not events.duplicated(subset=["trade_date", "symbol"]).any()

    def test_failure_marks_stale_and_visible(self, pool, monkeypatch):
        """先发布旧结果 -> 修正原始 -> 派生失败 -> 读取能看到 stale。"""
        self._three(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        assert not bool(DataPool(pool).read_limit_summary().iloc[0]["stale"])

        _put(
            pool, "SZ", "300001",
            [_bar(THU, 10.0), _bar(FRI, 11.0), _bar(MON, 12.1)],
            name="创业板测试",
        )

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("派生失败")

        with monkeypatch.context() as failing:
            failing.setattr(module, "_publish_batch", boom)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [FRI])

        assert not DataPool(pool).read_limit_staleness().empty
        summary = DataPool(pool).read_limit_summary()
        assert bool(summary[summary["trade_date"] == pd.Timestamp(FRI)].iloc[0]["stale"])

    def test_retry_clears_stale(self, pool, monkeypatch):
        self._three(pool)
        compute_limit_events(pool, [THU, FRI, MON])

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("派生失败")

        with monkeypatch.context() as failing:
            failing.setattr(module, "_publish_batch", boom)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [FRI])
        assert not DataPool(pool).read_limit_staleness().empty

        compute_limit_events(pool, [FRI])
        assert DataPool(pool).read_limit_staleness().empty

    def test_zero_denominator_sealed_ratio_is_null(self, pool):
        _put(
            pool, "SH", "600519",
            [_bar(FRI, 100.0, is_st=False), _bar(MON, 100.0, is_st=False)],
            name="贵州茅台",
        )
        compute_limit_events(pool, [MON])
        assert pd.isna(DataPool(pool).read_limit_summary().iloc[0]["sealed_ratio"])

    def test_sealed_ratio_computed(self, pool):
        _put(
            pool, "SZ", "300001",
            [_bar(FRI, 10.0), _bar(MON, 12.0, high=12.0, low=10.0)],
            name="创业板测试",
        )
        _put(
            pool, "SZ", "300002",
            [_bar(FRI, 10.0), _bar(MON, 11.5, high=12.0, low=10.0)],
            name="创业板测试2",
        )
        compute_limit_events(pool, [MON])
        summary = DataPool(pool).read_limit_summary()
        assert summary.iloc[0]["close_limit_up_count"] == 1
        assert summary.iloc[0]["touched_unsealed_up_count"] == 1
        assert summary.iloc[0]["sealed_ratio"] == pytest.approx(0.5)


class TestPublicReadContract:
    """公开读取：只读、规范 symbol、范围、参数与错误。"""

    def _one(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 12.0)], name="创业板测试")

    def test_reads_do_not_create_database(self, tmp_path):
        target = tmp_path / "absent"
        with pytest.raises(DataPoolError) as excinfo:
            DataPool(target).read_limit_summary()
        assert excinfo.value.code == "LIMIT_NOT_READY"
        assert not target.exists()

    def test_reads_do_not_modify_existing_pool(self, pool):
        self._one(pool)
        compute_limit_events(pool, [MON])
        before = {p: p.read_bytes() for p in pool.rglob("*") if p.is_file()}
        pool_api = DataPool(pool)
        pool_api.read_limit_summary()
        pool_api.read_limit_events()
        pool_api.read_limit_coverage()
        pool_api.read_limit_scope()
        pool_api.read_limit_exceptions()
        pool_api.describe_limits()
        after = {p: p.read_bytes() for p in pool.rglob("*") if p.is_file()}
        assert before == after

    def test_describe_without_tables_is_not_ready(self, tmp_path):
        described = DataPool(tmp_path).describe_limits()
        assert described["ready"] is False
        assert described["capabilities"]["daily_limit_events"] is False
        assert not (tmp_path / "catalog.duckdb").exists()

    def test_output_symbol_is_canonical(self, pool):
        self._one(pool)
        compute_limit_events(pool, [MON])
        assert list(DataPool(pool).read_limit_events()["symbol"]) == ["300001.SZ"]

    def test_legacy_symbol_input_accepted(self, pool):
        self._one(pool)
        compute_limit_events(pool, [MON])
        pool_api = DataPool(pool)
        assert len(pool_api.read_limit_events(symbols="300001.SZ")) == 1
        assert len(pool_api.read_limit_events(symbols="SZ.300001")) == 1

    def test_invalid_symbol_rejected(self, pool):
        with pytest.raises(DataPoolError):
            DataPool(pool).read_limit_events(symbols="300001")

    def test_invalid_bounds_rejected(self, pool):
        with pytest.raises(DataPoolError):
            DataPool(pool).read_limit_summary(start=MON, end=FRI)

    def test_unpublished_date_invisible(self, pool):
        self._one(pool)
        compute_limit_events(pool, [MON])
        assert DataPool(pool).read_limit_events(trade_date=TUE).empty

    def test_scope_exposes_processed_symbols(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 12.0)], name="创业板测试")
        _put(pool, "SZ", "300002", [_bar(FRI, 10.0), _bar(MON, 10.1)], name="创业板测试2")
        compute_limit_events(pool, [MON])
        scope = DataPool(pool).read_limit_scope(trade_date=MON)
        assert set(scope["symbol"]) == {"300001.SZ", "300002.SZ"}
        events = DataPool(pool).read_limit_events(trade_date=MON)
        assert set(events["symbol"]) == {"300001.SZ"}

    def test_all_unknown_not_in_event_table(self, pool):
        """四标志全 null：只进异常与 scope，不进事件表。"""
        _put(pool, "SH", "600519", [_bar(FRI, 100.0), _bar(MON, 110.0)], name="贵州茅台")
        compute_limit_events(pool, [MON])
        assert DataPool(pool).read_limit_events(trade_date=MON).empty
        assert set(DataPool(pool).read_limit_exceptions(trade_date=MON)["kind"]) == {"UNKNOWN"}
        assert set(DataPool(pool).read_limit_scope(trade_date=MON)["symbol"]) == {"600519.SH"}

    def test_scope_rejects_unsupported(self, pool):
        with pytest.raises(DataPoolError) as excinfo:
            compute_limit_events(pool, [MON], asset_type="etf")
        assert excinfo.value.code == "SCOPE_UNSUPPORTED"


class TestScopeFiltering:
    """范围：ETF 排除、指数排除。"""

    def test_etf_excluded_by_own_asset_type(self, pool):
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 12.0)], name="创业板测试")
        _put(pool, "SH", "510050", [_bar(FRI, 3.0), _bar(MON, 3.1)], name="50ETF", asset_type="etf")
        entries = {e.symbol for e in load_scope(pool)}
        assert "510050.SH" not in entries
        assert "300001.SZ" in entries

    def test_etf_excluded_even_without_universe(self, pool):
        _put(pool, "SH", "510050", [_bar(FRI, 3.0), _bar(MON, 3.1)], name="50ETF", asset_type="etf")
        assert load_scope(pool) == []

    def test_index_is_out_of_scope_not_unknown(self, pool):
        _put(pool, "SH", "000001", [_bar(FRI, 3000.0), _bar(MON, 3100.0)], name="上证指数")
        _put(pool, "SZ", "300001", [_bar(FRI, 10.0), _bar(MON, 12.0)], name="创业板测试")
        compute_limit_events(pool, [MON])
        exceptions = DataPool(pool).read_limit_exceptions(trade_date=MON)
        assert "000001.SH" not in set(exceptions["symbol"])
        scope = DataPool(pool).read_limit_scope(trade_date=MON)
        assert "000001.SH" not in set(scope["symbol"])


class TestIncrementalEqualsFullRecompute:
    """增量重算必须与完整顺序重算结果一致（PM v2 反例组）。"""

    def _seed(self, pool):
        """四会话：非涨停 → 涨停 → 涨停 → 非涨停。"""
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 14.4), _bar(TUE, 14.0)],
            name="创业板测试",
        )

    def _snapshot(self, pool):
        events = DataPool(pool).read_limit_events()
        events = events.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
        summary = DataPool(pool).read_limit_summary().sort_values("trade_date")
        summary = summary.reset_index(drop=True)
        return events, summary

    def test_single_modified_date(self, pool, tmp_path):
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON, TUE])
        expected_events, expected_summary = self._snapshot(pool)

        # 修正第二日（FRI）为非涨停后，只传 FRI。
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(THU, 10.0), _bar(FRI, 11.0), _bar(MON, 13.2), _bar(TUE, 12.5)],
            name="创业板测试",
        )
        compute_limit_events(pool, [FRI])
        got_events, got_summary = self._snapshot(pool)

        reference = DataPool(tmp_path / "ref")
        assert reference is not None  # 仅用于保持导入
        # 与完整顺序重算比对：另行构造一个池，全量重算同一天。
        clone = _clone_pool(pool, tmp_path / "full")
        compute_limit_events(clone, [THU, FRI, MON, TUE])
        want_events, want_summary = self._snapshot(clone)

        assert list(got_events["consecutive_up"]) == list(want_events["consecutive_up"])
        assert (
            got_summary["close_limit_up_count"].tolist()
            == want_summary["close_limit_up_count"].tolist()
        )
        assert expected_events is not None

    def test_multiple_non_adjacent_modified_dates(self, pool, tmp_path):
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON, TUE])

        # 同时修正 THU 与 TUE（不相邻，中间 MON 必须被重算）。
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 14.4), _bar(TUE, 13.0)],
            name="创业板测试",
        )
        compute_limit_events(pool, [THU, TUE])
        got_events, got_summary = self._snapshot(pool)

        clone = _clone_pool(pool, tmp_path / "full2")
        compute_limit_events(clone, [THU, FRI, MON, TUE])
        want_events, want_summary = self._snapshot(clone)

        assert list(got_events["consecutive_up"]) == list(want_events["consecutive_up"])
        assert (
            got_summary["close_limit_up_count"].tolist()
            == want_summary["close_limit_up_count"].tolist()
        )

    def test_gap_between_requested_dates_is_recomputed(self, pool, tmp_path):
        """两个请求日之间的依赖会话不得跳过。"""
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON, TUE])
        # 修正 FRI 与 TUE，中间的 MON 依赖 FRI 的连板状态。
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(THU, 10.0), _bar(FRI, 11.0), _bar(MON, 13.2), _bar(TUE, 15.84)],
            name="创业板测试",
        )
        compute_limit_events(pool, [FRI, TUE])
        got = DataPool(pool).read_limit_events().sort_values("trade_date")
        consecutive = dict(zip(got["trade_date"], got["consecutive_up"]))
        assert consecutive[pd.Timestamp(MON)] == 1
        assert consecutive[pd.Timestamp(TUE)] == 2

        clone = _clone_pool(pool, tmp_path / "full3")
        compute_limit_events(clone, [THU, FRI, MON, TUE])
        want = DataPool(clone).read_limit_events().sort_values("trade_date")
        assert list(got["consecutive_up"]) == list(want["consecutive_up"])

    def test_partial_correction_recomputed_like_full(self, pool, tmp_path):
        """仅重算一个尾日，仍与全量一致。"""
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON, TUE])
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 14.4), _bar(TUE, 17.28)],
            name="创业板测试",
        )
        compute_limit_events(pool, [TUE])
        got = DataPool(pool).read_limit_events().sort_values("trade_date")

        clone = _clone_pool(pool, tmp_path / "full4")
        compute_limit_events(clone, [THU, FRI, MON, TUE])
        want = DataPool(clone).read_limit_events().sort_values("trade_date")
        assert list(got["consecutive_up"]) == list(want["consecutive_up"])


def test_derivation_does_not_retain_all_symbol_histories(pool, monkeypatch):
    import weakref

    from aspool import limit_events as module

    for code in ("300001", "300002", "300003", "300004"):
        _put(pool, "SZ", code, [_bar(THU, 10.0), _bar(FRI, 12.0)])
    original = module._read_symbol_bars
    references = []

    class History(list):
        pass

    def tracked(*args):
        # The previous security can still be referenced during assignment,
        # but completed securities must not accumulate in a global dictionary.
        assert sum(ref() is not None for ref in references) <= 1
        rows = History(original(*args))
        references.append(weakref.ref(rows))
        return rows

    monkeypatch.setattr(module, "_read_symbol_bars", tracked)
    compute_limit_events(pool, [THU, FRI])
    assert all(ref() is None for ref in references)


class TestStaleCoverage:
    """陈旧标记必须覆盖早期失败，且 stale 前日不得作为可靠前史。"""

    def _seed(self, pool):
        _put(
            pool,
            "SZ",
            "300001",
            [_bar(THU, 10.0), _bar(FRI, 12.0), _bar(MON, 14.4)],
            name="创业板测试",
        )

    def test_scope_load_failure_still_marks_stale(self, pool, monkeypatch):
        """已发布旧结果 → 源更新 → 范围/源读取阶段失败 → 读取仍能识别陈旧。"""
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        assert DataPool(pool).read_limit_staleness().empty

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("范围加载失败")

        with monkeypatch.context() as failing:
            failing.setattr(module, "load_scope", boom)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [MON])

        stale = DataPool(pool).read_limit_staleness()
        assert not stale.empty
        assert pd.Timestamp(MON) in set(pd.to_datetime(stale["trade_date"]))

    def test_source_read_failure_still_marks_stale(self, pool, monkeypatch):
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON])

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("源读取失败")

        with monkeypatch.context() as failing:
            failing.setattr(module, "_read_symbol_bars", boom)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [MON])

        assert not DataPool(pool).read_limit_staleness().empty

    @pytest.mark.parametrize("failure_target", ["scope", "source"])
    def test_early_failure_marks_published_dependency_tail(self, pool, monkeypatch, failure_target):
        """早期失败也要标记请求日之后已发布的依赖日期。"""
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON])

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("early failure")

        target = "load_scope" if failure_target == "scope" else "_read_symbol_bars"
        with monkeypatch.context() as failing:
            failing.setattr(module, target, boom)
            with pytest.raises(RuntimeError):
                # FRI has a published dependent successor MON; do not request MON.
                compute_limit_events(pool, [FRI])

        for reader in (
            DataPool(pool).read_limit_events,
            DataPool(pool).read_limit_summary,
            DataPool(pool).read_limit_coverage,
        ):
            frame = reader()
            assert set(pd.to_datetime(frame.loc[frame["stale"], "trade_date"])) == {
                pd.Timestamp(FRI),
                pd.Timestamp(MON),
            }

        compute_limit_events(pool, [FRI])
        assert DataPool(pool).read_limit_staleness().empty

    def test_events_read_exposes_stale(self, pool, monkeypatch):
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON])
        assert not bool(DataPool(pool).read_limit_events(trade_date=FRI).iloc[0]["stale"])

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("派生失败")

        with monkeypatch.context() as failing:
            failing.setattr(module, "_publish_batch", boom)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [FRI])

        frame = DataPool(pool).read_limit_events(trade_date=FRI)
        assert bool(frame.iloc[0]["stale"])
        assert frame.iloc[0]["stale_reason"]

    def test_stale_prior_not_used_as_basis(self, pool, monkeypatch):
        """前日被标 stale 后，不得用其连板状态推出标记为新鲜的次日结果。"""
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON])

        from aspool import limit_events as module

        # 让 FRI 的发布失败，使其 stale；随后单独重算 MON。
        real_publish = module._publish_batch

        def fail_on_friday(root, batch_id, result, scope_id):
            if result.trade_date == FRI:
                raise RuntimeError("周五发布失败")
            return real_publish(root, batch_id, result, scope_id)

        with monkeypatch.context() as failing:
            failing.setattr(module, "_publish_batch", fail_on_friday)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [FRI])

        assert pd.Timestamp(FRI) in set(
            pd.to_datetime(DataPool(pool).read_limit_staleness()["trade_date"])
        )

        # 单独重算 MON：stale 的 FRI 不能作为前史 => 连板应为未知，而非凭空续板。
        compute_limit_events(pool, [MON])
        frame = DataPool(pool).read_limit_events(trade_date=MON)
        if not frame.empty:
            assert pd.isna(frame.iloc[0]["consecutive_up"])

    def test_retry_clears_all_stale(self, pool, monkeypatch):
        self._seed(pool)
        compute_limit_events(pool, [THU, FRI, MON])

        from aspool import limit_events as module

        def boom(*_a, **_k):
            raise RuntimeError("派生失败")

        with monkeypatch.context() as failing:
            failing.setattr(module, "_publish_batch", boom)
            with pytest.raises(RuntimeError):
                compute_limit_events(pool, [FRI])

        assert not DataPool(pool).read_limit_staleness().empty
        compute_limit_events(pool, [FRI])
        assert DataPool(pool).read_limit_staleness().empty


def test_reference_correction_is_published_and_audited_without_rewriting_bars(pool):
    rows = [_bar(FRI, 10.0, pre_close=8.0), _bar(MON, 12.0, pre_close=8.0)]
    rows[0]["pct_chg"], rows[1]["pct_chg"] = 0.0, 20.0
    _put(pool, "SZ", "300001", rows, name="参考价测试")
    compute_limit_events(pool, [FRI, MON])
    api = DataPool(pool)
    event = api.read_limit_events(trade_date=MON).iloc[0]
    assert event.close_limit_up and event.consecutive_up == 1
    audit = api.read_limit_references(start=MON, end=MON, symbols="SZ.300001").iloc[0]
    assert audit.stored_pre_close == 8.0 and audit.reference_pre_close == 10.0
    assert audit.basis == "previous_session_close_confirmed_by_return"
    assert not audit.stale
    raw = pd.read_parquet(daily_path(pool, "SZ", "300001"))
    assert raw.loc[raw.trade_date == MON, "pre_close"].iloc[0] == 8.0
    public = api.read_research_daily(symbols="300001.SZ", start=MON, end=MON)
    assert public.pre_close.iloc[0] == 10.0
    assert public.pre_close_source.iloc[0].startswith("limit_derived:")
    from aspool.limit_events import _mark_stale

    _mark_stale(pool, [MON], "source changed")
    stale = api.read_research_daily(symbols="300001.SZ", start=MON, end=MON)
    assert pd.isna(stale.pre_close.iloc[0])


def test_multi_year_cache_keeps_consecutive_state_identical(pool, tmp_path):
    warmup = [_bar(d, 10.0) for d in _sessions(date(2025, 12, 1), 15)]
    days = [date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 5)]
    rows = warmup + [_bar(d, c) for d, c in zip(days, [10.0, 12.0, 14.4, 17.28])]
    _put(pool, "SZ", "300001", rows, name="跨年测试", warmup=False)
    clone = _clone_pool(pool, tmp_path / "copy")
    compute_limit_events(pool, [days[0], days[-1]], cache_years=1)
    compute_limit_events(clone, [days[0], days[-1]], cache_years=6)
    a, b = DataPool(pool).read_limit_events(), DataPool(clone).read_limit_events()
    pd.testing.assert_frame_equal(a, b)
    assert b[b.trade_date == pd.Timestamp(days[-1])].consecutive_up.iloc[0] == 3


def _clone_pool(source, target):
    """复制一个池用于对照的全量重算。"""
    import shutil

    from aspool.limit_events import initialize_limits as _init

    shutil.copytree(source, target)
    _init(target)
    return target


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """本模块禁止任何网络连接（显式屏蔽，便于声明"未联网"）。"""
    import socket

    def _blocked(*_a, **_k):
        raise RuntimeError("network access is blocked in limit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
