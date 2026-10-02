# CLAUDE.md

> 数据分层术语以[基础数据与 Enriched 数据分层契约](docs/design/data_layers.md)为准：历史 K 线、下载的复权依据、财务等属于基础数据；计算出的复权因子、逐股衍生、市场/板块聚合及计算快照属于 Enriched 数据。


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build / Test / Lint

```bash
# 单元测试（无需网络，使用 tests/fixtures/ 中的 hex 数据）
python -m pytest tests/unit/ -v

# 集成测试（需要网络，默认跳过）
XMTDX_LIVE=1 python -m pytest tests/integration/ -v

# 类型检查（strict mypy）
mypy src/

# lint + format
ruff check src/ tests/
ruff format --check src/ tests/
```

## 架构

```
src/tdxman/
├── client.py          # TdxClient / AsyncTdxClient（高层 API）
├── transport/
│   ├── sync.py        # TdxConnection（socket）+ ping_host / ping_all
│   └── async_.py      # AsyncTdxConnection（asyncio）
├── commands/          # 每条命令：build_request() + parse_response()，无 IO
├── codec/             # price / volume / datetime / frame 编解码
└── models/            # 纯 dataclass，无业务逻辑
```

commands 层不依赖 transport，可独立单测。修改 codec 或 commands 时不需要网络。

## Aspool 数据管理原则

涉及数据维护、存储、派生计算和消费接口时遵循以下约束；整改主体是 aspool，Fundwise 是消费方，不因存储优化改变其业务口径。

1. **围绕真实变化更新。** 按证券、日期、字段记录实际差异；无变化重跑不得重写业务数据、增加 stale 或改变业务版本。近期热点用于日常调度，窗口外历史纠错使用明确入口。全量处理须说明原因与范围，不能成为日常增量的隐式副作用。
2. **按依赖确定失效范围。** 区分原始变化、预热读取、派生重算和物理重写。滚动窗口、递归连板和当日市场汇总分别确定边界；递归只能在完整状态收敛且后续输入/规则未变时停止。无法证明边界则保守回退，不直接清除 stale。
3. **统一入口，有界访问。** 日线读写通过统一存储入口，明确字段、键、日期范围、必要前置状态及资源边界；业务代码不自行遍历目录或拼接存储布局。热点操作不得隐含扫描、解码或重写全部冷历史。SQL 有日期过滤不等于底层扫描已经有界。
4. **保留事实语义，区分逻辑版本。** 保持唯一键、单位、复权、NULL、来源及生效时间契约；缺行情不等于停牌，缺值不能补成 0。记录 ST/板块所用快照，不能为性能静默改变历史解释。缓存跟随可靠的逻辑依赖版本；物理压缩或重排不等于业务变化。
5. **一致发布，失败可识别。** 保留池锁和现有单日发布事务，关联确定的输入、规则与输出代次；不得把混合版本当成完整新结果。单文件原子替换不能替代数据库与多文件的一致性。失败后应可重试、续跑或恢复，且不会把过时结果标为有效。
6. **按恢复能力决定保留与清理。** 生产修复、迁移前建立一致恢复点并验证恢复。删除前核对当前发布、保留恢复链、运行任务及未结问题引用；保留必要的唯一来源证据。文件年龄、版本号、SHA 校验或连续若干日成功，均不能单独证明可以删除。
7. **用正确性和增长负载验收演进。** 同一输入、规则与维度口径下，增量须与完整参考计算一致。存储选型同时验证近期全市场、单证券历史、修订写入、增长、并发和恢复；不以小文件数量直接决定拆分，也不将窄表微基准当作完整迁移验收。保持公开 API，覆盖股票、ETF 和既有消费者。

存储设计变更必须列出每类数据的唯一物理所有者、所有读写入口、迁移与恢复方案、旧对象退役及公共契约测试。接口兼容通过适配层维持，不能据此保留两套可写生产权威。布局 3 的日更和汇总维护使用共同发布机制；多 WAL 库提交必须有持久化意图及中断恢复，池锁或 ATTACH 本身不构成崩溃原子性。目录库读写的连接生存期遵循池锁；布局识别失败明确报错，禁止按文件存在猜测或回退。恢复材料保留在池外；代码和布局须配套部署。详见 [单一权威存储整改](docs/implements/single_authority_storage.md)。

按需查阅：[治理研究](docs/design/data_management_principles.md)、[存储专项评估](docs/audit/daily_storage_architecture_assessment.md)、[落地推进与验收](docs/achievements/remediation_execution_plan.md)。工程状态以推进方案及实际证据为准，不把计划或评估写成已实现。

在 Orca 中执行整改时按 [工作区与验证约定](docs/implements/orca_remediation_workflow.md) 准备提交基线、独立 Python 环境及数据根目录；worktree 只隔离代码，不隔离默认生产池。安装 orchestration 技能不等于授权启动多 agent。

Codex 默认实施使用 `gpt-6-sol / high`；复杂存储/依赖设计和关键恢复、切换、退役审查使用 `gpt-6-astra / high`。只有单独指定的纯资料整理使用 `gpt-6-luna / medium`。**未经用户明确确认，effort 最高为 `high`；使用 `xhigh`、`max`、`ultra` 或其他高于 `high` 的级别，必须事先获得用户对相应任务及级别的明确确认。**任务复杂、失败重试、切换模型或复用会话均不构成升级授权。启动及复用时固定并核对实际 model/effort，记录任务角色及确认依据；不静默回退。具体启动与核验见上述约定第 6 节；`.codex/` 是忽略的本地配置目录，新 worktree 不通过 Git 继承它。

