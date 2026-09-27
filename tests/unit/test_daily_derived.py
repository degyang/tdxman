"""日线衍生字段计算逻辑测试。

测试涨跌停价格计算、状态判定和连板统计。
注意：本文件中的 compute_limit_status/compute_consecutive_limits 是测试内示范函数，
非生产接口。生产实现必须调用公开API。
"""

import json
import unittest
from datetime import date
from pathlib import Path

from tdxman.codec.price_rules import compute_price_limits as tdxman_compute_price_limits
from tdxman.models.enums import Market


def get_board_type(code: str) -> str:
    """根据股票代码推断板块类型。"""
    if code.startswith(("4", "8", "920")):
        return "BJ"
    if code.startswith("688"):
        return "STAR"
    if code.startswith("30"):
        return "GEM"
    return "MAIN"


def get_market(code: str) -> Market:
    """根据股票代码推断市场。"""
    if code.startswith(("4", "8", "920")):
        return Market.BJ
    if code.startswith(("5", "6", "9")):
        return Market.SH
    return Market.SZ


def compute_price_limits_wrapper(
    pre_close: float | None,
    board_type: str,
    is_st: bool,
    name: str = "",
    listed_days: int | None = None,
    trade_date: date | None = None,
) -> dict:
    """计算涨跌停价格和状态（包装 tdxman 的实现）。"""
    # 根据 board_type 构造 market 和 code
    if board_type == "BJ":
        market = Market.BJ
        code = "830000"
    elif board_type == "STAR":
        market = Market.SH
        code = "688000"
    elif board_type == "GEM":
        market = Market.SZ
        code = "300000"
    else:  # MAIN
        market = Market.SH
        code = "600000"

    # ST 股票名称需要包含 ST
    if is_st and "ST" not in name.upper():
        name = f"ST{name}"

    # 使用 tdxman 的计算函数（它会处理上市窗口期）
    if pre_close is None or pre_close <= 0:
        # 无前收盘价，检查是否在无涨跌幅窗口期
        if listed_days is not None:
            window = tdxman_compute_price_limits(
                market, code, name, 1.0, listed_days=listed_days, trade_date=trade_date
            )
            if window == (None, None):
                return {
                    "price_limit_status": "NO_LIMIT",
                    "limit_up": None,
                    "limit_down": None,
                    "limit_pct": None,
                }
        return {
            "price_limit_status": "UNKNOWN",
            "limit_up": None,
            "limit_down": None,
            "limit_pct": None,
        }

    limit_up, limit_down = tdxman_compute_price_limits(
        market, code, name, pre_close, listed_days=listed_days, trade_date=trade_date
    )

    if limit_up is None or limit_down is None:
        return {
            "price_limit_status": "NO_LIMIT",
            "limit_up": None,
            "limit_down": None,
            "limit_pct": None,
        }

    # 计算 limit_pct (ratio, 0.10 = 10%)
    limit_pct = (limit_up / pre_close) - 1.0

    return {
        "price_limit_status": "KNOWN",
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_pct": round(limit_pct, 4),  # ratio
    }


