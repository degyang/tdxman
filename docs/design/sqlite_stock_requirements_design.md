# 股票四表：详细需求与设计

日期：2026-09-28。状态：四表迁移、日派生及显式新根公开接口已实现；尚未生产切换。周/月和多模型执行待实现。进展见[FW-04](../achievements/fw04_sqlite_public_api_execution.md)。审核结论见[审核记录](sqlite_design_review.md)。

这是[实施计划](sqlite_four_table_implementation_plan.md)的详细规格。范围为 tdxman/aspool 股票域及 Fundwise 消费接口；不改 ETF、指数、分钟的现有存储。此前评估中未确定的字段和算法，以本文及 [DDL](design/stocks_schema.sql)为本轮细化结果。业务库只有四表，不增加批次、发布、覆盖、异常、任务或模型结果表。

## 0. 新版数据根：tdxman/data

用户已指定新版数据统一放当前主项目的 `tdxman/data`，当前绝对路径为 `/mnt/d/Workstation/Services/tdxman/data`。这是权威运行根，取代此前“新版默认 ~/.aspool 或另设 Linux 家目录”的建议；旧 `/home/ubuntu/.aspool` 仅作为迁移/回退源，切换后不可成为运行时的隐式备用根。

```text
tdxman/data/
├── stocks.sqlite          新版股票四表主库；运行时可能伴随-wal/-shm
├── catalog.duckdb         沿用格式的公共资料/其他域目录（已核对store.py的既有文件名）
└── lake/                  ETF、指数等既有域，保持各自格式与相对布局
tdxman/.local/
├── recovery/              受控恢复材料
├── receive/               异机复制接收暂存
└── reports/               迁移差异、基准测量、同步日志
```

上图为清理后的路径契约，本机已安装运行文件；不表示已完成生产配置切换。其他域迁到统一根时只改变位置、保持格式/接口；不能直接把旧股票publication全套复制成新股票权威。股票主库、其他域及外部运行日志与Fundwise自己的模型结果存储分开，后者仍在应用目录。

项目内入口将默认根解析为绑定的主项目data绝对路径，不能按shell当前目录猜测，也不能在site-packages或各个worktree旁悄悄新建data。DataPool显式root和既有Fundwise `aspool.root` 用于隔离/部署配置；迁移/修复脚本仍必须显式给 source-root、target-root，检查解析后的路径不同并禁止覆盖已有目标。VPS及其他设备各自部署路径可以不同，但均配置为各自tdxman项目的data。

FW-02 原隔离目标现已安装到 data 根。后续接收暂存、备份、日志均位于 `.local/`，不递归复制整个项目或旧恢复源。复制只按已封闭数据清单执行；源/目标不能相同。`/data/` 与 `/.local/` 均忽略 Git，小型审查证据进入 docs/evidence。

当前项目位于WSL的 `/mnt/d`，本轮只读检查显示挂载类型9p。遵循用户指定路径，不擅自改成指向家目录的软链接；FW-01/FW-06必须在这个实际文件系统验证锁、WAL、同步落盘、进程中断恢复及I/O成本，不能拿Linux临时目录结果替代。避免Windows与WSL两套writer同时访问同一个库；如果实测不满足约束，再报告具体证据处理，不预先改动用户的目录决定。

## 1. 需求与验收对应

| 编号 | 必须实现 | 验收 |
| --- | --- | --- |
| R01 | 原始日线独立保存，日事实/限价/连板合并，除权与因子同表 | 四表 schema；43 个旧物理字段逐项映射，无静默丢弃 |
| R02 | 正常更新仅处理明确日期、证券及其真实依赖 | 新增日、单股历史补数、金额修订、无变化各有读写范围证据 |
| R03 | 缺行和已确认停牌用同一统计分支 | 同一输入分别删除 K 线/标停牌，涨跌、连板、成交结果一致 |
| R04 | 原始事实与派生在一个本地事务内完成 | 任一计算步骤失败全部回滚；重复提交不改值和更新时间 |
| R05 | 复权不改写原始日线，来源累计因子不重复累乘 | 跨事件、窗口前驱、同日多事件、更正事件、无事件区间样例 |
| R06 | 公共 D/W/M 特征随数据更新；多模型复用 | 改权重只运行评分，不读取全年逐股明细 |
| R07 | 五年事件金额 JOIN 有界读取 | 完整读取峰值 RSS ≤2 GiB；稳定排序、无重复/遗漏、提前退出释放快照 |
| R08 | 初始化/整史修复只在外部 ops 执行 | 业务无 full/rebuild 参数或全史失败兜底；超预算回滚并明确报错 |
| R10 | VPS 到其他设备单向数据库增量同步 | 首建/日更/历史更正/删除/断线恢复；分别记录网络量、扫描量、写入量；不在接收端重算 |
| R09 | 可恢复迁移及协调切换 | 源事实对账、其他域不变、实际恢复/切换后回退演练通过 |

完整目标只实现确定需要的指标、两种市场 scope 和一种可配置评分执行器。按用户优先级，首个里程碑先完成全部原始事实迁移、Fundwise所需日频派生和对外接口联调；W/M、多模型扩展、跨设备复制随后交付，详见实施计划M1。额外指标和算法通过后续明确需求添加，不预建任意表达式/任务调度平台。

## 2. 表、字段与职责

[stocks_schema.sql](design/stocks_schema.sql)给出全部列、主键和首期索引。使用 SQLite STRICT、WITHOUT ROWID，主键直接组织证券历史；日期索引支持市场截面。无触发器、视图、外键级联、持久化任务表。`PRAGMA user_version` 只表示物理 schema。

通用规则：symbol 为 `000001.SZ` 等规范字符串；日期为北京时间所属日期 `YYYY-MM-DD`；`updated_at` 为 UTC 整数微秒。价格 REAL 保持原 float64 语义，成交量为股、成交额为元；源 turnover_rate/pct_chg/amplitude 沿用现行百分数单位。nullable 布尔为 0/1/NULL。读契约继续标识 point_in_time=False：只存当前修订后的历史，不能声称可恢复过去任一时刻的数据版本。有限数、合法日历日期、资产分类、价格关系等在 writer 校验；缺字段和历史异常分开处理，拒绝迁移的行进入外部明细并阻止未经解释的切换，不能静默丢行。DDL 不是完整业务校验器。非有限数拒绝，不能由 sqlite 驱动将 NaN 悄悄转 NULL。

