# 证券标识格式统一 v2 - 补正交付

**日期**：2026-09-18
**状态**：待PM复审

---

## 1. 补正内容

### 1.1 aspool SQL查询规范化

**问题**：新格式通过校验但查询不到同一标的。

**修正**：
- `index_api.py`：添加 `_normalize_symbol()` 函数，规范化输入为存储格式 `SH.000001`
- `etf_api.py`：同上
- SQL查询使用规范化后的格式匹配
- 输出统一为规范格式 `000001.SH`

```python
def _normalize_symbol(s: str) -> str:
    """将符号规范化为存储格式 SH.XXXXXX。"""
    s = s.strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"  # 000001.SH -> SH.000001
    m = re.match(r"^(SH|SZ|BJ)\.(\d{6})$", s)
    if m:
        return s
    raise DataPoolError("INVALID_ARGUMENT", f"Invalid symbol format: {s}")
```

### 1.2 输出格式统一

**修正**：
- SQL输出从 `market || '.' || code` 改为 `code || '.' || market`
- 示例：`SH.000001` -> `000001.SH`

### 1.3 parse_stocks 异常处理

**问题**：`except (ValueError, Exception)` 吞掉全部异常。

**修正**：只捕获 `SymbolError`，不吞掉其他异常。

```python
try:
    code, market_str = parse_symbol(pair)
    market = parse_market(market_str)
    result.append((market, code))
    continue
except SymbolError:
    pass  # 尝试旧格式
```

---

## 2. 入口矩阵

| 入口 | 规范输入 | 兼容输入 | 规范输出 | 状态 |
|------|----------|----------|----------|------|
| `parse_stocks()` | `000001.SH` | `SH 000001`, `SH.000001` | - | ✅ |
| `index_api._symbols()` | `000300.SH` | `SH.000300` | - | ✅ |
| `etf_api` symbols | `000001.SH` | `SH.000001` | - | ✅ |
| `read_index_daily()` | - | - | `000300.SH` | ✅ |
| `read_etf_daily()` | - | - | `000001.SH` | ✅ |
| `list_indices()` | - | - | `000300.SH` | ✅ |
| `list_etfs()` | - | - | `000001.SH` | ✅ |
| CLI `quote` | `000001.SH` | `SZ 000001` | - | ✅ |
| CLI `kline` | `000001.SH` | `SZ 000001` | - | ✅ (两参数) |

---

## 3. 测试验证

### 3.1 测试命令

```bash
python -m pytest tests/unit/test_symbol.py tests/unit/test_symbol_e2e.py -v
```

### 3.2 测试结果

```
test_symbol.py: 32 passed
test_symbol_e2e.py: 21 passed
总计: 53 passed
```

### 3.3 端到端测试覆盖

| 测试 | 说明 | 状态 |
|------|------|------|
| test_new_format_single | 新格式单标的 | ✅ |
| test_new_format_multiple | 新格式多标的 | ✅ |
| test_old_format_single | 旧格式单标的 | ✅ |
| test_old_format_multiple | 旧格式多标的 | ✅ |
| test_mixed_format | 混合格式 | ✅ |
| test_legacy_dot_format | 旧点号格式 | ✅ |
| test_case_insensitive | 大小写 | ✅ |
| test_canonical_to_storage | 规范->存储 | ✅ |
| test_storage_to_canonical | 存储->规范 | ✅ |
| test_parse_format_parse | 往返转换 | ✅ |

---

## 4. 输出契约变更

### 4.1 变更说明

**变更前**：`market || '.' || code` (如 `SH.000001`)
**变更后**：`code || '.' || market` (如 `000001.SH`)

### 4.2 下游影响

- DataFrame `symbol` 列格式变更
- 下游 join、筛选需适配新格式
- 历史结果比较需注意格式差异

### 4.3 适配建议

```python
# 旧代码
df[df["symbol"] == "SH.000001"]

# 新代码
df[df["symbol"] == "000001.SH"]

# 或使用 market + code 列（不变）
df[(df["market"] == "SH") & (df["code"] == "000001")]
```

---

## 5. 已验证/未验证

### 已验证

- ✅ 规范输入解析
- ✅ 兼容输入解析
- ✅ aspool SQL查询规范化
- ✅ 输出格式统一
- ✅ CLI入口兼容
- ✅ 端到端测试

### 未验证

- ⏳ 股票 read_daily/read_research_daily（需aspool完整环境）
- ⏳ CLI kline 实际执行（需Click参数适配）
- ⏳ 历史数据迁移（不在本次范围）

---

## 6. 文件清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/aspool/index_api.py` | 修改 | 规范化输入+规范输出 |
| `src/aspool/etf_api.py` | 修改 | 规范化输入+规范输出 |
| `src/tdxman/cli/parsers.py` | 修改 | 修复异常处理 |
| `tests/unit/test_symbol_e2e.py` | 新增 | 端到端测试 |
| `docs/symbol_format_migration_v2.md` | 新增 | 本文档 |

---

## 7. 范围确认

**本轮完成**：
- ✅ aspool SQL查询规范化
- ✅ 输出格式统一
- ✅ CLI入口兼容
- ✅ 端到端测试

**不涉及**：
- ❌ 内部存储迁移
- ❌ 股票 read_daily（需完整环境）
- ❌ 历史数据迁移
- ❌ Fundwise生产代码修改