# 公开读取合同 v1.0

**日期**：2026-09-18
**状态**：待PM审核提案（未冻结）
**下一包**：最小公开读取实现

---

## 1. 方法签名

```python
@public_read
def get_daily_with_limits(
    root: Path,
    market: str,           # "SH", "SZ", "BJ"
    symbol: str,           # 6位股票代码
    start_date: date | None = None,
    end_date: date | None = None,
    session_dates: list[str] | None = None,  # 可选：市场会话日期序列
) -> pd.DataFrame:
    """读取日线数据（含涨跌停衍生字段）。

    Returns:
        DataFrame with columns defined in DAILY_FIELDS + DAILY_DERIVED_FIELDS

    Raises:
        DataPoolError("POOL_NOT_FOUND", ...): 数据池不存在
        DataPoolError("DAILY_INVALID", ...): 日线数据格式错误
        DataPoolError("SYMBOL_NOT_FOUND", ...): 股票代码不存在
        DataPoolError("DATE_RANGE_INVALID", ...): 日期范围无效
    """
```

---

## 2. 字段表

### 2.1 基础字段（BASE_FIELDS）

| 字段 | 类型 | 单位 | 缺失 | 说明 |
|------|------|------|------|------|
| symbol | VARCHAR | - | 不缺失 | 股票代码 |
| market | VARCHAR | - | 不缺失 | 市场（SH/SZ/BJ） |
| code | VARCHAR | - | 不缺失 | 6位代码 |
| date | DATE | - | 不缺失 | 交易日期 |
| open | DOUBLE | CNY/share | 不缺失 | 开盘价 |
| high | DOUBLE | CNY/share | 不缺失 | 最高价 |
| low | DOUBLE | CNY/share | 不缺失 | 最低价 |
| close | DOUBLE | CNY/share | 不缺失 | 收盘价 |
| volume | DOUBLE | share | 不缺失 | 成交量 |
| amount | DOUBLE | CNY | 不缺失 | 成交额 |

### 2.2 可选字段（OPTIONAL_FIELDS）

| 字段 | 类型 | 单位 | 缺失 | 来源优先级 |
|------|------|------|------|------------|
| pre_close | DOUBLE | CNY/share | 可空 | 当日已有 > 历史日线推算 > null |
| turnover_rate | DOUBLE | percent | 可空 | 当日已有 > null |
| is_st | BOOLEAN | - | 可空 | 当日已有 > null（不回填历史） |

**来源优先级**：
1. 优先读取已有当日字段
2. 缺依据时保持null，不默认近似
3. 本包不提供近似模式

### 2.3 涨跌停衍生字段（LIMIT_FIELDS）

| 字段 | 类型 | 单位 | 缺失 | 来源 |
|------|------|------|------|------|
| limit_up | DOUBLE | CNY/share | 可空 | 规则计算（依赖pre_close） |
| limit_down | DOUBLE | CNY/share | 可空 | 规则计算（依赖pre_close） |
| limit_pct | DOUBLE | ratio | 可空 | 规则计算（板块+ST状态） |
| price_limit_status | VARCHAR | - | 不缺失 | KNOWN/UNKNOWN/NO_LIMIT |

**price_limit_status 枚举**：
- `KNOWN`：有可靠pre_close，已计算limit_up/limit_down
- `UNKNOWN`：缺pre_close或ST状态不明确
- `NO_LIMIT`：新股上市窗口期（无涨跌幅限制）

### 2.4 涨跌停状态字段（LIMIT_STATUS_FIELDS）

| 字段 | 类型 | 缺失 | 说明 |
|------|------|------|------|
| is_limit_up | BOOLEAN | 可空 | 收盘涨停（close >= limit_up） |
| is_limit_down | BOOLEAN | 可空 | 收盘跌停（close <= limit_down） |
| touched_limit_up | BOOLEAN | 可空 | 日内触及涨停（high >= limit_up） |
| touched_limit_down | BOOLEAN | 可空 | 日内触及跌停（low <= limit_down） |
| is_sealed_limit_up | BOOLEAN | 可空 | 收盘封板（含一字板） |
| is_sealed_limit_down | BOOLEAN | 可空 | 收盘封板（含一字板） |
| is_one_word_limit_up | BOOLEAN | 可空 | 一字涨停板 |
| is_one_word_limit_down | BOOLEAN | 可空 | 一字跌停板 |

