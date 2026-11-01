# DG04 聚焦复审：发布前史守卫与具体补充计划

日期：2026-09-27。最终结论：**批准具体发布计划 `36794742ac1e3c4a373c52773a77ec40449c9de2e676062b4c926f11fed061c5`，在冻结提交 `13d627c2d71dd73cf139f49b23448552469295f7`（生产实现为 `9308d55`）下按既有最多 256 日分批协议执行。** 具体 2,506 条补充修复已先获本次 gate 批准，并由 owner 完成；旧发布前史缺口已闭合，无剩余发布阻断。只读审查 owner checkout，无代码、生产、恢复快照写入，无新增 worker。

## 冻结对象

- 实现提交 `9308d5509ed0b0f378b94a098e0ea8b5357f65ff`；新增恢复测试提交 `13d627c2d71dd73cf139f49b23448552469295f7`。后者仅 tests/docs，生产实现不变。owner checkout `/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair`。
- `src/aspool/remediation.py` SHA256 `3fed8f0f39d899d74782c4cd4fa209ec2548045c027af01e4da84513278a0d9b`；当前测试 SHA256 `7b16f38c483aa3fc6d5b3b6723983d10cbc0ebb3b1c99adfbf8c8dc98d0ae76c`，与冻结验证记录吻合。
- 原修复执行回执 [production-repair-validation.json](/home/ubuntu/aspool-labs/dg04-20260927/production-repair-validation.json)：冻结 `a410d735...`、plan `13414aed...` 已完成 30 证券，748 行/594 facts，pending null，after inventory=`eff91eab10e43c3b1df52c78320f51c2e26653e99159d9320daca41a6e937bdd`，等于先前批准值。未重跑原修复验证。

## 唯一旧阻断已由语义绑定闭合

[prior_state helper](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation.py:172) 在同一 catalog context 调用原 `_previous_session_states`，保留完整 scope 返回映射中每一项的日期、up、height、gaps，以及停牌递归恢复的完整结果。[规划](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation.py:225) 将该完整映射的规范化签名纳入不可变 plan；实现存储的是完整映射的 hash，并非 map 明文，也不是仅保存事件数或单一高度。

[执行](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/src/aspool/remediation.py:286) 在池写锁内，源校验后重新读取初始前史并比对 hash，先于 remediation ledger、checkpoint、首次 stale 和每日发布写入；每次 resume 同样执行。之后仍按既有 completed output 指纹验证，再恢复当前批次的递推状态。固定 raw/facts/calendar、scope/axis、规则和实现，加上初始完整语义状态与已完成输出校验，固定了实际计算输入。

接受语义完全不变的历史物理变化，不要求整个历史前缀物理 hash。该设计允许只有无关输出或物理排列改变、而上述计算输入不变的情况；不扩大到 DG05 或改动递推算法。运行时 implementation 指纹范围沿用原实现，因此生产必须继续使用本次冻结代码；不能仅凭局部指纹容许任意传递依赖修改。

## 新增证据与复用边界

- [四类前史漂移测试](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/tests/unit/test_remediation_publication.py:256)：直接 predecessor 高度 1→99、gap 漂移、前缀 stale 改变选择、停牌递归依赖高度漂移。均在首次发布前拒绝，断言状态文件不存在且 stale/publication 不变。源码确认 ledger 写入也在 guard 后。
- [新增恢复测试](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/tests/unit/test_remediation_publication.py:306)：真实计算得到非空初始高度 2，`max_days=1` 后 `max_days=2` 得到 3/4/5；另一参数在批间将原始 predecessor 高度 2→99，拒绝且 checkpoint 字节、stale、publication 指针不变。
- 复用 owner 的 [14 passed 日志](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/docs/evidence/data-remediation/20260927-dg04/predecessor-guard-tests.log)（13.24 秒）及 [仅两个新增恢复用例日志](/home/ubuntu/orca/workspaces/tdxman/aspool-dg04-repair/docs/evidence/data-remediation/20260927-dg04/nonempty-prior-resume-tests.log)（2 passed, 14 deselected，2.99 秒）。实读测试代码和 SHA；本 reviewer 没有重跑这些测试，也未增加 guard 复现，因为现有新增证据已覆盖缺口。
- 继续复用此前报告的 current-source 审计、30 证券演练、3 日算术参考、13 项成功测试和崩溃窗口代码审查。本次没有重跑这些工作，亦不把复用日志表述为独立重执行。

