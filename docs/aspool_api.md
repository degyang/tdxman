# aspool API

aspool 维护市场数据，Fundwise 通过 `from aspool import DataPool` 只读查询。
安装 tdxman 同时提供 `tdxman`、`aspool` 两个 CLI 和 Python 包。

```python
from aspool import DataPool

pool = DataPool('~/.aspool')
latest = pool.read_daily(end='2026-09-16', lookback=121)
history = pool.read_daily(symbols=['SZ.000001', 'SH.600519'],
                          start='2016-01-01', end='2026-09-16')
print(pool.status())
```

Fundwise 应使用 `read_research_daily()`，不要读取 lake 文件、DuckDB catalog 或 CLI 输出：

```python
bars = pool.read_research_daily(end='2026-09-16', lookback=121)
```

该方法返回日线契约 v1 的全部列，并增加 `total_share`、`float_share`、`eps`、`ttm_eps`、
`net_assets`、`total_mv`、`float_mv`、`pe_ttm`、`pb`、`fundamentals_refreshed_at` 和
`fundamentals_source`。市值、PE、PB以每一行的收盘价计算；基本面快照在同一标的的窗口行中
重复。它适用于当前 Screen；因快照只保留最新版，`fundamentals_refreshed_at` 之前的窗口不能
作为无前视偏差的历史回测输入。

日期先截断，再逐证券取最近 N 根有效记录。日期边界包含当日。
证券标识为 `市场.代码`，并保留 `market`、`code`；六位代码保持前导零。
结果按证券和日期排序，返回 pandas DataFrame。

| 字段 | 契约 v1 |
|---|---|
| date | 交易日期 |
| open/high/low/close | 原始未复权价格 |
| volume | 成交量，股；MAC 日 K 的 vol 也为股 |
| amount | 成交额，人民币元 |
| turnover_rate | 换手率，百分数值；0.43 表示 0.43% |

free-stockdb 日线的 `turnover` 映射为 `turnover_rate`。缺失换手率保留空值；
没有历史流通股本证据时不以最新股本回填历史换手率。
缺失 OHLCV/成交额、非有限数或重复日线会报错，不能当作无信号。

读取不启动网络连接，不创建或修改目录、行情文件及 catalog。
Linux/WSL 下通过目录共享锁与 aspool 的日线导入、在线更新互斥：
读取正在更新的数据池会等待整次更新结束，避免跨标的混读不同批次。
绕过 aspool API 直接写文件的程序不在此协调范围内。

Fundwise 使用配置：

```yaml
data_backend: aspool
aspool:
  root: ~/.aspool
```

Fundwise 自己安装相邻项目（其 uv 配置已声明本地 tdxman 依赖），不解析 aspool CLI 输出。
收盘后运行 `aspool update` 更新最新交易日；工作日 09:00 至 15:30（含）该命令会拒绝执行。
历史修补使用 `aspool sync --source tdx --tdx-mode online|offline`，随后运行 Fundwise Screen。
日线和分钟线更新不会调用报价接口。财报类字段是与复权因子平行的独立数据集，保存在
`lake/fundamentals/snapshots.parquet`；使用手动命令维护：

```bash
# 手动全量刷新快照；建议在财报披露后按周或按月执行
aspool fundamentals
```

快照保存总股本、流通股本、EPS、TTM 每股收益和每股净资产。`DataPool.read_fundamentals()`
以指定日的本地日 K 收盘价计算总/流通市值、PE(TTM) 与 PB，因此价格每日变化不会导致
全市场重复请求 `quote`。字段单位为股、元和人民币元；PE/PB 无意义或不可计算时为 null。

`aspool update` 和 `aspool sync` 对新增在线日线使用该快照补齐与 free-stockdb 一致的
`total_share`、`float_share`、`total_mv`、`float_mv`、`pe_ttm`、`pb` 和 `turnover` 字段。
OHLCV 与成交额始终直接来自 tdxman；未先维护快照时只写行情字段，不伪造基本面数值。
快照不是本阶段前提；运行保留输入指纹和原始价格口径，但不声称可以重放历史版本。
`available_at` 未知，不能用收盘时间假装成已验证的历史可知时间。
本阶段不导入分钟历史，不增加回测撮合引擎。
