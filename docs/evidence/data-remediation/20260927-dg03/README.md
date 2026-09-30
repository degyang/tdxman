# DG-03 原始证据

固定输入 manifest SHA-256：`123a53ecf9461d65ddcafe43bf73e3edb16278f9fc5ad1b84e014547b079a76b`。

`/home/ubuntu/aspool-labs/dg03-20260927` 保存大数据库、原 Arrow schema inventory、失败及中间数据。这里只提交可审阅的小型日志、JSON query plans、逐操作样本、约束版本和源码/测试记录；数据库不进入 Git。`samples.jsonl` 是按执行顺序追加的原始样本，`summary.json` 由 `scripts/dg03/summarize.py` 从该文件生成；失败记录不会过滤掉。

主要证据对应：

- `schema-parity.json` / `parity.log`：7,288 个原文件、17,862,387 行的原始 40 列精确比较，候选另含 2 个路径键；18 张原 catalog 表双向等值、所有原约束保留。这里的 18 排除新增 `dg03_*` 表。
- `reads-initial.log` / `reads.log`：初始日期排序布局的完整公开字段对照，第一轮五年完成，重复轮经协调者取消。退出中断不是数据不等值，也不能记作整个三轮阶段通过。
- `diagnose.log` / `code_only.json` / `code_market.json`：ART 条件选择和旧 Pandas overlay 开销。`tune.log` 的 old 是初始 DuckDB，不是 Parquet；`verify-overlay.log` 的 old 才是原公开 Parquet API。
- `events.log`：实际五年有界事件迭代器的批次、金额样本、RSS、清理与完成标记。P0 已通过，不因交接或重叠时间重新运行。
- `prepare-*`：完整增长构造，包含整份追加和源年提交的 SQL1GB COMMIT 失败；后续固定证券集合提交保留完整 schema、PK、ART。构造成本与日常查询/写入成本不同。
- `growth-*` / `facts_*` / `mutations` / `recovery`：按实际完成阶段归档；以样本 `status`、断言和末尾 phase 结果判断，不能仅凭文件存在判为成功。

资源解释：构造期部分和 DG04 同时运行，nice19/ionice idle、单线程、SQL1GB、RSS3GiB采样中断守卫；它不提供无竞争构建性能。协调者 11:23:39 UTC 明确释放独占槽位，之后 DG03 各重型进程顺序运行。早期诊断/P0/排序/窗口可能与 DG04 重演有重叠；不把这些观测当作 SLA。

`operator_rows_scanned` 是算子计数；日期 row-group 上界不等于实际设备读取；`read_bytes/write_bytes` 是 Linux 进程内核 I/O；`maxrss_bytes` 是进程历史高水位，`peak_sampled_rss` 是当前操作采样峰值；100ms磁盘采样可能漏短峰值。备份/恢复是本机进程崩溃和完整文件副本，不证明断电或异机灾备，也不同于旧 Parquet 后端回退。

最终裁决见 [architecture-decision.md](../../../design/architecture-decision.md)：两种原型均不批准生产迁移。任务交付是可运行实现与完整评估，不把迁移准入失败编码成全部门槛通过。

新增备选证据：

- `monthly-build-{1,2,5}x.log`：完整 schema 与真实偏移冷历史；前缀复用哈希与新月逐值/type/日期/key 核验。`monthly-?x-build-manifest.json` 是构造完成时不可变基线，后续写入根仍保留这些旧文件。
- `monthly-reads-*`、`monthly-single-bounded-1x.log`：纯 `api_seconds` 与含 assert 的外层秒数分开。初版单证券慢样本保留，物理键下推只重测受影响路径。
- `monthly-events.log`：月文件路径自己的实际五年 82,772 事件/258 批/33 个原金额样本，不能借用 native 的 P0 代替。
- `monthly-bounds-*`、`monthly_*_profile.json`：全部 42 列的真实窗口与单证券物理读取；前缀未变不代表全部单证券历史免费。
- `monthly-writes-*`：完整整日原子新增、全列对照、12 次修订/no-op、删除/回补、checkpoint。`monthly_rewrite_bound` 记录实际 changed 月份/行/文件字节，`monthly_cold_files_unchanged` 记录冷月 manifest/path/SHA 与 inode/size/mtime 均未变。不能把这些当成整 catalog 没有写放大。
- `monthly-crash-fixture-relative.json`、`tests-monthly-relative.log`：实际 SIGKILL 前/后多月与 facts 联合发布、六域状态与跨月 coverage。`monthly-recovery-full.log`：真实 active 新根恢复、全部文件哈希、发布阻塞读者、非空事件版本失效。
- `tests-monthly-facts-noop.log`、`tests-monthly-symbol.log`：facts-only+raw-noop 不重写月份；证券/ETF/空集合/未知符号选择与下推计划。最初 fixture import/期望错误日志仍保留，修正后只重跑受影响用例。
- `native-5x-remaining.log`：仅补点更新与 checkpoint；不将5x失败整日 append 写成成功。`history-profile.log` 明确真实公开年代物化扫描合计66,102,229。

`inventory.json` 由 `scripts/dg03/archive.py` 生成，给出每个小文件哈希、当前源文件哈希、源码 HEAD、24 个定向测试名称和大型数据库位置。它不宣称 24 项在最终 HEAD 一次全跑；各实现按变化选择检查、原始日志保留。`samples.jsonl` 中每个阶段 provenance 的源哈希/版本/commit 是实际运行绑定，最终归档提交会另含 docs 和证据，不倒推旧样本是在该提交重跑。

源码接续：`7539622` 共用规范化与存储注入（6 个受影响原候选测试通过）；`1b8c751` 月发布、相对 manifest、三项新 fixture 检查通过；`22748a7` facts/no-op 专项通过；`3805d41` 物理证券下推专项通过，日/全市场 ETF/事件路径未变。`2a21555` 根据实际原始行生成合成 coverage；后续 benchmark 提交记录新增核验与归档。当前源码与这些提交的对应关系可直接由 Git 和 inventory 复核。

限制：3 次样本不是 P95；未测生产消费者切换、额外月方案双倍证券完整根、跨机灾备、断电、自动 orphan GC 或切回旧物理 Parquet 的全量保真导出。双倍证券完整35字段压力已在 native 根实际运行，monthly 窄日期与合成历史另有完整规模证据；不互相冒称替代验证。

`c4f3406` 修正比较线程设置：MonthlyStorage 不覆盖 daily 调用者原2线程；新测试同时核验1/2线程保持。ETF adapter显式保留原实验1线程，已测事件批显式配置1线程，所以这两个已通过工作负载的实际配置未变。1x只补公开day/60/single的threads2样本；初始1线程样本不得混入同配置中位数。

`native-5x-one-thread.log` 是独立1线程/同1GB预算的配置反事实，65.660秒后同样COMMIT pin失败。首版harness的异常分类未匹配TransactionException，故phase真实标失败；保留完整日志，只用 `native_one_thread.py --verify-only` 补预期12次旧修订和其余域的只读状态核验，见 `native-5x-one-thread-state.log`，没有重跑此写负载或声称有该次预先保存的状态hash。
