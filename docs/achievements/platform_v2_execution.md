# asPool 数据域分层实施记录

日期：2026-09-29。本文记录 [分层需求](../design/data_platform_v2_requirements.md) 与
[分层设计](../design/data_platform_v2_design.md) 的实际实施边界，不以目标文档代替运行证据。

## 已实现

- `aspool sync --count N`：缺省 10、上限 60 个交易日；与 `--start/--end` 互斥，股票、指数、ETF
  和 `--type all` 使用同一窗口语义。
- `aspool update|sync --type all` 不再自动更新基本面。
- `aspool fundamentals update|status`：将 TDX 财务报告按来源报告日期保存到
  `fundamentals.sqlite`，股东人数保存为独立历史序列；无业务变化不增加 revision。
- `aspool platform prepare|verify|status|activate|rollback`：按证券分批复制 1,600 万级股票派生，迁移
  Regime 日汇总、股票/ETF 因子和因子锚点。prepare 可断点继续，且不会切换公开读取。
- `verify` 分浅层（默认，行数比对 + feature_state 状态检查）和深层（`deep=True`，全表 EXCEPT 内容比对）。
  日更路径使用浅层；`reconcile` 和 `activate` 使用深层。
- `reconcile` 使用 `ATTACH + INSERT SELECT` 从 `stocks.sqlite` 全量重建 `features.sqlite`，不经过
  Python 内存（16M 行原 ~3.8GB Python 占用降至近乎为零）。
- `features.feature_state` 同时记录原始行情 revision 和因子 revision。局部更新先标记 `DIRTY`，
  因子与派生均完成后才标记 `READY`；版本不一致时公开派生读取返回
  `DERIVED_NOT_READY`。
- `DataPool` 依据 `catalog.pool_metadata.layout_version` 选择完整布局。现有 Fundwise 方法名、
  参数和缺省含义保持不变；coverage/exceptions 由稳定表投影，不恢复批次表。
- 股票与 ETF 日线增加可选 `adjust="none|qfq|hfq"` 和 `adjustment_base`；缺省 `none` 仍为
  未复权价格。
- 状态和查询 CLI 已识别独立基本面、股东人数以及激活后的派生表。

## 激活门槛

`aspool platform activate` 只有在以下条件全部满足时才把布局标记从 1 改为 2：

1. `stock_daily_features` 和 `market_regime_features` 与来源表全内容一致（深层 EXCEPT 比对）；
2. 股票因子、因子锚点和 ETF 因子行数与来源一致；
3. 两个稳定派生数据集均为 `READY`；
4. 派生的 `raw_revision` 等于 `stocks.dataset_state`，`factor_revision` 等于
   `adjustments.adjustment_state`；
5. Fundwise 兼容读取验证通过。

迁移阶段保留旧派生表用于回滚。旧表退役及 SQLite 空间回收只在新布局稳定运行后单独执行，
不与首次激活合并。

激活后如消费者验收失败，运行 `aspool platform rollback --root data` 只把布局标记恢复为 1；
它不会删除影子库，也不会改写原始行情。

## 生产迁移与切换证据

2026-09-29 在 `tdxman/data` 完成可恢复影子迁移并激活布局 2：

| 数据集 | 来源行数 | 目标行数 |
|---|---:|---:|
| stock daily features | 16,356,177 | 16,356,177 |
| market regime features | 12,960 | 12,960 |
| stock adjustment factors | 57,406 | 57,406 |
| stock factor anchors | 57,391 | 57,391 |
| ETF adjustment factors | 1,286 | 1,286 |

切换时 `features.sqlite` 为 3,992,039,424 字节，`adjustments.sqlite` 为 26,374,144 字节；
`fundamentals.sqlite` 和 `snapshots.sqlite` 仅完成 schema，分别为 24,576 和 16,384 字节。复制按
证券断点续跑，主体进程轮询观察峰值 RSS 为 156,872 KiB；该数值是迁移采样，不冒充五年公共
读取验收。

切换前后对 2026-09-21 至 2026-09-28 的公开调用进行签名对照：27,802 行股票研究日线、5 日日级
市场特征、557 条涨跌停事件、200 条异常、5 日上证指数以及 557 条有界事件成交额，在值哈希、
列及顺序、dtype 和 `DataFrame.attrs` 上零差异。切换后 Fundwise 的 asPool/Regime/证券代码消费者
用例为 25 passed。生产布局标记现为 2；旧表仍保留用于观察期回滚，尚未执行空间回收。

拆库后的首轮性能检查发现，无筛选 `read_limit_events()` 没有命中事件条件索引；原因是 SQL 谓词
没有逐字包含原 partial-index 条件，SQLite 转而扫描日期宽索引。实现已补充等价约束并把事件索引
升级为覆盖索引。最终布局 2 五年验收覆盖 2021-09-24 至 2026-09-28：1,214 个会话、13,633 条
筛选事件、258 批、最大 512 行/批，全部与独立事件读取一致，成交额缺失 0；读取与对账 14.057 秒，
进程峰值 RSS 307,908,608 字节（约 293.6 MiB），低于 2 GiB，结束后文件描述符和线程数恢复基线。

## 当前未纳入本次切换

指数/ETF 稳定 Enriched、W/M Regime writer、按需 snapshot 计算/清理命令仍需后续实现。
`snapshots.sqlite` 已建立 schema，但不会在普通读取时隐式计算。跨设备逻辑增量同步仍只保留为
后续设计；当前不增加复制游标或网络传输入口。

后续 Enriched 计算器的需求已经冻结：按 `symbol` 隔离时序，排除无交易日；股票、ETF、指数分别
计算；稳定层保持窄字段，MA/EMA、MACD、动量、高低点、BOLL、ATR、波动率、KDJ、RSI、量价信号
和板块相对偏离进入按需 snapshot。首次覆盖全部标的，新增交易日做全目录增量，因子变化只重算
受影响标的。当前切换仅迁移既有稳定派生和日级 Regime，不把尚未实现的指标描述为已上线。

## 验证命令

```bash
PYTHONPATH=src python -m pytest -q \
  tests/unit/test_platform_v2.py \
  tests/unit/test_fundamentals_store.py \
  tests/unit/test_sqlite_read_api.py \
  tests/unit/test_sqlite_update_cli.py

aspool platform status --root data
aspool platform verify --root data
aspool fundamentals status --root data
```

完整测试需要安装 `tdxman[tick-stock-panel]` 的可选 `polars` 依赖，并在已安装 wheel 的隔离环境运行
wheel 安装测试；源码环境下缺少这些前提不属于分层实现回归。
