# DG-01 剩余实施与隔离验收

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


日期：2026-09-27。本文保留 `c3c45f9` 实施交付时的状态和证据；随后独立审查发现并修复的恢复、源故障、TTL 与成本问题，以及当前工程判定见 [独立恢复审查](dg01_review.md)。main 集成由协调者完成，本分支不执行生产修复、迁移、切换或清理。

首批 `981ab5f` 已由协调者 fast-forward 集成并推送 main/feature；本轮基于该提交。任务 `task_c544312049ac`、Dispatch `ctx_85e33bd51354`、终端 `term_53175c77-60be-4618-9acd-c8a2cbc3f787`，独占实施工作树 `/home/ubuntu/orca/workspaces/tdxman/aspool-dg00-contract`、分支 `degyang/aspool-dg00-contract`。本会话 turn_context 与协调者 requested/effective 核验均为 `gpt-6-sol / high`，全程未换模型、升级 effort 或使用子 agent。身份与依赖版本仅保存允许字段。

## 验收边界

DG-01 的无变化承诺针对源更新/补齐与其自动调度：健康相同输入不替换业务文件、不推进业务修订、不新增 stale、不重新发布派生。已有 stale 或缺失发布可重试，不能把这类恢复误称为健康 no-op。

协调者通过本次 live `orchestration ask` 明确批准：显式调用 `compute_limit_events` 继续表示调用者要求强制重算/重新发布。原单日发布事务保留，增加实际重算/读取范围观测；可靠派生依赖判定与输入/输出发布修订仍归 DG-05/06。完整 quote/BaoStock 命令与既有在线/enrichment 调度测试覆盖自动 no-op 不调用计算器的边界。本轮不改变 Fundwise，也不把内部计数器公开为 dataset_version/cache API。

## 完整入口清单

| 入口类别 | 实施与变化证据 | 回归 |
|---|---|---|
| `daily_storage.merge_daily` | 按实际字段差异准备每个受影响文件；保留跨年依赖与五行预热，空/重复/非法候选及显式 delete/retract 在新业务提交前拒绝；NULL 输入保留可靠值 | 真实字段/跨年依赖、健康重放、所有提交故障窗口、真实进程退出 |
| `enrichment._publish` / `enrich_daily` | 文件及新增上市事实接入日志；上市影响无法精确证明时保守标全部已发布日；报告差异增量落盘，观测失败仍保留已提交计数 | no-op、上市边界、第二分区失败、报告失败、完整调度禁算 |
| 在线 `tdx_online._publish_stock_rows` / `_sync_daily_run` | 同协议合并；失败条目保留提交行数、日期和证据；来源或发布失败不进入本轮自动派生 | 空/失败来源、提交后失败计数、股票与 ETF 路径、部分分区、相同输入 |
| vipdoc `update_daily_offline` | 通过 merge，不以空读取删旧事实；股票和 ETF 单位及范围不改 | 源入口重放与故障，ETF 变化不失效股票发布 |
| `free_stockdb.import_daily` | 同协议；失败 sync_runs 保留已完成证券与实际提交行数 | 全量/增量重放、空源、来源中断、文件提交故障 |
| `_write_daily` 兼容 helper | 显式完整对象写入，支持既有单文件/分年布局；遗漏旧行和重复键拒绝，字段遗漏保留旧字段；显式可选字段 None 替换有旧/新值证据；调用者仍负责完整对象字段语义并持写锁 | 分年不重写未变年、可选字段撤销、行删除/重复拒绝、锁内调用 |
| quote `_publish_quote_rows` / `update_from_quotes` | 日线提交与 snapshots 分对象恢复，真实失败计数不丢；仅变化或已有 stale/缺发布可自动派生；部分失败不发布新派生 | quote 入口故障、完整命令健康重放禁算、来源故障、catalog 已提交后观测失败 |
| `_write` / `_publish_snapshots` / `refresh_fundamentals` | 当前快照差异忽略刷新时间，缺字段不清空旧值；健康观测另存，不创建空快照 | no-op、快照替换故障、重试与原值一致、锁内调用 |
| BaoStock calendar / lifecycle / dated facts | 逐行事务日志；移除抓取前无条件 stale；fetched_at 不参与业务比较；生命周期健康 TTL 单独记录，只有真实刷新基本信息才续期 | 三类入口 no-op/事务回滚/重试，完整 supplement TTL、来源中断 |
| BaoStock `_publish` / `supplement_daily` | OHLCV 仍只补缺，不覆盖有效主来源；缺失/非法可选字段保留旧日期事实；明确主来源冲突可撤销对应 overlay，原因 `validated_primary_conflict` | 半空字段不撤销、空响应保留、冲突撤销及重放、公开 pre_close 优先级 |
| index `save_index` / `_publish_indices` / `sync_indices` | 指数文件与 index_coverage 同提交恢复；健康 no-op 保持覆盖时间/来源；覆盖修复单列元数据变化 | 文件/coverage 故障与重放、指数公开读取、空源及非法记录既有校验 |
| universe `_publish_universe` / refresh | 仅真实名称/市场/范围字段变化写业务记录；缺行/空列表不推断 inactive，显式停用/retract 拒绝；stock scope 保守失效，ETF scope 不影响股票；抓取 TTL 另存 | 部分目录、空列表、停用拒绝、健康 TTL、事务失败与重试 |
| `record_coverage(s)` / index extent repair | extent/provenance 元数据旧/新值、来源、原因和元数据序号同事务记录；健康相同元数据不更新时间；不冒充行情变化/业务修订 | 直接 helper no-op、coverage 写失败、catalog 回滚、修复后准确行数 |
| 显式 `compute_limit_events` / `_publish_batch` | 保留既有强制语义与单日原子事务；记录计划 stale、会话轴/计算读取及实际已发布日期/处理行数 | 既有失败/恢复/增量与完整计算、锁及范围测试；非健康 no-op 源入口 |

