# DG-04 当前事实、固定计划与生产执行证据

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


2026-09-27，基点 `be60d78`，独立 `.venv` editable 导入本 worktree，依赖复用 `/tmp/tdxman-dg00-constraints.txt`。生产 `/home/ubuntu/.aspool`；固定恢复快照 `/home/ubuntu/aspool-recovery/20260927-data-remediation/snapshot` 保持不变；实验及逐项来源证据 `/home/ubuntu/aspool-labs/dg04-20260927`。已完成两阶段具体授权的生产补数、1,213 会话重发布与真实 Fundwise 消费窗口验证；全池 stale=0。剩余 unknown/invalid 和消费者 partial 透明保留，不把发布成功称为基础数据完整。

## 当前只读审计

10:04:35 UTC 开始的当前生产核验，与 manifest SHA-256 `123a53ecf9461d65ddcafe43bf73e3edb16278f9fc5ad1b84e014547b079a76b` 对比 catalog + lake + change-state，无 added/missing/changed。复用 DG-00 已通过的快照哈希及恢复演练，没有重做快照全量哈希。当前实查 1,296 个发布日（2021-05-27～2026-09-24），605 个 stale 日（2024-04-01～2026-09-24），不是复用历史观察。

五年投影字段/会话审计：5,586 个股票身份、6,229,645 条日线；生命周期与明确交易日历下，10,135 个缺行情证券日，483 个明确停牌、9,652 个状态未确认。原始字段/日期事实层尚有 5,183 个 ST 未知、193 个有效参考价未知、193 个 pct_chg 缺失、1,271 个 turnover_rate/turnover 均缺失；67 行 open/high/low 非正，19 行 close 非正；2 个上市日期未知。该窗口自然日日历无缺行。已有源字段本身不等于经过新独立来源逐行认证，派生参考覆盖另验；不把这些缺口填成 0 或认定停牌。

原报告的 522 候选与 66 个重叠冲突重新逐项检查，原始 CSV/Parquet 与新的独立 BaoStock 缓存都保留在池外实验目录：

| 对象 | 处置 | 证据与限制 |
|---|---|---|
| 465 个候选日线 | 补入 | 新 BaoStock 未复权真实交易行与旧 TDX OHLC 相差 <0.011 元，volume/amount 相对差 ≤0.5%；记录每项差值，不称逐字段精确一致；写入 BaoStock 原始量额，不自行换算 |
| 57 个候选日线 | 无法确认 | 无独立记录，保留原候选，不写入，也不以不返回推断停牌 |
| 58 个有效主行情的重叠差异 | 否决覆盖 | 保留当前有效主 OHLCV 和二级来源冲突证据；未经新的独立裁决不替换 |
| 7 个无效主行情的重叠差异 | 否决交易行替换、补日期状态 | 新 BaoStock 明确 SUSPENDED，仅补权威日期事实，原始价格/量额保留；不制造平价零量日线 |
| 1 个无效主行情的重叠差异 | 无法确认 | 独立查询为空，保留 unknown/invalid 与原证据 |
| 已存在的 122 行历史修复行情 | 补缺失字段 | 当前原始 OHLCV 均存在，pre_close/is_st/pct_chg/amplitude/turnover_rate 均缺失；2024-04-01～09-27 新独立 BaoStock 122 条 TRADING 行，仅补日期字段和可计算派生，原 OHLCV 逐字段保持 |

## 实现与恢复边界

`src/aspool/remediation.py` 与 `scripts/repair_limit_publication.py` 提供一次固定 dependency plan，按 `--max-days` 顺序发布，状态落池外 JSON 与 catalog `remediation_runs` 权威 ledger。计划绑定完整输入内容水位、实现文件/依赖版本、v8 规则、scope/axis 与恢复 manifest；生产还需匹配具体 plan_id/source 的协调者 approval，明确 single_writer/recovery_verified。快照不能作为写入目标。

