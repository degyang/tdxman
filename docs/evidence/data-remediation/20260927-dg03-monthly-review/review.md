# DG-03 monthly Parquet 独立只读审查

结论：所审草稿有 2 项 P1 正确性问题、2 项 P2 契约/写入范围问题，已发送协调者，修复仍归原实现 owner。本报告完成的是指定版本的静态审查，不是生产切换批准，也不把未完成的月度测试/benchmark 当作失败证据。

## 版本与方法

- 工作树：`/home/ubuntu/orca/workspaces/tdxman/aspool-dg03-storage`。
- HEAD：`97fd0efcddf552c550933f69bce7156badd29294`；下列月度文件为未提交草稿，不能仅以 HEAD 代表审查对象。
- 开始快照：2026-09-27 12:22:47.426493 UTC。私有源码快照及完整哈希清单：`/tmp/tdxman-dg03-monthly-review-snapshot/manifest.json`。
- 核心文件 SHA-256（下文行号均指该快照）：

| 文件 | SHA-256 |
|---|---|
| `src/aspool/dg03_monthly.py` | `48f18518374fd9101a93d74105e77424de0d1fccd241579e5bcac1f999282b0e` |
| `src/aspool/dg03_candidate.py` | `fddaa4ed4973f45e5e962192af2ad84a395eff74070b4a447c7d0a15b31bb800` |
| `src/aspool/dg03_etf.py` | `5c2751364549109ddacecbc3bc4074e17fdeea8d9b52473453e780aab7ad5bd2` |
| `tests/unit/test_dg03_candidate.py` | `aff7226e7724b3f8d9ee14c1d4d6e5c846d43503e604f3758acbdb6e5aafeb97` |

已在报告前检查一次当前 tracked diff 和新增 monthly 文件 diff，并再次核对上述四个哈希与 HEAD：仍与开始快照相同。支持性 `api_contract.py`、`limit_amount.py`、harness 测试与 benchmark 脚本哈希也在 manifest 中。未运行测试、benchmark、DuckDB 连接或数据重读；未访问生产池；未修改 tracked 文件或执行 Git 写入。以下复现场景是静态调用链推导，不声称实际执行过。

## 已证实问题

### F1 / P1：删除月边界记录可丢失仍有其他月份数据的 coverage

定位：monthly 242–247、271–293；candidate 485–523。

临时 `dg03_daily` 只装入 touched 月份，然后调用 `WorkPool.apply`。假设证券只有 1 月一行与 2 月一行，删除 1 月行：它也是全局 start_date，candidate 进入边界重算分支，仅看到临时表的 1 月数据已空，于是删除 coverage 行。monthly 后续从 untouched 月份找回 2 月行、算出 n>0，却只执行 `UPDATE coverage ...`，受影响行数为零，因此发布后实际行情仍在、coverage 已缺失。

需使跨月修复能重建被阶段逻辑删除的 coverage，同时保持原 `coverage(symbol)` 主键及来源语义。验收应覆盖删除 touched 月最后一条、其他月仍存在的情形；无需重复完整历史 parity。

### F2 / P1：月度事件金额 API 仍查询已不存在的全局表

定位：monthly 161–172、303；candidate 766–800、803–830。

`MonthlyPool` 未覆盖事件迭代器，继承方法固定构造 `CandidateEventAmountBatches`；其金额查询 `JOIN dg03_daily`。月度正式 catalog 不保留该表（发布前明确 DROP），因此有事件且请求 amount（默认字段包含 amount）的批次会因缺表失败，无法完成月度金额消费。

应将金额查询接到按 lo/hi 选择月份的存储路径，并保留精确 `(market,code,trade_date)` 关联、NULL 金额语义和原版本检查。当前 candidate `_versions` 将发布/stale token 与 `dg03_revision` 合并；基类每批前后和结束时检查版本，因此修复不能只替换 JOIN 而丢掉该检查。

### F3 / P2：月度 ETF override 丢失公开错误包装

定位：monthly 164–172；candidate 750–764；`api_contract.py` 16–28（SHA 见 manifest）。

父类 `read_etf_daily/list_etfs` 有 `@public_read`，月度 override 没有。ETF `_read` 在 bind 外层只捕获 ValueError（etf 88–90），其后也未统一捕获 DuckDB 错误。当 catalog 指向缺失/损坏月份文件等存储错误时，月度方法直接抛出原始 DuckDB 异常，而原公共方法会返回稳定的 `DataPoolError(DAILY_INVALID)`；缺文件的其他路径同理会丢失 POOL_NOT_FOUND 包装。成功路径的单位/字段复用不能覆盖错误契约差异。

### F4 / P2：混合批次重写完全无变化的月份

定位：monthly 178–180、199–224、294–302。

`touched` 来自所有传入 bars 行，no-op 检测只保存全批次 changed 布尔值；一旦任一行真的改变，所有 touched 月份都会产生新文件并更新 manifest。例：1 月 amount 修正 + 12 月同值重放，12 月被无谓重写；真实 facts 更新 + 任意 no-op bars 行也会重写后者月份。单纯全 no-op 的短路正确，但混合批次不满足按实际变化限定物理重写范围。

