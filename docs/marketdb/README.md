# MarketDB 设计文档

当前架构统一为 **raw → staging → core → factor**。外部 collector 生产 Parquet、CSV、JSON 等文件，MarketDB 从 raw 导入开始处理，不直接调用 collector、行情 CLI 或远程 API。

| 文档 | 内容 |
| --- | --- |
| [AxData 对照与补充决策](axdata-comparison.md) | 本机 Services/AxData 的借鉴项、保留差异与核对依据 |
| [AxData raw → core 状态管理调查](axdata-state-management.md) | 证券业务状态、任务质量状态、实际取数与落盘链路，以及尚未闭合的能力 |
| [能力模型与角色边界](capability-model.md) | Provider、Collector、Dataset、查询引擎、SDK/API 与 Apps；Fundwise 接入定位 |
| [DuckDB＋Parquet 存储与发布架构](storage-and-publication.md) | 物理承载、文件版本、事务边界、消费者读取与横截面计算 |
| [四层逻辑架构](logical-architecture.md) | 系统边界、raw 文件合同、四层职责、core／factor 数据域与发布 |
| [生命周期跟踪矩阵](lifecycle-tracking-matrix.md) | 各数据域从 raw 到 factor 的归属、修订、保留与待办 |
| [每日周期与来源能力矩阵](daily-cycle-capabilities.md) | 外部生产 CLI/API、文件维度、导入与日周期、统计范围 |
| [事件、状态与业务流程范式](event-state-workflow-pattern.md) | 四层共用模板及 ST、上市／退市、复权的专属处理流程 |

原基础库的原始依据归 raw，标准事实与原状态库归 core；原 Enriched 归 factor。Snapshot 不再独立成层，只表示 core 状态或集合的某个版本。staging 承担清洗、代码映射和单位转换。

本目录所有文档均已按这一口径更新。物理承载方向采用 DuckDB＋Parquet：核心行情与高频派生数据优先入 DuckDB，事件、目录、日历与日期状态采用版本化 Parquet。字段字典、DDL、文件布局及导入命令仍待设计；尚未执行数据迁移和清理。
