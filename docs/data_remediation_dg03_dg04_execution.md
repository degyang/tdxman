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

DG-04 已验收并合并推送 main（`936b566`），DG-03 正在完成增长、更新与恢复试验。不会将候选生成、stale 清零或单项查询提速单独等同于 DG-03/DG-04 完成。最终记录需要提交对应关系、完整负载原始样本、逐案数据处置、实际发布与消费结果，以及仍未独立证明的字段。

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

### 完整候选读取与第二批修复审查

DG-03 首次完整核验保存 40 个原始字段、17,862,387 行及 18 张原 catalog 表（以 `parity.log` 的逐表集合为准）；实际候选五年事件迭代器读取 82,772 行、258 批，最大批次 1,004 行、无缺失金额，抽核原 Parquet 33 个金额。峰值 RSS 222,425,088 字节（212.12 MiB），29.107 秒；完成/关闭和临时目录清理通过。对应 `5cd370d` 及实验根 `events.log`，协调者读取结果后未重跑。该时段存在其他隔离维护/来源核验，耗时不解读为无竞争 SLA；实际进程 RSS 和有界读取结果仍为有效观测。

初版单证券组合过滤扫描 17,862,387 行，60 日读取约 1.76 秒花在 Pandas 字段覆盖。候选新增 SQL 覆盖与证券索引有界临时关系，试验月内按证券排序；相关 10 项小测，以及三个 60 日全 35 字段窗口、单证券、ETF、指数的字段/元数据精确比对已接受。60 日读取观测约 0.74 秒，增长、连续维护与完整进程恢复尚待实际执行。旧版本的完整等值仅支持旧源码，新路径使用本次有影响的验证，不能混淆源码对应关系。

DG-04 对新取得的独立资料形成 2,506 条可选字段/日期事实补数、245 个证券的精确计划 `107fcabe21d55519917e6987e1483b4118dfc2ab1201c7f351a0a47003187bff`。只读独立核对 245 份证据、所有 2,506 条记录及 519,796 条既有行，有效 OHLCV/前收价/ST/换手率/交易状态未覆盖；45 条既有事实无有效冲突。10 条冲突延期、3,695 条来源未确认不写入。恢复在不可变 DG-00 基础上叠加 77 个本轮已变对象、942,644,782 字节，经共享锁复制及哈希核对；没有为交接再复制旧全池。

独立审查任务 `task_d77082c8d20c` / `ctx_87bd2259dcf0` 于 `2026-09-27T10:52:26Z` 批准仅上述补数计划，见 [只读审查结果](evidence/data-remediation/20260927-dg04-gate-review/supplement-review-result.json)。协调者按审查的每批最多 8 个证券签发[补数批准](evidence/data-remediation/20260927-dg04-gate-review/coordinator-base-gap-approval.json)，指定唯一生产 writer，并在 DG-03 正面确认无重型进程后放行。该批准不包含重发布，实际执行后清单和最终输入仍需核实。

重发布 guard 源码冻结 `9308d55`；新增测试证据冻结 `13d627c`（仅测试/文档）。14 项相关验证通过后只新增运行 2 项非空初始状态用例（2.99 秒）：初始高度 2 经分批得到 3/4/5；两批之间把原前序改成 99 会在新输出/stale/checkpoint 写入前拒绝。原 14 项没有重复运行。最终重发布仍等待实际补数后输入匹配及独立结论。

### 第二批实际执行与最终发布放行

第二批于 `2026-09-27T11:00:02.631901Z`～`11:02:56.595289Z` 按每批最多 8 证券、31 批完成：245 证券全部完成、pending=NULL，2,726 行与 2,461 facts 变化；完整清单等于 `ff7cff8b970e2b9290dd2bb855211e9763552f831c28a52b5e065c1b55a00587`，实际计算输入等于 `ddd0c37c09a299c8addaf9e4f09ba6928178079f1ced11f960b463018ab1e44b`。见[生产补数回执](evidence/data-remediation/20260927-dg04-gate-review/production-base-gap-validation.json)。

复用首次审计并按实际变更核对差异后：日线 6,230,110 行、9,670 条缺行情（483 条停牌、9,187 条未确认）、3,693 条 ST 未知、39 条前收价/涨跌幅未知、11 条换手率缺失。非法 OHLC 与上市依据未知没有被擅自修正。上述统计不代替历史证券总体完整性证明。

