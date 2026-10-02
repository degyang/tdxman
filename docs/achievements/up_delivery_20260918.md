# UP 交付文档 v1.0

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


**日期**：2026-09-18
**任务**：日线衍生契约确认 + 实时统计核查
**状态**：已完成

---

## 目录

1. [任务概述](#1-任务概述)
2. [日线衍生字段契约](#2-日线衍生字段契约)
3. [实时统计映射核查](#3-实时统计映射核查)
4. [交付清单](#4-交付清单)
5. [关键发现与建议](#5-关键发现与建议)
6. [分工边界确认](#6-分工边界确认)
7. [下一步计划](#7-下一步计划)

---

## 1. 任务概述

### 1.1 任务目标

**任务1：确认日线衍生契约**
- 明确涨跌停价格依据
- 已知/未知/无限价分类
- 收盘封板与日内触及定义
- 连板断档定义
- 交付字段合同与可核对样例
- 实现公开读取接口（设计）

**任务2：核查实时统计**
- 统一 market-stat 与 Python SDK 的映射
- 缺值处理
- 时间字段
- 实时接入优先级靠后，不阻塞历史数据

### 1.2 分工原则

| 角色 | 职责 |
|------|------|
| UP | 提供市场事实 |
| Fundwise | 计算评分和阶段 |
| CC | 展示结果 |

**约束**：不要让 UP 实现策略评分。

---

## 2. 日线衍生字段契约

### 2.1 数据源现状

#### MAC K线 (`MacBar`) 原始字段

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

#### 行情快照 (`SecurityQuote`)

| 字段 | 当前状态 | 说明 |
|------|----------|------|
| limit_up | 硬编码 None | 需要业务规则计算 |
| limit_down | 硬编码 None | 需要业务规则计算 |

#### 市场统计 (`MarketStat`)

| 字段 | 来源 | 说明 |
|------|------|------|
| limit_up_count | 880006.close | 市场涨停家数 |
| limit_down_count | 880006.open | 市场跌停家数 |

### 2.2 涨跌幅限制规则（A股）

#### 主板/中小板（60xxxx, 00xxxx）

- **普通股票**：±10%
- **ST/*ST 股票**：±5%
- **新股上市首日**：±44%（2014年后）/ 无限制（历史时期不同）

#### 创业板（30xxxx）

- **2020年8月24日后**：±20%（注册制）
- **之前**：±10%
- **ST 股票**：±20%（注册制后）/ ±5%（之前）
- **新股上市前5日**：不设涨跌幅限制

#### 科创板（688xxx）

- **所有股票**：±20%
- **新股上市前5日**：不设涨跌幅限制

#### 北交所（8xxxxx, 4xxxxx, 920xxx）

- **所有股票**：±30%
- **新股上市首日**：不设涨跌幅限制

#### 计算公式

```
涨停价 = round(pre_close * (1 + limit_pct), 2)
跌停价 = round(pre_close * (1 - limit_pct), 2)
```

其中 `round` 为四舍五入到分（0.01元）。

### 2.3 字段定义

#### 价格限制类

| 字段名 | 类型 | 单位 | 定义 | 计算依据 |
|--------|------|------|------|----------|
| pre_close | DOUBLE | CNY/share | 前收盘价 | 需要补充数据源 |
| limit_up | DOUBLE | CNY/share | 涨停价 | pre_close * (1 + limit_pct) |
| limit_down | DOUBLE | CNY/share | 跌停价 | pre_close * (1 - limit_pct) |
| limit_pct | DOUBLE | percent | 涨跌幅限制 | 根据板块和ST状态确定 |
| price_limit_status | VARCHAR | - | 价格限制状态 | KNOWN/UNKNOWN/NO_LIMIT |

#### price_limit_status 取值

| 值 | 含义 | 判定条件 |
|----|------|----------|
| `KNOWN` | 已知限价 | 有 pre_close 且可计算涨跌停价 |
| `UNKNOWN` | 未知限价 | 缺少 pre_close 或 ST 状态不明确 |
| `NO_LIMIT` | 无限价 | 新股上市首日/前5日（根据板块规则） |

#### 涨跌停判定类

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

#### 连板统计类

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

### 2.4 字段合同（DAILY_FIELDS 扩展）

```python
# api_contract.py 扩展

LIMIT_FIELDS = {
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

### 2.5 覆盖状态

| 字段组 | 状态 | 说明 |
|--------|------|------|
| LIMIT_FIELDS | ❌ 未实现 | 需要补充 pre_close 数据源 |
| LIMIT_STATUS_FIELDS | ❌ 未实现 | 依赖 LIMIT_FIELDS |
| CONSECUTIVE_FIELDS | ❌ 未实现 | 依赖 LIMIT_STATUS_FIELDS |

### 2.6 可核对样例

#### 样例1：贵州茅台 - 主板涨停

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

#### 样例2：ST股票 - 主板涨停

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

#### 样例3：新股上市首日 - 无涨跌幅限制

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

#### 样例4：连板断档

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

## 3. 实时统计映射核查

### 3.1 数据源映射

#### MAC 协议特殊代码

市场统计通过查询三个特殊代码获取：

| 代码 | 含义 | 查询字段 |
|------|------|----------|
| 880005 | 市场涨跌统计 | close, open, low, high, amount, vol |
| 880001 | 市场总市值 | close |
| 880006 | 涨跌停家数 | close, open |

#### 字段映射表

| MarketStat 字段 | 来源代码 | 来源字段 | 转换逻辑 |
|----------------|----------|----------|----------|
| up_count | 880005 | close | int(value) |
| down_count | 880005 | open | int(value) |
| neutral_count | 880005 | low | int(value) |
| total_count | 880005 | high | int(value) |
| suspended_count | - | - | max(0, total - up - down - neutral) |
| total_amount | 880005 | amount | float(value) |
| total_volume | 880005 | vol | float(value) |
| total_market_cap | 880001 | close | float(value) * 1e10 |
| limit_up_count | 880006 | close | int(value) |
| limit_down_count | 880006 | open | int(value) |

#### 代码语义验证

**880005 字段语义**（基于测试用例反推）：
- `close` → 上涨家数（up_count）
- `open` → 下跌家数（down_count）
- `low` → 平盘家数（neutral_count）
- `high` → 总家数（total_count）
- `amount` → 总成交额（total_amount）
- `vol` → 总成交量（total_volume）

**880006 字段语义**：
- `close` → 涨停家数（limit_up_count）
- `open` → 跌停家数（limit_down_count）

**注意**：880006 的字段映射看起来不太直观（close=涨停, open=跌停），这可能是历史遗留设计。

### 3.2 缺值处理

#### 当前实现

```python
# _market_stat_from_quotes() 中的处理

# 1. 空数据检查
if quotes.empty or not {"market", "code"}.issubset(quotes.columns):
    raise click.ClickException("无法获取市场统计数据：MAC 服务器返回空数据或缺少代码字段")

# 2. 记录数检查
for code, fields in required.items():
    rows = quotes[(quotes["market"] == Market.SH) & (quotes["code"] == code)]
    if len(rows) != 1:
        raise click.ClickException(f"市场统计数据不完整：{code} 应返回一条记录")

# 3. 字段存在性检查
for field in fields:
    try:
        value = float(rows.iloc[0][field])
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(f"市场统计字段缺失或无效：{code}.{field}") from exc

# 4. 数值有效性检查
if not 0 <= value < float("inf"):
    raise click.ClickException(f"市场统计字段无效：{code}.{field}")

# 5. 停牌家数计算（允许为0或负数）
suspended_count = max(0, total - up - down - neutral)
```

#### 缺值场景分析

| 场景 | 当前行为 | 建议行为 |
|------|----------|----------|
| MAC 服务器返回空 | 抛出 ClickException | ✅ 正确 |
| 缺少 880005/880001/880006 | 抛出 ClickException | ✅ 正确 |
| 字段为 NaN | 抛出 ClickException | ✅ 正确 |
| 字段为负数 | 抛出 ClickException | ✅ 正确 |
| 字段为无穷大 | 抛出 ClickException | ✅ 正确 |
| 停牌家数为负 | 设为 0 | ✅ 正确（数据不一致时的容错） |

### 3.3 时间字段

#### 改进前

`MarketStat` 模型没有时间字段：

```python
@dataclass
class MarketStat:
    up_count: int
    down_count: int
    neutral_count: int
    suspended_count: int
    total_count: int
    total_amount: float
    total_volume: float
    total_market_cap: float
    limit_up_count: int
    limit_down_count: int
```

#### 改进后

添加 `query_time` 字段：

```python
@dataclass
class MarketStat:
    up_count: int
    down_count: int
    neutral_count: int
    suspended_count: int
    total_count: int
    total_amount: float
    total_volume: float
    total_market_cap: float
    limit_up_count: int
    limit_down_count: int
    query_time: datetime | None = None  # 查询时间
```

### 3.4 Python SDK 映射

#### MacClient.get_stock_quotes()

```python
def get_stock_quotes(
    self,
    stocks: list[tuple[int, str]],
    fields: object = None,
) -> pd.DataFrame:
    """批量获取自定义字段报价（最多80只/次）。"""
```

**参数**：
- `stocks`: [(market, code), ...] 列表
- `fields`: 字段选择，默认 PresetField.COMMON

**返回**：DataFrame，包含 market, code, name 和请求的字段

#### market-stat 调用方式

```python
# 当前实现
with get_mac_client() as client:
    quotes = client.get_stock_quotes(
        [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
    )
```

#### 字段请求

当前使用默认字段（PresetField.COMMON），包含：
- 基础字段：code, name, pre_close, open, high, low, close
- 量额字段：vol, amount, turnover, vol_ratio
- 股本字段：total_shares, float_shares
- 财务字段：eps, net_assets, pe_dynamic, pe_ttm, pe_static
- 其他：security_type_price, total_market_cap_ab, lot_size_info, dividend_yield 等

**结论**：PresetField.COMMON 包含 VOL 和 AMOUNT，满足 880005 的需求。

### 3.5 与 Python SDK 的一致性

| 功能 | CLI (market-stat) | SDK (MacClient) |
|------|-------------------|-----------------|
| 数据源 | get_stock_quotes([(SH, "880005"), (SH, "880001"), (SH, "880006")]) | 同左 |
| 字段选择 | 默认 COMMON | 默认 COMMON |
| 返回格式 | DataFrame → MarketStat → JSON/Table/CSV | DataFrame |

**结论**：CLI 和 SDK 使用相同的底层调用，一致性良好。

### 3.6 测试覆盖

测试文件 `test_cli_market_stat.py` 覆盖了：
- 正常映射（test_market_stat_mac_mapping）
- 多种输出格式（test_market_stat_formats）
- 缺值处理（test_market_stat_incomplete_data）
- 连接错误（test_market_stat_connection_error）

**覆盖率**：✅ 完整

---

## 4. 交付清单

### 4.1 文档交付

| 文件 | 说明 | 状态 |
|------|------|------|
| `docs/up_delivery_20260918.md` | 本文档 | ✅ 完成 |
| `docs/daily_derived_contract.md` | 日线衍生字段契约详细文档 | ✅ 完成 |
| `docs/market_stat_mapping.md` | 实时统计映射核查文档 | ✅ 完成 |

### 4.2 代码交付

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/aspool/api_contract.py` | 扩展字段定义（LIMIT_FIELDS, LIMIT_STATUS_FIELDS, CONSECUTIVE_FIELDS） | ✅ 完成 |
| `src/tdxman/models/stats.py` | 添加 query_time 字段 | ✅ 完成 |
| `src/tdxman/cli/cmd_monitor.py` | 填充 query_time 字段 | ✅ 完成 |

### 4.3 测试交付

| 文件 | 说明 | 状态 |
|------|------|------|
| `tests/fixtures/daily_derived_samples.json` | 可核对样例数据（9个单日 + 1个连板序列） | ✅ 完成 |
| `tests/unit/test_daily_derived.py` | 日线衍生字段测试套件（21个用例） | ✅ 完成 |
| `tests/unit/test_cli_market_stat.py` | 更新测试以处理 query_time 字段 | ✅ 完成 |

### 4.4 测试验证结果

```
tests/unit/test_daily_derived.py: 21 passed
tests/unit/test_cli_market_stat.py: 9 passed
tests/unit/test_protocol_fixes.py: 9 passed
tests/unit/test_aspool.py: 5 passed
```

---

## 5. 关键发现与建议

### 5.1 日线衍生字段

**发现**：
- tdxman 已有 `src/tdxman/codec/price_rules.py` 实现了完整的涨跌停价格计算
- 包含新股上市窗口期、指数类代码判断等逻辑
- 测试已与现有实现对齐

**建议**：
- Phase 1：使用已有的 `compute_price_limits` 函数
- Phase 2：补充 `pre_close` 数据源（从历史日线推算）
- Phase 3：补充 `is_st` 状态（从股票名称判断）

### 5.2 实时统计

**发现**：
- 880006 的字段映射不太直观（close=涨停家数, open=跌停家数）
- 缺值处理已完善（空数据、字段缺失、数值无效等）
- 已添加 `query_time` 字段

**建议**：
- 添加数据验证日志（当 total < up + down + neutral 时）
- 考虑添加本地缓存（如需要，5分钟 TTL）

### 5.3 分工边界

**确认**：
- UP：提供市场事实 ✅
- Fundwise：基于衍生字段计算评分和阶段（待实现）
- CC：展示结果（待实现）

**约束**：UP 不实现策略评分，只提供计算所需的字段和规则。

---

## 6. 分工边界确认

### 6.1 UP 职责边界

**已完成**：
- 定义涨跌停价格计算规则
- 定义价格限制状态分类
- 定义涨跌停判定逻辑
- 定义连板统计逻辑
- 提供可核对样例
- 实现公开读取接口设计

**不涉及**：
- 策略评分计算（Fundwise 职责）
- 阶段判定（Fundwise 职责）
- 结果展示（CC 职责）

### 6.2 数据流向

```
UP (市场事实) → Fundwise (评分/阶段) → CC (展示)
     ↓
  字段定义
  计算规则
  可核对样例
```

### 6.3 接口契约

**UP 提供给 Fundwise**：
- `DAILY_DERIVED_FIELDS`：字段定义
- `compute_price_limits()`：价格计算函数
- `compute_limit_status()`：状态判定函数
- `compute_consecutive_limits()`：连板统计函数

**Fundwise 需要实现**：
- 读取日线数据（含衍生字段）
- 计算评分和阶段
- 输出结果供 CC 展示

---

## 7. 下一步计划

### 7.1 短期（1-2周）

1. **补充 pre_close 数据源**
   - 方案A：从历史日线推算（推荐）
   - 方案C：从 free-stockdb 导入历史数据

2. **补充 is_st 状态**
   - 从股票名称判断（包含 "ST" 字样）
   - 从基本面数据获取

3. **实现公开读取接口**
   - `get_daily_with_limits()` 函数
   - 返回含衍生字段的 DataFrame

### 7.2 中期（2-4周）

1. **Fundwise 集成**
   - 使用衍生字段计算评分
   - 实现阶段判定逻辑

2. **CC 集成**
   - 展示涨跌停状态
   - 展示连板统计

### 7.3 长期（1-2月）

1. **实时数据接入**
   - 考虑添加本地缓存
   - 实现实时涨跌停状态更新

2. **历史数据补充**
   - 从 free-stockdb 导入完整历史
   - 补充历史 ST 状态变化

---

## 附录 A：文件清单

### 新增文件

```
docs/up_delivery_20260918.md          # 本文档
docs/daily_derived_contract.md        # 日线衍生字段契约
docs/market_stat_mapping.md           # 实时统计映射核查
tests/fixtures/daily_derived_samples.json  # 可核对样例
tests/unit/test_daily_derived.py      # 测试套件
```

### 修改文件

```
src/aspool/api_contract.py            # 扩展字段定义
src/tdxman/models/stats.py            # 添加 query_time 字段
src/tdxman/cli/cmd_monitor.py         # 填充 query_time 字段
tests/unit/test_cli_market_stat.py    # 更新测试
```

---

## 附录 B：测试用例清单

### test_daily_derived.py

| 测试类 | 测试方法 | 说明 |
|--------|----------|------|
| TestComputePriceLimits | test_main_board_normal | 主板普通股票 ±10% |
| TestComputePriceLimits | test_main_board_st | 主板ST股票 ±5% |
| TestComputePriceLimits | test_gem_board | 创业板 ±20% |
| TestComputePriceLimits | test_star_board | 科创板 ±20% |
| TestComputePriceLimits | test_bj_board | 北交所 ±30% |
| TestComputePriceLimits | test_new_stock_no_limit | 新股上市首日无涨跌幅限制 |
| TestComputePriceLimits | test_new_stock_day_5_no_limit | 新股上市第5日仍无涨跌幅限制 |
| TestComputePriceLimits | test_new_stock_day_6_has_limit | 新股上市第6日开始有涨跌幅限制 |
| TestComputePriceLimits | test_missing_pre_close | 缺少前收盘价时状态为 UNKNOWN |
| TestComputeLimitStatus | test_limit_up | 收盘涨停 |
| TestComputeLimitStatus | test_limit_down | 收盘跌停 |
| TestComputeLimitStatus | test_one_word_limit_up | 一字涨停板 |
| TestComputeLimitStatus | test_no_limit_status | 无涨跌幅限制时所有状态为 None |
| TestComputeLimitStatus | test_touched_but_not_sealed | 日内触及涨停但未封板 |
| TestComputeConsecutiveLimits | test_consecutive_limit_up | 连续涨停 |
| TestComputeConsecutiveLimits | test_board_break | 连板断档 |
| TestComputeConsecutiveLimits | test_new_start_after_break | 断档后重新开始计数 |
| TestComputeConsecutiveLimits | test_no_limit_up | 非涨停日 |
| TestSampleData | test_samples_valid | 验证样例数据文件格式正确 |
| TestSampleData | test_single_day_samples | 测试单日样例数据的计算结果 |
| TestSampleData | test_sequence_samples | 测试连板序列样例数据的计算结果 |

### test_cli_market_stat.py

| 测试方法 | 说明 |
|----------|------|
| test_market_stat_mac_mapping | 正常映射验证 |
| test_market_stat_formats | 多种输出格式 |
| test_market_stat_incomplete_data | 缺值处理（5种场景） |
| test_market_stat_connection_error | 连接错误处理 |

---

## 附录 C：版本历史

| 版本 | 日期 | 作者 | 说明 |
|------|------|------|------|
| 1.0 | 2026-09-18 | UP | 初始版本，完成日线衍生契约确认和实时统计核查 |