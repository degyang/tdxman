# DG-00：现行数据契约与未决项

日期：2026-09-27。依据 tdxman 基线 `5083dbb` 与 Fundwise `1b25658` 的代码；这是兼容性基线，不是全市场数据完整性证明。整改状态以 [推进方案](data_remediation_execution_plan.md) 为准。

## 键、范围与单位

| 数据域 | 唯一键 / 范围 | 契约 |
|---|---|---|
| 股票日线 | 公开 `(symbol,date)`；内部 `(market,code,trade_date)` | 接受 `000001.SZ` / `SZ.000001`；公开返回 `000001.SZ`。价格不复权，元/股；volume 为股，amount 为人民币元 |
| ETF 日线 | 与股票同一物理树，以 `asset_type=etf` 区分 | 股票接口排除 ETF，ETF 接口独立；不得随 Fundwise 字段裁剪删除 ETF 数据 |
| 指数日线 | `(market,code,trade_date)`，独立指数目录 | 保持既有指数 API 单位与元数据，不用证券 volume 换算覆盖指数定义 |
| 日期事实 | `security_daily_facts(symbol,trade_date)` | `pre_close,is_st,trading_status,source,fetched_at`；与真实成交 OHLCV 分离 |
| 生命周期 | `security_lifecycle(symbol)` | 上市/退市日、名称、来源及抓取时间；无历史名称有效期模型 |
| 日历 | `security_calendar(trade_date)` | `is_open` 是来源明确给出的会话事实；不能把缺行当休市 |
| 限价派生 | 日发布指针 `(trade_date,batch_id)` 关联批次内各表 | 同日 summary / coverage / events / exceptions / references 必须匹配发布批次，规则版本和 stale 独立保留 |

股票字段全集以 [api_contract.py](../src/aspool/api_contract.py) 的 `DAILY_FIELDS` 为准。`turnover_rate,pct_chg,amplitude` 是百分数值（0.43 表示 0.43%）；`vol_ratio` 是比率；`float_share,total_share` 为股，市值为元。限价 `limit_pct` 是比率（0.10 表示 10%）。MAC 日 K、实时 quote、离线 vipdoc 的成交量在各采集入口换算，不能重复乘 100。

日期使用市场交易日，非 UTC 截日。公开日期为 Pandas 时间列；输入起止日期包含边界，lookback 与日期过滤语义保持现行 API。Fundwise Regime 按上证指数实际会话选窗口，按月计算时向前读取 60 **自然日**预热，不是固定 60 个交易日。

## NULL、来源与覆盖优先级

- 缺失、不合法、无交易、停牌是不同状态。缺行情不证明停牌；只有明确停牌事实才允许算法按相应规则处理，不凭空制造平价零量日线。
- 普通增量合并忽略 None/NaN/pd.NA，保留已有可靠值；False 和合法 0 不是缺失。可选 `pre_close` 必须有限且大于 0，`is_st` 必须为布尔值。非法输入记录质量问题，不能覆盖可靠旧值。
- 原始 OHLCV 变动会使依赖指标失效；补齐算法可以显式撤销无法证明的派生值。这与“来源没返回某字段”不同。当前没有公开的任意删除/撤销输入协议；不得把空响应解释成删除。
- `pool._overlay_dated_fields`：非空日期事实优先；其次使用当前发布批次的限价参考价；对应日期 stale 时该参考价不可用；再按现行规则使用原始日线字段。来源字段随同输出。不能绕过 overlay 读取物理 Parquet 后宣称与公开契约相同。
- BaoStock 补足缺失 OHLCV；有效主来源价格冲突时记录并拒绝该候选，不能静默覆盖。日期事实冲突字段置未知，其他可验证字段保留。补齐派生优先保留 BaoStock 历史换手率，股份快照不得回填成所有历史时点股份。
- 名称/ST/板块不等同完整 PIT。Fundwise 当前 ST 排除使用截止日快照；概念/行业来自 tdxman SDK、本地缓存，不是 v8 发布的一部分。本轮不改此业务口径。

公开日线元数据当前 `contract_version=2, price_adjustment=raw, point_in_time=False, dataset_version=None`。v8 是限价规则/发布记录中的版本，不能代表全池版本或可删旧数据的依据。

## 消费方最低需求

Fundwise 环境读取：`symbol,market,code,date,close,pre_close,pct_chg,amount,is_st,turnover_rate`；截止日另读 `name,is_st`。选股还需要 OHLCV。上证指数用于会话轴与趋势；回测和观察还使用其他基准指数。Regime 同时依赖多年派生、批次/stale、连板、exceptions 和事件成交额关联。

`UNKNOWN/INVALID/NO_LIMIT` 必须区分。stale 不能由清标记解决；必须重新计算并一致发布。部分覆盖不得填成“0 个涨停”。有界事件读取继续遵守五年峰值 RSS ≤ 2 GiB、上下文退出清理以及跨批来源变化检测。

## 已知缺口及处置

| 缺口 | 处理 / 后续任务 |
|---|---|
| 既有 605 日 stale、来源冲突与 522 条候选记录 | 重新核验后的基线见 [证据](evidence/data-remediation/20260927-dg00/baseline-verification.json)；DG-04 逐项处理，不沿用历史数量直接批量覆盖 |
| 日期事实仅覆盖一部分证券/日期；27 种物理 schema | 保留 NULL 和来源；DG-04 做按证券/日期/字段缺口核验。现有行数不证明覆盖完整 |
| 分年布局有 writer/read/status/ETF 兼容缺口 | DG-02 统一入口，DG-03 全字段验证；不得先普遍拆分 |
| 无公开逻辑 revision；Fundwise 使用文件统计兜底 | DG-01 记录真实变化，DG-06 再建立公开版本契约，期间保留保守缓存失效 |
| 撤销、删除、来源优先级更改缺少统一写入契约 | DG-01/03 显式设计并测试；本轮增量合并不推断删除 |
| 历史名称、ST/板块有效期不完备 | 本期按现行快照语义，PIT 能力另立需求 |

完整迁移必须保存全部公开必需字段、扩展来源字段和 ETF/指数消费者要求；窄表实验不作为 schema 定义。