独立复审读取实际权威 ledger、完整初始前序及最新 812 个恢复增量对象（997,684,831 字节）后，明确批准最终计划 `36794742ac1e3c4a373c52773a77ec40449c9de2e676062b4c926f11fed061c5`。计划绑定同一实际输入、4,372 项前序签名和本轮源码，见[复审定稿](evidence/data-remediation/20260927-dg04-gate-review/tdxman-dg04-gate-followup-review.md)与[实际绑定核对](evidence/data-remediation/20260927-dg04-gate-review/actual-publication-gate-result.json)。复审任务成功结案并已 release，归档 transcript。

协调者签发[最终发布批准](evidence/data-remediation/20260927-dg04-gate-review/coordinator-publication-approval.json)，放行 1,213 日（2021-09-24～2026-09-24），每批最多 256 日，共 5 批顺序执行。运行源码为 `9308d55`，`13d627c` 新增测试、`4bb7484` 仅补数文档证据，不改变运行实现。首批实际 `11:08:07.483057Z`～`11:10:09.618288Z` 完成 256 日，122.14 秒、峰值 RSS 642.77 MiB，无错误；后续发布及实际消费尚待全部完成后验收，不以首批成功宣告任务完成。


### DG-04 最终验收与集成

Agent 最终提交 `75442bdea5670646f7f197c7ee5b21714c4cd39c`，成功结案消息 `msg_408ca0376e20`。协调者读取并接受[实施报告](data_remediation_dg04_implementation.md)、[执行源码与验证绑定](evidence/data-remediation/20260927-dg04/final-validation-linkage.json)、[实际发布回执](evidence/data-remediation/20260927-dg04/production-publication-execution.json)和[生产核验摘要](evidence/data-remediation/20260927-dg04/production-validation-summary.json)。五批共 1,213 日全部完成、pending=NULL，目标及全池 stale=0；发布进程累计 673.41 秒，峰值 RSS 706.17 MiB。实际产物包括 170,845 events、15,664 exceptions、1,019 gap states、115 IPO boundaries；四个真实证券的 224 events/7 exceptions/5 gaps/2 boundaries 与固定输入保守参考精确一致。目标内 derived references=0，健康前缀及非空专项证据另列，不虚称目标非空参考已验证。

[Fundwise 实际窗口](evidence/data-remediation/20260927-dg04/fundwise-window-validation.json)在未修改源码、隔离缓存上完成 22 个会话、354,337 投影行、1,400 涨停事件，金额无缺失，耗时 39.39 秒、RSS 1,425.96 MiB。22 日仍为 partial，阶段分类确实执行但因必要驱动未知返回 unknown；完整主线排名未运行。现存真实板块缓存超过 24 小时有效门槛，没有伪造时间或联网刷新；[管线边界证据](evidence/data-remediation/20260927-dg04/fundwise-pipeline-boundary.json)将新鲜日期成分快照及真实排名联调列为 DG-07 依赖。

合并提交 `936b566` 已推送 main 及 feature 分支。仅解决任务状态文档冲突，五个 Python 实现/测试文件与 agent 最终提交零差异；没有重复成功测试、旧 P0 或恢复演练。Agent 结案后立即请求 release；Orca 返回 `state=retained, reason=user_takeover, processAction=none`，保护已被用户接管的终端，不以强制关闭绕过该边界。工作区作为已合并分支与证据保留，不再是等待使用的任务空壳。


### DG-03 增长实测、修补与全局表否决

真实 2×/5× 原始历史分别为 35,724,774 / 89,311,935 行；全部原始字段、主键和证券索引保留。整份及按年追加在 SQL 1 GB 提交时失败，最终固定 128 证券集合、同事务进度、逐批闭连接续跑成功；最终续跑 498.63 秒、RSS 1,293,754,368 字节。初次整份失败仅日志及已提交 2× 前缀保留，后续按年失败另存完整部分库；不补造首次失败字节快照。

独立只读 Astra/high 审查 `task_1ef9487d36e2 / ctx_0ce55fe1c106` 发现联合崩溃断言与真实并发握手两项 P2 证明缺口，未发现已证实的 P0/P1 实现损坏。见[报告](evidence/data-remediation/20260927-dg03-review/review.md)。原 owner 在 `f170efa` 补齐 before/after COMMIT SIGKILL 的 raw/facts/coverage/stale/revision/audit 六域、原有 raw 不变、受控锁释放/争用探针、读读重叠及六次 writer 提交；新增局部测试通过，协调者只读核对，未重跑测试。审查任务成功结案并 release，transcript 已归档。

