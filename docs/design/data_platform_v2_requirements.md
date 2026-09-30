# asPool 数据域分层需求

日期：2026-09-29。状态：实施中。影子迁移、基本面分库、同步窗口和兼容读取已实现，实际进度见
[实施记录](../implements/platform_v2_execution.md)。当前生产结构与可用命令仍以
[生产数据流契约](production_data_flow_contract.md)为准；本文冻结下一阶段的数据边界和验收口径，
尚未通过激活门槛的目标能力不描述成已经上线。

## 1. 目标

asPool 将权威输入、稳定派生、Regime 公共特征和按需快照分开管理：

1. 股票、指数和 ETF 的来源行情分别落库，日线始终保存未复权值。
2. 原始公司行为与本地计算的复权因子分开；前复权、后复权在读取时计算。
3. 股票、指数和 ETF 的稳定 Enriched 按资产独立建表，不扩展成跨资产万能表。
4. Regime 公共维度单独建表；Fundwise 保存模型、权重、评分和阶段标签。
5. 基本面是独立、低频、手动更新的数据域，股东人数保存为历史序列。
6. 参数变化快、按需触发的扩展维度进入可清理的快照缓存，不持续增加稳定日表的列。
7. 原始数据变更只重算实际受影响的派生范围；读取不能返回已知过期的派生结果。

## 2. 生产数据域

目标数据根仍为 `tdxman/data/`：

```text
data/
├── catalog.duckdb       证券目录和交易日历
├── stocks.sqlite        股票未复权日线及原始公司行为
├── indices.sqlite       指数原始日线
├── etfs.sqlite          ETF 未复权日线及原始公司行为
├── fundamentals.sqlite  财务报告和股东人数历史
├── adjustments.sqlite   股票、ETF 日期复权因子
├── features.sqlite      三类资产稳定 Enriched 和 Regime 公共特征
└── snapshots.sqlite     按需计算的扩展快照
```

运行报告、恢复材料和临时文件继续放在 `.local/`，不得进入生产数据根。

| 数据域 | 权威性 | 典型更新 | 是否可重建 |
|---|---|---|---|
| catalog | 来源事实 | 每日目录、指数交易日 | 否 |
| stocks / indices / etfs | 来源事实 | 每日 update、缺口 sync | 否 |
| fundamentals | 来源事实 | 手动低频更新 | 否 |
| adjustments | 有版本的确定性派生 | 公司行为变化后局部更新 | 是 |
| features | 确定性派生 | 原始数据提交后局部更新 | 是 |
| snapshots | 参数化缓存 | 查询或显式命令触发 | 是 |

## 3. 原始行情要求

### 3.1 未复权原则

`aspool update` 和 `aspool sync` 写入股票及 ETF 日线的价格必须是来源返回的未复权价格。
不得在原始日线中保存另一套前复权或后复权 OHLC。

来源直接返回的昨收、换手率、量比等可以作为来源观测保留，并记录来源。由本地补算得到的
有效换手率、量比、涨跌幅和振幅属于 Enriched；计算值不能覆盖来源观测。

指数日线不参与证券复权。指数来源提供的上涨、下跌家数必须带可用状态；来源不支持时不能把
`0/0` 解释为真实广度。

### 3.2 update 与 sync

`update` 获取当日行情；`sync` 检查有限窗口并填补缺口。目标 CLI：

```sh
aspool update --type stock|index|etf|all

# 缺省检查最近 10 个已完成交易日
aspool sync --type stock|index|etf|all

# 检查最近 30 个已完成交易日
aspool sync --type stock --count 30

# 明确范围继续保留
aspool sync --type stock --start 2026-08-01 --end 2026-08-31
```

`--count` 缺省为 10，表示交易日，不是自然日；它与 `--start/--end` 互斥。未显式包含当日时，
窗口截止到最近一个已完成交易日，当日由 `update` 负责。`sync` 先检查每个证券的日期终态，
只请求缺行、`MISSING` 或 `INVALID`，不得把有限填坑变成固定窗口全量重写。

明确日期范围必须继续支持。`--status missing|invalid` 用于进一步缩小修复对象。

## 4. 公司行为和复权因子

TDX `get_xdxr_info` 返回公司行为事件，不返回可以直接当作前复权或后复权 K 线的数据。
原始公司行为保存在对应资产原始库；本地使用公司行为、因子锚点和未复权价格计算日期因子。

公司行为是稀疏、逐证券的数据，不采用 `sync --type corporate-actions --count N`。日常更新在行情
下载后根据以下条件选择候选证券：

- 来源昨收与上一真实未复权收盘不一致；
- 因子覆盖没有达到目标日期；
- 上次公司行为读取失败；
- 新上市或显式指定修复；
- 轮转审计选中的证券。

维护入口使用独立语义：

