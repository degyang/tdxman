# 证券标识格式统一 v4 - 收口交付

**日期**：2026-09-18
**状态**：待 PM 复审
**依赖树**：HEAD `cb32022`，工作区含本轮及既有未提交改动

本轮不迁移存储、不改 Fundwise 代码、不提交 Git。
证据按 **公开接口运行验证 / CLI 运行验证 / 静态检查 / 不适用** 分类，未执行项不标通过。

---

## 1. 本轮修正

| 项 | 内容 |
|----|------|
| 单标的 CLI | `tick`、`finance`、`fund-flow` 支持规范标识，保留旧双参数；与 `kline` 共用同一解析路径 |
| 复合输出修复 | `_read_fundamentals` 的 symbol join 由旧格式改为规范格式（真实回归，见 §5） |
| 既有断言更新 | 2 处旧格式输出断言更新为规范格式，旧输入用例保留 |
| 文档修正 | kline 示例 `600519.SZ` → `600519.SH`；股票 fixture 不再用上证指数 `000001.SH` 冒充股票 |

### 1.1 共享解析路径

`parsers.parse_symbol_or_market_code` 是单标的 CLI 的唯一实现，
`kline`、`tick`、`finance`、`fund-flow` 四处均导入该函数，不再各写一份。
新格式：`<code>.<market>`（`code` 为 `None`）；旧格式：`<market> <code>`（`code` 非空）。
显式市场不被纠正、不被猜测。

---

## 2. 公开接口运行验证（临时 parquet fixture，无联网）

命令：

```bash
python -m pytest tests/unit/test_symbol_e2e_pool.py -q
# 39 passed
```

| 接口 | 规范输入 | 旧输入 | 规范输出 | 结果 |
|------|----------|--------|----------|------|
| `read_daily` | `600519.SH` 1 行 | `SH.600519` 1 行 | `600519.SH` | 通过 |
| `read_daily` | `000001.SZ` 1 行 | — | `000001.SZ` | 通过 |
| `read_daily` 大小写 | `600519.sh` | — | `600519.SH` | 通过 |
| `read_daily` 列表 | `["600519.SH","000001.SZ"]` 2 行 | — | 规范 | 通过 |
| `read_daily` 未命中 | `600000.SH` | — | 空 | 通过（与非法输入区分） |
| `read_daily` 非法 | `600519`（纯六位） | — | `DataPoolError` | 通过 |
| `read_research_daily` | `600519.SH` | `SH.600519` | `600519.SH` | 通过 |
| `read_research_daily` 投影 | `fields=[...,"pe_ttm"]` | — | `pe_ttm=30.5` 非空 | 通过 |
| `read_index_daily` | `000001.SH` 1 行 | `SH.000001` 1 行 | `000001.SH` | 通过 |
| `list_indices` | `000001.SH` | `SH.000001` | `000001.SH` | 通过 |
| `read_etf_daily` | `510050.SH` 1 行 | `SH.510050` 1 行 | `510050.SH` | 通过 |
| `list_etfs` | `510050.SH` | `SH.510050` | `510050.SH` | 通过 |
| 股票接口隔离 | — | — | 不返回 ETF | 通过 |

`market`、`code` 列保持独立且含义不变（`test_market_and_code_columns_unchanged`）。

---

## 3. CLI 运行验证

### 3.1 CliRunner + mock 连接器（无联网）

命令：

```bash
python -m pytest tests/unit/test_cli_symbol_e2e.py -q
# 20 passed
```

两个客户端工厂 `get_mac_client` / `get_tdx_client` 同时以真正 contextmanager 替换，杜绝真实连接。

