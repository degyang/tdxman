# DG-04 当前事实、固定计划与生产执行门槛

2026-09-27，基点 `be60d78`，独立 `.venv` editable 导入本 worktree，依赖复用 `/tmp/tdxman-dg00-constraints.txt`。生产 `/home/ubuntu/.aspool`；固定恢复快照 `/home/ubuntu/aspool-recovery/20260927-data-remediation/snapshot` 保持不变；实验及逐项来源证据 `/home/ubuntu/aspool-labs/dg04-20260927`。本记录随实际执行更新，未通过 gate 的步骤不写成已执行。

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

输出批次有界，不宣称源读取已按 DG-05 优化：当前保守 v8 每证券读取完整历史，然后构建所选日期窗口的 compact cache。每次续跑重新核对完整输入哈希及 completed 输出哈希，属于显式维护任务。生产 1,124 日拟用每批最多 256 日，共 5 批，避免逐日重做计划和全源水位检查；保留单日 checkpoint，可在取消/异常后续跑。每批全源哈希涉及约 1.7 GiB lake 逻辑字节，completed 输出核验随完成日增长；这不是设备 I/O 测量或日常热点能力。

## 已完成的新验证

- 真实全范围三会话（2026-09-22～24）以三次 max_days=1 续跑，与独立保守 `compute_limit_events` 参考的 events/exceptions/scope/references/gap states/streak boundaries/summary 精确一致；270.53 秒包含副本、规划、续跑及参考，RSS 410.99 MiB。references/gap/boundaries 在这三日为 0 行，不声称覆盖非空边界；后续门槛加强由新专项测试验证，没有重复三日算术计算。
- 补入计划隔离演练：30 证券、594 个独立来源证券日，465 条新增原始行，122 行已有行情补日期字段，7 日仅补停牌事实；合计 748 条 raw/派生行真实变化及 594 条日期事实变化。多出的原始行变化来自插入行影响 vol_ratio 等已有依赖，未改既有 OHLCV。87,240 条既有行情的 OHLCV 逐字段保留。
- 同计划在另一隔离输入上 4 次 max_symbols=8 顺序续跑（8/16/24/30），最终完整源 inventory 与演练精确一致。最早实际变更 2022-02-11，因此 conservative publication 后缀扩为 1,124 日至 2026-09-24，不能只重算旧 605 日。
- 新专项测试覆盖状态篡改/越界/伪造 initialized、源变化、外部 stale、丢镜像恢复、每日提交后崩溃、补行情后/日期事实前崩溃、精确重放/no-op、版本门槛及非空 reference/IPO/gap 状态恢复。精确命令、源码指纹和测试提交链接见本目录证据摘要；不重复 DG-00～02 成功套件。

## 待 gate 的具体生产动作

补入计划 `/home/ubuntu/aspool-labs/dg04-20260927/production-repair-plan-v2.json`；发布计划 `/home/ubuntu/aspool-labs/dg04-20260927/production-publication-plan.json`。先按最多 8 证券重放补入、核对完整源与预计 post 水位，再按最多 256 日顺序重新发布。保持恢复快照和全部 reports；现有快照涵盖本次写入前完整输入，DG-01 的新的 change-state 和 DG04 ledger 后续恢复一并保留。不覆盖为旧池而丢失新写入，优先续跑已准备操作；需要整池回退时须协调停写并保留/重放后续变更。

发布后检查目标 stale、summary/coverage/events/exceptions/batch 关联、scope 计数、独立与派生参考、IPO 首日及跨缺行连板边界；保留所有剩余 unknown/invalid。Fundwise 按其 AGENTS 的只读入口验证实际最近30自然日消费窗口、向前60自然日预热、截止日 name/ST 和上证会话，必要时再验证默认扩展窗口；不改消费规则或 live cache。完整长窗口计算须另获重型槽位，不能与 DG-03 基准争用。当前尚无生产修复、重新发布或 Fundwise 运行成功的结论。

## 已授权的生产补数与新独立审查（续接）

