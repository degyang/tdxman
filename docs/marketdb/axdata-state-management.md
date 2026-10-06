# AxData raw → core 状态管理调查

核对日期：2026-10-06。本文记录本机 `Services/AxData` 的文档、源码与本地运行记录调查，用于 MarketDB 设计参考；不表示 MarketDB 已实现这些能力，也不是 AxData 全部插件和部署的验收。

[文档入口](README.md) · [AxData 对照](axdata-comparison.md) · [MarketDB 事件状态范式](event-state-workflow-pattern.md)

## 1. 结论与调查边界

AxData 已具备状态取数、局部业务判断、分类名单快照，以及任务、写入和质量记录。尚未在所查链路中形成完整的“全市场每日证券状态 → 状态变化 → 正式事件 → 历史重建”流程。

这里的“状态”分为两类：证券的业务状态，以及处理任务／数据资产的运行与质量状态。`stock_status` 在 TDX Collector 定义中是分类标签，不是一张统一股票状态表。接口、Dataset 或 core schema 已声明，也不表示已完成对应生产转换。

本次检查项目 `data/`、`metadata/`（含被 Git 忽略文件）和 `metadata/collector/collector_scheduler.json`：发现日线样本运行和落盘记录，未发现 ST／停牌快照文件或对应采集任务记录。此结论仅限所查本地目录，不能排除其他部署或自定义输出目录存在数据。

## 2. 设计分层与实际执行路径

[AxData 数据分层文档](../../../AxData/docs/data-layers.md)定义：

| 层 | 设计职责 | 状态相关内容 |
| --- | --- | --- |
| raw | 保存上游响应的最小改动版本及批次信息 | 原始状态字段、来源空值、采集依据 |
| staging | 字段、身份、日期、单位归一与去重 | 标记源侧缺失、异常、停牌、退市等 |
| core | 稳定用户 schema、主键、单位和业务事实 | 证券主数据、日历、行情及复权因子等 |
| factor | 派生指标、特征和研究结果 | 依赖明确的计算结果及质量信息 |

当前所查 TDX 独立 Collector 主要按下面路径运行：

```text
通达信接口
  → Adapter 解析、归一、关联及部分业务判断
  → Collector runner 返回 records + meta
  → Writer 写入指定层或 snapshot
  → 评估写出质量，登记输出和运行日志
```

普通 `request` 是临时取数路径，默认不落盘。Collector 可持久化其返回记录，但不是每个接口都先落完整 raw、再经过独立 staging/core 转换任务。文档明确指出全市场生产级 raw/staging → core 转换仍有缺口；已有 core 目录中的样本文件也不能单独证明转换闭环。

## 3. 证券业务状态与相关表汇总

表中“输出”包括 schema、接口结果及快照能力，不一律代表已运行的持久化表。

| 状态域 | 原始依据 | 当前处理 | 输出／承载 | 实现边界 |
| --- | --- | --- | --- | --- |
| 目录与身份 | TDX 目录、交易所证券资料 | 统一代码、交易所、名称、板块 | `tdx.stock_codes`；`stock_basic_exchange` schema | 生产级目录 latest/history 转换待补 |
| 上市／退市 | 交易所资料及相关日期 | 生成 `listing_status`、`list_date`、`delist_date`；TDX 目录归一直接标为 `listed` | `stock_basic_exchange` | 未形成每日目录差异驱动的完整生命周期事件状态机 |
| ST／*ST | 当前证券简称 | 识别 ST、*ST、SST、S*ST 前缀 | `tdx.stock_st_list` 快照，含 `st_type` | 保留命中名单；未形成风险警示实施／变更／撤销历史 |
| 停牌 | `0x053e` 五档报价状态字 | 检查 `trading_status_raw & 0x20` | `tdx.stock_suspensions` 快照 | 最终名单未保留原始状态字，也未保存全量证券状态 |
| 新股阶段提示 | 名称 N/C 标记 | 限价函数选择新股相关规则标签 | 限价处理过程 | 未看到独立的上市交易日序号状态表 |
| 价格限制规则 | 名称标记、板块代码和价格依据 | 选择规则及比例，计算限价 | `stock_daily_price_limit_tdx` 快照 | 代码内规则不等于已建立按生效日期版本化的规则库 |
| 涨停盘口状态 | 最新价、最高价、买一价、封单金额、涨停价 | 生成 `sealed/touched/none` | 连板天梯相关输出 | 即时派生状态，不是长期证券生命周期事实 |
| 交易日历 | 交易所日历 | 市场日期、`is_open` 等 | `trade_cal` schema、本地日历缓存 | 市场开市与个股停牌是不同维度 |
| 股本时点 | 财务／股本来源 | 股本值和更新依据 | `tdx.stock_daily_share` 快照 | 时点事实不等于完整股本变更历史 |
| 除权除息／股本事件 | TDX 公司行为记录 | 事件类别和参数归一 | `stock_capital_changes_tdx` 源接口及兼容下载 | 不在当前默认采集器中；未形成统一每日权息状态表 |
| 复权因子 | 公司行为与价格等依据 | 提供前／后复权因子 | `adj_factor` schema、源接口及兼容下载 | 生产级因子重建策略待补 |
| 公司属性 | 证券基础资料 | 保留 `is_profit/is_vie/has_weighted_voting_rights` 等 | `stock_basic_exchange` schema | 主数据属性，不是每日交易状态 |

