# aspool 数据库能力矩阵：可落地版本

[矩阵总览](README.md) · [完整现状矩阵](aspool-database-capabilities.md) · [aspool 能力](aspool-capabilities.md)

## 版本定位与落地范围

本文件定义以当前布局 3 为基础的生产数据基线，按 **个股字典（A股个股清单）→基础数据→Enriched 数据→管理维度表** 排列。保留 **8 个数据库、32 张表**：每张表都对应现有来源输入、计算依赖、对外读取或仍在使用的管理协议。不纳入旧布局数据、兼容副本、过时审计材料及仅有 schema、没有生产业务入口的计算缓存。

“去掉过时数据”指淘汰过时的存储职责与重复副本，不是删除仍支撑历史查询、回测和重算的历史行情、事件、报告或成员快照。下载前复权价格与历史绑定快照均为有效输入，不因采集日期较早而淘汰。

这是基于源码审理的落地合同；本次交付没有改写程序或清理数据库。对外功能完整性按下文逐项映射，物理淘汰必须在入口修正和验收通过后执行。当前日级功能是可实现基线；未实现 W/M 生产行、按需计算缓存、ETF 在线因子源不包装成已上线能力。

### 生产文件集合

`catalog.duckdb`、`stocks.sqlite`、`fundamentals.sqlite`、`indices.sqlite`、`etfs.sqlite`、`adjustments.sqlite`、`features.sqlite`、`event_baseline.sqlite`。

CLI 显式使用 `--root ROOT`；Python 显式使用 `DataPool(ROOT)`。表中 API 均为 Python 方法。成交量规范为股、金额为元；报价与历史 K 线的协议单位必须分别转换。公共读取只查询已发布数据，不触发采集或迁移。

## 一、个股字典（A股个股清单）

`securities` 是唯一目录。A股个股清单取 `asset_type=stock` 且 market 为 SH/SZ/BJ；同表的 ETF、指数按 asset_type 分开。当前 active 与历史上市/退市范围分别保存，名称仅表示当前目录名。

| 数据库 | 物理表 | 定位 | 用途 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取入口与源码 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `catalog.duckdb` | `securities` | 基础 | 确定采集、查询和统计的证券集合，提供当前身份与上市/退市范围。 | directory --type stock/etf/index；update/sync --type all；股票自动目录刷新 | MacClient.get_stock_quotes_list；指数 get_board_list/get_stock_quotes_list；finance 上市信息 | symbol；code/market、asset_type/name/active、listing/delisting_date | query --dataset securities；list_indices/list_etfs、预期集合；[实现](../../../src/aspool/securities.py) |

## 二、基础数据

来源日线、事件、财务、外部复权依据、会话及成员快照均保留。板块来源快照虽然位于 features.sqlite，逻辑上仍是基础数据。财报期、股东统计日期、抓取日期和公告日期分别表达。

