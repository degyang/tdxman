# 四表 SQLite：数据迁移、接口与 Regime 计算评估

日期：2026-09-28。对象：tdxman/aspool，消费者包括 Fundwise 与 tick-stock-panel。状态：只读调查与目标契约，尚未实现 SQLite 后端、迁移或上线。详细字段、DDL 和完整模板以[详细需求与设计](sqlite_stock_requirements_design.md)为准。目标和任务见[精简决策](daily_data_simplification.md)、[实施计划](sqlite_four_table_implementation_plan.md)。

## 1. 结论与评估边界

股票库只设四张业务表：daily_bars、corporate_actions、daily_features、market_daily_summary。取消独立 coverage/exceptions/revisions/publication 和业务 batch_id。单写入者在一次局部事务内写事实、计算派生和汇总；网络获取在事务外，异常回滚。业务没有全历史刷新入口；全量初始化、规则升级和修复由外部 ops 脚本执行。

Regime 的公共市场特征应在 aspool 日更时一起计算，放入 market_daily_summary。按用户后续要求扩展为日/周/月特征，Fundwise 用模型模板运行不同评分/阶段模型，避免每个模型重新读取全年股票明细。评分权重、模型参数、训练和用户自定义板块主线留在 Fundwise。四张表不增加模型结果表或通用特征平台。频率、周期边界及非可加指标以[模板与日周月设计](regime_model_template_design.md)为准。

本轮核对 tdxman `3f5054e2`、Fundwise `1b256585`、tick-stock-panel `d71cafc8`。生产根 `/home/ubuntu/.aspool` 仅以 Parquet footer、JSON 源记录和 DuckDB read_only 连接调查；没有运行同步、补齐或重算。各项观测不是冻结快照，也不是完整逐值迁移验收。证据：[本轮盘点 JSON](evidence/sqlite-migration-assessment/20260928-inventory.json)。

## 2. 存量数据及不能直接复制的部分

| 对象 | 本轮观测 | 迁移含义 |
| --- | ---: | --- |
| daily Parquet | 7,288 文件、17,862,852 行、1,587,617,703 字节 | 股票与 ETF 混存，不能全部进入 stocks.sqlite |
| 物理字段 | 35 种 Arrow schema（含 metadata）、43 个实际文件字段名并集 | 别名、整数/浮点/NULL 类型需规范化；公开 daily 为 35 字段，不得只迁 Fundwise 的少数字段 |
| asset_type=etf | 1,512,767 行 | 保持 ETF 路由与原数据，本期不顺手删掉或迁到股票库 |
| 未标 asset_type | 16,350,085 行 | 股票迁移候选；仍要与证券目录核对，不能把缺标记本身当资产分类证明 |
| 日期事实 | 413,515 行，含 412,066 个非空 pre_close | 其中 576 行明确 SUSPENDED；事实没有日线也必须保留 |
| 限价现行范围 | 1,296 日，2021-05-27 至 2026-09-24 | 是派生范围，不是原始日线全历史边界 |
| 现行事件 / scope | 182,536 / 6,594,702 行 | 旧事件稀疏；新逐股状态不能通过复制旧事件生成 |
| 现行参考价 | 277,429 行 | 公开日线目前使用这部分 overlay，漏迁会改变 pre_close |
| 现行 exceptions | UNKNOWN 16,686；NO_LIMIT 5,012；INVALID 17 | 不能一概删成 false；缺行部分按新口径排除，实际交易但不可判定部分保留状态 |
| staleness | 0 行 | 仅本轮观察，不等于数据完整或历史字段都可信 |
| 除权输入 | 5,586 JSON、186,622 条各类事件 | 含分红送转、股本变化、缩股等；不止 category=1 |
| 累计因子 | 58,677 行、288,593 字节 | 独立来源；不能假定均能由现有事件精确还原 |
| 公共资料 | 生命周期 5,906、证券目录 7,298、日历 13,064 行 | 继续作为公共资料，不为凑四张表而丢弃 |
| 旧 catalog | 933,507,072 字节、28 张表 | 含非股票域和旧维护记录，不能整体 DROP 或作为新库复制对象 |

