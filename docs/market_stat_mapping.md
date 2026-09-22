# market-stat 与 Python SDK 映射核查

## 1. 背景

`tdxman market-stat` 命令获取 A 股全市场涨跌统计概况。本文档核查其与 Python SDK 的映射关系、缺值处理和时间字段。

---

## 2. 数据源映射

### 2.1 MAC 协议特殊代码

市场统计通过查询三个特殊代码获取：

| 代码 | 含义 | 查询字段 |
|------|------|----------|
| 880005 | 市场涨跌统计 | close, open, low, high, amount, vol |
| 880001 | 市场总市值 | close |
| 880006 | 涨跌停家数 | close, open |

### 2.2 字段映射表

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

### 2.3 代码语义验证

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

---

## 3. 缺值处理

### 3.1 当前实现

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

### 3.2 缺值场景分析

| 场景 | 当前行为 | 建议行为 |
|------|----------|----------|
| MAC 服务器返回空 | 抛出 ClickException | ✅ 正确 |
| 缺少 880005/880001/880006 | 抛出 ClickException | ✅ 正确 |
| 字段为 NaN | 抛出 ClickException | ✅ 正确 |
| 字段为负数 | 抛出 ClickException | ✅ 正确 |
| 字段为无穷大 | 抛出 ClickException | ✅ 正确 |
| 停牌家数为负 | 设为 0 | ✅ 正确（数据不一致时的容错） |

### 3.3 缺失的处理

当前实现**没有处理**以下场景：

| 场景 | 风险 | 建议 |
|------|------|------|
| 880005 字段为 0（全部停牌？） | 可能是数据错误 | 添加合理性检查 |
| total_count < up + down + neutral | 数据不一致 | 已通过 suspended_count 容错 |
| limit_up_count > total_count | 数据错误 | 添加合理性检查 |

---

## 4. 时间字段

### 4.1 当前状态

`MarketStat` 模型**没有时间字段**：

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

### 4.2 问题

1. **无查询时间**：不知道数据是什么时候获取的
2. **无交易日**：不知道数据对应哪个交易日
3. **无盘中/盘后标识**：不知道是实时数据还是收盘数据

### 4.3 建议

添加时间字段：

```python
@dataclass
class MarketStat:
    # ... 现有字段 ...
    query_time: datetime | None = None  # 查询时间
    trade_date: date | None = None  # 交易日（如果可获取）
    is_realtime: bool = True  # 是否实时数据
```

---

## 5. Python SDK 映射

### 5.1 MacClient.get_stock_quotes()

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

### 5.2 market-stat 调用方式

```python
# 当前实现
with get_mac_client() as client:
    quotes = client.get_stock_quotes(
        [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
    )
```

### 5.3 字段请求

当前使用默认字段（PresetField.COMMON），包含：
- 基础字段：code, name, pre_close, open, high, low, close
- 量额字段：vol, amount, turnover, vol_ratio
- 股本字段：total_shares, float_shares
- 财务字段：eps, net_assets, pe_dynamic, pe_ttm, pe_static
- 其他：security_type_price, total_market_cap_ab, lot_size_info, dividend_yield 等

**结论**：PresetField.COMMON 包含 VOL 和 AMOUNT，满足 880005 的需求。

---

## 6. 发现的问题

### 6.1 字段语义不直观

880006 的字段映射：
- `close` → 涨停家数（limit_up_count）
- `open` → 跌停家数（limit_down_count）

这看起来不太直观，可能是历史遗留设计。建议在代码中添加注释说明。

### 6.2 缺少时间字段

MarketStat 没有记录查询时间，无法判断数据时效性。

### 6.3 停牌家数计算

```python
suspended_count = max(0, total - up - down - neutral)
```

这个计算假设 total >= up + down + neutral，但实际数据可能不一致。当前通过 max(0, ...) 容错，但没有记录这种不一致。

---

## 7. 建议改进

### 7.1 添加时间字段

```python
@dataclass
class MarketStat:
    # ... 现有字段 ...
    query_time: datetime | None = None  # 查询时间
```

### 7.2 添加数据验证

```python
# 在 _market_stat_from_quotes() 中添加
if total < up + down + neutral:
    # 记录警告日志
    pass
```

### 7.3 添加注释说明

```python
# 880006 字段映射（历史遗留设计）
# close = 涨停家数, open = 跌停家数
limit_up_count = int(values["880006"]["close"])
limit_down_count = int(values["880006"]["open"])
```

---

## 8. 与 Python SDK 的一致性

### 8.1 CLI vs SDK

| 功能 | CLI (market-stat) | SDK (MacClient) |
|------|-------------------|-----------------|
| 数据源 | get_stock_quotes([(SH, "880005"), (SH, "880001"), (SH, "880006")]) | 同左 |
| 字段选择 | 默认 COMMON | 默认 COMMON |
| 返回格式 | DataFrame → MarketStat → JSON/Table/CSV | DataFrame |

**结论**：CLI 和 SDK 使用相同的底层调用，一致性良好。

### 8.2 测试覆盖

测试文件 `test_cli_market_stat.py` 覆盖了：
- 正常映射（test_market_stat_mac_mapping）
- 多种输出格式（test_market_stat_formats）
- 缺值处理（test_market_stat_incomplete_data）
- 连接错误（test_market_stat_connection_error）

**覆盖率**：✅ 完整

---

## 9. 实时接入优先级

根据任务要求，实时接入优先级靠后，不阻塞历史数据。

**当前状态**：
- market-stat 是实时查询（每次调用都访问服务器）
- 无缓存机制
- 无历史数据存储

**建议**：
- Phase 1：保持当前实现，不添加缓存
- Phase 2：如需要，添加本地缓存（如 5 分钟 TTL）
- Phase 3：如需要，添加历史数据存储<tool_call>