# 动态证券目录与 bestip 整改计划

日期：2026-09-29
状态：实施中（DD-01 至 DD-03、IP-01 已完成代码和本机目录迁移；真实 EX 连通性与全链路验收待完成）
目标：以在线完整目录决定股票、ETF 和指数的当前同步范围；按设备验证并缓存可用行情服务器。运行链路不再依赖仓库中的 ETF/板块静态名单，也不再让标准协议和 MAC 协议共用一个“最佳主机”。

## 1. 已确认的问题与范围

当前股票目录已经采用“在线完整目录 → `catalog.duckdb` 当前状态”的方式管理。ETF 和指数尚未完全遵循同一原则：

- ETF 在线更新会读取 `Category.ETF`，但离线入口仍读取 `settings/etf_list.json`。2026-09-29 在线目录为 1,733 只，静态文件为 1,727 只。
- 指数更新直接读取 `settings/board_index.json`。该文件有 865 只，其中包含 92 只仅属于一级行业 `HY` 的指数；目标范围不包含一级行业。
- 目标板块目录是 `HY2 + GN + FG`，风格目录排除名称以“昨日”开头的临时指数。
- 普通指数 `Category.ZS` 不是当前代码常量中的 13 只。2026-09-29 在线接口稳定返回 100 只：上海 40、深圳 58、北京 2。目标目录应保存完整 ZS 集合，13 只常用指数仅作为 `benchmark` 标签。
- 当日 `HY2 + GN + FG` 共 760 个唯一代码，ZS 共 100 个，其中 `880712`、`880823` 重叠；合并后是 858 个唯一指数。分类因此是多对多关系，不能作为日 K 表的单值字段。
- `Category.ZS` 使用 `999999.SH` 表示上证指数，公开接口和既有历史使用 `000001.SH`；发布目录前必须规范化为同一代码。
- 当前 `indices.sqlite.daily_bars` 只允许 `SH/SZ`，完整 ZS 目录还需要支持 `BJ`。
- 当前候选服务器散落在 `config.py` 和 `~/.tdxman/config.json`，标准协议与 MAC 协议还会互相覆盖 `best_host`。`ping` 只完成基础握手，不能证明真实日 K 或扩展行情可用。

本次处理当前目录、分类关系、目录驱动的日线同步、协议候选和设备本地最佳服务器。不会拆分指数行情表，不修改股票/ETF/指数历史价格，不在目录表中增加批次或每日全量快照。

## 2. 目标数据结构

### 2.1 当前证券目录

扩展现有 `catalog.duckdb: securities`：

```text
securities
├── symbol          VARCHAR PRIMARY KEY，例如 000001.SZ、881001.SH
├── code            VARCHAR
├── market          VARCHAR，SH/SZ/BJ
├── asset_type      VARCHAR，stock/etf/index
├── name            VARCHAR
├── active          BOOLEAN
├── listing_date    DATE，可空
├── delisting_date  DATE，可空
└── updated_at      TIMESTAMP，业务字段变化时更新
```

目录每次保存当前状态，不每天复制整张表。完整在线目录中新增、消失、更名或重新出现时才修改对应记录。目录不存在不能推导准确退市日期；历史行情始终保留。

### 2.2 指数分类关系

新增 `catalog.duckdb: index_memberships`：

```text
index_memberships
├── symbol          VARCHAR，关联 securities.symbol
├── category        VARCHAR，HY2/GN/FG/ZS/benchmark
├── active          BOOLEAN
├── updated_at      TIMESTAMP
└── PRIMARY KEY(symbol, category)
```

`benchmark` 是业务选择标签，不代替 ZS 在线目录。目录变更明细写入 `.local/reports/directory/`；不为此新增 publication、revision 或每日快照表。

### 2.3 指数行情不分表

继续使用：

```text
indices.sqlite
└── daily_bars
    └── PRIMARY KEY(symbol, trade_date)
```

不按 HY2、GN、FG、ZS 分表，理由是：

1. 四类指数具有相同 OHLCVA、涨跌家数、来源和增量更新规则。
2. 一个指数可以属于多个分类，分表会重复保存相同行情。
3. Regime 主要按日期读取跨分类截面，单表的 `(trade_date, symbol)` 索引更直接。
4. 当前约 858 个指数，现有库约 435 MiB，尚没有容量或查询证据支持物理拆表。

