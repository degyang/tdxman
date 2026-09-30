# 每日更新与尾部修补流程

入口是 `scripts/ops/run_daily_data_pipeline.sh`。它只调用公开 `aspool` CLI，因此可在交互终端、tmux、cron 或 systemd timer 中使用同一套流程。

两个 shell 入口（`run_daily_data_pipeline.sh`、`repair_recent_data_gaps.sh`）都先解析并进入项目根目录，
统一环境（流水线缺省 `TZ=Asia/Shanghai`，`UV_CACHE_DIR` 缺省在项目 `.local/uv-cache`），
优先使用 `.venv/bin/python`，否则退回 `uv run --frozen`。数据根缺省为项目 `data/`，
可用环境变量 `ASPOOL_ROOT` 或命令行 `--root` 覆盖；同一工作流的所有命令使用同一个 root。

```bash
# 每日收盘后运行。
tmux new-session -d -s aspool-daily \
  'cd /mnt/d/workstation/services/tdxman && bash scripts/ops/run_daily_data_pipeline.sh'

# 最新财报与股东人数快照已包含在每天的默认运行中。

# 先核对实际会调用的命令，不写数据。
bash scripts/ops/run_daily_data_pipeline.sh --dry-run

# 覆盖数据根（与 ASPOOL_ROOT 等价）。
bash scripts/ops/run_daily_data_pipeline.sh --root /path/to/data
```

脚本严格按以下顺序运行。基础设施或数据块级失败会停止；单只证券的行情或因子失败会隔离并记入报告，
独立的指数、ETF 和基本面阶段继续执行，最终验收状态为 `partial`：

1. 刷新股票、ETF、指数目录；目录是后续请求的唯一有效集合。
2. `update --type stock` 写入当日未复权 quote 日线及来源量比、换手率。
3. 股票 `update` 先提交已验证的原始行情、交易状态和可计算派生；再逐证券更新近期公司行为和因子后缀。因子失败不撤销合法行情，依赖因子的 MA20/广度样本保持缺失并记录原因。
4. `factors-bootstrap` 以完整当前上市事件链和三种复权行情验证新股，初始化缺失因子及 MA20；默认每只最多 60 行、每批最多 32 只，旧历史只报告待维护。
5. `update --type index` 更新指数日 K 和交易日历。
6. `update --type etf` 更新 ETF 未复权日线；源端确认无交易时保留 `NO_TRADE`。
7. 每天刷新最新财报与股东人数快照。它不是历史财务回填；`--with-fundamentals` 仅保留为兼容旧调度的无操作参数。
8. 读取 `aspool contract --format json` 与 `aspool status --format json`，按目标交易日比较当前有效股票集合、派生集合、因子覆盖和市场汇总；不能只比较最大日期或行数。

流水线汇总各阶段的持久化子报告：单证券失败把该阶段标为 `partial` 而不是 `failed`，
后续独立阶段继续运行，但整个运行的最终状态为 `partial`。`factor_unavailable`
（如 `no_verified_anchor`）单独计数并在 `factor_quality` 中报告，不与失败混列。

最终闭合要求目标日证券集合完全一致，并且收益、涨跌停、连板状态没有未知或无效记录。
复权因子缺口单独报告为 `factor_ready=false`，MA20 使用 `ma20_valid_count` 作为明确分母；
新股或无可信锚点造成的 MA20 不可用不应抹掉已经闭合的涨跌停与 Regime 基础数据。

目录的“待初始化”通过 `daily_bars(symbol, trade_date)` 主键逐证券跳转，禁止用
`DISTINCT symbol` 扫描多年日线。股票按 128 只提交一次，使 Windows/WSL 数据盘上的
WAL 保持有界；已经提交的批次可在中断后直接复用。
相邻交易日且昨收可验证的无事件因子后缀与原始行情合并为同一事务，最多 128 只一起提交，
每批只重算一次逐股派生和市场截面；超过该上限的 SDK 批次，其剩余后缀仍按最多 32 只提交。
因子验证失败时只移除该证券的因子输入，再提交其有效原始行情；数据库或预算错误直接回滚并失败，
不作为证券问题重试。需要合并历史事件的证券仍在原始行情提交后逐只隔离。
布局 3 的每个有界批次在同一池写锁下直接写入唯一原始、因子和派生存储，不再复制镜像。
提交前持久化真实变化的行后像及删除键，提交后验证才完成发布。中断意图阻止读取和下一笔写入，
必须显式运行 `aspool platform recover --root data` 幂等前滚恢复；该命令不联网，不重新采集行情。
迁移中断使用 `aspool platform restore --root data --recovery <池外已验证恢复点>`。
旧布局 2 的 `reconcile` 仅用于迁移前的旧镜像恢复，不是布局 3 的维护入口。

已建立因子的后缀、Enriched 和 Regime 公共输入由日更维护。缺失因子的初始化有独立 `factors-bootstrap` 入口。每只证券的因子与受影响派生保持原子更新；全市场截面是否完整由最后的目标日验收决定。Fundwise 的模型评分、周期阶段和缓存更新仍由 Fundwise 触发，公开接口保持不变。

## 缺失股票因子初始化

```bash
# 日常路径自动执行；只接纳完整的短上市历史。
aspool factors-bootstrap --root data

# 显式历史维护：最多 200 只、累计 100,000 行，每只少于 2,000 行。
aspool factors-bootstrap --root data --maintenance --as-of 2026-09-30 --symbol 001232.SZ
```

初始化只处理没有因子和外部锚点的股票。目录上市日须与源端证券身份及 IPO 日期一致，
未复权、前复权和后复权行情必须有相同日期轴，完整覆盖该上市日至指定交易日。
SDK 除权金额按每股处理，排除上市前和目标日以后的事件；不支持的价格调整类别、
同日多价格事件、缺失前收、行情截断或本地原始价格差异均拒绝发布。
空事件响应还须通过财务身份确认和两种复权行情验证，不能单凭空响应补 1。