### 2.1 daily_bars：原始行情

键 `(symbol, trade_date)`。保存未复权 OHLC、volume、amount、turnover_rate、vol_ratio、源 pct_chg/amplitude、股本/市值/估值及对应来源。保留 float_share 与尚未核实同义的 float_shares 两列。源 pct_chg 不作为新统计的计算输入，避免与有效 pre_close 不一致。

name 保留来源值；新增 name_as_of/name_source 标明名称依据。name_as_of 无可靠依据时为空，不能因文件中有 trade_date 就认定名称是历史名称。市场/代码从规范 symbol 返回，date/datetime/vol/turnover 的旧别名经显式转换；不在新库复制所有别名。新写入只接收股票，ETF/指数按现行域路由。

### 2.2 daily_features：五类逐股日信息

键 `(symbol, trade_date)`，允许有日期事实而无 K 线，故不对 daily_bars 建强制外键。不是每日全池笛卡尔积：没有 K 线、没有来源事实就不造行。“来源”字段是记录接口/推导方法的简短标识，不是另一张表，也不是发布批次。

| 类别 | 列 | 定义 |
| --- | --- | --- |
| 日期事实 | source_is_st/source_is_st_source；is_st/is_st_source；is_st_name_date；trading_status/trading_status_source | source_* 留可靠日期源值；is_st 是来源/可靠名称解析后的有效值。交易状态保留来源文本，无值不推断为确认 TRADING |
| 参考昨收 | source_pre_close/source_pre_close_source；pre_close/pre_close_source | 前者是接口直接提供的原值，后者是计算所用有效值；二者不是两种昨收概念，本地推导值不能写回接口原值 |
| 交易及限价 | calc_status；limit_status/limit_reason；limit_up_price/limit_down_price；touch/close_limit_up/down | calc_status=TRADED/NO_TRADE/INVALID；limit_status=KNOWN/NO_LIMIT/UNKNOWN/INVALID，NO_TRADE 时为空 |
| 连板 | prior_consecutive_up；consecutive_up；streak_known | 前一有效交易的已知连板、本次交易后的连板、是否已知；未知不是 0 |
| 趋势 | ma20；above_ma20 | 最近 20 根有效交易 K 线、以本日为复权基准的 MA20；严格 close > ma20 为站上 |

这里是每只个股自己的 MA20，用于“站上各自 MA20 的股票比例”；指数 MA20 属于模型的指数输入，不能替代市场广度。MA20 两列是本次细化增加的定向缓存：复用现有逐股日行，每日汇总直接按日期聚合，避免每天重新为全市场读取 20 根历史。只为实际交易日保存，不新增指标表。没有足够 20 根或调整依据不足时两列均 NULL。

`calc_status=NO_TRADE` 包括无 K 线的事实行和确认停牌的占位 K 线；限价、连板、MA20 计算列为 NULL。冻结状态由上一有效交易行携带，不为停牌每天复制板数。正常有效 K 线且状态未知可按实际交易处理；明确停牌却有实质成交是源冲突，拒绝该次输入并报告，不静默丢成交。无状态的全零占位行不能据此宣布确认停牌；按 INVALID 排除无效价格，保留原因。INVALID 行不是缺行：递推经过它时，连板已知性变为未知，不能跳过它后把前后涨停直接相连；下一次确定非涨停恢复已知0。MA20只用有效K线，该不同处理需独立验收。

实际交易但参考价缺失属于 limit UNKNOWN；实际无价格限制属于 NO_LIMIT，标志为 false，板数归零。INVALID/UNKNOWN 不能伪装成“没有涨停”。NO_LIMIT 的四标志必须明确为0；UNKNOWN/INVALID 的四标志必须为NULL。收盘涨停/跌停蕴含相应触及标志，两个收盘标志不可同时为1。limit_reason 使用有限原因码：missing_reference、missing_st、missing_listing_date、unsupported_action、invalid_ohlc 等；列表由现有规则映射固定，不新建异常实体。

参考价选择顺序：可靠日期来源候选 > 由前一有效收盘和区间除权事件计算 > 可用原日线来源候选。实现中来源适配器将 dated/direct 与 raw_fallback 两类候选明确分级；迁移如需同时保留二者，低优先候选放外部逐字段差异及可恢复源。source_pre_close 保存实际选定的来源候选并在 source 标签区分优先级，pre_close 可选中间的推导结果。旧 publication reference 仅在依赖可验证时重建/采用为派生值，绝不冒充永久 source_*。

ST 优先采用可靠日期来源；否则用该日有依据的 name 调用现有 classify_st_name；其余 NULL。两种可靠来源冲突必须进入迁移/更新错误明细，不凭更新时间择一。现有来源明确标为本地推导的值，要恢复成可重算值，不因为放在旧 facts 表就当作不可变来源。

同一权威来源的更正可以替换自己的旧值；不同可靠来源不一致才构成冲突。

源 upsert 的缺字段表示“不修改”，显式 NULL 表示该权威来源撤回该值并重新选择/计算；只有原权威来源可撤回其值，低优先来源不能清掉高优先数据；源请求失败和空响应不表示删除。旧 raw_fallback 被替代后的恢复依赖保存的外部迁移源或重新获取；不得承诺四表保留所有供应商的历次版本。

删除单根错误 K 线须提交显式 `delete_bar(symbol, trade_date, source)` 操作，适配器校验来源权限，按同一事务重算已支持频率的依赖；不能把列表遗漏解释成删除。删除后还有日期源事实则保留 NO_TRADE 特征行，没有任何源事实则删除该特征行。已存在市场日汇总重算为实际样本（必要时零样本），不因最后一根 K 线被删而隐去该市场日。公司行为删除同样要求精确来源键或明确完整集合替换；不增加墓碑/操作日志业务表。

### 2.3 corporate_actions：事件和稀疏因子

键 `(symbol, effective_date, record_kind, source, source_key)`。同日可以有多事件和多来源，三类行：

