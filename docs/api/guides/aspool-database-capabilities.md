# aspool 数据库逐表能力矩阵

[矩阵总览](README.md) · [aspool 能力](aspool-capabilities.md) · [分层合同](../../design/data_layers.md)

## 检查范围与使用方式

2026-10-04 只读检查当前 `data/` 顶层 **9 个数据库、43 张用户表**：SQLite 使用 `mode=ro` 的 `sqlite_master/PRAGMA table_info`，DuckDB 使用 `read_only=True` 的 `SHOW TABLES/DESCRIBE`。没有采集、更新、迁移或统计全量行数。下表每张实际表均有一行；内部表同样登记。物理表存在仅证明结构存在，不证明有数据、最新、完整或已通过在线验收。

CLI 公共参数 `--root ROOT` 指定同一个池。表中 `update/sync` 均指 `aspool update/aspool sync`；股票显式备用源为 `--source baostock`，其他日常源为 `--source tdx`。输入 API 为 Python 方法，不是 HTTP 端点；当前 aspool 公共接口为 CLI 和 DataPool，不能据此编造 REST 路径。表格“主要维度”记录业务粒度，除明确说明外不冒充完整主键定义。文末按数据库列出实际字段，避免把来源缺失/可空字段误认为已提供能力。

基础、Enriched、内部状态、旧布局、可选缓存与审计材料分别标明。板块来源成员快照属于基础数据，物理位于 features.sqlite。生产布局 3 权威以 [单一权威存储](../../implements/single_authority_storage.md)为准；旧表不能继续充当新权威。

按业务顺序整理：**个股字典（A股个股清单）→基础数据→Enriched 数据→管理维度表**。数据库文件名作为每行的物理定位列，不作为一级分组。旧布局、可选缓存和审计材料继续保留原定位标记。

个股字典的当前权威是 `catalog.duckdb.securities` 中 `asset_type=stock` 的记录（SH/SZ/BJ）；该表也容纳 ETF 与指数，不能将全表当作 A股个股清单。旧 `universe/security_lifecycle` 放在同组供兼容对照。基础数据包含源行情、事件、财务、外部复权依据和来源成员快照；Enriched 数据包含本地因子、逐股/市场/板块计算及可选计算快照；管理维度表记录状态、覆盖、版本、运行与审计元信息。

## 一、个股字典（A股个股清单）

当前清单与兼容资料依次列出；`active` 表示当前目录状态，不代替历史有效范围。

| 数据库 | 物理表 | 定位 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取/用途与源码 |
| --- | --- | --- | --- | --- | --- | --- |
| `catalog.duckdb` | `securities` | 基础 | directory --type stock/etf/index；update/sync --type all；股票自动目录刷新 | MacClient.get_stock_quotes_list；指数 get_board_list/get_stock_quotes_list；finance 上市信息 | symbol；code/market、asset_type/name/active、listing/delisting_date | query --dataset securities；list_indices/list_etfs、预期集合；[实现](../../../src/aspool/securities.py) |
| `catalog.duckdb` | `universe` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧在线目录刷新 | symbol；当前名、active、first/last_seen、asset_type | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/universe.py) |
| `catalog.duckdb` | `security_lifecycle` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧 BaoStock get_stock_basic/显式生命周期资料 | symbol；listing/delisting_date、name/source/fetched_at | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/security_facts.py) |

## 二、基础数据

按股票行情与事件、财务、外部复权依据、指数/ETF、分类与会话、成员快照、补充来源及权威事件基准排列。