日期事实独立 1×/2×/5× 最初扫描量为 410,467 / 820,934 / 2,052,335，虽数值一致仍不满足冷历史裁剪。`9f39f64` 加实际返回日期范围，并保持全 NULL 字符串/状态的原类型；新受影响验证和真实 30 行参考价窗口严格等值。修复后扫描量统一 410,467、日期过滤输出 63,076，三个规模的纯 API 60 日读取中位 0.815 / 0.789 / 0.772 秒。该扫描量仍包含原基线粗粒度行组，不能称只扫描返回的 63,076 条事实；计时区分纯 API 与逐字段断言。

5× 日/60 自然日三轮已通过，中位 0.118 / 0.836 秒；单证券全历史在原 SQL 512 MB 宽列 ART 物化失败。`26f65d1` 改为窄索引定位实际十年段，再在同连接/读锁中按日期段物化完整字段，原预算下 31,590 行三轮通过，后两轮约 2.10 秒、进程 RSS 845,152,256 字节；大于 500,000 行请求保持 lazy 路径并实测 `DAILY_TOO_LARGE`。不持久化 rowid，不重跑成功 day/60 或复制整库，仅续做失败与未执行阶段。

1×/2× 全市场 7,266 行新增分别约 40.1 / 45.7 秒、写量 292 / 524 MB，no-op 没有物理写入，12 轮修订后的热点扫描均约 174,933 行。物理写量包含索引/检查点，不能从热点裁剪推导写量与冷历史完全无关。

5× 全市场新增仍在 SQL 1 GB COMMIT 失败；随后 ROLLBACK 因事务已终止而掩盖原始 pin OOM，原 owner 将修复错误保真并补失败后状态核对。禁止拆开 `apply` 改变整组原子性、去掉主键/索引或提高预算。此失败阻止当前单全局 DuckDB 表成为生产架构，不能标为全负载已验收。

协调者通过 `msg_ade55d57c6af` 回答原任务的 `msg_12b26250209c`：按任务卡进入完整 42 列月 Parquet 备选，原类型/键及 catalog 约束、同 35 字段读取、1×/2×/5× 冷月份、热点月更新/no-op、锁和恢复发布成本；1 线程、SQL 1 GB、RSS 3 GiB 守卫，预计额外 30～60 GB。若复制冷月份作压力，明确物理复制与逻辑日期映射，不将同日期重复伪装成长历史。只在隔离实验根运行；原 DuckDB 更多证券/恢复/bounds 也须收尾，已有原始逐值/P0/恢复成功证据不重复。最终选型及所有剩余门槛仍待真实备选结果。


### 月度备选独立审查与失败状态核实

`97fd0ef` 保留原始 COMMIT 错误，新增事务已自动终止的局部验证通过。实际失败的 5× 库与 prepared 输入对照：raw 仍为 89,311,935 行、新日期零行；facts/coverage/stale/revision/audit 差异均为零，没有半提交。完整原生 catalog 恢复已实测 before/after COMMIT SIGKILL、原生锁冲突、四种受控 flock 时序和六次顺序 writer 提交；4,881,295,773 字节副本恢复逐字节一致，12.11 秒。完整 42 列增长扫描也已完成；这些结果接受 agent 证据，不在协调层重跑。

月度草稿独立审查 `task_a2bb0a670f92 / ctx_6f47249c6704` 实际请求/生效为 Astra/high，启动 ready、turnStart observed。见[绑定源码哈希的只读报告](evidence/data-remediation/20260927-dg03-monthly-review/review.md)。审查未运行测试、连接数据池或改动 tracked 文件；发现跨月删除 coverage 丢失及事件金额仍绑定全局表两项 P1、ETF 错误包装及混合批次重写无变化月份两项 P2。全部交由原 owner 修复，新增对应局部证据及真实月度发布恢复验证待验收；完整 catalog 拷贝费用、绝对路径恢复与孤儿文件边界必须披露。

审查成功结案消息 `msg_e670ae5572a9` 已接受，随后立即 release：`state=released, processAction=closed_agent_terminal`，transcript captured。审查完成不等于实现验收，也没有批准生产切换。