| record_kind | 内容 | 计算用途 |
| --- | --- | --- |
| event | category + payload_json 保留该来源事件全部字段及原单位 | 归一化每股分红、送转、配股后计算参考价；不认识的类别不静默当无影响 |
| factor_anchor | source_cumulative_factor + source | 原始累计因子来源；不得再次 cumprod |
| factor | 选定序列的 event_factor、cumulative_factor、factor_basis、valid_from/valid_through | 直接供 asof 查询的稀疏计算缓存；每股/生效日唯一 |

JSON 仅用于不同类型公司行为的原始载荷、小型梯队映射及固定涨跌幅分布计数，不用来藏整行行情或动态模型特征。source_key 优先用上游稳定事件 ID；无 ID 时由适配器用稳定业务标识/同日事件槽位生成，不能只用可变金额的 hash，否则更正会变成新增重复事件。同日无法可靠区分时以该股该日该来源的完整事件集合替换，必须由来源明确声明集合完整；普通不完整响应不能删除旧事件。

factor_basis 标明选用的来源及统一归一化依据。因子链首条可为基准锚点，event_factor=NULL；已有可靠前驱才能得到单次比值。事件路线和来源因子路线只能择一形成有效链，跨源拼接必须核对尺度，不能两套相乘。每个选定 factor 行的 valid_from=effective_date；valid_through 为下一选定因子生效日前一天与来源确认截止日的较早者。插入/移除/移动生效日时，同时修订前驱区间端点，禁止重叠或以过期前驱跨越未知区间；基准因子1同样要有明确起点。有效范围是来源已核实的计算区间，不是全市场 coverage 表或发布流程；需要延长时只修改该股末端有效因子记录。经核实无事件的范围用因子 1 的基准记录表达，不能用“没查到记录”证明没有除权。

调整价格：`adjusted(D, A) = raw(D) * F(D) / F(A)`，F 为所属有效区间的向后 asof 因子。先取请求起点前一条锚点，再读窗口内记录。A 默认请求 end，禁止默认为当前最新日而让历史请求依赖未来事件；仅调整 OHLC，volume/amount 保持原单位。无可靠因子范围报 unavailable，不能悄悄退回原价。

现金分红示例：原收盘 10，每股分红 1，参考价 9，事件因子 10/9；以事件后为 A，旧价变为 9。同日事件先归并成一次有效调整；限价取整使用现有 Decimal/价格档位规则，与展示价格精度区分。

修改事件仅重算必要的该股稀疏因子后缀及依赖窗口。最新除权会改变历史前复权展示，但不重写 daily_bars，也不重算所有历史限价。tick 的因子文件很小，整体重写本身不是已证实瓶颈；本方案着重避免无变化输入与每次全历史 enriched 重算。

### 2.4 market_daily_summary：市场公共 D/W/M 特征

键 `(frequency, period_key, scope)`。scope 首期仅 all_stocks/exclude_known_st。D 为日期；W 为 ISO 年周；M 为年月。period_start/end 是日历边界，as_of 是该行实际计算到的市场日期。仅市场交易日生成 D；全日无样本仍生成零数量、NULL 比例的行。

D 行的交易样本只包含实际有效交易。calc_status=INVALID 的观测单独进入 limit_invalid_count，不进入 trading_count、收益或成交分母；NO_TRADE 则两者都不进入。应满足 valid_return_count+invalid_return_count=trading_count，limit_known_count+no_limit_count+limit_unknown_count=trading_count；limit_invalid_count 不混进这些等式。以下分母彼此独立，不能统一除以证券总数：

| 字段组 | 定义 |
| --- | --- |
| trading_count/st_unknown_count | 本 scope 实际有效交易样本及其中 ST 未知数 |
| valid_return_count/invalid_return_count | 有有效 close/pre_close 的收益样本数及其余实际交易样本数 |
| up/down/flat_count；strong_up/down_count | r=close/pre_close−1；分别 >0/<0/=0、≥3%/≤−3%，3% 边界沿用 1e−12 容差 |
| return_distribution_json | 涨停/跌停单列，其余按固定区间统计，共15档；边界见下节 |
| avg_return/median_return | 有效 r 的均值/中位数，单位小数；up/strong*_pct 单位 0–100 |
| amount_sum/amount_valid_count | 实际交易中有限且非负金额的和/个数；无样本时 sum=NULL，有真实零金额时可为 0 |
| avg_turnover/turnover_valid_count | 有效源换手率均值/个数，单位保持百分点 |
| limit_known/no_limit/unknown/invalid_count | 实际交易的限价判定统计；缺行/停牌不进入 unknown；无效原始行单列 invalid，不当收益样本 |
| close/touch_limit_up/down_count | 可判定的相应事件数；炸板数=touch_limit_up_count−close_limit_up_count |
| sealed_ratio | 收盘涨停/触及涨停，分母零为 NULL，不设 100% |
| max_consecutive_up/streak_unknown_count | 可确定的收盘涨停高度最大值/未知高度涨停数；没有涨停为 0，只有未知高度则 max=NULL |
| first_board/second_plus/third_plus/fifth_plus_count；ladder_json | 已知高度=1/≥2/≥3/≥5；JSON 是每个确切已知高度对应人数 |
| promotion_eligible/success_count；promotion_ratio | 今日实际可判定且上一有效交易为已知涨停的样本；今日仍涨停才成功；分母零为 NULL |
| ma20_valid/above_ma20_count；above_ma20_pct | 今日实际交易且有 MA20 的样本、站上数及 0–1 比例 |

exclude_known_st 在 D 上只排除该日有效 is_st=True，未知仍计入并明确数量。W/M 日特征聚合保持各日 scope；原生周期收益按周期 as_of 的可靠日期 ST 事实筛选，不能混用名称快照。as_of 日无 ST 依据则未知，不擅自前填。

W/M 的 D-only 字段均 NULL，使用 DDL 明确命名的周期字段：

