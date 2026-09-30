# 第一批整改独立审查与合并结论

日期：2026-09-27。结论：**首批代码可合并，未解决的合并阻塞项为零；DG-01 仍为部分完成，不授权生产修复、迁移或切换。**

审查 `6cc4fd9`、`61559e1` 相对 `5083dbb` 的全部变化，包括 DG-00 文档与脚本、DG-01 写入路径及证据。四个已记录基线失败均重新复现和逐项处理，没有用“此前已存在”豁免，也没有修改市场/ST 规则。

## 身份、边界与提交

- 任务 `task_e156082ba705`，Dispatch `ctx_3b811a14e5d6`；角色为独立关键审查。
- 请求与实际均为 `gpt-6-astra / high`。业务工作开始前从本会话 `turn_context` 核验，协调者另核验 Orca requested/effective 启动回执；未换模型或提高 effort，未使用子 agent。[身份和环境证据](evidence/data-remediation/20260927-orca-review/session.json)只保留允许的身份字段，无凭据。
- 工作区 `/home/ubuntu/orca/workspaces/tdxman/aspool-dg00-contract`，分支 `degyang/aspool-dg00-contract`，使用该工作区 `.venv` 和 editable 源码。
- 所有写入测试均用 `/home/ubuntu/aspool-labs/20260927-orca-review/task_e156082ba705/` 下的显式独立根。未写生产池/恢复快照，未抓取网络行情、迁移、删除数据、修改 Fundwise 或用户/全局配置，未合并 main 或推送。
- `f949dbc`：非法板块参数在建立行情连接前校验，独立小提交。
- `30870a5`：Aspool 合并阻塞修复、故障回归、四项基线测试纠正。
- 本报告及证据的后续文档提交不改变以上已验证代码。

本轮修改文件：

- 生产代码：`src/aspool/{change_observation,daily_storage,enrichment,free_stockdb,tdx_online}.py`，`src/tdxman/cli/cmd_board.py`。
- 回归：`tests/unit/{test_daily_remediation,test_aspool_index,test_daily_derived,test_protocol_fixes,test_cli_board}.py`，`tests/fixtures/daily_derived_samples.json`。
- 文档：本报告、`data_remediation_execution_plan.md`、`data_remediation_progress_20260927.md`，以及 `docs/evidence/data-remediation/20260927-orca-review/` 内的身份、检查日志、保护器和验证摘要。

## 发现与修复

| 问题 | 影响与处理 | 回归证据 |
|---|---|---|
| enrichment 在异常处理内再次保存同一观测 | `save_changes` 失败会二次抛异常、丢失已提交结果并中断后续证券；改为业务写入结束后只尝试一次，保留主错误、`observation_error`、实际计数及日期，继续后续任务 | 观测写失败后已写行数不丢失，后续任务仍返回；在线同类故障亦覆盖 |
| 在线部分写入只返回 symbol/error | 第二年文件失败时已写第一年的变化和成本不可见；失败条目现在保留变化日期、行数、成本及 `partial` 证据引用 | 在线与 enrichment 第二分区替换失败注入；旧第二文件未变、临时文件清理、第一文件计数与证据一致 |
| 失败批次仍触发派生发布 | 部分 enrichment 或在线发布失败后可能用混合输入重新发布并清 stale；有写入/观测错误时跳过该轮派生，保留 stale 和失败状态 | enrichment 的失败批次端到端测试禁止调用计算器 |
| 上市日期失效从 IPO 起算 | 新增 listing_date 也会排除上市日前原先纳入的记录，且递归前史受影响；无法证明精确边界时保守标记全部已有发布，从最早受影响发布启动重算，记录 `lifecycle_start` | 窄窗口、纯生命周期变化、上市前已发布日均覆盖；已有生命周期重跑不刷新；错误证券 finance 不建立事实 |
| 按年独立 merge 漏掉跨年依赖 | 新路由的离线 writer 会继承跨年量比/下一日参考价隐患；改为同一证券的完整逻辑日期序列计算，再仅替换有实际变化的年份 | 年末 volume 修订结果与同输入单文件参考逐行一致，跨年后续五行同步变化；相同输入重放零变化/零写入/零 stale |
| 第二分区失败后 coverage 丢失已提交进度 | 失败时按实际磁盘文件刷新 coverage；coverage 本身写失败后的 no-op 重试只修复不一致的日期范围/行数，不重写行情 | 新增首年记录、次年失败，coverage 仍与实际行数一致；coverage 故障重试独立测试，`coverage_repairs=1`，再次重跑为 0 |
| free-stockdb 失败批次计数停留在零 | `sync_runs` 失败时现在保留已完成证券数及实际提交行数，部分证券行数也累计 | 首证券成功、来源随后异常，failed 记录仍为 1 证券/1 变化行 |
| 板块 CLI 在验证非法类型前联网 | 严格离线运行揭露额外真实缺陷；只移动参数解析到 client factory 之前，不改市场规则或有效参数行为 | 无效类型返回 2，并断言 client factory 未调用；有效类型原有测试通过 |

失效仍按保守后缀传播；没有引入字段依赖提前收敛，没有静默清 stale。ETF 离线变化仍不影响股票限价发布，空/部分来源不解释为删除，单位与公开 API 保持原契约。

## 四个基线失败逐项结案

通过 `git archive 5083dbb src tests` 导出到独立实验目录，以该目录 `src` 作为 `PYTHONPATH`，使用当前工作区 `.venv` 和离线保护器重现。[原始复现日志](evidence/data-remediation/20260927-orca-review/baseline-audit.txt)还包含上述额外板块 CLI 缺陷。

