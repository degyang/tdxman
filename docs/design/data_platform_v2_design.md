# asPool 数据域分层设计

日期：2026-09-29。状态：实施中。需求基线见
[数据域分层需求](data_platform_v2_requirements.md)。本文说明物理结构、表职责、一致性和迁移方案；
实际完成范围见 [实施记录](../implements/platform_v2_execution.md)，当前生产库不能只按文件存在就推断为
已激活。

## 1. 设计决策

采用三个层次：

```text
来源层        catalog + stocks + indices + etfs + fundamentals
参考/派生层  adjustments + features
缓存层        snapshots
```

来源层保留不可从本地可靠重建的数据；参考/派生层保存确定性计算结果；缓存层允许随时删除重建。
股票日级派生接近股票日线的行数，和原始库分离后，其结构扩展、全量重建和文件压缩不再影响原始
行情的恢复点。

## 2. 物理文件和表

### 2.1 catalog.duckdb

```text
securities
  PK(symbol)
  code, market, asset_type, name, active
  listing_date, delisting_date, updated_at

security_calendar
  PK(trade_date)
  is_open, source

pool_metadata
  PK(key)
  value, updated_at
```

目录是当前有效证券集合；一次市场目录读取不完整时不发布该市场的部分结果。目录不保存日级行情、
派生指标或基本面报告。`pool_metadata.layout_version` 是 DataPool 的布局选择标记；迁移完成前保持
当前值，切换时在一次 catalog 事务中更新。缺少该键时按当前生产布局读取，不能靠文件是否存在猜测。

### 2.2 三个原始行情库

```text
stocks.sqlite
├── daily_bars
├── corporate_actions
└── dataset_state

indices.sqlite
├── daily_bars
└── dataset_state

etfs.sqlite
├── daily_bars
├── corporate_actions
└── dataset_state
```

文件名已经提供资产命名空间，因此表内继续使用 `daily_bars`，避免重复的
`stock_stock_daily_bars` 命名。公共 API 使用 `stock-bars / index-bars / etf-bars` 区分。

股票和 ETF 的 `daily_bars` 保存来源未复权数据：

```text
symbol, trade_date
open, high, low, close, volume, amount
source_pre_close
source_turnover_rate, source_vol_ratio
source_pct_chg, source_amplitude
source_total_share, source_float_share
name, name_as_of
source, updated_at
```

同一字段同时存在来源值和计算值时，原始表只保存来源值。统一生效值进入对应 Enriched 表。
单位归一属于解码规范化，不属于复权；例如报价手数转换成股后仍是来源成交量。

指数 `daily_bars` 保存来源 OHLCVA、`up_count/down_count` 和 `breadth_status`。指数没有公司行为或
复权因子。

`corporate_actions` 只保存来源事件：

```text
symbol, effective_date, source, source_key, category
payload_json, fetched_at, updated_at
PK(symbol, effective_date, source, source_key)
```

现有 `record_kind=factor_anchor/factor` 不继续与原始事件共表。可验证的外部因子锚点迁入
`adjustments.sqlite`，来源字段保留其外部身份。

`dataset_state` 每个数据集只保存当前状态：

```text
dataset, revision, max_date, updated_at
```

它用于判断派生是否落后，不保存发布批次或历史修订流水。一次原始写事务只在业务值实际变化时
增加 revision。

### 2.3 fundamentals.sqlite

```text
stock_financial_reports
  PK(symbol, period_end, source)

stock_shareholder_counts
  PK(symbol, as_of_date, source)

dataset_state
```

`stock_financial_reports` 使用固定类型的宽表保存 TDX finance 协议的稳定字段：报告期总股本、
流通股本、资产、负债、收入、利润、现金流、EPS 和每股净资产等。`source_hash` 用于识别同键修订，
`fetched_at` 表示采集时间，`published_at` 只有来源明确提供时才填写。

`stock_shareholder_counts` 保存：

```text
symbol, as_of_date, shareholder_count
source, source_report_date, published_at, fetched_at, updated_at
```

TDX 当前以 `finance.updated_date` 作为 `as_of_date/source_report_date`，以
`finance.gudong_renshu` 作为 `shareholder_count`。以后接入 F10 或其他历史来源时追加来源行，
不覆盖不同日期的记录。

当前 `catalog.duckdb:fundamental_snapshots` 没有可靠报告期，不能直接改名迁移为财务报告。迁移时
重新获取 `get_finance_info()`；原快照仅用于字段差异审计和恢复，不把采集日期写成报告期。

### 2.4 adjustments.sqlite

```text
stock_adjustment_factors
etf_adjustment_factors
adjustment_state
```

因子表键为 `(symbol, effective_date)`，保存：