| 数据库 | 物理表 | 定位 | 用途 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取入口与源码 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `stocks.sqlite` | `daily_bars` | 基础 | 提供个股未复权历史行情及量价/股本字段，作为研究读取、复权和逐股派生的基础。 | aspool update/sync --type stock --source tdx --root ROOT；sync --start DATE --end DATE | MacClient.get_stock_quotes（update）；get_stock_kline(period=DAILY, adjust=NONE)（sync）；显式 BaoStock get_daily | symbol×trade_date；OHLC、volume/amount；股票/ETF 另有量价、股本及逐字段来源 | DataPool.read_daily；query --dataset stock-bars；[实现](../../../src/aspool/sqlite_update_cli.py) |
| `stocks.sqlite` | `stock_qfq_bars` | 基础/下载依据 | 保存来源前复权价格，支持下载行情读取、参考价核验和修复。 | 审计维护 qfq_audit_repair；replica apply --package PATH（stock-qfq-bars） | 外部 QFQ 日线采集证据；非日常 update 的原始价格 | symbol×trade_date；OHLC、source、source_as_of | DataPool.read_downloaded_qfq；参考价核验；[实现](../../../src/aspool/qfq_audit_repair.py) |
| `fundamentals.sqlite` | `stock_financial_reports` | 基础 | 按报告期查询财务摘要，追溯来源字段及采集时间。 | aspool fundamentals --source tdx --symbol S --root ROOT；all 编排当前未调用 | TdxClient.get_finance_info → normalize_finance | symbol×period_end×source；股本、资产/负债、收入/利润、现金流摘要、payload/hash、published/fetched 时间 | DataPool.read_fundamental_reports；[实现](../../../src/aspool/fundamentals_store.py) |
| `fundamentals.sqlite` | `stock_shareholder_counts` | 基础 | 查询股东人数历史及其统计日期，观察持有人数量变化。 | 同上 fundamentals 入口 | 同一 get_finance_info 的股东人数与来源日期字段 | symbol×as_of_date×source；shareholder_count、source_report_date | DataPool.read_shareholder_counts；未提供时不造行；[实现](../../../src/aspool/fundamentals_store.py) |
| `adjustments.sqlite` | `etf_adjustment_factors` | 基础/复权参考 | 为 ETF 复权行情读取提供来源累计因子。 | 已审核来源因子的显式维护；无日常在线因子专用 CLI | 已审核来源 ETF 累计因子 | symbol×effective_date；cumulative_factor、source | query --dataset etf-factors；read_etf_daily；[实现](../../../src/aspool/platform_v2.py) |
| `indices.sqlite` | `daily_bars` | 基础 | 提供指数历史走势、成交量额及可用涨跌家数，支持指数研究和交易日确认。 | aspool update/sync --type index --source tdx --root ROOT；sync --start DATE --end DATE | MacClient.get_stock_kline(period=DAILY, adjust=NONE)（update/sync） | symbol×trade_date；OHLC、volume/amount、up_count/down_count、breadth_status、source | DataPool.read_index_daily；query --dataset index-bars；[实现](../../../src/aspool/sqlite_index_sync.py) |
| `etfs.sqlite` | `daily_bars` | 基础 | 提供 ETF 未复权历史行情，支持产品比较和复权行情读取。 | aspool update/sync --type etf --source tdx --root ROOT；sync --start DATE --end DATE | MacClient.get_stock_kline（日线主来源，update/sync）；get_stock_quotes（无交易状态确认） | symbol×trade_date；OHLC、volume/amount；股票/ETF 另有量价、股本及逐字段来源 | DataPool.read_etf_daily；query --dataset etf-bars；[实现](../../../src/aspool/sqlite_etf_sync.py) |
| `catalog.duckdb` | `index_memberships` | 基础 | 按行业、概念、风格和标准指数类别选择指数，避免重复存储同一指数行情。 | directory --type index；update/sync --type index/all | HY/HY2/GN/FG/ZS 在线目录；benchmark 规范化 | symbol×category；active；同一指数可多分类 | 指数选择/分类查询；不表示个股成分权重；[实现](../../../src/aspool/index_lists.py) |
| `catalog.duckdb` | `security_calendar` | 基础 | 对外提供已确认的交易日期与开市状态，限定采集和查询日期。 | update/sync --type index/all | 上证指数 get_stock_kline 的实际交易日期 | trade_date；is_open/source | query --dataset calendar；read_trading_calendar；[实现](../../../src/aspool/sqlite_index_sync.py) |
| `features.sqlite` | `market_sessions` | 基础/派生依赖 | 为量比、连板及滚动窗口计算提供连续交易会话顺序。 | 日常 writer save_sessions；显式 schema/平台维护 | 已观察交易日期；股票计算用的本地会话轴 | trade_date；不是完整休市日历 | 量比、连板、窗口计算；[实现](../../../src/aspool/sqlite_six_dimension.py) |
| `features.sqlite` | `board_snapshot_sets` | 基础/快照 | 标识一次成员采集的时间、口径和预期板块数，供聚合绑定与完整性检查。 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 完整行业/概念成员抓取的快照头 | snapshot_id；kind、membership_as_of/basis、expected_board_count | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `features.sqlite` | `board_snapshots` | 基础/快照 | 保存当时的板块成员，支持成员查询和按原成员重算历史聚合。 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | MacClient.get_board_list(HY2/GN)＋get_board_members | snapshot_id×kind×board_id；board_name、members_json | DataPool.read_board_members；[实现](../../../src/aspool/sqlite_board_daily.py) |

