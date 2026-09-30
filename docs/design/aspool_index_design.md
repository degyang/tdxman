# aspool 指数数据池需求、设计与验收

2026-09-28 新安排：按用户要求迁至独立 `indices.sqlite`，命令和公开 API 不变。
新存储、读写路由、恢复及执行结果见 [指数 SQLite 迁移](../ops/index_sqlite_migration.md)。
下文 Parquet 和根内报告路径为旧后端说明，仅适用于尚未迁移的数据根。

## 已确认需求

仅增加 `aspool sync --type stock|index`，默认 stock 保持原行为。index 仅支持 tdx 来源和 daily 周期，继承 online/offline、async、root 和 limit。首次建库或新增指数的历史取数据源实际可取得的最长范围，不以2010年截断；日常同步只增量补齐；涨跌统计缺失按0保存，由应用端判断。

名单包含 HY、HY2、GN、FG 和 ZS 常用指数，排除名称以“昨日”开头的指数。ETF 是上市基金证券，不属于该指数池；其独立设计见 [ETF 日线设计](aspool_etf_design.md)。清单保留 market、code、name、source。指数数据不混入股票主表，不改股票、分钟线和基本面数据。

## 命令

```bash
# 手动维护名单，默认只显示差异；确认内容后写入
.venv/bin/python scripts/maintain_board_lists.py
.venv/bin/python scripts/maintain_board_lists.py --write

# 指数可直接从空池建库，读取全部可用日线历史
aspool sync --type index --source tdx --period daily
aspool sync --type index --source tdx --period daily --async
aspool sync --type index --source tdx --tdx-mode offline --period daily
aspool sync --type index --root data/index-test --limit 10

# 股票使用原有同步行为
aspool sync --type stock --source tdx --period daily
```

`--limit` 只限制标的数。首次建库或新增指数每页最多800条，按实际返回数量推进，读至空页，取得最长可用历史。已有指数以已存最近5条记录的最早日期作为重叠起点；线上每页30条，向前读取至该日期，过滤掉更早记录后按日期合并。同日重跑会覆盖盘中数据与近期修订；停更超过30条时继续分页补齐，并非只取一页。早于重叠起点的历史保留，不在日常同步中重新抓取。离线初次导入全部本地历史，后续只合并重叠起点以后的记录。重复页面或分页超界报错，不把它标为完整。CLI默认使用4个独立连接（--workers 1～8），同步模式使用受限线程池，异步模式使用受限异步工作池；各连接内部串行，避免响应错配。离线只读取 vipdoc，不自动联网。

## 清单维护

配置为 `settings/board_index.json`，schema_version=1，indices 每条包含：

```json
{"market":"SH","code":"881001","name":"煤炭","source":["HY"]}
```

按 market+code 唯一、稳定排序，多来源保留在 source 数组。名称匹配“昨日*”排除；“昨曾跌停”等不以“昨日”开头的名称按原要求保留。名单代表当前关注范围，不作为历史时点成分关系保证。删除名单记录不会删除已保存历史。

常用 ZS 明确选取13只：上证指数、上证50、沪深300、中证500、中证1000、科创50、科创100、深证成指、深证综指、深证100、中小100、创业板指、创业板50。服务器 ZS 目录可能截断，缺少已选代码时通过精确报价查询确认名称，不静默丢失。

脚本默认仅输出 added、removed、changed（含更名前后和来源变化）、excluded、新旧数量；`--write` 原子更新。源请求失败或来源列表为空时保留旧清单。比较与写入独立于抓取，后续可增加 hs300 目标并维护 `settings/board-hs300.json`；本阶段不写该文件。

## 存储与字段

```text
ROOT/lake/indices/daily/market=SH/symbol=881001/bars.parquet
ROOT/catalog.duckdb: index_coverage
ROOT/reports/index-sync/<run-id>.json
```

| 字段 | 含义 |
|---|---|
| market / code / name | 市场、六位字符串代码、当前名称 |
| trade_date | 交易日期 |
| open / high / low / close | 指数点位，保存源精度 |
| volume | 指数原始成交量口径，不按股票乘100 |
| amount | 成交额（元） |
| up_count / down_count | 上涨/下跌家数；缺失按0 |

涨跌分布仅为上涨、下跌家数，不是涨幅区间直方图。保留服务器原始0/0，不推算历史家数。

在线使用 get_index_bars，支持881研究指数、880板块指数及常用市场指数。离线按32字节指数.day记录解析，尾部4字节按两个无符号16位整数读取上涨/下跌家数。原股票读取器保持原样。

覆盖表以 market+code 为主键记录起止日期、总行数、最近成功来源和更新时间。网络抓取与源数据校验在写锁外完成；发布阶段在锁内重新读取目标文件、合并尾部并批量更新覆盖信息。单指数校验通过且有变化时原子替换Parquet，没有变化则跳过文件重写。异常源记录逐行隔离，报告日期与原因，不覆盖已存记录；整个指数无有效记录或重复日期时失败。逐指数失败不中断其余指数；报告成功和失败明细，有失败时CLI返回非零退出码。重试可再次合并。

报告明确区分 fetched、added、changed、unchanged、rows、rejected 和 zero_breadth_rows；changed 比较整条数据，包含名称变化。报告附实际最早和最新日期、sync_scope（bootstrap/incremental）和 overlap_start。不删除源未返回的旧历史。同日线上/线下精度可能不同，切换来源允许覆盖，不以容差改变原始数值。

在线盘中最后一条是未收盘数据，后续同步会覆盖。离线最近日期受本地下载进度限制。最长历史表示当前数据源所提供的范围，不等于指数全部存续历史。

## 读取方式

本阶段指数维护仅在 sync 增加分支，现有股票 DataPool API/query 不混入指数。Fundwise 使用新增 `DataPool.list_indices()` 和 `DataPool.read_index_daily()`，详见 [aspool API](../design/aspool_api.md)。以下 DuckDB 示例仅供维护核验：

```python
from pathlib import Path
import duckdb

files = str(Path.home() / ".aspool/lake/indices/daily/market=*/symbol=*/bars.parquet")
with duckdb.connect() as conn:
    conn.read_parquet(files, hive_partitioning=False).create_view("indices")
    df = conn.execute(
        """SELECT * FROM indices
        WHERE market = ? AND code = ? ORDER BY trade_date""",
        ["SH", "000300"],
    ).df()
```

## 实施与验证

- [x] 源码核查及线上/离线样本验证。
- [x] 用户确认 ZS、最长历史、缺失为0、仅日线。
- [x] 差异清单脚本及正式配置。
- [x] 独立指数存储、覆盖表、sync 路由与帮助。
- [x] 在线分页、异步接口、离线涨跌解析。
- [x] 单元测试：长历史、幂等、修订、失败保留、短分页、重复页、异步备用线路、涨跌字段及源限制。
- [x] Fundwise 指数 API：隔离、只读、投影、日期过滤、lookback、错误码及重复键检查。
- [x] 全量日线建库和逐指数覆盖报告。
- [x] 线上补齐与最终覆盖、完整性验证。

最终数据结果见 [指数数据池验收报告](aspool_index_validation.md)：865个指数、2,566,718条日线，已完成全库校验及Fundwise实读。
