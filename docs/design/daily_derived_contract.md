# 日线衍生字段契约 v1.0

## 1. 背景与目标

定义 aspool 日线存储中**衍生字段**的语义、计算依据和覆盖状态。

UP 职责：提供市场事实（涨跌幅限制规则、交易日历、复权因子）。
Fundwise 职责：基于衍生字段计算评分和阶段。
CC 职责：展示结果。

---

## 2. 数据源现状

### 2.1 MAC K线 (`MacBar`) 原始字段

| 字段 | 类型 | 单位 | 说明 |
|------|------|------|------|
| datetime | datetime | - | 交易日期（日线时分秒为0） |
| open | float | 元 | 开盘价 |
| high | float | 元 | 最高价 |
| low | float | 元 | 最低价 |
| close | float | 元 | 收盘价 |
| vol | float | 股 | 成交量 |
| amount | float | 元 | 成交额 |
| float_shares | float | 万股 | 流通股本 |

**缺失**：前收盘价、涨跌停价、换手率、量比、振幅、涨跌幅、是否ST。

### 2.2 行情快照 (`SecurityQuote`)

| 字段 | 当前状态 | 说明 |
|------|----------|------|
| limit_up | 硬编码 None | 需要业务规则计算 |
| limit_down | 硬编码 None | 需要业务规则计算 |

### 2.3 市场统计 (`MarketStat`)

| 字段 | 来源 | 说明 |
|------|------|------|
| limit_up_count | 880006.close | 市场涨停家数 |
| limit_down_count | 880006.open | 市场跌停家数 |

---

## 3. 涨跌幅限制规则（A股）

### 3.1 主板/中小板（60xxxx, 00xxxx）