- session_count 是截至 as_of 的市场交易日数，包含全市场没有有效样本的市场日。trading/valid_return/st_unknown_observations 分别为日计数求和，是人次。
- daily_up_pct_weighted、daily_strong*_pct_weighted = 对应日人数之和 / valid_return_observations ×100；daily_avg_return_weighted 按日有效人数加权；daily_median_return_mean 是有值日中位数的平均，并给出 daily_median_valid_sessions。后者明确不是周期收益中位数。
- daily_limit_up_mean = limit_up_occurrences / session_count。limit_up/down_occurrences 和 limit_touch_* 为日事件人次之和；limit_up_symbols 对周期逐股事件去重。
- sealed_ratio 为整周期封板人次/触板人次。晋级同样按人次合计求比，绝不平均每日比例。
- end_max_consecutive_up 与 MA20 数量/比例取 as_of 当日；period_max_consecutive_up 取各日已知最大值。部分高度未知由 streak_unknown_observations 保留。
- amount_sum/amount_valid_count 为日和，avg_turnover 按 turnover_valid_count 加权。全周期无有效金额/换手样本时保持 NULL。
- period_*_return/count 为真实周期表现：有周期内有效交易的股票，取周期前最后有效收盘与周期内最后有效收盘，按同一因子尺度求收益。新上市缺前基准为未知；无周期内交易不计入。period_unknown_return_count 记录有交易但缺基准/因子的样本。不得把日中位数均值放入 period_median_return。

完整D/W/M能力启用后，先重算受影响 D，再按依赖重算 W/M，不能只取变更日期所在周期。日特征聚合只读小型 D 行；去重和原生周期收益读取目标周期的逐股必要列及各股前驱。允许全市场一个月的有界扫描，不允许每个模型扫描全年明细。如果这一事务成本超预算，S2 优化查询/收窄未经使用的字段再验收，不能改成先提交 raw 后异步补汇总。

### 2.5 涨跌幅分布：首期必需的广度特征

2026-09-28 用户明确口径：涨停、跌停各自单列；其余股票采用下闭上开区间；用户随后确定每2个百分点一档，即 `(10%—8%]`、`(8%—6%]`……，从小到大表示为 `[8%,10%)`、`[6%,8%)`。以下取代此前“所有股票同时进收益桶”的草案，不增加逐股分桶列或第五张表。

**D 先分涨跌停，再分剩余有效收益样本，分类互斥。** 涨跌停使用已有 close_limit_up/down 标志，不从涨幅数字猜测；触及但未收盘涨跌停的股票按实际涨跌幅入普通区间。普通收益用有效 close/pre_close 计算，不直接使用供应商 pct_chg。固定15档：

| 档位键 | 区间或条件 |
| --- | --- |
| limit_up / limit_down | 实际收盘涨停/跌停；优先分配，从下列区间移出 |
| pos_ge_10 | 未收盘涨跌停，p ≥ 10% |
| pos_08_10 … pos_02_04 | 未收盘涨跌停，[8%,10%)、[6%,8%)、[4%,6%)、[2%,4%) |
| pos_00_02 | 未收盘涨跌停，0% < p < 2% |
| flat | 未收盘涨跌停，p = 0 |
| neg_00_02 | 未收盘涨跌停，−2% < p < 0% |
| neg_02_04 … neg_08_10 | 未收盘涨跌停，(−4%,−2%]、(−6%,−4%]、(−8%,−6%]、(−10%,−8%] |
| neg_le_10 | 未收盘涨跌停，p ≤ −10% |

负向按幅度对称：恰好−8%属于 neg_08_10，恰好+8%属于 pos_08_10。未涨跌停的±10%归两端档；真正涨停即使实际涨幅为9.96%，仍只计 limit_up，不再进8%～10%区间。超出±10%但未收盘涨跌停的有效样本不能丢弃。limit_up/down 与已有 close_limit_up/down_count 保持一致，展示时不能再把这两个计数额外加一次。

边界对规范化价格用 Decimal 比较 `abs(close-pre_close)*100` 与 `k*pre_close`（k=2、4、6、8、10），不要先四舍五入涨跌幅。平盘严格 close=pre_close，微涨/微跌不归零。负向分档对绝对幅度使用与正向相同的下闭上开规则，平盘从零点移出。两种收盘限价标志同时为 true 是非法输入，不能任意选择一档。

JSON 包含所有固定键和非负整数人数；零档也保留键。无样本为全零对象，比例因分母为零为 NULL；NULL 对象表示该频率不适用/未计算，不当全零。writer 校验键集合、整数类型和人数守恒；DDL CHECK 只校验 JSON 对象格式。公开 DataFrame 返回 JSON 文本，消费适配器解码；各档比例由人数/有效收益样本数计算，不另存一套比例。

| 字段 | 适用频率 | 定义及守恒 |
| --- | --- | --- |
| return_distribution_json | D | 15档互斥股票数之和 = valid_return_count；其中限价两档与当天收盘涨跌停计数一致 |
| daily_return_distribution_json | W/M | 逐档累加 D 的15档；总和 = valid_return_observations，单位股票交易人次；限价档等于相应周期限价人次 |
| period_return_distribution_json | W/M | 根据每股真实周/月收益分成13个数值区间（没有 limit_up/down 档）；总和 = period_valid_return_count |

原生周/月收益不存在一个统一的“本周涨停价/本月涨停价”，不能凭期末一天是否涨停，把整周/月股票移到限价档。因此原生周期收益分布全部按13个数值区间统计，限价观察通过并列的日分布累计字段读取。原生周期分桶使用规范化十进制收益，不先按展示精度舍入；其正/负/平档分别等于 period_up/down/flat_count。

D 限价已知的涨跌停样本必须有有效收益参考价；否则属于输入/派生不一致，不能拿互斥分布掩盖。限价未知但收益有效时按实际收益分普通桶，继续在 limit_unknown_count 中披露，不能说已确认不是涨跌停。缺 pre_close 的真实交易不进分布，保留在 invalid_return_count；缺行/停牌不进分布和分母。

既有 up/down/flat 和正负3%家数继续按真实收益直接计算，不能仅从移除了涨跌停的普通桶反推：限价标志是价格规则事件，是否涨跌仍由实际收益决定，不能把 limit_up/down 桶固定当作某一百分比的收益桶。更精细的阈值无法由整档无损推导时，必须新增明确公共特征，不让每个模型回读全年行情。

D 分桶与现有收益截面聚合同次完成，不新增全市场扫描。W/M 日分布累计只读 D；原生周期分布与已有 period_* 收益同次计算。close/pre_close、ST 或限价判定变化都会更新受影响 D 和相交 W/M（例如仅 high/low 改动是否影响收盘标志，由实际判定结果决定）；金额单独变化不重算分布。首个四维评分模型不因新增统计自动修改公式。