```sh
aspool actions update [--symbol 000001.SZ]
aspool actions audit --limit 500
```

事件变化后只重算受影响的因子区间、滚动指标后缀、连板后缀和相关市场日期。

公开日线读取支持：

```python
pool.read_daily(..., adjust="none")
pool.read_daily(..., adjust="qfq")
pool.read_daily(..., adjust="hfq")
```

`none` 返回未复权价格；`qfq` 以查询终点或明确基准日归一；`hfq` 以历史基准归一。调整方式、
基准日和因子定义必须进入返回元数据。

## 5. 稳定 Enriched

Enriched 是增强后的日 K 数据层，面向全部已同步标的，不是只为单只股票临时计算的指标集合。
任何时序计算都必须先按 `symbol` 分组并按交易日排序；一个标的的窗口不得读取另一个标的的价格。
`NO_TRADE / MISSING / INVALID` 不制造平价 K 线，也不进入价格和成交量滚动窗口。

稳定派生按资产建表：

- 股票：未复权价格引用、前复权 OHLCV、有效昨收、日期 ST、交易状态、有效涨跌幅/振幅/换手率/
  量比、涨跌停、炸板和连板。MA20 只因当前市场广度的稳定消费而物化。
- ETF：未复权价格引用、前复权 OHLCV、有效收益、振幅、换手率/量比；溢价率等字段只有在来源
  可靠时加入。
- 指数：收益、均线、趋势、波动率和来源广度比例；不包含复权因子。

逻辑 Enriched 读取可以联结原始表返回未复权价格，不要求在 `features.sqlite` 重复保存同一份原始
OHLCV。前复权列是否物化由读取成本验收决定；无论物理上由因子投影还是持久化，其值都必须绑定
`factor_revision`，因子变化后不得继续返回旧值。

以下技术指标和信号缺省按需计算并进入版本化 snapshot，不持续扩宽稳定日表：

- 均线：MA5/10/20/30/60、EMA5/10/20/30/60；
- 趋势与动量：MACD、5/10/20/30/60 日动量、60 日高低点；
- 波动与摆动：BOLL、ATR14、20 日年化波动率、振幅、KDJ、RSI6/14/24；
- 量价：5/10 日均量、5 日量比；
- 信号：均线或 MACD 金叉死叉、均线或布林突破、60 日新高低、放量、涨跌停和炸板；
- 相对强弱：相对对应板块指数的 3/10/30 日偏离值。

某个字段只有在定义稳定、存在日常消费且需要按列过滤、排序或市场截面聚合时，才从 snapshot
提升为正式 Enriched 列。股票、ETF、指数使用各自的 Enriched 表、计算器和缓存命名空间，不混表。

首次生成覆盖本地原始日线中的全部已同步标的；新增交易日为当日全部目录内标的做增量计算；普通
日线修订只重算该标的受影响后缀；只有除权因子变化时，才重算受影响标的的复权字段及依赖窗口。
相对板块指标在运行时按需联结对应板块指数，不写回原始日线。

原始库与派生库分开后不依赖跨 SQLite WAL 原子提交。派生表保存当前输入版本和算法版本；
输入版本不一致时，接口返回 `DERIVED_NOT_READY`，不得把旧结果作为最新结果返回。

显式维护入口：

```sh
aspool derive --type stock|index|etf|regime --count 30
aspool derive --type stock --start 2026-08-01 --end 2026-08-31
```

日常 `update/sync` 成功后自动运行实际受影响范围；`derive` 用于修复、算法升级和验收。

## 6. Regime 公共特征

asPool 的 Regime 职责是提供模型基础数据，不运行 Fundwise 的评分模型。
`market_regime_features` 至少提供：

- 上涨、下跌、平盘数量及每两个百分点的收益分布；
- 总成交额、有效金额样本和平均换手率；
- 涨停、跌停、触板、炸板和封板率；
- 首板、二板以上、最高连板和梯队；
- 晋级候选、成功数、排除原因和晋级率；
- MA20 有效样本、站上 MA20 数量和比例；
- 输入未知、无效和有效分母；
- Fundwise 所需的指数公共输入引用。

主键保留 `(frequency, period_key, scope)`。当前实施优先 D；W/M writer 完成前必须明确返回
`FREQUENCY_NOT_READY`。模型模板、权重、评分、阶段标签、主线和用户配置继续由 Fundwise 保存。

## 7. 基本面

基本面从 `update/sync --type all` 移除，由手动低频命令维护：

```sh
aspool fundamentals update --source tdx
aspool fundamentals update --source tdx --symbol 000001.SZ
aspool fundamentals status
```

TDX `get_finance_info` 的 `updated_date` 作为来源报告日期，`gudong_renshu` 映射为股东人数。
基本面至少分成财务报告和股东人数历史：