| 数据库 | 物理表 | 定位 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取/用途与源码 |
| --- | --- | --- | --- | --- | --- | --- |
| `stocks.sqlite` | `daily_bars` | 基础 | aspool update/sync --type stock --source tdx --root ROOT；sync --start DATE --end DATE | MacClient.get_stock_quotes（update）；get_stock_kline(period=DAILY, adjust=NONE)（sync）；显式 BaoStock get_daily | symbol×trade_date；OHLC、volume/amount；股票/ETF 另有量价、股本及逐字段来源 | DataPool.read_daily；query --dataset stock-bars；[实现](../../../src/aspool/sqlite_update_cli.py) |
| `stocks.sqlite` | `corporate_actions` | 基础 | update/sync --type stock；factors-bootstrap --symbol S --as-of DATE | TdxClient.get_xdxr_info；显式副本事件包 | symbol×effective_date×record_kind/source/source_key；category、payload_json | query --dataset corporate-actions；参考价/因子计算；[实现](../../../src/aspool/sqlite_reference_factors.py) |
| `stocks.sqlite` | `stock_qfq_bars` | 基础/下载依据 | 审计维护 qfq_audit_repair；replica apply --package PATH（stock-qfq-bars） | 外部 QFQ 日线采集证据；非日常 update 的原始价格 | symbol×trade_date；OHLC、source、source_as_of | DataPool.read_downloaded_qfq；参考价核验；[实现](../../../src/aspool/qfq_audit_repair.py) |
| `fundamentals.sqlite` | `stock_financial_reports` | 基础 | aspool fundamentals --source tdx --symbol S --root ROOT；all 编排当前未调用 | TdxClient.get_finance_info → normalize_finance | symbol×period_end×source；股本、资产/负债、收入/利润、现金流摘要、payload/hash、published/fetched 时间 | DataPool.read_fundamental_reports；[实现](../../../src/aspool/fundamentals_store.py) |
| `fundamentals.sqlite` | `stock_shareholder_counts` | 基础 | 同上 fundamentals 入口 | 同一 get_finance_info 的股东人数与来源日期字段 | symbol×as_of_date×source；shareholder_count、source_report_date | DataPool.read_shareholder_counts；未提供时不造行；[实现](../../../src/aspool/fundamentals_store.py) |
| `adjustments.sqlite` | `stock_factor_anchors` | 基础/外部依据 | 已审核迁移/基线维护；replica apply 的锚点数据集 | 已验证来源累计因子/基线输入；不是凭空默认 1 | symbol×effective_date×source/source_key；覆盖区间、factor_basis、payload | 股票因子构建；通常无独立 CLI 查询；[实现](../../../src/aspool/baseline_factors.py) |
| `adjustments.sqlite` | `etf_adjustment_factors` | 基础/复权参考 | 已审核 ops 迁移/维护；无日常在线因子专用 CLI | 迁移源中的 ETF 累计因子 | symbol×effective_date；cumulative_factor、source | query --dataset etf-factors；read_etf_daily；[实现](../../../src/aspool/platform_v2.py) |
| `indices.sqlite` | `daily_bars` | 基础 | aspool update/sync --type index --source tdx --root ROOT；sync --start DATE --end DATE | MacClient.get_stock_kline(period=DAILY, adjust=NONE)（update/sync） | symbol×trade_date；OHLC、volume/amount；股票/ETF 另有量价、股本及逐字段来源 | DataPool.read_index_daily；query --dataset index-bars；[实现](../../../src/aspool/sqlite_index_sync.py) |
| `etfs.sqlite` | `daily_bars` | 基础 | aspool update/sync --type etf --source tdx --root ROOT；sync --start DATE --end DATE | MacClient.get_stock_kline（日线主来源，update/sync）；get_stock_quotes（无交易状态确认） | symbol×trade_date；OHLC、volume/amount；股票/ETF 另有量价、股本及逐字段来源 | DataPool.read_etf_daily；query --dataset etf-bars；[实现](../../../src/aspool/sqlite_etf_sync.py) |
| `catalog.duckdb` | `index_memberships` | 基础 | directory --type index；update/sync --type index/all | HY/HY2/GN/FG/ZS 在线目录；benchmark 规范化 | symbol×category；active；同一指数可多分类 | 指数选择/分类查询；不表示个股成分权重；[实现](../../../src/aspool/index_lists.py) |
| `catalog.duckdb` | `security_calendar` | 基础 | update/sync --type index/all；兼容 BaoStock 显式来源 | 上证指数 get_stock_kline 的实际交易日期；旧 BaoStock get_trade_calendar | trade_date；is_open/source | query --dataset calendar；read_trading_calendar；[实现](../../../src/aspool/sqlite_index_sync.py) |
| `features.sqlite` | `market_sessions` | 基础/派生依赖 | 日常 writer save_sessions；显式 schema/平台维护 | 已观察交易日期；股票计算用的本地会话轴 | trade_date；不是完整休市日历 | 量比、连板、窗口计算；[实现](../../../src/aspool/sqlite_six_dimension.py) |
| `features.sqlite` | `board_snapshot_sets` | 基础/快照 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 完整行业/概念成员抓取的快照头 | snapshot_id；kind、membership_as_of/basis、expected_board_count | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `features.sqlite` | `board_snapshots` | 基础/快照 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | MacClient.get_board_list(HY2/GN)＋get_board_members | snapshot_id×kind×board_id；board_name、members_json | DataPool.read_board_members；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `catalog.duckdb` | `fundamental_snapshots` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧 MacClient.get_stock_quotes 财务快照路径；当前仅审计保留 | symbol；market/code/name、total/float_share、eps/ttm_eps/net_assets | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/fundamentals.py) |
| `event_baseline.sqlite` | `events` | 基础/权威事件基准 | python -m aspool.event_baseline --root ROOT --source PARQUET | ageman/thsman 全历史同花顺事件导出，原始行完整保留 | row_id；symbol×effective_date、payload | read_events；基准因子与参考价计算；[实现](../../../src/aspool/event_baseline.py) |