**缺失条件**：price_limit_status != KNOWN 时所有状态字段为null

### 2.5 连板统计字段（CONSECUTIVE_FIELDS）

| 字段 | 类型 | 缺失 | 说明 |
|------|------|------|------|
| consecutive_limit_up | INTEGER | 可空 | 连板天数（null=不确定） |
| consecutive_limit_down | INTEGER | 可空 | 连跌停天数（null=不确定） |
| board_break | BOOLEAN | 可空 | 连板断档（null=不确定） |

**三值语义**：
- True/False：确定
- None：不确定（前史未知、数据缺失、截断边界）

### 2.6 异常标记字段

| 字段 | 类型 | 缺失 | 说明 |
|------|------|------|------|
| _invalid_reason | VARCHAR | 可空 | 异常原因（null=正常） |

**异常判定条件**：
- high > limit_up（A股交易所限制下不应出现）
- low < limit_down
- OHLC 逻辑错误（high < low 等）
- 其他数据质量问题

**统计排除**：正常统计时排除 `_invalid_reason IS NOT NULL` 的记录

---

## 3. describe 能力标识

```python
def describe_daily_with_limits(root: Path) -> dict:
    """返回数据集描述信息。"""
    return {
        "fields": {**DAILY_FIELDS, **DAILY_DERIVED_FIELDS, "_invalid_reason": ("VARCHAR", None)},
        "scope": {
            "markets": ["SH", "SZ", "BJ"],
            "asset_types": ["stock"],
            "period": "daily",
        },
        "coverage": {
            # 实际范围以数据为准，不预设
            "start_date": None,  # 待实测
            "end_date": None,    # 待实测
            "symbol_count": None,  # 待实测
        },
        "completion": {
            # 每字段有效/未知/无约束/异常数量待实测
            "status": "pending_measurement",
        },
        "quality": {
            "limit_fields": "computed",  # 规则计算
            "consecutive_fields": "computed",
            "invalid_isolation": True,  # _invalid_reason 列
        },
        "rule_coverage": {
            # 规则日期支持边界
            "main_board": "2010-01-01至今",  # 主板规则相对稳定
            "gem_board": "2020-08-24至今",   # 注册制改革
            "star_board": "2019-07-22至今",  # 科创板开板
            "bj_board": "2021-11-15至今",    # 北交所开板
            "st_rule": "仅基于当前名称，不回填历史",
        },
        "version": "1.0",
        "source": "aspool",
    }
```

---

## 4. 样例响应

### 4.1 正常 DataFrame

```python
import pandas as pd

df = pd.DataFrame([
    {
        "symbol": "600519", "market": "SH", "code": "600519",
        "date": "2026-09-15",
        "open": 1810.00, "high": 1980.00, "low": 1790.00, "close": 1980.00,
        "volume": 1000000, "amount": 1800000000,
        "pre_close": 1800.00, "turnover_rate": 0.5,
        "is_st": False,
        "limit_up": 1980.00, "limit_down": 1620.00,
        "limit_pct": 0.10, "price_limit_status": "KNOWN",
        "is_limit_up": True, "is_limit_down": False,
        "touched_limit_up": True, "touched_limit_down": False,
        "is_sealed_limit_up": True, "is_sealed_limit_down": False,
        "is_one_word_limit_up": False, "is_one_word_limit_down": False,
        "consecutive_limit_up": 3, "consecutive_limit_down": None,
        "board_break": False,
        "_invalid_reason": None,  # 正常数据
    },
    {
        "symbol": "920001", "market": "BJ", "code": "920001",
        "date": "2026-09-15",
        "open": 10.00, "high": 13.00, "low": 9.00, "close": 12.00,
        "volume": 500000, "amount": 5000000,
        "pre_close": None, "turnover_rate": None,
        "is_st": None,
        "limit_up": None, "limit_down": None,
        "limit_pct": None, "price_limit_status": "NO_LIMIT",
        "is_limit_up": None, "is_limit_down": None,
        "touched_limit_up": None, "touched_limit_down": None,
        "is_sealed_limit_up": None, "is_sealed_limit_down": None,
        "is_one_word_limit_up": None, "is_one_word_limit_down": None,
        "consecutive_limit_up": None, "consecutive_limit_down": None,
        "board_break": None,
        "_invalid_reason": None,  # 正常数据
    },
])
```