| `event_baseline.sqlite` | `events` | 基础/权威事件基准 | 保存全历史来源事件，支撑基准因子、参考价计算与来源追溯。 | python -m aspool.event_baseline --root ROOT --source PARQUET | ageman/thsman 全历史同花顺事件导出 | row_id；symbol×effective_date；payload 内保留分红、送股、配股、币种和原始日期等字段 | aspool.event_baseline.read_events；[实现](../../../src/aspool/event_baseline.py) |

复权事件唯一来源为 thsman 全历史基准。目标结构不保留第二套 TDX 事件或外部因子锚点；核验记录放入报告。原始重复行和未来事件保留，计算按明确去重/冲突规则及生效日期使用。复权表职责详见 [复权数据表整理](aspool-adjustment-tables.md)。

## 三、Enriched 数据

保留已有日级计算链路：因子→逐股→市场/板块。market_regime_features 只承诺已发布 D 行；W/M 请求按现有公共合同明确返回未就绪，不返回伪造数据。

| 数据库 | 物理表 | 定位 | 用途 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取入口与源码 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `adjustments.sqlite` | `stock_adjustment_factors` | Enriched | 提供本地复权计算结果及有效区间，支持复权投影和依赖特征更新。 | python -m aspool.baseline_factors --root ROOT --through DATE --report-dir DIR --apply；日常计算入口待统一接入 | 权威 event_baseline 事件＋未复权行情；现有基准重建来源为 derived:baseline_event_chain；在线/增量切换须核验，不能默认全链路已切换 | symbol×effective_date×来源/事件；event/cumulative_factor、valid_from/through、算法/hash | read_daily/read_research_daily 的复权及参考计算；[实现](../../../src/aspool/sqlite_reference_factors.py) |
| `features.sqlite` | `stock_daily_features` | Enriched | 提供逐股交易/ST状态、参考价、涨跌停、连板和 MA20，支撑筛选与市场汇总。 | 股票 update/sync 的共同发布；显式修复/副本重算 | 股票日线＋权威 event_baseline 事件/本地因子＋当日名称/ST＋交易会话 | symbol×trade_date；交易状态、参考昨收/ST、涨跌停、连板、MA20、质量原因 | query --dataset stock-features/limit-events；DataPool.read_limit_*；[实现](../../../src/aspool/sqlite_daily_update.py) |
| `features.sqlite` | `market_regime_features` | Enriched | 提供日级市场广度、收益分布和涨跌停等特征，供市场研究及下游 Regime 模型使用。 | 股票局部 writer 计算受影响 D 行 | stock_daily_features＋daily_bars；本地市场聚合 | frequency×period_key×scope；涨跌分布、广度、涨跌停/连板/晋级、MA20、量价；当前 D，W/M 预留 | query --dataset market-summary；DataPool.read_market_summary/read_market_daily；[实现](../../../src/aspool/sqlite_daily_derived.py) |
| `features.sqlite` | `board_daily` | Enriched | 提供行业/概念板块日收益和成员统计，支持板块比较与市场结构分析。 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 绑定的成员快照＋逐股日收益聚合 | date×scope×classification×kind×board_id；成员数、交易/有效收益人数、平均收益、snapshot_id | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |

## 四、管理维度表

保留当前版本检查、共同发布和维护协议所依赖的管理表。它们支撑对外一致读取与恢复，不能仅因没有行情字段就删除。业务输入记录、内部算法状态和运行成本分别登记。

