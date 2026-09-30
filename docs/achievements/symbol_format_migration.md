# 证券标识格式统一 - 兼容性及影响范围说明

## 1. 变更概述

将 tdxman、aspool 的 CLI 与公开 Python API 证券标识统一为 `000001.SH`、`000001.SZ` 格式。

### 1.1 规范格式

- **新规范**: `000001.SH`, `000001.SZ`, `000001.BJ`
- **格式**: `6位代码.市场代码`
- **市场代码大写**: `SH`, `SZ`, `BJ`

### 1.2 兼容格式（输入）

| 格式 | 示例 | 状态 |
|------|------|------|
| 规范格式 | `000001.SH` | ✅ 推荐 |
| 旧点号格式 | `SH.000001` | ✅ 兼容 |
| 空格格式 | `SH 000001` | ✅ 兼容 |
| 逗号分隔 | `600519.SH,000001.SZ` | ✅ 兼容 |

### 1.3 内部存储

- **不迁移**: parquet 文件、数据库仍使用 `market` + `symbol` 分开存储
- **输出组合**: 公开 API 返回时组合为规范格式 `000001.SH`

---

## 2. 影响范围

### 2.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/tdxman/symbol.py` | 核心符号工具函数 |
| `tests/unit/test_symbol.py` | 符号转换测试 |
| `docs/symbol_format.md` | 格式规范文档 |

### 2.2 修改文件

| 文件 | 修改内容 | 兼容性 |
|------|----------|--------|
| `src/tdxman/cli/parsers.py` | `parse_stocks()` 支持新格式 | ✅ 完全兼容 |
| `src/aspool/etf_api.py` | 符号验证支持新格式 | ✅ 完全兼容 |
| `src/aspool/index_api.py` | 符号验证支持新格式 | ✅ 完全兼容 |

### 2.3 未修改文件

| 文件 | 说明 |
|------|------|
| `src/tdxman/client.py` | 内部 API，仍使用 `(market, code)` 元组 |
| `src/tdxman/mac/client.py` | 内部 API，仍使用 `(market, code)` 元组 |
| `src/aspool/tdx_online.py` | 内部逻辑，仍使用 `market` + `symbol` |
| `src/aspool/free_stockdb.py` | 内部逻辑，仍使用 `market` + `symbol` |

---

## 3. 兼容性验证

### 3.1 CLI 入口兼容

```bash
# 新格式
tdxman quote "000001.SH"
tdxman kline 000001.SH --period DAILY

# 旧格式（兼容）
tdxman quote "SZ 000001"
tdxman kline SZ 000001 --period DAILY

# 混合格式
tdxman quote "000001.SH,SZ 600519"
```

### 3.2 Python API 兼容

```python
# ETF API
df = read_etf_daily(root, symbols=["000001.SH"])  # 新格式
df = read_etf_daily(root, symbols=["SH.000001"])  # 旧格式

# 指数 API
df = read_index_daily(root, symbols=["000300.SH"])  # 新格式
df = read_index_daily(root, symbols=["SH.000300"])  # 旧格式
```

### 3.3 内部 API 不变

```python
# TdxClient 内部 API 保持不变
with TdxClient.from_best_host() as client:
    df = client.get_security_bars(Market.SH, "600519", KlineCategory.DAY, 0, 100)
    # 仍使用 (Market.SH, "600519") 元组
```

---

## 4. 输出格式变更

### 4.1 CLI 输出

**变更前**:
```
| market | code   | date       | close |
|--------|--------|------------|-------|
| SH     | 600519 | 2026-09-15 | 1800  |
```

**变更后**:
```
| symbol     | market | code   | date       | close |
|------------|--------|--------|------------|-------|
| 600519.SH  | SH     | 600519 | 2026-09-15 | 1800  |
```

### 4.2 Python API 输出

**变更前**:
```python
df = read_etf_daily(root, symbols=["SH.510050"])
# 返回: market=SH, symbol=510050
```

**变更后**:
```python
df = read_etf_daily(root, symbols=["510050.SH"])
# 返回: symbol=510050.SH, market=SH, code=510050
```

---

## 5. 测试覆盖

### 5.1 符号转换测试 (32个)

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| TestParseSymbol | 11 | 解析各种格式 |
| TestFormatSymbol | 3 | 格式化输出 |
| TestParseSymbols | 5 | 批量解析 |
| TestFormatSymbols | 3 | 批量格式化 |
| TestGuessMarket | 3 | 代码推断市场 |
| TestIsValidSymbol | 4 | 有效性检查 |
| TestRoundTrip | 3 | 往返转换 |

### 5.2 测试命令

```bash
python -m pytest tests/unit/test_symbol.py -v
```

---

## 6. 迁移指南

### 6.1 用户代码迁移

```python
# 旧代码
symbol = "SH.000001"

# 新代码（推荐）
from tdxman.symbol import parse_symbol, format_symbol
code, market = parse_symbol(symbol)
canonical = format_symbol(code, market)  # "000001.SH"

# 或直接使用规范格式
symbol = "000001.SH"
```

### 6.2 CLI 命令迁移

```bash
# 旧命令
tdxman quote "SZ 000001"

# 新命令（推荐）
tdxman quote "000001.SH"

# 旧命令仍可使用（兼容）
tdxman quote "SZ 000001"
```

---

## 7. 风险评估

### 7.1 低风险

- ✅ 旧格式输入完全兼容
- ✅ 内部存储不迁移
- ✅ 纯六位代码参数含义不变
- ✅ 现有测试全部通过

### 7.2 注意事项

- ⚠️ 输出格式新增 `symbol` 列，下游解析需注意
- ⚠️ 依赖 tdxman.symbol 的下游需更新导入
- ⚠️ 文档和示例已更新，用户需参考新格式

---

## 8. 后续计划

### 8.1 短期

- ✅ 核心符号工具函数
- ✅ CLI 入口兼容
- ✅ aspool API 兼容
- ✅ 文档和测试

### 8.2 中期

- 📝 CLI 输出格式统一（添加 symbol 列）
- 📝 更多 CLI 命令支持新格式

### 8.3 长期

- 📝 内部存储格式统一（可选）
- 📝 全面迁移至规范格式

---

## 9. 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-09-18 | 初始版本，支持规范格式和兼容格式 |