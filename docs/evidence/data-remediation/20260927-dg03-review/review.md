DG-03 存储与恢复独立只读审查，2026-09-27

结论：冻结实现未发现已证实的 P0/P1 数据损坏缺陷；现有恢复/并发 harness 有两项 P2 验证缺口，应由原 owner 修补后再签收对应验收门槛。它们是源码中可确定的测试盲点，不是“实际规模最终样本尚未完成”的重复发现，也不意味着已观察到 DuckDB 事务或池锁失效。审查已完成；候选整体验收仍由协调者综合后续增长、维护、恢复、日期事实压力结果判定。

审查 Task `task_1ef9487d36e2`，Dispatch `ctx_0ce55fe1c106`。实现所有者仍为 `task_43714f545a49` / `ctx_eaf6fc0a6fb8`。只读取源文件和现有小型日志，没有运行测试、启动数据库连接、重跑 benchmark、访问生产数据、修改 tracked 文件或执行 Git 写操作；本文件是唯一写入的报告。以下路径均相对 `/home/ubuntu/orca/workspaces/tdxman/aspool-dg03-storage`，行号对应冻结提交。

审查指纹：`c1a46fb05cd3f119eecfb36a4d59d6c61a10a8db`，审查开始与报告前 HEAD 一致，四个目标源码没有工作区 diff。

| 文件 | SHA-256 |
| --- | --- |
| src/aspool/dg03_candidate.py | 9abd93e04e859d3d22b5d520835ffcd5d0155dedfe85b108c46043f77e1b9f15 |
| scripts/dg03/bench.py | 47f2fc17c08265fa6d80efb4810ce6674308d995bacbe1ec64f7a4aca8ceeb51 |
| scripts/dg03/facts_growth.py | a4271a82f49a05855e4a8bdb982cbb7b0d67bf07ebabab5e97878d97de4938ca |
| tests/unit/test_dg03_candidate.py | a8b0aec237117e2a3f12d823753e780c344aeec5729a730ce23589c9ef9e4e65 |
| docs/architecture-decision.md（owner 未跟踪的审查时版本） | 2f1ba862f3dfcf8979b25d7b28307c8faf042d6f163e2a58cb01756dac51df6f |

**F1 / P2：崩溃恢复用例无法验证 raw、facts、coverage、stale 的联合提交。**

定位：`c1a46fb` 的 `scripts/dg03/bench.py:869-873`、`:930-943`，以及 `tests/unit/test_dg03_candidate.py:17-57`、`:131-143`。child 在 COMMIT 前后被杀是实进程故障注入，但变更仅是既有 raw 行的 amount+7。恢复后只断言 amount、revision、audit 行数；stale 只读 count 并输出，没有期望值断言；facts 和 coverage 没有任何改变或恢复断言。调用 recovery 的 unit fixture 原 catalog 只有 sentinel，甚至没有 facts、coverage、publication、staleness 表。

可复现推理：若实现遗漏 stale 写入，或把 facts/coverage 写入移至 COMMIT 后，这个恢复测试仍可能全部通过。amount 修订按 `dg03_candidate.py:448-451` 本来就跳过 coverage，因此仅在真实规模根上运行同一 harness 也不会补上覆盖范围变化的崩溃断言。`test_atomic_stale_and_coverage` 和 `test_dated_facts_atomic_with_raw_and_null_precedence` 能证明普通成功路径，但没有对同时改变这些状态的批次注入崩溃。

建议原 owner 增加小型联合 fixture：已发布且未 stale 的日期、已有 stale 日期、coverage、日期事实，并在同批执行一个确实改变 coverage 的 raw 插入或边界删除及一条 facts 变更。在 before/after COMMIT 两个既有故障点分别断言完整旧状态/完整新状态，包括 raw、facts、revision、audit、coverage 日期/计数与 stale 日期/原因；保留原成功日志。必要时只补这项定点验证，不重跑全量逐值对账。本项要求修复验证能力；没有指控当前 `apply` 真的把这些写入拆出了事务。

**F2 / P2：协调并发用例没有保证竞争重叠，串行执行也能被记录为并发通过。**

定位：`c1a46fb` 的 `scripts/dg03/bench.py:875-902`、`:957-985`。holder 取得锁并发出 READY 后固定 sleep 1.5 秒；父进程随后才启动会重新导入 DuckDB/Pandas/PyArrow 的 contender。协调读写和双读者场景只检查 contender exit code=0，未断言 holder 仍持锁时 contender 已开始尝试，也未断言锁获取/释放次序、两个 reader 的实际重叠、writer 的最终预期提交数；holder 的退出状态也未断言。

可复现推理：在调度/导入超过 1.5 秒的机器上，contender 打开数据库时 holder 已结束，协调场景照样成功并输出 `coordinated_writer_reader`、`concurrent_readers` 或 `hold_*_then_writer`。即使将共享 reader 锁错误改成排他锁，双 reader 场景只会串行成功，仍不会失败。native conflict 用例确实有拒绝断言（`:883-890`），但它也依赖 1.5 秒时间窗；晚启动会造成偶发失败，而且不能证明之后重新启动的几个协调场景都有竞争。

建议原 owner 用显式进程握手控制 holder 释放，确认 contender 到达获取锁之前的位置；按事件顺序验证 writer 释放后 reader/writer 才进入，reader 持锁时另一个 reader 可以进入。增加有界超时、所有 child 退出码及预期提交计数断言，不把某个固定耗时阈值当成唯一正确性证据。这是 harness 缺陷，未发现当前 `pool_lock` 实现违背其共享/独占锁契约。

