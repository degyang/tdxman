# aspool 完整需求、设计与验证计划

## 目标

aspool 是 A 股长期数据池，保存全市场未复权日线、可选分钟线、复权因子和低频基本面快照。
它是 Fundwise 等研究工具的只读数据底座；研究工具不得自行维护行情副本或调用网络更新。

## 数据契约

日线主库按 free-stockdb 字段命名与单位保存：`date/code/name`、OHLC、`volume`（股）、
`amount`（人民币元）、`turnover`（百分数）、`pct_chg`、`amplitude`、`vol_ratio`、
`total_share`、`float_share`、`total_mv`、`float_mv`、`pe_ttm`、`pb`。

`lake/fundamentals/snapshots.parquet` 是与复权因子平行的低频数据集，保存每个标的最新
`total_share`、`float_share`、`eps`、`ttm_eps`、`net_assets`、`refreshed_at`、`source`。
股本单位统一为股。日线新增或修补时以快照补齐股本和估值字段；不以最新快照回填早期历史。

日线读取 API `DataPool.read_daily()` 与 `DataPool.read_research_daily()` 对外提供稳定字段：
`symbol`、`market`、`code`、`date`、OHLC、`volume`、`amount`、`turnover_rate`，以及主库
保存的名称、昨收、量比、涨跌幅、振幅、ST 状态、股本、市值、PE、PB。基本面快照
是内部维护数据，不属于 Fundwise API。契约 2 支持 fields 投影、describe 能力目录和公开错误码；
完整接口与回测扩展需求见 [aspool API](aspool_api.md)。不可变版本、交易日历、历史证券状态和
严格时点查询尚未实现，主库历史日期不代表具有历史可知时间保证。

## 命令语义

`aspool import --source free-stockdb --period daily|minutes`：一次性完整导入历史原始 K 线。
`aspool import --factor`：导入共享复权因子。

`aspool fundamentals`：手动全市场读取 quote，刷新低频基本面快照。建议财报披露后按周或按月执行。

`aspool update`：收盘后读取 quote，只更新当前或最近交易日的一条日线，并同时刷新基本面快照。
quote 的成交量为手，写入主库前转换为股；其原始 `turnover`、`vol_ratio` 优先保留。
中国工作日 09:00 至 15:30（含）拒绝执行，避免盘中不完整数据落库。

`aspool sync --source free-stockdb`：保留完整历史校准和在线 K 线尾部补齐逻辑。
`aspool sync --source tdx`：使用 K 线修补**已导入标的**的 OHLCV 和成交额，再合并已有基本面快照。它不发现或首次导入全市场标的；空数据池须先执行 `aspool import --period daily` 或 `aspool sync --source free-stockdb --period daily`。每次日线校准会拉取每个标的最近最多 30 根 K 线，命令输出的行数是获取并合并的记录数，包含覆盖重拉的历史行，不是净新增行数。
若 K 线不提供换手率，按 `volume / float_share * 100` 计算；若不提供量比，收盘口径为
当日成交量除以前五个交易日平均成交量。量比的盘中同刻口径需要分钟线，不能由纯日线伪造。

## 一致性与安全

所有写入持有数据池目录排他锁；`DataPool` 读取持有共享锁，避免读到跨标的半批次数据。
导入、同步和 quote 更新均以临时 Parquet 文件原子替换。主日线必须无重复交易日、OHLCV 与
成交额非空且非负、`high >= low`。基本面快照缺失时日线仍可写入行情字段，但不填造估值数据。

## 实施待办

- [x] 建立日线、复权因子、基本面快照及 DuckDB 只读 API。
- [x] 将日线单位规范为成交量（股）、成交额（元）、换手率（百分数）。
- [x] 实现 quote 驱动的收盘后最新日线更新和交易时段保护。
- [x] 将 quote 基本面快照合并进新增日线的 free-stockdb 同名字段。
- [x] 保留 free-stockdb 导入和在线 K 线 `sync` 尾部修补。
- [x] 增加 `sync --source tdx --tdx-mode offline`，从 vipdoc 批量修补日 K。
- [x] 增加收盘量比的五日均量计算并写入 `sync` 新增日线。

## 验证计划

1. 单元测试覆盖字段单位、快照合并、PE/PB/市值计算、quote 成交量换算和交易时段拒绝。
2. 全部 unit tests、Ruff 和文档命令帮助检查。
3. 实盘小批量 quote 更新，核验最新行与 quote 的 OHLC、成交量、金额、换手率相符。
4. 全市场运行 `aspool fundamentals` 与 `aspool update`，检查快照标的数、日线日期、空值、
   非法 OHLC 和重复键。
5. 用 `DataPool.read_daily()` 和 Fundwise Screen 读取真实数据池，确认无网络写入且可筛选。
