# 日终涨跌停字段来源准备度

**日期**：2026-09-18
**审核依据**：`Fundwise/docs/northstar/reviews/UP-daily-limit-events-pm-review.md` 的“v4复审”及其“增量字段来源核查与最小补齐方案”指令。
**本轮状态**：来源核查轮记录完成；本文件保留其“未修改生产代码”的历史口径。后续最小实施包已另行完成并记录于 `daily_limit_source_quality_delivery.md`，相关成果随 checkpoint `a78fb49` 提交。

## 1. 核查边界与证据

本轮只读取现有代码、只读读取本地 `/home/ubuntu/.aspool` 数据池，并使用现有 mock/临时目录验证字段合并行为。未调用行情接口，也未写入本地生产池。

本地覆盖统计限定为 `universe` 中的 stock，读取 `lake/bars/daily/**/*.parquet`，取最近五个已有交易日期。`coverage.source` 是标的级来源/最新覆盖登记，不能单独证明每一行的字段来源；以下字段数量均直接按日线行统计。

## 2. 两字段来源与落库路径

| 字段 | 来源接口/源字段 | 转换与落库 | 有效日期与口径 | 当前判断 |
|---|---|---|---|---|
| `pre_close` | 在线报价：`MacClient.get_stock_quotes` → `PresetField.COMMON` → `FieldBit.PRE_CLOSE`；free-stockdb 日线原始记录也可带同名字段 | `src/aspool/fundamentals.py:_quote_bar` 原样取 `quote["pre_close"]`，经 `_enrich_daily` 后由 `merge_daily` 写入标的日线 parquet；free-stockdb 的 `_normalize` 保留原始字段并由 `import_daily` 写入 | 在线报价使用经过日期校验的 `server_update_date` 作为 `trade_date`，语义是该交易日的前一交易日收盘价，单位 CNY/share；free-stockdb 使用原始日线日期和源字段口径 | 报价写入链路能够保留该字段；K 线和本地 vipdoc 离线链路只有 OHLCV，不能补出该字段。当前不会用上一根 `close` 代替，也不会从涨跌幅反推精确参考价 |
| `is_st` | 当前在线报价没有已接入的 `is_st` 转换。协议请求包含 `FieldBit.STOCK_TAG_FLAGS`，但现有 aspool 转换没有把它映射为 `is_st`；free-stockdb 日线原始记录可直接带 `is_st` | free-stockdb 的 `_normalize` 保留原始布尔值，`_write_daily`/`import_daily` 持久化；在线报价、在线 K 线、vipdoc 离线日线均未生成该字段 | free-stockdb 的值随原始日线日期保存，只能视为该来源在该日期的历史标记；当前名称、当前快照或其他标签不被当作历史 ST。尚无经确认的历史生效区间/公告依据 | 历史 free-stockdb 子集有值；当前在线增量五日全部未知。不能把缺失转换成 `False` |

关键代码位置：

- 在线报价与日期校验：`src/aspool/fundamentals.py:182-206, 250-284`；写入：`src/aspool/fundamentals.py:319-353`。
- 在线 K 线只取 bar 的日期、OHLCV：`src/aspool/tdx_online.py:82-91`。
- vipdoc 离线日线构造的原始行只有 OHLCV：`src/aspool/tdx_online.py:449-508`。
- free-stockdb 原始字段保留及写入：`src/aspool/free_stockdb.py:113-195`。
- 协议字段定义：`src/tdxman/codec/bitmap.py:34-39, 68, 293-328`；数据契约：`src/aspool/api_contract.py:31-45`。

### 合并语义

`src/aspool/tdx_online.py:_merge_rows` 对 incoming 中的 `None` 不覆盖已有值，`src/aspool/daily_storage.py:26-90` 负责校验 OHLCV 后落盘。因此：

- 已有可靠 `pre_close`/`is_st` 不会被“源缺值”的增量行意外抹掉；
- 新日期如果来源没有字段，仍然落为未知；
- 现有合并逻辑没有把缺失字段补齐，也没有提供字段级来源追踪。

事件计算同样只接受当日有效 `pre_close`，不跨日替代：`src/aspool/limit_events.py:389-395, 443-459`。

## 3. 最近五个本地会话覆盖

统计母体为每个日期的 stock 日线行；当前每行对应一个 symbol，故本表的 rows 与 symbols 相同。`pre_close` 有效定义为非空、有限且大于 0；`is_st` 有效定义为非空布尔值。

