# aspool 能力矩阵

[矩阵总览](README.md) · [数据库逐表矩阵](aspool-database-capabilities.md) · [生产数据流](../../design/production_data_flow_contract.md)

审理日期：2026-10-04；以当前源码及布局 3 的单一权威存储为准。aspool 是本地采集、计算与读取层；读取不自动联网采集。CLI 默认根为当前目录的 `data/`；`DataPool` 构造默认 `~/.aspool`，示例应显式给根，避免查到另一池。

## CLI 能力

| 能力 | 入口与关键参数 | 输入/输出 | 实现与边界 |
| --- | --- | --- | --- |
| 初始化 | `aspool init --root ROOT` | 初始化本地池 | 已实现；不等于完成生产迁移或历史补齐 |
| 离线导入 | `aspool import --source free-stockdb --period daily/1min/5min --root ROOT；或 --factor` | free-stock-db 行情/因子（路径来自配置，period/factor 二选一） | 已实现旧导入路径；不视为布局 3 日常增量入口 |
| 当前日更 | `aspool update --root ROOT --type stock/index/etf/all --source tdx --symbol SYMBOL` | 股票当日报价；指数/ETF 日 K；局部派生 | 已实现；TDX stock update 不接受历史日期；all 顺序执行而非跨全部数据库一个事务 |
| 历史补齐 | `aspool sync --root ROOT --type stock/index/etf/all --source tdx --period daily --start DATE --end DATE` | 历史日线、事件、受影响派生 | 已实现；股票日常窗口最多 10 个输入交易日、每股至多 1024 根定位；旧分钟/lake 分支不属于生产日线合同 |
| 显式备用源修复 | `aspool sync --type stock --source baostock --status missing --start DATE --end DATE` | 沪深日线、交易状态与日历 | 已实现；不自动切源；BJ 保持未知/缺失；all 仅 TDX |
| 当前证券目录 | `aspool directory --root ROOT --type stock/etf/index` | 当前目录；指数分类关系 | 已实现；`universe` 为隐藏兼容别名；当前目录不证明历史成员 |
| 财务与股东人数 | `aspool fundamentals --root ROOT --source tdx --symbol SYMBOL` | `TdxClient.get_finance_info`→报告期/股东日期行 | 已实现；重复 symbol 可选；独立命令；当前 all 未调用；历史 sync 不倒填历史财报；最新摘要不是完整 PIT 财务库 |
| 因子初始化 | `aspool factors-bootstrap --root ROOT --as-of DATE --symbol SYMBOL [--maintenance]` | 上市区间 NONE/QFQ/HFQ 价格与事件核验、局部因子 | 已实现显式维护；maintenance 最多 200 标的；不把无依据因子设为 1 |
| 有界查询 | `aspool query [SYMBOL] --dataset DATASET --start DATE --end DATE --limit 1000 --format json` | 目录、calendar、fundamentals、stock/index/etf-bars、corporate-actions、stock-features、limit-events、market-summary、etf-factors | 已实现；默认 stock-bars；query 不采集/迁移；逻辑数据名不等于物理表名 |
| 状态/合同 | `aspool status --dataset DATASET --format json`；`aspool contract --format json` | 数据状态、机器可读公共合同 | 已实现；状态检查不能代替完整质量验收 |
| 布局维护 | `aspool platform ACTION --root ROOT [--deep] [--recovery PATH]` | prepare/reconcile/verify/status/activate/rollback/consolidate/recover/restore | 已实现；深验仅 verify；迁移和恢复必须按现有维护合同执行 |
| 增量副本 | `aspool replica export --source-root A --target-root B --dataset D --symbol S --start DATE --end DATE --output PATH`；`replica apply/apply-stock --root ROOT --package PATH`；`replica recover --root ROOT` | 有界数据包、发布与恢复 | 已实现；不以手工复制某张派生表替代一致发布 |
| 扩展分类 | `aspool ex categories` | 扩展市场分类枚举 | 已实现目录辅助；不宣称扩展商品已纳入生产股票池 |

公共采集选项包含 `--retries 0..5`、`--retry-delay 0..30`、`--max-consecutive-failures`、`--workers`（具体命令见 help）。股票报价可并发读取，写入有界串行提交；零散 MISSING/INVALID 与源熔断分别报告。普通 update/sync 不自动扩大到全历史维护。