| 数据库 | 物理表 | 定位 | 用途 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取入口与源码 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `stocks.sqlite` | `dataset_state` | 内部状态 | 登记股票基础数据的提交版本及最新日期，支撑状态查询和一致性检查。 | 对应库业务 writer；无独立采集命令 | 本地提交后的 revision/max_date | dataset；revision、max_date、updated_at | 状态/一致性检查，不是市场数据；[实现](../../../src/aspool/sqlite_stock_store.py) |
| `fundamentals.sqlite` | `dataset_state` | 内部状态 | 登记财务与股东数据的提交版本及最新日期，支撑状态查询和一致性检查。 | 对应库业务 writer；无独立采集命令 | 本地提交后的 revision/max_date | dataset；revision、max_date、updated_at | 状态/一致性检查，不是市场数据；[实现](../../../src/aspool/fundamentals_store.py) |
| `adjustments.sqlite` | `adjustment_state` | 内部状态 | 登记因子版本和数据末端，校验派生结果是否使用当前因子。 | 因子 writer | 本地发布版本与末端 | dataset×revision/max_date | 因子一致性检查；[实现](../../../src/aspool/platform_v2.py) |
| `features.sqlite` | `feature_state` | 内部状态 | 记录特征依赖版本、影响范围和就绪状态，阻止读取未完成或过期的派生结果。 | 股票共同发布 | raw_revision/factor_revision 与算法状态 | dataset×scope_key；影响范围、status | 读取 stale/版本核验；[实现](../../../src/aspool/platform_v2.py) |
| `features.sqlite` | `board_sync_state` | 内部状态 | 记录板块成员采集是否成功及失败原因，支持重试和快照可用性判断。 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 类别抓取成功/失败记录 | kind；status、snapshot_id、attempted_at/error | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `features.sqlite` | `board_daily_status` | 内部状态 | 记录板块日聚合覆盖与完成状态，避免将不完整聚合当作完整截面。 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 板块日聚合完整性结果 | date×scope×kind；processed/expected/mapped、status | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `catalog.duckdb` | `business_revisions` | 内部协议/审计 | 记录业务对象当前修订号，供变更比较和幂等处理。 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | object_key；revision | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `business_changes` | 内部协议/审计 | 追溯每次业务修改的输入/输出版本、变更字段、依据和应用状态。 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | operation_id；object/input/output_revision、source/reason、changed_rows/fields、evidence/state/run | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `fetch_observations` | 内部协议/审计 | 记录来源读取时间及状态，区分已尝试采集与实际业务数据变化。 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | object_key；observed_at/status | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `maintenance_runs` | 内部协议/审计 | 记录维护任务状态和指标，支持执行追踪、故障定位与恢复。 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | run_id；command/state/metrics | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `maintenance_totals` | 内部协议/审计 | 汇总一次维护中实际变更行数、字段数和已应用操作数。 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | run_id；changed_rows/fields/applied_operations | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `coverage_totals` | 内部协议/审计 | 汇总维护协议中的覆盖元信息变更量，供运行报告与成本核对。 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | run_id；changed_rows | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `pool_metadata` | 内部布局 | 标识当前存储布局和池版本，供读写路由、状态检查与恢复。 | 当前布局版本登记；平台状态/恢复 | 本地池 layout_version=3 与版本标记 | key→value、updated_at | 布局路由与状态；[实现](../../../src/aspool/platform_v2.py) |

| `event_baseline.sqlite` | `metadata` | 管理/基准版本 | 核验来源版本、覆盖范围、单位及因子/派生重建状态。 | 基准导入和显式重建维护 | 导入文件与重建回执 | payload；source/file、sha256、日期范围、factor_status、enriched_status | 基准版本与读取门禁；[实现](../../../src/aspool/event_baseline.py) |

## 对外功能完整性映射