def compute_limit_status(
    close: float | None,
    high: float | None,
    low: float | None,
    open_: float | None,
    limit_up: float | None,
    limit_down: float | None,
    price_limit_status: str,
) -> dict:
    """计算涨跌停状态（测试示范函数，非生产接口）。"""
    if price_limit_status != "KNOWN":
        return {
            "is_limit_up": None,
            "is_limit_down": None,
            "touched_limit_up": None,
            "touched_limit_down": None,
            "is_sealed_limit_up": None,
            "is_sealed_limit_down": None,
            "is_one_word_limit_up": None,
            "is_one_word_limit_down": None,
        }

    if close is None or high is None or low is None or open_ is None:
        return {
            "is_limit_up": None,
            "is_limit_down": None,
            "touched_limit_up": None,
            "touched_limit_down": None,
            "is_sealed_limit_up": None,
            "is_sealed_limit_down": None,
            "is_one_word_limit_up": None,
            "is_one_word_limit_down": None,
        }

    is_limit_up = close >= limit_up
    is_limit_down = close <= limit_down
    touched_limit_up = high >= limit_up
    touched_limit_down = low <= limit_down

    # 一字板判定（开高低收都等于涨跌停价）
    is_one_word_up = open_ == high == low == close == limit_up
    is_one_word_down = open_ == high == low == close == limit_down

    # 封板：收盘在限价（含一字板）
    return {
        "is_limit_up": is_limit_up,
        "is_limit_down": is_limit_down,
        "touched_limit_up": touched_limit_up,
        "touched_limit_down": touched_limit_down,
        "is_sealed_limit_up": is_limit_up,  # 修正：一字板也计入封板
        "is_sealed_limit_down": is_limit_down,  # 修正：一字板也计入封板
        "is_one_word_limit_up": is_one_word_up,
        "is_one_word_limit_down": is_one_word_down,
    }


def align_to_session(
    daily_bars: list[dict], session_dates: list[str]
) -> list[dict]:
    """将日线数据对齐到市场会话日期序列。

    缺行的交易日填充 is_limit_up=None（未知状态）。
    输入 daily_bars 的 trade_date 必须是 session_dates 的子集。

    Args:
        daily_bars: 日线数据列表，每项包含 trade_date 和 is_limit_up
        session_dates: 市场会话日期序列（连续交易日）

    Returns:
        对齐后的日线数据列表，长度等于 session_dates
    """
    bar_map = {row["trade_date"]: row for row in daily_bars}
    aligned = []
    for date_str in session_dates:
        if date_str in bar_map:
            aligned.append(bar_map[date_str])
        else:
            # 缺行：填充未知状态
            aligned.append({"trade_date": date_str, "is_limit_up": None})
    return aligned


def compute_consecutive_limits(
    daily_bars: list[dict],
    current_index: int,
    session_dates: list[str] | None = None,
) -> dict:
    """计算连板天数和断档状态（测试示范函数，非生产接口）。

    会话轴：如果提供 session_dates，先将 daily_bars 对齐到会话日期序列，
    缺行的交易日填充 is_limit_up=None。
    如果不提供 session_dates，直接使用 daily_bars（需确保输入是连续会话）。

    三值语义：
    - True: 确定是
    - False: 确定否
    - None: 不确定（前史未知、数据缺失、或初始截断）

    规则：
    1. 当前状态未知 → consecutive=null, board_break=null
    2. 当前涨停：
       - 首条记录（无前史）→ consecutive=null（不能确认首板）
       - 前日未知 → consecutive=null
       - 前日涨停 → 向前回溯
       - 前日非涨停 → consecutive=1
       - 回溯到输入开头仍未遇到已知非涨停 → consecutive=null（截断边界）
    3. 当前非涨停：
       - 首条记录 → board_break=null（无前史依据）
       - 前日未知 → board_break=null
       - 前日涨停 → board_break=true
       - 前日非涨停 → board_break=false
    """
    # 会话对齐：补齐缺行
    if session_dates is not None:
        daily_bars = align_to_session(daily_bars, session_dates)
        # current_index 需要重新映射到对齐后的索引
        # 假设调用者已正确计算 current_index

    current = daily_bars[current_index]

    # 当前状态未知，无法判断
    if current.get("is_limit_up") is None:
        return {
            "consecutive_limit_up": None,
            "board_break": None,
        }

    if not current["is_limit_up"]:
        # 当前非涨停
        # 首条记录（无前史）→ board_break=null（无依据断言）
        if current_index == 0:
            return {"consecutive_limit_up": 0, "board_break": None}
        prev = daily_bars[current_index - 1]
        if prev.get("is_limit_up") is None:
            # 前日未知，无法判断断档
            return {"consecutive_limit_up": 0, "board_break": None}
        elif prev["is_limit_up"]:
            return {"consecutive_limit_up": 0, "board_break": True}
        return {"consecutive_limit_up": 0, "board_break": False}

    # 当前涨停，向前回溯
    # 首条记录（无前史）→ 不能确认首板
    if current_index == 0:
        return {"consecutive_limit_up": None, "board_break": False}

    count = 1
    hit_known_non_limit = False
    for i in range(current_index - 1, -1, -1):
        prev = daily_bars[i]
        if prev.get("is_limit_up") is None:
            # 前日未知，停止回溯，连板数不确定
            return {"consecutive_limit_up": None, "board_break": False}
        elif prev["is_limit_up"]:
            count += 1
        else:
            hit_known_non_limit = True
            break

    # 回溯到输入开头仍未遇到已知非涨停 → 截断边界，连板数不确定
    if not hit_known_non_limit:
        return {"consecutive_limit_up": None, "board_break": False}

    return {"consecutive_limit_up": count, "board_break": False}


