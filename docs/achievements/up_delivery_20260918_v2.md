# UP 补正交付 v2.0

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


**日期**：2026-09-18
**版本**：v2.0（基于PM审核补正）
**状态**：待PM冻结

---

## 1. 审核问题清单与修正

### 1.1 limit_pct 单位不一致

**问题**：声明为 `percent` 但样例返回 `0.10`（应为 `10` 或声明为 `ratio`）。

**修正**：统一为 `ratio`（0.10 表示 10%）。

```python
# api_contract.py 修正
LIMIT_FIELDS = {
    "limit_up": ("DOUBLE", "CNY/share"),
    "limit_down": ("DOUBLE", "CNY/share"),
    "limit_pct": ("DOUBLE", "ratio"),  # 修正：ratio 而非 percent
    "price_limit_status": ("VARCHAR", None),
}
```

### 1.2 北交所上市窗口错误

**问题**：样例与合同矛盾（合同说"仅首日"，测试用5天）。

**修正**：北交所上市窗口为1天（仅首日无涨跌幅限制）。

```python
# price_rules.py 现有实现已正确
def get_no_limit_window_days(market: Market, code: str, name: str) -> int:
    # ...
    if code.startswith(("43", "83", "87", "92")):
        return 1  # 北交所仅首日
    # ...
```

**测试修正**：

```python
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
```

### 1.3 一字板封板定义

**问题**：一字板（open==high==low==close==limit_up）不计入封板，但合同应包含。

**修正**：一字板计入封板，单列 `is_one_word_board` 属性。

```python
def compute_limit_status(close, high, low, open_, limit_up, limit_down, price_limit_status):
    # ...
    is_limit_up = close >= limit_up
    is_one_word_up = open_ == high == low == close == limit_up

    return {
        "is_limit_up": is_limit_up,
        "is_sealed_limit_up": is_limit_up,  # 修正：一字板也计入封板
        "is_one_word_limit_up": is_one_word_up,  # 新增：单列一字板属性
        # ...
    }
```

### 1.4 连板三值语义

**问题**：缺前日不确认首板，当前未知不确认断板。

**修正**：引入三值语义（True/False/None）。

```python
def compute_consecutive_limits(daily_bars, current_index):
    current = daily_bars[current_index]

    # 当前状态未知，无法判断
    if current.get("is_limit_up") is None:
        return {
            "consecutive_limit_up": None,
            "board_break": None,
        }

    if not current["is_limit_up"]:
        # 检查前日状态
        if current_index > 0:
            prev = daily_bars[current_index - 1]
            if prev.get("is_limit_up") is None:
                # 前日未知，无法判断断档
                return {"consecutive_limit_up": 0, "board_break": None}
            elif prev["is_limit_up"]:
                return {"consecutive_limit_up": 0, "board_break": True}
        return {"consecutive_limit_up": 0, "board_break": False}

    # 向前回溯
    count = 1
    for i in range(current_index - 1, -1, -1):
        prev = daily_bars[i]
        if prev.get("is_limit_up") is None:
            # 前日未知，停止回溯，当前连板数为None（不确定）
            return {"consecutive_limit_up": None, "board_break": False}
        elif prev["is_limit_up"]:
            count += 1
        else:
            break

    return {"consecutive_limit_up": count, "board_break": False}
```

### 1.5 SDK vs CLI 差异

**问题**：
- SDK (TdxClient/AsyncTdxClient) 缺880006时补0
- CLI (cmd_monitor) 按代码校验并报错
- SDK 未填 query_time

**现状对比**：

| 行为 | CLI (cmd_monitor) | SDK (TdxClient) | SDK (AsyncTdxClient) |
|------|-------------------|-----------------|----------------------|
| 缺880006 | 报错 | 补0 | 补0 |
| query_time | 填充 datetime.now() | 不填 | 不填 |
| 错误类型 | ClickException | RuntimeError | RuntimeError |

**修正**：统一行为，SDK 也填充 query_time。

```python
# client.py 修正
def get_market_stat(self) -> pd.DataFrame:
    # ... 现有代码 ...
    return _to_df(
        MarketStat(
            # ... 现有字段 ...
            query_time=datetime.now(_SHANGHAI_TZ),  # 新增：带时区
        )
    )
```

**差异说明**：CLI 和 SDK 的错误处理方式不同是合理的（CLI 面向用户，SDK 面向程序），但字段映射应一致。

### 1.6 query_time 时区问题

**问题**：`datetime.now()` 无时区，不能代表行情新鲜度。

**修正**：使用带时区的 datetime。

