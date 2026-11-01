# MAC 行情：服务器、目录与文件

[MAC 入口](README.md) · [参数签名](../signatures.md)

| 方法 | 用途 | 返回 |
| --- | --- | --- |
| `get_server_info()` | 当前服务器的交易时段配置 | DataFrame：`today`、`last_trading_day`、两段交易时段及市场参数；不是交易所日历 |
| `get_kline_offset(offset=0, count=128000)` | 远端 K 线偏移查询 | DataFrame：`total`、`returned` |
| `get_file_meta(filename)` | 查询远端文件元数据 | DataFrame：`offset`、`size`、`flag`、`hash` |
| `download_file_chunk(filename, index, offset, size)` | 下载指定块；`index` 从 1 开始，`offset` 是字节偏移 | 原始 `bytes` |
| `download_file(filename, filesize=0)` | 自动组合文件块，每块最多 30000 字节 | `bytearray`；`filesize=0` 时由实现查询元信息，提前遇到空/短块可结束 |
| `get_goods_list(market, start=0, count=600)` | 7709 MAC 扩展商品原始目录 | DataFrame 列含 `name/category/u/index/switch/code/c1/c2`；`code` 是协议浮点数组，单次最多 1000 条，与 7727 `goods_list` 的合约代码目录不同 |

```python
from tdxman import MacClient

with MacClient.from_best_host() as client:
    meta = client.get_file_meta("block_zs.dat")
    print(meta)
```

这些文件接口返回原始内容或元信息，不会自动写入本地文件。下载前先确认服务端文件名和元信息，保存时自行选择路径并检查下载长度。要读取本机 vipdoc 文件请用[离线读取](../reader/README.md)。源码：`src/tdxman/mac/client.py`、`mac/commands/file_query.py`、`goods_list.py`、`kline_offset.py`、`server_info.py`。