class TestComputePriceLimits(unittest.TestCase):
    """测试涨跌停价格计算。"""

    def test_main_board_normal(self):
        """主板普通股票 ±10%。"""
        result = compute_price_limits_wrapper(10.05, "MAIN", False, "浦发银行")
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.10, places=2)
        self.assertEqual(result["limit_up"], 11.06)
        self.assertEqual(result["limit_down"], 9.05)

    def test_main_board_st(self):
        """主板ST股票 ±5%。"""
        result = compute_price_limits_wrapper(
            22.53, "MAIN", True, "ST益丰", trade_date=date(2026, 7, 5)
        )
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.05, places=2)
        self.assertEqual(result["limit_up"], 23.66)
        self.assertEqual(result["limit_down"], 21.40)

    def test_main_board_st_current(self):
        result = compute_price_limits_wrapper(
            22.53, "MAIN", True, "ST益丰", trade_date=date(2026, 7, 6)
        )
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.10, places=2)
        self.assertEqual(result["limit_up"], 24.78)
        self.assertEqual(result["limit_down"], 20.28)

    def test_gem_board(self):
        """创业板 ±20%。"""
        result = compute_price_limits_wrapper(86.36, "GEM", False, "华大九天")
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.20, places=2)
        self.assertEqual(result["limit_up"], 103.63)
        self.assertEqual(result["limit_down"], 69.09)

    def test_gem_st_board(self):
        """创业板ST股票 ±20%（注册制后ST也是20%）。"""
        result = compute_price_limits_wrapper(10.00, "GEM", True, "ST某某")
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.20, places=2)
        self.assertEqual(result["limit_up"], 12.00)
        self.assertEqual(result["limit_down"], 8.00)

    def test_star_board(self):
        """科创板 ±20%。"""
        result = compute_price_limits_wrapper(101.52, "STAR", False, "中芯国际")
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.20, places=2)
        self.assertEqual(result["limit_up"], 121.82)
        self.assertEqual(result["limit_down"], 81.22)

    def test_bj_board(self):
        """北交所 ±30%。"""
        result = compute_price_limits_wrapper(84.36, "BJ", False, "万达轴承")
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.30, places=2)
        self.assertEqual(result["limit_up"], 109.67)
        self.assertEqual(result["limit_down"], 59.05)

    def test_main_new_stock_no_limit(self):
        """沪深新股上市前5日无涨跌幅限制。"""
        result = compute_price_limits_wrapper(
            10.00, "MAIN", False, "主板新股", listed_days=1,
        )
        self.assertEqual(result["price_limit_status"], "NO_LIMIT")

    def test_main_new_stock_day_5_no_limit(self):
        """沪深新股上市第5日仍无涨跌幅限制。"""
        result = compute_price_limits_wrapper(
            10.00, "MAIN", False, "主板新股", listed_days=5,
        )
        self.assertEqual(result["price_limit_status"], "NO_LIMIT")

    def test_main_new_stock_day_6_has_limit(self):
        """沪深新股上市第6日开始有涨跌幅限制。"""
        result = compute_price_limits_wrapper(
            10.00, "MAIN", False, "主板新股", listed_days=6,
        )
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertEqual(result["limit_up"], 11.00)
        self.assertEqual(result["limit_down"], 9.00)

    def test_bj_new_stock_day_1_no_limit(self):
        """北交所新股上市首日无涨跌幅限制。"""
        result = compute_price_limits_wrapper(
            10.00, "BJ", False, "北交所新股", listed_days=1,
        )
        self.assertEqual(result["price_limit_status"], "NO_LIMIT")

    def test_bj_new_stock_day_2_has_limit(self):
        """北交所新股上市第2日开始有涨跌幅限制。"""
        result = compute_price_limits_wrapper(
            10.00, "BJ", False, "北交所新股", listed_days=2,
        )
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertAlmostEqual(result["limit_pct"], 0.30, places=2)
        self.assertEqual(result["limit_up"], 13.00)
        self.assertEqual(result["limit_down"], 7.00)

    def test_missing_pre_close(self):
        """缺少前收盘价时状态为 UNKNOWN。"""
        result = compute_price_limits_wrapper(None, "MAIN", False)
        self.assertEqual(result["price_limit_status"], "UNKNOWN")
        self.assertIsNone(result["limit_up"])
        self.assertIsNone(result["limit_down"])


