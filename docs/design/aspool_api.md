# aspool API：Screen 与 Backtest 数据接口契约

> 数据分层术语以[基础数据与 Enriched 数据分层契约](data_layers.md)为准：历史 K 线、下载的复权依据、财务等属于基础数据；计算出的复权因子、逐股衍生、市场/板块聚合及计算快照属于 Enriched 数据。


契约版本：2。Python 包随 tdxman 安装，对外入口为 `DataPool` 和 `DataPoolError`。
本文明确区分已经实现的 API 与回测扩展需求；规划中的方法不可直接调用。

## 1. 职责与边界

aspool 维护市场主数据，提供只读查询；Fundwise 负责信号、选股、撮合、账户和报告。
Fundwise 通过 Python API 获取 pandas DataFrame，不解析 CLI 输出，不依赖 DuckDB SQL、
Parquet 路径或内部基本面快照。读取不得联网、创建数据目录、修改 catalog 或触发同步。

主库各日期已经保存的股本与估值直接返回；读取时不连接最新基本面快照、不重新计算历史值。
内部 EPS、TTM 收益、净资产以及快照来源、快照时间不进入公开日线结果。

## 2. 已实现 API

```python
from aspool import DataPool, DataPoolError

pool = DataPool("~/.aspool")
contract = pool.describe()
status = pool.status()

# 当前 Screen：截止日期之后不读，每个证券最多 121 根有效日线。
bars = pool.read_research_daily(end="2026-09-16", lookback=121)

# 读取主库实际保存的历史股本与估值。
history = pool.read_research_daily(
    symbols=["SZ.000001", "SH.600519"],
    start="2020-01-01",
    end="2026-09-16",
    fields=["symbol", "date", "close", "total_mv", "pe_ttm", "pb"],
)
```

### 2.1 日线读取

`read_research_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)`

`read_daily()` 使用相同参数、字段和质量校验。Fundwise 统一使用 `read_research_daily()`。

| 参数 | 语义 |
|---|---|
| symbols | 完整证券标识字符串或列表；None 为池内全部证券，空列表返回空结果 |
| start/end | 日期或可解析日期字符串；包含边界，省略表示不限制该边界 |
| lookback | 正整数；先按日期截断，再逐证券保留最近 N 根实际记录 |
| fields | 业务字段字符串或列表；None 返回全部，指定后按指定顺序返回；未知、重复或空列表报错 |

返回 pandas DataFrame，按完整 `symbol,date` 排序。缺失交易日不补行，停牌与缺失数据不能仅靠
日线缺行区分。证券代码保留前导零；不得将 `SH.000001` 与 `SZ.000001` 合并。
字段投影会下推到实际输出查询和补充事实关联；即使只请求成交额，仍在 SQL 中校验
所选日线的 OHLCV/成交额和重复键，不物化完整宽表。普通 DataFrame 读取最多
500,000 行，超过时返回 `DAILY_TOO_LARGE`；事件长窗口请用下述分批接口。
重复键在证券与日期过滤之后、lookback 截取之前检查：过滤范围内存在重复日线即返回
DAILY_INVALID，即使重复日期不在最后 N 根内；过滤范围外的重复记录不影响本次查询。

### 2.2 全部公开业务字段

