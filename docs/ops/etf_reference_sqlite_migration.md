# ETF 与辅助数据完整迁移回执

2026-09-28。此前仅迁移股票和指数，遗漏了 ETF 与辅助文件；本次按整个生产池核对并补齐。
这里的“完成”指本机 `tdxman/data` 的存储和读写入口切换，不包含 Jakarta 的再次复制。

## 最终目录与接口

```text
data/
├── stocks.sqlite     # 个股日线、除权因子、逐股派生、市场汇总
├── indices.sqlite    # 宽基、行业、概念、风格指数日线
├── etfs.sqlite       # ETF 日线、基金来源复权因子
└── catalog.duckdb    # 公共日历、目录及基本面快照
```

运行时允许 WAL/SHM。`data/lake` 已退役，生产目录没有 Parquet、JSON 采集缓存、备份或报告。
旧文件完整保存在 `.local/recovery/`，没有删除唯一来源证据。

原入口保留：

```bash
aspool update --root data
aspool sync --type index --source tdx --period daily --root data
aspool sync --type ex --category ETF --source tdx --period daily --root data --workers 4 --retries 2
aspool status --root data
```

```python
from aspool import DataPool
pool = DataPool("data")
pool.read_etf_daily(
    symbols=["510050.SH", "159915.SZ"], end="2026-09-28", lookback=60,
    fields=["symbol", "date", "close", "volume", "turnover_rate", "vol_ratio"],
)
pool.list_etfs(symbols=["510050.SH"])
```

`read_etf_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)` 与
`list_etfs(*, symbols=None)` 签名保持不变，保留代码别名、2010 起始边界和 attrs。
`code` 按公开字段契约统一为字符串，消除旧 Hive 分区推断出的整数类型。
字段裁剪进入 SELECT，证券/日期/lookback 使用主键范围与 LIMIT，不展开全部冷历史。

ETF `daily_bars` 主键为 `(symbol, trade_date)`；保留源字段，包括 MAC 的原始份额字段。
`adjustment_factors` 保存 1,286 条基金来源因子，未把它们冒充成自动选择的复权结果。
基本面快照从 Parquet 迁入现有 catalog 的 `fundamental_snapshots` 表，未新增独立数据库。

## 更新与失败处理

- 每次在线 ETF sync 先读取最新 ETF 名单，不再仅依赖静态名单；不会自动改写配置文件。
- 已有标的只取最近 5 条记录作为重叠边界，缺更时有界向前分页；新增标的显式初始化历史。
- 原始行情按键比较，逐标的事务写入新增/变化行。历史量变化时更新相关后续量比；无变化不改时间戳。
- 量比使用前 5 个观测日成交量；换手率在有流通份额时补算。缺少必要输入仍为 NULL。
- 空 K 线须同时核对证券、报价日期和零交易活动，才能记录 `NO_TRADE`。它涵盖无成交、
  停牌或尚未上市等可能情况，不宣称已经确认停牌，也不插入假的零价日线。
- 网络失败有限重试；源日期过期、身份不符、无法核实的空响应仍报错，数据库故障不伪装成源缺失。
- CLI、同步/异步 SDK 入口及离线 ETF 入口均已转接新库。旧股票文件导入、因子导入与旧补齐
  入口在 SQLite 池上明确拒绝执行；历史全量变更走显式 ops，避免重新生成旧分区。
- 基本面手动更新及内部读取已转接 catalog 表，无变化不重写，失败回滚；初始化和 status
  不再为 SQLite 生产池创建 lake 脚手架。status 分别查询三个实际行情库。

## 实库验收

| 项目 | 结果 |
| --- | --- |
| ETF 历史 | 1,702 个标的、1,512,767 行，全部逐值比较通过 |
| ETF 当前库 | 1,705 个有历史标的、1,514,458 行，377,577,472 字节（约 360.1 MiB） |
| 最新 ETF 名单 | 1,732 个代码；1,691 个有 9 月 28 日日线，41 个有当日无交易报价，失败/过期均为 0 |
| 今日可空指标 | 换手率 4 条 NULL（源份额为 0），量比 18 条 NULL（前置历史不足或平均量为 0）；未伪造补值 |
| 物理修改范围 | 实查 10,075 个新增/修改键，全部位于对应标的同步窗口内；冷历史抽样一致 |
| ETF 迁移资源 | 76.21 秒；峰值 RSS 356.2 MiB；完整性校验通过 |
| 最终 ETF 同步 | 21.89 秒；峰值 RSS 171.3 MiB |
| 因子对账 | 58,677 条原始因子均找到完全一致的库内记录：股票 57,391、基金 1,286 |
| 历史事件对账 | 5,586 个缓存文件、186,622 条事件，全部业务键已入库 |
| 基本面快照 | 5,570 条，迁移前后所有字段逐值一致，消费接口实际读取通过 |
| 整池 status | 个股 16,355,642 行、指数 2,571,899 行、ETF 1,514,458 行 |

对账发现并补入 `920017.BJ / 2026-09-28 / category=5:slot=1` 股本事件。这是源缓存中已有、
SQLite 中遗漏的事实；类别 5 不改变价格复权比例，没有触发股票涨跌停重算。
`301668.SZ` 当日股本事件有一项源修订，保留当前已更新值，原始值与差异存入恢复报告，未用旧缓存覆盖新值。

7 项 ETF 新回归、3 项辅助数据退役回归、21 项相关既有回归及 ETF WAL 复制保护检查通过。
没有重复执行股票和指数已完成的整套测试。Ruff 和差异空白检查通过。

## 恢复与证据

- ETF 源文件：`.local/recovery/20260928-etfs-sqlite/legacy-etfs/`，附逐文件 SHA、行数和迁移库校验值。
- 辅助源与迁移前目录库：`.local/recovery/20260928-reference-retirement-final/`，附逐文件 SHA 和事件差异。
- 完整过程：`.local/reports/execution-20260928-etfs/`。首次无交易识别不全及事件对账失败的日志也保留。
- 小型可提交回执：[验收 JSON](evidence/etf_and_reference_migration_20260928.json)。

ETF 迁移在本地临时磁盘构建，分批校验后复制关闭数据库、核对 SHA，再原子安装；不会再次重写整池。
如需回退，先停写并关闭数据库使用者，保留当前三个 SQLite 的所有新数据，再按对应恢复报告还原旧路径；
旧文件截止迁移前，不能直接用它们覆盖今日新增数据。
Jakarta 复制检查已覆盖 ETF WAL/SHM，并拒绝遗漏 ETF 库的旧清单；本次未执行远端复制或增量传输。