### 4.2 异常数据 DataFrame

```python
df_with_invalid = pd.DataFrame([
    {
        "symbol": "600001", "market": "SH", "code": "600001",
        "date": "2026-09-15",
        "open": 1010.00, "high": 1150.00, "low": 990.00, "close": 1100.00,
        "limit_up": 1100.00,
        "is_limit_up": True, "is_sealed_limit_up": True,
        "_invalid_reason": "high(1150) > limit_up(1100)",  # 异常标记
    },
])

# 统计时排除异常
valid_df = df_with_invalid[df_with_invalid["_invalid_reason"].isna()]
limit_up_count = valid_df["is_limit_up"].sum()  # 只统计正常数据
```

---

## 5. 批次状态枚举

```python
class BatchStatus(str, Enum):
    """批次完成度状态。"""
    COMPLETE = "complete"      # 全部完成
    PARTIAL = "partial"        # 部分完成
    EMPTY = "empty"            # 无数据
    PENDING = "pending"        # 待测量
```

**每字段数量结构**（待实测）：
```python
@dataclass
class FieldStats:
    """字段统计。"""
    valid: int = 0       # 有效数量
    unknown: int = 0     # 未知数量
    unconstrained: int = 0  # 无约束数量
    invalid: int = 0     # 异常数量
    total: int = 0       # 总数
```

---

## 6. 错误码

| 错误码 | 含义 | 场景 |
|--------|------|------|
| POOL_NOT_FOUND | 数据池不存在 | root目录无效 |
| DAILY_INVALID | 日线数据格式错误 | parquet文件损坏 |
| SYMBOL_NOT_FOUND | 股票代码不存在 | 未在universe中 |
| DATE_RANGE_INVALID | 日期范围无效 | start > end |

---

## 7. 规则依据

### 7.1 涨跌幅限制规则

| 板块 | 普通 | ST | 新股窗口 | 规则日期 |
|------|------|-----|----------|----------|
| 主板(60/00) | ±10% | ±5% | 5个交易日 | 2010-01-01至今 |
| 创业板(30) | ±20% | ±20% | 5个交易日 | 2020-08-24至今 |
| 科创板(688) | ±20% | ±20% | 5个交易日 | 2019-07-22至今 |
| 北交所(43/83/87/92) | ±30% | ±30% | 1个交易日 | 2021-11-15至今 |

**注意**：规则日期边界明确，超出范围的日期使用unknown，不泛称2010年至今均可靠。

### 7.2 计算规则

```python
# 涨停价 = round(pre_close * (1 + limit_pct) + 0.00001, 2)
# 跌停价 = round(pre_close * (1 - limit_pct) + 0.00001, 2)
# 一字板 = (open == high == low == close == limit_up)
# 封板 = is_limit_up（含一字板）
# 连板 = 从当前向前回溯连续涨停天数（需会话对齐）
```

### 7.3 三值语义

| 字段 | True | False | None |
|------|------|-------|------|
| is_limit_up | 收盘涨停 | 非涨停 | 无限价或数据缺失 |
| consecutive_limit_up | 连续N日涨停 | 非涨停 | 前史未知或截断边界 |
| board_break | 前日涨停今日非 | 非断档 | 前日状态未知 |

---

## 8. 已知限制

1. **历史ST状态**：仅基于当前名称判断，不回填历史
2. **上市日期**：无数据源，新股窗口期依赖外部数据
3. **复权影响**：pre_close使用未复权价格
4. **北交所覆盖**：部分代码可能缺失
5. **规则日期边界**：明确标注，超出范围使用unknown

---

## 9. 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-09-18 | 待PM审核提案 |