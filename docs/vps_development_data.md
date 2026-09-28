# 数据先行：可迁移的 VPS 开发环境

2026-09-28 用户调整优先级：先完成数据迁移与异机开发数据准备，再开展派生计算和接口接入。新版数据统一放各设备主项目 `tdxman/data/`；任务 worktree 显式引用主项目数据，不隐式生成另一套池。

2026-09-28 M0 已交付：本机及 JakartaVPS 均有经验证的 `data/_staging/fw02/`，详情见[M0 执行记录](sqlite_migration_m0_execution.md)。

当前：本机已完成日派生并安装到 `data/`，仅 stocks.sqlite、catalog.duckdb、lake；报告和恢复源移到 `.local/`。Jakarta 尚为旧 M0 副本，待新版全量复制。当前操作与验收见 [FW-04](fw04_sqlite_public_api_execution.md)，以下保留 M0 原始操作记录。

## M0 历史交付边界

第一步迁入原始股票日线、来源日期事实、原始公司行为和来源累计因子。四表 schema 已创建；`daily_features` 只完成来源字段和基础有效性分类，计算字段未就绪，`market_daily_summary` 保持空表。不能因为 SQLite 可以打开就切换 Fundwise 或声称日频链路验收通过。

`source_pre_close` 不承接旧本地推导值；旧已发布参考价仍在冻结源中，待 FW-03 核对依赖并重建。当前名称不倒灌历史 ST。股息事件与来源累计因子分开保留，不进行重复累乘。

## M0 历史目录与复现（不要用于当前运行目录）

```text
tdxman/data/
├── _staging/legacy-source/     冻结旧池：catalog + lake + SHA-256清单
├── _staging/fw02/
│   ├── stocks.sqlite          全历史事实迁移目标，尚非生产库
│   └── _reports/              迁移输入、逐股进度、字段差异和结果
└── _reports/migration-bootstrap/  tmux任务日志、退出码和资源记录
```

下列构建命令在持有冻结源的设备上运行。其他 VPS 接收已封闭并校验的工件后，直接使用读取入口或校验命令，无需再次下载旧池、重迁股票历史。源与目标必须位于不同目录树；已有目标不会被静默覆盖。

```bash
uv sync --extra dev
.venv/bin/python scripts/ops/snapshot_development_data.py \
  --source-root /home/ubuntu/.aspool \
  --target-root "$PWD/data/_staging/legacy-source"

.venv/bin/python scripts/ops/migrate_stocks_sqlite.py \
  --source-root "$PWD/data/_staging/legacy-source" \
  --target-root "$PWD/data/_staging/fw02"

.venv/bin/python scripts/ops/stage_development_public_data.py \
  --source-root "$PWD/data/_staging/legacy-source" \
  --target-root "$PWD/data/_staging/fw02"

.venv/bin/python scripts/ops/export_legacy_reference_candidates.py \
  --source-root "$PWD/data/_staging/legacy-source" \
  --target-root "$PWD/data/_staging/fw02"

.venv/bin/python scripts/ops/verify_stock_migration.py \
  --target-root "$PWD/data/_staging/fw02"
```

源快照在旧池目录共享锁下复制，拒绝待恢复操作、未关闭的 DuckDB WAL 和符号链接；只复制 catalog/lake，不携带旧报告、已应用操作日志及备份。每个文件的源/目标哈希一致后才写完成清单。它是当前开发与迁移输入快照，不替代生产恢复演练。

迁移按证券事务写入并在提交前逐字段回读比较；日期等非唯一辅助索引在离线导入末尾统一创建，主键与唯一约束始终保留。中断后以同一命令追加 `--resume`；源清单、迁移实现和 schema 必须不变，否则应使用新隔离目标。已完成目标再次运行会核对源与数据库哈希，复用原验收结果，不重写索引。进度与差异记录在库外，不增加业务任务表。SQLite 采用 WAL/FULL、32 MiB 页缓存；完整库体积和实际 RSS 以运行报告为准。

公共配套目录只携带 ETF 原始日线、指数、基本面及原因子文件。公共 catalog 保留 universe、security_lifecycle、security_calendar、index_coverage，以及只含 ETF 的 coverage；不复制旧股票 publication/批次/派生表，也不复制旧股票 Parquet。旧参考价另导出为 `_reports/legacy-reference-candidates.parquet`，明确标为待核对计算证据，不进入新库来源事实。

股票读取在此阶段使用明确的 SQLite 入口；旧 `DataPool.read_daily` 尚未切换，不能用它读取新库股票。下面的基础读取可供后续派生开发使用，指数和 ETF 仍走旧公开接口：

