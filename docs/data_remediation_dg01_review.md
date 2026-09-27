# DG-01 独立恢复审查

日期：2026-09-27。**DG-01 feature 实现与独立隔离验收通过，无已发现未修复的本期要求；main 集成待协调者执行。** 审查基线 `981ab5f`，完整审阅实施 `c3c45f9` 及其记录 `9db1718` 的源码、测试、脚本、入口清单和证据；修复提交 `bbb50b0`。最终完整离线 **640 passed、4 skipped、18 subtests passed，177.80 秒**，详见 [验证摘要](evidence/data-remediation/20260927-dg01-review/validation.json)。

派发 `task_e72b8369e2bb / ctx_a6a121f168bf`，角色为独立关键恢复审查。Orca requested/effective 均核验为 `gpt-6-astra / high`，只保存允许字段；未升级 effort、未启动子 agent。实施派发已完成后独占 feature 修改，无 main 编辑、push、市场抓取、生产池或恢复快照写入。所有测试使用本 worktree `.venv` 和 `/home/ubuntu/aspool-labs/20260927-dg01-review/task_e72b8369e2bb/` 下的独立根。

## 发现与修复

| 问题 | 原实现的可观察后果 | 修复与回归 |
|---|---|---|
| 恢复版本前置条件检查晚于替换；applied 分支仅检查操作存在 | 版本冲突仍先覆盖目标；不一致的 catalog revision/日志可被当作已恢复 | 替换之前验证输入或输出 revision，核对 applied 全部身份、修订、来源、计数与证据引用；冲突保持目标 bytes/mtime 不变和 pending |
| manifest 无完整性校验 | 修改 `stale_start` 为 NULL 后仍可前滚并漏标 stale；已提交分支也接受损坏 manifest | 每次原子持久化写 manifest checksum，恢复先校验，再核对目录 operation ID 与证据位置；损坏、缺失所需对象保持读阻断 |
| 首次建池绕过持久 mkdir | `pool_lock` / `initialize` / `catalog` 提前创建的 root、lake、bars 等父目录未 fsync；后续目标 mkdir 无法识别这些新目录 | 三个创建入口使用相同持久 mkdir；测试逐个验证新目录的父目录都被 fsync |
| enrichment 在源错误或空响应后仍发布 `source=None` | 已知资本字段被清空并自动重算，来源失败变成业务变化 | 完整命令跳过该证券并记录来源失败；存在任何来源失败时禁止自动派生；异常和空响应均保持文件、mtime、revision、coverage、stale 不变 |
| enrichment 重试先读后恢复 | 自己留下 pending 后，下一次完整命令在规划读即报错，无法到达 writer 恢复 | 规划前在独占锁内前滚；重试源仍失败时保持已恢复原始变化的 stale，不发布新派生 |
| 观测时钟混用 | DuckDB 非 UTC session 写入本地 naive 时间，BaoStock 按 UTC 计算 TTL；universe 又用本地 now 对 UTC 观测比较 | 观测统一 UTC；universe 观测按 UTC 计算，旧 `last_seen` 回退保持本地语义；Asia/Shanghai 与七日边界测试 |
| universe 以整批新 asset_type 决定失效 | 股票转 ETF 漏失效；混合批仅 ETF 真变化也使股票 stale | 对实际变化行的旧/新范围决定保守失效；健康股票 no-op 不参与 ETF 变化的失效判断 |
| 指数来源失败及完整性读取成本记录不足 | `sync_indices` 的 failed 列表未被任务摘要识别，状态仍 completed；SHA 读取未单列 | 识别 failed/source_failures 为 partial_failure，加入 integrity_read 文件/逻辑字节代理，保留业务提交计数 |

第一组 7 个场景在旧实现上全部失败；第二组时区、源失败与成本的 5 个场景全部失败；完整 enrichment 恢复重试另有 1 个失败。新增独立回归共 19 个，修复后全部通过；原四个真实 `os._exit` 提交窗口扩展到全部八个 pending 阶段，另加准备阶段退出、并发读阻断、损坏证据修复后恢复、历史归档不被公开 pending 检查枚举。

