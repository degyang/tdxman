# DG-03 / DG-04 执行记录

2026-09-27；共同代码基线 `be60d78`。用户授权继续 DG-03 与 DG-04，沿用真实 Orca worker、effort 最高 high、复用已通过验证的协作要求。

## 任务与责任

Run：`run_e72d464ad133`。两个独立 worktree 解决并行代码修改冲突，每个都有实际已接收任务的 Codex 会话。

| 工作包 | Task / 当前 Dispatch | 工作区与模型 | 交付与集成路径 |
| --- | --- | --- | --- |
| DG-03 | `task_43714f545a49` / `ctx_eaf6fc0a6fb8` | `aspool-dg03-storage`；gpt-6-astra / high | 完整字段候选、增长/并发/恢复证据及 architecture-decision；验收后提交合入 main，不直接切换生产 |
| DG-04 | `task_9cdaed33aadf` / `ctx_6b4aa5a0b9c2` | `aspool-dg04-repair`；gpt-6-sol / high | 当前缺口及逐案裁决、可恢复分批修复与重新发布、Fundwise 实际窗口；核实当前输入和恢复门槛后由唯一维护写入者执行 |

实验根分别为 `/home/ubuntu/aspool-labs/dg03-20260927` 与 `/home/ubuntu/aspool-labs/dg04-20260927`。候选只接收确定来源及可重放变更；既有恢复快照保持不可变。重型候选实验和派生重算由协调者安排时段，避免并发资源竞争污染性能样本。

协调者负责核验提交和证据、生产门槛审查、必要的关键恢复独立审查、合并及推送。DG-00～02 已通过且源码未变的验证不重复运行；仅新修改、失败或明确缺证触发相关验证。

## Orca 启动异常及恢复

首次新工作区启动和一次既有工作区重试均在 `agent_readiness` 超时，任务没有运行；模型 requested/effective 一致，但后台标签的 `paneRuntimeId=-1`、`agentWait=null`，普通发送只能报告 provider unsupported。不得把这些回执写成成功派发，也不能靠继续创建工作区解决。

仅发送无修改 READY 探测后，终端能真实回复。通过 `terminal switch` 激活各自标签，两个 `terminal wait --for tui-idle` 均得到 `satisfied=true`；随后复用同一已核验模型会话执行原 Task 的 retry，得到 `state=ready`、`turnStart=observed`、provider codex supported。复用启动不重新指定模型，实际模型依据同一进程的初始 effective 回执与 TUI 核对。

首轮失败资源按回执 release；第二轮资源转由当前 Dispatch 复用，不能在活跃任务上回收。此现象只证明本轮激活标签解决状态注册缺口，不推断所有 Orca 后台启动均有同一问题。

## 验收状态

两任务已开始执行，尚未验收。不会将候选生成、stale 清零或单项查询提速单独等同于 DG-03/DG-04 完成。最终记录需要提交对应关系、完整负载原始样本、逐案数据处置、实际发布与消费结果，以及仍未独立证明的字段。

### 已接受的过程检查点

DG-04 于 `2026-09-27T10:04:35Z` 完成当前源核验：catalog/lake/change-state 与既有恢复 manifest 一致，没有 added/missing/changed；复用已完成的快照恢复证据。新查询得到 605 个 stale 发布日，范围 2024-04-01～2026-09-24。五年投影审计保留缺行情、未确认状态、独立 ST/前收价及换手率缺口；审计统计不被视为完整历史证券范围证明。

522 条候选中 465 条获新 BaoStock 交叉佐证、57 条仍未确认。价格及量额使用明确容差核验，不能声称精确一致；既有有效主来源冲突不覆盖，独立停牌事实另行裁决。当前可信补数的最早日期为 2022-02-11，保守重算后缀需由实际变更重新确定，不能固定为原 605 日。

隔离实验 2026-09-22～2026-09-24 三个会话：events 288 行、exceptions 15 行、scope 16,673 行及三条 summary 与原完整参考精确一致，耗时 270.527 秒，RSS 410.992 MiB。该窗口 references/gap/streak-boundaries 均为空；不能据此宣称非空参考价或连板边界场景已经验证。协调者已读取并接受结果，不重复运行；状态完整性修改及修复后的边界只补必要新验证。

上述检查点对应 DG-04 实验根中的 `current-audit.json`、逐案 dispositions 与 `lab-validation.json`；最终源码/测试对应关系及持久交付证据待任务收尾补入。生产定点修复已执行，详见以下检查点；候选性能实验与生产重发布继续按重型时段顺序安排。

### 生产定点修复与重发布门槛

独立 Astra/high 审查任务 `task_c34f15539dbd` 接受修复计划，否决旧重发布计划：初始前序连板状态未绑定，隔离复现证明前序从 1 改为 99 后仍会发布错误的 100。证据见 [审查报告](evidence/data-remediation/20260927-dg04-gate-review/review.md)。仅针对新发现的缺口补验证，没有重复既有测试。审查终端完成后已释放；后续新版本另做局部审查。

修复执行冻结于 `a410d735a26b77b72650a6b26930eaea86922080`，批准计划 `13414aedffc0ad72e3e33e6cc0f1faea8e9b4fe24e818461505932a364819641`。唯一生产 writer 按每批至多 8 个证券完成 4 批、30 个证券：465 条可信新日线、122 条已存在日线的缺失可选字段及 7 条停牌日期事实处置，连同有限派生传播实际改变 748 行和 594 条事实；87,240 条既有 OHLCV 保持精确不变。7 条停牌状态已有依据，不能记作 7 条新确认状态。

`2026-09-27T10:35:02Z` 回执确认完整修复后清单等于批准的 `eff91eab10e43c3b1df52c78320f51c2e26653e99159d9320daca41a6e937bdd`，无 pending。1124 个受影响交易日（2022-02-11～2026-09-24）仍为 stale，旧重发布计划从未执行。恢复快照及原调查报告保持不变。[执行结果](evidence/data-remediation/20260927-dg04-gate-review/production-repair-validation.json)与[批准范围](evidence/data-remediation/20260927-dg04-gate-review/coordinator-repair-approval.json)已保存。

重发布仅在完整前序状态签名校验、新增拒绝漂移测试及新冻结计划审查通过后执行；未获独立证明的 ST、缺行情与非法记录继续明确标注未知。修复后清单哈希与重发布输入哈希使用不同定义，不直接比较两者。
