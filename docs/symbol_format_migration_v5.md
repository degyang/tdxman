# 证券标识格式统一 v5 - 收口交付

**日期**：2026-09-18
**状态**：已验收成果已纳入本地 checkpoint `a78fb49`，规范输出补正见 `fc5da06`
**依赖树**：基线 `cb32022`；最终代码 checkpoint `fc5da06`；工作区仍保留其他未提交改动

本轮不迁移存储、不改 Fundwise 代码、不提交 Git。
未在共享 dirty 工作树使用 stash/reset 切换基线；基线比较采用隔离 worktree（见 §4）。

---

## 1. 本轮修正（对应 v4 指令）

| 指令 | 处理 |
|------|------|
| 1. 修复 `_read_fundamentals` 筛选键 + 补 fixture | 已修复，新增 7 项 fixture |
| 2. `transaction`/`auction`/`capital-flow`/`belong-board`/`symbol-info`/`offline` 纳入范围 | 6 条命令全部支持规范输入 + 旧调用兼容 |
| 3. 补真实 DataFrame 输出相等断言 | 已补（quote 渲染内容逐字节相等） |
| 4. 交付 v5 文档 + Fundwise 完成记录 | 本文档 |

### 1.1 `_read_fundamentals` 筛选键修复

**缺陷**（PM 指出）：`_normalize_symbol` 返回存储格式 `market.code`，代码按 `code_part, market_part` 解包后原样拼接，`want` 实为 `SH.600519`，而 `symbol_id` 已是 `600519.SH` → 显式 `symbols=` 筛选静默落空。

**修复**：

```python
want = set()
for value in requested:
    market_part, code_part = _normalize_symbol(value).split(".", 1)
    want.add(f"{code_part}.{market_part}")
frame = frame[frame.symbol_id.isin(want)]
```

无 `symbols` 的 join 修复保持不变。

### 1.2 单标的 CLI 收口

`transaction`、`auction`、`capital-flow`、`belong-board`、`symbol-info`、`offline`
全部改为 `SYMBOL_OR_MARKET [CODE]`，复用 `parsers.parse_symbol_or_market_code`
（`grep` 确认仅一处定义）。旧双参数调用保留。

---

## 2. 公开接口运行验证（临时 parquet fixture，无联网）

```bash
python -m pytest tests/unit/test_symbol_e2e_pool.py -q
# 46 passed
```

### 2.1 读取与目录（新旧等价、规范输出）

已通过项与 v4 相同（`read_daily`/`read_index_daily`/`read_etf_daily` 及三种目录），
本轮不重做。

### 2.2 `_read_fundamentals` 筛选分支（本轮新增）

| 用例 | 断言 | 结果 |
|------|------|------|
| 无筛选 | 2 行，`total_mv`/`pe_ttm` 全非空 | 通过 |
| `symbols="600519.SH"` | 非空，`code == 600519` | 通过 |
| `symbols="SH.600519"` | 非空，`code == 600519` | 通过 |
| 筛选 vs 无筛选 | `total_mv`/`float_mv`/`pe_ttm` 逐值相等 | 通过 |
| 新旧列表输入 | 行数、code 集合、`total_mv` 一致 | 通过 |
| `symbols="000001.SZ"` | `total_mv == 11.0 × 2_000_000` | 通过 |
| 未命中 | 空 | 通过 |

---

## 3. CLI 运行验证（CliRunner + mock，无联网）

```bash
python -m pytest tests/unit/test_cli_symbol_e2e.py -q
# 31 passed
```

两个客户端工厂以真正 contextmanager 替换，杜绝真实连接。

### 3.1 路由等价（新旧参数到达相同 market/code）

| 命令 | 结果 |
|------|------|
| `kline`（股票分支 / 指数分支 / 大小写 / 北交所 / 非法拒绝） | 通过 |
| `tick` | 通过 |
| `finance` | 通过 |
| `fund-flow` | 通过 |
| `transaction` | 通过（本轮新增） |
| `auction` | 通过（本轮新增） |
| `capital-flow` | 通过（本轮新增） |
| `belong-board` | 通过（本轮新增） |
| `symbol-info` | 通过（本轮新增） |
| 上述 5 条命令拒绝纯六位代码 | 通过（本轮新增） |
| `quote` 单只 / 旧格式 / 混合 | 通过 |

### 3.2 最终内容相等（本轮新增，回应"仅证明路由"）

`quote` 以**真实 DataFrame** 作为返回值，断言新旧调用渲染输出**逐字节相等**：

```python
new = CliRunner().invoke(cli, ["quote", "600519.SH"])
old = CliRunner().invoke(cli, ["quote", "SH 600519"])
assert new.output == old.output
```

---

## 4. 受影响既有测试回归