| 对外功能 | 保留的数据及依赖 | 对外入口 | 完整性要求 |
| --- | --- | --- | --- |
| A股清单、证券资料、指数/ETF 目录 | securities、index_memberships | directory；query --dataset securities；read_security_info/list_indices/list_etfs | 目录按市场完整发布；保留明确上市/退市日期，不用一次缺页推断退市 |
| 股票未复权日线与量价/股本字段 | stocks.daily_bars | update/sync --type stock；query --dataset stock-bars；read_daily/read_research_daily | 保留有效历史与逐字段来源；有界读取、行数预算、空值与单位合同保持 |
| 指数与 ETF 日线 | indices.daily_bars、etfs.daily_bars、securities | update/sync --type index/etf/all；query --dataset index-bars/etf-bars；read_index_daily/read_etf_daily | 指数 breadth_status 与涨跌家数同读；ETF 不误用股票报价日更路径 |
| 除权事件、前复权下载依据、股票复权 | event_baseline.events/metadata、stock_qfq_bars、stock_adjustment_factors | 事件读取迁移到 event_baseline.read_events；read_downloaded_qfq；read_daily 的 adjust/adjustment_base；基准事件因子重建 | 事件唯一来源；保留 source_as_of 和覆盖区间；旧事件 CLI/API 需改接权威库，来源字段契约同步调整 |
| ETF 因子与复权读取 | etf_adjustment_factors、etfs.daily_bars | query --dataset etf-factors；read_etf_daily | 只使用已审核来源；没有在线更新入口时必须显式维护并报告来源/日期 |
| 财务报告、股东人数 | stock_financial_reports、stock_shareholder_counts、各库 dataset_state | fundamentals；query --dataset fundamentals/shareholder-counts；read_fundamental_reports/read_shareholder_counts | 独立执行 fundamentals；all 目前未调用；公告时间缺失不伪装 PIT |
| 日期交易/ST事实、参考价、涨跌停/连板/MA20 | stock_daily_features＋股票日线/事件/因子 | read_security_daily；query --dataset stock-features/limit-events；read_limit_*、iter_limit_events_with_amount | TRADED/NO_TRADE/MISSING/INVALID 与未知原因保留；BJ 为 NO_LIMIT；历史名称/ST 不能用当前目录倒填 |
| 市场日级特征与 Regime 输入 | market_regime_features、stock_daily_features、daily_bars | read_market_daily/read_market_summary/describe_market_fields；query --dataset market-summary --frequency D | scope、分母、质量原因和版本一致；Regime 评分由消费端实现 |
| 行业/概念成员与日聚合 | board_snapshot_sets、board_snapshots、board_daily、board_sync_state、board_daily_status | read_board_members/read_board_daily | 每个历史聚合保留 snapshot_id 绑定；保留成员 JSON，不能只留摘要哈希 |
| 交易日历与计算会话轴 | security_calendar、market_sessions | read_trading_calendar；query --dataset calendar；日级计算 | 只发布来源确认的日期；两表用途不同；不能从无行情推断休市 |
| 状态、合同、一致读取和恢复 | dataset_state、adjustment_state、feature_state、pool_metadata及现有管理协议 | status/contract/platform verify/status/recover；stock_snapshot/describe/status；replica export/apply/recover | 输入/因子/特征 revision 必须匹配；未完成发布阻止读取，不能为减少表数跳过保护 |

### 数据库外仍必需的运行材料

共同发布与增量副本的恢复意图、来源证据和报告保存在池外，不能随生产表清理一起删除：

- `.aspool-state/<池名>/`：pending-publication/pending-migration 等当前恢复意图；对应 [sqlite_publication](../../../src/aspool/sqlite_publication.py)。
- replica/base_delta 的 pending 状态、事件覆盖和原始来源证据：保证幂等、恢复及因子依据完整；位置由 [base_delta](../../../src/aspool/base_delta.py)确定。
- `.local/` 中的当前采集/维护报告：记录请求窗口、失败、缺失、成本与来源；不作为行情权威数据。

内部 SQL 路由或同名方法适配可以继续存在，它们直接访问上述唯一物理表，不要求保留另一份数据。

## 落地条件与验收

