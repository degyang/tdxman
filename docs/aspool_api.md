# aspool API：Screen 与 Backtest 数据接口契约

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
    start="2020-01-01", end="2026-09-16",
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
字段投影不会绕过 OHLCV/成交额和重复键检查；目前是结果列投影，尚非完整存储扫描下推。
重复键在证券与日期过滤之后、lookback 截取之前检查：过滤范围内存在重复日线即返回
DAILY_INVALID，即使重复日期不在最后 N 根内；过滤范围外的重复记录不影响本次查询。

### 2.2 全部公开业务字段

| 字段 | 类型与单位 | 说明 |
|---|---|---|
| symbol / market / code | 字符串 | 市场.代码、市场、代码 |
| date | 日期，DataFrame 中为 datetime64 | 主库交易日期；不是内部重复日期列 |
| name | 字符串，可空 | 主库该日保存的名称 |
| pre_close | 浮点，元/股，可空 | 昨收 |
| open / high / low / close | 浮点，元/股 | 未复权 OHLC |
| volume | 数值，股 | 成交量 |
| amount | 数值，人民币元 | 成交额 |
| turnover_rate | 浮点，百分数，可空 | 主库 turnover 的标准对外名称；0.43 表示 0.43% |
| vol_ratio | 浮点，倍，可空 | 主库保存的量比；来源可为报价值或收盘五日均量计算值 |
| pct_chg / amplitude | 浮点，百分数，可空 | 涨跌幅、振幅 |
| is_st | 可空布尔 | 当日主库保存的 ST 状态；未知不等于 False |
| total_share / float_share | 浮点，股，可空 | 总股本、流通股本 |
| total_mv / float_mv | 浮点，人民币元，可空 | 总市值、流通市值 |
| pe_ttm / pb | 浮点，倍，可空 | 主库保存的估值指标 |

可选字段在所有返回结果中保持列结构；源文件无此列则返回有类型的空值。不能用 0 替换未知值。
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
| 基准指数 | 独立证券类型与覆盖说明，不混入默认股票池 |

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