## 新版运行数据目录

新版 `tdxman/data/` 包含公共目录库 `catalog.duckdb`、三个原始行情库 `stocks.sqlite`、`indices.sqlite`、`etfs.sqlite`，以及分层布局的 `fundamentals.sqlite`（`stock_financial_reports`、`stock_shareholder_counts`）、`adjustments.sqlite`（复权因子与锚点）、`features.sqlite`（唯一派生存储）、`snapshots.sqlite`（按需缓存 schema）（运行时可有数据库 WAL/SHM）。当前生产池已退役全部 `lake/` 分区：个股、指数、ETF 均从对应 SQLite 读写，基本面报告和股东人数在 `fundamentals.sqlite`，catalog 的 `fundamental_snapshots` 仅为遗留审计表；不能因旧命令重新生成 Parquet 或采集缓存。指数保留 `aspool sync --type index`，ETF 保留 `aspool sync --type ex --category ETF`。迁移源和恢复材料放 `.local/recovery/`，运行日志、传输清单和详细验证放 `.local/reports/`；小型交付证据进 `docs/evidence/`。临时测试库不得留在运行数据目录，也不参与复制。迁移验收必须列全数据域及其读写入口，不能以股票或指数迁移代替整个池完成。先完成 Jakarta 的关闭工件全量复制、哈希验收和受控安装，再考虑增量同步；不能将初始 M0 副本冒充最新派生成品。

## 协议编解码注意事项

- **价格编码**：变长有符号整数（类 LEB128），bit8=继续，bit7=符号。差分编码（相邻 tick 存 delta）。
- **成交量编码**：4 字节自定义浮点（`_decode_volume`），字节 3=指数，字节 0-2=精度。**不可用于价格字段**。
- **握手**：连接后必须顺序发送 3 条 setup 命令，响应丢弃。
- **帧格式**：16 字节响应头，body 按需 zlib 解压。
- 新增编解码逻辑时务必在 `tests/fixtures/` 中补充 hex fixture 并编写对应的离线解析测试。

## 已知限制

- `Market.BJ` 的 `get_security_list()` 不能稳定获取（服务器端问题），不要尝试依赖它。
- `limit_up` / `limit_down` 在 `SecurityQuote` 中默认为 `None`，涨跌停价应通过 `get_price_limits()` 或 `compute_price_limits()` 计算。

## 常用行情查询

### 服务器测速与优选

```bash
tdxman ping --timeout 3
```

`ping` 只展示候选服务器的测速结果，不会保存排名。候选端点池维护在 `settings/allip.json`（按 standard/mac/ex 协议），`tdxman bestip` 用真实行情请求验证候选，并把本机可用排名写入 `settings/bestip.json`。`TdxClient.from_best_host()`、`MacClient.from_best_host()` 等 Python 工厂方法默认直接采用本机排名取最优端点，`refresh=True` 时才重新测速选取。`TDXMAN_KNOWN_HOSTS`、`TDXMAN_HOST` 等环境变量或 `~/.tdxman/config.json`（含手工 `save_best_host()`）可覆盖标准行情候选池；`config.py` 仅保留内嵌兜底列表与上述配置的读写入口。

### 行业板块与行业日 K

```bash
# 通达信一级、二级行业板块完整目录
tdxman board-list --type HY --format csv
tdxman board-list --type HY2 --format csv

# 使用目录返回的 market、code 查询行业板块日 K，例如 market=SH、code=881165
tdxman kline SH 881165 --period DAILY --count 250 --format csv
```

`board-list` 返回行业板块的 `market`、`code`、`name` 等字段。CLI 的 `kline` 走 MAC 协议的 `MacClient.get_stock_kline()`，可查询行业板块代码；不要改用标准协议的 `TdxClient.get_index_bars()` 查询此类 `88xxxx` 板块代码，该接口可能返回空响应。

### 实时指标与日 K 衍生指标

```bash
tdxman quote "SH 600519"
```

实时 `quote` 默认包含 `vol_ratio`（量比）、`turnover`（换手率）、`vol`（成交量）和 `float_shares`（流通股本）。日 K 不包含 `vol_ratio` 或 `turnover`，但收盘后可推算：

```python
# MAC 日 K 的 vol 单位为股，float_shares 单位为万股；结果为百分比数值。
# 实时报价的 vol 使用手，不能与日 K 混用。
bars["turnover_pct"] = bars["vol"] / (bars["float_shares"] * 100)

# 收盘量比：当日成交量 / 前 5 个交易日平均成交量。
bars = bars.sort_values("datetime")
bars["vol_ratio_close"] = bars["vol"] / bars["vol"].shift(1).rolling(5).mean()
```

历史换手率应按流通股本变化（解禁、增发、送配、拆合股等）的生效日期分段计算。盘后 `vol_ratio_close` 是基于完整日成交量的收盘口径；盘中实时量比还需要同一时刻的历史分时累计成交量，不能只由日 K 严格复原。

## 代码风格

- ruff: line-length 100, target py310, rules: E/F/I/UP
- mypy strict mode
- 所有 `get_*` 公开方法返回 `pd.DataFrame`（通过 `_df._to_df()` 转换）。内部方法仍使用 dataclass 列表。
- 依赖：pandas（>=2.0）、tzdata（>=2024.1）。