首批把完整计划标 stale；成功日期只经原单日发布事务清 stale。每日开始保存 pending，发布后提交权威 ledger，外部镜像原子替换并 fsync；外部 JSON 无权伪造进度，镜像落后一个 checkpoint 或丢失时从 ledger 恢复。已发布但 checkpoint 未完成的日期可重算一次，不能跳过。源变化、实现变化、恢复 manifest 变化、已完成输出变化或状态伪造均拒绝续跑。

`remediation_repair.py` 将来源记录冻结为一次补入计划，隔离演练记录各证券 source before / after_daily / after 水位；生产按 `--max-symbols` 逐证券重放，使用 DG-01 `merge_daily/catalog_rows` 的变化证据与前滚协议。日线已提交而日期事实未提交的失败仅在精确匹配已演练中间水位时继续；不相关输入变化拒绝。生产输入、独立证据文件及代码版本必须与演练完全相同。生命周期、名称、calendar、ST/板块规则没有改动。

输出批次有界，不宣称源读取已按 DG-05 优化：当前保守 v8 每证券读取完整历史，然后构建所选日期窗口的 compact cache。每次续跑重新核对完整输入哈希及 completed 输出哈希，属于显式维护任务。最终生产 1,213 日已用每批最多 256 日，共 5 批，避免逐日重做计划和全源水位检查；保留单日 checkpoint，可在取消/异常后续跑。每批全源哈希涉及约 1.7 GiB lake 逻辑字节，completed 输出核验随完成日增长；这不是设备 I/O 测量或日常热点能力。

## 已完成的新验证

- 真实全范围三会话（2026-09-22～24）以三次 max_days=1 续跑，与独立保守 `compute_limit_events` 参考的 events/exceptions/scope/references/gap states/streak boundaries/summary 精确一致；270.53 秒包含副本、规划、续跑及参考，RSS 410.99 MiB。references/gap/boundaries 在这三日为 0 行，不声称覆盖非空边界；后续门槛加强由新专项测试验证，没有重复三日算术计算。
- 补入计划隔离演练：30 证券、594 个独立来源证券日，465 条新增原始行，122 行已有行情补日期字段，7 日仅补停牌事实；合计 748 条 raw/派生行真实变化及 594 条日期事实变化。多出的原始行变化来自插入行影响 vol_ratio 等已有依赖，未改既有 OHLCV。87,240 条既有行情的 OHLCV 逐字段保留。
- 同计划在另一隔离输入上 4 次 max_symbols=8 顺序续跑（8/16/24/30），最终完整源 inventory 与演练精确一致。最早实际变更 2022-02-11，因此 conservative publication 后缀扩为 1,124 日至 2026-09-24，不能只重算旧 605 日。
- 新专项测试覆盖状态篡改/越界/伪造 initialized、源变化、外部 stale、丢镜像恢复、每日提交后崩溃、补行情后/日期事实前崩溃、精确重放/no-op、版本门槛及非空 reference/IPO/gap 状态恢复。精确命令、源码指纹和测试提交链接见本目录证据摘要；不重复 DG-00～02 成功套件。

## 当前生产发布门槛与消费验证

两阶段补数与发布已按具体 gate 完成。最终实际输入发布计划为实验目录 `production-authorized-publication-plan.json`，plan ID `36794742ac1e3c4a373c52773a77ec40449c9de2e676062b4c926f11fed061c5`，source `ddd0c37c09a299c8addaf9e4f09ba6928178079f1ced11f960b463018ab1e44b`，initial prior `00aaa72f98803d6cecb5370d478db204675e89342eda36e6e51055764e4a1ec8`。完整连续后缀为 2021-09-24～2026-09-24 共 1,213 个会话，已按最多 256 日、5 批顺序重新发布；1,213 日 stale 均经实际计算/单日事务清除，全池 stale=0。