| 字段 | 类型与单位 | 说明 |
|---|---|---|
| symbol / market / code | 字符串 | 市场.代码、市场、代码 |
| date | 日期，DataFrame 中为 datetime64 | 主库交易日期；不是内部重复日期列 |
| name | 字符串，可空 | 主库该日保存的名称 |
| pre_close | 浮点，元/股，可空 | 当日参考前收价；除权日不等于上一条收盘价 |
| open / high / low / close | 浮点，元/股 | 未复权 OHLC |
| volume | 数值，股 | 成交量 |
| amount | 数值，人民币元 | 成交额 |
| turnover_rate | 浮点，百分数，可空 | 主库 turnover 的标准对外名称；0.43 表示 0.43% |
| vol_ratio | 浮点，倍，可空 | 主库保存的量比；来源可为报价值或收盘五日均量计算值 |
| pct_chg / amplitude | 浮点，百分数，可空 | 涨跌幅、振幅 |
| is_st | 可空布尔 | 当日主库保存的 ST 状态；未知不等于 False |
| trading_status | 字符串，可空 | 已有依据的 TRADING/SUSPENDED；缺失不等于停牌 |
| pre_close_source / is_st_source / trading_status_source | 字符串，可空 | 对应字段保存的数据来源 |
| vol_ratio_source / turnover_rate_source / pct_chg_source / amplitude_source | 字符串，可空 | 指标来源或计算口径 |
| float_share_source / total_share_source / float_mv_source / total_mv_source | 字符串，可空 | 日期股本依据及市值计算口径 |
| total_share / float_share | 浮点，股，可空 | 总股本、流通股本 |
| total_mv / float_mv | 浮点，人民币元，可空 | 总市值、流通市值 |
| pe_ttm / pb | 浮点，倍，可空 | 主库保存的估值指标 |

可选字段在所有返回结果中保持列结构；源文件无此列则返回有类型的空值。不能用 0 替换未知值。
股票 `sync` 的字段补齐顺序、日期股本和收盘量比口径见 [日线补齐](daily_enrichment.md)。
返回值不为 CLI 显示而舍入。纯技术 Screen 无须要求股本、估值非空；使用这些字段的策略自行
检查所需字段覆盖率。空结果保持相同字段结构。投影可排除不需要的字段。

DataFrame.attrs 包含 `contract_version=2`、`price_adjustment='raw'`、`volume_unit='share'`、
`amount_unit='CNY'`、`turnover_rate_unit='percent'`、`available_at=None`、
`point_in_time=False`、`dataset_version=None`。这是数据契约说明，不是内部基本面维护信息。

### 2.3 状态与能力目录

`describe()` 返回字段逻辑类型、单位、可空性、主键、价格口径和能力开关，无文件写入。

`status()` 返回 backend、status、root；有日线时另含 row_count、symbol_count、start、end、
price_adjustment。存在但没有日线的目录返回 status=empty；数据池目录不存在则报错。
当前不返回不可变版本 ID；以 `describe().capabilities` 判断回测扩展是否具备。

### 2.4 错误与一致性

```python
try:
    frame = pool.read_research_daily(end="2026-09-16")
except DataPoolError as exc:
    print(exc.code, str(exc))
```

| 错误码 | 含义 |
|---|---|
| POOL_NOT_FOUND | 数据池目录不存在 |
| DAILY_NOT_FOUND | 数据池没有日线文件 |
| DAILY_INVALID | OHLCV/成交额缺失、非有限或非法值、重复键、读取解析失败 |
| FIELD_UNSUPPORTED | 请求字段未知、重复或列表为空 |
| INVALID_ARGUMENT | 无效窗口或日期参数 |

一般参数错误也可能表现为 ValueError；DataPoolError 继承 ValueError，兼容原调用方。
读取通过共享锁等待使用排他锁的 aspool 写入完成；直接绕过 API 写底层文件不受此机制保证。
同一次读取完成后数据在内存中稳定，多次读取之间不保证版本相同。

### 2.5 BaoStock 日期事实与基础资料

完成 `aspool sync --source baostock` 或 `aspool update` 的补齐阶段后可读取：

| API | 数据 |
|---|---|
| `read_security_daily(*, symbols=None, start=None, end=None)` | 按证券和日期保存的 pre_close、is_st、trading_status、source、fetched_at；包含可靠停牌会话 |
| `read_security_info(*, symbols=None)` | listing_date、delisting_date、当前名称、source、fetched_at；退出日期可能是旧代码退出 |
| `read_trading_calendar(*, start=None, end=None)` | 已存沪深自然日日历，trade_date、is_open、source |