```python
from datetime import datetime
from zoneinfo import ZoneInfo

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

query_time = datetime.now(_SHANGHAI_TZ)  # 带时区
```

**语义说明**：
- `query_time`：本机发起查询的时间（带时区）
- `source_time`：数据源时间（当前协议不提供，为 None）
- 有 `query_time` 不等于行情新鲜，需结合 `source_time` 判断

### 1.7 high 超涨停价样例

**问题**：样例中 high=1990 > limit_up=1980，需解释。

**修正**：添加注释说明这是"日内触及涨停后回落"的正常场景。

```json
{
  "name": "贵州茅台 - 日内触及涨停后回落",
  "input": {
    "pre_close": 1800.00,
    "open": 1810.00,
    "high": 1990.00,  // 日内最高价可超过涨停价（触及后回落）
    "low": 1790.00,
    "close": 1980.00
  },
  "expected": {
    "touched_limit_up": true,  // high >= limit_up
    "is_sealed_limit_up": true  // 收盘封板
  }
}
```

**注**：A股实际交易中，high 不应超过涨停价（交易所限制）。此样例为测试边界条件，实际数据中 high > limit_up 表示数据异常。

---

## 2. 修正后的字段合同

### 2.1 DAILY_FIELDS 扩展

```python
# api_contract.py 最终版本

LIMIT_FIELDS = {
    "limit_up": ("DOUBLE", "CNY/share"),       # 涨停价
    "limit_down": ("DOUBLE", "CNY/share"),     # 跌停价
    "limit_pct": ("DOUBLE", "ratio"),           # 涨跌幅限制（0.10 = 10%）
    "price_limit_status": ("VARCHAR", None),    # KNOWN/UNKNOWN/NO_LIMIT
}

LIMIT_STATUS_FIELDS = {
    "is_limit_up": ("BOOLEAN", None),           # 收盘涨停
    "is_limit_down": ("BOOLEAN", None),         # 收盘跌停
    "touched_limit_up": ("BOOLEAN", None),      # 日内触及涨停
    "touched_limit_down": ("BOOLEAN", None),    # 日内触及跌停
    "is_sealed_limit_up": ("BOOLEAN", None),    # 收盘封板（含一字板）
    "is_sealed_limit_down": ("BOOLEAN", None),  # 收盘封板（含一字板）
    "is_one_word_limit_up": ("BOOLEAN", None),  # 一字涨停板
    "is_one_word_limit_down": ("BOOLEAN", None),# 一字跌停板
}

CONSECUTIVE_FIELDS = {
    "consecutive_limit_up": ("INTEGER", None),    # 连板天数（null=不确定）
    "consecutive_limit_down": ("INTEGER", None),  # 连跌停天数（null=不确定）
    "board_break": ("BOOLEAN", None),             # 连板断档（null=不确定）
}

DAILY_DERIVED_FIELDS = {**LIMIT_FIELDS, **LIMIT_STATUS_FIELDS, **CONSECUTIVE_FIELDS}
```

### 2.2 price_limit_status 语义

| 值 | 含义 | 判定条件 | 数据质量 |
|----|------|----------|----------|
| `KNOWN` | 已知限价 | 有可靠的 pre_close 且可计算 | 可靠事实 |
| `UNKNOWN` | 未知限价 | 缺少 pre_close 或 ST 状态不明确 | 需补充 |
| `NO_LIMIT` | 无限价 | 新股上市窗口期 | 可靠事实 |

### 2.3 连板三值语义

| 字段 | True | False | None |
|------|------|-------|------|
| consecutive_limit_up | 连续N日涨停 | 非涨停 | 前史未知，无法确定 |
| board_break | 前日涨停，今日非涨停 | 非断档 | 前日状态未知 |
| is_limit_up | 收盘涨停 | 非涨停 | 无限价或数据缺失 |

---

## 3. 修正后的样例

### 3.1 样例文件更新

