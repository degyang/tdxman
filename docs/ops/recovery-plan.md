# DG-00：恢复基线与实施前置条件

日期：2026-09-27。适用本期单机整改，不代表已建立生产灾备 SLA。

## 可复用的恢复点

已验证快照：`/home/ubuntu/aspool-recovery/20260927-data-remediation/snapshot`；清单与完整恢复副本在同级目录。清单 SHA-256：`123a53ecf9461d65ddcafe43bf73e3edb16278f9fc5ad1b84e014547b079a76b`。

[原始演练](../evidence/data-remediation/20260927/baseline.json)包含 catalog + lake 全部 13,742 文件、2,702,716,946 字节，独立复制后逐文件哈希、股票/ETF/指数/限价/证券事实公开接口核验。全量恢复与校验 10.349 秒，指定证券文件恢复与校验 2.636 秒；仅一次本机热缓存观测，非 P95、故障发现时间或生产停机承诺。

[本次复核](../evidence/data-remediation/20260927-dg00/baseline-verification.json)使用 [只读核验脚本](../../scripts/aspool_verify_baseline.py) 比对快照、生产池与既有清单，没有为补文档重新复制全池。此全量哈希是一次整改检查点，不放入日常更新或 Fundwise 缓存检查。

范围：数据库和 lake 中日线、指数、ETF、日期事实、派生及其他已有数据。reports 留在原池，未纳入此快照；既有冲突报告和旧日线/v7 证据不能因此删除。这个副本与源同机，不能抵御主机/磁盘损坏。

## 工程门槛与尚未确定的业务目标

| 项目 | 本期执行规则 | 验证状态 |
|---|---|---|
| 修复/切换回退数据损失 | RPO=0：暂停生产写入后确定恢复点；有后续写入则必须保留并重放，不能直接覆盖为旧池 | 工程门槛；本次未进行切换 |
| 本机恢复时间 | 先保留实测值；含故障发现、协调和服务恢复的 RTO 尚未由业务确定 | 不把 10 秒副本校验当 RTO 达标 |
| 允许最新行情延迟 | 尚未确定 | 不自动延长维护停机，不在无预算下切换生产 |
| 历史追溯/错误发现窗口 | 本期保留现有完整历史、修复前来源和未结证据 | 不用固定 7/30 日删除恢复点 |
| 异机/异盘恢复 | 尚未建立和验证 | 生产迁移前 DG-07 明确是否需要及恢复目标 |

这些未知项不妨碍隔离开发和旧后端回归测试，但生产切换/关键恢复点退役不能据此标记已验收。DG-00 的交付是可核验基线、契约、清单和已知缺口；业务 SLA 不由工具性能数字代替。

## 恢复与验证步骤

1. 记录事件及受影响池；停止/协调所有写入者，保留失败现场，检查活动 WAL。不要删除 WAL 或在写入中复制数据库。
2. 核验 manifest 与所有文件哈希；把 snapshot 普通复制或写时复制到新的独立目录，禁止可写硬链接。源和目标不可嵌套，提前核算磁盘空间。
3. 在恢复目录核对清单、表行数、键/字段、发布指针关联、公开读取和错误状态；把原有 stale/unknown 当恢复基线保留，不能为通过检查清零。
4. 单证券/日期误改优先在独立目录演练局部恢复。恢复日线同时核对目录元数据及派生依赖；原演练的“恢复一文件”不证明任意生产局部恢复都可单独替换。
5. 如果基线之后仍有写入，保留其来源/变更并重放。重放未验证时继续使用当前有效池或保持协调停写，不能覆盖丢失新数据。
6. DG-07 才执行受控切换，完成消费者检查后恢复写入；DG-08 按引用和恢复目标决定退役，不在此次开发顺带清理。

生产核验命令（证据目录必须在池外）：

```bash
.venv/bin/python scripts/aspool_verify_baseline.py \
  --root /home/ubuntu/.aspool \
  --manifest /home/ubuntu/aspool-recovery/20260927-data-remediation/manifest.json \
  --output docs/evidence/data-remediation/<run_id>/baseline-verification.json
```

脚本报告 added/missing/changed；源有变化不等于损坏，应先解释差异并重建适用恢复点。快照哈希失败返回非零；后续生产动作必须显式检查 `source_matches_baseline`，不能只看进程退出码。

## DG-01 新增恢复状态

2026-09-27 隔离实施新增 catalog 内 `business_revisions/business_changes/catalog_change_rows`、coverage 元数据旧新值/序号、fetch health 与任务聚合，以及 `change-state/**` 准备/未决/完成行证据。恢复点必须同时包括 catalog、lake 和 change-state，不能继续把后者当普通 reports 排除。snapshot/verify 工具已适配；旧 DG-00 已验证恢复点继续保留，不为补文档复制全池。

文件写先持久 preparing，原子转 pending 后才替换目标；catalog 的 coverage/stale/日志/修订同事务提交，跨文件及跨 catalog 不是整体事务。源 writer 在写锁内前滚，公开读遇 pending 返回 RECOVERY_REQUIRED；完成后仅保留字段证据，不保留每代完整冷历史。snapshot 的裸独占锁不隐式恢复源，pending 时拒绝拍点。必要 manifest/redo/证据损坏则保留阻断，恢复可信对象或独立恢复点后才能继续，不能清状态假称成功。

细节、故障窗口、真实进程退出及边界见 [DG-01 实施报告](../achievements/dg01_implementation.md)。没有通用旧值自动回滚工具，也没有断电/异机/生产 SLA 验收；字段旧值是审计/恢复依据，默认支持的故障恢复方式是同一 prepared 操作前滚。