- **普通股票**：±10%
- **ST/*ST 股票**：±5%
- **新股上市首日**：±44%（2014年后）/ 无限制（历史时期不同）

### 3.2 创业板（30xxxx）

- **2020年8月24日后**：±20%（注册制）
- **之前**：±10%
- **ST 股票**：±20%（注册制后）/ ±5%（之前）
- **新股上市前5日**：不设涨跌幅限制

### 3.3 科创板（688xxx）

- **所有股票**：±20%
- **新股上市前5日**：不设涨跌幅限制

### 3.4 北交所（8xxxxx, 4xxxxx, 920xxx）

- **所有股票**：±30%
- **新股上市首日**：不设涨跌幅限制

### 3.5 计算公式

```
涨停价 = round(pre_close * (1 + limit_pct), 2)
跌停价 = round(pre_close * (1 - limit_pct), 2)
```

其中 `round` 为四舍五入到分（0.01元）。

---

## 4. 字段定义

### 4.1 价格限制类

| 字段名 | 类型 | 单位 | 定义 | 计算依据 |
|--------|------|------|------|----------|
| pre_close | DOUBLE | CNY/share | 前收盘价 | 需要补充数据源 |
| limit_up | DOUBLE | CNY/share | 涨停价 | pre_close * (1 + limit_pct) |
| limit_down | DOUBLE | CNY/share | 跌停价 | pre_close * (1 - limit_pct) |
| limit_pct | DOUBLE | percent | 涨跌幅限制 | 根据板块和ST状态确定 |

### 4.2 价格状态分类

| 字段名 | 类型 | 定义 |
|--------|------|------|
| price_limit_status | VARCHAR | 价格限制状态分类 |

**price_limit_status 取值**：

| 值 | 含义 | 判定条件 |
|----|------|----------|
| `KNOWN` | 已知限价 | 有 pre_close 且可计算涨跌停价 |
| `UNKNOWN` | 未知限价 | 缺少有效参考价、上市日期依据或规则不受支持；缺 ST 依据默认按非 ST |
| `NO_LIMIT` | 无限价 | 新股上市首日/前5日（根据板块规则） |

### 4.3 涨跌停判定类

| 字段名 | 类型 | 定义 |
|--------|------|------|
| is_limit_up | BOOLEAN | 收盘涨停（close >= limit_up） |
| is_limit_down | BOOLEAN | 收盘跌停（close <= limit_down） |
| touched_limit_up | BOOLEAN | 日内触及涨停（high >= limit_up） |
| touched_limit_down | BOOLEAN | 日内触及跌停（low <= limit_down） |
| is_sealed_limit_up | BOOLEAN | 收盘封板（is_limit_up 且 非一字板） |
| is_sealed_limit_down | BOOLEAN | 收盘封板（is_limit_down 且 非一字板） |

**注**：
- 一字板（open == high == low == close == limit_up）不计入封板
- 无涨跌幅限制的股票（price_limit_status = NO_LIMIT）所有 limit 相关字段为 NULL

### 4.4 连板统计类

| 字段名 | 类型 | 定义 |
|--------|------|------|
| consecutive_limit_up | INT | 连板天数（当前涨停 + 前N日连续涨停） |
| consecutive_limit_down | INT | 连跌停天数 |
| board_break | BOOLEAN | 连板断档（前一日涨停，今日非涨停） |

**连板计算规则**：
- 从当前交易日向前回溯
- 遇到第一个非涨停日停止
- 新股上市首日/前5日不参与连板计数
- 连板断档 = `is_limit_up[昨] AND NOT is_limit_up[今]`

---

## 5. 字段合同（DAILY_FIELDS 扩展）

```python
# api_contract.py 扩展

LIMIT_FIELDS = {
    "pre_close": ("DOUBLE", "CNY/share"),
    "limit_up": ("DOUBLE", "CNY/share"),
    "limit_down": ("DOUBLE", "CNY/share"),
    "limit_pct": ("DOUBLE", "percent"),
    "price_limit_status": ("VARCHAR", None),
}

LIMIT_STATUS_FIELDS = {
    "is_limit_up": ("BOOLEAN", None),
    "is_limit_down": ("BOOLEAN", None),
    "touched_limit_up": ("BOOLEAN", None),
    "touched_limit_down": ("BOOLEAN", None),
    "is_sealed_limit_up": ("BOOLEAN", None),
    "is_sealed_limit_down": ("BOOLEAN", None),
}

CONSECUTIVE_FIELDS = {
    "consecutive_limit_up": ("INTEGER", None),
    "consecutive_limit_down": ("INTEGER", None),
    "board_break": ("BOOLEAN", None),
}

DAILY_DERIVED_FIELDS = {**LIMIT_FIELDS, **LIMIT_STATUS_FIELDS, **CONSECUTIVE_FIELDS}
```

---

## 6. 覆盖状态

### 6.1 当前实现状态

| 字段组 | 状态 | 说明 |
|--------|------|------|
| LIMIT_FIELDS | ❌ 未实现 | 需要补充 pre_close 数据源 |
| LIMIT_STATUS_FIELDS | ❌ 未实现 | 依赖 LIMIT_FIELDS |
| CONSECUTIVE_FIELDS | ❌ 未实现 | 依赖 LIMIT_STATUS_FIELDS |

### 6.2 数据源需求

| 数据项 | 当前可用 | 需要补充 |
|--------|----------|----------|
| pre_close | ❌ | 需要从行情快照或历史数据获取 |
| is_st | ❌ | 需要从基本面数据或公告获取 |
| 板块类型 | ✅ | 可从代码前缀推断 |
| 新股标识 | ❌ | 需要从上市日期判断 |

---

## 7. 可核对样例

### 7.1 样例数据（贵州茅台 600519）

```
交易日期: 2026-09-15
pre_close: 1800.00
close: 1980.00
high: 1990.00
low: 1790.00
open: 1810.00
板块: 主板
is_st: false
limit_pct: 10%

计算结果:
limit_up = round(1800 * 1.10, 2) = 1980.00
limit_down = round(1800 * 0.90, 2) = 1620.00
price_limit_status = KNOWN
is_limit_up = (1980.00 >= 1980.00) = true
is_limit_down = (1980.00 <= 1620.00) = false
touched_limit_up = (1990.00 >= 1980.00) = true
touched_limit_down = (1790.00 <= 1620.00) = false
is_sealed_limit_up = true (非一字板)
is_sealed_limit_down = false
```

### 7.2 样例数据（ST 股票）

```
交易日期: 2026-09-15
pre_close: 5.00
close: 5.25
high: 5.30
low: 4.90
open: 5.10
板块: 主板
is_st: true
limit_pct: 5%

计算结果:
limit_up = round(5.00 * 1.05, 2) = 5.25
limit_down = round(5.00 * 0.95, 2) = 4.75
price_limit_status = KNOWN
is_limit_up = (5.25 >= 5.25) = true
is_limit_down = false
touched_limit_up = (5.30 >= 5.25) = true
is_sealed_limit_up = true (非一字板)
```

### 7.3 样例数据（新股上市首日）

```
交易日期: 2026-09-15
pre_close: NULL (新股无前收盘)
close: 30.00
high: 35.00
low: 25.00
open: 28.00
板块: 创业板 (301xxx)
is_st: false
上市日期: 2026-09-15

计算结果:
price_limit_status = NO_LIMIT (新股上市前5日)
limit_up = NULL
limit_down = NULL
is_limit_up = NULL
is_limit_down = NULL
touched_limit_up = NULL
touched_limit_down = NULL
```

### 7.4 样例数据（连板断档）

```
股票: 000001
日期       | close  | limit_up | is_limit_up | consecutive_limit_up | board_break
2026-09-11 | 10.00  | 10.00    | true        | 1                    | false
2026-09-12 | 11.00  | 11.00    | true        | 2                    | false
2026-09-13 | 12.10  | 12.10    | true        | 3                    | false
2026-09-14 | 13.31  | 13.31    | true        | 4                    | false
2026-09-15 | 14.00  | 14.64    | false       | 0                    | true  ← 断档
2026-09-16 | 15.40  | 15.40    | true        | 1                    | false
```

---

## 8. 实现路径

### 8.1 Phase 1: 补充 pre_close（阻塞项）

**方案 A：从历史日线推算**
- 优点：无需额外数据源
- 缺点：需要至少2天历史数据；复权调整时需特殊处理

**方案 B：从实时行情快照获取**
- 优点：准确
- 缺点：需要网络；历史数据无法补充

**方案 C：从 free-stockdb 导入**
- 优点：一次性补充
- 缺点：依赖外部数据源

**推荐**：Phase 1 使用方案 A（从历史推算），Phase 2 考虑方案 C 补充历史数据。

### 8.2 Phase 2: 补充 is_st

**数据源**：
- 从股票名称判断（包含 "ST" 字样）
- 从基本面数据获取
- 从交易所公告获取

### 8.3 Phase 3: 实现计算逻辑

```python
def compute_price_limits(
    pre_close: float | None,
    board_type: str,  # "MAIN", "GEM", "STAR", "BJ"
    is_st: bool,
    listing_date: date | None,
    trade_date: date,
) -> dict:
    """计算涨跌停价格和状态。"""
    # 1. 判断是否无涨跌幅限制
    if listing_date and (trade_date - listing_date).days < 5:
        return {"price_limit_status": "NO_LIMIT", "limit_up": None, "limit_down": None}

    # 2. 确定涨跌幅限制
    if board_type == "BJ":
        limit_pct = 0.30
    elif board_type in ("GEM", "STAR"):
        limit_pct = 0.20 if not is_st else 0.20
    else:  # MAIN
        limit_pct = 0.05 if is_st else 0.10

    # 3. 计算涨跌停价
    if pre_close is None:
        return {"price_limit_status": "UNKNOWN", "limit_up": None, "limit_down": None}

    limit_up = round(pre_close * (1 + limit_pct), 2)
    limit_down = round(pre_close * (1 - limit_pct), 2)

    return {
        "price_limit_status": "KNOWN",
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_pct": limit_pct,
    }
```

### 8.4 Phase 4: 连板统计

```python
def compute_consecutive_limits(
    daily_bars: list[dict],
    current_index: int,
) -> dict:
    """计算连板天数和断档状态。"""
    current = daily_bars[current_index]
    if not current.get("is_limit_up"):
        # 检查是否断档
        if current_index > 0 and daily_bars[current_index - 1].get("is_limit_up"):
            return {"consecutive_limit_up": 0, "board_break": True}
        return {"consecutive_limit_up": 0, "board_break": False}

    # 向前回溯连续涨停
    count = 1
    for i in range(current_index - 1, -1, -1):
        if daily_bars[i].get("is_limit_up"):
            count += 1
        else:
            break

    return {"consecutive_limit_up": count, "board_break": False}
```

---

## 9. 公开读取接口（设计）

```python
# aspool/api_contract.py 扩展

@public_read
def get_daily_with_limits(
    root: Path,
    market: str,
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """读取日线数据（含涨跌停衍生字段）。

    Returns:
        DataFrame with columns defined in DAILY_FIELDS + DAILY_DERIVED_FIELDS

    Raises:
        DataPoolError: POOL_NOT_FOUND, DAILY_INVALID
    """
    # 1. 读取原始日线
    # 2. 补充 pre_close（从历史推算）
    # 3. 计算涨跌停价格
    # 4. 计算涨跌停状态
    # 5. 计算连板统计
    pass
```

---

## 10. 待确认事项

1. **新股上市日期数据源**：需要从哪里获取？
2. **复权因子对 pre_close 的影响**：使用前复权还是后复权？
3. **北交所新股首日规则**：是否需要特殊处理？
4. **历史 ST 状态**：如何获取历史 ST 状态变化？

---

## 11. 变更记录

| 版本 | 日期 | 作者 | 说明 |
|------|------|------|------|
| 1.0 | 2026-09-18 | UP | 初始版本，定义字段语义和计算规则 |

## 2026-10-02 交易状态与历史维护补充

生产 SQLite 的交易状态规则以 `sqlite_daily_derived.classify_trading` 为准：明确未知、未确认或停牌视作无交易，非法 OHLC 在逐股保留诊断、统计上排除；单股无有效派生判断或收盘价不阻断整日汇总。连板遇到这些日期冻结，MA20 只使用有效交易收盘。涨跌停参考缺失的有效交易仍保留其他已知指标。

历史规则已排除上市首日的股票采用当时常规限幅，不能因缺少实际上市日期就将已观察多个交易日的股票永久记未知。注册制前首日未能可靠排除仍未知；不套用注册制后的五日窗口。详见 [全历史修复依据与入口](../topic/st-default-and-historical-limits.md)。
