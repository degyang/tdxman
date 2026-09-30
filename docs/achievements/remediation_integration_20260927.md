# DG-00～DG-02 本轮集成与下一工作包

日期：2026-09-27。协调者已接受四个实际 Orca 任务的完成结果；首批 `981ab5f`、DG-01 `5b42c52`、DG-02 `3bfc857` 依次 fast-forward 进入 main。生产池、既有恢复快照、Fundwise 均未修改；本轮没有生产补数、全池迁移、普遍分区或删除数据。

## 交付与验收

| 工作包 | 最终代码与证据 | 已验收结果 |
|---|---|---|
| DG-00 / DG-01 首批审查 | `f949dbc` / `30870a5` / `981ab5f`；[审查](remediation_first_batch_review.md) | 原四项基线失败逐项结案；556 passed、4 skipped、18 subtests |
| 完整 DG-01 实现与独立恢复修复 | `c3c45f9` / `9db1718` / `bbb50b0` / `5b42c52`；[实施](dg01_implementation.md)、[独立审查](dg01_review.md) | 真实变更/no-op、旁路、持久恢复、内部修订/覆盖日志与成本；最终 640 passed、4 skipped、18 subtests |
| DG-02 统一旧后端访问 | `6c85113` / `3bfc857`；[实施与入口清单](dg02_implementation.md) | 股票/research/ETF/status/事件金额/维护兼容，跨年预热、冷年解码禁令；666 passed、2 skipped、18 subtests |

DG-01 的 4 个跳过是 2 项未启用联网集成测试及 2 项未配置外部 TickStockPanel host 测试；DG-02 执行完整 unit 范围，2 个跳过均为后者。两次套件范围不同，不将跳过数量变化称为测试覆盖增加。

DG-02 五年 P0 使用现有恢复快照，2021-09-24～2026-09-24：1,213 个发布会话、261 个检查窗口、258 个输出批次，82,772 条事件全部精确比对，30 个金额样本独立核对一致。最大批 1,004 行，峰值进程 RSS **298,508,288 字节（284.68 MiB）≤ 2 GiB**，71.178 秒，无 swap/spill，结束 fd/线程数回到开始值。见 [完整基准](evidence/data-remediation/20260927-dg02/p0-five-year.json) 与 [命令/源码/限制](evidence/data-remediation/20260927-dg02/validation.json)。这不是全部金额审计、冷缓存/P95 或增长验收。

公开接口签名与调用方式保持，[P0 接口签名和样例](up_regime_bounded_read_delivery.md) 继续适用；新 `DailyStorage` 属内部边界。旧式文件仍可整文件读写；分年 footer、缺统计日期列回退和特殊 ETF schema 探测成本明确保留，尚未选定新后端。公开逻辑修订/Fundwise 缓存与依赖收敛仍属 DG-05/06。

协调者通过源码/差异审阅、命令/日志/提交对应关系接受证据：`bbb50b0` 后的 DG-01 交付、`6c85113` 后的 DG-02 交付没有源码/测试/脚本变化。没有重复运行 agent 已通过的测试、lint、基准或恢复演练；最后仅更新集成文档和 Git/Orca 状态。后续仅新相关修改、真实失败或明确证据缺口触发必要验证。

## 实际 Orca 协作与工作区去向

Run：`run_9d4b546218b4`。同一工作区 `/home/ubuntu/orca/workspaces/tdxman/aspool-dg00-contract` 顺序复用，独立 `.venv`、显式实验根；协调者负责主目录、审查和合并，没有同时派两位实施者修改存储模块。

| 实际任务 | Dispatch | 已核验模型 / effort | 结果与终端处置 |
|---|---|---|---|
| `task_e156082ba705` 首批审查修复 | `ctx_3b811a14e5d6` | gpt-6-astra / high | succeeded；release 返回 user_takeover，保留 |
| `task_c544312049ac` DG-01 实施 | `ctx_85e33bd51354` | gpt-6-sol / high | succeeded；release 返回 user_takeover，保留 |
| `task_e72b8369e2bb` 独立恢复审查修复 | `ctx_a6a121f168bf` | gpt-6-astra / high | succeeded；release 返回 user_takeover，保留 |
| `task_813d030661c0` DG-02 实施 | `ctx_fef135ce8756` | gpt-6-sol / high | succeeded；release 已归档 transcript 并关闭 agent 终端 |

[回收状态](evidence/data-remediation/20260927-integration/orca-lifecycle.json) 只保存允许的任务/资源字段。四项均已由 worker_done 正式结算，没有待执行或未结算 dispatch。Orca 工作区本轮交付完成后标 completed，分支全部成果已进入 main；它不再承载尚未合并的交付。

物理工作区暂留：Orca 对前三个会话返回 user_owned / user_takeover，回收没有获得关闭权限，不能借 worktree rm/force 绕过终端保护。这是运行时所有权回执，不推断用户实际发过新的任务。完成标记与物理删除分开；后续核实这些会话已不需保留后才移除目录。恢复点、实验数据和未结证据独立按引用退役，不随代码工作区删除。

## 下一包与决策门槛

1. **DG-03 完整候选实验。** 以已合并 DG-02 为代码基线、既有恢复快照为固定输入；先完成全部 schema、键、扩展字段、日期事实、股票/ETF 和指数隔离映射，再建独立 DuckDB 候选。比较近期全市场、单证券历史、事件金额、新增日、历史修订、无变化回放；覆盖 1×/2×/5× 历史、并发锁、事务恢复与磁盘总量，并评估 metadata index。交付 `architecture-decision.md` 和原始样本；不以当前 9 列微基准或文件数直接决定切换。复杂设计使用 gpt-6-astra/high，先有真实任务与 agent，再创建/复用工作区。
2. **DG-04 数据可用性路径。** 可以先只读重新核实覆盖、字段缺口、来源冲突及实际 stale；历史 605 日和 522 条候选不是当前执行范围。形成逐项补入/否决/延期清单和分批重算计划，生产修改前核实恢复点是否覆盖最新输入，并确定唯一维护写入者。该路径不等待新后端，但候选实验不直接改变生产事实。
3. **DG-05/06 → DG-07 → DG-08。** 在可靠变化和统一入口上建立字段依赖、递归收敛及公开修订，随后做 Fundwise 配套、同输入影子与故障切换/回退；最后按恢复链、当前发布和未结引用决定退役。生产恢复目标未知项未被工程测试代替，不提前清 stale、切换或删除旧证据。

本轮关闭的是 DG-00 工程基线及 DG-01/DG-02 工程工作包，不是全部数据整治计划。后续状态仍以 [推进方案](remediation_execution_plan.md) 为唯一看板。
