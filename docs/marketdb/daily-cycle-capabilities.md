# MarketDB 四层每日周期与来源能力矩阵

状态：当前设计讨论稿；2026-10-05。[架构](logical-architecture.md) · [事件流程](event-state-workflow-pattern.md)

## 1. 外部来源与 MarketDB 入口

表中 CLI/API 属于 **raw 文件的外部生产端**。MarketDB 不运行这些命令、不直接请求这些 API；它只接收完成交付的文件。文件导出和伴随的来源、日期、单位、完整性描述须由外部生产流程补齐。已有命令不代表已经具备完整交付合同。

| 数据域 | 外部生产 CLI／API | MarketDB raw 入口及维度 | staging → core | factor 用途／缺口 |
| --- | --- | --- | --- | --- |
| 证券目录 | `tdxman stock-codes [ALL/SH/SZ/BJ] --format json --output FILE`；沪深 `TdxClient.get_security_count/get_security_list`，BJ `MacClient.get_stock_quotes_list` | 证券×市场／板别×来源观察时刻的目录文件 | 身份映射、名称标记解析 → 当前目录、名称变化及日状态 | 候选集合；来源日期不能等同运行日期 |
| ST | 外部目录主文件；`tdxman abnormal-list --type st --format json --output FILE` 可核对 | 全目录和风险子集；事件文件另交付 | 完整性核验、类型归一 → ST 观察／事件／日期状态 | 限价与风险统计；子集不足以单独判断摘帽，两命令不是独立来源 |
| 上市／退市 | `tdxman symbol-info SYMBOL --source baostock`／`BaostockClient.get_stock_basic`；正式公告由外部生产端取得 | 证券×事件／相关日期×证据的文件 | 事件类型、日期归一 → 生命周期与上市阶段 | 沪深资料已有；BJ 和其它事件覆盖待补 |
| 停复牌 | `tdxman abnormal-list --type suspended`／`TdxClient.get_security_quotes`；显式备用来源由外部选择 | 有来源日期的状态／事件文件 | 检查缺失和完整性 → 交易状态事实／日状态 | CLI 已采用 AxData 单步报价握手并通过全市场完整性实测；尚需外部补齐来源交易日期，失败报告不能当作有效空停牌清单 |
| 未复权行情 | `tdxman kline SYMBOL --period DAILY --adjust NONE --start OFFSET --count N --output FILE`／`MacClient.get_stock_kline`；free-stockdb 存量文件 | 证券×行情日期×资产类型，OHLCV与金额 | 单位、日期、价格尺度校验 → 标准未复权行情 | 量价、技术指标、聚合；全历史需生产端覆盖与分页保证 |
| 财务／股东 | `tdxman finance SYMBOL`／`TdxClient.get_finance_info`；现有 `DataPool.read_fundamental_reports/read_shareholder_counts` 可由外部导出 | 证券×报告期／统计日×来源的文件 | 日期和金额归一 → 基本面历史 | 完整历史及披露时点仍有缺口 |
| 日期股本 | 外部财务／股本事件交付，暂无完整独立 CLI 合同 | 证券×股本类型×有效日期×单位 | 单位及时间校验 → 日期股本 | 换手率、市值；历史口径需明确 |
| 股票复权事件 | thsman 权威全历史交付；导出 CLI 参数尚未核实 | 证券×经济事件生效日×参数×来源版本文件 | 参数单位、币种及重复校验 → 事件、修订与参考状态 | 本地因子与复权价格；既有 `aspool.event_baseline.install` 只是旧系统能力，不是新入口 |
| ETF 因子 | free-stockdb 等已审核外部文件 | ETF×日期×来源基准 | 归一尺度 → ETF 来源因子 | ETF 复权；保留真实来源 |
| 交易日历 | `tdxman workdays --start DATE --end DATE`／`WorkdayService`；外部导出 | 市场×日期×确认范围文件 | 会话、时区、覆盖校验 → 日历 | 已观测日期不能冒充所有市场的未来安排 |
| 分类／成员 | `tdxman board-list --type ...`、`tdxman board-members BOARD`／对应 MacClient 方法 | 板块／指数×成员×集合版本文件 | 身份映射与完整性校验 → 分类及成员历史依据 | 板块统计；当前来源不证明采集前历史成员 |
| 外部复权参考 | `tdxman kline SYMBOL --adjust QFQ/HFQ`／`MacClient.get_stock_kline` | 证券×日期×来源复权基准文件 | 尺度归一 → 可选独立参考集 | 对照用途；不覆盖 NONE 或本地 factor |
| 日状态与统计集合 | 无外部 collector 入口 | core 内部由已发布事实及规则产生 | core 目录、事件、会话、行情质量 → 日期状态及范围 | factor 涨跌停、连板、横截面依赖 |
| 指标／特征／聚合 | MarketDB factor 任务待实现；现有 DataPool 读取能力仅作消费合同参考 | 从 core 版本及上游 factor 读取 | 不通过 raw 绕过计算依赖 | 因子、MA、BIAS、量比、换手率、市场及板块统计，供 Fundwise |

