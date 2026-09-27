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