| 原失败 | 核实的原因 | 保留/增强的断言 |
|---|---|---|
| `test_stock_default_still_uses_existing_pipeline` | 只 mock 在线获取，实际默认流程还会调用 enrichment，虚拟池没有日历；后续暴露旧断言遗漏 `derive_limits=False` | mock 明确的 enrichment 边界及 BaoStock 开关，断言股票更新先延后派生、补齐按默认 30 日/4 workers 调用、指数路径未调用 |
| `TestComputePriceLimits.test_main_board_st` | 未指定日期却期待历史 5% 规则 | 明确 2026-07-05 历史断言，并新增 2026-07-06 生效日 10% 断言 |
| `TestSampleData.test_single_day_samples` 的 ST 子样例 | fixture 写 2026-09-15 却期待 5%，且 wrapper 未向算法传样例日期 | 5% 样例改为明确历史交易日 2026-07-03；wrapper 将每个样例的日期传到底层，保留原价格、触板和涨停断言；当前规则另有直接测试 |
| `test_compute_price_limits_for_stocks` | 未指定日期却期待旧主板 ST 限价 | 明确旧规则末日与新规则首日，两套限价均验证，保留普通股/创业板/科创板/北交所断言 |

这些是对仓库既有日期合同的测试修正；`src/tdxman/codec/price_rules.py` 未修改，不从外部资料另行解释市场规则。

## 验证、成本与内存边界

- 最终完整离线套件：**556 passed、4 skipped、18 subtests passed，38.49 秒**。[完整日志](evidence/data-remediation/20260927-orca-review/full-offline-final.txt)。没有剩余失败。
- 4 个 skip 全部说明：两项真实网络 smoke test 按 `XMTDX_LIVE=0` 跳过；两项外部 tick-stock-panel 宿主只读契约测试因未设置 `TICK_STOCK_PANEL_ROOT` 跳过。本任务不配置该外部宿主。
- DG-01 专项：21 passed；[日志](evidence/data-remediation/20260927-orca-review/regression-3.txt)。包含此前 11 项和本轮增加的故障、生命周期、跨年及计数测试。
- [scoped lint](evidence/data-remediation/20260927-orca-review/lint.txt)通过，包含首批 5 个 Aspool 模块、2 个脚本、本轮 CLI 模块、相关测试和离线保护器。4 个原先采用统一格式的文件 format check 通过；既有大文件未做无关格式化。`git diff --check` 通过。
- [离线保护器](evidence/data-remediation/20260927-orca-review/run_offline.py)禁止当前测试进程的外部 socket 连接，允许 transport 单测使用 loopback；拒绝 Python 层对生产/恢复根的写操作。它不是对 native 库/子进程的 OS 沙箱；实际测试通过显式独立根隔离，不据此声称绝对系统级防护。
- 字段差异仅在一个证券内累计，保存到独立 JSON 后，批次结果只保留路径、计数和日期；回归检查 partial 文件只包含已提交年份并且总结果没有内嵌 `changes`。这是 O(单证券变化行数) 的证据内存边界，不是固定大小，也不是整任务 RSS 上限；conflicts/quality 汇总仍需后续资源治理。
- `rows_read` 是已解码 Arrow 行数，`rows_materialized` 是用于合并/推导的 Python 行记录数；enrichment 复用已经物化的行，避免未计数的二次 `tail.to_pylist()`。文件字节是逻辑大小代理，不是设备 I/O；`changed_rows`/写入计数仅在 replace 成功后累计，stale 标记可重复。
- enrichment 的 `elapsed_seconds` 现在包含单证券观测保存；在线 cost 仍测 `merge_daily` 阶段，不包含观测保存或最后批量 coverage 提交；均不包括外部网络和锁等待。直接 merge 的早期异常可能没有完整耗时值，不将其当总任务耗时。`coverage_repairs` 单列元数据修复，不冒充行情变化。
- 跨年正确性回退会读取该证券全部年份；历史修订仍可能物化整个后缀。已取消成功更新后用于 coverage 的第二轮全证券回读，部分失败才重读磁盘确认真实状态。该改变优先保证正确性，没有宣称满足 DG-02 的有界读取或完成性能选型。

## 未实现及下一包

**无已知未解决的首批合并阻塞项。** 事后字段报告与上述可捕获异常处理不等于跨文件/数据库事务：进程强制退出、磁盘/目录同时故障或报告目录不可写仍可能缺少持久化变化证据；调用方可见错误和 stale 必须保留。只有下一包完成准备/完成/失败状态、恢复重放及故障演练，才能宣称 DG-01 可重放变更链完整。

下一包 DG-01 先统一 BaoStock 日历/生命周期/日期事实、quote、指数 coverage 和证券范围旁路的 no-op/变化观测，再补确定的输入/输出修订、事务恢复状态与观测失败补证机制，最后补全任务级锁等待/RSS/缓存/计算/临时空间成本。DG-02 随后集中旧后端访问并优化跨年预热、覆盖元数据与有界扫描；本批保守跨年读取应作为其明确优化基线。

未重跑五年 RSS、真实快照性能实验、生产恢复演练及外部消费者联调；既有 DG-00 基线核验证据仅作为历史证据，不声称本任务重新核验生产水位或恢复 SLA。生产修复、迁移、DG-07 切换和 DG-08 退役仍需独立验收与授权。