## 新增补充修复 gate：批准

协调者通过 `msg_3df3700a885f` 明确新增本范围。批准 [production-base-gap-plan.json](/home/ubuntu/aspool-labs/dg04-20260927/production-base-gap-plan.json)，plan_id=`107fcabe21d55519917e6987e1483b4118dfc2ab1201c7f351a0a47003187bff`，文件 SHA256 `9dc801e6cec3847ebbc46aa9ce58fd98195338c483a7fe2a4a82861e8a861e44`，source=`eff91eab10e43c3b1df52c78320f51c2e26653e99159d9320daca41a6e937bdd`，expected after=`ff7cff8b970e2b9290dd2bb855211e9763552f831c28a52b5e065c1b55a00587`。限既有 single writer 和最多 8 证券分批协议；已通过 `msg_c76c1bb6639b` 向协调者明确批准，不由本 reviewer 执行。

新增只读核查 [脚本](/home/ubuntu/aspool-labs/dg04-gate-review-20260927/review_supplement.py) 与 [结果](/home/ubuntu/aspool-labs/dg04-gate-review-20260927/supplement-review-result.json)：245 个证券、2,506 条记录均与 245 个独立 BaoStock evidence 文件的 OHLCV/pre_close/is_st/status/turnover_rate 对应字段一致，所有文件 hash、plan 内容签名、245 步 before/after 连续性、实现/repair_code 和 recovery hash 均通过。记录全部 TRADING，pre_close 有限且正，is_st 为布尔；逐记录价格差 <0.011、量额相对差 ≤0.005 的交叉核对通过。此为已明确的来源容差，不声称不同上游数值逐位相同。

按 DG00 基础快照加 post-initial delta 重建只读 before 文件选择，对比 owner 已有 isolated reference：519,796 条既有行日期键、OHLCV 逐字段不变，既有有效 pre_close/is_st/turnover_rate/trading_status 不变，无新增 raw 行；45 条已有日期事实与计划有效值一致，未发现有效事实覆盖冲突。未再执行 prepare/replay。其余 2,461 条为新日期事实，与 owner rehearsal 的 2,726 raw/派生变化、2,461 facts 变化对应。10 条有效 pre_close 冲突保持 defer，3,695 条无独立确认保持 unconfirmed；均不在计划中。

恢复增量 [manifest](/home/ubuntu/aspool-labs/dg04-20260927/recovery-after-initial-manifest.json) SHA256 `df0c77fa3d78d4d34dbb7413574f442bea2cceaa53915ba0d799576f55a8f9a6`：本次独立读取并核验全部 77 对象（catalog、30 变更日线和 change-state）的大小与 SHA，全部吻合；基础 manifest hash 仍为 `123a53ecf9461d65ddcafe43bf73e3edb16278f9fc5ad1b84e014547b079a76b`。复用原基线已验证恢复，新增核查证明对象完整性，不将其称作完整灾备/RTO 演练。

## 最终发布计划静态绑定

[production-final-publication-plan.json](/home/ubuntu/aspool-labs/dg04-20260927/production-final-publication-plan.json)：plan_id=`a3fc2e4e59492c0f97d47dfb14fecf6d7cbce8e0033204feb4471c95e0e81d4b`，文件 SHA256 `cb5859c122a0d7b73ec53ec7d327ce9e5790e4ba2c6b297999a0b4e68bc271a8`，expected source=`ddd0c37c09a299c8addaf9e4f09ba6928178079f1ced11f960b463018ab1e44b`，initial prior hash=`00aaa72f98803d6cecb5370d478db204675e89342eda36e6e51055764e4a1ec8`。