协调者及独立审查批准 `a410d735` 的 `13414aed...` 修复计划，生产按 8/16/24/30 四批完成，实际 748 行、594 日期事实；完整 post 输入 inventory 为 `eff91eab...`，与已审批准值及隔离演练一致，pending=NULL。现有 1,124 日 stale 持续保留，未执行旧发布计划。许可文件、四批日志及 `production-repair-validation.json` 位于实验目录。

独立审查复现旧发布计划的初始前史漂移（连板 1 改 99 后仍可计算 100），明确拒绝旧 publication plan。已新增完整语义前史 hash：规划时绑定 `_previous_session_states(first_day, frozen_scope)` 的会话日、是否涨停、板数和缺行计数，以及递归恢复的明确停牌链；执行在创建 ledger、标记 stale 或发布前验证该 hash，并在续跑时再验证。规划使用只读 catalog session。新前史 1→99、gap 状态、prefix stale、递归停牌前史漂移均拒绝且不新增 stale/发布/状态文件；连同受影响非空 reference/IPO/gap 续跑等 **14 项通过，13.24 秒**。算术 kernel 不变，复用此前三日通过证据。

随后仅对已实查缺失的基础字段做独立查询，逐证券/日期检查 OHLC、量额与已知 pre_close/is_st。当前新增 **2,506 条**可补基础字段记录，245 个证券，均为独立来源明确 TRADING，pre_close 有限正数、is_st 为布尔；无新证券或会话，无名称/ST/板块规则推断。10 条与有效主 pre_close 冲突，延期不写；3,695 条缺独立记录，其中 3,693 条为 BJ 来源不支持、2 条查询空记录，保留 unknown。

这 2,506 条尚未写生产。隔离演练产生 2,726 条 raw/派生真实变化及 2,461 条日期事实变化；45 条日期事实为无业务变化，不刷新 fetched_at。**519,796 条既有 OHLCV 与日期键逐字段保持不变**，额外 row 变化为既有可选字段/派生依赖，无新增原始行。逐项差值、字段及来源文件 SHA 保留在实验目录 `base-gap-dispositions.json` / `base-gap-payload.json` / `production-base-gap-plan.json`，不把 raw 数据提交 Git。该补充必须独立通过精确 plan/source/recovery gate；通过后最终发布后缀为 2021-09-24～2026-09-24 共 1,213 日，否则仍为原 1,124 日，不先假定获批。

为保护已完成的第一阶段新数据，保留 `recovery-after-initial-repair/` 与 `recovery-after-initial-manifest.json`：在只读锁及无 WAL/pending 条件下，复制并逐对象核验当前 catalog、30 个改变的日线文件及全部 change-state，共 77 对象、942,644,782 字节。此 delta 与原 DG-00 不变快照叠加，覆盖 post-initial-repair 水位；原 snapshot 和 reports 未改。恢复说明要求普通复制原快照到独立目录后 overlay delta，不能用旧快照覆盖后续已写入数据。原快照的已通过全量恢复演练继续复用，本项是新增当前状态的对象核验，尚不称完整新灾备演练或 RTO 达标。

独立复审另要求证明“非空初始前史”的正常续跑与两批之间原前史漂移。仅新增并运行这两个案例：初始板数 2 经 max_days=1、max_days=2 两批得到 3/4/5；两批间将原前史改为 99，执行在任何新增 stale/发布/checkpoint 前拒绝。**2 passed、14 deselected，2.99 秒**；14 项通过证据复用，生产执行源码仍为 `9308d55`，只增测试与文档。此前三日与边界测试的 initial prior 为空，不能把它们单独当非空 initial prior 续跑证明。

新增基础字段演练不是性能验收；DG-03 当时持有性能槽位，存在资源重叠，不能标为无争用样本。可靠的持久变更时间范围为 UTC 10:40:25.697004～10:41:32.091206，445 个变更操作；精确进程启动/退出与完整 inventory 起止没有提前埋点，只能用源变更及产物时间给诊断边界，不补造测量值。详见 `supplement-resource-timing.json`。后续生产维护和消费验证需使用协调者明确交还的槽位，并从启动时记录真实 UTC 起止及资源。