## 三、Enriched 数据

按本地因子、逐股特征、市场特征、板块聚合、可选快照排列。`snapshot_catalog` 是计算快照的配套元信息，与快照行一并列示。

| 数据库 | 物理表 | 定位 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取/用途与源码 |
| --- | --- | --- | --- | --- | --- | --- |
| `adjustments.sqlite` | `stock_adjustment_factors` | Enriched | 股票 update/sync 局部 writer；factors-bootstrap --as-of DATE --symbol S [--maintenance] | corporate_actions＋前一有效未复权收盘＋已核实锚点；bootstrap 核验 NONE/QFQ/HFQ 与 finance/xdxr | symbol×effective_date×来源/事件；event/cumulative_factor、valid_from/through、算法/hash | read_daily/read_research_daily 的复权及参考计算；[实现](../../../src/aspool/sqlite_reference_factors.py) |
| `features.sqlite` | `stock_daily_features` | Enriched | 股票 update/sync 的共同发布；显式修复/副本重算 | 股票日线＋事件/因子＋当日名称/ST＋交易会话 | symbol×trade_date；交易状态、参考昨收/ST、涨跌停、连板、MA20、质量原因 | query --dataset stock-features/limit-events；DataPool.read_limit_*；[实现](../../../src/aspool/sqlite_daily_update.py) |
| `features.sqlite` | `market_regime_features` | Enriched | 股票局部 writer 计算受影响 D 行 | stock_daily_features＋daily_bars；本地市场聚合 | frequency×period_key×scope；涨跌分布、广度、涨跌停/连板/晋级、MA20、量价；当前 D，W/M 预留 | query --dataset market-summary；DataPool.read_market_summary/read_market_daily；[实现](../../../src/aspool/sqlite_daily_derived.py) |
| `features.sqlite` | `board_daily` | Enriched | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 绑定的成员快照＋逐股日收益聚合 | date×scope×classification×kind×board_id；成员数、交易/有效收益人数、平均收益、snapshot_id | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `snapshots.sqlite` | `snapshot_catalog` | 可选缓存 | 平台 prepare 创建空结构；源码未提供独立业务 CLI 在线采集入口 | 消费端计算快照契约；不等于已持续生产快照 | snapshot_id；feature_set/version、as_of_date、asset_type/scope、params/input_version、expires_at | 可选缓存元信息；存在表不证明缓存已填充；[实现](../../../src/aspool/platform_v2.py) |
| `snapshots.sqlite` | `snapshot_rows` | 可选缓存 | 同上；无独立 CLI writer | 快照实体 payload | snapshot_id×entity_id；payload_json | 可选快照行；[实现](../../../src/aspool/platform_v2.py) |

## 四、管理维度表

按数据集状态、派生/板块状态、覆盖统计、修订与运行协议、布局和审计元信息排列。

