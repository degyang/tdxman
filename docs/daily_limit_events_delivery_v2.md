# 日终稀疏涨跌停事件 v2 — 补正交付

**日期**：2026-09-18
**依赖树**：HEAD `cb32022`，工作区含本轮及既有未提交改动
**状态**：待 PM 复审
**上一版**：`docs/daily_limit_events_delivery.md`（已被本版取代）

本轮不改 Fundwise、不接实时、不回填生产全库、不提交 Git、未触碰生产池。

---

## 1. 六组阻塞的处置

### 1.1 历史更正自动传播

**原状**：`compute_limit_events` 只循环调用方传入的日期，传播靠测试手动传后续日。

**现实现**（`limit_events.py::compute_limit_events`）：
计划序列 = 请求日期 + 其后**已在市场轴内且已发布**的会话；
逐日重算并在该日**发布前**取旧连板值比较，一致即停止。

**PM 反例实测**（`/tmp` 临时池）：

```text
初次发布 09-08 连板: 2
只传 09-07 修正后, 09-08 连板: 1   ← 应为 1，已自动传播
```

**测试**：`test_correction_propagates_automatically`（只传一个日期）。

**前史预热**：上市窗口判定要求该证券自身截至该日的会话数（见 §1.2）；
不足即 UNKNOWN，不声称"任意窗口都有准确连板"。

### 1.2 上市无约束与规则覆盖

**现实现**（`price_rules.py::resolve_limit_rule`）返回三态：
`KNOWN`（rule 非空）/ `NO_LIMIT`（`no_limit=True` + `no_limit_basis`）/ `UNKNOWN`。

- 各板块**生效起点**（科创板 2019-07-22、创业板开板 2009-10-30 与注册制 2020-08-24、
  北交所 2021-11-15、主板 1996-12-16）现在**阻止**更早日期使用该规则 → UNKNOWN。
- **上市无涨跌幅窗口**必须能可靠排除：
  - `listed_days`（可靠上市依据，来自 `universe.listing_date` 且日线首根 = 上市日）
    且 ≤ 窗口 → **NO_LIMIT**；
  - 否则用该证券自身截至该日的会话数作**下界**：> 窗口 → 可排除 → 继续判 KNOWN；
    ≤ 窗口 → **UNKNOWN**（`无法排除上市无涨跌幅窗口`）；
  - 无任何依据 → **UNKNOWN**（`缺上市日期依据`）。
- **不再把"按板块比例"称为完整历史限价规则**：`describe_limits()["rule_basis"]` 逐条列出
  板块、幅度与生效日，并声明无涨跌幅窗口需可靠上市依据。
- **pre_close 不再跨日替代**：只接受当日自带 `pre_close`；缺失 → UNKNOWN 并记原因
  `当日无可靠参考前收盘价（不做跨日收盘替代）`。实测 `920065.BJ`（无 pre_close 列）
  正确落 UNKNOWN。
- **非法价格**：布尔、零/负价、非有限值、`high < low`、OHLC 越界 → INVALID 隔离。
- **ST 类型**：只接受真布尔；字符串等一律视为无依据 → UNKNOWN，不 `bool()` 强转。

**测试**：`test_listing_window_is_no_limit`、`test_insufficient_listing_basis_is_unknown`、
`test_no_listing_basis_at_all_is_unknown`、`test_no_limit_recorded_per_symbol`、
`test_missing_pre_close_is_unknown_not_substituted`、`test_boolean_price_is_invalid`、
`test_zero_price_is_invalid`、`test_non_boolean_st_is_not_coerced`、
`test_star_before_effective_is_unknown`、`test_gem_registration_switch`。

### 1.3 稀疏事件契约

**原状**：四标志全 null 的行仍写入事件表，与"至少一个 true 才入表"冲突。