原 `test_failed_enrichment_does_not_publish_mixed_inputs` 同时注入源错误和报告错误，旧断言依赖错误源仍然修改业务。现改为成功的隔离源输入再注入报告写失败，保留“已提交变化、stale、禁止派生”全部断言；来源故障由新测试独立断言零业务变化。未删除原始失败日志或削弱读端坏文件、NULL、单位及兼容性断言。

## 全量审查覆盖与结论边界

已核对 DG-00 清单中每个日线源入口：merge、在线/vipdoc/free-stockdb、兼容完整对象写入、quote/当前快照、enrichment、BaoStock calendar/lifecycle/dated facts、index/coverage、universe 均进入文件准备前滚或 catalog 事务日志。显式 `compute_limit_events` 保留协调者确认的强制计算/单日发布语义，记录计算范围；分钟、复权与配置清单仍为清单明确排除项。没有遗漏本期已盘点的源写入口。

文件准备在 pending 前落盘，文件替换与 catalog 不是跨对象原子事务；公开读在共享锁之后检查 pending，writer 前滚，coverage/stale/日志/逻辑序号/任务聚合同一 catalog 事务提交。异常和真实进程退出覆盖准备、替换前后、coverage、catalog commit、applied marker、redo 释放；重试与干净参考一致，已提交逻辑 revision 不重放。坏 manifest、redo、行证据、catalog revision/记录均拒绝，并保持可见的未决状态。测试证明这组 Linux/WSL 进程故障行为，不冒充断电/设备故障证明。

成功 applied 归档只有 manifest 与 delta 证据，无每次更新的完整历史 Parquet；pending 才保留必要 redo。公开 pending 检查只查看当前队列，不扫历史归档。任务汇总按 run_id 主键取计数，原实现 20,000 个无关任务 Index Scan 回归保留；SHA 访问现在单列为实际触及文件的逻辑字节代理。原始中断任务的耗时/RSS/等待未知项保持 unavailable，恢复计数与恢复任务分开；不是设备 I/O 或独占 RSS 测量。

正式 writer/命令的恢复在 maintenance context 内统计；实验直接调用低层 `recover` 而未提供该 context 时，没有独立恢复任务耗时/RSS，`recovery_run_id` 为 NULL，这些指标为 unavailable，不能从原任务耗时反推。原实现真实 12 行样本证据经指纹核验复用，没有为本次审查重复采样或复跑。

恢复工具包含 catalog、lake 和 `change-state/**`，独占锁内拒绝 pending 而不隐式修改源。原实施报告的工件 SHA 和 `c3c45f9` 源码指纹已全部逐项核验，见 [校验结果](evidence/data-remediation/20260927-dg01-review/implementation-evidence-check.json)；本轮没有再次复制生产或恢复根。旧无 checksum 的实验 pending 不自动视为可信，需用匹配旧代码完成恢复或恢复可信对象；未部署的本轮 feature 没有授权在线状态迁移。

无变化的业务文件、内部业务 revision、stale 与自动重算承诺通过。健康观测与任务摘要仍会写 catalog，因此可能触发现有 Fundwise 物理指纹；这是 **DG-06 限制**，本轮没有公开逻辑 revision/cache 支持，`dataset_version` 仍未提供。旧后端整证券/受影响年读取与重写、全会话规划和保守 stale 后缀仍归 DG-02/05，不把这些现有限制误写成已实现的有界存储。

尚未验证：真实断电、存储设备损坏后的自动重建、异机灾备、Windows native fsync/hardlink、生产增长性能 SLA、市场联网和 Fundwise 联调。缺失可信恢复材料需人工恢复；checksum 检测损坏，不构成对恶意篡改的认证。

## 验证与交付

原始红/绿日志和 [检查命令](evidence/data-remediation/20260927-dg01-review/checks.json) 全部保留。前两次新回归启动分别因导入路径和未建 basetemp 父目录失败，第三次才是 7 个实质红测；第四次的 outage fixture 缺日历，第五次修正后证实全部 5 项实质失败。它们均未替代最终隔离结果。

最终完整离线结果、精确提交、依赖、工件指纹和完成判定见 [validation.json](evidence/data-remediation/20260927-dg01-review/validation.json)。scoped lint 覆盖 `981ab5f..bbb50b0` 的 23 个 Python 源码/脚本/测试文件；format 只声明明确的 5 个文件。diff --check 明确排除原始测试日志，保留日志中的原始空白，不声称整个含日志差异无空白告警。