两项发现已通过授权 CLI 提前发送给协调者，消息 `msg_6ad74733bd0e`；没有向实现 owner 直接派发修改或启动额外 agent。

**已审查且未发现阻断缺陷的实现路径。**

- `dg03_candidate.py:312-501`：写锁内单连接显式 BEGIN，raw/facts 分域去重；merge 忽略缺失输入，clear/delete 显式操作；实际变更统一产生日志并仅增加一次 revision；保守 stale 后缀和 coverage 写入在同一 COMMIT 前。异常在未成功提交时回滚。值修订跳过 coverage 历史扫描；普通插入/内部删除增量维护；边界删除和插入时缺失 coverage 的情况走证券范围重算。no-op 不改 revision 或 stale。这里的结论限定为已规范化输入的候选入口，不扩展为网络抓取/生产变更协议已完成。
- `dg03_candidate.py:764-791` 配合 `limit_amount.py:94-113,171-200`：金额来自同一个 candidate catalog；原 publication/stale token 外另加单调 raw/facts revision，批前、批后与完成前复查。各查询间允许 writer 进入，但 apply 导致的变化会使后续校验拒绝，要求调用者丢弃 staging 输出。`_versions` 两段锁不是一个快照，不过在此单调 revision 和前后检查模型下，本次未构造出混代且最终成功的路径；不要把静态推理写成并发故障注入已通过。
- `bench.py:530-593`：128 code 批次的数据插入与 `(copy,chunk)` 进度在同一事务内提交并闭连；已有 `(copy,source_year)` 前缀被排除，chunk 完成而 year 标记尚未完成的中断可在重启后跳过 chunk 再补 year 标记。末尾 coverage 刷新、总行数断言与 ready 标记只在构造完成后运行。未发现当前冻结代码在稳定固定输入下重复插入已提交 chunk 的路径。外部 JSON 初始化文件和完整复制过程不具备所有崩溃点自动恢复保证，现有测试也只声称提交前缀续跑，不据此扩大故障覆盖范围。
- `facts_growth.py:65-108,138-141`：只扩大 security_daily_facts 并显式标注合成来源，保留其他表；近期完整公开读与基准逐字段及 attrs 比较；记录本脚本独立 hash。该阶段的最终实测待 owner 完成，不能从脚本存在推断通过。它也不证明 derived/reference 表同步扩大后的性能，当前文档已说明该边界。

**复用的现有证据及适用范围。**

固定输入 manifest SHA-256 为 `123a53ecf9461d65ddcafe43bf73e3edb16278f9fc5ad1b84e014547b079a76b`。读取 `docs/evidence/data-remediation/20260927-dg03/schema-parity.json` 与 `parity.log`：7,288 文件、17,862,387 行全部原列等值，18 张原 catalog 表和原约束保持；日志 provenance 属于旧提交 `dc4c292a25d9f870503cf6588dd0cd8a583f8ecd`，不冒充当前整个实现的重新全量验收。

读取 lab `events.log`：旧提交 `5cd370daa7139d4362780577e9e203ce09c07df8` 的五年迭代消费通过，82,772 行、258 批、最大 1,004 行、33 个原 Parquet 金额抽样、全原始金额复用 parity，RSS 222,425,088 bytes，closed/completed/temp_removed 均为 true。已有性能和正确性事实保留，本审查没有重跑。

读取归档 unit 与定点日志：`unit.log` 9 passed；`tests-final-read-path.log` 11 passed；`tests-bounded-preparation-write.log` 3 passed/9 deselected；`tests-prefix-resume.log` 1 passed/11 deselected；`tests-securities-key.log` 1 passed/12 deselected。这些 pytest 文本本身没有完整源码 hash 绑定，因此只复用其已记录结果，不声称独立运行或覆盖本报告新增反例。提交前缀恢复测试在 `test_dg03_candidate.py:358-372` 明确是已提交 chunk 后抛 Python 异常，不是每个进程崩溃点的证明。

读取 lab `prepare-5x-security-resume.log` 的 provenance，与本次 candidate/bench 文件 hash 及冻结提交一致。审查时已见 copy=3 的多批 128 codes 成功，后续仍在执行；不将当前尚无最终 ready 样本判为缺陷，也不重复已知整份/按年 OOM。

**剩余候选验收门槛。**

原 owner 继续完成实际 5× 有界构造、1×/2×/5× 与证券扩大后的完整公开读及冷历史扫描界限、日常新增/修订/no-op/回补/删除和连续维护/检查点成本、修补后的恢复与进程锁证据、日期事实独立压力。协调者需将最终源码指纹、有效阶段完成标记、原始失败记录和最终架构选择关联；不得只依据样本文件存在或部分成功宣告完成。架构决策仍需保留迁移/回退成本及失败条件的实测边界。

本报告没有将已知单证券慢、无生产切换、固定基线与 DG04 实际输入不同、OS 热缓存限制、旧 5× OOM、已修复合成非日线范围问题列成新缺陷。没有要求重复已通过 DG00–02、全量原始逐值检查或五年消费。这里只要求针对 F1/F2 追加必要的小范围验证，其余 pending 工作仍属原 owner。

收尾前已读取协调者 follow-up `msg_e6df1758d29b`：协调者接受这两项证明缺口并已交原 owner 局部补齐，明确无需本 reviewer 等待修改或自行复现；后续修复版本和新证据由协调者按本报告检查项核验。