**现实现**：事件表**只保留至少一个标志为 true** 的行；
四个全 null 只进 `daily_limit_exceptions`（UNKNOWN/NO_LIMIT/INVALID）与 `daily_limit_scope`。
消费者判定无事件的条件：**在 scope 名单内 + 无异常 + 无事件行**。

**PM 反例实测**（is_st 为 None 的主板）：

```text
事件表行数: 0 (应为 0)
异常表: {'UNKNOWN'}
scope 名单: {'600519.SH'}
```

**NO_LIMIT 逐股可识别**：以异常记录的 `(trade_date, symbol, kind='NO_LIMIT')` 表达，
不只是计数；`no_limit_count` 为派生计数。

**测试**：`test_all_unknown_not_in_event_table`、`test_scope_exposes_processed_symbols`。

### 1.4 只读接口不再建库

**原状**：所有 read/describe 先 `initialize_limits` 再 `catalog`，对不存在目录会创建
`catalog.duckdb`。

**现实现**：
- 新增 `store.read_only_catalog()`（不建目录、不建文件、只读打开）与 `store.existing_tables()`。
- `limit_api` 所有读取先 `_require_ready()` 校验派生表齐备；缺则
  `DataPoolError("LIMIT_NOT_READY", ...)`。
- `describe_limits()` 返回 `ready` 与逐项 capability，不建表。

**PM 反例实测**：

```text
抛出: LIMIT_NOT_READY
目录是否被创建: False (应为 False)
describe: ready=False, 目录存在=False
```

**测试**：`test_reads_do_not_create_database`（含目录不存在断言）、
`test_reads_do_not_modify_existing_pool`（逐文件字节前后一致）、
`test_describe_without_tables_is_not_ready`。

### 1.5 失败/陈旧状态可见

**现实现**：新增 `daily_limit_staleness(trade_date, reason, marked_at)`。
派生在任一日失败时，把**该日及其后尚未重算的计划日**标记 stale；成功发布某日时清除该日标记。
`read_limit_summary` / `read_limit_coverage` 输出 `stale` 与 `stale_reason`；
`read_limit_staleness()` 提供专门查询，消费者可拒绝或降级。

**PM 反例实测**（先发布旧结果 → 修正原始 → 强制派生失败 → 读取）：
`test_failure_marks_stale_and_visible` 断言失败后该日 `stale=True` 且 staleness 非空；
`test_retry_clears_stale` 断言重试成功后 staleness 清空。

### 1.6 范围与公开核对接口

**现实现**：
- `load_scope(root, asset_type)`：`asset_type` 固定支持集 `{"stock"}`，其他抛
  `DataPoolError("SCOPE_UNSUPPORTED")`；**不再吞异常退化**。
- ETF 排除改为读取**日线自身的 `asset_type` 列**（`_read_asset_types`），不依赖 universe。
- **指数/板块类**：从 scope 排除（`out_of_scope_count`），不记为 UNKNOWN。
- 新增公开 `read_limit_scope()`，给出**实际处理证券名单**，消费者可判定
  "无事件证券是否被处理"；`describe_limits()` 增加 scope 字段表。
- 发布以 `trade_date` 为键，同一日只保留一个 scope 的批次（scope_id 记录在批次行）。

**测试**：`test_etf_excluded_by_own_asset_type`、`test_etf_excluded_even_without_universe`、
`test_index_is_out_of_scope_not_unknown`、`test_scope_rejects_unsupported`、
`test_scope_exposes_processed_symbols`。

---

## 2. 生产函数与公开签名

| 函数 | 位置 |
|------|------|
| `resolve_limit_rule(market, code, name, trade_date, st_status=None, listed_days=None, observed_sessions=None)` | `src/tdxman/codec/price_rules.py` |
| `compute_limit_events(root, trade_dates, *, scope_id="stock", asset_type="stock", propagate=True)` | `src/aspool/limit_events.py` |
| `load_scope(root, asset_type="stock")` | 同上 |
| `DataPool.read_limit_summary / read_limit_events / read_limit_exceptions / read_limit_coverage / read_limit_scope / read_limit_staleness / describe_limits / compute_limit_events` | `src/aspool/pool.py` |
| `store.read_only_catalog(root)` / `store.existing_tables(root)` | `src/aspool/store.py` |