调用样例（目标 API，尚未实现）：

```python
breadth = pool.read_market_summary(
    start="2025-01-01", end="2025-12-31", frequency="D",
    fields=["period_key", "valid_return_count", "return_distribution_json"],
)
# Parsed return_distribution_json includes limit_up and limit_down already.
```

## 3. 局部更新、递推和缓存

### 3.1 统一 writer

所有股票来源调用一个内部入口；获取、限流、重试在事务外。下列是目标签名，不是已存在的 SDK 方法：

```python
def apply_stock_changes(
    changes: StockChanges, *, budget: UpdateBudget = NORMAL_BUDGET,
) -> UpdateResult: ...
# StockChanges: bars, dated_facts, actions, explicit_deletes；省略、NULL与删除不同。
# 每条包含规范键、权威来源、实际修改字段；不接受任意 SQL 或文件替换。
# UpdateResult: changed_rows, affected_sessions, recomputed_feature_rows,
#               summary_rows, elapsed_ms；没有 batch_id。
```

普通业务默认获取最近 5 个市场交易日；首期防误操作建议最多 10 个不同输入交易日、100,000 条实际源变更、250,000 条逐股派生重算行、60 个受影响市场交易日，writer 30 秒截止。均为 S2 必须实测调整并记录的初始守卫，不是已测性能承诺。上述行数守卫不是 SQL 扫描量上限；预热和周期截面扫描另计入读成本并受总时间预算约束；时间预算须结合 sqlite progress handler 检查，不能只在 SQL 后计时。业务调用不得自行放大守卫；外部 ops 可显式使用维护预算。

流程：规范化/按键合并输入 → BEGIN IMMEDIATE → 对实际语义字段比较 → 更新源值 → 计算实际依赖 → 每个受影响日/scope 聚合一次 → 更新 W/M → COMMIT。比较不包含抓取时间；浮点按规范化后的实际值比较，不用大容差吞掉真实修订。无变化不更新 updated_at、不调用派生。超时/超范围/计算失败回滚，报 LOCAL_UPDATE_BUDGET_EXCEEDED 或具体数据错误；调用者安排明确范围的 ops。

公共目录/生命周期/交易日历在计算前读取为小型固定输入；股票事务不声称能与现有 catalog 跨库原子提交。其历史规则或日期修订由维护入口协调停写和受影响派生重建，不能在读端自动全历史修补。正常增加未来交易日不触碰无关历史。

不需要持久消息队列、event/outbox 表或全局 revision。这里的“事件触发”就是本进程在事务中调用计算函数。输入较大时在事务外按日期收集、分段提交；一个日期缺少某些证券不声称全市场完整，但现有已提交事实及其派生始终一致。

### 3.2 按字段传播

| 实际变化 | 必须重算 | 不应发生 |
| --- | --- | --- |
| amount、volume、turnover、估值 | 受影响日适用汇总、相交 W/M；金额 JOIN 缓存 | 全股历史限价/MA20 重算 |
| high/low/open | 当日限价与数据有效性；有效交易身份变化时另传播 | 仅 high 变化就重写所有股 K 线 |
| close、pre_close | 当日收益/限价；下一有效交易参考价；连板实际后缀；受影响 MA20 窗口 | 在板数相同处停止所有其他依赖 |
| 新增/移除有效 K 线或停牌纠正 | 前后有效交易链接、20 根窗口、连板及对应 D/W/M | 缺行补成平价零成交行 |
| name/日期 ST | 当日 ST scope、限价及连板后缀；相交周期 scope | 当前名称覆盖多年历史 |
| 除权事件/来源因子 | 该股有效因子后缀、区间参考价、跨变动边界的 MA20/周期收益、复权缓存 | 所有历史原始 K 线重写 |

MA20 close 修订最多影响以该根为窗口成员的 20 个有效交易位置；插入/删除可能改变后续成员，须比较实际窗口直至恢复原成员。受参考价变更影响的下一根 K 线另算。连板只在完整状态（上次有效交易位置、已知性、板数等）相同且无更晚待处理源变化时停止；遇到很久没有非涨停的历史链，允许真实后缀较长，不能假定固定 5 天足够。

缺行/停牌冻结状态：已知板数 2 → 间隔任意缺行/停牌 → 下一次涨停为 3，下一次确定非涨停为 0。真实交易但涨停未知则连板也未知；后续确定非涨停恢复 0。首次历史即涨停且无可靠前驱时板数未知，不默认首板。上市无价格限制期沿用已核实规则/市场日历，不能按可见 K 线条数推断。

历史修改还须查找依赖该股价格作为“周期前基准”或“周期末价”的后续周期。例如1月末收盘修订，即使2月首日有可靠独立 pre_close、其日收益未变，2月原生周期收益仍可能变化。定位旧/新前驱与下一有效交易，按有交易的实际周期枚举依赖；无交易整个周期不造收益样本。超过正常预算仍整笔回滚交 ops，不能只修当周/月后提交。只有来源因子变化、尚无有效日收益变化，也可能改变周期收益或其可用性。

as_of 当日仅 ST 日期事实修订、没有 K 线，也可能改变该股原生 W/M scope；必须检查该期内是否有其他有效交易。scope 变化需更新旧/新两个范围的依赖时间。no-op、收敛判定针对所有依赖，不能只比较限价标志或日汇总数值。

### 3.3 更新时间与消费者缓存

updated_at 仅在行内容/相关依赖实际变化时更新，单 writer 取 `max(UTC微秒, 本事务受影响旧行最大值+1)`，不建全局序列表。股票某日成交额/事件等有变化，即使均值/总和恰好相抵，仍触碰该日 summary 时间，供消费逐股数据的主线缓存识别；W/M 同理。因此 summary.updated_at 表示该 scope 周期的输入变化，而不仅是最终数值变化。

缓存对所需日期/周期的 `(key, as_of, updated_at)` 完整序列做指纹，包含预热/递推依赖及新增/删除键，不能只取 max(time)。相邻库的指数和板块输入分别加入内容指纹；读取小型输入后关闭快照，输出前复核依赖，变化则丢弃本次结果。数据库恢复/替换必须清理相应应用缓存，时间戳不提供跨恢复的版本史。

## 4. 读接口和错误语义

