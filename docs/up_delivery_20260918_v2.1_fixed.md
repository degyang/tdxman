# UP v2.1 补正交付

**日期**：2026-09-18
**版本**：v2.1 补正
**状态**：待PM审核（未冻结）

---

## 1. v2.1复审指令执行记录

### 已通过（保留）
- ✅ GEM/ST修正（limit_pct=0.20）
- ✅ 时区补正（CLI/SDK统一使用_SHANGHAI_TZ）

### 阻塞问题修正

#### 1.1 连板逻辑修正

**问题**：
- test_session_continuity日期错误（09-15是周二不是周一）
- 截断历史当已知首板
- 回溯到开头未遇已知非涨停时返回consecutive=2而非null

**修正**：
- 文件：`tests/unit/test_daily_derived.py`
- 改动：
  1. 修正日期注释（09-14周一，09-15周二）
  2. 回溯到输入开头仍未遇到已知非涨停 → consecutive=null
  3. 首条非涨停的board_break → null（无前史依据）

**逻辑变更**：
```python
# 新增：截断边界检查
hit_known_non_limit = False
for i in range(current_index - 1, -1, -1):
    prev = daily_bars[i]
    if prev.get("is_limit_up") is None:
        return {"consecutive_limit_up": None, "board_break": False}
    elif prev["is_limit_up"]:
        count += 1
    else:
        hit_known_non_limit = True
        break

# 回溯到输入开头仍未遇到已知非涨停 → 截断边界
if not hit_known_non_limit:
    return {"consecutive_limit_up": None, "board_break": False}
```

**新增测试**（8个）：
- test_truncated_history_returns_null：截断历史返回null
- test_09_14_missing_row_returns_null：09-14缺行返回null
- test_complete_session_cross_week：完整会话跨周
- test_two_bars_truncated_history：两条以上历史截断
- test_known_start_point：已知起点
- test_prefix_consistency：前缀一致性

#### 1.2 公开读取合同修正

**问题**：
- 默认以昨日close/当前名称产生历史限价
- completion写无依据百分比
- 异常示例只有JSON备注，未定义DataFrame隔离方式

**修正**：
- 文件：`docs/public_read_contract.md`
- 改动：
  1. 状态改为"待PM审核提案（未冻结）"
  2. 删除无依据百分比，改为"待实测"
  3. 添加数据来源优先级（优先读取已有当日字段）
  4. 添加DataFrame样例展示异常隔离方式（`_invalid_reason`列）
  5. 明确近似模式需显式启用

**数据来源优先级**：
1. 优先读取已有当日字段（pre_close、is_st）
2. 缺依据时保持unknown，不默认近似
3. 近似模式需显式启用，单列质量/来源
4. 不把当前名称回填为历史事实

**异常隔离方式**：
```python
# DataFrame 通过 _invalid_reason 列标记
valid_df = df[df["_invalid_reason"].isna()]
limit_up_count = valid_df["is_limit_up"].sum()  # 只统计正常数据
```

#### 1.3 SDK校验说明

**问题**：SDK仍以字典覆盖重复代码，未关闭market/重复/非法数值校验。

**说明**：此实时项可明确延期，不阻塞历史合同，但不得标通过。

**记录**：
- 已测入口：CLI cmd_monitor、TdxClient.get_market_stat、AsyncTdxClient.get_market_stat
- 未测项：market校验、重复代码校验、非法数值校验
- 延期原因：不阻塞历史路径

---

## 2. 测试验证结果

### 2.1 测试命令

```bash
python -m pytest tests/unit/test_daily_derived.py -v
python -m pytest tests/unit/test_cli_market_stat.py -v
python -m pytest tests/unit/test_protocol_fixes.py -v
```

### 2.2 测试结果

```
test_daily_derived.py: 40 passed, 12 subtests passed
test_cli_market_stat.py: 9 passed
test_protocol_fixes.py: 9 passed
```

### 2.3 反例验证结果

| 反例 | 期望 | 实际 | 状态 |
|------|------|------|------|
| GEM + ST，pre_close=10 | limit_pct=0.20 | limit_pct=0.20 | ✅ |
| 09-10已知非涨停、09-11涨停、09-15涨停 | consecutive=3 | consecutive=3 | ✅ |
| 09-11涨停、09-15涨停（缺09-14） | consecutive=null | consecutive=null | ✅ |
| 输入仅首条涨停，无前史 | consecutive=null | consecutive=null | ✅ |
| 首条非涨停，无前史 | board_break=null | board_break=null | ✅ |
| 显式前日null、今日涨停 | consecutive=null | consecutive=null | ✅ |

---

## 3. 已实现/提案/延期 分类

### 已实现（可调用）
- ✅ DAILY_DERIVED_FIELDS 字段定义
- ✅ compute_price_limits() 价格计算（已修正GEM+ST）
- ✅ MarketStat.query_time 字段（CLI/SDK统一）
- ✅ 连板三值语义与截断边界处理

### 提案（待PM审核）
- ⏳ 公开读取合同（docs/public_read_contract.md）
- ⏳ get_daily_with_limits() 生产实现

### 延期（不阻塞历史合同）
- ⏳ SDK market/重复/非法数值校验
- ⏳ 联网行情请求
- ⏳ 全市场回填

### 契约实验（测试内示范）
- ⚠️ compute_limit_status()：测试文件内函数
- ⚠️ compute_consecutive_limits()：测试文件内函数

---

## 4. 文档清单

| 文档 | 说明 | 状态 |
|------|------|------|
| docs/up_delivery_20260918_v2.1.md | 本文档 | ✅ |
| docs/public_read_contract.md | 公开读取合同 | ⏳ 待PM审核 |
| docs/up_delivery_20260918_v2.md | v2交付文档 | 保留 |

---

## 5. 代码变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| src/tdxman/codec/price_rules.py | 修正规则顺序 | GEM+ST=20%（已通过） |
| src/tdxman/cli/cmd_monitor.py | 添加时区 | query_time带时区（已通过） |
| src/tdxman/client.py | 统一SDK行为 | 按代码查找、校验、带时区 |
| tests/unit/test_daily_derived.py | 修正连板逻辑 | 截断边界、日期修正 |
| tests/fixtures/daily_derived_samples.json | 更新样例 | v2.1，含异常隔离 |
| docs/public_read_contract.md | 更新合同 | 待PM审核提案 |

---

## 6. 范围确认

**本轮完成**：
- ✅ GEM/ST修正（已通过）
- ✅ 时区补正（已通过）
- ✅ 连板逻辑修正（截断边界、日期修正）
- ✅ 公开读取合同修正（待PM审核）

**不涉及**：
- ❌ 生产读取实现
- ❌ 批量同步/回填
- ❌ 联网行情请求
- ❌ SDK完整校验（延期）

---

## 7. 下一步

1. **PM审核**：本文档提交后等待PM审核
2. **契约冻结**：PM确认后冻结公开读取合同
3. **下一包**：最小公开读取实现（get_daily_with_limits）