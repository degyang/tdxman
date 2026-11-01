# DG04 生产恢复 gate 审查

日期：2026-09-27。结论：**批准具体修复计划按最多 8 证券分批执行；拒绝当前具体发布计划按最多 256 日执行，直到补齐下述唯一关键依赖门槛。** 修复批准限于已有 single writer、固定 DG00 恢复点与前滚协议，不增加用户审批或 SLA。审查完成不等于生产执行完成。

## 审查对象与指纹

- 只读代码目录：`/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair`；实际 HEAD `a410d735a26b77b72650a6b26930eaea86922080`；开始及结束工作树均干净。
- [修复计划](/home/ubuntu/aspool-labs/dg04-20260927/production-repair-plan-v2.json)：plan_id `13414aedffc0ad72e3e33e6cc0f1faea8e9b4fe24e818461505932a364819641`；source `244df2024ea64ba0ea2ad42bc18b6698d385b2189515ccd73f7d576ddbe9cd0d`；最终 after `eff91eab10e43c3b1df52c78320f51c2e26653e99159d9320daca41a6e937bdd`；文件 SHA256 `3184c9fbefdb38608d681c08a3e2b972d07bf90ae137d2dfe6f3dff0a98f904b`。
- [发布计划](/home/ubuntu/aspool-labs/dg04-20260927/production-publication-plan.json)：plan_id `89553a2926e536f6dce3f74e4d6a848b3f9bf515fefb03fbfbc893afaf328e35`；修复后计算输入 source `9dff73146110836d3f5427fbdfd1b2f687baf504f4d9e7656b4c75eb3455d6de`；文件 SHA256 `d3e780dbbc4d31dd251ab7e4f91dbebcb5884ffc696840c48be5f785baf4ee11`。修复 inventory 与发布 inputs 使用不同摘要算法，两者不应直接比较。
- 两份计划内容签名重新计算均正确；修复 30 步 before/after 链连续。恢复 manifest 实读 SHA256 `123a53ecf9461d65ddcafe43bf73e3edb16278f9fc5ad1b84e014547b079a76b`。
- [验证记录](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/docs/evidence/data-remediation/20260927-dg04/validation.json) 所列 5 个代码/测试文件 SHA 全部与当前代码吻合；依赖记录为 DuckDB 1.5.5、PyArrow 25.0.1、pandas 3.0.5。未修改代码、生产或快照，未执行旧测试、三会话算术参考或 30 证券重放。

## 唯一阻断：发布计划未固定其初始前置递推依赖

`remediation.inputs()` 仅包含 daily 文件、universe、lifecycle、calendar、daily facts（[代码](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation.py:126)）。`execute()` 校验该 source 与本计划 completed 日期的输出，随后从当前 catalog 调用 `_previous_session_states()`（[调用](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation.py:298)）。后者会选择最新健康、同规则前置发布，并在停牌时递归读更早状态（[实现](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/limit_events.py:830)）。计划没有保存这些前置输出、发布选择依据或递推状态的摘要。

因此在计划创建后、首个批次开始前，或者恢复重试期间，前置发布的事件/边界/健康状态变化不一定改变 source；旧计划仍会接受不同前史。单写者排除了同时写入，不能让这个遗漏的依赖成为已被计划验证的输入。当前生产只读查询确认 2022-02-10 确实存在 `cn-a-share-limit-v8`、published、非 stale 的前置发布；**没有发现当前生产该日已漂移**，阻断针对计划与恢复门槛的可重现缺口。

最小新增隔离重现：[脚本](/home/ubuntu/aspool-labs/dg04-gate-review-20260927/reproduce_predecessor_drift.py)，[结果](/home/ubuntu/aspool-labs/dg04-gate-review-20260927/predecessor-drift-result.json)。使用一证券、三目标日与五条预热行，先发布前两日，再为第三日建计划；将第二日已发布 consecutive_up 从 1 改为 99，未改变任何 raw/facts。结果：`source_unchanged=true`，execute 成功 `next=1`，第三日 consecutive_up=100，未漂移应为 2。运行退出码 0。初次构造缺少预热行，落入上市窗口 UNKNOWN，没有产生可修改事件；保留该初次隔离目录，增加五条预热后才得到有效重现，没有重复既有验证。