`listing_status` 的 schema 建议值包含 `listed/delisted/suspended/unknown`，但不能据此推断停牌扫描已回写此字段：所查停牌接口走独立名单输出链路。

限价函数有根据名称先处理 ST、再处理板块的简化逻辑，且不同函数的名称识别方式存在差异。本文记录其代码行为，不认证其为所有市场、历史日期均适用的交易规则；MarketDB 不应直接将其视为权威限价合同。

证据：[core schema](../../../AxData/libs/axdata_core/axdata_core/schema.py)、[证券归一](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/security_codes.py)、[交易所适配器](../../../AxData/libs/axdata_core/axdata_core/adapters/exchange/request.py)、[状态筛选](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/status_fetch.py)、[限价与涨停判断](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/price_limits.py)。

## 4. 停牌状态完整调用链

```text
stock_suspensions_tdx
  → request_stock_suspensions
  → 获取按 scope/code/name 筛选的股票目录
  → 分批请求 legacy quotes（默认每批 80 只）
  → 解析 0x053e 证券记录中的两字节无符号状态字
  → 按 full_code / tdx_code 关联目录
  → suspension_rows_from_stock_rows 筛选 0x20 位
  → normalize_suspension_row 输出证券身份与名称
  → 可选由 Collector/Writer 保存名单快照
```

报价可以按配置顺序请求或使用连接池／多服务器并行。全量报价存在于内存中，不意味着形成了持久化全量状态表。

核心判定：

```python
trading_status_raw = int(get_value(quote, "trading_status_raw", 0) or 0)
if not trading_status_raw & status_bit:  # status_bit = 0x20
    continue
rows.append(normalize_suspension_row(stock_row))
```

该筛选函数自身的行为：

| 输入情形 | 行为 |
| --- | --- |
| 目录证券无对应报价 | 跳过 |
| 状态字缺失、None 或空字符串 | 按 0 处理 |
| 报价代码重复 | 字典后值覆盖前值 |
| 报价代码不在目录中 | 不输出 |
| 状态字命中 0x20 | 输出停牌名单记录 |
| 状态字未命中 0x20 | 不输出 |

这不是整条链路所有校验的描述；例如底层解析器会拒绝截断的状态字段。不能从筛选函数的容错行为推断网络故障全部会被吞掉。

最终记录包含 `instrument_id/symbol/tdx_code/exchange/name/market`，不包含原始状态字；meta 保留来源接口、状态位、批大小、扫描数、报价数及 `checked_at`。`checked_at` 是本机检查时间，不是来源行情交易日。未命中停牌位也不意味着此刻正在交易。

证据：[请求编排](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/request_methods.py)、[报价分批](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/quote_fetch.py)、[协议解析](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/_tdx_wire/protocol/commands/quotes.py)、[输出字段](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/quote_identity.py)。

## 5. ST、限价和涨停状态的处理边界

ST 流程先获取目录，再由 `st_type_from_name()` 判定简称前缀。未命中证券被过滤，命中证券输出 `st_type`。这能回答当前名称中哪些证券带风险标记，不能仅凭结果确定摘帽生效日或目录缺失原因。

