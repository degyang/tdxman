# 连接、输出与错误

[API 导航](README.md)

## 客户端与协议

| 同步 / 异步客户端 | 默认协议 | 常用数据 | 默认端口 |
| --- | --- | --- | --- |
| `TdxClient` / `AsyncTdxClient` | 标准行情 | 沪深证券目录、指数 K 线、财务、7709 文件 | 7709 |
| `MacClient` / `AsyncMacClient` | MAC 行情 | 股票报价、复权 K 线、板块、竞价 | 7709 |
| `ExTdxClient` / `AsyncExTdxClient` | 标准扩展行情 | 扩展市场目录、行情、逐笔 | 7727 |
| `MacExClient` / `AsyncMacExClient` | MAC 扩展行情 | 扩展市场商品、复权 K 线、分时 | 7727 |
| `UnifiedTdxClient` / `AsyncUnifiedTdxClient` | MAC + MAC EX | 代理前两类 MAC 方法 | 7709 + 7727 |

这些类由 `tdxman` 包根导出。`with Client.from_best_host() as client` 和 `async with AsyncClient.from_best_host() as client` 会连接并在退出时关闭；手动使用时调用 `connect()`、`close()`。标准和 MAC 客户端有 `ensure_connected()`；扩展标准客户端没有。统一客户端在进入上下文时连接 MAC，首次调用 `goods_*` 才连接 MAC EX。

一个异步客户端实例维护一条 TCP 连接，其内部调用会按连接串行处理；需要独立并发请求时使用独立实例，并考虑服务器负载和数据一致性。

构造参数的当前默认值：

| 客户端 | 构造参数（省略 `self`） |
| --- | --- |
| `TdxClient` / `AsyncTdxClient` | `host=None, port=None, timeout=None, auto_reconnect=True, heartbeat_interval=15.0 / 60.0` |
| `MacClient` / `AsyncMacClient` | `host=None, port=None, timeout=None, auto_reconnect=True, heartbeat_interval=15.0` |
| `ExTdxClient` / `AsyncExTdxClient` | `host=None, port=None, timeout=15.0, auto_reconnect=True`；异步另有 `heartbeat_interval=60.0` |
| `MacExClient` / `AsyncMacExClient` | `host=None, port=None, timeout=15.0, auto_reconnect=True`；异步另有 `heartbeat_interval=60.0` |
| `UnifiedTdxClient` / `AsyncUnifiedTdxClient` | `heartbeat_interval=15.0, timeout=15.0`；内部各自选择 MAC 主机 |

构造函数的 `host=None` 使用项目配置/本机优选端点，`timeout` 为秒，`auto_reconnect=True` 默认在连接故障时尝试重连。`from_best_host(refresh=False)` 复用本机排名；`refresh=True` 才重新测速候选主机，`ping_all()` 返回按延迟排序的 `(host, seconds)`。`heartbeat_interval=0` 可关闭对应客户端的心跳；同步 EX 构造函数没有此参数。类方法与查询方法的精确签名见[总表](signatures.md)。

服务器配置来自环境变量、`~/.tdxman/config.json` 和内置候选（按此前顺序覆盖）。常用变量 `TDXMAN_HOST`、`TDXMAN_PORT`、`TDXMAN_TIMEOUT`、`TDXMAN_KNOWN_HOSTS`、`TDXMAN_CONFIG_DIR`；MAC、标准扩展、MAC 扩展分别可用 `TDXMAN_MAC_HOST`、`TDXMAN_EX_HOST`、`TDXMAN_MAC_EX_HOST` 指定主机。设备本机验证排名位于 `settings/bestip.json`，由 CLI `bestip` 更新。[CLI 参考](cli/README.md)说明执行方式。

异步调用示例：

```python
import asyncio
from tdxman import AsyncTdxClient, KlineCategory, Market

async def main():
    async with AsyncTdxClient.from_best_host() as client:
        bars = await client.get_security_bars(Market.SH, "600519", KlineCategory.DAY, 0, 10)
        print(bars.tail())

asyncio.run(main())
```

## 返回值约定

- 标准 A 股、MAC A 股及 MAC EX 的多数数据方法返回 `pandas.DataFrame`；标准 EX 的方法返回 `list`、单个模型或 `None`，不自动变为 DataFrame。`F10Client.query()` 返回 `F10Response`，可调用 `to_frame()`。
- Python API 不使用 CLI 的 JSON/CSV 格式化、两位小数显示或 `--fields` 投影。CLI 统一输出规则见 [CLI 参考](cli/README.md)。
- MAC 报价与板块目录的 `DataFrame.attrs` 包含 `complete`、缺失标的/字段、数据单位或分页完结状态等元信息；将 DataFrame 序列化为 CSV/JSON 可能丢失 attrs。请在序列化前检查。
- 在线接口的服务器时间、客户端查询时间、K 线日期各有不同含义；只有返回字段明确提供时间时才可当作来源时间。

## 异常

`TdxError` 是基础异常：`TdxConnectionError` 表示断线/超时，`TdxDecodeError` 表示报文或字段解析失败，`TdxCommandError` 表示服务端命令错误；本地文件还可能抛 `TdxFileNotFoundError`、`TdxOfflineError`。这些类定义在 `src/tdxman/exceptions.py`；其中后两者可从 `tdxman.exceptions` 导入，但包根未导出。`BaostockError` 是 BaoStock 补充源异常，见[辅助服务](services/README.md)。

tdxpy 文档提到的 `raise_exception`、`auto_retry`、`to_df()` 是 tdxpy API，不是这些类的共同构造选项或必需转换步骤；请用 tdxman 当前异常类和方法返回契约处理错误。
