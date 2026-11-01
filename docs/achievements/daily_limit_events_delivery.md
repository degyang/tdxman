# 日终稀疏涨跌停事件 — 交付

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


**日期**：2026-09-18
**依赖树**：HEAD `cb32022`，工作区含本轮及既有未提交改动
**状态**：待 PM 审核

本轮不迁移存储、不改 Fundwise 代码、不主动联网、不新增调度、不提交 Git。

---

## 1. 模块与公开签名

| 文件 | 职责 |
|------|------|
| `src/tdxman/codec/price_rules.py` | 日期感知规则解析（新增 `resolve_limit_rule`） |
| `src/aspool/limit_events.py` | 计算、幂等发布、历史修正 |
| `src/aspool/limit_api.py` | 公开只读接口 |
| `src/aspool/pool.py` | DataPool 门面方法 |
| `src/aspool/tdx_online.py` | sync/update 成功后挂接增量派生 |
| `src/aspool/daily_storage.py` | `merge_daily` 新增 `changed_dates` 出参 |

### 1.1 规则解析

```python
resolve_limit_rule(market, code, name, trade_date, st_status=None) -> LimitRuleResult
```

`rule is None` 即 UNKNOWN，附 `reason`。规则生效区间：

| 板块 | 幅度 | 生效起点 | 依据 |
|------|------|----------|------|
| 科创板 688 | ±20% | 2019-07-22 | 科创板交易特别规定 |
| 北交所 43/83/87/92 | ±30% | 2021-11-15 | 北交所交易规则 |
| 创业板注册制后 | ±20% | 2020-08-24 | 创业板交易特别规定 |
| 创业板注册制前 | ±10%/ST ±5% | 2009-10-30 | 需 ST 依据 |
| 主板 | ±10%/ST ±5% | 1996-12-16 | 需 ST 依据 |

**ST 为三态输入**：`True`/`False` 为已知，`None` 为无依据。无历史 ST 来源时传 `None`，不得用当前名称回填。ST 不改变幅度的板块（科创板/北交所/创业板注册制后）不受此限制。

### 1.2 公开读取（DataPool 方法）

```python
read_limit_summary(*, start=None, end=None) -> DataFrame
read_limit_events(*, trade_date=None, start=None, end=None, symbols=None) -> DataFrame
read_limit_exceptions(*, trade_date=None, start=None, end=None, symbols=None) -> DataFrame
read_limit_coverage(*, start=None, end=None) -> DataFrame
describe_limits() -> dict
compute_limit_events(trade_dates, *, scope_id="stock", asset_type="stock") -> list[str]  # 独立重算入口
```

沿用 DataPool 参数/错误风格；`symbols` 接受规范与旧点号输入，输出恒为 `code.market`。
错误码：`INVALID_ARGUMENT`、`DAILY_NOT_FOUND`（沿用 `DataPoolError`）。

---

## 2. 物理路径与发布方式

全部落在现有 `catalog.duckdb`，不新增数据库服务；不重复存 OHLCV。

| 表 | 内容 |
|----|------|
| `daily_limit_events` | 逐证券逐日事件；主键 `(trade_date, symbol, batch_id)` |
| `daily_limit_batches` | 批次：scope、规则版本、计算时间、已知/未知/无约束/异常数量、状态 |
| `daily_limit_scope` | 该批次实际处理的证券名单（仅存数量不足以判断个股是否处理） |
| `daily_limit_exceptions` | UNKNOWN / INVALID 及原因、受影响字段 |
| `daily_limit_summary` | 日汇总 |
| `daily_limit_publication` | 发布指针 `trade_date -> batch_id` |

**发布**：单事务内清理该日旧行 → 写批次/事件/异常/汇总/范围 → 切换发布指针 → 提交。
公开读取一律 join `daily_limit_publication`，因此**只见唯一已发布版本**，不混半更新批次。

**派生失败**：`update_daily` 在原始日线提交后挂接派生；派生异常被捕获并记入报告的 `limit_events.status="failed"`，**不影响原始批次**，可用 `compute_limit_events` 重试。

---

## 3. 字段与语义

事件表四个标志 `close_limit_up` / `close_limit_down` / `touched_limit_up` / `touched_limit_down`：
`true` 确定发生，`false` 确定未发生，`null` 无法确认。至少一个为 `true` 才入表；
四个全为 `null`（规则/参考价/会话缺失）也入表以便定位未知。

- **缺席**：在该批次 `daily_limit_scope` 内、无异常且无事件行 = 确定无涨跌停事件。
- **触板未封**：`touched_limit_up=true AND close_limit_up=false`，派生不冗余存储。
- **一字板**：由 OHLC 与限价判定，不额外持久化；收盘涨停包含一字板。
- **`consecutive_up`**：确定当日未涨停为 `0`；涨停为上一会话已确认值 +1；
  前史不足、会话缺行、未纳入上批次范围均为 `null`。
- **`sealed_ratio`**：`收盘涨停家数/(收盘涨停家数+触涨停未封家数)`；分母为 0 时为 `null`。
- **`max_consecutive_up`**：已知样本最大值，`max_consecutive_up_note` 说明是否存在连板未知的个股。
- **未统计**：触板次数、开板次数、回封次数（用户已明确不需要，日线 OHLC 也无法还原）。