```text
event_factor, cumulative_factor
valid_from, valid_through
factor_basis, source, input_hash
algorithm_version, updated_at
```

`input_hash` 覆盖使用的公司行为、因子锚点和必要前收盘；`algorithm_version` 固定因子方向、单位和
同日事件合并规则。事件或算法变化时，以受影响日期为起点重建有限后缀。

前复权读取将日期因子归一到查询终点或显式基准；后复权读取归一到历史基准。调整只发生在查询
投影或稳定派生计算中，不写回原始日线。

### 2.5 features.sqlite

```text
stock_daily_features
index_daily_features
etf_daily_features
market_regime_features
feature_state
```

三类资产分别建表，避免大量不适用列和规则混用。

`stock_daily_features` 的稳定核心包括：

```text
symbol, trade_date
trading_status, effective_pre_close, is_st
pct_chg, amplitude, turnover_rate, vol_ratio
limit_status, limit_reason
limit_up_price, limit_down_price
touch_limit_up, close_limit_up, touch_limit_down, close_limit_down
prior_consecutive_up, consecutive_up, streak_known
ma20_adjusted, above_ma20
raw_revision, factor_revision, algorithm_version, updated_at
```

对外的股票 Enriched 逻辑视图还包含原始 OHLCV 和前复权 OHLCV。原始值从 `stocks.daily_bars`
按键联结，避免在 `features.sqlite` 复制 1,600 万行原始价格；前复权值由日期因子投影，只有性能
验收证明读取时投影仍是主要瓶颈时才物化。无论是否物化，查询都必须在同一 snapshot 中核对
`raw_revision` 和 `factor_revision`。停牌、缺失和无效日期保留状态事实，但从单证券技术指标窗口排除。

`index_daily_features` 包括收益、均线、趋势、波动率和来源广度比例；`etf_daily_features` 包括收益、
换手/量比和复权滚动指标。资产特有指标只进入对应表。

大部分技术指标不进入上述稳定宽表。`snapshots.sqlite` 的按需计算器按 `symbol` 分组，提供
MA/EMA、MACD、多周期动量、60 日高低点、BOLL、ATR14、年化波动率、KDJ、RSI、多周期均量、
量比和对应信号；需要板块相对强弱时再联结板块指数，计算 3/10/30 日偏离。股票、ETF 和指数使用
不同的 feature set 与计算入口。首次构建遍历各自原始库的全部标的，日常新增只处理新交易日；
普通修订传播到受影响窗口后缀，因子修订只使对应标的的复权字段及依赖快照失效。

`market_regime_features` 延续现有 `(frequency, period_key, scope)` 主键，迁入现有日汇总字段并明确
质量分母。D writer 优先迁移；W/M 在独立验收前保持未就绪。该表不增加模型标识、权重或评分。

`feature_state` 保存当前物化状态：

```text
dataset, scope_key
raw_revision, factor_revision
algorithm_version, status
affected_from, affected_through, updated_at
```

`status` 为 `READY / DIRTY / FAILED`。它是当前依赖水位，不是历史版本、发布表或批次平台。

### 2.6 snapshots.sqlite

```text
snapshot_catalog
  PK(snapshot_id)
  UNIQUE(feature_set, feature_version, as_of_date, asset_type,
         scope, params_hash, input_version)

snapshot_rows
  PK(snapshot_id, entity_id)
  payload_json
```

一次快照只在请求参数和输入版本都一致时复用。删除过期快照不影响任何权威或稳定派生数据。
需要频繁 SQL 过滤、排序的特征从 JSON 快照提升为正式列或专表。

## 3. 更新和一致性

网络读取发生在数据库事务外。一次正常股票更新按以下依赖执行：

```text
读取目录与当日未复权行情
→ 识别公司行为候选并读取事件
→ stocks.sqlite 提交原始变化并增加 revision
→ adjustments.sqlite 更新受影响因子
→ features.sqlite 更新逐股后缀和市场日期
→ feature_state 标记匹配当前输入 revision 的 READY
```

SQLite WAL 不提供这些独立文件之间可依赖的跨库原子性。读取端先比较来源库、因子库和
`feature_state` 的当前 revision：完全匹配才返回派生结果。原始提交后进程中断时，原始数据仍可读，
派生接口返回 `DERIVED_NOT_READY`；显式运行 `aspool platform reconcile` 从本地权威库恢复。

