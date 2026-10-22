"""A 股价格限制规则引擎。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..models.enums import Market
from ..models.finance import FinanceInfo

# 各板块规则的生效起点。早于该日期的历史区间无可靠依据，按 UNKNOWN 处理。
STAR_EFFECTIVE = date(2019, 7, 22)  # 科创板开板
GEM_REGISTRATION_EFFECTIVE = date(2020, 8, 24)  # 创业板注册制改革
GEM_INCEPTION = date(2009, 10, 30)  # 创业板开板
BJ_EFFECTIVE = date(2021, 11, 15)  # 北交所开市
MAIN_BOARD_INCEPTION = date(1996, 12, 16)  # 主板涨跌幅限制制度生效
# The 2023 revised SSE Trading Rules apply to the first main-board stock
# issued under the registration system, which listed on 2023-04-10.
MAIN_BOARD_IPO_WINDOW_EFFECTIVE = date(2023, 4, 10)
MAIN_BOARD_LEGACY_IPO_EFFECTIVE = date(2014, 6, 13)
MAIN_BOARD_ST_TEN_PERCENT = date(2026, 7, 6)


@dataclass(frozen=True)
class LimitRule:
    """一条已确认依据的涨跌幅规则。"""

    limit_pct: float
    label: str
    effective_from: date
    source: str


@dataclass(frozen=True)
class LimitRuleResult:
    """规则解析结果。

    三态：
    - rule 非空            => KNOWN
    - no_limit 为 True     => NO_LIMIT（已确认处于无涨跌幅限制状态）
    - 两者皆空             => UNKNOWN，reason 说明原因
    """

    rule: LimitRule | None
    reason: str | None = None
    no_limit: bool = False
    no_limit_basis: str | None = None

    @property
    def is_known(self) -> bool:
        return self.rule is not None

    @property
    def is_no_limit(self) -> bool:
        return self.no_limit


def is_star_board(code: str) -> bool:
    return code.startswith("688")


def is_gem_board(code: str) -> bool:
    return code.startswith(("300", "301", "302"))


def is_bj_board(code: str) -> bool:
    return code.startswith(("43", "83", "87", "92"))


# 规则依据出处（可定位的交易所公开规则）。
_MAIN_BOARD_SOURCE = (
    "《上海证券交易所交易规则》第3.4节 / 《深圳证券交易所交易规则》第3.4节："
    "主板日涨跌幅限制为10%；风险警示股票为5%（1998-04-22起实施风险警示制度）"
)
_MAIN_BOARD_2026_SOURCE = (
    "沪深交易规则（2026年修订），2026-07-06起主板风险警示股票限幅10%；"
    "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml；"
    "https://www.szse.cn/lawrules/service/member/t20260630_621404.html"
)
_STAR_SOURCE = (
    "《上海证券交易所科创板股票交易特别规定》（2019-07-22施行）第4章："
    "竞价交易涨跌幅限制为20%，风险警示股票同为20%"
)
_GEM_REG_SOURCE = (
    "《深圳证券交易所创业板交易特别规定》（2020-08-24施行）第4章："
    "竞价交易涨跌幅限制为20%，风险警示股票同为20%"
)
_GEM_LEGACY_SOURCE = (
    "《深圳证券交易所创业板股票上市规则》（2009-10-30施行，注册制前）："
    "日涨跌幅限制为10%；风险警示股票为5%"
)
_BJ_SOURCE = (
    "《北京证券交易所交易规则》（2021-11-15施行）第3.4节："
    "竞价交易涨跌幅限制为30%，风险警示股票同为30%"
)

# 上市初期无涨跌幅窗口的适用区间与出处。
# 区间内按窗口交易日数处理；区间外（含更早历史）无可靠依据，返回 None 交由上层记 UNKNOWN。
_NO_LIMIT_WINDOWS = (
    # (判定, 窗口交易日数, 适用起, 适用止, 出处)
    (
        lambda market, code: is_star_board(code),
        5,
        STAR_EFFECTIVE,
        None,
        "《科创板股票交易特别规定》：首次公开发行上市后前5个交易日不设涨跌幅限制",
    ),
    (
        lambda market, code: is_gem_board(code),
        5,
        GEM_REGISTRATION_EFFECTIVE,
        None,
        "《创业板交易特别规定》（2020-08-24起）：首次公开发行上市后前5个交易日不设涨跌幅限制",
    ),
    (
        lambda market, code: is_bj_board(code),
        1,
        BJ_EFFECTIVE,
        None,
        "《北京证券交易所交易规则》：公开发行上市首日不设涨跌幅限制",
    ),
    (
        lambda market, code: market == Market.SH and code.startswith("60"),
        5,
        MAIN_BOARD_IPO_WINDOW_EFFECTIVE,
        None,
        "《上海证券交易所交易规则（2023年修订）》第3.4.13条（上证发〔2023〕32号）："
        "自首只按《首次公开发行股票注册管理办法》发行的主板股票上市首日起，"
        "首次公开发行上市股票上市后前5个交易日不设价格涨跌幅限制；"
        "上交所首批主板注册制企业于2023-04-10上市",
    ),
    (
        lambda market, code: market == Market.SZ and code.startswith("00"),
        5,
        MAIN_BOARD_IPO_WINDOW_EFFECTIVE,
        None,
        "《深圳证券交易所交易规则（2023年修订）》第3.3.15条及全面注册制主板适用安排："
        "自首只按《首次公开发行股票注册管理办法》发行的主板股票上市首日起，"
        "首次公开发行上市股票上市后前5个交易日不设价格涨跌幅限制；"
        "沪深首批主板注册制企业于2023-04-10上市",
    ),
)


def resolve_limit_rule(
    market: Market,
    code: str,
    name: str,
    trade_date: date,
    st_status: bool | None = None,
    listed_days: int | None = None,
    observed_sessions: int | None = None,
) -> LimitRuleResult:
    """按交易日解析适用的涨跌幅规则。

    Args:
        st_status:
            该交易日是否风险警示（ST）。三态：True/False 为已确认，None 为无依据。
            无历史 ST 来源时必须传 None，不得用当前名称回填历史。
        listed_days:
            该交易日为止的**实际**已上市交易日数（首日=1）。可靠来源才可传。
        observed_sessions:
            池内可观察到的、截至该日的会话数。这是上市交易日数的**下界**：
            首根可得日线必然不早于上市日，故 observed_sessions > 窗口
            即可**排除**无涨跌幅窗口；反之只能判为 UNKNOWN。

    Returns:
        LimitRuleResult：KNOWN（rule 非空）/ NO_LIMIT（no_limit=True）/ UNKNOWN。
    """
    if _is_index_like(market, code, name):
        return LimitRuleResult(None, "指数/板块类代码不属于个股涨跌停范围")

    # 板块身份与规则生效区间。
    if is_star_board(code):
        if trade_date < STAR_EFFECTIVE:
            return LimitRuleResult(None, f"早于科创板开板日 {STAR_EFFECTIVE}，无可靠规则依据")
        rule = LimitRule(0.20, "科创板", STAR_EFFECTIVE, _STAR_SOURCE)
    elif is_bj_board(code):
        if trade_date < BJ_EFFECTIVE:
            return LimitRuleResult(None, f"早于北交所开市日 {BJ_EFFECTIVE}，无可靠规则依据")
        rule = LimitRule(0.30, "北交所", BJ_EFFECTIVE, _BJ_SOURCE)
    elif is_gem_board(code):
        if trade_date >= GEM_REGISTRATION_EFFECTIVE:
            rule = LimitRule(0.20, "创业板(注册制)", GEM_REGISTRATION_EFFECTIVE, _GEM_REG_SOURCE)
        else:
            if trade_date < GEM_INCEPTION:
                return LimitRuleResult(None, f"早于创业板开板日 {GEM_INCEPTION}，无可靠规则依据")
            if st_status is None:
                return LimitRuleResult(
                    None, "创业板注册制前涨跌幅取决于风险警示状态，无历史 ST 依据"
                )
            pct = 0.05 if st_status else 0.10
            label = "创业板(注册制前,ST)" if st_status else "创业板(注册制前)"
            rule = LimitRule(pct, label, GEM_INCEPTION, _GEM_LEGACY_SOURCE)
    else:
        if trade_date < MAIN_BOARD_INCEPTION:
            return LimitRuleResult(
                None, f"早于主板涨跌幅制度生效日 {MAIN_BOARD_INCEPTION}，无可靠规则依据"
            )
        if trade_date >= MAIN_BOARD_ST_TEN_PERCENT:
            rule = LimitRule(
                0.10, "主板(2026新规)", MAIN_BOARD_ST_TEN_PERCENT, _MAIN_BOARD_2026_SOURCE
            )
        elif st_status is None:
            return LimitRuleResult(None, "主板涨跌幅取决于风险警示状态，无历史 ST 依据")
        else:
            pct = 0.05 if st_status else 0.10
            label = "主板(ST)" if st_status else "主板"
            rule = LimitRule(pct, label, MAIN_BOARD_INCEPTION, _MAIN_BOARD_SOURCE)

    # 上市无涨跌幅窗口：必须能可靠排除，否则不得按常规限价声称 KNOWN。
    main_board = (market == Market.SH and code.startswith("60")) or (
        market == Market.SZ and code.startswith("00")
    )
    if (
        main_board
        and MAIN_BOARD_LEGACY_IPO_EFFECTIVE <= trade_date < MAIN_BOARD_IPO_WINDOW_EFFECTIVE
    ):
        # Legacy IPO first-day bounds refer to the issue price, not pre_close.
        # SSE: https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988762.shtml
        # SZSE: https://www.szse.cn/www/disclosure/notice/company/t20140613_508770.html
        age = listed_days if listed_days is not None else observed_sessions
        if age is not None and age > 1:
            return LimitRuleResult(rule)
        return LimitRuleResult(
            None, "主板注册制前上市首日采用发行价特殊限幅，缺少首日排除依据或发行价"
        )
    if (main_board and trade_date < MAIN_BOARD_LEGACY_IPO_EFFECTIVE) or (
        is_gem_board(code) and trade_date < GEM_REGISTRATION_EFFECTIVE
    ):
        # Historic exchanges exempted the first listing session only. A dated
        # age or an observed-session lower bound greater than one excludes it.
        # SZSE 2004 historical review of the 1996 SSE/SZSE rule:
        # https://www.szse.cn/aboutus/research/secuities/documents/t20040106_531320.html
        # SZSE 2006 Trading Rules 3.3.14 and 2011 amendment explanation:
        # https://www.szse.cn/disclosure/notice/t20060515_499577.html
        # https://www.szse.cn/disclosure/notice/t20110118_500654.html
        age = listed_days if listed_days is not None else observed_sessions
        if age is not None and age > 1:
            return LimitRuleResult(rule)
        return LimitRuleResult(None, "无法排除注册制前上市首日特殊交易规则，缺少可靠上市依据")
    window_spec = resolve_no_limit_window(market, code, trade_date)
    if window_spec is None:
        return LimitRuleResult(
            None,
            f"{trade_date} 所属时期的上市无涨跌幅窗口规则未确认，"
            "不得按常规限价处理（不套用当前窗口）",
        )
    window, window_source = window_spec
    if window > 0:
        if listed_days is not None:
            if 0 < listed_days <= window:
                return LimitRuleResult(
                    None,
                    None,
                    no_limit=True,
                    no_limit_basis=(
                        f"上市第 {listed_days} 个交易日，处于 {window} 日无涨跌幅窗口；"
                        f"依据：{window_source}"
                    ),
                )
        elif observed_sessions is None:
            return LimitRuleResult(None, "缺上市日期依据，无法排除上市无涨跌幅窗口")
        elif observed_sessions <= window:
            return LimitRuleResult(
                None,
                f"可观察会话仅 {observed_sessions} 个（窗口 {window} 日），"
                "无法排除上市无涨跌幅窗口，缺可靠上市依据",
            )

    return LimitRuleResult(rule)


def resolve_no_limit_window(market: Market, code: str, trade_date: date) -> tuple[int, str] | None:
    """该交易日适用的上市初期无涨跌幅窗口。

    Returns:
        (窗口交易日数, 出处)。窗口为 0 表示该板块无此窗口。
        返回 None 表示**该时期窗口规则无可靠依据**，调用方应记 UNKNOWN。
    """
    if _is_index_like(market, code, ""):
        return (0, "指数/板块类不适用个股上市窗口")
    for predicate, window, effective_from, effective_to, source in _NO_LIMIT_WINDOWS:
        if not predicate(market, code):
            continue
        if trade_date < effective_from:
            # 该板块该时期尚无已确认的窗口规则。
            return None
        if effective_to is not None and trade_date > effective_to:
            return None
        return (window, source)
    return (0, "该证券不属于已知上市窗口板块")


def get_no_limit_window_days(market: Market, code: str, name: str) -> int:
    """返回上市初期不设涨跌幅限制的交易日窗口。

    返回值:
        0: 默认按常规涨跌幅限制处理
        1: 北交所上市首日不设涨跌幅限制
        5: 沪深主板/创业板/科创板上市前 5 个交易日不设涨跌幅限制
    """
    if _is_index_like(market, code, name):
        return 0

    if code.startswith(("43", "83", "87", "92")):
        return 1

    if market == Market.SH and code.startswith(("60", "68")):
        return 5
    if market == Market.SZ and code.startswith(("00", "30")):
        return 5

    return 0


def _is_index_like(market: Market, code: str, name: str) -> bool:
    """判断是否为指数/板块类代码。"""
    if market == Market.SH and code.startswith(
        ("000", "880", "881", "882", "883", "884", "885", "999")
    ):
        return True
    if market == Market.SZ and code.startswith(("395", "399")):
        return True
    return "指数" in name or "板块" in name


def compute_price_limits(
    market: Market,
    code: str,
    name: str,
    pre_close: float,
    finance_info: FinanceInfo | None = None,
    listed_days: int | None = None,
    trade_date: date | None = None,
) -> tuple[float | None, float | None]:
    """根据板块规则计算涨跌停价。

    Returns:
        (limit_up, limit_down)

    无涨跌幅限制或当前规则无法可靠判断时返回 ``(None, None)``。

    Args:
        listed_days:
            已上市交易天数（按交易日计，首日=1）。
            若提供该值，函数会按上市初期无涨跌幅限制规则优先返回 ``(None, None)``。
        trade_date:
            主板 ST 规则适用日，默认上海时区当日；完整历史规则使用 resolve_limit_rule。
    """
    if pre_close <= 0:
        return None, None

    upper_name = name.upper()
    trade_date = trade_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()

    # 指数/板块类代码通常无涨跌停。
    if _is_index_like(market, code, name):
        return None, None

    no_limit_window_days = get_no_limit_window_days(market, code, name)
    if listed_days is not None and 0 < listed_days <= no_limit_window_days:
        return None, None

    # 规则依据：
    # Main-board ST: 5% before 2026-07-06, 10% from that date.
    # - 创业板(30): 2020-08-24后注册制, 普通±20%, ST±20%
    # - 科创板(688): 注册制, 普通±20%, ST±20%
    # - 北交所(43/83/87/92): 普通±30%, ST±30%

    # 1. 科创板/创业板: 注册制板块, ST也是20%
    if is_star_board(code) or is_gem_board(code):
        limit_pct = 0.20
    # 2. 北交所: 30%
    elif code.startswith(("43", "83", "87", "92")):
        limit_pct = 0.30
    # Main-board ST before the 2026 rule change.
    elif "ST" in upper_name and trade_date < MAIN_BOARD_ST_TEN_PERCENT:
        limit_pct = 0.05
    # 4. 主板普通: 10%
    else:
        limit_pct = 0.10

    # `listed_days` 是更可靠的交易日维度输入；finance_info 仍保留给上层调用方扩展。
    _ = finance_info

    def _round_price(p: float) -> float:
        return round(p + 0.00001, 2)

    limit_up = _round_price(pre_close * (1 + limit_pct))
    limit_down = _round_price(pre_close * (1 - limit_pct))

    return limit_up, limit_down