精确运行命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src ASPOOL_ROOT=/home/ubuntu/aspool-labs/dg04-gate-review-20260927/predecessor-drift-pool-v2 /home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/.venv/bin/python /home/ubuntu/aspool-labs/dg04-gate-review-20260927/reproduce_predecessor_drift.py
```

必要修补限于依赖绑定：在固定计划中记录初始前置递推状态及选择它的发布指针、规则、stale 和所需更早停牌递归依赖；执行/恢复时在池锁内验证这些依赖未变，或使用已固定且验证过的前史。可保守绑定首目标日前的相关发布表前缀，避免引入新递推算法。不能仅校验 2022-02-10 的 events 数量或一项连板值。更新实现/计划签名后，只补本缺口及相关恢复路径验证，不要求重跑旧套件或全范围参考。

已完成日期被外部标 stale **不是缺口**：`output_version()` 明确包含 `daily_limit_staleness`，逐个 completed 校验会拒绝；现有专项测试也覆盖此行为。

## 修复批准的依据

1. 独立来源与范围：31 个 BaoStock evidence 文件的 SHA 全部吻合；逐条读证据并核对 594 个计划 record 的 OHLCV、pre_close、is_st、status、turnover_rate，零不匹配。记录中 587 条 TRADING、7 条 SUSPENDED；[候选裁决](/home/ubuntu/aspool-labs/dg04-20260927/candidate-dispositions.json) 为 465 supplement、57 unconfirmed。数据适配器请求 `adjustflag="3"`（[代码](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/tdxman/baostock.py:220)）；既有 TDX 候选与新 BaoStock 是不同来源渠道，按已记录容差交叉核对，并非声称两个上游完全独立或数值逐位相同。
2. 保留有效原始事实：`apply_symbol()` 只在原始行不存在时填 OHLCV；已有行只填缺失 optional 值，再交既有 merge 计算受影响派生（[代码](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation_repair.py:50)）。复用 [修复验证](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/docs/evidence/data-remediation/20260927-dg04/repair-validation.json)：465 新增、87,240 个既有行 OHLCV 保留、748 个 raw/派生变化、594 日期事实变化，四次 8/16/24/30 续跑最终 inventory 一致。
3. 122 行 000723 的 [额外源核验](/home/ubuntu/aspool-labs/dg04-20260927/existing122-source-check.json) 显示主行情匹配，只补缺失字段。58 个有效主行情冲突不覆盖；57 个候选无独立确认不写入；另一个无效主行情且查询为空的冲突维持未知。7 个 SUSPENDED record 不写交易行。只读核对发现这 7 日原日期事实已有 BaoStock SUSPENDED、pre_close=NULL，计划填参考价等缺值；另外 587 个计划日期没有已有日期事实，未发现覆盖已有效的相反日期事实。
4. 输入、恢复点、代码与证据漂移会拒绝：修复每批完整 inventory 校验，pending 仅接受已演练 before/after_daily/after；外部 JSON 不能越过 catalog 权威 ledger。每次执行核对所有 evidence SHA、恢复 manifest SHA、实现版本与 repair_code。生产入口已要求 plan/source 相符的协调者文档，此为现有执行约束，不是新增用户审批。

## 崩溃窗口与发布语义

| 窗口 | 审查判断 |
|---|---|
| 修复单文件准备/替换后、catalog 未提交 | DG01 持久 manifest、redo 与行证据前滚；修复先 `recover()`，后核对精确 inventory。覆盖元数据、stale、版本与变更证据在同事务提交。 |
| 单文件 catalog 已提交、pending 未归档 | DG01 比较逻辑版本、已提交记录及实际文件摘要，归档前滚；不会当作原始状态重写旧文件。 |
| daily 已完成、facts 未提交 | 精确匹配 after_daily 后重放，已有 daily 为 no-op，facts 继续。现有专项测试覆盖。 |
| facts 事务中或提交后、修复 checkpoint 前 | facts/行证据/stale 同事务；回滚时仍是 after_daily，提交后精确匹配 after 并完成该步。 |
| 单日输出提交前 | 八类输出、发布指针、清 stale 同一事务；事务失败不出现半个新日。 |
| 单日输出已提交、catalog checkpoint 前 | pending 保持旧 next，重算该日；不会跳过。前置依赖缺口须先修补。 |
| catalog checkpoint 已提交、外部镜像前 | catalog 自提交是权威；允许外部镜像等于当前或上一 checkpoint，或缺失，再以 catalog 恢复。 |
| 外部镜像写入中/写完后 | 临时文件 fsync、原子 replace、目录 fsync；失败留下旧或新镜像。镜像不能伪造跳步，plan/state 摘要不一致拒绝。 |

依据：[DG01 文件协议](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/change_protocol.py:287)、[facts 事务](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/change_protocol.py:608)、[checkpoint](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation.py:93)、[每日事务](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/limit_events.py:977)。以上未逐个新增进程退出测试；采用源代码审查及已成功验证证据，不能称全断电窗口已实测。

日期和边界：发布计划 1,124 日、2022-02-11 至 2026-09-24，生产 calendar 同范围开放日数亦为 1,124；最早变更后缀扩展正确。代码保留 v8 处理 UNKNOWN/INVALID、确认停牌、IPO 首日和 gap/streak 状态的语义；各批次恢复完整上一发布状态。复用 [13 项测试日志](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/docs/evidence/data-remediation/20260927-dg04/targeted-tests.log) 与 [非空边界测试源码](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/tests/unit/test_remediation_publication.py:169)，其 reference/gap/boundary 确为非空且与保守参考一致。三真实会话的这些表为空，不能替代非空测试；该参考也共享现有算术内核，不是独立重实现。

## 接受范围与后续

本修复批准仅对应固定提交、当前 flat 单证券文件布局和具体计划。已只读核对全部 30 证券各只有一个 `bars.parquet`：多年度多文件中途提交不在本次证据覆盖内，不能把本结论推广到未来 yearly 布局。实现指纹未包含所有传递依赖（例如 store/change_protocol/daily_storage）；执行须保持本次冻结提交，不能单凭较窄的运行时 implementation 字段允许换代码。

catalog 为可信单写者权威，摘要用于完整性/漂移检查，不是抵御可同时改 catalog 及摘要的恶意写者。无效 JSON 可导致安全拒绝，不能称任意损坏均自动恢复。生产长范围发布与 Fundwise 消费检查仍未执行；保留既有 unknown/invalid，不能把 gate 通过写成全数据质量已解决。

协调者可先执行获批修复并核对最终 source；当前 publication plan 暂不执行。明确接受协调者提出的分阶段策略：先用冻结 a410d73 完成全部修复，再由原 owner 修补发布 guard、仅生成新的 publication plan；未完成修复前保留可续跑的冻结实现，避免共享 remediation.py 的修改导致旧 repair plan 版本门槛拒绝续跑。发布依赖修补后保持同一恢复点，再做聚焦复审；无需重新建立恢复快照、重跑 30 证券演练、增加广泛审批、跑旧基准或启动其他 worker。