完整证据链允许将上市基准设为 1，随后按 `前收盘 / 除权参考价` 累乘，
比例复权时基准尺度消去。TDX 的仿射复权 OHLC 用于验证事件完整性；
公开 `qfq/hfq` 仍使用累计因子的既有比例口径，不声称与 TDX 仿射价格逐点相同。
北交所 MA20 沿用当前派生契约保持空值；不足 20 个有效交易样本也保持空值。

证据（含三种 OHLC、事件、范围和 SHA-256）及操作报告留在池外
`.local/reports/factor-bootstrap/`。因子及其来源证明仍只写 `adjustments.sqlite`，
事件只写 `stocks.sqlite`，MA20 与受影响日汇总只写 `features.sqlite`。
每个初始化批次通过共同持久化发布机制提交；异常恢复使用同一 `platform recover`。
重跑发现已有因子后跳过网络和业务写入。历史维护会暂时给原始库和派生库各 128 MiB、因子库 16 MiB 页缓存，结束后恢复连接原值；正常日更不调整。历史维护预算与正常日更预算分离，
不会因为初始化自动改写已有因子，也不会隐式修补原始行情。

## 尾部缺口修补

`sync` 不属于每日正常路径。错过日更、网络中断或需要确认近期历史连续性时，先运行只读检查：

```bash
bash scripts/ops/repair_recent_data_gaps.sh --count 10
```

它严格检查最新交易日的股票目录终态；历史股票只检查整日缺失和显式 `MISSING/INVALID`。指数只检查作为交易日历锚点的上证指数，ETF 只检查整日缺失，并确认日级 Regime 跟随股票派生。它不追求逐证券历史完备性，默认只读取已存交易日历并输出 JSON 缺口和即将调用的命令，不写数据。它不能证明日历本身是新的——错过日更同样会让日历滞后——收据的 `freshness` 字段明确这一边界。

确认后才执行对应数据域的有界修补：

```bash
bash scripts/ops/repair_recent_data_gaps.sh --count 30 --repair
```

`--repair` 先执行一次有界 `aspool sync --type index` 刷新交易日历（绝不按工作日推断节假日），
再重新检测缺口，只对受影响域运行股票/ETF 修补（指数不重复运行）。股票修补路径会按日期在本地
重算缺失的市场日汇总（只对已有股票派生的日期，不重抓完整日线），再补齐股票缺口。最后复查一遍；
残留缺口非零或任一操作未达 `ok` 时以非零退出。完成上述 index 日历刷新后，若只发现股票缺口，
后续只会运行 `aspool sync --type stock --count 30`；ETF 同理。ETF 单标的无日线不直接判为缺口，因为 ETF 可以合法无交易，仍由同步器的带日期报价确认 `NO_TRADE`。

每次运行会在 `.local/reports/daily-pipeline/<UTC 时间>.json` 写入阶段命令、状态、数据块覆盖和最新股票派生闭合情况。报告不进入 `data/`，因此不污染设备间的基础数据同步。

基本面虽是低频来源，仍在每日运行中检查并写入有变化的最新快照；报告保留财报和股东人数的变化数量。ETF 复权因子也不由在线 TDX 更新，仍属于已审核迁移参考数据；报告会保留它的覆盖范围，不会把 ETF 日线同步成功误报为因子已更新。

## cron 接入

先确认机器时区为 Asia/Shanghai、仓库 `.venv` 已安装锁定依赖，并创建日志目录。
以下配置是待安装示例；脚本中的 `TZ` 控制应用日期，不改变 cron 守护进程的调度时区。

```bash
mkdir -p /mnt/d/Workstation/Services/tdxman/.local/reports
```

```cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
40 17 * * 1-5 /bin/bash /mnt/d/Workstation/Services/tdxman/scripts/ops/run_daily_data_pipeline.sh --root /mnt/d/Workstation/Services/tdxman/data >> /mnt/d/Workstation/Services/tdxman/.local/reports/cron-daily.log 2>&1
```

同一规范化数据根的整轮日更运行通过池外的 `.local/locks/<数据目录名>.daily-pipeline.lock`
防止交错，重复启动立即返回 75；不同数据根独立运行。子命令继承运行锁，父进程单独退出后，
仍在执行的子命令继续持锁。锁文件不删除，避免替换 inode 绕过正在持有的锁。
运行锁只约束此日更入口；其他手动写入仍遵循池锁。它不替代每批数据写入的池锁，也不提供跨机器互斥。

无变化批次在确认上一发布完整后跳过提交，保留原始库、因子库及派生库的业务行、
时间戳与版本。股票子报告的 `performance` 记录行情采集、事件读取、writer、逐股派生、
市场汇总、镜像和整轮耗时，并进入流水线的 `source_performance`。
逐股派生与市场汇总耗时包含在 writer 内，不能再次相加；布局 3 的镜像耗时为零。
批次 `elapsed_ms` 包含本批原始/无事件事务及后续独立因子事务，避免只统计第一笔写入。

退出 0 表示全部阶段和最终闭合验收通过；`partial`、源失败或闭合不完整退出 1，
操作员中断退出 130。报告记录每个阶段、最终验收和整轮的单调时钟耗时；
文件名使用 UTC 微秒，快速连续重跑不会覆盖上一轮报告。
节假日不能仅按周一至周五判断：当前入口要求带当日日期的指数报价确认交易日，
没有确认时以非零状态结束，不把旧数据标为今日同步成功。调度侧应保留日志及非零退出通知。
错过的历史交易日通过上文 `repair_recent_data_gaps.sh --repair` 显式恢复。