class TestComputeLimitStatus(unittest.TestCase):
    """测试涨跌停状态判定。"""

    def test_limit_up(self):
        """收盘涨停。"""
        result = compute_limit_status(
            close=1980.00, high=1980.00, low=1790.00, open_=1810.00,
            limit_up=1980.00, limit_down=1620.00, price_limit_status="KNOWN",
        )
        self.assertTrue(result["is_limit_up"])
        self.assertFalse(result["is_limit_down"])
        self.assertTrue(result["touched_limit_up"])
        self.assertFalse(result["touched_limit_down"])
        self.assertTrue(result["is_sealed_limit_up"])
        self.assertFalse(result["is_sealed_limit_down"])
        self.assertFalse(result["is_one_word_limit_up"])

    def test_limit_down(self):
        """收盘跌停。"""
        result = compute_limit_status(
            close=9.00, high=9.80, low=9.00, open_=9.50,
            limit_up=11.00, limit_down=9.00, price_limit_status="KNOWN",
        )
        self.assertFalse(result["is_limit_up"])
        self.assertTrue(result["is_limit_down"])
        self.assertFalse(result["touched_limit_up"])
        self.assertTrue(result["touched_limit_down"])
        self.assertFalse(result["is_sealed_limit_up"])
        self.assertTrue(result["is_sealed_limit_down"])
        self.assertFalse(result["is_one_word_limit_down"])

    def test_one_word_limit_up_is_sealed(self):
        """一字涨停板计入封板。"""
        result = compute_limit_status(
            close=11.00, high=11.00, low=11.00, open_=11.00,
            limit_up=11.00, limit_down=9.00, price_limit_status="KNOWN",
        )
        self.assertTrue(result["is_limit_up"])
        self.assertTrue(result["touched_limit_up"])
        self.assertTrue(result["is_sealed_limit_up"])  # 修正：一字板计入封板
        self.assertTrue(result["is_one_word_limit_up"])

    def test_one_word_limit_down_is_sealed(self):
        """一字跌停板计入封板。"""
        result = compute_limit_status(
            close=9.00, high=9.00, low=9.00, open_=9.00,
            limit_up=11.00, limit_down=9.00, price_limit_status="KNOWN",
        )
        self.assertTrue(result["is_limit_down"])
        self.assertTrue(result["is_sealed_limit_down"])  # 修正：一字板计入封板
        self.assertTrue(result["is_one_word_limit_down"])

    def test_no_limit_status(self):
        """无涨跌幅限制时所有状态为 None。"""
        result = compute_limit_status(
            close=30.00, high=35.00, low=25.00, open_=28.00,
            limit_up=None, limit_down=None, price_limit_status="NO_LIMIT",
        )
        self.assertIsNone(result["is_limit_up"])
        self.assertIsNone(result["is_limit_down"])
        self.assertIsNone(result["touched_limit_up"])
        self.assertIsNone(result["touched_limit_down"])
        self.assertIsNone(result["is_sealed_limit_up"])
        self.assertIsNone(result["is_sealed_limit_down"])
        self.assertIsNone(result["is_one_word_limit_up"])
        self.assertIsNone(result["is_one_word_limit_down"])

    def test_unknown_status(self):
        """未知限价时所有状态为 None。"""
        result = compute_limit_status(
            close=10.00, high=11.00, low=9.00, open_=10.50,
            limit_up=None, limit_down=None, price_limit_status="UNKNOWN",
        )
        self.assertIsNone(result["is_limit_up"])
        self.assertIsNone(result["is_sealed_limit_up"])

    def test_missing_close(self):
        """收盘价缺失时状态为 None。"""
        result = compute_limit_status(
            close=None, high=11.00, low=9.00, open_=10.50,
            limit_up=11.00, limit_down=9.00, price_limit_status="KNOWN",
        )
        self.assertIsNone(result["is_limit_up"])
        self.assertIsNone(result["is_sealed_limit_up"])