以下是拟交付接口；旧入口参数保留细节见[迁移评估](sqlite_migration_api_assessment.md)。字段白名单编译为 SELECT 列，用户值通过绑定参数传入。

```python
class DataPool:
    def read_snapshot(self) -> StockReadSnapshot: ...
    def read_daily(self, symbols=None, start=None, end=None,
                   lookback=None, fields=None) -> DataFrame: ...
    def read_research_daily(self, symbols=None, start=None, end=None,
                            lookback=None, fields=None) -> DataFrame: ...
    def read_daily_features(self, *, start, end, symbols=None,
                            fields=None) -> DataFrame: ...
    def read_market_summary(self, *, start, end, frequency="D",
                            scope="exclude_known_st", fields=None,
                            closed_only=True) -> DataFrame: ...
    def read_market_daily(self, *, start, end,
                          scope="exclude_known_st", fields=None,
                          closed_only=True) -> DataFrame: ...
    def read_adjustment_factors(self, *, symbols, start, end,
                               include_predecessor=True) -> DataFrame: ...
    def read_adjusted_daily(self, *, symbols, start, end,
                            anchor_date=None, fields=None) -> DataFrame: ...
    def iter_limit_events_with_amount(self, *, start, end, symbols=None,
            fields=None, close_limit_up=None, min_consecutive_up=None,
            chunk_days=None, batch_days=None, max_rows=25_000, memory_limit="512MB",
            threads=2, temp_directory=None) -> EventAmountIterator: ...
```

- read_snapshot 在进入时执行只读 BEGIN 并实际读取以固定 SQLite 快照；StockReadSnapshot 只暴露股票读接口。其创建的 iterator 不得活得比外层上下文更久，退出关闭所有 cursor/事务。独立 DataPool 读方法自行管理短快照。指数/ETF/公共 catalog 不在同一事务，不能伪称跨域原子快照。
- read_market_daily 等价 frequency=D 的 read_market_summary，并增加白名单别名 trade_date=period_key；通用接口仅用 period_key。is_closed 是读取时依据日历、as_of、当前时间算出的布尔输出，不是持久列。scope 显式默认 exclude_known_st；旧 read_limit_summary 为保持全市场旧含义固定映射 all_stocks，不能受新默认值影响。
- daily/read_research 仍输出未复权数据、公开35字段及原单位，50万行 DataFrame 上限；字段/符号/日期/空结果语义兼容。read_daily_features 同样有上限，包括无 K 线日期事实。大范围逐股普通行情后续需要流接口时单独实现，不能绕上限直接 materialize。
- read_adjustment_factors 返回选定因子记录及有效区间、basis；include_predecessor=True 每股至多多出一条早于 start 的锚点，明确标识 is_predecessor。新 read_adjusted_daily 保持 read_daily 不变，anchor_date 默认 end、不得晚于 end；无因子依据抛 ADJUSTMENT_UNAVAILABLE，并列受影响符号/范围。
- iter 在一个 SQLite 读快照中，daily_features 与 daily_bars 用主键 JOIN；只输出四标志至少一个 true 的稀疏事件及 requested 字段。以 `(trade_date,symbol)` keyset 分页，不用 OFFSET；单日超过 max_rows 继续股内分页。fields 不会让金额未请求时也加载金额。
- chunk_days 是新分页参数，batch_days 仅为兼容别名；均未传时为7，两者同传报 INVALID_ARGUMENT。天数1..31、max_rows为1..100000，均拒绝布尔值；max_rows控制每页而非单日总数。这是旧版单日过大时报错行为的显式改善，不代表业务批次。memory_limit/threads/temp_directory 在 SQLite 路径属于废弃的 DuckDB 参数：兼容期接受现有调用并返回 backend=sqlite、legacy_options_ignored 元信息及一次 DeprecationWarning，不能谎称 SQLite 按该参数限制总 RSS。内存有界由分页实现并实测。
- iterator 的 context manager 保留 `.completed`：正常穷尽 true；提前退出 false；退出关闭 cursor/事务。处理中出错立即抛错，不返回看似完成的半份结果。不能为了释放 WAL 在中途换快照拼接两版数据；运行预算不足则明确终止本次读取。
- read_limit_summary/events 保留兼容投影，去掉 batch_id/stale/publication 字段。coverage/exceptions/revisions 接口在 v3 移除并有明确迁移错误，不返回虚假空表遮掩旧消费者依赖。
- 保留现有 INVALID_ARGUMENT、FIELD_UNSUPPORTED、DAILY_TOO_LARGE、DAILY_INVALID、POOL_NOT_FOUND 等错误码；日特征/复权 DataFrame 超限同样 DAILY_TOO_LARGE。新增 SOURCE_CONFLICT、ADJUSTMENT_UNAVAILABLE、HISTORICAL_SNAPSHOT_UNAVAILABLE、SQLITE_BUSY、SQLITE_CORRUPT、SQLITE_READONLY、SCHEMA_UNSUPPORTED、API_REMOVED、LOCAL_UPDATE_BUDGET_EXCEEDED；新汇总读取未知字段也用 FIELD_UNSUPPORTED。尚未实现频率报 FREQUENCY_NOT_READY；已支持频率但日期范围未回填报 FEATURE_NOT_READY，不返回貌似真实的零值。按现有 DataPoolError 类型映射，不向用户暴露 SQL/栈代替业务原因。

调用样例（目标 API）：

```python
from aspool import DataPool
pool = DataPool(root="/mnt/d/Workstation/Services/tdxman/data")
k = pool.read_daily(symbols=["000001.SZ"], start="2006-01-01",
                    end="2025-12-31", fields=["symbol", "date", "close", "pre_close"])
weekly = pool.read_market_summary(start="2025-01-01", end="2025-12-31",
    frequency="W", fields=["period_key", "daily_up_pct_weighted", "amount_sum"])
with pool.iter_limit_events_with_amount(start="2021-01-01", end="2025-12-31",
        fields=["symbol", "trade_date", "amount"], close_limit_up=True,
        max_rows=25_000) as chunks:
    for chunk in chunks:
        consume(chunk)  # Do not collect every chunk into one list.
    assert chunks.completed
```