现行日线不是裸 Parquet：`pool._overlay_dated_fields` 依次使用非空日期事实、有效已发布限价参考价、原日线值。迁移必须保存这条来源优先关系的语义，再去掉发布指针；不能简单选一个文件列当正确值。发布参考价还要识别其依赖与可信条件，不能变成永久不可撤销的源事实。

旧日线体积估算不能当新四表总容量：旧事件只约 18 万行，新 daily_features 合并日期事实与逐股状态，行数可能接近股票日线约 1,635 万行，另含无 K 线的源事实。需要实测完整字段与索引后的容量，再加 WAL、迁移暂存和恢复副本；本轮没有给出未经实测的整库空间承诺。

## 3. 四张表的边界与字段归属

| 表 | 键与访问路径 | 内容及约束 |
| --- | --- | --- |
| daily_bars | PK(symbol, trade_date)，INDEX(trade_date, symbol) | 未复权 OHLC、volume、amount、换手/量比/市值/估值等既有日线字段及来源；原始有效值不得因迁移重新复权或乘 100 |
| corporate_actions | PK(symbol, effective_date, record_kind, source, source_key) | 原始除权/股本事件与独立 factor_anchor 记录；区分事件、累计因子和来源，不强行合并同日多事件 |
| daily_features | PK(symbol, trade_date)，INDEX(trade_date, symbol) | 合并 pre_close、必要 ST/交易状态及来源、限价/四个标志/连板和必要原因；无真实 K 线的源日期事实也保留，计算列为空，不制造交易事件 |
| market_daily_summary | PK(frequency, period_key, scope) | D/W/M 固定市场范围公共特征、样本数、period_start/end、as_of、必要 NULL 状态、updated_at；不含模型权重/评分结果 |

价格与金额先保持现有 float64 语义，限价取整复用 Decimal/价格档位算法，不把所有历史金额改成整数分而丢失原精度。布尔使用可空 0/1，日期统一 ISO 文本，时间明确 UTC；来源和名称用文本。SQLite 类型约束与入口校验配合，不能依赖动态类型自动转换来吞掉非法输入。

`pre_close` 就是参考昨收，在新库统一放 daily_features，公开 daily 通过 JOIN 返回，避免日线、事实、发布参考三处各维护一个“有效值”。迁移保留来源与依赖属性：源给出的参考价和由原始价格/除权推导的参考价必须可区分，后者随相关输入修订重算。原始文件中的不同候选值进入迁移差异清单与恢复副本；若仍有实际消费用途，保留命名明确的源扩展列，不能静默覆盖。无法判定的来源冲突在切换前解决或明确保留 NULL，不任意择一。

可靠当日 name 可以直接判断 ST；name 与其日期依据随 daily_bars 保留，不为名称判断另建表。对于没有可靠历史名称但已有明确日期 ST 来源的记录，daily_features 保存可空 is_st 和来源；按名称生成的标记是可重算缓存，不是另一份独立事实。特别核对 is_st_name_date 与快照来源，不把当前名字拷贝成多年 ST。trading_status 非本统计流程必需，仅作为 daily_features 可选源字段保留，识别停牌占位并兼容旧读者，不引入状态补齐流程。源字段与计算字段有各自列，计算更新不覆盖源字段。

canonical volume 沿用 `coalesce(volume, vol)`，turnover_rate 沿用 `coalesce(turnover_rate, turnover)`；发生别名不等时列入差异记录。`float_share` 与 `float_shares` 不因拼写相似自动合并。原 date/int64、datetime、code、market 与路径键逐项核对，证券前导零保留；派生 market/code 可在读取时从规范 symbol 返回。源扩展的保留/退役逐字段见[字段映射](evidence/sqlite-migration-assessment/20260928-field-map.json)。

corporate_actions 的 factor_anchor 是现有累计因子的来源记录，不冒充一笔分红事件。计算先按已验证的来源口径选择一种有效因子序列；不能把因子与重建事件乘两遍，也不能从累计因子反推不存在的分红金额。本轮 event category=1 有 58,687 条，并不等于因子 58,677 条；必须按证券/日期/来源比较后确定有效序列。源为每股还是每十股、复权基准日及因子方向要固定。