分类查询通过 `catalog.index_memberships` 选出 symbol，再读取 `indices.daily_bars`。只有实际测得单表索引或维护成为主要瓶颈时，才重新评估物理分区。

## 3. 在线目录发布规则

### 3.1 ETF

1. 从 `Category.ETF` 分页读取完整目录。
2. 校验 market、code、name、重复项和分页完整性。
3. 在事务中发布到 `securities(asset_type='etf')`。
4. 新增 ETF 补齐其全部可用历史；已有 ETF 只更新尾部。
5. 完整目录中消失的 ETF 标为非活跃，但不删除历史。
6. 目录仍存在但当日没有 K 线的 ETF 保持活跃，另记 `NO_TRADE/MISSING`；行情缺失不能反向改变目录。

### 3.2 指数

1. 分别读取完整 `BoardType.HY2/GN/FG` 和 `Category.ZS`。
2. 只在 FG 中排除名称以“昨日”开头的记录。
3. 将源端 `999999.SH` 规范化为 `000001.SH`，避免生成第二份上证指数历史。
4. 以 `(market, code)` 合并证券，以 `(symbol, category)` 保存全部分类关系。
5. 所有目录读取和校验完成后，在一个事务中发布 `securities + index_memberships`。任何目录失败或异常为空时保留上次成功状态，不做非活跃标记。
6. 新增指数补齐可用历史；消失的指数停止日更但保留历史；重新出现时从本地末端续补。

名称包含解码替换字符时不得覆盖已有正常名称；新代码名称不可可靠解码时记录异常并阻止该次完整目录发布。

### 3.3 本地和离线行为

- 在线 `update/sync` 先刷新目录，再按当前有效目录更新行情。
- 离线同步读取 `catalog.duckdb` 中最后一次成功发布的有效目录。
- 在线目录失败时不静默回退仓库 JSON，也不把部分响应发布成完整目录。
- `settings/etf_list.json`、`settings/board_index.json` 退出运行链路。兼容测试需要的样本迁入 `tests/fixtures`；生产路径确认无引用后删除原文件。

## 4. CLI 与编排

保留并扩展现有 CLI，不增加另一套目录命令：

```bash
aspool directory --type stock
aspool directory --type etf
aspool directory --type index
aspool directory --type all

aspool update --type etf
aspool update --type index
aspool update --type all

aspool sync --type etf --count 10
aspool sync --type index --count 10
aspool sync --type all --count 10
```

`update/sync --type all` 顺序调整为：

```text
股票目录 → ETF目录 → 指数目录
→ 指数和交易日历 → 股票行情及派生 → ETF行情
→ 完整性检查与统一报告
```

`aspool status` 增加各目录最后成功观察时间、活动数量和最近一次新增/非活跃/更名数量。目录时间来自运行报告或既有观察记录，不能通过所有行的 `updated_at` 推测，因为无变化刷新不会改业务时间。

## 5. allip 与 bestip

### 5.1 文件职责

```text
settings/
├── allip.json      # 随代码维护的候选端点，提交 Git
└── bestip.json     # 当前设备实测的完整可用排名，不提交 Git
```

`settings/allip.json` 合并当前源码中的标准、MAC、EX 和 MAC-EX 候选。候选按协议分组；相同 IP 可以属于多个协议，但每个协议单独验证。EX 组记录 `dialect=standard/mac`，供 `ExTdxClient` 和 `MacExClient` 分别选择，不能把两种报文当成同一种连接。

候选集合必须在文件级记录采集由来，说明合并了哪些项目或静态表，不要求逐个 IP 维护来源关系。端点只保存连接所需字段和必要备注：

```json
{
  "schema_version": 1,
  "updated_on": "2026-09-29",
  "notes": "TDX endpoints are candidates; bestip performs protocol-specific verification.",
  "collected_from": [
    {
      "project": "tdxman",
      "reference": "src/tdxman/config.py built-in host lists",
      "collected_on": "2026-09-29",
      "note": "standard, MAC, EX and MAC-EX candidates before consolidation"
    },
    {
      "project": "hotdx",
      "reference": "Projects/hotdx: hotdx bestip candidate lists",
      "collected_on": "2026-09-29"
    },
    {
      "project": "mootdx",
      "reference": "mootdx static server configuration used by hotdx bestip",
      "collected_on": "2026-09-29"
    }
  ],
  "endpoints": [
    {
      "host": "183.60.224.177",
      "port": 7709,
      "protocols": ["standard"],
      "name": "广发",
      "note": "optional operator note"
    }
  ]
}
```