证券输入接受 `600519.SH` / `SH.600519`，输出采用 `代码.市场`；日期列为 `trade_date`。
缺行不表示非 ST、停牌或休市。未建表返回 `DATASET_NOT_FOUND`。读取不创建表或联网。
`attrs` 声明 `scope='SH,SZ'`、`source='baostock'`、`point_in_time=False`。
能力开关为 `security_daily/security_info/stored_trading_calendar=True`；这不代表完整历史股票池、
跨市场日历与回测交易状态契约已经实现，原 `calendar/trading_status` 扩展开关仍为 False。
来源和合并规则见 [BaoStock 补充数据源](../ops/baostock.md)。

## 3. Backtest 扩展需求：尚未实现

当前日线接口可以支持历史信号实验；尚不具备完整、可重放、无前视保证的收益回测输入。
主库保存了历史日期，并不证明字段在当时已公开可知。旧同步可能使用后来的股本快照补算
历史估值；这些数据须审计来源后才能用于严格历史财务筛选。

以下是规划能力，不能按此示例调用：

| 拟定能力 | 需求 |
|---|---|
| 固定数据版本读取 | 所有读取接受同一 dataset_version；更新与修订后旧版本内容不变 |
| dataset_metadata(version) | 版本、内容哈希、覆盖范围、缺口及历史时点保证；不暴露内部快照表 |
| read_calendar() | 按市场读取真实交易会话，支持下一交易日及预热日期轴 |
| read_instruments() | 按历史日期读取上市、退市、证券类型及当时股票池 |
| read_trading_status() | 当日停复牌、价格限制、适用规则依据；未知状态明确表达 |
| read_corporate_actions() | 分红、送转、配股等事件、生效日与所需账户处理事实 |
| read_adjustments() | 复权因子、基准与版本；因子不替代分红现金流和持仓调整 |
| 分批读取 | 大区间迭代读取、固定版本和预热衔接，控制多年全市场内存使用 |
| 基准指数 | 已支持独立指数日线与覆盖读取（见第6节）；不可变版本及历史成分仍待实现 |

普通 read 方法保持只读；不可变版本由 aspool 维护流程发布，Fundwise 只保存版本引用。
严格历史查询须对决策时刻过滤未来才可知的数据；缺少保证时明确拒绝，不能用最新值代替。
不可变版本保证重现，历史可知时间保证无前视，二者分别验收。

价格默认 raw；以后复权须显式选择前/后复权及基准日，说明各字段调整范围。
信号价格与成交价格分开，Fundwise 按原始价格记现金成交、按公司行动处理账户。
分钟历史仍属后续阶段，不在本次接口实现范围内。

## 4. 交付与验收顺序

1. 当前 Screen：主库业务字段全量返回、字段投影、空值、标识、错误和只读契约。
2. 历史信号：固定数据版本、交易日历、分批读取和逐日截断一致性。
3. 收益回测：历史股票池、交易状态、公司行动、复权与基准。
4. 财务历史筛选：逐字段审计历史时点覆盖，缺失能力拒绝或由调用方明确限制实验范围。

验收应覆盖：无内部基本面快照仍可读取；历史 PE 不因最新快照改变；跨市场同码不混淆；
投影与空结果列结构稳定；读取无网络和文件修改；未来数据扰动不改变严格历史结果；
固定版本重读哈希一致；停牌、未知状态和行情缺口能分别识别。

## 5. 从旧契约迁移

契约 2 在原日线基础上增加主库业务列；默认 raw 价格、日期截断和 lookback 语义不变。
不再提供公开 read_fundamentals，也不提供 EPS、TTM 收益、净资产和 fundamentals_* 日线列。
Fundwise 可用 fields 固定当前技术策略输入；以后增加市值或估值策略时显式声明所需字段。
不要根据新增字段静默更改原策略规则。

## 6. 指数读取接口（已实现）

指数池通过独立 API 对接 Fundwise，股票 `read_daily/read_research_daily/status` 仍只读取股票。
新增能力开关 `describe().capabilities.index_daily`、`index_listing` 均为 true；
`describe().index_fields` 提供指数专用字段类型和单位。保持原契约版本2，以新增能力发现兼容扩展。