| 数据库 | 物理表 | 定位 | 写入 CLI / 维护入口 | 输入来源/API | 主要数据维度 | 读取/用途与源码 |
| --- | --- | --- | --- | --- | --- | --- |
| `stocks.sqlite` | `dataset_state` | 内部状态 | 对应库业务 writer；无独立采集命令 | 本地提交后的 revision/max_date | dataset；revision、max_date、updated_at | 状态/一致性检查，不是市场数据；[实现](../../../src/aspool/sqlite_stock_store.py) |
| `fundamentals.sqlite` | `dataset_state` | 内部状态 | 对应库业务 writer；无独立采集命令 | 本地提交后的 revision/max_date | dataset；revision、max_date、updated_at | 状态/一致性检查，不是市场数据；[实现](../../../src/aspool/fundamentals_store.py) |
| `adjustments.sqlite` | `adjustment_state` | 内部状态 | 因子 writer/迁移 | 本地发布版本与末端 | dataset×revision/max_date | 因子一致性检查；[实现](../../../src/aspool/platform_v2.py) |
| `features.sqlite` | `feature_state` | 内部状态 | 股票共同发布/平台迁移 | raw_revision/factor_revision 与算法状态 | dataset×scope_key；影响范围、status | 读取 stale/版本核验；[实现](../../../src/aspool/platform_v2.py) |
| `features.sqlite` | `board_sync_state` | 内部状态 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 类别抓取成功/失败记录 | kind；status、snapshot_id、attempted_at/error | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `features.sqlite` | `board_daily_status` | 内部状态 | 股票 update/sync 的 refresh_board_snapshots/recompute_board_daily；schema 未启用时 not_provided | 板块日聚合完整性结果 | date×scope×kind；processed/expected/mapped、status | DataPool.read_board_daily/内部快照绑定；[实现](../../../src/aspool/sqlite_board_daily.py) |
| `catalog.duckdb` | `coverage` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧 free-stock-db/TDX lake 写入及覆盖统计 | symbol；market/start_date/end_date/row_count/source | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/store.py) |
| `catalog.duckdb` | `coverage_minutes` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧 free-stock-db/TDX lake 写入及覆盖统计 | symbol；分钟文件时间覆盖与计数 | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/store.py) |
| `catalog.duckdb` | `index_coverage` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧 free-stock-db/TDX lake 写入及覆盖统计 | market×code；指数文件覆盖/名称/来源 | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/index_pool.py) |
| `catalog.duckdb` | `business_revisions` | 内部协议/审计 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | object_key；revision | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `business_changes` | 内部协议/审计 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | operation_id；object/input/output_revision、source/reason、changed_rows/fields、evidence/state/run | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `fetch_observations` | 内部协议/审计 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | object_key；observed_at/status | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `maintenance_runs` | 内部协议/审计 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | run_id；command/state/metrics | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `maintenance_totals` | 内部协议/审计 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | run_id；changed_rows/fields/applied_operations | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `coverage_totals` | 内部协议/审计 | 业务 writer 与 change_protocol；无独立来源采集命令 | 本地操作、字段差异与取源观察 | run_id；changed_rows | 维护幂等/修订/报告，非金融因子；[实现](../../../src/aspool/change_protocol.py) |
| `catalog.duckdb` | `sync_runs` | 旧布局/兼容保留 | 旧 import --source free-stockdb --period ...、旧 sync 分支或兼容 writer；不是布局 3 新权威写入入口 | 旧 free-stock-db/TDX lake 写入及覆盖统计 | run_id；command/source、起止时刻、symbols/rows/status/error | 兼容/审计读取；生产日线与新目录不能以此表证明覆盖；[实现](../../../src/aspool/store.py) |
| `catalog.duckdb` | `pool_metadata` | 内部布局 | init；platform prepare/activate/consolidate/restore | 本地池布局/版本标记 | key→value、updated_at | 布局路由与状态；[实现](../../../src/aspool/platform_v2.py) |
| `event_baseline.sqlite` | `metadata` | 管理/基准版本 | 基准导入及重建维护 | 来源文件、SHA-256、范围、单位、factor/enriched 状态 | payload 文档 | 基准身份与重建状态检查；[实现](../../../src/aspool/event_baseline.py) |

## 源码定义但本次根目录未出现的表

这部分是条件建表/旧布局/审计工具的能力清单，不计入上述实际表分母。运行根、迁移阶段或显式工具不同可能出现这些表；不得默认为当前生产已启用。