限价流程使用名称 N/C/ST 等提示、证券板块及价格依据。新股阶段提示和限价规则选择在业务函数内完成，并没有独立维护按日有效的阶段状态历史。

`limit_ladder_status()` 判断：最新价接近涨停价，且买一价接近涨停价或存在正封单金额时为 `sealed`；否则最高价或最新价达到涨停价时为 `touched`；其余为 `none`，缺涨停价也返回 `none`。因此 `none` 并不天然表示输入完整且确定未涨停。

这些结果不能自动替代有日期、完整性和证据版本的 core 状态。

## 6. 任务、质量与资产状态汇总

| 管理对象 | 当前字段／值 | 职责及边界 |
| --- | --- | --- |
| 采集运行 | `pending/queued/running/success/failed/skipped/cancelled` | 排队、执行与结束状态；不是证券业务状态 |
| 任务依赖 | `dependency_status`、依赖 Dataset 声明 | 记录依赖信息，不代表所有业务字段已就绪 |
| 来源观察 | 扫描数、报价数、来源、检查时间等 meta | 解释取数范围与来源，不替代逐证券缺失检查 |
| 数据质量 | schema、主键、行数、日期范围、数值、日历等检查 | 检查由具体 profile/规则决定，不保证每个数据集都执行所有检查 |
| 写入状态信息 | `write_mode/primary_key/partition_by/date_field`，行数及去重信息 | 描述写入方式和影响范围 |
| 本地资产 | 路径、层、列、日期范围、行数、质量摘要 | 供发现与预览，不等于严格版本发布合同 |

写入模式包括 `append/snapshot/overwrite_partition/replace_range/upsert_by_key`。ST／停牌为快照能力；独立 Collector 默认调度为 manual，不能推断为已经每天自动更新。默认快照格式为 Parquet，相关 profile 还支持 CSV、JSONL 等。

独立 Collector Runner 当前先写文件、后评估质量，随后返回运行 `success` 和独立的 quality 信息。运行成功不能单独证明全市场完整、质量全部通过或已完成正式 core 发布。Data Browser 从运行元数据、输出日志和已知 core 路径发现数据，不能将这种发现机制等同于事务化版本发布。

证据：[Collector 定义与执行](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/collectors.py)、[快照 profile](../../../AxData/packages/axdata-source-tdx/src/axdata_source_tdx/downloader_profiles.py)、[调度状态](../../../AxData/libs/axdata_core/axdata_core/collector_scheduler.py)、[Runner 写入及质量顺序](../../../AxData/libs/axdata_core/axdata_core/collector_runner.py)、[质量检查](../../../AxData/libs/axdata_core/axdata_core/downloader_engine.py)。

## 7. 尚未闭合的状态管理链路

当前各域分别产出目录、ST 名单、停牌名单、限价、股本和日历结果。所查实现没有把它们汇总成统一证券身份 × 适用日期 × 输入版本的一行完整状态，也没有实现完整的以下闭环：

```text
前次有效状态 + 本次有效观察
  → 完整性与日期核验
  → 状态差异与候选变化
  → 正式事件确认／修订
  → 有效时间区间与每日状态
  → 依赖结果重算及历史重建
```

需要区分的缺口包括：全量状态字持久化、未命中与未知分离、跨域日期对齐、观察时间与生效时间分离、正式事件依据、迟到修订、规则版本和重建依据。

## 8. 对 MarketDB 的衔接约束

以下是 MarketDB 已有方向的归纳，不是本次新增实现：

1. 外部生产端交付全量目录、报价状态及必要依据；MarketDB 不直接调用 AxData 或 tdxman 在线补数。
2. raw 保留来源事实，staging 完成代码、日期、类型和完整性校验；core 才统一生成业务状态及变化依据。
3. 全量状态先保留，再生成 ST／停牌等查询子集。缺失保留未知，不置 false；生命周期、风险警示、停牌和权息分别维护。
4. 运行成功、质量合格和 core 发布分别记录。正式横截面只使用匹配发布版本的行情与所需状态。
5. core 日期状态可按可重建性清理，事件和必要观察依据按长期合同保留；具体方案沿用[事件状态范式](event-state-workflow-pattern.md)与[存储发布架构](storage-and-publication.md)。

本文只新增调查记录及文档索引，不创建库表、修改 AxData、启动采集或迁移数据。