复权因子、分钟、可审阅 ETF/指数配置列表维护不属于本期日线源变更协议；原入口与数据保留。证券目录仍是当前快照，PIT/历史名称/ST/板块解释没有改变。源观察缓存/维护报告是证据，不把 JSON 缓存刷新当行情事实或全池版本。

## 最小持久协议与原子性

实现位于 `src/aspool/change_protocol.py`，沿用 Parquet、DuckDB catalog、目录池锁与现有后端，没有新增存储后端、全历史版本回填或通用事务框架。

1. 在写锁下比较真实内容，仅有差异才创建 `change-state/preparing/<operation>/`。旧/新字段、证券/日期、操作、来源、原因、逻辑行指纹逐行写 `rows.jsonl`；固定内存保留一行。准备受影响文件的 `new.parquet`，fsync 文件及新建目录的全部父项。
2. 持久写 manifest，包含目标、输入/输出内部逻辑序号、确实变化的行/字段数、字段证据、期望 coverage 与物理恢复 SHA。原子移到 `pending/` 后 fsync 两侧目录，才允许替换目标；准备与 pending 分开，不能把丢失的 pending manifest 误判为“从未提交”。
3. 把新对象硬链接到目标同目录的临时项并原子替换目标，fsync 目标目录，持久记录 `file_replaced`。只使用准备的受影响文件，不再复制一份旧冷历史来观察差异。
4. DuckDB 一个事务提交 coverage（含旧/新元数据日志）、保守 stale、业务操作 `applied`、业务逻辑序号和任务聚合。失败回滚该 catalog 事务，文件不能因此称为已回滚；pending 仍要求前滚。
5. 完成记录持久化后释放冗余 redo 硬链接，再移动到 `applied/` 并 fsync。成功历史只保留 manifest 与逐行证据，不保留每一代完整 Parquet；下一次 hot 更新不会因日志保留上一代冷历史。释放后但归档前崩溃可根据 catalog applied + 目标 SHA + 行证据完成归档。