| 条件表 | 创建/输入入口 | 粒度及职责 | 源码 |
| --- | --- | --- | --- |
| `security_daily_facts` | 旧 BaoStock 明确同步；get_daily | symbol×trade_date；源交易状态、ST 等兼容事实 | [security_facts](../../../src/aspool/security_facts.py) |
| `daily_features` / `market_daily_summary` | 布局 1/2 股票 writer；布局 3 迁移输入 | symbol×trade_date / date×scope；现权威名称为 stock_daily_features/market_regime_features | [旧 DDL](../../../src/aspool/stocks_schema.sql) |
| `adjustment_factors` | 旧 ETF 迁移/维护 | symbol×effective_date；现权威在 adjustments.sqlite | [ETF 旧 DDL](../../../src/aspool/sqlite_etf_store.py) |
| `migration_state` | platform prepare/reconcile | dataset；迁移状态，不是金融数据 | [platform_v2](../../../src/aspool/platform_v2.py) |
| `metadata_revisions` / `coverage_changes` / `catalog_change_rows` | change_protocol 条件登记 | 对象元信息修订、覆盖差异、catalog 行变化审计 | [change_protocol](../../../src/aspool/change_protocol.py) |
| `dg03_daily` / `dg03_revision` / `dg03_changes` / `dg03_monthly_parts` | DG03 候选构建/月分区工具 | market×code×trade_date；版本/变更/月分区清单；非生产日线权威 | [dg03_monthly](../../../src/aspool/dg03_monthly.py) |
| `observations` / `collection` / `price_changes` / `repaired_dates` | qfq_audit 采集/修复工具及单独审计库 | 来源观测、收集断点、价格差异、修复日期；不是生产 QFQ 行情表 | [qfq_audit](../../../src/aspool/qfq_audit.py) |
| `evidence_index` / `event_coverage` | replica/base_delta 池外证据与事件覆盖登记 | sha256 证据索引；symbol×事件覆盖窗口 | [base_delta](../../../src/aspool/base_delta.py) |
| `remediation_runs` | remediation/remediation_repair 显式维护 | run_id；修复状态/报告；不属常规来源数据 | [remediation](../../../src/aspool/remediation.py) |

## 解读限制

- 日线的成交量单位需要沿实际来源规范化：MAC 报价按手转股，MAC 历史 K 线已为股；不能从同名 volume 列推断原始协议单位。
- 日线日期、事件生效日期、报告期、股东统计日期、抓取日期和公告日期是不同维度。published_at 可空，不构成自动可用的 point-in-time 库。
- stock_daily_features 包含 TRADED/NO_TRADE/MISSING/INVALID；没有日线不等于已确认停牌。market_regime_features 的 W/M 字段存在不等于 W/M 生产可用。
- board_snapshots 是抓取时成员快照；board_daily 通过 snapshot_id 绑定，不能将当前成员当成来源历史成分。
- stock_qfq_bars 是下载前复权价格依据；stock_adjustment_factors 是本地计算；stock_factor_anchors 是外部核实依据。三者不能混用。
- 可选缓存、旧覆盖统计和运行日志不是新行情源。此次未查询全量值，因此没有断言各表的行数、最新日期、缺失率或来源覆盖率。

## 实际字段附录（按物理数据库）

用于核对物理结构；业务分类以上述四组为准。

### `adjustments.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `adjustment_state`：`dataset`, `revision`, `max_date`, `updated_at`。
- `etf_adjustment_factors`：`symbol`, `effective_date`, `cumulative_factor`, `source`, `updated_at`。
- `stock_adjustment_factors`：`symbol`, `effective_date`, `record_kind`, `source`, `source_key`, `category`, `payload_json`, `source_cumulative_factor`, `event_factor`, `cumulative_factor`, `valid_from`, `valid_through`, `factor_basis`, `updated_at`, `input_hash`, `algorithm_version`。
- `stock_factor_anchors`：`symbol`, `effective_date`, `record_kind`, `source`, `source_key`, `category`, `payload_json`, `source_cumulative_factor`, `event_factor`, `cumulative_factor`, `valid_from`, `valid_through`, `factor_basis`, `updated_at`, `input_hash`, `algorithm_version`。

</details>

### `catalog.duckdb`

<details>
<summary>只读检查到的字段</summary>

