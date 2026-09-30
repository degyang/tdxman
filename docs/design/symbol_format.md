# 证券标识格式规范

## 1. 规范格式

**规范格式**: `000001.SH`, `000001.SZ`, `000001.BJ`

- 6位代码 + `.` + 市场代码
- 市场代码大写: `SH`, `SZ`, `BJ`
- 示例: `600519.SH`, `000001.SZ`, `830001.BJ`

## 2. 兼容格式

为保持向后兼容，以下格式仍可接受作为输入：

| 格式 | 示例 | 说明 |
|------|------|------|
| 规范格式 | `600519.SH` | 推荐格式 |
| 旧点号格式 | `SH.600519` | 兼容旧版 aspool |
| 空格格式 | `SH 600519` | 兼容旧版 tdxman CLI |
| 逗号分隔 | `600519.SH,000001.SZ` | 批量标识 |

## 3. 使用方式

### 3.1 Python API

```python
from tdxman.symbol import parse_symbol, format_symbol

# 解析（支持所有兼容格式）
code, market = parse_symbol("000001.SH")  # ('000001', 'SH')
code, market = parse_symbol("SH.000001")  # ('000001', 'SH')
code, market = parse_symbol("SH 000001")  # ('000001', 'SH')

# 格式化为规范格式
symbol = format_symbol("000001", "SH")  # '000001.SH'

# 批量解析
pairs = parse_symbols("600519.SH,000001.SZ")  # [('600519', 'SH'), ('000001', 'SZ')]
```

### 3.2 CLI 命令

```bash
# 新格式（推荐）
tdxman quote "600519.SH"
tdxman kline 600519.SH --period DAILY
tdxman quote "600519.SH,000001.SZ"

# 旧格式（兼容）
tdxman quote "SH 600519"
tdxman kline SH 600519 --period DAILY
tdxman quote "SZ 000001,SH 600519"

# 混合格式
tdxman quote "600519.SH,SZ 000001"
```

### 3.3 aspool API

```python
# ETF API - 支持两种格式
df = read_etf_daily(root, symbols=["510050.SH"])
df = read_etf_daily(root, symbols=["SH.510050"])

# 指数 API - 支持两种格式
df = read_index_daily(root, symbols=["000300.SH", "399001.SZ"])
df = read_index_daily(root, symbols=["SH.000300", "SZ.399001"])
```

## 4. 输出格式

所有公开 API 的复合标识输出统一使用规范格式 `code.market`：

```python
# DataFrame 输出
   symbol    market  code   date        close
0  600519.SH  SH    600519 2026-09-15  1800.00
1  000001.SZ  SZ    000001 2026-09-15  10.50
```

`market` 与 `code` 列为独立标识，含义不变。
CLI `quote` / `quote-list` 输出只有独立的 `market` / `code` 列，不产生复合 `symbol`。

## 5. 市场代码

| 代码 | 交易所 | 代码示例 |
|------|--------|----------|
| `SH` | 上海证券交易所 | 600519, 688001, 000001 |
| `SZ` | 深圳证券交易所 | 000001, 300001, 002001 |
| `BJ` | 北京证券交易所 | 830001, 430047, 920001 |

## 6. 错误处理

```python
from tdxman.symbol import parse_symbol, SymbolError

try:
    code, market = parse_symbol("000001")  # 纯六位代码
except SymbolError as e:
    print(e)  # "纯六位代码需指定市场: 000001 -> 请使用 000001.SH 或 000001.SZ"
```

## 7. 迁移指南

### 7.1 从旧格式迁移

```python
# 旧代码
symbol = "SH.000001"

# 新代码
from tdxman.symbol import parse_symbol, format_symbol
code, market = parse_symbol(symbol)
canonical = format_symbol(code, market)  # "000001.SH"
```

### 7.2 内部存储

内部存储格式不变，仍使用 `market` + `symbol` 分开存储：
- parquet 文件: `market=SH/symbol=000001/bars.parquet`
- 数据库: `market` 列 + `symbol` 列

公开 API 返回时组合为规范格式 `000001.SH`。

## 8. 测试

```bash
python -m pytest tests/unit/test_symbol.py -v
```

测试覆盖：
- 规范格式解析
- 兼容格式解析
- 大小写处理
- 空格处理
- 错误格式处理
- 往返转换