# Tick Stock Panel 适配器

[辅助服务](README.md) · [API 总入口](../README.md)

`tdxman.adapters.tick_stock_panel` 导出 `TdxmanProvider` 与带稳定 `code` 的 `ProviderError`。它将 tdxman 客户端或注入的 aspool `DataPool` 规范化为 Tick Stock Panel 所需接口；这是集成适配层，不是标准行情协议的新命令。`ProviderConfig` 从 `tdxman.adapters.tick_stock_panel.contracts` 导入。

该适配器依赖 Polars；安装包的 `tick-stock-panel` 可选依赖组会安装它（例如 `pip install 'tdxman[tick-stock-panel]'`）。

类标识为 `name='tdxman'`、`builtin=True`。方法参数的精确签名也列在[签名总表](../signatures.md)。

## 构造与配置

`TdxmanProvider(config=None)` 接受 `ProviderConfig`、同字段映射或 `None`。`ProviderConfig` 字段如下：

| 字段 | 默认值 | 作用 |
| --- | --- | --- |
| `pool` | `None` | `daily_mode='local'` 的 aspool 池 |
| `daily_mode` | `local` | `local` 从池读日线；`online` 从标准 `TdxClient` 读 |
| `standard_client` / `mac_client` | `None` | 注入已配置的标准/MAC 客户端 |
| `close_standard_client` / `close_mac_client` | `False` | `close()` 时是否连同注入客户端一起关闭 |
| `online_page_size` | `800` | 在线日线每页请求量 |
| `online_max_bars_per_symbol` | `8000` | 每标的在线历史预算 |
| `universe_limit` | `10000` | 商品目录上限 |
| `request_timeout` | `300.0` 秒 | 单次适配请求时间预算，必须大于零 |
| `max_result_rows` | `2000000` | 聚合结果行数上限，必须大于零 |

`provider.config.datasets` 是能力表；日线始终登记，注入 MAC 客户端后增加 `realtime`、`minute`、`full_minute`、`depth5`。`check()` 返回 `(bool, message)`，检查已关闭、local 模式缺池、online 模式缺标准客户端等状态。调用方拥有注入客户端，除非显式设置对应 `close_*_client=True`。

## 数据方法

| 方法 | 请求 | 返回/契约 |
| --- | --- | --- |
| `get_daily(symbols, start_time=None, end_time=None, asset_type='stock', on_chunk_done=None)` | 股票/ETF/指数、日期边界 | 规范化 Polars DataFrame；超过 `max_result_rows` 抛 `ProviderError('RESULT_LIMIT', ...)` |
| `iter_daily(...)` | 同上 | 按在线标的或本地最多 80 只一批迭代 Polars DataFrame，可用回调 `(current,total)` |
| `get_instruments(asset_type='stock')` | MAC 客户端与资产类型 | 标的字典列表 |
| `get_realtime()` | 当前股票与 ETF 目录 | 完整快照字典列表；故障时记录日志并返回空列表 |
| `get_realtime_indices(symbols)` | 显式指数列表 | 字典列表；故障时返回 `None`，便于保留消费者缓存 |
| `get_minute(symbols, start_time=None, end_time=None, asset_type='stock', freq='1m', on_chunk_done=None)` | `freq` 为 `1m/5m/15m/30m/60m` | 规范化 Polars DataFrame；需 MAC 客户端 |
| `get_intraday_batch(symbols, count=300, asset_type='stock')` | 当日每标的最多 `count` 条 | Polars DataFrame |
| `get_intraday_latest(symbols=None, count=3)` | `None` 时用股票+ETF 目录 | 当日最新分钟窗口；不安排轮询 |
| `get_depth_batch(symbols)` | 显式标的列表，最多 80 只一批 | `symbol -> {bid_prices,bid_volumes,ask_prices,ask_volumes,timestamp}`，每侧五档 |
| `test_dataset(dataset, symbols=None)` | 能力表中数据集名称 | 诊断字典，含 `provider/dataset/rows/columns/preview` 或错误 |
| `close()` | 无 | 关闭适配器并按配置处理注入连接 |

日线规范列为 `symbol/date/open/high/low/close/volume/amount`；分钟列为 `symbol/datetime/open/high/low/close/volume/amount`。`get_daily` 合并时按 `(symbol,date)` 去重排序；`iter_daily` 便于处理较大窗口。实时报价和五档要求每批标的完整匹配；缺失身份、时间戳或档位会抛有错误码的 `ProviderError`。`get_realtime` 和 `get_realtime_indices` 则把上游错误软化为 `[]` / `None`，使用时需区分无数据与失败。

```python
from tdxman import MacClient, TdxClient
from tdxman.adapters.tick_stock_panel import TdxmanProvider
from tdxman.adapters.tick_stock_panel.contracts import ProviderConfig

with TdxClient.from_best_host() as standard, MacClient.from_best_host() as mac:
    provider = TdxmanProvider(ProviderConfig(
        daily_mode="online", standard_client=standard, mac_client=mac,
    ))
    try:
        print(provider.check())
        daily = provider.get_daily(["600519.SH"], "2026-09-01", "2026-09-30")
    finally:
        provider.close()
```

宿主插件包装在 `src/tdxman/integrations/tick_stock_panel/plugin/`。`availability() -> tuple[bool,str]` 只检查依赖与配置，不建立网络连接；默认在线模式，可通过 `TDXMAN_TICK_STOCK_PANEL_MODE=local` 切换，local 模式从 `TDXMAN_ASPOOL_ROOT`（默认 `~/.aspool`）寻找 `catalog.duckdb`。插件包装类自动建立并拥有客户端连接，且**覆盖 `get_intraday_latest=None`**；需要该方法时使用上面的核心适配器并自行安排调用节奏。核心契约源码：`src/tdxman/adapters/tick_stock_panel/provider.py`、`contracts.py`、`normalize.py` 与 `routing.py`。
