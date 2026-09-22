# UP 补正交付 v2.1

**日期**：2026-09-18
**版本**：v2.1（基于v2复审补正）
**状态**：已由最新补正复审替代（见 v2.1-done 和 final-done）
**注意**：本文档部分内容已过时，以最新完成记录为准

---

## 1. v2复审问题执行记录

### 1.1 GEM+ST反例修正 ✅

**问题**：GEM+ST返回limit_pct=0.05（ST优先匹配），但注册制后应为0.20。

**修正**：`price_rules.py` 调整规则顺序，板块判断优先于ST判断。

```python
# price_rules.py 修正后
# 1. 科创板/创业板: 注册制板块, ST也是20%
if code.startswith("688") or code.startswith("300") or code.startswith("301"):
    limit_pct = 0.20
# 2. 北交所: 30%
elif code.startswith(("43", "83", "87", "92")):
    limit_pct = 0.30
# 3. 主板 ST: 5%
elif "ST" in upper_name:
    limit_pct = 0.05
# 4. 主板普通: 10%
else:
    limit_pct = 0.10
```

**规则依据**：
- 创业板/科创板：2020-08-24后注册制，ST也是±20%
- 主板：ST是±5%
- 北交所：ST也是±30%

**测试验证**：
```python
def test_gem_st_board(self):
    """创业板ST股票 ±20%（注册制后ST也是20%）。"""
    result = compute_price_limits_wrapper(10.00, "GEM", True, "ST某某")
    self.assertEqual(result["price_limit_status"], "KNOWN")
    self.assertAlmostEqual(result["limit_pct"], 0.20, places=2)
    self.assertEqual(result["limit_up"], 12.00)
    self.assertEqual(result["limit_down"], 8.00)
```

### 1.2 连板会话轴与截断历史 ✅

**问题**：
- 周末样例需改为正确会话
- 截断历史不能当已知首板

**修正**：
- 样例使用正确交易日序列（不含周末）
- 首条记录无前史时 consecutive=null

```python
def compute_consecutive_limits(daily_bars, current_index):
    # ...
    # 首条记录（无前史）→ 不能确认首板
    if current_index == 0:
        return {"consecutive_limit_up": None, "board_break": False}
    # ...
```

**新增测试**：
- `test_first_bar_no_prev_is_null`：首条涨停 → consecutive=null
- `test_session_continuity`：周末不中断连续涨停
- `test_prev_known_not_limit_up`：前日已知非涨停 → consecutive=1

### 1.3 CLI时区修正 ✅

**问题**：`cmd_monitor.py` 仍为 `datetime.now()` 无时区。

**修正**：统一使用 `datetime.now(_SHANGHAI_TZ)`。

```python
# cmd_monitor.py 修正
from zoneinfo import ZoneInfo
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

query_time=datetime.now(_SHANGHAI_TZ),  # 带时区
```

### 1.4 high超限价隔离 ✅

**问题**：样例中 high>limit_up 未明确隔离。

**修正**：样例新增 `data_quality: "INVALID"` 和 `invalid_reason` 字段。

```json
{
  "name": "数据异常：high超涨停价",
  "data_quality": "INVALID",
  "invalid_reason": "high(1150) > limit_up(1100)，A股交易所限制下不应出现",
  "input": {
    "high": 1150.00,
    "limit_up": 1100.00
  },
  "expected": {
    "_note": "此样例用于测试边界条件，实际数据中high>limit_up表示数据异常"
  }
}
```

**公开读取合同**：异常数据应隔离，不参与正常统计。

```json
{
  "_invalid": {
    "reason": "high(1150) > limit_up(1100)",
    "severity": "data_quality",
    "action": "excluded_from_statistics"
  }
}
```

### 1.5 公开读取合同冻结 ✅

**新增文档**：`docs/public_read_contract.md`

**内容**：
- 方法签名：`get_daily_with_limits(root, market, symbol, start_date, end_date)`
- describe能力标识：字段、scope、coverage、completion、quality
- 类型/单位/缺失：完整字段定义
- 来源质量/覆盖/完成度：数据源说明
- 样例响应：正常、无限价、异常隔离
- 错误码：POOL_NOT_FOUND, DAILY_INVALID 等
- 规则依据：涨跌幅限制规则、计算规则、三值语义