| 交易日 | 母体 rows / symbols | `pre_close` 有效 | `pre_close` 未知（缺失 / 非法） | `is_st` 有效 | `is_st` 未知（缺失 / 非法） | 两字段联合有效 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-14 | 5,548 / 5,548 | 0 | 5,548 / 0 | 0 | 5,548 / 0 | 0 |
| 2026-09-15 | 5,546 / 5,546 | 0 | 5,546 / 0 | 0 | 5,546 / 0 | 0 |
| 2026-09-16 | 5,562 / 5,562 | 5,184 | 378 / 0 | 0 | 5,562 / 0 | 0 |
| 2026-09-17 | 5,563 / 5,563 | 5,492 | 71 / 0 | 0 | 5,563 / 0 | 0 |
| 2026-09-18 | 5,563 / 5,563 | 5,563 | 0 / 0 | 0 | 5,563 / 0 | 0 |

本表没有发现非空但非法的 `pre_close` 或非空非布尔的 `is_st`；未知主要是源字段缺失。五日合计 27,782 行，`pre_close` 有效 16,239 行，`is_st` 与联合有效均为 0。

作为历史来源对照，本地 `coverage` 中标记为 free-stockdb 的 16 个标的共 66,253 行，日期范围为 2000-01-04 至 2026-07-29；这部分逐行同时具备有效 `pre_close` 和非空 `is_st`。该结果只证明已有 free-stockdb 子集的字段保存能力，不代表全市场或当前在线增量已覆盖。

## 4. Mock 验证

本轮用临时目录构造三类输入，未触碰生产池：

| 场景 | 结果 |
|---|---|
| 源行同时提供 `pre_close=10.0`、`is_st=False` | `_normalize` 后两值均保留 |
| K 线源行不提供两字段 | `_daily_rows` 不制造字段，结果保持缺失 |
| 已落盘可靠字段，后续同日增量不提供两字段 | `_merge_rows`/`merge_daily` 保留 `10.0` 与 `False` |

既有回归：`python -m pytest tests/unit/test_aspool.py tests/unit/test_aspool_performance.py -q`，结果为 **22 passed，6 subtests passed**。本轮未因来源核查修改生产代码，因此没有新增生产实现可回归。

## 5. 精确缺口与最小补齐方案

### 5.1 不需要新来源即可做

1. 保持当前安全口径：源有值才落库，源缺值保持 `NULL/UNKNOWN`；不从名称推断 ST，不用上一根收盘价替代 `pre_close`。
2. 下一实现包可复用现有 free-stockdb 导入链路，把其已有字段继续保留，并在同步报告中增加字段级的母体、有效、未知和联合有效计数。
3. 把字段来源/质量计数纳入日终报告或 coverage 扩展，但不改变现有事件计算的 UNKNOWN 语义。

验收条件：mock 中“源有值保留、源缺值未知、增量不抹除已有可靠值”继续通过；每次同步能按字段报告覆盖率；不出现跨日替代、名称推断或反推价格。

### 5.2 需要行情联网验证

1. 对在线 `PRE_CLOSE` 做真实会话验证：确认报价日期、除权除息日价格语义、非交易日/断线时的拒绝行为，并复核五日覆盖。
2. 若计划使用 `STOCK_TAG_FLAGS`，需要用真实样本确认协议位与 ST 语义，再单独实现并测试映射；目前不能仅因该字段被请求就认为 `is_st` 已可用。

验收条件：获得明确的源字段定义和日期口径；真实样本的有效/未知计数可复现；`pre_close` 与权威行情参考价一致；协议标签映射有正反例，未知不被写成 `False`。本轮未执行这些联网验证。

### 5.3 需要新的历史身份依据

1. 要覆盖当前在线增量及 free-stockdb 未覆盖的历史，需提供按标的、按日期或生效区间的历史 ST 身份来源，例如带生效日期的权威公告/状态数据或经审计的历史身份数据集。
2. 对缺少历史身份依据的日期继续保留 `UNKNOWN`；不把当前名称、当前 ST 状态回填到旧日期。
3. 若要求全市场历史 `pre_close` 完整，也需要补齐 free-stockdb 未覆盖区间的历史行情来源；不能用上一根 `close` 无条件代替。

验收条件：每个字段都有源日期/生效区间、覆盖统计和缺失清单；跨复权/除权日期通过独立样本核验；历史身份与价格均能回溯到明确来源。该类工作不属于本包，也不在本轮回填。

## 6. 与 v4 事件交付的边界

本文件只报告来源准备度，不改变 `docs/daily_limit_events_delivery_v4.md` 已确认的限制：旧时期整体 `UNKNOWN` 仍保留；主板全面注册制前的五日窗口仍无依据即 `UNKNOWN`；生产 `NO_LIMIT` 仍受上市日期字段和现有上市日龄限制约束。字段准备度提升不等于全市场历史规则、实时覆盖或策略有效性通过。

**下一步**：由 PM 基于本文件决定最小实施包；本轮不另造 v5 补正文档。