```python
from aspool import DataPool

pool = DataPool("~/.aspool")
indices = pool.list_indices()
benchmark = pool.read_index_daily(
    symbols=["SH.000300", "SZ.399001"],
    start="2010-01-01",
    end="2026-09-16",
    fields=["symbol", "date", "close", "volume", "amount", "up_count", "down_count"],
)
sector_history = pool.read_index_daily(symbols="SH.881001", end="2026-09-16", lookback=120)
```

`list_indices(*, symbols=None)` 返回实际已存指数的 symbol、market、code、name、start、end、row_count。
这不是历史成分股列表，也不依赖消费端能够找到 settings 配置文件。

`read_index_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)`：

- symbols 用市场.代码（SH.000300），支持单个字符串或列表；None 为全部存储指数，空列表返回空表。
- 日期包含边界，不传时返回最长已存历史；lookback 在日期过滤后按指数取最近 N 根。
- 重复键先检查、再截取 lookback；字段投影不绕过返回记录 OHLCV/金额质量检查。
- fields 为指数字段的有序子集；空、重复、未知字段报错。
- 返回 pandas DataFrame，按 symbol/date 排序；读取只使用本地数据和共享锁，无联网写入。

| 字段 | 含义与单位 |
|---|---|
| symbol / market / code / name | 指数标识、市场、代码、名称 |
| date | 日期，datetime64 |
| open / high / low / close | 指数点位，非人民币股价 |
| volume | 通达信指数成交量原始口径，单位元数据为 tdx_index_volume；不作为股票股数 |
| amount | 成交额，CNY |
| up_count / down_count | 原始上涨/下跌家数，缺失按0保留 |

attrs 包含 asset_type=index、price_unit=point、volume_unit=tdx_index_volume、amount_unit=CNY、
breadth_missing_value=0、price_adjustment=raw、point_in_time=False、dataset_version=None。
当前无历史涨跌家数时0不代表确定“零涨零跌”，消费端须自行处理；指数统计口径随指数而异。

新增错误码：INDEX_NOT_FOUND（无指数文件）、INDEX_INVALID（重复键、非法数据或读取失败）；
沿用 POOL_NOT_FOUND、INVALID_ARGUMENT、FIELD_UNSUPPORTED。错误由 DataPoolError.code 读取。

Fundwise 可据此读取宽基回测基准、行业/概念/风格趋势和市场宽度。
实时同步可能包含当日未收盘条目，Screen/Backtest 应显式指定已收盘的 end 日期。
名称与名单是当前维护结果，不具备历史时点保证；不可变版本、历史成分、交易日历等正式回测前提仍未实现。

源数据中 OHLC 关系非法的记录在同步时隔离并记入报告，读取结果可能因此缺少交易日；不补造或前向填充。

## 7. ETF 读取接口（已实现）

ETF 是上市基金证券，使用股票式日线契约，不属于指数 API。同步命令为：

```bash
aspool sync --type ex --category ETF --source tdx --period daily
```

首次或新 ETF 初始化只保留 `2010-01-01` 及以后的可用日线，后续按最近重叠窗口增量更新。
`DataPool.list_etfs()` 返回已同步 ETF 的覆盖范围；`read_etf_daily()` 的参数和普通
`read_daily()` 一致，字段为股票式 OHLCV、成交额和换手率，`attrs.asset_type` 为 `etf`。
ETF 不含 `up_count/down_count`，不能传给 `read_index_daily()`。

扩展资产类别由 `DataPool.describe()["ex_categories"]` 发现，完整规划见
[aspool ex 扩展资产域设计](aspool_ex_design.md)。只有 `status=implemented` 的类别允许进入
数据同步；当前为 `ETF`，港股、美股和大宗期货仍是规划项。

## 8. 连板跨缺行口径（v7）

`read_limit_events()` 的 `consecutive_up` 按用户指定的口径接续：缺行前已有确定的
涨停连板，缺行会话暂停计数；其后继续涨停则在原连板数上递增。例如前 3 板、
中间缺 2 个会话、后 2 板，最终为 5 板。中间出现明确非涨停则归零。

