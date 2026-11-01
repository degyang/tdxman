# aspool 复权数据表：单一事件来源方案

[可落地数据库矩阵](aspool-database-production-capabilities.md) · [完整现状矩阵](aspool-database-capabilities.md)

目标原则：thsman 提供的全历史同花顺事件是股票复权事件唯一权威来源。保留来源行情与必要计算结果，淘汰重复事件及第二套股票因子输入路径。本文件整理目标职责；现有数据库和代码尚未执行退役。

## 保留表

| 数据库 | 表 | 来源 / 内容 | 用途 | 分层 | Action |
| --- | --- | --- | --- | --- | --- |
| `event_baseline.sqlite` | `events` | ageman/thsman 全历史同花顺分红、送股、配股等原始事件；原始 payload 完整保留 | 唯一股票复权事件库，供事件查询和本地因子计算 | 基础数据 | 导入基准；后续按来源新增/修订事件，生效后参与计算 |
| `stocks.sqlite` | `stock_qfq_bars` | 来源前复权 OHLC、source、source_as_of | 直接提供前复权行情；在当天基准一致时供前一有效日 close 读取 | 基础数据 | 来源行情采集/修订；新事件可能影响此前价格，不能只追加当天 |
| `adjustments.sqlite` | `stock_adjustment_factors` | 权威事件＋未复权行情计算出的因子及覆盖区间 | 支撑既有复权投影 API，包括后复权/指定基准，以及依赖因子的特征 | Enriched 数据 | 基准重建；基础输入变化后重算受影响区间 |
| `adjustments.sqlite` | `etf_adjustment_factors` | 已审核的 ETF 来源累计因子 | 支撑既有 ETF 复权读取；当前股票事件基准未证明覆盖 ETF | 基础数据 | 显式来源维护，当前没有在线日更因子源 |
| `event_baseline.sqlite` | `metadata` | 来源文件、SHA-256、范围、单位、factor/enriched 重建状态 | 管理原始事件基准身份及输入版本 | 管理数据 | 输入基准版本或重建状态变化时更新 |
| `adjustments.sqlite` | `adjustment_state` | 因子版本及最新日期 | 管理本地计算结果版本，校验特征发布的一致性 | 管理数据 | 因子结果真实变化或覆盖推进时更新 |

合计 **4 张业务数据表、2 张管理表**。未复权股票/ETF daily_bars 是计算依赖，属于行情主表，不在复权专用表中重复列入。

stock_qfq_bars 保存来源行情；stock_adjustment_factors 保存计算结果，两者分别支撑直接行情读取与复权投影。当前保留计算因子是为了保持已有后复权/指定基准及依赖特征能力，不能只因有前复权 OHLC 就默认这些 API 已不再需要因子。

metadata 管理来源输入版本；adjustment_state 管理计算输出版本。两者不同，来源更新但计算尚未完成时必须能识别。

## 退役职责

| 现有表 / 字段 | 目标处理 | 切换要求 |
| --- | --- | --- |
| `stocks.sqlite.corporate_actions` | 退出目标生产表集合；不再长期保存 TDX 第二套复权事件 | 日常采集、事件查询和增量副本接入唯一事件库；TDX 若用于核验仅生成报告。TDX 独有股本变动若仍有独立产品需求，应单独定义职责，不能假称同花顺复权事件已覆盖 |
| `adjustments.sqlite.stock_factor_anchors` | 退出目标生产表集合；基准事件链不再依赖第二套外部锚点 | 原始行情＋权威事件确定可计算区间；保留输入不完整的质量边界。所有读取/计算对旧锚点的 fallback 需移除 |
| `source_pre_close`、`pre_close`、`pre_close_source` | 按前述讨论，不作为新方案必需的持久化字段 | 日级参考价使用截至计算当天的同基准前复权行情，取前一有效日 close；当前字段尚未删除。因子/历史指标计算入口另按其价格尺度接入 |

## 必须完成的入口切换

1. **来源维护**：当前 event_baseline.install 是全量基准安装，相同文件幂等，不同 SHA-256 会拒绝。必须补明确的版本修订/更新入口；不将现有 install 冒充已经支持每日事件增量。
2. **日常流程**：update/sync 与旧 factors-bootstrap 仍有 TDX 事件和锚点路径；改接权威库后才可退役物理表。source_qfq 日更亦需落实采集入口，目前主要是审计维护/副本导入路径。
3. **查询能力**：corporate-actions 逻辑查询名可以保留，但数据改读 event_baseline.events；旧 TDX 类别字段与新事件 payload 的契约必须明确，不制造不存在的类别信息。
4. **副本和发布**：传递权威事件及其输入版本，接收端重算并发布因子/特征；复制计划、恢复门禁和管理版本同步更新。
5. **验证**：检查分红、送转、配股、同日重复/冲突、停牌期间事件与未来生效边界；验证前复权读取、既有复权 API、特征一致性及相同输入重放。

当前事件原始结构、来源优先级及基准因子工具见 [全历史复权事件基准](../../ops/event_baseline.md)、[事件实现](../../../src/aspool/event_baseline.py)及[基准因子实现](../../../src/aspool/baseline_factors.py)。本次仅整理职责和切换条件，没有删除或重算生产数据。