- `stock_financial_reports`：报告期股本、资产、负债、收入、利润、现金流、EPS 等来源字段。
- `stock_shareholder_counts`：证券、统计日期、股东人数和来源。

当前用 `close / PE-TTM` 反推的 `ttm_eps` 不进入基本面原始表。公告日期未知时保持 NULL，
不得把报告期或采集时间冒充公告时间，也不得把最新基本面倒填历史回测日期。

股东人数变化、户均持股、估值和财务比率属于派生或按需快照，不写回基本面来源表。

## 8. 按需快照

参数变化快、仅特定分析使用的维度以 `feature_set + feature_version + as_of_date + scope +
params_hash + input_version` 标识。快照输入版本变化后失效；无调用需求时不主动生成全历史。

```sh
aspool snapshot compute stock-momentum --date 2026-09-29 \
  --params '{"windows":[5,20,60]}'
aspool snapshot prune --before 2026-01-01
```

实验字段可以先用 JSON 载荷；稳定且高频的字段再提升为正式 Enriched 列或专表。

## 9. 数据状态和读取边界

股票目标日期继续使用 `TRADED / NO_TRADE / MISSING / INVALID` 终态。原始行情不制造停牌平价 K 线；
缺数据与确认无交易可以在统计中同样排除，但来源事实必须区分。北交所真实行情保留，涨跌停和
连板统一标记 `NO_LIMIT`，不进入对应市场统计。

所有高基数查询必须有证券、日期范围或行数上限。普通查询不触发网络；只有 snapshot 的显式
计算接口可以按需触发本地派生。

## 10. Fundwise 兼容约束

物理拆库、表重命名和派生状态迁移是 asPool 内部变化。Fundwise 继续只依赖安装后的 `aspool`
Python 包和同一个数据根，不直接打开任何 SQLite/DuckDB 文件，也不需要感知数据位于哪个库。

迁移期间必须保留 Fundwise 当前实际使用的公开入口：

```text
DataPool(root)
describe() / status() / describe_limits()
read_research_daily() / read_index_daily() / read_trading_calendar()
read_market_daily() / stock_snapshot()
read_limit_summary() / read_limit_events()
read_limit_coverage() / read_limit_exceptions()
iter_limit_events_with_amount()
```

兼容不仅指方法仍可调用，还包括已有参数和缺省值、字段投影、列名与顺序、日期和数值类型、单位、
证券代码格式、排序、空值语义、`DataFrame.attrs` 以及 `DataPoolError.code`。新增复权、基本面和快照
能力使用新方法或可选参数；已有调用不传新参数时保持当前含义。`read_market_daily()` 继续作为稳定
名称，不能要求 Fundwise 因物理表改为 `market_regime_features` 而改名。

`read_limit_coverage()` 和 `read_limit_exceptions()` 是兼容投影，不要求重新建立 coverage、exceptions、
revision 或 publication 物理表。它们分别从交易终态、逐股稳定派生和市场公共特征生成现有列；
批次专属字段在新后端中按公开契约返回空值或明确的兼容值，不能伪造发布批次。
`iter_limit_events_with_amount()` 继续以有界批次关联同日成交额，不允许退化为整段历史一次加载。

`stock_snapshot()` 必须为其 reader 的 `read_market_daily()` 提供逻辑一致视图。即使原始行情与特征
分属不同文件，也只能返回与同一输入 revision 匹配的结果；无法建立一致视图时抛出既有
`DataPoolError`，不能混合新原始数据和旧派生数据。

迁移切换前，用 Fundwise 当前字段集合和日期样本对旧布局与目标布局做契约对比。除明确批准的新增
字段外，DataFrame 值、元数据和错误码必须一致，并运行 Fundwise 的 asPool、Regime、筛选和回测
消费者测试。兼容门未通过时 DataPool 继续读取旧布局，不切换 Fundwise。

## 11. 当前实施范围与验收

当前计划包括：

1. 建立目标数据库和 schema；迁移存量后切换公开读取。
2. 将三类原始日线、公司行为、因子、稳定 Enriched、Regime 和基本面分层。
3. 实现 `sync --count` 并保留 `--start/--end`。
4. 从 `--type all` 移除基本面，完成手动基本面命令组。
5. 完成局部派生、过期拒绝和按需快照。
6. 真实数据迁移、计数/范围/抽样对账、Fundwise 兼容契约和资源验收。

跨设备增量同步只记录为未来方向：权威基础库、基本面和因子库可提取逻辑增量，Enriched、Regime
和快照在接收端重算。它不属于当前实施计划，不新增 replica CLI、游标、变更日志或网络传输任务。

验收必须证明：原始日线未复权；前后复权可重复计算；因子修订触发正确后缀；派生过期不会被
误读；基本面保留报告期和股东人数历史；Fundwise 无需扫描全年逐股明细即可读取日级公共特征，
且无需修改数据根、物理库路径或上述现有公开调用。