- `business_changes`：`operation_id`, `object_key`, `input_revision`, `output_revision`, `source`, `reason`, `changed_rows`, `changed_fields`, `evidence`, `state`, `applied_at`, `run_id`。
- `business_revisions`：`object_key`, `revision`。
- `coverage`：`symbol`, `market`, `start_date`, `end_date`, `row_count`, `source`, `updated_at`。
- `coverage_minutes`：`symbol`, `market`, `start_date`, `end_date`, `row_count`, `source`, `updated_at`。
- `coverage_totals`：`run_id`, `changed_rows`。
- `fetch_observations`：`object_key`, `observed_at`, `status`。
- `fundamental_snapshots`：`symbol`, `market`, `code`, `name`, `total_share`, `float_share`, `eps`, `ttm_eps`, `net_assets`, `refreshed_at`, `source`。
- `index_coverage`：`market`, `code`, `name`, `start_date`, `end_date`, `row_count`, `source`, `updated_at`。
- `index_memberships`：`symbol`, `category`, `active`, `updated_at`。
- `maintenance_runs`：`run_id`, `command`, `state`, `metrics`。
- `maintenance_totals`：`run_id`, `changed_rows`, `changed_fields`, `applied_operations`。
- `pool_metadata`：`key`, `value`, `updated_at`。
- `securities`：`symbol`, `code`, `market`, `asset_type`, `name`, `active`, `listing_date`, `delisting_date`, `updated_at`。
- `security_calendar`：`trade_date`, `is_open`, `source`。
- `security_lifecycle`：`symbol`, `listing_date`, `delisting_date`, `name`, `source`, `fetched_at`。
- `sync_runs`：`run_id`, `command`, `source`, `started_at`, `finished_at`, `symbols`, `rows_written`, `status`, `error`。
- `universe`：`symbol`, `market`, `name`, `active`, `first_seen`, `last_seen`, `source`, `asset_type`。

</details>

### `etfs.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `daily_bars`：`symbol`, `market`, `code`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `turnover_rate`, `name`, `pre_close`, `vol_ratio`, `pct_chg`, `amplitude`, `is_st`, `trading_status`, `pre_close_source`, `is_st_source`, `trading_status_source`, `vol_ratio_source`, `turnover_rate_source`, `amplitude_source`, `pct_chg_source`, `float_share_source`, `total_share_source`, `float_mv_source`, `total_mv_source`, `total_share`, `float_share`, `total_mv`, `float_mv`, `pe_ttm`, `pb`, `datetime`, `vol`, `float_shares`, `asset_type`, `turnover`, `source`, `updated_at`。

</details>

### `event_baseline.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `events`：`row_id`, `symbol`, `effective_date`, `payload`。
- `metadata`：`payload`。

</details>

### `features.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `board_daily`：`date`, `scope`, `classification`, `kind`, `board_id`, `board_name`, `member_count`, `trading_member_count`, `valid_return_count`, `avg_return`, `membership_as_of`, `membership_basis`, `snapshot_id`, `input_digest`, `updated_at`。
- `board_daily_status`：`date`, `scope`, `kind`, `status`, `processed_board_count`, `expected_board_count`, `mapped_trading_count`, `snapshot_id`, `updated_at`。
- `board_snapshot_sets`：`snapshot_id`, `kind`, `membership_as_of`, `membership_basis`, `expected_board_count`, `updated_at`。
- `board_snapshots`：`snapshot_id`, `kind`, `board_id`, `board_name`, `members_json`。
- `board_sync_state`：`kind`, `status`, `snapshot_id`, `attempted_at`, `error`, `updated_at`。
- `feature_state`：`dataset`, `scope_key`, `raw_revision`, `factor_revision`, `algorithm_version`, `status`, `affected_from`, `affected_through`, `updated_at`。
- `market_regime_features`：`frequency`, `period_key`, `scope`, `period_start`, `period_end`, `as_of`, `session_count`, `trading_count`, `st_unknown_count`, `valid_return_count`, `invalid_return_count`, `up_count`, `down_count`, `flat_count`, `strong_up_count`, `strong_down_count`, `avg_return`, `median_return`, `return_distribution_json`, `up_pct`, `strong_up_pct`, `strong_down_pct`, `close_limit_up_count`, `touch_limit_up_count`, `close_limit_down_count`, `touch_limit_down_count`, `limit_known_count`, `no_limit_count`, `limit_unknown_count`, `limit_invalid_count`, `streak_unknown_count`, `max_consecutive_up`, `first_board_count`, `second_plus_count`, `third_plus_count`, `fifth_plus_count`, `ladder_json`, `promotion_eligible_count`, `promotion_success_count`, `amount_sum`, `amount_valid_count`, `avg_turnover`, `turnover_valid_count`, `sealed_ratio`, `promotion_ratio`, `ma20_valid_count`, `above_ma20_count`, `above_ma20_pct`, `trading_observations`, `st_unknown_observations`, `valid_return_observations`, `daily_up_pct_weighted`, `daily_strong_up_pct_weighted`, `daily_strong_down_pct_weighted`, `daily_avg_return_weighted`, `daily_median_return_mean`, `daily_median_valid_sessions`, `daily_return_distribution_json`, `period_return_distribution_json`, `daily_limit_up_mean`, `limit_up_occurrences`, `limit_up_symbols`, `limit_touch_up_occurrences`, `limit_down_occurrences`, `limit_touch_down_occurrences`, `limit_unknown_observations`, `limit_invalid_observations`, `streak_unknown_observations`, `end_max_consecutive_up`, `period_max_consecutive_up`, `promotion_eligible_observations`, `promotion_success_observations`, `period_valid_return_count`, `period_unknown_return_count`, `period_up_count`, `period_down_count`, `period_flat_count`, `period_avg_return`, `period_median_return`, `updated_at`, `upper_median_return`, `avg_vol_ratio_5d`, `vol_ratio_5d_valid_count`, `high_vol_ratio_5d_count`。
- `market_sessions`：`trade_date`。
- `stock_daily_features`：`symbol`, `trade_date`, `source_pre_close`, `source_pre_close_source`, `source_is_st`, `source_is_st_source`, `is_st_name_date`, `trading_status`, `trading_status_source`, `pre_close`, `pre_close_source`, `is_st`, `is_st_source`, `calc_status`, `limit_status`, `limit_reason`, `limit_up_price`, `limit_down_price`, `touch_limit_up`, `close_limit_up`, `touch_limit_down`, `close_limit_down`, `consecutive_up`, `prior_consecutive_up`, `streak_known`, `ma20`, `above_ma20`, `updated_at`。