| 必须完成的事项 | 当前证据/落地要求 | 验收 |
| --- | --- | --- |
| 复权事件来源统一 | 停止生产写入 TDX 事件与旧锚点；update/sync、bootstrap、查询、replica 均改接权威事件库；保留所需事件参数 | 事件读取与计算不存在退役表依赖；旧查询入口保持业务能力并明确字段契约；基准更新及重算通过 |
| 所有生产入口只依赖本文件表集合 | 当前布局 3 已有原生读写；store.initialize 仍会创建退役覆盖/运行表；部分入口仍有旧库回退 | 初始化及一次目录/日更/补齐后，退役数据表不被创建；所有生产读写落点只在批准集合 |
| 新根完整初始化 | 不能只调用当前 init 就认为七库及派生发布状态齐备 | 空根按新布局准备结构、版本及来源初始化；未 ready 返回明确错误，不能临时查旧副本 |
| 财务入口统一 | fundamentals.sqlite 存在时 CLI 使用新报告；缺库时仍存在旧快照 fallback | 缺库明确不可用；补齐新报告及股东数据后按新入口读取；不保留旧快照作为备用输出 |
| 单一目录/状态读取 | read_security_info/read_security_daily 的布局 3 分支已分别使用 securities/stock_daily_features | 缺新表明确错误；目录、事实、日历读取均无旧数据依赖 |
| ETF 因子读取 | 内部 adjustment_factors 逻辑名需确认始终映射到 adjustments.etf_adjustment_factors | 带 symbol、日期过滤的 CLI/API 同时通过；不要求 etfs 内保留另一个因子表 |
| 财务与行情编排分开交代 | _run_all 当前没有调用 fundamentals | 日常任务明确执行行情 all 与独立 fundamentals，并分别验收；若承诺一个命令完成全部，必须补编排 |
| 派生与历史输入完整 | 共同发布需 raw/factor/feature revision 一致；历史聚合依赖来源快照/ST事实 | 选有事件、ST、无交易/缺失、板块绑定的有界日期检查；同输入重放不改变业务结果；修订只影响明确依赖区间 |
| 发布失败与副本恢复 | 当前发布/replica 有恢复门禁 | 故障后明确拒绝未完成读取；恢复后原始/因子/逐股/市场/板块一致；来源证据仍可核验 |
| 退役数据的历史价值核对 | 删除职责不等于丢弃唯一有效历史；财报字段/摘要来源可能不完全等价 | 唯一有效记录及仍承诺的字段先进入保留表；字段转换需来源/日期依据，不能默认新库自动覆盖 |
| 老入口中的独立业务能力 | 旧 import/minute lake/扩展同步不能仅因旧布局退役就静默成功或输出空数据 | 如仍属产品对外承诺，先迁移到明确的新存储与接口并验收；否则明确退出旧产品范围。当前日级矩阵不证明这些能力已迁移 |

本次已完成表级来源、维度和对外功能依赖核对；没有执行上述初始化、接口回归、故障恢复或数据清理验收。因此本文件可作为实施与验收基线，不能当成“已删除旧数据且全部功能回归通过”的回执。原完整矩阵保留作现状审计，生产职责以本文件为准。

## 保留表字段附录

字段来自现状矩阵的只读结构检查；未知或可空字段保持原质量语义。

### `catalog.duckdb`

- `securities`：`symbol`, `code`, `market`, `asset_type`, `name`, `active`, `listing_date`, `delisting_date`, `updated_at`。
- `index_memberships`：`symbol`, `category`, `active`, `updated_at`。
- `security_calendar`：`trade_date`, `is_open`, `source`。
- `business_revisions`：`object_key`, `revision`。
- `business_changes`：`operation_id`, `object_key`, `input_revision`, `output_revision`, `source`, `reason`, `changed_rows`, `changed_fields`, `evidence`, `state`, `applied_at`, `run_id`。
- `fetch_observations`：`object_key`, `observed_at`, `status`。
- `maintenance_runs`：`run_id`, `command`, `state`, `metrics`。
- `maintenance_totals`：`run_id`, `changed_rows`, `changed_fields`, `applied_operations`。
- `coverage_totals`：`run_id`, `changed_rows`。
- `pool_metadata`：`key`, `value`, `updated_at`。