### 3.1 对照 tick 的除权与因子链（2026-09-28 补充）

核对本地 tick-stock-panel `d71cafc8` 的实际源码，未改 tick 或运行它的测试：

- `services/kline_sync.py:495`：TickFlow 路径调用 `tf.klines.ex_factors`；自定义 provider 调用 `get_adj_factors`。主流水线要求单次事件比值 `ex_factor`，而非每日累计因子。
- `plugins/fuyao/provider.py:680`：从分红、送转、配股事件与前一有效原始收盘价计算参考价，再以 `ex_factor = 原始前收盘 / 除权参考价` 得到事件因子。其 `_ref_price` 使用每股公式及两位 half-up；同日事件成分先合并。tdxman 已有 `_reference`，应复用并核对边界，不能直接把多条同日原始记录分别累乘。
- `kline_sync.py:589`：保存 `symbol, trade_date, ex_factor` 到 `adj_factor/all.parquet`，ETF 使用单独目录；按股票/生效日期合并。毫秒时间戳先转北京时间，避免事件错一天。
- `indicators/pipeline.py:246`：按股票将单次因子累乘，K 线通过向后 asof 取当日已生效累计值；前复权价格为 raw × 当日累计因子 / 基准累计因子。累计值在该函数中临时计算，不是另一个每日持久表；保持成交量/成交额原口径。

目标采用同一简单公式，在 corporate_actions 保存原始事件、选择后的单次事件因子；累计因子可作为同表稀疏因子记录的可再生计算列保存，不另建表、不按所有交易日重复保存。已导入的累计来源因子保留 factor_anchor，只有在来源口径一致且有前一可靠锚点时才能用相邻累计值之比转换成事件因子。不能根据字段恰好名为 adj_factor 就认为它是单次因子。

目标读取指定复权基准日 A：P_adj(D)=P_raw(D)×F(D)/F(A)。F 只包含其生效日及之前的有效事件；未覆盖的事件历史不能无条件以 1 冒充完整覆盖。例：10 元价格每股分红 1 元，参考价 9 元，事件因子 10/9，事件前价格按基准日比例调整为 9 元；这只是算术示例，不是生产样本验收。

更新只比较实际变化：新增事件更新该股对应稀疏因子；修改旧事件重算该股必要的后续因子记录及相关缓存，不重写原始历史 K 线。新增末端事件会改变以最新日为基准的历史前复权显示，因此相关复权缓存必须失效，但原始日线和过去已确定的涨跌停事实不自动全量重算。请求范围在事件之后仍需读取此前的累计锚点；按请求起点裁掉全部先前事件会算错。

不照搬 tick 的两处实现：sync_adj_factor 目前将所有返回因子的股票视为 affected 并重写整个因子文件，即使值未变；pipeline 对 affected 股票重算全部历史 enriched。另 stock-sdk 插件 bridge.mjs:177 返回 hfq_close/raw_close，却直接名为 ex_factor，与主流水线单次因子定义存在需要验证的口径风险，不能作为迁移转换范例。本轮是静态检查，不宣称已经复现其数值错误。

四张表是股票业务库范围，不是要求整个 aspool 只剩四个存储对象。指数、ETF、分钟、公共日历/证券目录继续由已有独立域维护；不增加新的批次/发布系统。数据库 schema 版本可以使用 PRAGMA user_version，不建业务修订表。

## 4. 缺数据、停牌、连板与时间边界

按用户确定的统计口径，存续期间缺行与已确认停牌走同一分支：当天涨/跌/平家数、涨跌停数和成交统计均不纳入，比例分母不纳入；停牌占位行同样排除。仅存在来源确认差别供追溯，不因缺行增加 UNKNOWN 或阻断其他有效样本。整个市场当日无样本时数量为 0、均值和比例为 NULL，不能生成中性分。

连板在两类间隔中冻结，恢复实际交易后再延续或清零。补数会改变当天和后续递推，直到完整递推状态一致，并确保更晚另一个改动区间仍被处理。未知参考价的真实交易不是停牌，不得与缺行混为一类。首次可见历史缺少前驱时不能假称已知全程板数；保留边界未知，遇到确定非涨停后可以恢复确定状态。

