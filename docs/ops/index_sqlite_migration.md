# 指数 SQLite 迁移与原命令接入

2026-09-28。用户明确要求将指数也迁至 SQLite，并保留原有命令。
本文替代此前“指数首期仍存 Parquet”的存储安排；股票四表不变。
随后已补齐 [ETF 与辅助数据迁移](etf_reference_sqlite_migration.md)，生产池不再保留 lake 分区。

## 存储与接口

`tdxman/data/indices.sqlite` 独立保存宽基、行业、概念和风格指数。
只有 `daily_bars` 一张业务表，主键 `(symbol, trade_date)`，另有日期索引。
字段保存原始 OHLC、指数成交量、金额、上涨/下跌家数、名称、来源和更新时间。
指数成交量不按个股乘 100，指数没有个股的 ST、复权因子或涨跌停状态。
不增加发布批次、覆盖或模型结果表。

命令保持：

```bash
aspool sync --type index --source tdx --period daily --root data
aspool sync --type index --source tdx --period daily --root data --workers 4 --retries 2
aspool sync --type index --source tdx --period daily --root data --async
aspool status --root data
```

配置范围仍由 `settings/board_index.json` 管理。没有新增 `aspool index` 命令组，股票的
`aspool update` 也没有悄悄扩大范围。存在 `indices.sqlite` 时读写均走 SQLite；库损坏或
版本不符会报错，不静默回退旧 Parquet。尚未迁移的数据根保留旧后端兼容能力。
SQLite 新路径不再维护 DuckDB 的旧 `index_coverage`，该表仅是迁移前的历史记录；
现有 `aspool status` 已改为读取 SQLite 的实际范围。

公开签名不变：

```python
pool.read_index_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)
pool.list_indices(*, symbols=None)

from aspool import DataPool
pool = DataPool("data")
pool.read_index_daily(
    symbols=["000001.SH", "881165.SH"],
    start="2026-09-24", end="2026-09-28", fields=["symbol", "date", "close"]
)
```

字段裁剪进入 SELECT；指定证券、日期和 lookback 使用主键范围及 LIMIT seek。
保留规范/旧式代码、日期类型、字段顺序、单位和 attrs；不为窗口读取展开全部指数历史。
完整目录的行数统计属于显式 list/status 操作，不在日更的窗口规划中执行。

## 同步与迁移边界

- 已有指数用索引读取最近 5 条日期，网络从最新记录向前分页至重叠起点，物理写入仅针对
  新增或变化键。网络读取不占用 SQLite 写事务；逐指数事务提交，真实数据库故障不当作
  可忽略的源数据错误。相同输入不写更新时间。
- MAC 日 K 可以读取 `88xxxx` 行业板块。指数记录最后四字节按两项 uint16 还原上涨/
  下跌家数，不能按股票流通股本解释。保留 MAC 原始点位精度；与旧标准协议相比，近期
  重叠行可能出现浮点精度修订，只作用于该重叠窗口。
- 有界连接重试，源读取失败保留历史；返回日期未达到已观察市场交易日则报告 stale/partial。
  首次新增指数的显式 sync 沿用初始化语义，上限 64,000 条；不会重新初始化已有证券。
- WAL/FULL；恢复、运行报告在 `.local/`。股票与指数是独立事务，不宣称跨库原子更新。
- 一次性迁移由 `scripts/ops/migrate_indices_sqlite.py --root ... --workdir ...` 执行：
  持池写锁、每批最多 4,096 条、逐值比较、输入文件 SHA-256 前后核对、完整性检查，
  验证后原子安装 SQLite；旧指数文件移至 workdir 的 `legacy-indices/`，不删除唯一来源。
  存在目标库时拒绝覆盖，日常同步绝不暗中调用全历史迁移。
  若数据库已安装、仅旧目录归档失败，可用相同 root/workdir 加 `--finish-archive` 续跑；
  续跑先检查已安装库的 SHA-256 与迁移校验值完全一致、有无未关闭写入，再移动目录。
- 复制检查已包含指数 WAL/SHM；旧复制清单遗漏新指数库会被拒绝。此次不执行 Jakarta
  复制，后续需使用当前数据的完整新清单和相应验收证据。

## 执行回执

2026-09-28 已完成生产迁移及今日同步。详细日志位于
`.local/reports/execution-20260928-indices/`，恢复目录为
`.local/recovery/20260928-indices-sqlite/`。

| 验收项 | 实测结果 |
| --- | --- |
| 历史迁移 | 865 个指数，2,571,034 行；全量逐值核对、输入 SHA 核对、SQLite integrity_check 通过 |
| 当前数据 | 2,571,899 行；865 个指数末日全部为 2026-09-28 |
| 今日同步 | 新增 865 行、修订重叠范围内 4,308 行；失败、stale、隔离异常均为 0 |
| 无变化重跑 | 单指数真实同步：新增 0、修改 0、未变 5；没有业务行重写 |
| 库文件 | 434,597,888 字节，约 414.5 MiB |
| 资源 | 初次迁移进程峰值 RSS 321.8 MiB；今日同步 12.78 秒、峰值 RSS 159.3 MiB |
| API | 原签名、字段投影、日期窗口、lookback、代码别名通过；今日截面返回 865 行 |
| 冷历史 | 实际同步后抽查上证、沪深300、深证、创业板及行业指数，历史值一致 |
| 有界查询 | lookback 查询计划使用 PRIMARY KEY 范围 SEARCH 与 LIMIT |
| 回归 | 8 项新增回归、52 项相关既有回归通过；未重复运行股票全套测试 |

ZS/HY/HY2/GN/FG 标签分别覆盖 13/128/345/269/146 个指数，标签可重叠，唯一指数为 865。
同步修订包含 MAC 与旧接口的浮点表示差异；这里的重叠范围是每个指数最近 5 条已存记录，
不是固定 5 个自然日。历史较稀疏的指数会有更早的重叠起点，不会因此更新其他证券的冷历史。

首次归档遇到 Windows 目录重命名权限错误：已安装库的 SHA 核对通过后单独续跑归档成功，
未重建数据库。脚本已补充显式关闭 Arrow 文件句柄、归档恢复入口及失败恢复回归。
初次失败日志与续跑记录均保留，不将首次进程的非零退出隐去。

Fundwise 使用现有 `tickflow_regime_adapted_v1` 模型和公开 API，成功计算 2026-09-28：
综合分 **11**（未取整 11.2），状态 `weak`，评分输入质量 `complete`。
四维分数为盈利 0、投机 34.8、韧性 0、趋势 12.5。
结果位于 `.local/reports/execution-20260928-indices/fundwise/`。
这是一次真实归档计算，尚未接入自动调度或刷新 Fundwise 应用缓存；也未执行 Jakarta 复制。

小型可提交回执见 [验收证据](evidence/index_sqlite_20260928.json)。