### `stocks.sqlite`

- `daily_bars`：`symbol`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `turnover_rate`, `vol_ratio`, `pct_chg`, `amplitude`, `total_share`, `float_share`, `float_shares`, `total_mv`, `float_mv`, `pe_ttm`, `pb`, `name`, `name_as_of`, `name_source`, `ohlcv_source`, `turnover_rate_source`, `vol_ratio_source`, `pct_chg_source`, `amplitude_source`, `total_share_source`, `float_share_source`, `total_mv_source`, `float_mv_source`, `updated_at`。
- `stock_qfq_bars`：`symbol`, `trade_date`, `open`, `high`, `low`, `close`, `source`, `source_as_of`, `updated_at`。
- `dataset_state`：`dataset`, `revision`, `max_date`, `updated_at`。

### `fundamentals.sqlite`

- `stock_financial_reports`：`symbol`, `period_end`, `source`, `liutong_guben`, `zong_guben`, `guojia_gu`, `faqiren_faren_gu`, `faren_gu`, `b_gu`, `h_gu`, `zhigong_gu`, `province`, `industry`, `ipo_date`, `zong_zichan`, `liudong_zichan`, `guding_zichan`, `wuxing_zichan`, `liudong_fuzhai`, `changqi_fuzhai`, `ziben_gongjijin`, `jing_zichan`, `zhuying_shouru`, `zhuying_lirun`, `yingshou_zhangkuan`, `yingye_lirun`, `touzi_shouyu`, `jingying_xianjinliu`, `zong_xianjinliu`, `cunhuo`, `lirun_zonghe`, `shuihou_lirun`, `jing_lirun`, `weifen_lirun`, `meigujing_zichan`, `source_hash`, `payload_json`, `published_at`, `fetched_at`, `updated_at`。
- `stock_shareholder_counts`：`symbol`, `as_of_date`, `source`, `shareholder_count`, `source_report_date`, `published_at`, `fetched_at`, `updated_at`。
- `dataset_state`：`dataset`, `revision`, `max_date`, `updated_at`。

### `adjustments.sqlite`

- `etf_adjustment_factors`：`symbol`, `effective_date`, `cumulative_factor`, `source`, `updated_at`。
- `stock_adjustment_factors`：`symbol`, `effective_date`, `record_kind`, `source`, `source_key`, `category`, `payload_json`, `source_cumulative_factor`, `event_factor`, `cumulative_factor`, `valid_from`, `valid_through`, `factor_basis`, `updated_at`, `input_hash`, `algorithm_version`。
- `adjustment_state`：`dataset`, `revision`, `max_date`, `updated_at`。

### `indices.sqlite`

- `daily_bars`：`symbol`, `trade_date`, `market`, `code`, `name`, `open`, `high`, `low`, `close`, `volume`, `amount`, `up_count`, `down_count`, `breadth_status`, `source`, `updated_at`。

### `etfs.sqlite`

- `daily_bars`：`symbol`, `market`, `code`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `turnover_rate`, `name`, `pre_close`, `vol_ratio`, `pct_chg`, `amplitude`, `is_st`, `trading_status`, `pre_close_source`, `is_st_source`, `trading_status_source`, `vol_ratio_source`, `turnover_rate_source`, `amplitude_source`, `pct_chg_source`, `float_share_source`, `total_share_source`, `float_mv_source`, `total_mv_source`, `total_share`, `float_share`, `total_mv`, `float_mv`, `pe_ttm`, `pb`, `datetime`, `vol`, `float_shares`, `asset_type`, `turnover`, `source`, `updated_at`。

### `features.sqlite`