</details>

### `fundamentals.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `dataset_state`：`dataset`, `revision`, `max_date`, `updated_at`。
- `stock_financial_reports`：`symbol`, `period_end`, `source`, `liutong_guben`, `zong_guben`, `guojia_gu`, `faqiren_faren_gu`, `faren_gu`, `b_gu`, `h_gu`, `zhigong_gu`, `province`, `industry`, `ipo_date`, `zong_zichan`, `liudong_zichan`, `guding_zichan`, `wuxing_zichan`, `liudong_fuzhai`, `changqi_fuzhai`, `ziben_gongjijin`, `jing_zichan`, `zhuying_shouru`, `zhuying_lirun`, `yingshou_zhangkuan`, `yingye_lirun`, `touzi_shouyu`, `jingying_xianjinliu`, `zong_xianjinliu`, `cunhuo`, `lirun_zonghe`, `shuihou_lirun`, `jing_lirun`, `weifen_lirun`, `meigujing_zichan`, `source_hash`, `payload_json`, `published_at`, `fetched_at`, `updated_at`。
- `stock_shareholder_counts`：`symbol`, `as_of_date`, `source`, `shareholder_count`, `source_report_date`, `published_at`, `fetched_at`, `updated_at`。

</details>

### `indices.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `daily_bars`：`symbol`, `trade_date`, `market`, `code`, `name`, `open`, `high`, `low`, `close`, `volume`, `amount`, `up_count`, `down_count`, `breadth_status`, `source`, `updated_at`。

</details>

### `snapshots.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `snapshot_catalog`：`snapshot_id`, `feature_set`, `feature_version`, `as_of_date`, `asset_type`, `scope`, `params_hash`, `input_version`, `params_json`, `created_at`, `expires_at`。
- `snapshot_rows`：`snapshot_id`, `entity_id`, `payload_json`。

</details>

### `stocks.sqlite`

<details>
<summary>只读检查到的字段</summary>

- `corporate_actions`：`symbol`, `effective_date`, `record_kind`, `source`, `source_key`, `category`, `payload_json`, `updated_at`。
- `daily_bars`：`symbol`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `turnover_rate`, `vol_ratio`, `pct_chg`, `amplitude`, `total_share`, `float_share`, `float_shares`, `total_mv`, `float_mv`, `pe_ttm`, `pb`, `name`, `name_as_of`, `name_source`, `ohlcv_source`, `turnover_rate_source`, `vol_ratio_source`, `pct_chg_source`, `amplitude_source`, `total_share_source`, `float_share_source`, `total_mv_source`, `float_mv_source`, `updated_at`。
- `dataset_state`：`dataset`, `revision`, `max_date`, `updated_at`。
- `stock_qfq_bars`：`symbol`, `trade_date`, `open`, `high`, `low`, `close`, `source`, `source_as_of`, `updated_at`。

</details>