---

## 2. 测试验证结果

### 2.1 测试命令

```bash
# 测试命令
python -m pytest tests/unit/test_daily_derived.py -v
python -m pytest tests/unit/test_cli_market_stat.py -v
python -m pytest tests/unit/test_protocol_fixes.py -v

# 版本信息
HEAD: cb320226 (dirty, 有未提交改动)
```

### 2.2 测试结果

```
tests/unit/test_daily_derived.py: 33 passed, 12 subtests passed
tests/unit/test_cli_market_stat.py: 9 passed
tests/unit/test_protocol_fixes.py: 9 passed
```

### 2.3 反例验证结果

| 反例 | 期望 | 实际 | 状态 |
|------|------|------|------|
| GEM + ST，pre_close=10 | limit_pct=0.20, 涨停12.0 | limit_pct=0.20, 涨停12.0 | ✅ |
| 9月11日与15日均涨停，中间会话缺行 | consecutive=3 | consecutive=3 | ✅ |
| 输入仅首条涨停，无前史 | consecutive=null | consecutive=null | ✅ |
| 显式前日null、今日涨停 | consecutive=null | consecutive=null | ✅ |

---

## 3. 已实现/提案/未验证 分类

### 已实现（可调用）
- ✅ DAILY_DERIVED_FIELDS 字段定义
- ✅ compute_price_limits() 价格计算（price_rules.py，已修正GEM+ST）
- ✅ MarketStat.query_time 字段（CLI/SDK统一）
- ✅ TdxClient/AsyncTdxClient.get_market_stat() 统一行为
- ✅ 公开读取合同（docs/public_read_contract.md）

### 提案（未实现）
- ❌ get_daily_with_limits() 生产实现
- ❌ 日线数据自动填充衍生字段
- ❌ 历史数据回填

### 契约实验（测试内示范）
- ⚠️ compute_limit_status()：测试文件内函数
- ⚠️ compute_consecutive_limits()：测试文件内函数
- ⚠️ 生产实现必须由测试调用生产函数和公开接口

---

## 4. 文档清单

| 文档 | 说明 | 状态 |
|------|------|------|
| docs/up_delivery_20260918_v2.1.md | 本文档 | ✅ |
| docs/public_read_contract.md | 公开读取合同 | ✅ 冻结 |
| docs/up_delivery_20260918_v2.md | v2交付文档 | 保留 |
| docs/daily_derived_contract.md | 字段合同详细文档 | 需同步 |
| docs/market_stat_mapping.md | SDK映射核查文档 | 需同步 |

---

## 5. 代码变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| src/tdxman/codec/price_rules.py | 修正规则顺序 | GEM+ST=20% |
| src/tdxman/cli/cmd_monitor.py | 添加时区 | query_time带时区 |
| src/tdxman/client.py | 统一SDK行为 | 按代码查找、校验、带时区 |
| src/aspool/api_contract.py | 更新字段定义 | ratio、一字板字段 |
| tests/unit/test_daily_derived.py | 新增测试 | GEM+ST、连板会话轴 |
| tests/fixtures/daily_derived_samples.json | 更新样例 | v2.1，含异常隔离 |

---

## 6. 未覆盖项

| 场景 | 原因 | 后续计划 |
|------|------|----------|
| 历史ST状态变化 | 无数据源 | 不在本轮 |
| 复权后pre_close | 需特殊处理 | 不在本轮 |
| 联网行情请求 | 不阻塞历史路径 | 不在本轮 |
| 全市场回填 | 需大量计算 | 不在本轮 |
| 生产实现get_daily_with_limits | 需新建 | 下一包 |

---

## 7. 范围确认

**本轮完成**：
- ✅ v2复审5项指令全部执行
- ✅ GEM+ST反例修正
- ✅ 连板会话轴与截断历史
- ✅ CLI/SDK时区统一
- ✅ high超限价隔离
- ✅ 公开读取合同冻结

**不涉及**：
- ❌ 批量同步/回填
- ❌ 覆盖历史数据
- ❌ 联网行情请求
- ❌ 策略评分

---

## 8. 下一步

1. **PM冻结契约**：本文档提交后等待PM审核
2. **下一包**：最小公开读取实现（get_daily_with_limits）
3. **实时统一**：不阻塞历史路径