因子后缀在有界小批内提交（快路径多只共享一次市场截面，权威事件读取逐只隔离），每个小批与
对应的分层镜像在同一池写锁内完成。写入路径在锁内先检查上一次镜像提交完整（`feature_state`
全部 `READY` 且 revision 对齐），不完整则以 `DERIVED_NOT_READY` 拒绝本次有界写入并提示
`aspool platform reconcile`；后续局部更新不得把待恢复状态改写为已完成，也不能用不相关切片
证明缺失行已恢复。无日期的纯镜像补跑不会把 `DIRTY` 改成 `READY`；日更内部刚提交的无派生变化
事务（如非价格类公司行为）在给出精确 `previous_raw_revision` 且旧状态为 `READY`、版本匹配时，
允许只把新 revision 带入镜像状态而不改变 status。日更路径不自动全量重建镜像。

受影响范围至少满足：

- 原始日线变化：该证券当日、滚动窗口后缀、连板依赖后缀和对应市场日期。
- 公司行为或因子变化：因子有效后缀、跨越该区间的复权滚动窗口和对应市场日期。
- 指数变化：对应指数特征及引用该指数的 Regime 日期。
- 股票截面变化：对应 scope 的 Regime 行。
- 基本面变化：只使显式依赖该报告或股东人数的快照失效。

### 3.1 校验分层

`aspool platform verify`（`verify_platform_v2(root, deep=False)`）是不修改数据的只读校验，
提供两级：

- **浅层**（默认，毫秒级）：行数比对 + `feature_state` 状态/revision 一致性检查。浅层不做内容
  比对，报告中的 `content_equal=null` 表示未校验，而不是内容一致；`prepare`、`status` 返回此级别，
  日更镜像路径的写入门闩使用同一套状态/revision 检查。
- **深层**（`aspool platform verify --deep`，秒~分钟级）：在浅层基础上增加 `ATTACH + EXCEPT`
  全表内容比对（只读），给出 `extra`/`missing` 行数与布尔 `content_equal`。`reconcile` 和
  `activate` 的门槛使用此级别做完整验收。

日常增量更新只影响数千行（7000 股 × 1 天），浅层适合作为快速冒烟检查，但行数相等可以由
遗漏行与多余行相互抵消，不能证明内容正确；内容完整性只能靠深层 EXCEPT 验收（如灾难恢复后）。
浅层结果不能充当全内容一致的证据。

### 3.2 恢复路径

`reconcile_platform_v2(root)` 是灾难恢复入口，顺序执行：

1. 重建 `adjustments.sqlite`（从 `stocks.sqlite.corporate_actions` 重新计算）。
2. 重建 `features.sqlite`（`ATTACH + INSERT SELECT` 全量复制，不经过 Python 内存）。
3. 运行深层 `verify` 做完整内容验收。

镜像中断时保留最后一次已提交状态：可能停在 `DIRTY`，也可能停留在旧 revision 的 `READY`
（状态滞后），`reconcile` 按 revision 对齐识别并修复。恢复必须由显式
`aspool platform reconcile` 触发：写入前的镜像完整性检查会拦住后续有界写入，等待恢复期间
局部更新不能覆盖待恢复状态。恢复后公开读取自动通过 revision 匹配返回最新派生结果。

## 4. CLI 编排

日常 `update --type all` 的目标顺序：

1. 刷新股票和 ETF 目录。
2. 更新指数和交易日历。
3. 下载股票当日未复权行情，选择公司行为候选并更新因子。
4. 更新 ETF 未复权行情及可用因子。
5. 计算股票、指数、ETF 稳定 Enriched。
6. 计算日级 `market_regime_features`。
7. 输出各数据域输入、变化范围、派生状态和失败块。

基本面不属于这个流程，只能显式运行 `aspool fundamentals update`。快照也不属于每日全量编排。

`sync` 的窗口选择顺序：显式 `--start/--end` 优先；否则读取 `--count`，缺省 10 个已完成交易日。
每个资产根据自己的日历和终态找缺口。下载到的股票、ETF 价格保持未复权，随后只刷新受影响派生。

## 5. 公开读取

`DataPool(root)` 是物理布局的唯一兼容层。它先读取布局标记，再将公开读取路由到当前布局或目标
布局；消费者不 attach 数据库，也不传具体文件路径。迁移期允许旧布局和目标布局并存，但一次
DataPool 读取只能选择其中一个完整布局，不能逐表混用。

新增能力保持面向数据集：

```python
read_daily(..., adjust="none|qfq|hfq")
read_index_daily(...)
read_etf_daily(..., adjust="none|qfq|hfq")
read_security_daily(...)
read_market_summary(...)
read_fundamental_reports(...)
read_shareholder_counts(...)
```

现有 Fundwise 调用面保持原方法签名、缺省值、字段和异常语义：

```text
describe / status / describe_limits
read_research_daily / read_index_daily / read_trading_calendar
read_market_daily / read_market_summary / stock_snapshot
read_limit_summary / read_limit_events / read_limit_coverage / read_limit_exceptions
iter_limit_events_with_amount
```

