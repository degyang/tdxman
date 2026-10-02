# 日终稀疏涨跌停事件 v3 — 补正交付

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


**日期**：2026-09-18
**依赖树**：HEAD `cb32022`，工作区含本轮及既有未提交改动
**状态**：待 PM 复审
**历史版本**：`daily_limit_events_delivery.md`（v1）、`daily_limit_events_delivery_v2.md`（v2）

本轮不迁移存储、不改 Fundwise、不联网、不全历史重建、不提交 Git、未触碰生产池。

---

## 1. v2 复审五组阻塞的处置

### 1.1 多修正日期之间漏算

**原状**：plan = 请求日 + 最大请求日之后的已发布日，遗漏两请求日之间的会话。

**现实现**（`limit_events.py::compute_limit_events`）：
计划为**从最早请求日起、市场会话轴上的连续后缀**，直到
`max(最晚请求日, 其后已发布的最晚会话)`。两请求日之间的依赖会话必然被包含。

采用 PM 提示的"直接计算受影响后缀"路线，**不做提前退出**——
去掉了 v2 那个不完整的 `before` 比较，正确性优先于省算。

**PM 反例实测**（四会话：非涨停→涨停→涨停→非涨停，第三日连板 2）：

```text
初次:                第三日连板 2
修正第二日未涨停,
调用 [第二日, 第四日]: 第三日连板 1   ← 应为 1
```

**定向 fixture**（`TestIncrementalEqualsFullRecompute`，4 项）：单日、多非相邻日、
跨缺口、仅尾日；每项都与**完整顺序重算的克隆池**逐值比对
（事件 `consecutive_up` 与汇总 `close_limit_up_count`）。

### 1.2 终止比较与失败重试

**处置**：不再依赖"某日事件相等"提前停止，故 v2 的遗漏（未比较 scope/异常/无事件股票状态）
随之消除。重试同样计算完整后缀，不保留无故 stale 尾部。

**测试**：`test_retry_clears_all_stale`（失败后重试 → staleness 清空）。

### 1.3 陈旧标记的时间与覆盖

**原状**：stale 只在逐日循环的 `except` 内写入；范围加载、源读取、前史读取都在 try 之外，
这些阶段失败时旧结果不会被标过期。

**现实现**（时序）：

1. 校验参数 → `initialize_limits`；
2. **先标记请求日** stale（`已请求重算，派生未完成`）——此后任何失败都不会留下"看似新鲜"的旧结果；
3. 加载 scope / 源 / 会话轴；
4. 计划确定后**再标记整个计划** stale（`受影响后缀待重算`）；
5. 逐日发布，**成功发布某日才清除该日标记**。

**前史可靠性**：`_previous_session_states` 通过 `not exists (select 1 from daily_limit_staleness ...)`
排除 stale 日期；配合 `_compute_date` 的"紧邻会话"校验，stale 前日不会被当作可靠递推依据。

**消费约定**：`read_limit_events` / `read_limit_summary` / `read_limit_coverage`
均输出 `stale` 与 `stale_reason`；`describe_limits()["semantics"]["stale_consumption"]`
明确要求单独读事件时也必须检查 stale，或与 coverage 联合消费。

**测试**（`TestStaleCoverage`，5 项）：范围加载阶段失败、源读取阶段失败、
事件读取暴露 stale、stale 前日不得作为前史、重试清空全部 stale。

### 1.4 上市窗口与规则日期化

**`_listed_days` 改为按市场会话计数**：有来源的 `universe.listing_date` 起，
在**会话轴**上计数；仅当会话轴覆盖 `[上市日, trade_date]` 时才返回数值，
否则返回 None → UNKNOWN。避免了"中间缺会话低估日龄 → 误判 NO_LIMIT"。

**`resolve_no_limit_window(market, code, trade_date)`**：窗口按日期适用，
返回 `(窗口交易日数, 出处)`；**该时期规则未确认时返回 None** → UNKNOWN，不套用当前窗口。
适用区间：

| 板块 | 窗口 | 适用起 | 未确认时期 |
|------|------|--------|------------|
| 科创板 688 | 5 | 2019-07-22 | 之前 |
| 创业板 300/301 | 5 | 2020-08-24 | 之前（含开板至注册制） |
| 北交所 43/83/87/92 | 1 | 2021-11-15 | 之前 |
| 主板 60/00 | 5 | 2014-01-01 | 之前 |

**出处**改为可定位的规则位置（交易所交易规则/特别规定章节），
`describe_limits()["rule_basis"]` 同步更新。

**测试**：`test_no_limit_window_is_date_aware`、
`test_gem_before_registration_window_unconfirmed_is_unknown`、
`test_main_board_before_2014_window_unconfirmed_is_unknown`。

**代价（如实记录）**：注册制前创业板与 2014 前主板整体落入 UNKNOWN，历史覆盖下降。
这是"不强行套当前窗口"的直接结果，未为提高覆盖而降级依据。

### 1.5 验证记录与范围收口