本次只读 [核查结果](/home/ubuntu/aspool-labs/dg04-gate-review-20260927/final-plan-review-result.json)：签名、实现、原恢复 manifest 匹配；5,586 scope 与 6,479 日 axis 与已审旧计划完全一致，所有新补数日期均在原 axis；最早新补数 2021-09-24，完整发布后缀为 2021-09-24～2026-09-24 共 1,213 日。对 isolated repaired reference 重新调用原 prior reader 得到 4,372 个映射项，日期均为健康前置会话 2021-09-23，完整签名匹配。尚未用这些静态证据代替实际生产 source 回执。

原任务预期 publication source `9dff73146110836d3f5427fbdfd1b2f687baf504f4d9e7656b4c75eb3455d6de` 属于已完成第一阶段 30 证券修复；现在 `ddd0...` 的变化由随后明确新增的 2,506 条补数解释。repair inventory 与 publication inputs 摘要算法不同，不直接比较 `ff7cff...` 和 `ddd0...`。

## 最终 gate

**APPROVE publication**，仅对应以下最终对象：

- 最终实际生产计划：[production-authorized-publication-plan.json](/home/ubuntu/aspool-labs/dg04-20260927/production-authorized-publication-plan.json)。plan_id `36794742ac1e3c4a373c52773a77ec40449c9de2e676062b4c926f11fed061c5`，文件 SHA256 `a24d04decd729f9aa90fd53b04568549f1f9e5920161e2b9ce083d323f9fdc96`。
- 冻结 checkout `13d627c2d71dd73cf139f49b23448552469295f7`，实际实现 `9308d5509ed0b0f378b94a098e0ea8b5357f65ff`、remediation.py SHA `3fed8f0f39d899d74782c4cd4fa209ec2548045c027af01e4da84513278a0d9b`；结束只读检查 owner 工作树干净。相对原 `a410d73`，生产源码仅本守卫 18 行增量。
- 实际 publication source `ddd0c37c09a299c8addaf9e4f09ba6928178079f1ced11f960b463018ab1e44b`；完整 initial prior hash `00aaa72f98803d6cecb5370d478db204675e89342eda36e6e51055764e4a1ec8`。仍为 1,213 日、2021-09-24～2026-09-24，最多 256 日一批。
- 最终恢复清单：[recovery-before-publication-manifest.json](/home/ubuntu/aspool-labs/dg04-20260927/recovery-before-publication-manifest.json)，SHA256 `0231763157911fea7b6d24590db2b359dcd0a7541850220107b590d818a4e237`。该计划相对已审条件计划 `a3fc...` **仅**改变 recovery_manifest、recovery_sha256 和因此变化的 plan_id，其余计算语义完全一致。

新增实证：[production-base-gap-validation.json](/home/ubuntu/aspool-labs/dg04-20260927/production-base-gap-validation.json) 记录实际 245 证券补修完成，after inventory=`ff7cff8b970e2b9290dd2bb855211e9763552f831c28a52b5e065c1b55a00587`、compute source=`ddd0...`，分别与修复 after 及最终发布 source 精确匹配。这里复用 owner 实际全量 source 指纹回执，未重复整池 source 审计。

Reviewer 另以只读连接直接查询生产 catalog：权威 remediation ledger 的签名正确，245 项 completed 与计划证券序列逐项一致，next=245、pending=NULL，累计 changed_rows=2,726、changed_facts=2,461；实际 stale 1,213 日且范围吻合。重新读取实际生产 4,372 项初始 prior，完整 hash 与最终计划相等。并独立重算最终 plan 内容签名、实现/依赖绑定、恢复 manifest hash，逐对象核验最终 812 个恢复增量对象的大小与 SHA（总计 997,684,831 字节），全部通过。详见 [最终实际 gate 核查结果](/home/ubuntu/aspool-labs/dg04-gate-review-20260927/actual-publication-gate-result.json)。

此批准允许协调者按现有 single writer / recovery / plan-source gate 签发具体执行文档并让 owner 发布，无需扩大审批或另跑旧验证。保持已验证恢复对象与后续 change-state/ledger，沿用前滚恢复协议。没有剩余 actionable blocker；本审查完成不等于长范围发布已经完成，生产发布结果及 Fundwise 消费结果尚待执行后验证。