发布前当前恢复包 `recovery-before-publication/` 及 `recovery-before-publication-manifest.json` SHA `0231763157911fea7b6d24590db2b359dcd0a7541850220107b590d818a4e237`，覆盖当前 catalog、两次变更日线文件并集和全部 change-state，共 812 对象、997,684,831 字节。在只读锁下复制并逐对象对比原件/副本哈希、验证副本 catalog 可读与生产无 WAL；与固定 DG-00 快照 overlay 后覆盖当前已修复输入。复用基线完整恢复演练，不宣称新全量 RTO 验收。发布经独立复审 msg_569b062d16d2 和协调者对具体 plan/source/prior/recovery 的 single-writer 与资源时段批准；许可保存在实验目录 coordinator-publication-approval.json。

发布后已检查目标 stale、summary/coverage/events/exceptions/batch 关联、scope 计数、独立与派生参考、IPO 首日及跨缺行连板边界。Fundwise 按其 AGENTS 的当前源码完成真实最近 30 自然日消费窗口、向前 60 自然日预热、截止日 name/ST 和上证会话验证，未改消费规则或 live cache；详细范围与结果见后文。

## 已授权的生产补数与新独立审查（续接）

协调者及独立审查批准 `a410d735` 的 `13414aed...` 修复计划，生产按 8/16/24/30 四批完成，实际 748 行、594 日期事实；完整 post 输入 inventory 为 `eff91eab...`，与已审批准值及隔离演练一致，pending=NULL。当时 1,124 日 stale 在最终新计划发布前持续保留，旧发布计划未执行。许可文件、四批日志及 `production-repair-validation.json` 位于实验目录。

独立审查复现旧发布计划的初始前史漂移（连板 1 改 99 后仍可计算 100），明确拒绝旧 publication plan。已新增完整语义前史 hash：规划时绑定 `_previous_session_states(first_day, frozen_scope)` 的会话日、是否涨停、板数和缺行计数，以及递归恢复的明确停牌链；执行在创建 ledger、标记 stale 或发布前验证该 hash，并在续跑时再验证。规划使用只读 catalog session。新前史 1→99、gap 状态、prefix stale、递归停牌前史漂移均拒绝且不新增 stale/发布/状态文件；连同受影响非空 reference/IPO/gap 续跑等 **14 项通过，13.24 秒**。算术 kernel 不变，复用此前三日通过证据。

随后仅对已实查缺失的基础字段做独立查询，逐证券/日期检查 OHLC、量额与已知 pre_close/is_st。当前新增 **2,506 条**可补基础字段记录，245 个证券，均为独立来源明确 TRADING，pre_close 有限正数、is_st 为布尔；无新证券或会话，无名称/ST/板块规则推断。10 条与有效主 pre_close 冲突，延期不写；3,695 条缺独立记录，其中 3,693 条为 BJ 来源不支持、2 条查询空记录，保留 unknown。

这 2,506 条已获独立审查及协调者具体批准，11:00:02.631901～11:02:56.595289 UTC 按 max_symbols=8 顺序 31 批完成，245 completed、pending=NULL。生产产生 2,726 条 raw/派生变化及 2,461 日期事实变化，与隔离演练完整水位精确相符。隔离演练产生 2,726 条 raw/派生真实变化及 2,461 条日期事实变化；45 条日期事实为无业务变化，不刷新 fetched_at。**519,796 条既有 OHLCV 与日期键逐字段保持不变**，额外 row 变化为既有可选字段/派生依赖，无新增原始行。逐项差值、字段及来源文件 SHA 保留在实验目录 `base-gap-dispositions.json` / `base-gap-payload.json` / `production-base-gap-plan.json`，不把 raw 数据提交 Git。该补充已通过精确 plan/source/recovery gate，实际 source inventory `ff7cff8b970e2b9290dd2bb855211e9763552f831c28a52b5e065c1b55a00587`、compute inputs `ddd0c37c09a299c8addaf9e4f09ba6928178079f1ced11f960b463018ab1e44b`。最终发布后缀为 2021-09-24～2026-09-24 共 1,213 日。