新增 `consecutive_gap_sessions`：当前连板累计跳过的缺行市场会话数，上例为 2。
它不含已证实停牌的会话；大于 0 表示结果采用了跨缺行连续性假设。
该标记沿当前连板传播，在明确非涨停时归零。未知连板及旧版事件返回 null。
日汇总 `max_consecutive_up_note` 会说明是否包含此类接续连板。

缺行日自身的事件状态和异常记录仍是未知；原始行情与停牌身份不因连板接续被改写。
缺行前连板数未知、缺行前为非涨停，以及存在行情但价格非法、规则或参考价未知的情况，
不适用本次“前连板 + 后连板”的接续条件。

每日缺行中的可接续状态保存在 `daily_limit_gap_states`，与发布批次同事务更新，
保证分日同步、进程重启后和全量顺序计算使用同一口径。

## 9. 已发布事件与当日成交额的有界读取

`DataPool.iter_limit_events_with_amount(*, start, end, symbols=None, fields=None,
close_limit_up=None, min_consecutive_up=None, batch_days=7, max_rows=25000,
memory_limit="512MB", threads=2, temp_directory=None)` 返回上下文管理器中的迭代器。
`start/end` 必填且含边界；每批最多 7 个自然日和 25,000 条事件。筛选默认不排除
未知事件；显式指定 `close_limit_up=True, min_consecutive_up=1` 才选择已知连板涨停。
批次过大时报 `LIMIT_TOO_LARGE`，调用方可缩短 `batch_days` 或缩小证券范围。

默认字段为 `trade_date, symbol, close_limit_up, close_limit_down, touched_limit_up,
touched_limit_down, limit_up_price, limit_down_price, consecutive_up,
consecutive_gap_sessions, amount, batch_id, rule_version, computed_at, published_at,
stale, stale_reason`。`fields` 可以选取这些字段并保留指定顺序。证券格式为
`000001.SZ`，`amount` 来自同证券同日已存日线，单位人民币元；没有同日日线或
成交额时为 null；源 Parquet 没有 `amount` 列或多文件 schema 不一致时，同样保留 null。
日线键重复时报 `DAILY_INVALID`；事件键重复时报 `LIMIT_INVALID`。
不按前后交易日填补，也不丢弃缺成交额事件。
每批 DataFrame 的 `attrs` 包含 `contract_version=1` 与 `amount_unit='CNY'`。

```python
from pathlib import Path
from aspool import DataPool

staging = Path("/tmp/regime-events-staging")  # 用本轮独立目录，失败时整轮丢弃
staging.mkdir(parents=True, exist_ok=True)
with DataPool("~/.aspool").iter_limit_events_with_amount(
    start="2021-05-27", end="2026-09-24",
    close_limit_up=True, min_consecutive_up=1,
) as batches:
    for index, frame in enumerate(batches):
        frame.to_parquet(staging / f"part-{index:05d}.parquet")
        del frame
# 只有正常读到末尾、无 LIMIT_REVISION_CHANGED 时，才发布本轮缓存。
```

读取开始及每批边界会比较整个日期窗口的发布批次、发布时间、规则版本、计算时间及
stale 状态。读中发生重发或修订时报 `LIMIT_REVISION_CHANGED`，调用方应删除本轮
暂存结果并重试。上下文结束、提前退出及异常均关闭查询连接和锁，并清理本轮独立
DuckDB spill 目录；`temp_directory` 指定其父目录，调用方负责提供可写目录。
`memory_limit` 限制 DuckDB 引擎，不包含 Pandas/Arrow 和写缓存分配；总进程 RSS
需独立测量。默认 `512MB` 为 DuckDB 十进制配置（显示约 488.2 MiB），线程为 2；
`batch_days` 允许 1—31、`max_rows` 允许 1—100,000、`threads` 允许 1—8。
普通日线读取的扫描和必要事实关联也各自使用 `512MB`、2 线程及自动清理的临时目录；
仅请求 `symbol/date/amount` 时不关联 ST、参考价及其来源字段。输出超过 500,000 行报
`DAILY_TOO_LARGE`；证券代码 `code` 仍为字符串。普通 DataFrame 接口不提供跨调用版本一致性。

## 10. 旧主板 IPO 首日的连板计数（v8）