class TestComputeConsecutiveLimits(unittest.TestCase):
    """测试连板统计（三值语义）。"""

    def test_consecutive_limit_up(self):
        """连续涨停（交易日序列，已知起点非涨停）。"""
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},   # 周五
            # 周末 09-12, 09-13 不在会话中
            {"trade_date": "2026-09-14", "is_limit_up": True},   # 周一
            {"trade_date": "2026-09-15", "is_limit_up": True},   # 周二
        ]
        result = compute_consecutive_limits(daily_bars, 3, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 3)  # 09-11+09-14+09-15
        self.assertFalse(result["board_break"])

    def test_board_break(self):
        """连板断档（已知起点）。"""
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},
            {"trade_date": "2026-09-15", "is_limit_up": True},
            {"trade_date": "2026-09-16", "is_limit_up": False},
        ]
        result = compute_consecutive_limits(daily_bars, 4, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 0)
        self.assertTrue(result["board_break"])

    def test_new_start_after_break(self):
        """断档后重新开始计数（已知起点）。"""
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": False},
            {"trade_date": "2026-09-15", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 3, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 1)
        self.assertFalse(result["board_break"])

    def test_unknown_prev_not_confirm_first_board(self):
        """前日状态未知，不能确认首板。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": None},  # 未知
            {"trade_date": "2026-09-12", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 1)
        self.assertIsNone(result["consecutive_limit_up"])  # 不确定
        self.assertFalse(result["board_break"])

    def test_unknown_current_not_confirm_break(self):
        """当前状态未知，不能确认断板。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-12", "is_limit_up": None},  # 未知
        ]
        result = compute_consecutive_limits(daily_bars, 1)
        self.assertIsNone(result["consecutive_limit_up"])
        self.assertIsNone(result["board_break"])  # 不确定

    def test_prev_unknown_current_down_not_break(self):
        """前日未知，当前非涨停，不能确认断板。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": None},  # 未知
            {"trade_date": "2026-09-12", "is_limit_up": False},
        ]
        result = compute_consecutive_limits(daily_bars, 1)
        self.assertEqual(result["consecutive_limit_up"], 0)
        self.assertIsNone(result["board_break"])  # 不确定

    def test_unknown_in_middle_stops_backtrace(self):
        """回溯遇到未知状态，连板数不确定。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-12", "is_limit_up": None},  # 未知
            {"trade_date": "2026-09-15", "is_limit_up": True},
            {"trade_date": "2026-09-16", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 3)
        self.assertIsNone(result["consecutive_limit_up"])  # 不确定
        self.assertFalse(result["board_break"])

    def test_no_limit_up(self):
        """非涨停日。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": False},
            {"trade_date": "2026-09-12", "is_limit_up": False},
        ]
        result = compute_consecutive_limits(daily_bars, 1)
        self.assertEqual(result["consecutive_limit_up"], 0)
        self.assertFalse(result["board_break"])

    def test_first_bar_no_prev_is_null(self):
        """第一根K线涨停，无前史 → consecutive=null（截断历史，不能确认首板）。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 0)
        self.assertIsNone(result["consecutive_limit_up"])  # 截断历史，不确定
        self.assertFalse(result["board_break"])

    def test_first_bar_not_limit_up(self):
        """第一根K线非涨停，无前史 → board_break=null（无依据断言）。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": False},
        ]
        result = compute_consecutive_limits(daily_bars, 0)
        self.assertEqual(result["consecutive_limit_up"], 0)
        self.assertIsNone(result["board_break"])  # 无前史，无法判断是否断档

    def test_session_continuity(self):
        """会话连续性：周末不中断连续涨停计数，但回溯到开头未遇已知非涨停 → null。"""
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": True},   # 周五（无前史）
            # 周末 2026-09-12, 2026-09-13 不在序列中
            {"trade_date": "2026-09-14", "is_limit_up": True},   # 周一
            {"trade_date": "2026-09-15", "is_limit_up": True},   # 周二
        ]
        result = compute_consecutive_limits(daily_bars, 2)
        # 回溯到开头（09-11）仍未遇到已知非涨停 → 截断边界，不确定
        self.assertIsNone(result["consecutive_limit_up"])
        self.assertFalse(result["board_break"])

    def test_prev_known_not_limit_up(self):
        """前日已知非涨停，当前涨停 → consecutive=1。"""
        session_dates = ["2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": False},
            {"trade_date": "2026-09-14", "is_limit_up": False},
            {"trade_date": "2026-09-15", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 2, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 1)
        self.assertFalse(result["board_break"])

    def test_pm_case_1_missing_09_14(self):
        """PM反例1：09-10非涨停、09-11涨停、09-15涨停，缺09-14 → null。

        会话对齐后：09-10=False, 09-11=True, 09-14=None, 09-15=True
        回溯到09-14遇到unknown → null
        """
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},
            {"trade_date": "2026-09-11", "is_limit_up": True},
            # 09-14 缺行
            {"trade_date": "2026-09-15", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 3, session_dates)
        self.assertIsNone(result["consecutive_limit_up"])  # 应为null
        self.assertFalse(result["board_break"])

    def test_pm_case_2_missing_09_14(self):
        """PM反例2：09-11涨停、09-15非涨停，缺09-14 → null。

        会话对齐后：09-11=True, 09-14=None, 09-15=False
        前日(09-14)为unknown → board_break=null
        """
        session_dates = ["2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": True},
            # 09-14 缺行
            {"trade_date": "2026-09-15", "is_limit_up": False},
        ]
        result = compute_consecutive_limits(daily_bars, 2, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 0)
        self.assertIsNone(result["board_break"])  # 应为null

    def test_complete_09_10_11_14_15_returns_3(self):
        """完整09-10/11/14/15序列返回3。"""
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},
            {"trade_date": "2026-09-15", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 3, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 3)
        self.assertFalse(result["board_break"])

    def test_append_future_data_no_change(self):
        """追加未来数据不改已有日期结果。"""
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},
            {"trade_date": "2026-09-15", "is_limit_up": True},
            {"trade_date": "2026-09-16", "is_limit_up": True},
        ]
        # 09-15的结果
        result_09_15 = compute_consecutive_limits(daily_bars, 3, session_dates)
        self.assertEqual(result_09_15["consecutive_limit_up"], 3)

        # 追加09-16后，09-15的结果不应改变
        result_09_15_after = compute_consecutive_limits(daily_bars, 3, session_dates)
        self.assertEqual(result_09_15_after["consecutive_limit_up"], 3)
        self.assertEqual(result_09_15_after, result_09_15)

    def test_truncated_history_returns_null(self):
        """回溯到开头仍未遇已知非涨停 → consecutive=null（截断边界）。"""
        # 有09-10已知起点
        session_dates_1 = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars_with_start = [
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},  # 周一
            {"trade_date": "2026-09-15", "is_limit_up": True},  # 周二
        ]
        result = compute_consecutive_limits(daily_bars_with_start, 3, session_dates_1)
        self.assertEqual(result["consecutive_limit_up"], 3)  # 09-11+09-14+09-15

        # 无已知起点，只有两条涨停
        session_dates_2 = ["2026-09-11", "2026-09-14"]
        daily_bars_truncated = [
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},  # 周一
        ]
        result = compute_consecutive_limits(daily_bars_truncated, 1, session_dates_2)
        self.assertIsNone(result["consecutive_limit_up"])  # 截断边界

    def test_09_14_missing_row_returns_null(self):
        """09-14缺行场景：09-11涨停、09-15涨停，中间缺失 → consecutive=null。

        PM反例：使用会话对齐补齐缺行后，09-14为unknown → 停止回溯 → null
        """
        session_dates = ["2026-09-11", "2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-11", "is_limit_up": True},  # 周五
            # 09-14 缺行（周一），对齐后填充 is_limit_up=None
            {"trade_date": "2026-09-15", "is_limit_up": True},  # 周二
        ]
        # 对齐后：[09-11=True, 09-14=None, 09-15=True]
        # 回溯到09-14遇到unknown → null
        result = compute_consecutive_limits(daily_bars, 2, session_dates)
        self.assertIsNone(result["consecutive_limit_up"])

    def test_complete_session_cross_week(self):
        """完整会话跨周：已知起点，周末不中断。"""
        session_dates = [
            "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12",
            # 周末 09-13, 09-14 不在会话中
            "2026-09-15",
        ]
        daily_bars = [
            {"trade_date": "2026-09-08", "is_limit_up": False},  # 周一，已知起点
            {"trade_date": "2026-09-09", "is_limit_up": True},   # 周二
            {"trade_date": "2026-09-10", "is_limit_up": True},   # 周三
            {"trade_date": "2026-09-11", "is_limit_up": True},   # 周四
            {"trade_date": "2026-09-12", "is_limit_up": True},   # 周五
            {"trade_date": "2026-09-15", "is_limit_up": True},   # 周一
        ]
        result = compute_consecutive_limits(daily_bars, 5, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 5)  # 09-09到09-15
        self.assertFalse(result["board_break"])

    def test_two_bars_truncated_history(self):
        """两条以上历史截断：09-14、09-15涨停，无前史 → null。"""
        session_dates = ["2026-09-14", "2026-09-15"]
        daily_bars = [
            {"trade_date": "2026-09-14", "is_limit_up": True},  # 周一，无前史
            {"trade_date": "2026-09-15", "is_limit_up": True},  # 周二
        ]
        result = compute_consecutive_limits(daily_bars, 1, session_dates)
        self.assertIsNone(result["consecutive_limit_up"])  # 截断边界

    def test_known_start_point(self):
        """已知起点：起点非涨停，后续涨停。"""
        session_dates = ["2026-09-10", "2026-09-11", "2026-09-14"]
        daily_bars = [
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},
        ]
        result = compute_consecutive_limits(daily_bars, 2, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 2)
        self.assertFalse(result["board_break"])

    def test_prefix_consistency(self):
        """前缀一致性：前缀截断不影响后续已知起点。"""
        session_dates = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14"]
        daily_bars = [
            {"trade_date": "2026-09-08", "is_limit_up": True},   # 截断前史
            {"trade_date": "2026-09-09", "is_limit_up": True},
            {"trade_date": "2026-09-10", "is_limit_up": False},  # 已知起点
            {"trade_date": "2026-09-11", "is_limit_up": True},
            {"trade_date": "2026-09-14", "is_limit_up": True},
        ]
        # 从index=2（09-10非涨停）开始，后续连板应从09-11计数
        result = compute_consecutive_limits(daily_bars, 4, session_dates)
        self.assertEqual(result["consecutive_limit_up"], 2)  # 09-11+09-14
        self.assertFalse(result["board_break"])


class TestSampleData(unittest.TestCase):
    """测试样例数据文件。"""

    def test_samples_valid(self):
        """验证样例数据文件格式正确。"""
        fixtures_path = Path(__file__).parent.parent / "fixtures" / "daily_derived_samples.json"
        with open(fixtures_path) as f:
            data = json.load(f)

        self.assertIn("version", data)
        self.assertIn("samples", data)
        self.assertGreater(len(data["samples"]), 0)

        for sample in data["samples"]:
            self.assertIn("name", sample)
            self.assertIn("symbol", sample)
            self.assertIn("market", sample)

    def test_single_day_samples(self):
        """测试单日样例数据的计算结果。"""
        fixtures_path = Path(__file__).parent.parent / "fixtures" / "daily_derived_samples.json"
        with open(fixtures_path) as f:
            data = json.load(f)

        for sample in data["samples"]:
            if "sequence" in sample:
                continue  # 跳过连板序列样例

            with self.subTest(sample=sample["name"]):
                board_type = sample.get("board_type", get_board_type(sample["symbol"]))
                is_st = sample.get("is_st", False)
                name = sample.get("name", "")
                listed_days = sample.get("listed_days")
                if listed_days is None and "listing_date" in sample and "trade_date" in sample:
                    listing_date = date.fromisoformat(sample["listing_date"])
                    trade_date = date.fromisoformat(sample["trade_date"])
                    listed_days = (trade_date - listing_date).days + 1

                # 计算涨跌停价格
                price_result = compute_price_limits_wrapper(
                    sample["input"].get("pre_close"),
                    board_type,
                    is_st,
                    name,
                    listed_days,
                    trade_date=date.fromisoformat(sample["trade_date"]),
                )

                # 验证涨跌停价格
                expected = sample["expected"]
                self.assertEqual(
                    price_result["price_limit_status"],
                    expected["price_limit_status"],
                    f"{sample['name']}: price_limit_status mismatch",
                )
                self.assertEqual(
                    price_result["limit_up"],
                    expected["limit_up"],
                    f"{sample['name']}: limit_up mismatch",
                )
                self.assertEqual(
                    price_result["limit_down"],
                    expected["limit_down"],
                    f"{sample['name']}: limit_down mismatch",
                )

                # 计算涨跌停状态
                if price_result["price_limit_status"] == "KNOWN":
                    status_result = compute_limit_status(
                        sample["input"]["close"],
                        sample["input"]["high"],
                        sample["input"]["low"],
                        sample["input"]["open"],
                        price_result["limit_up"],
                        price_result["limit_down"],
                        price_result["price_limit_status"],
                    )

                    # 验证状态
                    for field in [
                        "is_limit_up", "is_limit_down",
                        "touched_limit_up", "touched_limit_down",
                        "is_sealed_limit_up", "is_sealed_limit_down",
                        "is_one_word_limit_up", "is_one_word_limit_down",
                    ]:
                        if field in expected:
                            self.assertEqual(
                                status_result[field],
                                expected[field],
                                f"{sample['name']}: {field} mismatch",
                            )


if __name__ == "__main__":
    unittest.main()