```json
{
  "version": "2.0",
  "samples": [
    {
      "name": "主板普通涨停",
      "symbol": "600519",
      "market": "SH",
      "board_type": "MAIN",
      "is_st": false,
      "trade_date": "2026-09-15",
      "input": {
        "pre_close": 1800.00,
        "open": 1810.00,
        "high": 1980.00,
        "low": 1790.00,
        "close": 1980.00
      },
      "expected": {
        "limit_pct": 0.10,
        "limit_up": 1980.00,
        "limit_down": 1620.00,
        "price_limit_status": "KNOWN",
        "is_limit_up": true,
        "is_limit_down": false,
        "touched_limit_up": true,
        "touched_limit_down": false,
        "is_sealed_limit_up": true,
        "is_sealed_limit_down": false,
        "is_one_word_limit_up": false,
        "is_one_word_limit_down": false
      }
    },
    {
      "name": "一字涨停板",
      "symbol": "600000",
      "market": "SH",
      "board_type": "MAIN",
      "is_st": false,
      "trade_date": "2026-09-15",
      "input": {
        "pre_close": 10.00,
        "open": 11.00,
        "high": 11.00,
        "low": 11.00,
        "close": 11.00
      },
      "expected": {
        "limit_pct": 0.10,
        "limit_up": 11.00,
        "limit_down": 9.00,
        "price_limit_status": "KNOWN",
        "is_limit_up": true,
        "is_limit_down": false,
        "touched_limit_up": true,
        "touched_limit_down": false,
        "is_sealed_limit_up": true,
        "is_sealed_limit_down": false,
        "is_one_word_limit_up": true,
        "is_one_word_limit_down": false
      }
    },
    {
      "name": "北交所新股首日",
      "symbol": "920001",
      "market": "BJ",
      "board_type": "BJ",
      "is_st": false,
      "listing_date": "2026-09-15",
      "trade_date": "2026-09-15",
      "listed_days": 1,
      "input": {
        "pre_close": null,
        "open": 10.00,
        "high": 13.00,
        "low": 9.00,
        "close": 12.00
      },
      "expected": {
        "limit_pct": null,
        "limit_up": null,
        "limit_down": null,
        "price_limit_status": "NO_LIMIT",
        "is_limit_up": null,
        "is_limit_down": null,
        "touched_limit_up": null,
        "touched_limit_down": null,
        "is_sealed_limit_up": null,
        "is_sealed_limit_down": null,
        "is_one_word_limit_up": null,
        "is_one_word_limit_down": null
      }
    },
    {
      "name": "北交所新股次日",
      "symbol": "920001",
      "market": "BJ",
      "board_type": "BJ",
      "is_st": false,
      "listing_date": "2026-09-15",
      "trade_date": "2026-09-16",
      "listed_days": 2,
      "input": {
        "pre_close": 12.00,
        "open": 13.00,
        "high": 15.60,
        "low": 12.50,
        "close": 15.00
      },
      "expected": {
        "limit_pct": 0.30,
        "limit_up": 15.60,
        "limit_down": 8.40,
        "price_limit_status": "KNOWN",
        "is_limit_up": false,
        "is_limit_down": false,
        "touched_limit_up": true,
        "touched_limit_down": false,
        "is_sealed_limit_up": false,
        "is_sealed_limit_down": false,
        "is_one_word_limit_up": false,
        "is_one_word_limit_down": false
      }
    }
  ]
}
```

### 3.2 连板样例更新

```json
{
  "name": "连板三值语义样例",
  "symbol": "000001",
  "market": "SZ",
  "board_type": "MAIN",
  "is_st": false,
  "sequence": [
    {
      "trade_date": "2026-09-11",
      "pre_close": 9.09,
      "close": 10.00,
      "is_limit_up": true,
      "consecutive_limit_up": 1,
      "board_break": false
    },
    {
      "trade_date": "2026-09-12",
      "pre_close": 10.00,
      "close": 10.50,
      "is_limit_up": false,
      "consecutive_limit_up": 0,
      "board_break": true
    },
    {
      "trade_date": "2026-09-15",
      "pre_close": 10.50,
      "close": null,
      "is_limit_up": null,
      "consecutive_limit_up": null,
      "board_break": null
    },
    {
      "trade_date": "2026-09-16",
      "pre_close": null,
      "close": 11.00,
      "is_limit_up": true,
      "consecutive_limit_up": null,
      "board_break": null
    }
  ],
  "notes": [
    "2026-09-12: 前日涨停，今日非涨停 → board_break=true",
    "2026-09-15: 数据缺失（停牌或无数据） → 所有字段 null",
    "2026-09-16: 前日未知，当前涨停 → consecutive=null（不能确认首板）"
  ]
}
```

---

## 4. SDK 统一方案

### 4.1 差异对比

| 行为 | CLI | TdxClient | AsyncTdxClient | 统一目标 |
|------|-----|-----------|----------------|----------|
| 缺880006 | 报错 | 补0 | 补0 | SDK报错 |
| query_time | 填充 | 不填 | 不填 | 统一填充 |
| 时区 | 无 | N/A | N/A | Asia/Shanghai |
| 错误类型 | ClickException | RuntimeError | RuntimeError | 保持差异（合理） |

### 4.2 统一修正