`collected_from` 至少覆盖 tdxman、hotdx、mootdx 以及后续实际补充候选集合的项目。`reference` 优先写仓库相对路径、配置文件或上游仓库位置。相同地址和端口合并为一个 endpoint，协议取并集。维护时更新文件级来源说明和提交记录；不能因某台设备一次探测失败就从 `allip.json` 删除候选。

`settings/bestip.json` 保存每组全部通过验证的端点、端口、协议/方言、延迟和验证时间。它是设备本地状态：WSL 与 JakartaVPS 分别运行测速，不互相复制结果。将 `/settings/bestip.json` 加入 `.gitignore`，使用临时文件加原子替换，失败时保留上次成功文件。

### 5.2 命令

```bash
tdxman bestip
tdxman bestip --protocol standard
tdxman bestip --protocol mac
tdxman bestip --protocol ex
tdxman bestip --timeout 3
tdxman bestip --limit 5
```

默认刷新全部协议组、保存每组完整可用排名，终端每组只显示前 5 名。`--limit` 只改变终端展示数量，不截断持久化排名。`tdxman ping` 保留为不落盘的快速连通诊断。

### 5.3 真实探测

```text
standard → 建连、标准协议握手、读取稳定标的的真实日 K 并校验身份/OHLC/日期
mac      → MAC 登录、读取稳定标的的真实日 K 并校验身份/OHLC/日期
ex       → 按 standard/mac 方言读取有效品种数量并取得一条可解析行情
```

TCP 建连成功但业务响应为空、过期、身份不符或解码失败均判为不可用。各协议并发测速有界；同一端点失败只影响该端点。某协议没有任何通过项时命令整体返回非零，旧 `bestip.json` 保留，并清楚列出失败协议。

### 5.4 客户端选址

- `from_best_host()` 首先读取与自身协议匹配的 `bestip.json` 排名，不再让标准和 MAC 共用 `config.json: best_host`。
- 连接失败按同组排名顺序尝试下一台；不能跨协议借用地址。
- `bestip.json` 不存在时，按 `allip.json` 对应组做一次有界选择；候选文件也不存在才使用包内最小兜底。
- 环境变量指定单主机仍具有最高优先级，便于故障定位和临时运维。
- `~/.tdxman/config.json` 的端口、超时等非主机设置暂时兼容；主机候选和最佳结果迁出后停止写入 `best_host*` 字段。

## 6. 实施工作包与依赖

| 工作包 | 内容 | 依赖 | 完成证据 |
| --- | --- | --- | --- |
| DD-01 | 迁移 `securities` 约束以支持 index；新增 `index_memberships`；扩展 BJ 指数存储 | 无 | 原 stock/ETF 目录和指数历史逐项保留；迁移可重复执行 |
| DD-02 | 实现 ETF、HY2/GN/FG/ZS 在线完整目录采集、校验、规范化和事务发布 | DD-01 | 新增/更名/非活跃/重新活跃/重叠分类/失败不发布测试 |
| DD-03 | 指数与 ETF 同步改读 catalog；接入 directory、update、sync、all 编排 | DD-02 | 移走两个 JSON 后目标命令仍能在线和离线运行 |
| DD-04 | 真实目录迁移与数据核对；静态名单退出生产路径；更新契约文档 | DD-03 | 当日活动数、分类数、历史保留量、缺失状态和报告一致 |
| IP-01 | 生成并审核 `settings/allip.json`；记录候选集合来自哪些项目/静态表；实现协议分组读取和 `bestip.json` 原子存储 | 无 | 候选无非法/重复组内端点；文件级 collected_from 可追溯；bestip 被 Git 忽略 |
| IP-02 | 实现三组真实业务探测及 `tdxman bestip` | IP-01 | 模拟失败/空响应/错误身份和真实 WSL 运行通过 |
| IP-03 | 标准、MAC、EX、MAC-EX 客户端消费各自排名并组内故障切换 | IP-02 | 各客户端选中正确协议，首端点失败时使用下一端点 |
| IN-01 | 在 WSL 完成目录刷新、bestip、`sync --type all --count 10` 集成验收 | DD-04、IP-03 | 报告、数据库计数、最大日期、CLI 输出和 Git 状态可审查 |
| IN-02 | JakartaVPS 单独运行 `tdxman bestip` 和目录/尾部补齐验证 | IN-01 | 使用 Jakarta 本地排名；不复制 WSL 的 bestip.json |