单个文件替换原子；catalog 的 coverage/stale/逻辑序号/日志/任务计数原子；两者之间与多文件/多证券任务**不是整体事务**。第二文件失败时第一文件可能已提交，并有精确状态/计数/coverage。公开池读在 pending 存在时抛 `RECOVERY_REQUIRED`，不发布混合输入；源 writer 在下一写锁内恢复。裸 `pool_lock(write=True)` 只协调，不隐式改源；snapshot 明确拒绝 pending，保持源不变。锁上下文按真实 root 记录，持写锁但没有 catalog_session 的 helper 也不会自锁。

前滚验证 redo 与证据 SHA，检测是否 catalog 已提交；已提交不再次推进序号，文件已正确替换时不重复替换，未提交时补同一 catalog 事务。准备目录未进入 pending 的对象只能废止：保留行证据、标 `aborted_before_publication`，释放未发布临时 redo，业务为零。pending manifest/证据/必要 redo 缺失或损坏则拒绝恢复并保持读阻断，需要恢复可信对象/恢复点；不能制造 applied。没有自动从字段报告重建任意损坏的 Parquet。

catalog-only 四类事实在同一 DuckDB 事务内完成变更、逐行旧/新值证据、序号、stale 与任务聚合；rollback 不产生 applied/新序号。其持久状态等价于“事务内全部 applied 或全部无提交”，不为每行额外复制数据库或虚构一个外部 prepared JSON。

`reports/changes` / maintenance JSON 仍是**事后兼容观测**，不是事务日志。内部来源报告关联 `change_run_id`，业务事实以 `business_changes`、`catalog_change_rows`、`coverage_changes` 和准备状态为准。报告写失败保留已提交计数与主错误；提交后观测失败有专门回归。原来源报告 ID 也关联 maintenance run，不能仅以一份 JSON 文件存在判断提交成功。

## 修订、NULL、删除和恢复链

`business_revisions` 是按受影响文件或事实表记录的内部逻辑变更序号，0 表示协议启用前既有状态；仅实际业务变化推进。`input_row_version/output_row_version` 是规范化逻辑行指纹，忽略 None/NaN/数值存储类型差异。物理 SHA 只用于恢复，不是逻辑 revision。`metadata_revisions` 仅追踪 extent/provenance 等 catalog 元数据操作（完整 before/after 包含提交时间，fields 列表仅计结构字段），不替代业务修订；修复覆盖不制造行情变化。公开 dataset_version 仍为 None，Fundwise 物理兜底不改，DG-06 才建立可靠消费修订接口。

普通 source None/NaN、空响应与异常保留事实。显式行删除、目录停用及通用 delete/retract 在新业务提交前拒绝。两个已有内部撤销语义有测试：完整对象 helper 显式可选字段 None；BaoStock 经有效主来源冲突裁决撤销日期 overlay。二者与来源遗漏不同，证据保存旧/新值与原因，普通缺失字段不触发撤销。算法撤销不可信派生值的已有依赖行为保持。

恢复 snapshot/manifest 工具新增 `change-state/**`，catalog 自身包含事实日志、修订、健康与任务聚合。snapshot 拿独占锁但拒绝 pending；verify 不排除新状态。保留策略新增 preparing/pending、applied 行证据和 catalog 引用，不能按日期/版本/成功次数删除重放链。仅本协议冗余临时 redo 在已提交或确定未发布时释放，不执行生产清理；旧 DG-00 全池恢复证据继续复用，没有为本轮复制全池。

## 成本与增长边界

`maintenance_runs` 是同一命令及其嵌套发布阶段的任务视图。`maintenance_totals`、`coverage_totals` 随 catalog 提交更新，最终按 run_id 主键读取；不为统计扫描全部历史 changes。20,000 条无关任务聚合的实测计划验证 `Index Scan`。