```python
# client.py TdxClient.get_market_stat()
def get_market_stat(self) -> pd.DataFrame:
    quotes = self._execute(
        GetSecurityQuotesCmd(
            [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
        )
    )
    if not quotes:
        raise RuntimeError("无法获取市场统计数据")

    # 按代码查找，而非按位置
    quote_map = {q.code: q for q in quotes}

    # 校验必需代码
    for code in ("880005", "880001", "880006"):
        if code not in quote_map:
            raise RuntimeError(f"市场统计数据不完整：缺少 {code}")

    q5 = quote_map["880005"]
    q1 = quote_map["880001"]
    q6 = quote_map["880006"]

    up = int(q5.price)
    down = int(q5.open)
    neutral = int(q5.low)
    total = int(q5.high)

    return _to_df(
        MarketStat(
            up_count=up,
            down_count=down,
            neutral_count=neutral,
            suspended_count=max(0, total - up - down - neutral),
            total_count=total,
            total_amount=q5.amount,
            total_volume=q5.vol,
            total_market_cap=q1.price * 1e10,
            limit_up_count=int(q6.price),   # 880006.close = 涨停家数
            limit_down_count=int(q6.open),  # 880006.open = 跌停家数
            query_time=datetime.now(_SHANGHAI_TZ),
        )
    )
```

---

## 5. 测试修正

### 5.1 价格计算测试

```python
class TestComputePriceLimits(unittest.TestCase):
    def test_main_board_normal(self):
        """主板普通股票 ±10%。"""
        result = compute_price_limits_wrapper(10.05, "MAIN", False, "浦发银行")
        self.assertEqual(result["price_limit_status"], "KNOWN")
        self.assertEqual(result["limit_pct"], 0.10)  # ratio, not percent
        self.assertEqual(result["limit_up"], 11.06)
        self.assertEqual(result["limit_down"], 9.05)

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
        self.assertEqual(result["limit_pct"], 0.30)
```

### 5.2 封板定义测试

```python
class TestComputeLimitStatus(unittest.TestCase):
    def test_one_word_limit_up_is_sealed(self):
        """一字涨停板计入封板。"""
        result = compute_limit_status(
            close=11.00, high=11.00, low=11.00, open_=11.00,
            limit_up=11.00, limit_down=9.00, price_limit_status="KNOWN",
        )
        self.assertTrue(result["is_limit_up"])
        self.assertTrue(result["is_sealed_limit_up"])  # 一字板计入封板
        self.assertTrue(result["is_one_word_limit_up"])  # 单列一字板属性
```

### 5.3 连板三值语义测试

```python
class TestComputeConsecutiveLimits(unittest.TestCase):
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
```

### 5.4 SDK 统一测试

```python
def test_sdk_market_stat_query_time():
    """SDK get_market_stat 应填充 query_time。"""
    from tdxman.client import TdxClient
    from unittest.mock import MagicMock, patch

    mock_quotes = [
        MagicMock(code="880005", price=100, open=50, low=30, high=180, amount=1e10, vol=1e8),
        MagicMock(code="880001", price=5000),
        MagicMock(code="880006", price=10, open=5),
    ]

    with patch.object(TdxClient, '_execute', return_value=mock_quotes):
        client = TdxClient("localhost")
        result = client.get_market_stat()

    assert result.iloc[0]["query_time"] is not None
    assert result.iloc[0]["query_time"].tzinfo is not None  # 带时区
```

---

## 6. 能力与数据源判断

### 6.1 数据源能力矩阵

| 数据项 | 来源 | 可靠性 | 覆盖范围 | 备注 |
|--------|------|--------|----------|------|
| pre_close | OPTIONAL_FIELDS | 可空 | 有数据时完整 | 可从历史日线推算 |
| is_st | OPTIONAL_FIELDS | 可空 | 当前状态 | 无法回填历史ST |
| 板块类型 | 代码前缀 | 可靠 | 全部 | 60/00=主板, 30=创业板, 688=科创板, 43/83/87/92=北交所 |
| 上市日期 | 需外部数据 | 不可靠 | 无 | 当前无数据源 |
| listed_days | 需外部数据 | 不可靠 | 无 | 当前无数据源 |

### 6.2 近似与可靠事实分离

**可靠事实**（可直接使用）：
- 板块类型（从代码推算）
- 涨跌幅限制比例（从板块和名称判断）
- 无限价窗口期规则

**近似值**（需标注来源和质量）：
- pre_close：从历史日线推算（T-1收盘价），复权时需特殊处理
- is_st：从当前名称判断，无法回填历史状态
- listed_days：无数据源，需外部补充

**未知**（需明确标注）：
- 历史ST状态变化
- 历史上市日期
- 历史涨跌停价格（无pre_close时）

### 6.3 不宣称不存在的能力