其中 `read_market_daily()` 继续读取日级 `all_stocks` 公共特征，是
`market_regime_features` 的兼容门面。表名变化不进入接口。`read_limit_summary()` 和
`read_limit_events()` 分别投影市场公共特征和逐股涨跌停特征；`read_limit_coverage()` 根据证券目录、
交易终态和有效分母投影覆盖列；`read_limit_exceptions()` 投影当日 `MISSING/INVALID` 证券。后两者
没有独立物理表，也不恢复批次、stale 或 publication 写入流程。旧契约需要的批次字段只能返回
空值或由契约定义的兼容值，不能把 `dataset_state.revision` 冒充发布批次。

`iter_limit_events_with_amount()` 仍在 SQL 层按交易日关联逐股事件与同日原始 `amount`，保留
`batch_days`、`max_rows`、DuckDB 内存和线程边界及未完整消费检测。它不能先物化全历史再分批返回。

`stock_snapshot()` 在同一个 context 中打开只读事务，并验证 `stocks.dataset_state`、
`adjustments.adjustment_state` 与 `features.feature_state` 完全匹配。reader 至少保持现有
`read_market_daily()` 行为。进入和退出快照时再次核对 revision；不一致时回滚并抛出兼容的
`DataPoolError`，从而避免跨文件提交窗口产生混合版本。

返回元数据声明数据源、单位、调整方式、基准日、算法版本和派生就绪状态。已有读取保持当前
`DataFrame.attrs`；新键只能追加。普通读取只读本地文件，不触发网络或派生；快照使用单独的显式
计算入口。

## 6. 存量迁移

迁移按可恢复的阶段执行：

1. 备份并记录当前四个数据库的大小、摘要、schema、行数和日期范围。
2. 创建 `fundamentals.sqlite`、`adjustments.sqlite`、`features.sqlite` 和 `snapshots.sqlite` 临时库。
3. 从 `stocks.sqlite.corporate_actions` 分离原始 event、外部 anchor 和计算 factor。
4. 迁移 `daily_features` 到 `stock_daily_features`，迁移 `market_daily_summary` 到
   `market_regime_features`。
5. 迁移 ETF 因子；指数、ETF 稳定 Enriched 按目标算法初始化。
6. 逐股调用 `get_finance_info()` 建立带报告日期的财务和股东人数首期数据。
7. 使用独立参考计算核对未复权日线、因子、前后复权样本、涨跌停、MA20 和市场汇总。
8. 让 DataPool 兼容层读取目标布局，对同一输入运行旧/新布局契约对比和 Fundwise 消费者测试。
9. 兼容门通过后切换布局标记，再从 `stocks.sqlite/etfs.sqlite/catalog` 退役旧派生表。

迁移期间旧库保持可回滚。只有新库、公开 API、CLI 和消费者全部验收后，才删除旧表并执行空间回收。

兼容门至少覆盖 Fundwise 当前使用的股票筛选字段、环境字段、上证指数、交易日历、日级市场特征、
涨跌停事件及同日成交额。对比项包括方法参数、列及其顺序和类型、值、排序、单位、attrs、空结果、
非法字段、日期边界、派生未就绪和资源上限。目标布局特有的新能力不参与旧接口的等值比较。

## 7. 当前实现差异

截至 2026-09-29，影子迁移和布局切换机制、独立基本面、`sync --count`、股票/ETF 读取时复权、
跨库 revision 检查及 Fundwise 兼容投影已经实现。首次激活前，`stocks.sqlite` 仍保留旧派生表，
公开读取继续走布局 1；激活后走 `features.sqlite` 和 `adjustments.sqlite`。旧表在稳定运行和回滚期
结束前不删除。

指数/ETF 稳定 Enriched、独立 derive 命令、W/M writer 和按需 snapshot 命令尚未实现；
`snapshots.sqlite` 当前只建立了 schema。跨设备增量同步仍不在本轮范围。精确执行证据和测试前提见
[实施记录](../implements/platform_v2_execution.md)。

## 8. 跨设备同步备忘（非当前计划）

未来可以只提取 `catalog`、三个原始行情库、`fundamentals` 和 `adjustments` 的逻辑增量；目标设备
局部重算 `features`、Regime 和 snapshots。设备复制应使用独立 `aspool replica` 语义，不能复用
行情 `aspool sync`。

本节只保留设计约束。当前实施范围不包括 replica CLI、逻辑增量格式、游标、变更日志、SSH/rsync
传输、接收端安装或多主冲突解决；这些内容不得进入本轮任务验收。