按用户指定口径，2014-06-13（含）至 2023-04-10（不含）的沪深主板 IPO，
只有证券上市日期明确等于计算日时，才将该上市首日排除在连板计数之外。
后续第一个已确认涨停记为 1 板，连续涨停依次累计；同时保留 v7 的跨缺行接续口径。
不使用日线文件的首条记录推断上市日期。

这项调整只建立连板计数边界。首日特殊限价尚无充分计算依据时，该日涨跌停、
触板及限价仍为 UNKNOWN，异常说明中记录“连板口径排除已确认的上市首日”。
不得将其当作已确认的非涨停事件。

边界以 `basis=legacy_ipo_first_day_excluded` 保存于
`daily_limit_streak_boundaries`，与当日发布批次同事务更新。
分日同步读取已发布边界，因此与连续全量重算使用相同计数起点。
规则版本为 `cn-a-share-limit-v8`；全量重算报告另导出 `streak_boundaries.csv`。

## 11. SQLite 分层布局兼容接口（contract v3）

项目 `data/stocks.sqlite` 存在时，`DataPool` 使用 contract v3。物理布局由
`catalog.duckdb:pool_metadata.layout_version` 选择；调用方不能根据某个 SQLite 文件是否存在来
自行路由。布局 2 激活后，稳定逐股派生和市场 Regime 公共特征来自 `features.sqlite`，复权因子
来自 `adjustments.sqlite`，但 Fundwise 现有方法和缺省参数不变：

```python
DataPool(root)
pool.describe()
pool.status()
pool.describe_limits()
pool.read_research_daily(...)
pool.read_index_daily(...)
pool.read_trading_calendar(...)
pool.read_market_daily(...)
pool.stock_snapshot()
pool.read_limit_summary(...)
pool.read_limit_events(...)
pool.read_limit_coverage(...)
pool.read_limit_exceptions(...)
pool.iter_limit_events_with_amount(...)
```

`read_limit_coverage()` 和 `read_limit_exceptions()` 在 v3 是兼容投影。它们不依赖旧 publication、
revision、coverage 或 exceptions 物理表；`batch_id/rule_version/computed_at` 等旧批次字段返回
NULL，`stale=False`，未知和无效记录从当前逐股状态投影。

新增的可选读取签名为：

```python
pool.read_daily(
    *, symbols=None, start=None, end=None, lookback=None, fields=None,
    adjust="none", adjustment_base=None,
)
pool.read_etf_daily(
    *, symbols=None, start=None, end=None, lookback=None, fields=None,
    adjust="none", adjustment_base=None,
)
pool.read_fundamental_reports(*, symbols=None, start=None, end=None)
pool.read_shareholder_counts(*, symbols=None, start=None, end=None)
```

`adjust="none"` 保持未复权；`qfq` 缺省以查询结果末日因子归一，`hfq` 缺省以该证券查询历史的
首个因子归一；`adjustment_base` 可显式指定基准日期。仅 OHLC 和 `pre_close` 乘因子，成交量、
成交额、换手率及证券身份不调整。返回 `attrs` 追加 `adjustment`、`adjustment_base` 和实际使用的
因子基准。因子缺失或覆盖不完整时报 `ADJUSTMENT_NOT_READY`；布局 2 的原始、因子与派生 revision
不一致时报 `DERIVED_NOT_READY`。

基本面读取按来源报告日期返回，股东人数按 `as_of_date` 返回。公告日期未知时
`published_at=NULL`，不能把这些行当作严格 point-in-time 财务数据。单次读取上限 500,000 行，
超限时报 `FUNDAMENTALS_TOO_LARGE`。

```python
pool = DataPool("/mnt/d/workstation/services/tdxman/data")
raw = pool.read_daily(symbols="000001.SZ", start="2020-01-01", end="2026-09-29")
qfq = pool.read_daily(
    symbols="000001.SZ", start="2020-01-01", end="2026-09-29",
    fields=["date", "open", "high", "low", "close"], adjust="qfq",
)
holders = pool.read_shareholder_counts(symbols="000001.SZ")
```