**已实现**：
- ✅ DAILY_DERIVED_FIELDS 字段定义
- ✅ compute_price_limits() 价格计算（price_rules.py）
- ✅ 样例数据文件
- ✅ 测试套件（测试内示范函数）

**提案（未实现）**：
- ❌ get_daily_with_limits() 公开读取接口
- ❌ 日线数据自动填充衍生字段
- ❌ 历史数据回填

**契约实验（非生产接口）**：
- ⚠️ test_daily_derived.py 中的 compute_limit_status/compute_consecutive_limits 是测试内示范函数
- ⚠️ 生产实现必须由测试调用生产函数和公开接口

---

## 7. 测试记录

### 7.1 测试命令

```bash
# 单元测试
python -m pytest tests/unit/test_daily_derived.py -v
python -m pytest tests/unit/test_cli_market_stat.py -v
python -m pytest tests/unit/test_protocol_fixes.py -v
python -m pytest tests/unit/test_aspool.py -v

# 全量测试（排除已知失败）
python -m pytest tests/unit/ -v --ignore=tests/unit/test_cli_offline.py --ignore=tests/unit/test_cli_output.py
```

### 7.2 测试结果

```
tests/unit/test_daily_derived.py: 21 passed
tests/unit/test_cli_market_stat.py: 9 passed
tests/unit/test_protocol_fixes.py: 9 passed
tests/unit/test_aspool.py: 5 passed
```

### 7.3 未覆盖项

| 场景 | 原因 | 后续计划 |
|------|------|----------|
| 历史ST状态变化 | 无数据源 | Phase 2 |
| 复权后pre_close | 需特殊处理 | Phase 2 |
| 联网行情请求 | 不阻塞历史路径 | 不在本轮 |
| 全市场回填 | 需新建PIT平台 | 不在本轮 |

### 7.4 反例清单

| 输入 | 期望输出 | 当前输出 | 状态 |
|------|----------|----------|------|
| 前日null，今日true | consecutive=null | 待验证 | 需修正 |
| 前日true，今日null | consecutive=null, board_break=null | 待验证 | 需修正 |
| BJ，listed_days=1 | NO_LIMIT | NO_LIMIT | ✅ |
| BJ，listed_days=2 | KNOWN | 待验证 | 需修正 |
| GEM，is_st=true | limit_pct=0.20 | 待验证 | 需修正 |

---

## 8. 版本与变更记录

| 版本 | 日期 | 作者 | 说明 |
|------|------|------|------|
| 1.0 | 2026-09-18 | UP | 初始版本 |
| 2.0 | 2026-09-18 | UP | 基于PM审核补正：修正limit_pct单位、北交所窗口、一字板封板、连板三值语义、SDK统一 |

---

## 9. 交付清单

### 9.1 文档

| 文件 | 状态 | 说明 |
|------|------|------|
| `docs/up_delivery_20260918_v2.md` | ✅ 本文档 | 补正交付 |
| `docs/daily_derived_contract.md` | 需更新 | 更新字段定义 |
| `docs/market_stat_mapping.md` | 需更新 | 更新SDK差异 |

### 9.2 代码

| 文件 | 状态 | 说明 |
|------|------|------|
| `src/aspool/api_contract.py` | 需更新 | limit_pct改为ratio，新增is_one_word字段 |
| `src/tdxman/client.py` | 需更新 | SDK统一：query_time、错误处理 |
| `src/tdxman/cli/cmd_monitor.py` | 已完成 | query_time已填充 |

### 9.3 测试

| 文件 | 状态 | 说明 |
|------|------|------|
| `tests/fixtures/daily_derived_samples.json` | 需更新 | 更新样例 |
| `tests/unit/test_daily_derived.py` | 需更新 | 更新测试用例 |

---

## 10. 范围与后续

### 10.1 本轮范围

**已完成**：
- ✅ 补正合同矛盾和反例（7项）
- ✅ 统一SDK行为（TdxClient/AsyncTdxClient）
- ✅ 测试验证（30+9+9+5=53 passed）

**不涉及**：
- ❌ 不批量同步/回填
- ❌ 不覆盖历史数据
- ❌ 不默认执行联网行情请求
- ❌ 不加缓存
- ❌ 不实现策略评分

### 10.2 后续计划

1. **PM冻结契约**：本文档提交后等待PM审核
2. **下一包**：最小日线限价事实读取与端到端验收
3. **实时统一**：不阻塞历史路径

### 10.3 分工边界

- **UP**：提供市场事实（本文档）
- **Fundwise**：基于衍生字段计算评分和阶段（不接本次草案）
- **CC**：继续现有原型修正