```python
from pathlib import Path
from aspool import DataPool
from aspool.sqlite_stock_store import stock_connection

root = Path("data/_staging/fw02").resolve()
with stock_connection(root) as conn:
    bars = conn.execute(
        "SELECT trade_date,close,amount FROM daily_bars "
        "WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        ("000001.SZ", "2025-01-01", "2025-12-31"),
    ).fetchall()

pool = DataPool(root)
index = pool.read_index_daily(symbols="000001.SH", end="2026-09-24", lookback=5)
etf = pool.read_etf_daily(symbols="159915.SZ", end="2026-09-24", lookback=5)
```

## 数据分类与来源保留

- 股票和 ETF 先按公共目录/生命周期区分。当前目录缺失的历史证券只能经项目现有 A 股代码规则识别，结果进入报告。
- 基金因子留在冻结原文件中，不进入股票库。旧因子中 `920xxx.SH` 与公共目录北交所身份冲突时，只有经目录核实才映射为 `920xxx.BJ`；原始市场/代码保存在因子载荷中，转换逐项记录。
- `volume/vol`、`turnover_rate/turnover` 有差异时按既定公开读优先级选值，并在压缩 JSONL 中记录原候选。`float_share` 与 `float_shares` 分列保存。
- 非有限数、未知字段、键冲突、日期冲突不静默丢弃；迁移失败须解决后继续。来源冲突与尚未验证的历史语义不因迁移成功而自动视为生产可用。

## 远端任务与 tmux

JakartaVPS 的 Orca 项目为 `/home/ubuntu/Services/tdxman`。运行环境安装已通过 tmux 执行。其他设备复用同样的相对目录与命令。

所有迁移、传输、校验、长测试及 Codex worker 均在远端或发起端 **tmux 会话**中运行。Orca 终端只连接会话、发送任务和查看结果；不能把耗时工作直接挂在 Orca PTY 上。

```bash
tmux new-session -d -s tdxman-task 'sh /absolute/path/to/task.sh'
tmux attach-session -t tdxman-task
```

任务脚本必须将输出写入文件，保存真实退出码；tmux 会话消失不等于任务成功。只重连同一会话，不因 Orca 重启再次启动相同任务。Codex effort 最高 `high`。已完成的验证证据随任务交接，除新改动、失败或异机特有风险外不重复运行。

## 首次同步与后续更新

1. 源端迁移完成、校验通过、SQLite checkpoint 并关闭连接后，记录库大小与哈希。首次复制的是封闭的开发工件；普通 rsync 不用于活跃数据库。
2. 先核对接收端剩余空间是否足够容纳库、必要公共域、暂存和后续 WAL，再决定传输集合。不把全套备份、样本和报告递归复制过去。
3. 数据进入接收端明确的开发目录，校验文件、SQLite schema/counts，再运行少量异机读取样例。`verify_stock_migration.py --reuse-source-integrity` 必须先通过完整数据库SHA-256/大小一致检查，才能复用源端成功的完整性结果；报告标明来源，不冒充远端重跑。默认不带该参数仍执行完整检查。股票、指数、ETF 和公共目录分别列明是否可用，不以单个 stocks.sqlite 宣称所有旧接口可用。
4. 开发接收库保持只读；写入测试使用隔离样本。初期本机仍为数据来源，不形成两台机器同时更新同一逻辑主库。
5. 源快照、代码提交和迁移结果一起记录，确保其他 VPS 可复现。后续日常活库增量复制仍属 FW-09 的 sqlite3_rsync 专项，不用首次文件传输冒充其验收。

本机 WSL SSH 使用已有加密私钥 `~/.ssh/id_ed25519_vps_wsler`，通过本地 SSH agent 解锁后传输；不将口令固化到脚本或项目，也不回显口令。Orca 的远端访问通道与 WSL 文件传输通道分别验证，不将前者连通误报为后者已可用。

## 派生成品进度（2026-09-28）

本机 `data/_staging/fw02/stocks.sqlite` 已完成全历史派生并通过完整校验，约 8.59 GiB；[证据](evidence/sqlite-migration-assessment/20260928-derived-result.json)。Jakarta 的相同相对路径仍是初始 M0 工件。新成品同步等待 WSL wsler 私钥重新加载 SSH agent；不要将远端原始迁移副本误认作已更新的派生成品。传输应在 tmux 下执行，副本必须与成品 SHA-256 完全一致，才可复用其完整性校验。
