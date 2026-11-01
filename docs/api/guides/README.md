# 能力矩阵

[API 文档总览](../README.md)

| 矩阵 | 用途 |
| --- | --- |
| [tdxman](tdxman-capabilities.md) | 在线、离线、CLI 与 Python 能力及限制 |
| [aspool](aspool-capabilities.md) | 本地采集、维护、查询与研究读取能力 |
| [aspool 数据库可落地矩阵](aspool-database-production-capabilities.md) | 当前生产功能所需的数据职责、来源与落地验收；不含旧布局/兼容数据 |
| [aspool 复权数据表](aspool-adjustment-tables.md) | 单一事件来源下的保留表、退役职责与入口切换 |
| [aspool 数据库逐表矩阵](aspool-database-capabilities.md) | 每个实际物理表的输入入口、来源接口、数据维度与用途 |

审理日期：2026-10-04。参考 ageman 的 `docs/guides/*-capabilities.md` 表格模式；本目录记录当前 tdxman 工作区实现，不继承 ageman 移植计划的 planned 状态。证据为当前源码、Click 注册与本地数据库只读结构检查。未执行在线采集或完整数据质量验收；“已实现”不等于上游当前可用，也不等于每条历史记录已验收。