主路径为 `DD-01 → DD-02 → DD-03 → DD-04 → IN-01`。IP-01 至 IP-03 可以在不接触生产数据的情况下独立推进，最终在 IN-01 汇合。实现期间不让两个工作包同时编辑 CLI/config 客户端接线文件。

## 7. 验收标准

### 7.1 动态目录

1. ETF、HY2、GN、FG、ZS 均由在线完整目录决定当前活动范围；仓库 JSON 不参与运行选择。
2. 相同完整目录重复发布时数据库业务记录和 `updated_at` 不变化。
3. 部分页、空目录、异常市场、重复身份和坏名称不会导致已有证券批量失活。
4. 指数多分类不重复保存 K 线；按任一分类都能查询到相同 symbol。
5. `999999.SH` 不产生第二份上证指数历史；BJ 两只普通指数可以正常落库和读取。
6. 目录消失只停止后续请求，不删除历史；日 K 缺失不改变目录活动状态。
7. 在线失败后的离线同步使用 catalog 最后成功状态，不使用静态 JSON。

### 7.2 bestip

1. `tdxman bestip` 对全部候选执行真实业务探测，按协议保存完整可用排名，终端默认展示前 5。
2. 标准、MAC、EX/MAC-EX 的结果互不覆盖；客户端只消费兼容协议。
3. 单端点失败不阻断其他端点；整组失败返回非零并保留上次成功结果。
4. WSL 与 JakartaVPS 分别生成自己的 `bestip.json`，Git 不跟踪该文件。
5. 使用排名第一的端点分别完成一条真实日 K/扩展行情请求；首端点故障时组内切换成功。
6. `allip.json` 在文件级记录候选集合来自哪些项目/静态表；测速失败不会自动删除候选。

### 7.3 回归范围

优先运行目录、SQLite index/ETF、CLI 编排、配置和 transport 的针对性测试。已有未受修改影响的全量测试证据不重复执行；只有公共 schema、公开 API 或共享客户端改动触达更广范围时，才扩大到相关单元测试集。真实数据验收前备份 `catalog.duckdb`，迁移和发布均必须可回滚。

## 8. 提交计划

按可独立审查和回退的边界提交：

1. `docs: plan dynamic directories and bestip remediation`
2. `catalog: add index directory memberships`
3. `aspool: publish live ETF and index directories`
4. `aspool: drive sync from catalog directories`
5. `network: add allip and device-local bestip storage`
6. `cli: verify and rank protocol endpoints with bestip`
7. `client: consume protocol-specific bestip rankings`
8. `docs: record live migration and integration acceptance`

生产目录迁移和真实全链路更新不混入结构代码提交；先以临时数据根验收，再备份并迁移 `tdxman/data`。每个提交记录已验证、未验证和对现有数据的影响。

## 9. 本次实施记录（2026-09-29）

- 已迁移真实 `data/catalog.duckdb`；迁移前备份保存在忽略的 `.local/backups/`。本次只变更 catalog 目录与分类关系，没有重写股票、ETF 或指数日线。
- 在线指数目录发布结果为 858 个活动证券：`HY2=345`、`GN=269`、`FG=146`、`ZS=100`、`benchmark=12`；排除了 12 个“昨日”风格指数。`000001.SH` 和 `899050.BJ` 均在活动目录中，未产生 `999999.SH` 重复证券。
- `tdxman bestip --timeout 3` 已在 WSL 产生设备本地排名：标准协议 48 个、MAC 2 个、EX 2 个通过真实请求。EX 通过项均为 MAC 方言；没有通过的静态候选仍保留在 `allip.json`。
- 定向验证通过：`ruff check`；目录、索引 SQLite、ETF SQLite、CLI、transport 与 bestip 共 54 项测试。此前 `test_aspool_performance.py` 中 5 项 DuckDB 连接配置/旧返回契约失败不属于本次改动，未把它们计为通过。