W/M 选择与 start/end 相交的周期，默认只返回已闭合且期内最后市场日不晚于 end 的周期。跨年首周允许 period_start 早于 start，必须返回完整周期边界。当前周期闭合需满足交易日历判定的最后市场日已经结束，且 as_of 已到该日；不新增 is_published 状态。仅日期到了不能假称尚未抓取的末日已完成；这里的闭合也不保证每股数据完整。closed_only=False 只能直接返回 as_of≤end 的已存值；若保存值晚于历史 end，则报 HISTORICAL_SNAPSHOT_UNAVAILABLE，不裁掉行标签冒充历史快照。实时查询不内置历史周中重建。

## 5. Regime 模板和模型结果

公共计算在 aspool 完成；Fundwise 读市场特征和小型指数序列，执行纯评分。完整参数样例见 [market_four_dimensions.v1.json](design/market_four_dimensions.v1.json)（JSON 是合法 YAML 子集，可直接作为结构化模板加载）。模板对象一个数据类即可；calculator 从固定注册表选择，不运行模板中的任意代码/SQL。

D 参数来自 Fundwise `1b256585` 的 score_market；W/M 首个模板是“周期内日市场状态”的参考模型：每日涨跌比例按样本数加权、封板率按人次、涨停家数取日均、连板/MA20 取期末；指数使用真实周期收益。W/M 显式继承 D 阈值，标 experimental_uncalibrated，不能暗称与日评分统计等价或已验证投资效果。原生周期收益分布另有 period_* 字段，模型选用时须另立模板版本。

模板加载校验：ID/版本、字段白名单、单位、支持频率、有限数、low<high、每维及总权重和=1、状态边界不重叠、输出映射；参数只允许覆盖模板实际声明的路径；首个样例仅权重/归一化上下界，不开放没有实现的窗口或任意字段。子项先线性归一化到 0–100，Python round（ties-to-even）后 clamp；维度加权不再 round；总分最后 round，再按降序 ≥70/55/45/30 分类。缺一维保留其他维，但总分/state=NULL。未知限价或高度的真实交易按模板 strict 策略使投机维不可用；缺行/停牌不制造未知或阻断评分。需要已知子集评分时另立显式模板参数并标明输入质量。

目标应用接口：

```python
def compute_regime(model_id: str, start: str, end: str, *,
                   frequency: str = "D", parameters: dict | None = None) -> DataFrame: ...
def compute_regimes(model_ids: list[str], start: str, end: str, *,
                    frequency: str = "D") -> dict[str, DataFrame]: ...
```

多模型调用按 scope/输入并集读取一次，参数变化只重算评分。模板存 `Fundwise/config/regime_models/`；结果在应用数据目录 `regime/<model_id>/<template_and_parameters_hash>/<frequency>/<scope>/<period_year>.json` 按年组织，临时文件+原子 rename；同键写锁避免两个 worker 覆盖。period_year=D/M的自然年、W的ISO周年（period_key前四位），避免跨年周重复写两个文件。缓存文件保存必要依赖指纹，不保存另一份逐股历史。JSON 样例保持 .json；生产加载器显式支持 .json 与 .yaml/.yml（safe_load），不能仅把 .json 文件丢进只扫描 *.yaml 的目录。

阶段/周期平滑是独立注册计算器，首个四维模板不偷偷附带阶段状态。已有 Fundwise cycle 需显式接入同一输入；递推模型保存其必要前驱状态到结果文件，有缺口时从可靠前驱有界重算；没有可靠前驱就报告需要初始化，不能用固定60自然日保证所有模型。窗口参数单位是当前频率的周期数，D=20 不自动等于 W/M=20 的同样时间长度。

## 6. 迁移、实现次序与验收样例

