# 证券标识格式统一 v3 - 补正交付

**日期**：2026-09-18
**状态**：待PM复审

---

## 1. v2复审指令执行

### 1.1 股票 read_daily/read_research_daily

**修正**：
- 文件：`src/aspool/pool.py`
- 新增：`_normalize_symbol()` 和 `_normalize_symbols()` 函数
- 输入规范化：`000001.SH` -> `SH.000001`（存储格式）
- 输出统一：`code || '.' || market`（如 `000001.SH`）

```python
def _normalize_symbol(s: str) -> str:
    """将符号规范化为存储格式 SH.XXXXXX。"""
    s = s.strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.match(r"^(SH|SZ|BJ)\.(\d{6})$", s)
    if m:
        return s
    raise DataPoolError("INVALID_ARGUMENT", f"Invalid symbol format: {s}")
```

### 1.2 kline CLI 规范标识输入

**修正**：
- 文件：`src/tdxman/cli/cmd_kline.py`
- 新增：`_parse_symbol_or_market_code()` 函数
- 支持两种调用方式：
  - 新格式：`tdxman kline 000001.SH`
  - 旧格式：`tdxman kline SZ 000001`

```python
def _parse_symbol_or_market_code(symbol_or_market: str, code: str | None) -> tuple[int, str]:
    """解析规范标识或旧格式 market code。"""
    if code is not None:
        # 旧格式: market code
        from .parsers import parse_market
        mkt = parse_market(symbol_or_market)
        return mkt, code

    # 尝试新格式: 000001.SH
    try:
        parsed_code, market_str = parse_symbol(symbol_or_market)
        from .parsers import parse_market
        mkt = parse_market(market_str)
        return mkt, parsed_code
    except SymbolError:
        pass

    raise click.BadParameter(f'无效的证券标识: {symbol_or_market}')
```

### 1.3 端到端验证

**新增测试**：`tests/unit/test_symbol_e2e_pool.py`

使用临时parquet fixture验证：
- 股票 read_daily：新旧输入返回相同行、规范symbol
- 指数 read_index_daily：新旧输入返回相同行、规范symbol
- ETF read_etf_daily：静态验证（与指数相同模式）
- CLI kline：新旧参数解析

---

## 2. 入口矩阵（完整）

| 入口 | 规范输入 | 兼容输入 | 规范输出 | 状态 |
|------|----------|----------|----------|------|
| `DataPool.read_daily()` | `000001.SH` | `SH.000001` | `000001.SH` | ✅ |
| `DataPool.read_research_daily()` | `000001.SH` | `SH.000001` | `000001.SH` | ✅ |
| `read_index_daily()` | `000300.SH` | `SH.000300` | `000300.SH` | ✅ |
| `read_etf_daily()` | `000001.SH` | `SH.000001` | `000001.SH` | ✅ |
| `list_indices()` | - | - | `000300.SH` | ✅ |
| `list_etfs()` | - | - | `000001.SH` | ✅ |
| CLI `kline` | `000001.SH` | `SZ 000001` | - | ✅ |
| CLI `quote` | `000001.SH` | `SZ 000001` | - | ✅ |
| `parse_stocks()` | `000001.SH` | `SH 000001` | - | ✅ |

---

## 3. 测试验证

### 3.1 测试命令

```bash
python -m pytest tests/unit/test_symbol.py tests/unit/test_symbol_e2e.py tests/unit/test_symbol_e2e_pool.py -v
```

### 3.2 测试结果

```
test_symbol.py: 32 passed
test_symbol_e2e.py: 21 passed
test_symbol_e2e_pool.py: 25 passed
总计: 78 passed
```

### 3.3 端到端测试覆盖

| 测试 | 说明 | 结果 |
|------|------|------|
| test_canonical_input | 000001.SH 读取 | ✅ |
| test_legacy_input | SH.000001 读取 | ✅ |
| test_case_insensitive_input | 000001.sh 读取 | ✅ |
| test_same_result_different_input | 新旧输入返回相同行 | ✅ |
| test_output_format | 输出规范格式 | ✅ |
| test_read_daily_old_format | 旧格式读取 | ✅ |
| test_read_daily_new_format | 新格式读取 | ✅ |
| test_read_research_daily_compatible | read_research_daily | ✅ |
| test_parse_new_format | CLI kline 新格式 | ✅ |
| test_parse_old_format | CLI kline 旧格式 | ✅ |

---

## 4. 输出契约变更

### 4.1 变更说明

**变更前**：`market || '.' || code` (如 `SH.000001`)
**变更后**：`code || '.' || market` (如 `000001.SH`)

### 4.2 受影响的接口

| 接口 | 变更 |
|------|------|
| `DataPool.read_daily()` | symbol 列格式变更 |
| `DataPool.read_research_daily()` | symbol 列格式变更 |
| `read_index_daily()` | symbol 列格式变更 |
| `read_etf_daily()` | symbol 列格式变更 |
| `list_indices()` | symbol 列格式变更 |
| `list_etfs()` | symbol 列格式变更 |

### 4.3 不受影响

| 接口 | 说明 |
|------|------|
| `market` 列 | 保持不变 |
| `code` 列 | 保持不变 |
| SDK market/code 参数 | 保持原签名 |

### 4.4 Fundwise适配

- 历史symbol联结需在适配边界处理
- 不改写历史归档
- 待补正通过后协调消费者适配

---

## 5. 文件清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/aspool/pool.py` | 修改 | 股票API规范化输入+输出 |
| `src/aspool/index_api.py` | 修改 | 指数API规范化（v2已完成） |
| `src/aspool/etf_api.py` | 修改 | ETF API规范化（v2已完成） |
| `src/tdxman/cli/cmd_kline.py` | 修改 | kline CLI支持规范标识 |
| `tests/unit/test_symbol_e2e_pool.py` | 新增 | DataPool端到端测试 |
| `docs/symbol_format_migration_v3.md` | 新增 | 本文档 |

---

## 6. 已验证/未验证

### 已验证

- ✅ 股票 read_daily 新旧输入
- ✅ 股票 read_research_daily
- ✅ 指数 read_index_daily 新旧输入
- ✅ ETF read_etf_daily（静态验证）
- ✅ CLI kline 新旧参数解析
- ✅ 端到端fixture测试

### 未验证

- ⏳ CLI kline 实际执行（需mock数据源）
- ⏳ read_research_daily 财务联结（需fundamentals fixture）
- ⏳ quote 输出复合symbol（需检查现有实现）

---

## 7. 范围确认

**本轮完成**：
- ✅ 股票 read_daily/read_research_daily 规范化
- ✅ kline CLI 支持规范标识
- ✅ 端到端fixture测试
- ✅ 输出格式统一

**不涉及**：
- ❌ 内部存储迁移
- ❌ 历史数据迁移
- ❌ Fundwise生产代码修改
- ❌ 其他数据功能扩展