## 公共 Python 读取

| 能力 | `DataPool` 方法 | 主要维度 | 边界 |
| --- | --- | --- | --- |
| 股票行情 | `read_daily`、`read_research_daily` | 证券×交易日；字段投影/复权/范围 | 原始与研究视图按各自质量合同；不自动采集 |
| 股票事实与资料 | `read_security_daily`、`read_security_info` | 证券×日期、证券身份 | 兼容来源/布局需按合同；当前名称不可倒填历史 ST |
| 交易日历 | `read_trading_calendar` | 日期×开市状态 | 已观测日期，不据空数据推断休市 |
| 指数行情/目录 | `read_index_daily`、`list_indices` | 指数×日、分类 | breadth_status 决定涨跌家数是否可解释 |
| ETF 行情/目录 | `read_etf_daily`、`list_etfs` | ETF×日 | ETF 因子依赖已审核来源，不承诺在线因子维护 |
| 财务/股东人数 | `read_fundamental_reports`、`read_shareholder_counts` | 证券×period_end/as_of_date×来源 | 报告日期不等于公告日期；published_at 可空 |
| 下载前复权依据 | `read_downloaded_qfq` | 证券×日×source_as_of | 外部价格依据，不是本地原始日线 |
| 涨跌停与连板 | `read_limit_events`、`read_limit_summary`、`read_limit_exceptions/references/coverage/scope/staleness`、`iter_limit_events_with_amount`、`describe_limits` | 证券×日；市场×日；金额有界分批 | BJ 为 NO_LIMIT；缺参考价、ST、行情各自表达未知 |
| 市场特征 | `read_market_daily`、`read_market_summary`、`describe_market_fields` | frequency×period_key×scope | D 可用；W/M 契约预留、生产未 ready 时返回 FREQUENCY_NOT_READY；Regime 评分属于消费端 |
| 板块聚合/成员 | `read_board_daily`、`read_board_members` | 日期×scope×行业/概念×板块；snapshot×成员 | 绑定成员快照，不宣称来源提供历史成分 |
| 一致读取/描述 | `stock_snapshot`、`describe`、`status` | 发布版本、数据集状态 | 高基数读取按 symbol/日期/行数预算；异常为 DataPoolError |
| 显式计算 | `compute_limit_events` | 证券×日期 | 独立计算入口，不代表所有只读 API 都会写派生 |

## 本次审理结论

股票/指数/ETF 日线、日级派生、板块快照、财务摘要历史归档已有实现。应持续区分分时与分钟 OHLC、当前目录与历史成员、财报期与公告期、下载复权依据与本地因子、物理表与逻辑读取名。旧 lake/coverage/universe 表、可选 snapshot 缓存和审计库单列在数据库矩阵中；其存在不扩大生产能力。此次仅做源码/结构审理，未运行在线采集、迁移、全市场行级审计或生产性能验收。

源码：[CLI](../../../src/aspool/cli.py) · [DataPool](../../../src/aspool/pool.py) · [公共读取实现](../../../src/aspool/sqlite_read_api.py) · [日常更新合同](../../ops/sqlite_update_sync_cli.md)。

## 与既有设计文档的差异

当前 `cli._run_all` 实际顺序为目录→指数/日历→股票/派生→ETF→报告，未调用 `update_fundamentals`。因此 [生产数据流文档](../../design/production_data_flow_contract.md)中“all 最后刷新基本面”的文字不能作为当前已实现能力；需要单独执行 `aspool fundamentals`。当前 ETF `update` 与 `sync` 都调用 `sync_etfs`，主来源为日 K 线，报价仅用于空 K 线时确认无交易；不能套用股票 update 只读报价的规则。

旧 `sync` 仍注册 `--tdx-mode online/offline`、`--async`、`--category`、`--lookback`、`--baostock`、`--enrich/--no-enrich`、`--enrich-only` 等兼容参数及 `--type ex` 分支。这说明命令树仍有旧能力，不表示布局 3 SQLite 股票日常写入接受全部组合；入口会按资产/布局拒绝不支持的组合。此次只记录差异，未修改业务实现或既有设计文档。