```bash
python -m pytest tests/unit/test_aspool.py tests/unit/test_aspool_index.py \
  tests/unit/test_aspool_etf.py tests/unit/test_aspool_pool.py -q
# 47 passed, 6 subtests passed
```

```bash
python -m pytest tests/unit/ -q
# 376 passed, 18 subtests passed, 3 failed
```

3 项失败为**本轮开始前既存**，与本轮无关：表格浮点渲染断言
（`test_cli_offline.py` 期望 `1258.00` 实得 `1258`；`test_cli_output.py` 两处同类）。

基线归因用**隔离 worktree** 复核，未在共享 dirty 工作树 stash/reset：

```bash
git worktree add /tmp/tdxman-base cb32022
cd /tmp/tdxman-base && PYTHONPATH=/tmp/tdxman-base/src python -m pytest \
  tests/unit/test_cli_offline.py::test_offline_reads_the_latest_local_daily_bar_with_count \
  tests/unit/test_cli_output.py::test_table_and_csv_render_price_columns_with_two_decimal_places \
  tests/unit/test_cli_output.py::test_output_formats_quote_metrics_by_field_type -q --tb=no
# 3 failed in 0.37s
```

`offline` 测试使用的旧格式 `["offline","SH","600519",...]` 调用路径仍正常（exit 0）。

ruff：`All checks passed!`

---

## 5. 单标的 CLI 矩阵（最终）

| 命令 | 参数形态 | 涉及复合标识 | 状态 |
|------|----------|--------------|------|
| `kline` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `tick` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `finance` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `fund-flow` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `transaction` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `auction` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `capital-flow` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `belong-board` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `symbol-info` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `offline` | `SYMBOL_OR_MARKET [CODE]` | 是 | 规范 + 旧格式支持 |
| `quote` | `STOCKS` 逗号列表 | 是 | 规范 + 旧格式 + 混合 |
| `quote-list` | `CATEGORY` | 否 | 不适用（分类枚举） |
| `unusual` / `market-stat` / `server-info` / `markets` / `ping` / `version` | 无证券标识或仅市场 | 否 | 不适用 |
| `board-list` / `board-members` | `BOARD_SYMBOL`（板块/指数码） | 否 | 不适用 |
| `ex *` | 扩展市场（HK/US/期货） | 否 | 不适用（非 A 股） |

输出侧：公开复合标识统一 `code.market`；`quote`/`quote-list` 仅独立 `market`/`code`，无复合列。

---

## 6. 输出契约变更与下游影响

- 复合标识输出由 `SH.600519` 改为 `600519.SH`。
- 受影响：`read_daily`、`read_research_daily`、`read_index_daily`、`list_indices`、
  `read_etf_daily`、`list_etfs`。
- 不受影响：`market` 列、`code` 列、SDK `market`/`code` 参数签名、纯六位 `code` 语义。
- Fundwise 历史 symbol 联结需在其适配边界处理；**不改写历史归档**。

---

## 7. 通过与未验证项

| 项 | 状态 |
|----|------|
| 公开读取/目录新旧等价与规范输出 | 运行验证通过 |
| `_read_fundamentals` 筛选分支 | 运行验证通过 |
| 单标的 CLI 路由等价（含本轮 6 条） | 运行验证通过 |
| `quote` 新旧渲染内容相等 | 运行验证通过 |
| 既有 aspool 回归 | 运行验证通过 |
| 全量单测 | 376 通过 / 3 既存失败 |
| 真实行情服务器端到端 | **未验证**（按要求不联网） |
| 生产 DataPool 数据上的新旧等价 | **未验证**（仅临时 fixture） |
| 存储迁移 | **未做**（范围外） |

---

## 8. 文件清单

| 文件 | 变更 |
|------|------|
| `src/aspool/pool.py` | `_read_fundamentals` 筛选键修复 |
| `src/tdxman/cli/parsers.py` | 共享 `parse_symbol_or_market_code`（v4 起） |
| `src/tdxman/cli/cmd_transaction.py` | 规范标识 + 旧兼容 |
| `src/tdxman/cli/cmd_auction.py` | 规范标识 + 旧兼容 |
| `src/tdxman/cli/cmd_capital.py` | 规范标识 + 旧兼容 |
| `src/tdxman/cli/cmd_board.py` | `belong-board` 规范标识 + 旧兼容 |
| `src/tdxman/cli/cmd_info.py` | `symbol-info` 规范标识 + 旧兼容 |
| `src/tdxman/cli/cmd_offline.py` | 规范标识 + 旧兼容 |
| `src/tdxman/cli/help.py` | 9 条命令参数说明与示例改为规范格式 |
| `tests/unit/test_symbol_e2e_pool.py` | 新增 fundamentals 筛选 fixture（7 项） |
| `tests/unit/test_cli_symbol_e2e.py` | 新增 5 条命令路由 + 纯码拒绝 + 渲染相等 |
