# asPool 完整数据与 CLI 落地契约

日期：2026-09-28。状态：实施基线。本文冻结生产数据块、数据状态、在线更新、历史补齐、读取和验收命令。实现不得自行增加批次、发布、覆盖或异常业务表；完整迁移和全历史重建仍放在 `scripts/ops/`。

> 本文记录当前已经运行的生产结构。2026-09-29 冻结的下一阶段目标见
> [数据域分层需求](data_platform_v2_requirements.md)与
> [数据域分层设计](data_platform_v2_design.md)。目标文档将原始行情、复权因子、稳定 Enriched、
> Regime、基本面和按需快照分层；在迁移验收完成前，不用目标路径推断当前生产状态。

## 1. 数据根和十个生产数据块

权威数据根是当前项目 `tdxman/data/`：

```text
data/
├── catalog.duckdb
│   ├── securities
│   ├── security_calendar
│   └── fundamental_snapshots
├── stocks.sqlite
│   ├── daily_bars
│   ├── corporate_actions
│   ├── daily_features
│   └── market_daily_summary
├── indices.sqlite
│   └── daily_bars
└── etfs.sqlite
    ├── daily_bars
    └── adjustment_factors
```

运行报告、断点和恢复材料放在 `tdxman/.local/`，不进入运行数据根。

| 数据块 | 输入 | 写入入口 | 输出 |
|---|---|---|---|
| `securities` | TDX 沪深北股票目录、ETF 目录、明确的上市/退市资料 | `update/sync` 自动刷新；`directory` 独立刷新 | 当日预期证券集合、当前名称及有效日期边界 |
| `security_calendar` | 上证指数日 K 线的实际日期 | 指数 `update/sync` | 市场交易会话轴 |
| `fundamental_snapshots` | TDX quote 基本面字段 | `fundamentals`；`--type all` 复用/补取当前 quote | 最新股本及估值基础快照 |
| `stocks.daily_bars` | TDX 当日 quote；TDX/BaoStock 历史 K 线 | 股票 `update/sync` | 未复权股票日线 |
| `stocks.corporate_actions` | TDX 除权事件 | 股票 `update/sync` | 参考昨收和稀疏复权因子输入 |
| `stocks.daily_features` | 股票日线、事件、名称/ST、交易状态 | 与股票事实同一事务 | `TRADED/NO_TRADE/MISSING/INVALID`、涨跌停、连板、MA20 |
| `stocks.market_daily_summary` | 当日逐股事实和派生 | 与股票事实同一事务 | D 公共市场特征及 Regime 输入；W/M 使用同表预留契约 |
| `indices.daily_bars` | 配置指数目录和 TDX 指数日 K 线 | 指数 `update/sync` | 指数 OHLCVA 和可用的上涨/下跌家数 |
| `etfs.daily_bars` | 当日 ETF 完整目录和 TDX ETF 日 K 线 | ETF `update/sync` | 未复权 ETF 日线 |
| `etfs.adjustment_factors` | 已审核迁移源；后续明确的在线因子源 | ops 迁移/维护 | ETF 复权参考；不伪装成已在线更新 |

Regime 模型模板和评分结果归 Fundwise；asPool 提供日级 `market_daily_summary`、指数日线和有界事件金额读取。W/M 字段与读取签名已经冻结，但当前生产池没有 W/M 行，读取会明确返回 `FREQUENCY_NOT_READY`；后续周期 writer 只聚合受影响周/月。

## 2. 证券目录

`universe` 的业务含义是当前证券目录。新权威表统一为 `securities`，旧 `universe` 和 `security_lifecycle` 在迁移后只保留兼容读，不继续双写。

```text
securities
├── symbol          TEXT PRIMARY KEY，例如 000001.SZ
├── code            TEXT
├── market          TEXT，SH/SZ/BJ
├── asset_type      TEXT，stock/etf
├── name            TEXT，当前名称
├── active          BOOLEAN，是否仍在完整当日目录
├── listing_date    DATE，可空
├── delisting_date  DATE，可空；只接收明确来源
└── updated_at      TIMESTAMP；业务字段实际变化才更新
```

`listing_date` 用于排除上市前日期及计算新股无涨跌幅限制窗口；`delisting_date` 用于历史有效范围和停止退市后请求。目录遗漏可以改变 `active`，但不能凭一次遗漏填写精确退市日期。删除 `first_seen/last_seen/directory_source/lifecycle_source/refreshed_at`：它们不参与业务，目录观察时间和来源进入外部运行报告。

每次目录必须完整读取对应市场后再发布。任一市场分页失败时，不发布该市场的部分目录。

## 3. 数据状态和闭合验收

对目标日期有效的股票，结果必须落入一个终态：

| 状态 | 定义 | 统计 | 修复 |
|---|---|---|---|
| `TRADED` | 有效 OHLCVA | 正常进入统计 | 无需 |
| `NO_TRADE` | 请求成功但该日无交易，或来源明确无交易 | 与停牌同样排除 | 不自动重试 |
| `MISSING` | 请求超时、连接失败、响应过期或没有可靠结果 | 与停牌同样排除，同时报告数量 | `--status missing` |
| `INVALID` | 返回身份、日期或字段不合法 | 排除并报告 | `--status invalid` |