## 2. 每日导入与发布流程

1. 外部生产端完成当天各数据域文件及交付描述，不将正在写入的文件作为完成包。
2. raw 验收文件、摘要、格式、来源范围、时间、单位、交付类型和完整性；登记批次。
3. staging 解析与清洗，完成代码映射、单位／日期归一；隔离异常并登记是否使范围失去完整性。
4. core 先处理身份、目录、日历，再按依赖处理行情、事件与日期状态；发布通过校验的版本。
5. factor 按依赖计算，标记缺失／未知输入，发布匹配 core 版本的结果与覆盖信息。
6. 历史更正或迟到文件按新批次处理，重建受影响状态并重算相关 factor。
7. 缺口转成外部补充交付需求；MarketDB 不自行调用 collector 修复。

时间顺序不是要求所有文件同时到达。可以按已具备的域推进，但在发布边界明确依赖就绪情况，不用前一天文件假扮当日完整输入。

## 3. 目录标记与 core 状态

| 观察 | staging 清洗 | core 业务处理 | factor 使用约束 |
| --- | --- | --- | --- |
| 新证券 | 代码／市场映射，保留原名 | 无论是否带 N 都建身份；首次发现不是上市日 | 上市阶段需日期依据 |
| ST/*ST/SST/S*ST | 解析类型，保留组合标记与原值 | 日期有效观察及实施／变更／撤销事件 | 板别、日期及有效规则共同确定涨跌停口径 |
| N／适用市场的 C | 名称标记归一 | 核对上市日期，生成上市阶段与会话序号 | 无 N 不等于已恢复普通价格限制 |
| XD/XR/DR | 区分除息／除权／权息同除提示 | 对账权威事件，生成参考价状态 | 标记没有事件金额，不能独立计算因子 |
| 目录缺失 | 判断文件类型、范围和完整性 | 完整快照缺失触发成员核查；增量缺行无此含义 | 未确认退市不伪造事件；不拿局部清单做全市场统计 |

现有 abnormal-list 只提供简单 ST 前缀识别；组合标记归一、日期归属和新 core writer 属于目标能力。

## 4. 参考价、状态与输出边界

前一有效交易日未复权收盘 C_prev 和当日涨跌幅参考价 R_t 含义不同。仅现金分红 d 的简化情形为 R_t=C_prev-d；更复杂事件按正式规则、参数和精度处理。已调整的来源参考价不能再扣一次分红；来源与权威事件冲突进入核查。

core 公司行为状态可以保存本次 R_t 的依据和校验结果；factor 使用它计算涨跌幅、限价命中等结果。事件修订可能影响整段复权价格和后续技术指标，而非只影响当天。未复权原始收盘不因事件而改写。

core 日期状态维度包括身份范围、is_st／类型、上市阶段、trading_status、权息标记、行情有效性、参考价就绪、适用限价规则、输入与规则版本。收盘涨跌停、盘中触及、一字候选及连板属于 factor 结果或其临时缓存；日 OHLC 不提供封板时长和开板次数。

## 5. 横截面集合与物理读取

正式横截面读取 DuckDB 中已发布的当天 core 行情切片，并关联发布清单指定的 Parquet 目录、事件及状态。当天 raw 先经过 staging 和 core 验收；仅导入核对或预览可以直接针对 staging 计算。量比、连板和技术指标读取各自需要的历史窗口，不要求重扫全部历史。详见[存储与发布架构](storage-and-publication.md)。

U_t 是已确认适用日期的目录候选集合；某统计 M 的有效集合为：

`D_t(M) = U_t ∩ M 所需行情有效集合 ∩ M 所需状态就绪集合 ∩ M 的范围规则`

停牌数量统计不强制要求存在当日成交行情；涨跌停统计必须明确无价格限制证券的处理。发布候选数、有效数、未知数和排除数，比例分母采用实际合同，而不是一律使用目录总数。历史回算需当日成员依据，不能套用最新目录。

## 6. 规则依据与实现边界

- [上交所交易规则（2026 年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)：除权除息参考价与涨跌幅基准，实施时核对有效及暂缓条款。
- [深交所创业板简称标识通知](https://www.szse.cn/www/disclosure/notice/general/t20200612_578382.html)：N/C 的阶段语义，不推广到所有市场。
- [上交所上市规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816589.shtml)：风险警示类别。

来源能力细节见 [tdxman 矩阵](../api/guides/tdxman-capabilities.md)、[aspool 矩阵](../api/guides/aspool-capabilities.md)。这些是旧系统当前能力的证据，不等于 MarketDB 已实现文件导入、四层发布或完整规则引擎。本轮不修改 collector、数据库和交易规则代码。

## 7. 运行与查询能力补充

每日文件明确写入模式、完整范围与 schema 版本。目录、行情和事件分别设置质量门槛，导入任务成功与每个资产就绪分开登记。对外资产目录只列已发布可读数据；查询和跨页结果固定发布版本，不因 Dataset 声明存在而触发源端取数。参见[能力模型](capability-model.md)与[AxData 对照](axdata-comparison.md)。