| 命令 | 新格式 | 旧格式 | 断言 |
|------|--------|--------|------|
| `kline` | `600519.SH` | `SH 600519` | 到达同一 `(Market.SH,"600519")` |
| `kline` | `000001.SZ` | `SZ 000001` | 到达同一 `(Market.SZ,"000001")` |
| `kline` | `600519.sh` | — | 规范化为 `(Market.SH,"600519")` |
| `kline` | `830001.BJ` | — | `(Market.BJ,"830001")` |
| `kline` | `000001.SH` | — | 走**指数**分支 `get_index_bars` |
| `kline` | `abc` / `600519` | — | 非零退出 |
| `tick` | `000001.SZ` | `SZ 000001` | 到达同一 market/code |
| `finance` | `000001.SZ` | `SZ 000001` | 到达同一 market/code |
| `fund-flow` | `000001.SZ` | `SZ 000001` | 到达同一 market/code |
| `quote` | `600519.SH` | `SZ 000001` | `[(Market.SH,"600519")]` / `[(Market.SZ,"000001")]` |
| `quote` 混合 | `600519.SH,SZ 000001` | — | 两项均正确 |

### 3.2 静态检查（未运行）

| 项 | 证据 |
|----|------|
| 共享解析 | `cmd_kline.py` / `cmd_tick.py` / `cmd_finance.py` 均导入 `parsers.parse_symbol_or_market_code`，无本地副本 |
| quote 输出 | `_flatten_quote_fields` 只产出 `market`/`code`/`name`，无复合 `symbol` 列 → 无需规范化，不加多余列 |
| quote-list 输出 | 同上 |

### 3.3 不适用

| 项 | 原因 |
|----|------|
| `ex` 系列命令 | 扩展市场（HK/US/期货），不属 A 股证券标识范围 |
| `offline` / `transaction` / `auction` / `capital-flow` / `unusual` / `board-*` / `symbol-info` | 仍为 `MARKET CODE` 双参数或纯 code，未纳入本轮；矩阵见 §6 |
| SDK `market`/`code` 参数签名 | 保持原签名，未改 |

---

## 4. 受影响既有测试回归

命令：

```bash
python -m pytest tests/unit/test_aspool.py tests/unit/test_aspool_index.py \
  tests/unit/test_aspool_etf.py tests/unit/test_aspool_pool.py -q
# 47 passed, 6 subtests passed
```

| 测试 | 变更 | 旧输入保留 |
|------|------|-----------|
| `test_fundwise_index_api_isolated_read_only_filters_and_projection` | 输出断言 `SH.881001` → `881001.SH`，新增规范输入等价检查 | 是（仍以 `SH.881001` 调用） |
| `test_read_contract_and_date_before_window` | 输出断言 → `000001.SZ`，新增新旧输入等价 | 是（`SH.000001` 未命中用例保留） |
| `test_same_date_code_in_different_markets_is_not_duplicate` | 输出断言 → `000001.SH`/`000001.SZ` | — |

全量：

```bash
python -m pytest tests/unit/ -q
# 358 passed, 18 subtests passed, 3 failed
```

3 项失败为本轮开始前既存、与本轮无关（表格浮点格式断言）：
`test_cli_offline.py::test_offline_reads_the_latest_local_daily_bar_with_count`、
`test_cli_output.py::test_table_and_csv_render_price_columns_with_two_decimal_places`、
`test_cli_output.py::test_output_formats_quote_metrics_by_field_type`。
已在基线 `cb32022` 上以 `git stash` 复现确认。

---

## 5. 本轮修复的真实回归

**问题**：`DataPool._read_fundamentals` 用 `f"{market}.{code}"` 构造 join 键，而 `read_daily` 输出已改为 `code.market`，导致 `frame.symbol_id.map(closes)` 全部落空，`total_mv` / `float_mv` / `pe_ttm` / `pb` 变为 NaN。

**证据**：`test_fundamentals_snapshot_uses_current_bar_for_valuation` 由失败转为通过。

**修复**：`_read_fundamentals` 的 `symbol_id` 改用 `code + "." + market`，与公开输出一致；`symbols` 过滤同样按规范格式比较。

这正是 PM 提示的"只改显示导致 join 丢失"，属真实缺陷而非文案问题。

---

## 6. 单标的 CLI 矩阵