- **网络隔离**：`test_limit_events.py` 与 `test_limit_sync_hook.py` 各自加 autouse 夹具，
  把 `socket.socket.connect` / `connect_ex` / `socket.create_connection` 替换为抛错，
  因此这两组测试运行期间**不存在任何网络尝试**。
- **全量运行方法**（显式排除联网测试）：

  ```bash
  python -m pytest tests/unit/ -q \
    --deselect tests/unit/test_cli_board.py::test_board_list_rejects_unknown_type_without_traceback
  # 439 passed, 18 subtests passed, 3 failed, 1 deselected
  ```

  未再泛跑 `tests/unit` 并声称"无联网"：`test_cli_board` 该项会真实发起连接
  （v2 记录中的连接失败即来自它；已在基线 `cb32022` 隔离 worktree 复现同样失败）。
- **scope 收口**：`SUPPORTED_SCOPES` 与 `SUPPORTED_SCOPE_IDS` 均限定 `{"stock"}`，
  传入其他值抛 `DataPoolError("SCOPE_UNSUPPORTED")`，避免不同范围覆写同一日期键。
- Fundwise 完成登记已重写：早期"39 项/8-8 KNOWN/证明规则正确"改为明确的历史轮次引用，
  当前结论以 64 项测试与 7/8 样本为准。

---

## 2. 生产函数与公开签名

| 函数 | 位置 |
|------|------|
| `resolve_limit_rule(market, code, name, trade_date, st_status=None, listed_days=None, observed_sessions=None)` | `src/tdxman/codec/price_rules.py` |
| `resolve_no_limit_window(market, code, trade_date) -> (int, str) \| None` | 同上 |
| `compute_limit_events(root, trade_dates, *, scope_id="stock", asset_type="stock", propagate=True)` | `src/aspool/limit_events.py` |
| `load_scope(root, asset_type="stock")` | 同上 |
| `DataPool.read_limit_summary / read_limit_events / read_limit_exceptions / read_limit_coverage / read_limit_scope / read_limit_staleness / describe_limits / compute_limit_events` | `src/aspool/pool.py` |
| `store.read_only_catalog(root)` / `store.existing_tables(root)` | `src/aspool/store.py` |

存储表：`daily_limit_events`、`daily_limit_batches`、`daily_limit_exceptions`、
`daily_limit_summary`、`daily_limit_publication`、`daily_limit_scope`、`daily_limit_staleness`。

---

## 3. 测试

```bash
python -m pytest tests/unit/test_limit_events.py tests/unit/test_limit_sync_hook.py -q
# 64 passed
```

```bash
python -m pytest tests/unit/ -q \
  --deselect tests/unit/test_cli_board.py::test_board_list_rejects_unknown_type_without_traceback
# 439 passed, 18 subtests passed, 3 failed, 1 deselected
```

3 项失败为既存表格浮点渲染断言，与本轮无关，未在本包修复。
ruff：`All checks passed!`

---

## 4. 真实样例（隔离池，未触碰生产池）

样本 `/tmp/aspool-limit-sample4`（自 `~/.aspool` 复制 8 只，含主板与北交所）。
窗口 2026-09-07 ~ 09-11：`known=7 / unknown=1` 每日稳定；
09-10 收盘涨停 1 家（`sealed_ratio=1.0`），09-11 触板未封 1 家（`sealed_ratio=0.0`）。
事件明细 `600180.SH`：09-10 涨停（limit_up=1.62，首板 `consecutive_up=1`）、
09-11 触板未封（limit_up=1.77）。
唯一 UNKNOWN 为 `920065.BJ`（日线无 `pre_close` 列）。

---

## 5. 未覆盖范围与阻塞

| 项 | 状态 |
|----|------|
| 主板增量尾部 `is_st` 为 null | 阻塞（上游数据缺口） |
| 在线同步不提供 `pre_close` | 阻塞（增量新行即 UNKNOWN） |
| `NO_LIMIT` 生产路径 | 需 `universe.listing_date`；当前池无该列 |
| 注册制前创业板 / 2014 前主板 | window 规则未确认 → UNKNOWN（§1.4 代价） |
| 停牌参与连板的口径 | 未定义；停牌与缺行同按会话缺行 → null |
| 全市场回填 / 长历史重算 / 自动调度 / 联网 | 未做，需另行授权 |
| 实时快照、盘中次数、情绪阶段、行业主线 | 后置 |
| 生产池全量性能与新旧等价 | 未验证（仅小样本与 fixture） |

---

## 6. 与契约的对应

| Fundwise 逻辑契约 | 落点 |
|---|---|
| 四标志至少一 true 才入表 | §v2 1.3（沿用） |
| 缺席 = 范围内 + 无异常 + 无事件 | `read_limit_scope` |
| 规则生效区间与上市依据 | §1.4 |
| 连板会话轴与递增/断板 | §1.1、§1.4 |
| 更正传播与撤销 | §1.1 |
| 失败/陈旧可见、旧数据不得看似最新 | §1.3 |
| 批次关联实际处理范围 | `daily_limit_scope` |
| 读取只见已发布版本 | `daily_limit_publication` 指针 |
| 不统计触板次数 | `describe_limits` 声明 `touched_count=False` |

工程完成不等于策略有效。