为保护已完成的第一阶段新数据，保留 `recovery-after-initial-repair/` 与 `recovery-after-initial-manifest.json`：在只读锁及无 WAL/pending 条件下，复制并逐对象核验当前 catalog、30 个改变的日线文件及全部 change-state，共 77 对象、942,644,782 字节。此 delta 与原 DG-00 不变快照叠加，覆盖 post-initial-repair 水位；原 snapshot 和 reports 未改。恢复说明要求普通复制原快照到独立目录后 overlay delta，不能用旧快照覆盖后续已写入数据。原快照的已通过全量恢复演练继续复用，本项是新增当前状态的对象核验，尚不称完整新灾备演练或 RTO 达标。

独立复审另要求证明“非空初始前史”的正常续跑与两批之间原前史漂移。仅新增并运行这两个案例：初始板数 2 经 max_days=1、max_days=2 两批得到 3/4/5；两批间将原前史改为 99，执行在任何新增 stale/发布/checkpoint 前拒绝。**2 passed、14 deselected，2.99 秒**；14 项通过证据复用，生产执行源码仍为 `9308d55`，只增测试与文档。此前三日与边界测试的 initial prior 为空，不能把它们单独当非空 initial prior 续跑证明。

新增基础字段演练不是性能验收；DG-03 当时持有性能槽位，存在资源重叠，不能标为无争用样本。可靠的持久变更时间范围为 UTC 10:40:25.697004～10:41:32.091206，445 个变更操作；精确进程启动/退出与完整 inventory 起止没有提前埋点，只能用源变更及产物时间给诊断边界，不补造测量值。详见 `supplement-resource-timing.json`。后续生产维护和消费验证需使用协调者明确交还的槽位，并从启动时记录真实 UTC 起止及资源。

两阶段修复后的基础质量采用原完整当前审计加两个冻结 payload 的去重逐日期实际差量核算，未重复全池审计：原始行 6,230,110，缺行情 9,670（明确停牌 483、状态未确认 9,187），来源层 ST 未知 3,693、参考价/pct_chg 缺失各 39、换手缺失 11；原非正 OHLC 和上市日期未知不变。差量脚本对两个阶段涉及的记录从固定快照和修复后隔离源读取实际行及日期事实；不能将来源层已有字段称为全池新独立认证。逐项拒绝/延期/未确认记录仍在池外证据中。

## 实际生产发布与验收

在冻结实现 `9308d5509ed0b0f378b94a098e0ea8b5357f65ff` 上执行最终计划，后续 `13d627c2d71dd73cf139f49b23448552469295f7` 仅新增两项已通过测试和文档，`4bb7484` 仅记录补数证据。11:08:07.483057～11:20:20.495273 UTC 完成五次顺序 max_days=256，进度 256/512/768/1024/1213，全部 exit=0、pending=NULL。各批真实耗时 122.14/130.52/136.39/150.53/133.83 秒，累计进程 673.41 秒、峰值 RSS 706.17 MiB。第三批末及第四/五批可能与协调者允许的 DG-03 nice19/ioniceidle 单进程隔离数据构造竞争，耗时只记实际维护成本，不作无争用 SLA 或 DG-05 性能验收。逐批日志在池外；小型时间与版本证据见 `production-publication-execution.json`。

实际生产只读检查 4.90 秒、RSS 396.11 MiB：目标/global stale 均 0，1,213 个公开 coverage/summary 关联，6,239,234 scope 行、170,845 events、15,664 exceptions、1,019 gap states、115 IPO streak boundaries。batch processed 分类计数与 scope、summary 与 batch 分类计数、summary 与事件四标志/未封板计数/最高已知连板/sealed_ratio 均一致；所有事件、异常、参考和状态组件均在同一当前 batch 的 scope 内，目标无未发布 open session，source/inventory 与已批准水位相符。

