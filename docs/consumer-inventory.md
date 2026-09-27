# DG-00：消费者、写入入口与锁清单

日期：2026-09-27。检查边界：本仓库 `src/aspool`、CLI/维护脚本，以及 Fundwise `fundwise/`（`1b25658`）。不声称已发现机器上所有外部脚本；切换前需复核部署入口。

## 已识别消费者

| 消费者 | 读取 / 契约 | 迁移检查 |
|---|---|---|
| Fundwise `data/aspool_backend.py` | 选股/环境日线、指数、限价四表；只读 | 字段、单位、符号转换、NULL、重复键错误保持 |
| Fundwise `regime/service.py`、`data/regime_events.py` | 截止日名称/ST、60 自然日预热、有界事件+amount | 日期边界、金额关联、批次一致和五年 RSS |
| Fundwise `regime/freshness.py` | 发布覆盖、读取器源码指纹、全池文件统计 | 目前耦合物理文件，DG-06 改公开修订后才能放弃兜底 |
| Fundwise `backtest/runner.py`、`strategy_recipe.py`、`watch/runner.py` | 多种基准指数 | 不局限于 Regime 的上证指数 |
| aspool DataPool / CLI | 股票、ETF、指数、证券事实、状态与限价 API | 契约测试覆盖，`status()` / ETF 目前有旧目录假设 |
| aspool 限价计算 / enrichment / BaoStock 规划 | 日线物理读取、日期事实、会话轴 | 存储重构必须覆盖内部消费者；仅改公开 API 不够 |
| `scripts/` 恢复/基准与既有维护脚本 | 指定池及内部路径 | 历史证据脚本保持原样；正式维护工具随新入口适配 |

## 写入入口与旁路

| 入口 | 数据对象 | 协调 / 已确认问题 |
|---|---|---|
| `tdx_online._publish_stock_rows`（经 `_sync_daily_run` 发布锁调用） | 股票/ETF 日线 → `merge_daily`、coverage | 在线获取在锁外，发布受协调；已有 no-op 文件跳过 |
| `tdx_online.update_daily_offline` | vipdoc 股票/ETF 日线 | `@writer`；基线绕过 `merge_daily`，重复写文件/coverage，分年兼容不完整 |
| `free_stockdb.import_daily` | 历史日线、coverage、sync_runs | `@writer`；基线直接 `_write_daily`，重跑重写，不能把来源空响应当删除 |
| `fundamentals._publish_quote_rows` | quote 日线 → `merge_daily`，快照 | `@writer`；快照 `_write` 排除刷新时间比较，后续日期重算仍需观测 |
| `baostock_source._publish_calendar/_publish` | 日历、生命周期、日期事实、补充日线 | `@writer`；日线走 merge，日期事实已有差异比较；生命周期/日历仍有无条件 upsert，失效与发布分离 |
| `enrichment._publish` | 日线可选字段、生命周期 | `@writer`；基线在差异计算前整段标 stale，是真实 no-op 缺陷 |
| `index_pool._publish_indices/save_index` | 指数文件及 coverage | 发布锁；文件有差异才写，coverage 基线仍每次更新时间 |
| `universe._publish_universe` | 当前证券范围和名称 | 发布锁/事务；范围变化会影响限价 scope，不能当纯缓存 |
| `fundamentals._publish_snapshots` | 当前基本面快照 | `@writer`；不是历史日期事实，不随日线迁移丢弃 |
| `limit_events.compute_limit_events`、各发布辅助函数 | 批次/指针/事件/参考/汇总/stale 等 | 写锁、单日数据库事务；递归后缀与会话轴当前偏全量，DG-05/06 处理 |
| `free_stockdb.import_adjustments/import_minutes`、`tdx_online.update_minutes` | 复权因子、分钟 | 本期不迁移；仍保留并隔离，不能随日线清理删除 |

`store.record_coverage(s)`、`daily_storage.merge_daily`、`_write_daily` 是内部辅助函数，不自行获得池锁；调用者必须持写锁。测试可以在独立临时池直接调用。`_write_daily` 还被既有测试/诊断使用；不能通过“入口已替换”宣称所有外部调用受管理。

## 一致性边界

`pool_lock` 使用池目录的共享/独占 flock；`@writer` 组合独占锁与 `catalog_session`。公开读取使用共享锁。网络获取通常位于锁外，但旧 free-stockdb 导入持锁包含获取，锁耗时需单独观测。

Parquet 临时文件替换只保证单文件原子性；coverage、stale、其他文件和数据库不是同一个事务。失效应在实际变化已确定后、替换文件之前执行；替换失败可以保守留下 stale，不能留下“已改变但仍有效”的发布。多证券失败可能部分完成，报告必须区分成功、失败和未执行。

所有写入试验必须使用显式独立 root。复用会话/创建 worktree 不会改变默认 `~/.aspool`。本阶段没有在生产池运行上述维护入口。