| 命令 | 参数形态 | 涉及复合标识 | 本轮状态 |
|------|----------|--------------|----------|
| `kline` | `SYMBOL_OR_MARKET [CODE]` | 是 | **规范+旧格式已支持** |
| `tick` | `SYMBOL_OR_MARKET [CODE]` | 是 | **规范+旧格式已支持** |
| `finance` | `SYMBOL_OR_MARKET [CODE]` | 是 | **规范+旧格式已支持** |
| `fund-flow` | `SYMBOL_OR_MARKET [CODE]` | 是 | **规范+旧格式已支持** |
| `quote` | `STOCKS`（逗号列表） | 是 | **规范+旧格式+混合已支持** |
| `quote-list` | `CATEGORY` | 否（分类枚举） | 不适用 |
| `transaction` | `MARKET CODE` | 是 | 未纳入本轮 |
| `auction` | `MARKET CODE` | 是 | 未纳入本轮 |
| `capital-flow` | `MARKET CODE` | 是 | 未纳入本轮 |
| `unusual` | `MARKET` | 否 | 不适用 |
| `belong-board` | `MARKET CODE` | 是 | 未纳入本轮 |
| `symbol-info` | `MARKET CODE` | 是 | 未纳入本轮 |
| `offline` | `MARKET CODE` | 是 | 未纳入本轮 |
| `ex *` | 扩展市场 | 否（非 A 股） | 不适用 |
| `board-list` / `board-members` / `server-info` / `markets` / `ping` / `version` / `market-stat` | 无证券标识参数 | 否 | 不适用 |

---

## 7. 输出契约变更与下游影响

- 复合标识输出由 `SH.600519` 改为 `600519.SH`（`code.market`）。
- 受影响接口：`read_daily`、`read_research_daily`、`read_index_daily`、`list_indices`、`read_etf_daily`、`list_etfs`。
- 不受影响：`market` 列、`code` 列、SDK `market`/`code` 参数签名、纯六位 `code` 语义。
- Fundwise 存在历史 symbol 联结，需在其适配边界处理；**不改写历史归档**。
- CLI `quote` / `quote-list` 无复合 `symbol` 列，不受影响。

---

## 8. 未验证项

| 项 | 状态 |
|----|------|
| `transaction` / `auction` / `capital-flow` / `belong-board` / `symbol-info` / `offline` 的规范标识输入 | 未实现、未验证 |
| 真实行情服务器端到端 | 未执行（按要求不联网） |
| 生产 DataPool 数据上的新旧等价 | 未执行（仅用临时 fixture） |

---

## 9. 文件清单

| 文件 | 变更 |
|------|------|
| `src/tdxman/symbol.py` | 核心解析/格式化（v1 起） |
| `src/tdxman/cli/parsers.py` | `parse_stocks` 支持规范格式，仅捕获 `SymbolError`；新增共享 `parse_symbol_or_market_code` |
| `src/tdxman/cli/cmd_kline.py` | 可选 code + 规范标识，docstring 示例修正 |
| `src/tdxman/cli/cmd_tick.py` | 可选 code + 规范标识 |
| `src/tdxman/cli/cmd_finance.py` | `finance`/`fund-flow` 可选 code + 规范标识 |
| `src/tdxman/cli/cmd_quote.py` | help 主推荐改为规范格式 |
| `src/tdxman/cli/help.py` | quote STOCKS 说明更新 |
| `src/aspool/pool.py` | 输入规范化、规范输出、`_read_fundamentals` join 修复 |
| `src/aspool/index_api.py` | 输入规范化、规范输出 |
| `src/aspool/etf_api.py` | 输入规范化、规范输出 |
| `tests/unit/test_symbol.py` | 规范化示例配对 |
| `tests/unit/test_symbol_e2e.py` | 显式市场不被纠正用例 |
| `tests/unit/test_symbol_e2e_pool.py` | 公开接口 fixture（股票/指数/ETF/目录） |
| `tests/unit/test_cli_symbol_e2e.py` | CLI CliRunner + mock |
| `tests/unit/test_aspool_index.py` | 旧断言更新 + 新旧等价 |
| `tests/unit/test_aspool_pool.py` | 旧断言更新 + 新旧等价 |