存储（沿用 `catalog.duckdb`）：
`daily_limit_events`、`daily_limit_batches`、`daily_limit_exceptions`、
`daily_limit_summary`、`daily_limit_publication`、`daily_limit_scope`、`daily_limit_staleness`。

---

## 3. 测试

```bash
python -m pytest tests/unit/test_limit_events.py tests/unit/test_limit_sync_hook.py -q
# 52 passed
```

```bash
python -m pytest tests/unit/ -q
# 428 passed, 18 subtests passed, 3 failed
```

3 项既存失败（表格浮点渲染）与本轮无关；另 `test_cli_board` 为联网测试，本轮偶发失败
（已在基线 `cb32022` 隔离 worktree 复现同样的连接失败）。
ruff：`All checks passed!`

---

## 4. 真实样例（隔离池，未触碰生产池）

样本：`/tmp/aspool-limit-sample4`（自 `~/.aspool` 复制 8 只，含主板与北交所）。

窗口 2026-09-07 ~ 09-11：

| trade_date | known | unknown | close_limit_up | touched_unsealed_up | max_consecutive_up | sealed_ratio |
|---|---|---|---|---|---|---|
| 09-07 ~ 09-09 | 7 | 1 | 0 | 0 | 0 | null |
| 09-10 | 7 | 1 | 1 | 0 | 1 | 1.0 |
| 09-11 | 7 | 1 | 0 | 1 | 0 | 0.0 |

事件明细：`600180.SH` 09-10 收盘涨停（limit_up=1.62，首板，`consecutive_up=1`）、
09-11 触板未封（limit_up=1.77）。
唯一 UNKNOWN 为 `920065.BJ`，原因 `当日无可靠参考前收盘价`（该标日线无 pre_close 列）。

**该样本只说明现有逻辑在该输入下的输出，不足以证明所有规则正确。**

---

## 5. 未覆盖范围与阻塞

| 项 | 状态 |
|----|------|
| 主板增量尾部 `is_st` 为 null | **阻塞**（上游数据缺口，见上一版 §6.1；不猜测、不回填） |
| 在线同步不提供 `pre_close` | **阻塞**：增量新行缺 `pre_close` 即 UNKNOWN，需上游补齐 |
| `NO_LIMIT` 的生产路径 | 需要 `universe.listing_date`（且日线首根 = 上市日）；当前池无该列，故生产 `no_limit_count` 多为 0 |
| 停牌参与连板的口径 | 仍未定义；停牌与缺行同按会话缺行 → null |
| 全市场回填 / 长历史重算 / 自动调度 / 联网 | 未做，需另行授权 |
| 实时快照、盘中次数、情绪阶段、行业主线 | 后置 |
| 生产池全量性能与新旧等价 | 未验证（仅小样本与 fixture） |

---

## 6. 与契约的对应

| Fundwise 逻辑契约 | 落点 |
|---|---|
| 四标志至少一 true 才入表 | §1.3 |
| 缺席 = 范围内 + 无异常 + 无事件 | §1.3、`read_limit_scope` |
| NO_LIMIT 逐股可识别 | §1.2、§1.3 |
| 规则生效区间与上市依据 | §1.2 |
| 连板会话轴与递增/断板 | §1.1、§1.2 |
| 更正传播与撤销 | §1.1 |
| 失败/陈旧可见、旧数据不得看似最新 | §1.5 |
| 批次关联实际处理范围 | §1.6 |
| 读取只见已发布版本 | `daily_limit_publication` 指针 |
| 不统计触板次数 | 未实现，`describe_limits` 声明 `touched_count=False` |

工程完成不等于策略有效。