四个真实证券在完整市场/日历 axis 上作轻量保守顺序参考，包含实际 IPO/缺行接续；目标内 224 events、7 exceptions、5 gap states、2 IPO boundaries 与生产逐字段精确相等，包括跨 5 个缺行会话接续的已知 16 板及 IPO 起点后递增边界。原全范围三日精确算术结果和门槛专项测试继续复用，没有因交接重复成功测试。目标 1,213 日 **derived references=0**，不能把它称为非空 reference 验证；健康前缀的保守四证券参考有 166 条 reference，且已有非空 reference 专项测试。来源层未知和独立来源可信度分别记录，派生参考不替代独立来源认证。

## Fundwise 实际消费窗口及限制

使用 Fundwise commit `1b2565859f7c5c24a7ecd13e2a1625a196c402d8` 的原样 `RegimeService._recompute_market` 与 `iter_mainline_events`，当前 aspool editable 导入路径实查为本工作树。2026-08-26～09-24、向前预热至 06-27，11:21:03.033024～11:21:42.422085 UTC 成功完成，39.39 秒，RSS 1,425.96 MiB；缓存仅写实验目录，生产和 Fundwise 仓库未改。上游 source state=ready、1,296 发布日、stale=0，消费前后 revision 稳定。

真实市场服务产出 22 个会话，读取 354,337 投影行、64 个上证指数会话、5,570 行截止日名称/ST 快照；主线事件读入口在两个自然月/五个量额批次读取 1,400 条可排序涨停事件，amount 缺失=0。没有执行板块成员刷新、完整默认五年环境重算、独立主线排名编排或写 live cache；服务结果明确 phase_days=0、mainline_rows=0、mainline=not_computed，不能把事件读取通过称为完整主线排名通过。市场服务实际上已调用 attach_ladder 和 classify_phases；全 22 日 partial 使 first_board/ge2_count/promo_rate 均保持未知，阶段分类按既有缺输入规则返回未知，并非没有运行阶段算法。

全部 22 日仍为 partial：21 日 unknown_scope，1 日 unknown_scope,invalid_scope。实际消费者预热投影仍有 pre_close/pct_chg 缺失各 26、is_st 缺失 3,402、turnover_rate 缺失 1、amount 缺失 0；这是现有消费者投影口径，与前述股票五年原始/日期事实审计口径分别记录，不直接相加比较。未填零、未隐去部分覆盖，也未改当前名称/ST 排除及板块/评分业务规则。验收摘要见 `fundwise-window-validation.json`；原始缓存与逐日结果仅保留池外。

最终源码与已测提交绑定、精确入口、检查退出结果和复用边界见 `final-validation-linkage.json`。五个新增/修改 Python 文件的 Ruff check 与 format --check 均通过。固定快照、两阶段当前恢复 delta、reports、独立来源缓存、冻结计划及权威 ledger/external state 均保留；没有清理或回滚后续已修复事实。缺独立证据和冲突案件继续按原逐项 disposition 保留，后续有新可信证据时另行固定计划，不宣称本次关闭全部基础未知。

补充核对原样管线及已保存板块快照：Fundwise 本地 `.fundwise/regime/membership.json` version=1，2,652,558 字节、614 板块、concept 46,904 / industry 5,572 成分映射，来源记录为 `tdxman.MacClient.get_board_list/get_board_members`、snapshot_at=2026-09-24T16:26:09.095073 UTC，SHA `cad8c8477d2f0ed31552af3e978d0728016afbe69cc292a2e6dcdae7047b2f59`。11:28:14.670122 UTC 实查 age=241,325.575 秒，超过 `read_membership` 的 86,400 秒门槛； 原样 `recompute_mainline` 会转入联网刷新目录/成分，不能在“不刷新且轻量”约束下声称真实排名通过。没有伪造快照时间、替换 reader 或成员数据，也没有重复市场计算。仅用既存隔离缓存运行公开 `phases`/`mainline` 读取，0.05 秒：阶段 total=0、unknown_days=22，主线 rows=0、status=not_computed。证据与两个消费者模块指纹见 `fundwise-pipeline-boundary.json`。DG-07 尚需可信且当前有效的日期板块快照、单独资源时段和实际主线排名发布验证；阶段可用性仍受真实完整梯队输入约束。