- 行/字段数是**实际已提交变化事件数**，同一键跨阶段再次修订会再次计数，不能当唯一受影响证券/行数。业务与 coverage 元数据计数分开；partial/fault 只数 committed 操作。恢复注明原准备 run 与恢复 run，原任务聚合按点读取修正；进程强制退出缺失的原耗时/RSS/等待设为 unavailable。
- candidate、pool 读取、物理替换、业务重写、保守 stale 后缀、重算读取/实际发布与恢复分别记录访问数和日期边界。路径样本最多 16 项；范围端点/访问次数不是唯一文件数。保守 stale 是已有发布的后缀，下游尚未证明的依赖不提前收敛。
- Arrow 解码行访问和文件逻辑字节是访问代理，不能当设备 I/O；源 transport/native I/O 无法从这层精确测得。递归计算与旧后端跨年全读是原行为，DG-02/05 再优化，不加入观察用的市场全历史扫描。
- elapsed 包含命令工作及锁等待，最终摘要写入自身不纳入；真实锁竞争回归测到等待。RSS 为进程 ru_maxrss 高水位，含进程此前工作，非独占任务峰值。临时空间为同时存活准备 Parquet + 行证据的字节代理，排除 WAL、库内部临时文件及页缓存；不是全池物理占用。
- 可用缓存信息是业务逻辑变化、保守 stale 与重算范围；实际 Fundwise/下游 cache 驱逐数量为 unavailable，不编造 0。市场全任务不累积 row delta 列表，兼容字段报告也用增量 JSONL spool；当前快照索引不是历史行情/差异全集。

验证支持同一证券多轮 hot 更新：applied 下没有 `new.parquet`，历史证据只按实际 delta 增长。旧后端本身仍会读取/重写整证券或受影响年文件，日志没有增加一份完整冷历史；这不代表已满足 DG-02 的有界扫描或完成存储选型。

## 验证、提交和未验证项

实现提交为 `c3c45f96c05c343fab86ca090b9395dcbf63a0ef`。最终完整离线 **617 passed、4 skipped、18 subtests passed，144.88 秒**；两项市场网络默认关闭、两项外部 tick_stock_panel host 未提供，均沿用首批明确跳过边界。22 个本轮 Python 实施/测试文件加离线保护器的 scoped lint 通过，5 个选定文件格式检查通过。真实样本修正实际 1 行（0.220 秒），重放 0 行（0.051 秒），源快照未变。

最终数字、代码提交、输入/版本指纹及命令保存在 [验收摘要](../evidence/data-remediation/20260927-dg01-complete/validation.json)。完整离线日志、scoped lint、身份、真实样本与格式检查同目录；所有实验数据写入位于 `/home/ubuntu/aspool-labs/20260927-dg01/task_c544312049ac/` 下显式独立根，最终全套使用唯一 `final-independent` 根。离线保护器复用首批已验证 audit，禁止当前 Python 进程外部 socket 与生产/恢复根写；不宣称它覆盖 native 库或子进程的 OS 沙箱，子进程故障/锁测试使用明确小型实验根且代码无网络获取。

开发验证出现 writer 新增重复键拒绝与读端故意制造坏文件 fixture 的冲突，已让坏样本直接写 Parquet，保留原全部读端断言，并新增 writer 重复拒绝。较早全套复用临时目录且出现状态缺失，单项独立重现通过；它们不作最终验收，最终串行独立根重跑。旧首批失败日志的行尾空白不改，diff/format 检查仅声明本轮明确代码/测试/文档范围。

真实样本只从既有已验证快照读取 `SZ/000001` 末 12 行，记录源 manifest/文件 SHA、依赖与代码指纹；不抓市场数据，不做全池新副本。验证原始 OHLCV/金额公开读取与原快照精确一致，末日 amount +1 CNY 后实际变化及重放零业务变化。该样本不代表全市场、完整 overlay/PIT 或生产恢复 SLA。

尚未验证：真实断电/存储设备故障、异机灾备、Windows native 目录 fsync/跨文件系统 hardlink、市场网络、多年生产性能与消费方联调。协议依赖当前 WSL/Linux 同文件系统、可靠 fsync/原子 rename 和 DuckDB 持久事务；跨文件系统或损坏必要对象时安全失败，需独立恢复处置。全任务仍可 partial，故障后必须恢复 pending 才能读；这一限制明确存在，不以 post-write 报告包装成整体事务。

完成本轮验证不授权 DG-04 生产补数、DG-07 切换、DG-08 退役；复杂恢复独立审查仍由协调者后续实施。保留本 feature 工作树和实验数据供审查，不 push、不合并 main。
