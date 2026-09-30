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

# 纳入低频基本面快照。
bash scripts/ops/run_daily_data_pipeline.sh --with-fundamentals

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
4. `update --type index` 更新指数日 K 和交易日历。
5. `update --type etf` 更新 ETF 未复权日线；源端确认无交易时保留 `NO_TRADE`。
6. 仅传入 `--with-fundamentals` 时刷新最新财报与股东人数快照。它不是历史财务回填。
7. 读取 `aspool contract --format json` 与 `aspool status --format json`，按目标交易日比较当前有效股票集合、派生集合、因子覆盖和市场汇总；不能只比较最大日期或行数。

流水线汇总各阶段的持久化子报告：单证券失败把该阶段标为 `partial` 而不是 `failed`，
后续独立阶段继续运行，但整个运行的最终状态为 `partial`。`factor_unavailable`
（如 `no_verified_anchor`）单独计数并在 `factor_quality` 中报告，不与失败混列。

最终闭合要求目标日证券集合完全一致，并且收益、涨跌停、连板状态没有未知或无效记录。
复权因子缺口单独报告为 `factor_ready=false`，MA20 使用 `ma20_valid_count` 作为明确分母；
新股或无可信锚点造成的 MA20 不可用不应抹掉已经闭合的涨跌停与 Regime 基础数据。

目录的“待初始化”通过 `daily_bars(symbol, trade_date)` 主键逐证券跳转，禁止用
`DISTINCT symbol` 扫描多年日线。股票按 128 只提交一次，使 Windows/WSL 数据盘上的
WAL 保持有界；已经提交的批次可在中断后直接复用。
相邻交易日且昨收可验证的无事件因子后缀按最多 32 只合并提交，只重算一次市场截面；
需要合并历史事件的证券仍逐只隔离，单只失败不会回滚其他证券。每个有界小批在同一池写锁内
提交因子并完成分层镜像。写入前先检查上一次镜像提交是否完整；不完整时本次写入以
`DERIVED_NOT_READY` 拒绝，必须显式运行 `aspool platform reconcile` 恢复，后续局部更新
不得改写待恢复状态，日更也不自动全量重建镜像。若进程在源因子提交后、分层镜像提交前中断，
同样运行 `aspool platform reconcile --root data` 从本地权威库重建因子镜像；该命令不联网，也不重算行情。

复权、Enriched 和 Regime 公共输入没有额外独立命令。每只证券的因子与受影响派生保持原子更新；全市场截面是否完整由最后的目标日验收决定。Fundwise 的模型评分、周期阶段和缓存更新仍由 Fundwise 触发，公开接口保持不变。

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

基本面因低频且当前只维护最新快照，默认不参加定时日行情任务；需要时用 `--with-fundamentals` 明确纳入。ETF 复权因子也不由在线 TDX 更新，仍属于已审核迁移参考数据；报告会保留它的覆盖范围，不会把 ETF 日线同步成功误报为因子已更新。