**会话轴**：取池内实际出现的交易日（有来源的会话），并集为市场会话轴。
某证券在某会话缺行时不跨越，事件与连板均记 `null`。

---

## 4. 测试

```bash
python -m pytest tests/unit/test_limit_events.py tests/unit/test_limit_sync_hook.py -q
# 39 passed
```

`test_limit_events.py`（34 项）：规则与生效日期、精确价格边界、一字板、触板未封、
同日两侧触及、非法 OHLC 隔离、规则 UNKNOWN、参考价缺失、首板/续板/断板/周末/
缺会话/截断、重复计算幂等、撤销旧事件、历史修正传播、派生失败重试、
零分母封板率为 null、封板率计算、规范 symbol 输出、旧输入兼容、窗口过滤、未发布不可见。

`test_limit_sync_hook.py`（5 项，mock 数据源、无网络）：
派生失败不回滚原始日线、失败在报告中可见、独立入口可重试、
成功时按 `changed_dates` 触发派生、无变更不重算。

回归：

```bash
python -m pytest tests/unit/ -q
# 415 passed, 18 subtests passed, 3 failed
```

3 项失败为既存（表格浮点渲染），已用隔离 worktree 在 `cb32022` 复核确认，与本轮无关。
ruff：`All checks passed!`

---

## 5. 真实样例抽查（隔离池，未触碰生产池）

从现有池 `~/.aspool` 读取，复制到 `/tmp/aspool-limit-sample*` 后计算，**未修改生产池**。

### 5.1 历史窗口（`is_st` 有依据）— 8 只主板/北交所，5 个会话

| trade_date | known | unknown | close_limit_up | touched_unsealed_up | max_consecutive_up | sealed_ratio |
|---|---|---|---|---|---|---|
| 2026-09-07 | 8 | 0 | 0 | 0 | 0 | null |
| 2026-09-08 | 8 | 0 | 0 | 0 | 0 | null |
| 2026-09-09 | 8 | 0 | 0 | 0 | 0 | null |
| 2026-09-10 | 8 | 0 | 1 | 0 | 1 | 1.0 |
| 2026-09-11 | 8 | 0 | 0 | 1 | 0 | 0.0 |

逐股明细（事件表）：

| trade_date | symbol | close_limit_up | touched_limit_up | limit_up_price | consecutive_up |
|---|---|---|---|---|---|
| 2026-09-10 | 600180.SH | true | true | 1.62 | 1 |
| 2026-09-11 | 600180.SH | false | true | 1.77 | 0 |

可核对：09-10 收盘涨停（首板，`consecutive_up=1`，封板率 1/(1+0)=1.0）；
09-11 触板未封（`sealed_ratio` 0/(0+1)=0.0）。与原始 OHLC 一致。

### 5.2 增量窗口（最近 5 会话）— 6 只北交所

`known=6/unknown=0`，窗口内无涨跌停事件（北交所 30% 幅度，样本期内未触发）。

---

## 6. 阻塞与覆盖边界（如实报告）

### 6.1 主板增量尾部无 ST 依据 → UNKNOWN（阻塞）

实测 `600105.SH` 等主板标的：历史行 `is_st` 有值（`False`，个别日期 `True`），
但**最近 5 个会话（在线增量写入的行）`is_st` 为 null**。按契约不得用当前名称回填，
故这些日期一律 UNKNOWN。

实测同一批标的、同一套代码：
- 历史窗口（`is_st` 有值）→ `known=8, unknown=0`，正常产出事件；
- 增量窗口（`is_st` 为 null）→ `known=2, unknown=6`，主板全部 UNKNOWN。

**结论**：规则与计算路径正确，主板增量覆盖受上游 `is_st` 缺失阻塞，
需要上游在在线日线写入时补齐 ST 依据，或另行提供历史 ST 来源。**本轮不猜测、不回填。**

### 6.2 未实现

| 项 | 状态 |
|----|------|
| 停牌参与连板的口径 | **未定义**：停牌与缺行当前同按"会话缺行 → null"处理，尚未区分 |
| 全市场回填 / 长历史重算 | 未做 |
| 自动调度、联网同步 | 未做（需另行授权） |
| 实时快照、盘中事件次数、情绪阶段、行业主线 | 未做（后置） |
| 指数/板块类 | 明确排除（NO_LIMIT 之外的 out-of-scope） |
| ETF | 明确排除（不进入股票事件批次） |

### 6.3 未验证

- 生产池全量上的新旧等价与性能；
- 跨年/分年存储布局下的长历史修正传播（仅在小样本与 fixture 上验证）。

---

## 7. 与契约的对应

| Fundwise 逻辑契约 | 落点 |
|---|---|
| `daily_limit_events` 字段与唯一键 | §2、§3 |
| 四标志至少一 true 才入表 | §3 |
| `daily_limit_batches` 含范围与状态 | `daily_limit_batches` + `daily_limit_scope` |
| `daily_limit_exceptions` | §2 |
| `daily_limit_summary` 与封板率分母 | §3 |
| 连板递推与历史更正 | §3、§4 测试 |
| 读取只见已发布版本 | §2 发布指针 |
| 不统计触板次数 | §3 |

工程完成不等于策略有效。