原始 `daily_bars` 只保存真实行情，不制造零价平盘。股票状态保存在 `daily_features`。成功闭合满足：

```text
expected_count = traded_count + no_trade_count + missing_count + invalid_count
unclassified_count = 0
```

单股重试耗尽后写 `MISSING` 并继续。连续失败熔断默认值为 **3**：连续三只股票都在各自重试耗尽后发生连接、超时、协议或身份错误，记录剩余区块并中断。任意可靠响应（包括 `NO_TRADE`）清零连续失败计数。

运行状态：

```text
ok                       完成且没有 MISSING/INVALID
completed_with_missing   完成但有零散 MISSING/INVALID；退出成功并明确报告
aborted_source_failure   连续失败熔断；退出非零
failed                   数据库、事务或结构错误；退出非零
```

BaoStock 不支持北交所；BJ 无法修复时保持 `MISSING`，同时不进入涨跌停统计。

## 4. 写入 CLI

```sh
# 当前交易日
aspool update --type stock --source tdx
aspool update --type index --source tdx
aspool update --type etf --source tdx
aspool update --type all --source tdx

# 各数据块按自己的末端自动补齐；也可给出明确窗口
aspool sync --type all --source tdx
aspool sync --type all --source tdx --start 2026-09-20 --end 2026-09-28

# 只修复未解决状态
aspool sync --type stock --source baostock --status missing
aspool sync --type stock --source baostock --status missing \
  --start 2026-09-01 --end 2026-09-28
```

兼容规则：未传 `--type` 的 `update/sync` 等价于 `stock`。`--type all` 只允许 TDX；BaoStock 只允许股票并且必须显式选择，不自动切源。

`update --type all` 与 `sync --type all` 顺序：

1. 完整获取股票和 ETF 目录。
2. 用指数 K 线更新指数库并从上证指数日期更新交易日历。
3. 更新/补齐股票，查询受影响证券的除权事件，原子计算逐股派生和 D 汇总；W/M writer 尚未投产。
4. 更新/补齐 ETF。
5. 刷新当前基本面快照；历史 `sync` 不倒填历史基本面。
6. 核对每块最大日期、终态闭合、派生范围和失败区块，写统一报告。

无日期的 `sync --type all` 按各库自己的末端和重叠窗口补齐，不能以一个全局最大日期代表全部数据。

指数 `update` 和 `sync` 统一调用 TDX 日 K 线接口。K 线末尾的上涨/下跌家数带 `AVAILABLE/UNAVAILABLE` 状态；兼容存储中的 `0/0` 只有在 `AVAILABLE` 时才能解释为真实值。Regime 全市场广度仍以股票逐股截面计算，指数广度只表示来源对该指数/板块给出的范围。

## 5. 读取和检查 CLI

```sh
aspool query --dataset securities [SYMBOL]
aspool query --dataset calendar --start ... --end ...
aspool query --dataset fundamentals [SYMBOL]
aspool query --dataset stock-bars SYMBOL --start ... --end ...
aspool query --dataset corporate-actions [SYMBOL] --start ... --end ...
aspool query --dataset stock-features [SYMBOL] --status missing --start ... --end ...
aspool query --dataset limit-events [SYMBOL] --start ... --end ...
aspool query --dataset market-summary --start ... --end ...
aspool query --dataset index-bars SYMBOL --start ... --end ...
aspool query --dataset etf-bars SYMBOL --start ... --end ...
aspool query --dataset etf-factors [SYMBOL] --start ... --end ...

aspool status
aspool status --dataset stock-features
aspool contract --format table
aspool contract --format json
```

旧 `aspool query SZ000001` 兼容映射到 `--dataset stock-bars`。查询不触发采集、派生或数据库创建。高基数读取必须给 symbol、日期范围或有界行数。

## 6. 基本面快照边界

`fundamental_snapshots` 当前只保存最新的 `total_share、float_share、eps、ttm_eps、net_assets` 和证券身份。`ttm_eps` 由当前 close/PE-TTM 反推；市值、PE 和 PB 可由快照与价格计算。它不是利润表/资产负债表/现金流历史，也不是 point-in-time 财务库，严禁用最新快照倒填历史回测日期。

## 7. 实施与验收顺序

1. 迁移 `universe + security_lifecycle` 到 `securities`，字段和计数对账后再切读写。
2. 实现十块机器可读契约及 schema 对照检查。
3. 完成 `update/sync --type all`、`--status` 和连续三只熔断。
4. 完成所有 query/status 数据集。
5. 合成库验证单股失败继续、三连失败熔断、BaoStock 只修 MISSING、四表事务回滚、指数 K 线广度和 ETF 目录闭合。
6. 备份真实 catalog，迁移目录，再运行真实 `sync --type all`；最后报告十块数据的行数、日期、MISSING/INVALID 和派生完整性。