应以实际变化的 bars 月份决定 install 集合；验证冷月 path/hash 不变即可。允许实际变化月整月替换，不要求更细分文件布局。

## 各检查项的可确认范围

- **完整 schema / 35 公共字段**：月度 staging 和 install 都使用 `SELECT *`，绑定只把内部路径键映射为公开 market/symbol；没有主动缩成窄表。公共 read_daily 继续使用既有 DAILY_FIELDS、optional typed NULL、volume/vol 与 turnover fallback、overlay、50 万行限制及 attrs。原 small evidence 的 `schema-parity.json` 证明旧全局候选 40 raw + 2 key、18 原 catalog 表等值，不证明当前 monthly 的完整迁入。当前 initialize 不创建 empty.parquet，也没有本快照内的完整月度 builder，因此仍需 owner 的月度完整 42 列构造证据；这是在制验收项。
- **约束与键**：initialize 复制整个原 catalog；未看到放宽原 schema/constraints。写入阶段创建 `PRIMARY KEY(__market,__code,trade_date)`；install 校验月内日期范围及 distinct key 数量，manifest 用 month 主键防止一月多个活动文件。源 raw symbol/code 与内部路径键独立的设计保留。应在最终 builder 证明每月 schema/键非 NULL 与全量 manifest 完整性，不能由原 schema inventory 文件的存在代替验证。
- **namespace/date offset**：已读现有 bench 的完整 `SELECT * REPLACE`、40 年 trade_date 偏移，以及全局唯一六位合成 code 与 coverage 保持原 symbol 主键的构造；这些代码针对 `dg03_daily`，不能记作 monthly 增长构造已完成。raw date/datetime 等保留源值的压力数据语义不能改称真实历史，月度 builder 的日期归月、各倍率 namespace 和完整键验收仍待对应源码/证据。
- **文件裁剪**：MonthlyStorage.bind 117–129 从 catalog 的 first_day/last_day 选择显式文件列表，有真正的日期文件边界。绑定自身未使用 symbols/fields，但股票与 ETF 调用方仍执行日期/证券/asset 过滤，因此未发现成功结果的证券串读。无日期的单证券查询会绑定全部月份；当前不能宣称按证券裁掉月文件或已证明 row-group 有界扫描。需要实际计划/文件数/资源证据，不从 SQL WHERE 推断 I/O。
- **NULL / 单位 / no-op**：抽出的 normalized_row 与 diff 中旧逻辑一致：merge 缺值跳过、clear 显式清空、delete 显式删除；不把 NULL 补 0。全批次 no-op 在 225–234 返回前没有复制 catalog、改 revision/stale 或发布文件。其检查会读取 touched 月完整列，这是读取成本，非零物理重写。混合批次的问题见 F4。
- **ETF 与单证券 binding**：storage_type 注入接通股票 read_daily/status 与 ETF 查询；字段、ETF_START、规范符号、asset_type、成交量股/金额 CNY/换手百分比保留。错误包装缺失见 F3；事件路径未接通见 F2。

## 文件 / catalog / pending / recovery 的真实边界

monthly 235–317 的顺序是：持有根 pool 写锁 → 复制完整 catalog 到 work → 在私有 catalog 事务中应用变化、revision/audit/stale → 写新的不可变月文件并 fsync → 更新私有 manifest → DROP staging 表并 CHECKPOINT → fsync parts 目录及私有 catalog → hardlink 旧 catalog 到 history 并同步 → os.replace 发布 catalog → fsync 根目录。所有月份通过一个 catalog 指针集合一起变为可见；正常持锁读者不会逐文件观察混合新旧版本。完整 catalog 拷贝费用已明确披露，不能称为仅写某月成本。

当前没有 pending journal/状态表、重启恢复入口、孤儿文件分类/清理协议，也没有校验 manifest sha256 的读取/恢复入口。before_publish 失败可留下 work 与未引用的新 part；after_publish 失败时新结果已发布但调用未成功返回；旧 catalog/旧 parts 保留有助于人工恢复。不能由 hardlink history 或单个原子 rename 宣称自动恢复、事务重试身份识别、保留链完备或断电/异机灾备已验收。manifest 路径是绝对路径，恢复到新根目录还需处理路径引用；复制 catalog 本身不是自包含备份。

已读 `recovery_protocol.py` 与 harness，它们面向全局 `dg03_daily` 的 COMMIT 与联合六域证据，不面向月份文件 + catalog 替换。原 parity/P0/recovery 已通过且本审查没有重跑；它们不能外推上述月度协议。pending 设计/故障点覆盖尚未完成本身不计为新缺陷，最终状态须诚实标为待验收。

## 后续验收门与交付边界

原 owner 修复 F1–F4 后，应仅针对改动做月度 fixture/接口/故障点验证并附实际源哈希；最终完整 42 列与 35 字段对照、月文件日期边界、namespace/倍率构造、有界单证券/ETF/event 访问、实际资源门槛和多文件恢复结果由 owner/协调者补齐。本审查不等待这些结果，也不将旧全局候选已拒绝的 OOM 重列为月度新问题。

协调者据报告路由修复与综合结论；任何后续 owner 源码变更均超出本报告哈希绑定的结论范围。审查角色的请求已完成，生产后端仍无切换授权。