- `market_sessions`：`trade_date`。
- `board_snapshot_sets`：`snapshot_id`, `kind`, `membership_as_of`, `membership_basis`, `expected_board_count`, `updated_at`。
- `board_snapshots`：`snapshot_id`, `kind`, `board_id`, `board_name`, `members_json`。
- `stock_daily_features`：`symbol`, `trade_date`, `source_pre_close`, `source_pre_close_source`, `source_is_st`, `source_is_st_source`, `is_st_name_date`, `trading_status`, `trading_status_source`, `pre_close`, `pre_close_source`, `is_st`, `is_st_source`, `calc_status`, `limit_status`, `limit_reason`, `limit_up_price`, `limit_down_price`, `touch_limit_up`, `close_limit_up`, `touch_limit_down`, `close_limit_down`, `consecutive_up`, `prior_consecutive_up`, `streak_known`, `ma20`, `above_ma20`, `updated_at`。
- `market_regime_features`：`frequency`, `period_key`, `scope`, `period_start`, `period_end`, `as_of`, `session_count`, `trading_count`, `st_unknown_count`, `valid_return_count`, `invalid_return_count`, `up_count`, `down_count`, `flat_count`, `strong_up_count`, `strong_down_count`, `avg_return`, `median_return`, `return_distribution_json`, `up_pct`, `strong_up_pct`, `strong_down_pct`, `close_limit_up_count`, `touch_limit_up_count`, `close_limit_down_count`, `touch_limit_down_count`, `limit_known_count`, `no_limit_count`, `limit_unknown_count`, `limit_invalid_count`, `streak_unknown_count`, `max_consecutive_up`, `first_board_count`, `second_plus_count`, `third_plus_count`, `fifth_plus_count`, `ladder_json`, `promotion_eligible_count`, `promotion_success_count`, `amount_sum`, `amount_valid_count`, `avg_turnover`, `turnover_valid_count`, `sealed_ratio`, `promotion_ratio`, `ma20_valid_count`, `above_ma20_count`, `above_ma20_pct`, `trading_observations`, `st_unknown_observations`, `valid_return_observations`, `daily_up_pct_weighted`, `daily_strong_up_pct_weighted`, `daily_strong_down_pct_weighted`, `daily_avg_return_weighted`, `daily_median_return_mean`, `daily_median_valid_sessions`, `daily_return_distribution_json`, `period_return_distribution_json`, `daily_limit_up_mean`, `limit_up_occurrences`, `limit_up_symbols`, `limit_touch_up_occurrences`, `limit_down_occurrences`, `limit_touch_down_occurrences`, `limit_unknown_observations`, `limit_invalid_observations`, `streak_unknown_observations`, `end_max_consecutive_up`, `period_max_consecutive_up`, `promotion_eligible_observations`, `promotion_success_observations`, `period_valid_return_count`, `period_unknown_return_count`, `period_up_count`, `period_down_count`, `period_flat_count`, `period_avg_return`, `period_median_return`, `updated_at`, `upper_median_return`, `avg_vol_ratio_5d`, `vol_ratio_5d_valid_count`, `high_vol_ratio_5d_count`。
- `board_daily`：`date`, `scope`, `classification`, `kind`, `board_id`, `board_name`, `member_count`, `trading_member_count`, `valid_return_count`, `avg_return`, `membership_as_of`, `membership_basis`, `snapshot_id`, `input_digest`, `updated_at`。
- `feature_state`：`dataset`, `scope_key`, `raw_revision`, `factor_revision`, `algorithm_version`, `status`, `affected_from`, `affected_through`, `updated_at`。
- `board_sync_state`：`kind`, `status`, `snapshot_id`, `attempted_at`, `error`, `updated_at`。
- `board_daily_status`：`date`, `scope`, `kind`, `status`, `processed_board_count`, `expected_board_count`, `mapped_trading_count`, `snapshot_id`, `updated_at`。

### `event_baseline.sqlite`

- `events`：`row_id`, `symbol`, `effective_date`, `payload`。
- `metadata`：`payload`。

事件基准身份与重建维护以 [全历史复权事件基准](../../ops/event_baseline.md)为准。该库是必须保留和复制的基础数据；此前将其作为历史审计材料排除的分类已纠正。