新上市无价格限制窗口仍按市场交易日与上市日计算，不能用稀疏 K 线行号替代；交易日历不从单股缺行推断。间隔内有除权时，复牌参考价要使用可靠来源或区间事件调整。

这会改变旧版某些连板/异常/Regime 结果。验收分两部分：原始有效数据及未改口径字段保持等值；新统计与新的完整参考算法等值。不能要求新规则逐值复制旧错误，也不能把所有差异都归因于规则变更。

## 5. Regime：重计算前移，评分复用

### 5.1 项目证据与判断

tick 的 `services/regime_builder.py::_aggregate_daily` 对 enriched 做按日聚合，然后分类评分；`upsert_regime_history` 保存到 `regime_history/part.parquet`。已有缓存走 `get_enriched_range`，慢路径 `_scan_enriched_fallback` 按有预热的窗口计算后立即压缩为日级行。`refresh_phase_labels` 重扫的是千级日汇总，不是千万级逐股历史。tick 仍会整体改写日汇总文件、存在全历史慢路径；本项目采用其“复用日特征”思想，不复制这些全量更新入口。[本地实现](../../../Projects/tick-stock-panel/backend/app/services/regime_builder.py)

Qlib 区分基础数据、表达式特征、处理器、模型 Dataset，并提供表达式/数据集缓存；VeighNa AlphaLab 分别保存 dataset、model、signal。共同可借鉴的是复用特征，模型不重复从原始数据开始；这些项目不证明 SQLite 适合任意规模，也不要求引入完整特征平台。[Qlib 官方数据层](https://github.com/microsoft/qlib/blob/main/docs/component/data.rst)、[VeighNa AlphaLab 源码](https://github.com/vnpy/vnpy/blob/master/vnpy/alpha/lab.py)

Fundwise 当前 `regime/service.py` 按月取股票明细并向前读 60 自然日，`market_environment.aggregate_daily` 重新计算涨跌、MA20 广度、中位数等；`cycle.attach_ladder` 又读事件计算梯队。`freshness.py` 枚举全池 Parquet/DuckDB 文件，某处修订容易让整个历史缓存失效。因此重复成本确实存在；问题不限于 SQL 引擎。

### 5.2 公共特征的落点

market_daily_summary 的 D 频率一日一个固定 scope 一行；W/M 按周期保存一行。首期两个 scope：`all_stocks`、`exclude_known_st`（日频只排除该日 is_st=True；未知 ST 仍在样本并另报 st_unknown_count，不假称已知非 ST）。不存任意自选池、任意模型参数的排列组合。截止日 ST 快照过滤改成逐日事实过滤是明确口径变化，Fundwise 输出标识随之调整。周/月日特征汇总沿用每日样本，原生周期收益样本使用周期 as_of 的 ST 事实；两类特征名称和定义不混用。

| 特征组 | 首期前移至 aspool | 分母/局部更新范围 |
| --- | --- | --- |
| 涨跌分布 | up/down/flat、正负 3% 家数、涨跌停单列、其余按±10%内每2个百分点的15档分布（含平盘/两端）、均值、中位数、有效收益样本数 | close/pre_close 有效的实际交易样本；修正当天，必要时下一实际交易日 |
| 量能 | 成交额合计、有效量能样本数、平均换手率 | 不把缺值当 0；修正对应日期 |
| 限价 | 收盘/触及涨跌停、炸板、封板率、最高连板 | 真实可判定样本；按事件递推影响日期 |
| 梯队 | 首板、二板以上、三板以上、五板以上、有效高度档位、晋级人数及可晋级样本数 | 当前实际交易且前一有效交易记录为涨停才进晋级分母；停牌/缺行不进分母；与冻结连板口径一致 |
| 趋势广度 | MA20 有效样本数、站上 MA20 家数/比例 | 默认取最近 20 根有效交易 K 线，缺行/停牌不补平价；修订传播到受影响的滚动窗口 |

MA20 采用以目标日为基准、可用因子调整到同尺度的价格。相较 Fundwise 旧“连续市场会话原始 close”是单独的口径变化，必须给出差异归因；缺复权依据而窗口跨已知公司行为时不硬算。没有变化的新除权不会使所有过去的 close/MA20 比较重新变化；后续修正历史事件仅更新实际跨该事件的窗口。

初期不增加逐股通用指标表：在 daily_features 保存定向的 ma20/above_ma20 两个缓存列，按股票局部更新；市场日汇总直接读取该日截面，避免每天为全市场重复加载 20 根历史。历史修正仅重算受影响日期，允许扫描这些日期的全市场截面以精确重算中位数/最大值；不扫描全市场全年。这个有限窗口方案仍有真实成本，必须实测，不承诺“只碰一个数据页”。多证券同日输入先汇总变化，再只聚合一次该日，禁止每 upsert 一只股票就重算全市场。

### 5.3 多模型消费与指数

一次读取一个年度的固定市场特征，每个 scope 约 250 行；即使模型有 50 个 float64 特征，250×50×8 约 100 KB 数值量级（不含容器/字段开销），不是 250×数千股票的明细。Fundwise 可在内存把同一份年度特征传给多个模型，各自保存模型/参数/特征定义标识下的结果。

指数全年数据是“指数数×交易日数”，不等于全市场股票历史。首期保留 `read_index_daily`，一次按范围与字段取需要的指数，和市场特征在 Fundwise 按日对齐并复用；不在 stocks.sqlite 复制整套指数。指数修订只失效依赖它的模型结果，不使股票限价重算。公共指数滚动特征若确有重复瓶颈，再放指数域计算，不增加股票表。

评分权重/阈值/标准化/阶段平滑是模型逻辑，留在 Fundwise。改权重无需读逐股日线；新增 MA60 广度、其他涨幅阈值等尚未保存的基础特征，需要明确增加公共特征及一次 ops 回填，不能假称任何新模型都能从现有汇总还原。训练用归一化不得利用预测日之后的数据。

概念/行业主线仍需逐股事件、成交额和板块成员；不能由全市场日汇总推导。继续使用有界事件金额接口，并按成员快照与日期范围缓存；不为减少一次读取把模型和所有板块动态配置塞进 aspool。

## 6. 公开接口变更矩阵

以下为目标签名与迁移决定，均未实现。`contract_version` 是 API 版本，不是数据发布批次；现行 daily 版本为 2，新破坏性派生契约拟升为 3。

| 当前接口/入口 | 决定与新行为 | 消费变更 |
| --- | --- | --- |
| DataPool(root)、read_daily / read_research_daily | 保留签名、符号、日期/字段语义与 50 万行 DataFrame 上限；底层 SQLite 投影与事实 JOIN | 选股、回测、watch、tick provider 可优先保持调用 |
| read_security_daily | 从 daily_features 投影源日期字段；兼容期保留旧 source 列，不再有同名实体表 | 包括无 K 线事实，不能 INNER JOIN 掉 |
| read_security_info / read_trading_calendar | 保留公共资料来源 | 不把公共 catalog 一起删掉 |
| read_index_daily / list_indices、read_etf_daily / list_etfs | 首期保留原后端与返回值 | 防止股票库迁移影响其他域 |
| read_market_summary / read_market_daily（新增） | 通用接口支持 D/W/M；daily 是 D 的便利接口，返回特征、样本数、updated_at | Fundwise 模型模板主要输入；避免拉全市场明细 |
| read_limit_summary | 保留为 read_market_daily 的限价列投影 | 新版无 batch_id/stale/published_at；不再联合 coverage |
| read_limit_events | 保留日期/证券参数；公开结果保持稀疏事件过滤，新增 status/必要原因 | 底层逐股状态较密集，不让旧 API 突然返回全部非事件；检查消费者 false 过滤语义 |
| iter_limit_events_with_amount | 保留有界事件+金额 JOIN，改为同一 SQLite 读快照 | 不再逐窗口对比发布代次；字段去 batch_id/stale 等；提前退出关闭连接 |
| read_limit_coverage / exceptions / references / scope / staleness | 新契约退役，不保留实体表；诊断从日汇总、日期事实、逐股状态读取 | 老客户端在旧后端过渡；不制造假 batch_id 通过检查 |
| compute_limit_events | 从常规 DataPool 业务接口移出 | 更新自动触发；显式历史重算改 ops 脚本 |
| describe / describe_limits / status | 根据已实现能力返回字段、单位、规则定义与后端；status 查 SQL | 不枚举全 Parquet；不再把 REQUIRED_TABLES 的旧七表当 ready 条件 |
| CLI sync / quotes / enrich | 全部接统一局部写入，不追加单独发布阶段 | 最近范围与明确补数；失败不自动全史回退 |
| CLI import --period daily / --factor、历史重算命令 | 迁到外部 ops 入口；旧命令输出明确迁移提示 | 大范围模式不能由业务自动调用 |

目标使用样例：

```python
# Planned v3 signatures; these APIs are not implemented yet.
with pool.read_snapshot() as reader:
    features = reader.read_market_daily(
        start="2025-01-01", end="2025-12-31", scope="exclude_known_st",
        fields=["trade_date", "up_count", "median_return", "above_ma20_pct",
                "close_limit_up_count", "max_consecutive_up", "updated_at"],
    )
    daily = reader.read_daily(
        symbols=["000001.SZ"], start="2025-01-01", end="2025-12-31",
        fields=["symbol", "date", "close", "pre_close", "amount"],
    )

with pool.iter_limit_events_with_amount(
    start="2021-01-01", end="2025-12-31", fields=["trade_date", "symbol", "amount"],
    close_limit_up=True, min_consecutive_up=1, chunk_days=7, max_rows=25_000,
) as chunks:
    for frame in chunks:
        consume(frame)
    assert chunks.completed
```

`read_snapshot()` 将股票查询绑定到一个 read-only 事务，不是数据版本表。独立 iterator 自己持有读快照；需与汇总一致时由同一个 snapshot reader 创建。指数旧后端不能因套进 Python context 就宣称跨库同一事务，读取小范围指数并在模型输出前复核其输入。

`chunk_days` 替代旧 `batch_days`（兼容期保留别名，两者同传拒绝，均未传默认7），只表示读取分块。`memory_limit`/`threads`/`temp_directory` 是旧 DuckDB 参数：新版明确弃用，首期由兼容签名告警后转入有界实现，不声称 SQLite cache_size 是 RSS 硬上限。资源依靠投影、索引、fetchmany/日期块和实测；五年峰值 RSS ≤2 GiB 不变。新快照读不会因并发写入主动报 LIMIT_REVISION_CHANGED；返回同一时点数据，结束后 Fundwise 检查相关日 updated_at 是否变化再决定是否缓存。这是显式契约变更。

SQLite 异常加入 DataPoolError 映射：非法字段/参数及原数据错误保持稳定；锁超时、损坏、只读失败分别说明，不能把所有 sqlite3.Error 都伪装成 DAILY_INVALID。空列表、缺日范围、排序、lookback 每证券尾部 N 条、NULL dtype 和 DataFrame attrs 均列入验证。

## 7. 写入入口与消费方改造范围

| 模块 | 具体改造 |
| --- | --- |
| daily_access / daily_storage / store | 明确 SQLite 股票访问；旧 Parquet 保留迁移/其他域，不构建通用后端插件平台 |
| tdx_online 在线/离线、fundamentals quote | 获取与规范化在事务外；按日聚合输入进入统一 writer，无需完整替换股票历史 |
| baostock_source / enrichment / st_source | 补数、ST、参考价、除权写同一路径；fact-only/action-only 也触发相应局部计算；源失败不得解释为删除 |
| free_stockdb | 完整导入由 ops 调用；因子进入 corporate_actions 的独立来源记录 |
| limit_events / limit_api / limit_amount | 提取纯计算，删除新路径的批次 SQL、scope/异常副本和 stale 后缀；金额直接同库 JOIN |
| change_protocol / pool.writer | 股票新路径用 SQLite 事务；旧文件前滚机制仅在旧域确有需要时保留，不让股票路径再准备 catalog 副本 |
| Fundwise aspool_backend | 日汇总一次读取，删除四接口拼接和批次一致检查，收益/限价未知仍按真实状态处理 |
| Fundwise regime/service、runner、market_environment、cycle | 取消每个模型重复逐股聚合；取公共特征，保留模型评分/阶段逻辑；移除 batch_id 检查；缺口、ST、MA20 与晋级分母按新定义 |
| Fundwise freshness / regime_events | 移除全池 rglob 与全局 revision 缓存键；按特征窗口、模型、参数、指数/板块依赖检查；主线事件仍有界 |
| tick_stock_panel adapter/provider/normalize | local 模式依赖 read_daily / ETF / index 三个入口；签名与单位保持；online TDX 路由不动，未实现的 get_adj_factors 不冒称已接通 |

不是只改 Fundwise 的一个 adapter：`cycle.attach_ladder`、`service._events`、输出中的 limit_batch_id、过期提示和缓存文件 schema 都有依赖。应用本身的报告缓存、任务进度与模型计算标识可以存在，它们不成为 aspool 的业务发布批次。

缓存按各 scope 对应日期 updated_at 序列、字段定义/模型参数生成窗口指纹，不用单一 max(updated_at) 或文件 mtime 代表所有内容。只读短序列，窗口包含新增、删除和预热依赖；逻辑日期空集合也需识别。规则升级在 ops 重算后修改受影响日期时间。替换数据库/恢复副本后清理受影响应用缓存，避免时间戳回退误命中；不另建缓存版本表。

## 8. SQLite 事务、备份与迁移风险

按用户最新要求，新版根统一为本主项目tdxman/data（当前位于/mnt/d的9p挂载）；旧/home/ubuntu/.aspool只作为迁移源。此前优先Linux家目录的建议不再作为目标路径。实际文件系统的锁/WAL/落盘与性能须专项验证，不让Windows与WSL两套writer混用。计划使用 WAL、synchronous=FULL、明确 busy_timeout、单 writer，日期范围只读事务；长读需及时关闭以控制 WAL 增长。多库不假设联合原子性。备份使用 SQLite backup API 或停写并正确 checkpoint/关闭后的副本，不能仅复制活跃主文件。这些是引擎约束，不是额外业务表。[SQLite WAL](https://sqlite.org/wal.html)、[事务隔离](https://sqlite.org/isolation.html)、[备份 API](https://sqlite.org/backup.html)

默认单个局部写事务内同步重算保证简单正确；需实测一天全市场与历史修订的 writer 占用、读并发和 WAL 增量。若递推范围超过正常工作预算，整笔回滚并交外部 ops，不改成原始先提交、派生异步追平再重建发布协议。正常写入边界按变化范围配置，不因历史总量增长自动扩大。

切换首选维护窗口：旧 writer 停止、排空任务、冻结可恢复输入，离线构建/验证新库，再一次切换应用配置并重启读连接。开发阶段在隔离副本反复验证；生产不双写两套权威，不给旧库与新库各建事件日志。若开发快照后旧源继续更新，最终窗口用旧变更证据或只读差异扫描补齐事实，再按新规则计算；该扫描属于 ops，不能进入每日业务。

上线后直接回退旧快照会丢失新写入。回退脚本必须在停写下导出切换后差异到外部文件、反向应用旧源并按旧规则重算，或者修复新库后继续服务；验收中必须演练。旧代码无法支持新缺口口径，回退时应明确恢复旧语义，不能称统计等价。未具备可执行回退前不清理旧源。

## 9. 已检查与未验证

已检查：实际文件规模/schema 与 catalog 数量、源因子/事件并存、参考价 overlay、日线公开签名、旧批次依赖、主要写入链、Fundwise 多模型重复聚合路径、tick/Qlib/VeighNa 实现。没有重跑已通过的旧 P0/agent 测试。

待实现后验证：字段级全量迁移等值、因子与事件源一致性、SQLite 四表容量、正常事务耗时与增长压力、长读 WAL、故障回滚、全链路新统计及缓存、实际切换/回退。未承诺迁移耗时、最终磁盘量或 SQLite 性能已达标。

跨设备同步已纳入详细规格 R10 和实施工作包 FW-09，方案、假设、扫描成本及部署边界见[详细需求第8节](sqlite_stock_requirements_design.md#8-跨设备同步需求r10)。本评估早期的生产盘点数仍是当时观测，不作为冻结迁移计数。
