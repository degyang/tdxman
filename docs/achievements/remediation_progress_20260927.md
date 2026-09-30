# 数据整治第一批实施记录

独立审查更新：本记录描述 `61559e1` 的首次交付状态；后续修复、四项失败结案及最终 556 passed / 4 skipped / 18 subtests passed 以 [第一批审查报告](remediation_first_batch_review.md) 为准，首批代码可合并，DG-01 仍部分完成。

日期：2026-09-27。工作区：`/home/ubuntu/orca/workspaces/tdxman/aspool-dg00-contract`；分支 `degyang/aspool-dg00-contract`，基点 `5083dbb`。由 Orca CLI 创建、当前会话顺序实施，没有启动新的 Codex worker 或多 agent，没有更改模型/effort。工作区独立 venv 的 DuckDB/PyArrow/Pandas 与原基线固定为 1.5.5/25.0.1/3.0.5。

## 已落地

DG-00 工程基线已补齐：[数据契约](../design/data-contract.md)、[消费者与入口清单](../design/consumer-inventory.md)、[恢复计划](../ops/recovery-plan.md)。原恢复点及当前生产 catalog + lake 共 13,742 文件逐项 SHA-256 一致；仍为 1,296 发布日、605 stale 日。核验脚本不复制全池，明确这是一次整改检查点而非每日扫描流程。业务 RTO、最新行情延迟容忍度、异机灾备目标仍未确定，不作为后续生产切换已获验收的依据。

DG-01 本轮落实幂等修复和日线观测部分：

- `enrichment._publish` 在发现实际变化后、替换文件前才标 stale；失效从对应文件中最早变化日开始。新增上市日期仍保守失效；已有生命周期不因重跑刷新。替换失败保留旧文件及 stale，清理 `.part` 文件。
- enrichment 重算计划来自实际变化、请求区间已有 stale 和未发布会话；真正无变化且发布齐全时不再固定前扩 120 日重算。仍保留递归后缀传播，尚未完成 DG-05 字段级依赖收敛。
- free-stockdb 和 vipdoc 离线日线接入 `merge_daily`，重复输入不重写文件/coverage；部分或空响应不删旧历史。返回“写入行数”改为真实变化行数。ETF 离线更新不使股票限价发布 stale。
- 日线合并可输出真实变化的证券、日期、字段、旧/新值、来源、原因及输入/输出**行内容指纹**。行指纹忽略 NULL 表示、字典顺序和整数/浮点存储差异，不是 DataPool 公开版本或发布代次。
- 在线日线及 enrichment 报告记录读入行、Python 物化行、重写行、文件数/字节、stale 标记次数与耗时。字段明细逐证券保存到 `<root>/reports/changes/<id>.json`，总报告仅保留引用，避免一次历史回补在总报告中累积全部字段差异。
- 修复 `_merge_rows` 已有的空字段扩散：输入变化只使已存在的依赖指标失效，不为没有相应指标的记录添加一批 NULL 及来源列。原基线相关两项语义单测失败，本轮通过；不修改限价业务规则。

`merge_daily` 的兼容扩展参数：

```python
from aspool.change_observation import empty_cost
from aspool.daily_storage import merge_daily
from aspool.pool import pool_lock

changes, cost = [], empty_cost()
with pool_lock(lab_root, write=True):
    changed = merge_daily(
        lab_root, "SZ", "000001", incoming_rows, "my-source",
        changes=changes, metrics=cost,
    )
```

这是内部维护入口，不替代 DataPool 的只读公共接口。`file_bytes_read_proxy` 是读取文件逻辑大小之和，`file_bytes_rewritten` 是生成文件大小；不是块设备实际 I/O。`stale_date_marks` 可以重复计数，不等于新增 stale 的唯一日期数；`elapsed_seconds` 不包含外部锁等待/网络；审查后 enrichment 单证券值包含观测报告保存，在线 merge 值不含报告保存和最终批量 coverage 提交。`coverage_repairs` 单列失败重试中的元数据修复。全局缓存失效、计算量、锁等待和事务级修订仍待补。

## 验证与证据

- [基线核验](../evidence/data-remediation/20260927-dg00/baseline-verification.json)：源与快照一致，没有生产写入、补数、迁移或清理。
- [真实样本隔离验证](../evidence/data-remediation/20260927-dg01/delta-probe.json)：8 只股票，各修订末日 amount 一元；校验其他全部物理字段不变、公开读取成功、来源文件哈希不变；回放同样输入为零变化/零重写。
- 实验反映旧后端的结构限制：8 行变化仍需读取和重写 44,881 行，约 5,610 倍行重写放大。无变化回放仍读 44,881 行。工程修复消除了无效写入，尚未消除历史读取和有效修订时的整文件重写。
- 测试结果和范围见 [validation.json](../evidence/data-remediation/20260927-dg01/validation.json)。新增覆盖字段差异、NULL/False/数值类型、无变化回放、失效起点、失败替换、空来源、离线入口、ETF 隔离、生命周期补入与已有指标失效。

全量 unit suite 的 4 个已有失败已在 `5083dbb` 复现：`test_stock_default_still_uses_existing_pipeline` 缺少当前默认补齐流程所需的日历；`test_main_board_st`、ST 单日样例子测试、`test_compute_price_limits_for_stocks` 的预期未指定旧规则日期。它们没有通过改业务口径规避，也没有作为本轮通过项。此次没有重跑五年 RSS 验收、网络补数或生产恢复演练。

## DG-01 尚未关闭的验收项

1. BaoStock 日历/生命周期/日期事实、quote 快照、指数 coverage、证券范围等写入入口需要统一变化清单与 no-op 审计；BaoStock 当前还有抓取前按计划失效、补数后按 jobs 重算的保守行为。
2. 行内容指纹和事后报告不能作为跨文件/数据库事务日志。进程在文件替换后、报告写入前崩溃仍依赖恢复基线。必须补入可识别的准备/完成/失败状态、输入输出逻辑修订及故障恢复证据，再宣称可重放变更链完整。
3. 现有离线/quote 路径虽复用幂等 merge，尚未全部持久化字段明细与成本；增量版本、锁等待、进程 RSS、计算/缓存失效和临时空间需按任务统一记录。
4. 分年更新仍可能重读全部分区刷新 coverage；跨年滚动依赖、全部消费者兼容应在 DG-02 统一入口验收。此次未普遍分区、引入候选库或切换后端。

下一包先补齐这些 DG-01 项，再按 [推进方案](../achievements/remediation_execution_plan.md) 进入 DG-02。`reports/changes` 中仍被未结任务引用的证据须保留；清理政策不能把它们当普通可再生统计报告一律删除。