源规模、完整消费者清单及字段映射见[只读迁移评估](sqlite_migration_api_assessment.md)。下列是职责划分；执行顺序已按用户最新要求改为 S1→S3a事实迁移→S2-D派生→S4-D接口/Fundwise，详见[工作包与M1](sqlite_four_table_implementation_plan.md#9-实现工作包依赖与提交计划)。

1. **S1：存储与代表样本。** 实现四表连接/字段适配，分别选普通股票、长期停牌、历史 ST、IPO、同日多公司行为、只有来源因子、无 K 线日期事实。先验证 effective pre_close 与旧公开 overlay，再验证新规则。DDL 当前只是草案，并非 S1 已完成。
2. **S2：一个日期的完整写入。** raw/facts/actions → 逐股派生 → D/W/M；无变化、补数、故障回滚、预算超限、有界读。先实测每个分支访问范围和 writer 时间，再固定运行参数。
3. **S3a提前：外部 ops 全量事实迁入；S3b随派生模块回填。** 显式不同 source/target；股票/ETF分类、43字段、日期事实、186,622源事件及58,677因子以冻结快照重新计数对账。旧派生只作对比，新派生按新规则生成；外部清单保存进度和源冲突，不带批次入新库。
4. **S4：消费者适配。** Fundwise 移除旧批次/全池 freshness 扫描，接公共特征、模板、窗口缓存和有界主线明细；tick local daily/ETF/index 契约保持。旧消费者不能混读新旧统计。
5. **S5–S7：全量性能/恢复 → 维护窗口切换 → 退役清理。** 新写入之后的回退必须保全新增变更，不能直接换回过期旧备份。没有新的全链路证据不清旧权威源。

最小语义验收集：

| 场景 | 确切预期 |
| --- | --- |
| 2板→缺行→涨停；2板→停牌→涨停 | 两者第三次有效交易均3板，中间不计涨跌/成交 |
| 上例补入一根非涨停 | 补入日0板，后日1板，传播至与原完整状态收敛 |
| 实际交易缺 pre_close | 收益未知、限价未知，不把它计为停牌或平盘 |
| 原始字段相同重复抓取 | changed_rows=0，所有 updated_at 不变，派生调用0 |
| 仅两股金额一增一减，市场总额不变 | 原始两行和该日依赖时间改变；主线缓存失效，限价不重算 |
| 历史 close 更正 | 当日/下一有效参考价及真正跨该价的 MA20 窗口重算；其余冷历史不写 |
| 10元每股分红1元；来源累计因子10/9 | 旧价调整9；选择来源累计因子时不再叠乘事件10/9 |
| 新股首根可见历史涨停无前驱 | 连板未知，不假首板；后续确定非涨停可恢复已知0 |
| 所有股票当天缺失/停牌 | 数量0、比例/均值NULL；模型不可评分，不是0分或50分 |
| 周内两天同一股票涨停 | occurrences=2，symbols=1，不把两者混用 |
| 非涨跌停的−10%、−9%、−1%、0%、+1%、+9%、+10% | 负端、负8～10、负0～2、平盘、正0～2、正8～10、正端；各进一档 |
| 实际涨幅9.96%且收盘涨停 | 仅 limit_up 档计1，pos_08_10 不再计入 |
| 同一股两日均涨1% | 未涨停时日分布累计为 pos_00_02 的2人次；周收益2.01%时原生周分布为 pos_02_04 的1股 |
| 两日封板/触板为1/1、1/9 | 周封板率2/10=0.2，不是每日比例均值 |
| 历史 end 在周三、库中已存周五 | closed_only=True 排除；False 报历史快照不可用 |
| 周期前一收盘修订、下一日源pre_close保持不变 | 下一日收益可不变，后续依赖该基准的周/月原生收益仍更新 |
| 涨停→INVALID观测→涨停 | 后次高度未知；不能当停牌冻结后直接累计 |
| facts-only 的周期末ST修订 | 若周期内有交易，重算对应原生周期scope |
| 两模板同输入不同权重 | 特征只读一次、两个结果互不覆盖 |
| 提交前进程中断 | 事实与派生全旧；提交后响应丢失重试为无变化 |

有别名/来源冲突未解决就不把“源行数相同”作为迁移通过；迁移允许变化仅限明示新统计口径，逐项给出归因。性能要求和1×/2×/5×增长场景沿用实施计划；设计验证只证明 DDL/样例自洽，不证明五年 RSS、全量容量或真实迁移成功。

首发阶段未回填的历史日，普通writer不可仅算被修改一股便生成看似完整的市场汇总；返回 FEATURE_NOT_READY，安排ops在隔离/维护模式初始化明确日期范围后再走正常局部更新。新增末端市场日则对既有历史状态执行正常当日初始化。D汇总行只在该日全部已有有效输入已完成派生的事务中生成；就绪范围按交易日逐日检查，不能仅用min/max两端表示中间全部就绪。

## 7. 本轮设计验证与未决实施证据

本轮使用隔离内存 SQLite 检查四表创建、关键约束、典型查询计划；检查旧字段映射目标列与完整模型模板；用当前 Fundwise 纯评分函数对照 D 模板算术。涨跌分布增量设计检查见[分桶验证记录](evidence/sqlite-migration-assessment/20260928-distribution-design-validation.json)。原有检查结果见[设计验证记录](evidence/sqlite-migration-assessment/20260928-design-validation.json)。未运行生产同步、迁移、重算或旧 agent 测试。

仍须 S1/S2 通过真实样本确定：多源参考价/因子冲突数量及处置、历史名称日期可靠性、周期扫描成本和30秒初始守卫是否适合实际机器。仍须 S5 证明：全量逐值对账、五年≤2GiB、新库磁盘占用、长读 WAL、并发/故障与恢复。不会把设计样例通过写成生产验收通过。


## 8. 跨设备同步需求（R10）

首期按 VPS 唯一行情 writer、接收端只读副本设计；这是当前实现假设，用户尚未单独确认多写需求。配置必须声明 source/replica 角色，replica 禁用行情更新入口；本地模型结果和用户配置在其他目录/库。若部署实际存在两端行情写入，拒绝按单向方案上线并重新评估冲突合并，不能覆盖本地独立数据。

首选试点 sqlite3_rsync：两端锁定同一经验证版本，经 SSH 由接收设备主动拉取，首次建立库，之后同步差异页面；VPS 正常更新事务完成后可触发外部调度，设备离线后自行补拉。其一致性为同步开始时的单库快照，不要求重放每天一份自建业务补丁。两端需要安装专用工具；Python 的 sqlite3 版本不能代替该二进制版本。[SQLite 官方说明](https://sqlite.org/rsync.html)

这里是网络传输增量，页面摘要计算仍有随库增长的扫描成本；不得将它当作“全流程仅访问热点”的验收证据。FW-09 分别记录首建、无变化、日更、旧日修订/删除、离线追平的两端扫描/写入/网络/RSS/耗时及WAL增长。若增长试验不满足设备同步窗口，再评估 Litestream 日志追随；其 restore -f 支持只读本地副本，但需持久恢复位置、保留窗口及重建策略。首期不同时引入两个生产同步通道。[Litestream 官方说明](https://litestream.io/reference/restore/#follow-mode)

同步作用于整个 stocks.sqlite，包括索引和 schema，因此接收端必须校验 user_version/读契约并在应用升级顺序不兼容时暂停接入；不向新库加入同步批次表。它是镜像而非保留历史的备份，误删也会同步，仍需独立恢复点。验收按表/字段及索引有效性，不要求整个数据库文件字节hash一致（工具可能修改文件头）。普通 rsync/cp 不得复制活跃主库充当一致副本。

接收端每库同步任务串行，运行时仅同步进程可写；应用用 mode=ro，不能设置 immutable=1 忽略正在更新的副本。普通查询用短事务，既有快照读完后新请求看到新数据；模式变化/恢复回退需关闭旧连接并清对应缓存。网络中断、进程中断和重复运行都要实际验证，不以文档承诺替代本机试验。同步状态/错误日志放外部文件；不得把 max(trade_date) 相同当作历史修订已同步。

指数、ETF、公共目录/交易日历属于独立域，必须列入接收端依赖清单；首期不改它们的存储格式。活动 Parquet/catalog 不能裸拷贝：由源端现有受控锁/备份入口形成稳定输入，再传必要差异。旧目录无可靠差异清单时扫描成本也要计入，不能伪称与股票库一样页级同步。各域依次同步不具有跨库原子性；应用检查所需日期/字段就绪和小型输入指纹，不混用失败一半的更新去覆盖已缓存结果。涉及历史公共规则变更时协调维护窗口，普通日更不恢复业务 publication 流程。

远端地址、SSH认证、接收设备路径/操作系统、日更触发时间、允许离线时长及同步时间预算在 FW-09 的部署配置阶段确定；这些不阻塞隔离存储/算法实现，本轮不连接实际 VPS、不安装同步服务。
