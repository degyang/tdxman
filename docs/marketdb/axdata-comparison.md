# Services/AxData 对照与 MarketDB 补充决策

核对日期：2026-10-05。参考本机 `Services/AxData` 的文档及局部源码，不是对其全部实现的验收。

[文档入口](README.md) · [能力模型](capability-model.md) · [状态管理专项调查](axdata-state-management.md)

## 1. 对照结论

| 主题 | AxData 证据 | MarketDB 采用／修正 |
| --- | --- | --- |
| Provider 与 Collector | 独立注册；Collector 可以独立存在，非必须通过 Provider 临时请求路由采集 | 修正能力图的强依赖表达；保留外部 Collector 位置，本期不引入插件框架 |
| 数据集声明与资产 | schema/queryable 等声明不等于已落盘 | 分开 Dataset 定义目录和已发布资产目录；文件存在也不等于 MarketDB 已验收发布 |
| Dataset 写入合同 | 明确 write_mode、主键、日期、范围、分区等 | 补充写入模式及前置条件，按版本发布实现，不能照搬就地覆盖文件 |
| 元数据与质量 | task/run/batch、输出、血缘、质量；源码已有必填和重复键检查 | 独立记录导入／计算任务、批次、发布及质量；检查结果成为发布门槛，不只作为日志 |
| SDK 本地与远程 | 本地 SDK 直读，HTTP 承载相同语义 | 保留统一 SDK 优先及双通道；受混合存储并发限制，本地直读仅针对安全只读环境／发布件 |
| 存储权威 | 全层 Parquet 为正式来源，DuckDB 可重建缓存 | **不照搬**：MarketDB DuckDB 保存正式行情和 factor，必须备份和恢复，不能当缓存删除 |
| 源端直取 | SDK call/request 可临时联网，默认不落盘 | **不引入本期 MarketDB**：仍只导入 raw 文件，查询不采集 |
| 标准化与口径 | 来源字段显式映射到稳定 schema | 采用显式映射；不同来源与价格口径不得按同名列静默合并 |
| schema 演进 | 单位、名称、语义变化需版本化 | 纳入 Dataset 兼容规则，不直接固定采用其字段名或 YYYYMMDD 字符串存储 |
| 删除操作 | 任务、运行记录、数据和缓存删除分开 | 采用分离原则；删除正式 DuckDB 数据不是清缓存，需独立数据删除流程 |
| 插件／实时／Web／云端 | 提供扩展、实时及多部署形态 | 保留未来可能性，本期不扩展范围；Fundwise 继续作为 App |

## 2. 纳入设计的验收点

- 只声明但未导入的 Dataset 不得显示为已有数据；只接受 raw、尚未发布 core 的批次不提供正式查询。
- 相同文件重复导入、相同业务键冲突、部分范围更新和全量替换有明确不同语义。
- 目录记录隔离导致不完整时，不能继续推断退市或摘帽。
- 字段改名、单位或复权口径变化，必须显式升级 schema／口径版本并验证消费兼容。
- 普通查询可获得范围、质量、版本和就绪说明；内部批次和文件明细通过诊断元数据访问，不污染每条行情业务行。
- DuckDB 正式数据与引用 Parquet 的恢复演练必须覆盖同一发布视图；保留 raw 不自动保证全部历史状态可恢复。
- Fundwise 本地／远程访问保持同一数据语义，且不依赖写入中的库文件或内部路径。

## 3. 本次核对依据

- [AxData 架构](../../../AxData/docs/architecture.md)：角色边界、独立 Collector、SDK 与存储方向。
- [数据分层](../../../AxData/docs/data-layers.md)：分层职责、写入模式与修订处理。
- [数据集与 DuckDB](../../../AxData/docs/dataset-and-duckdb.md)：声明和落盘资产区别、输出合同、缓存及删除语义。
- [API 设计](../../../AxData/docs/api-design.md)：读库与直取分离、本地与远程一致语义。
- [Collector 说明](../../../AxData/services/collector/README.md)：文件输入、目录范围及完整性检查示例。
- [质量检查源码](../../../AxData/libs/axdata_core/axdata_core/quality.py)：已核对必填列／值和重复主键检查。
- [查询源码](../../../AxData/libs/axdata_core/axdata_core/query.py)：字段投影、日期转换和 DuckDB 查询能力。
- [Collector 注册源码](../../../AxData/libs/axdata_core/axdata_core/collector_registry.py)：独立注册与 legacy Provider 兼容路径。

参考项目中的具体阈值、路径布局、字段名及默认覆盖策略不自动成为 MarketDB 合同。本次只更新 MarketDB 文档，未修改 AxData、数据或运行服务。
