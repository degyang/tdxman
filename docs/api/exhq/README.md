# 扩展行情 API（7727）

[总入口](../README.md) · [连接](../connections.md)

tdxman 有两套扩展市场协议，方法名和返回形态不同。请先确定服务器支持哪套协议，再选相应客户端。

| 协议 | 客户端 | 方法前缀与返回 |
| --- | --- | --- |
| 标准扩展 | `ExTdxClient` / `AsyncExTdxClient` | `get_instrument_*`；模型列表、字典列表或单个模型 |
| MAC 扩展 | `MacExClient` / `AsyncMacExClient` | `goods_*`；主要为 DataFrame |

- [标准扩展行情](standard.md)：市场、合约、报价、K 线、分时及逐笔。
- [MAC 扩展行情](mac.md)：商品目录、报价、K 线、分时及逐笔。

[tdxpy 扩展行情](https://tdxpy.readthedocs.io/zh-cn/latest/api/exhq/)展示同一类市场数据，但其 `TdxExHq_API` 方法和返回转换不等同于这里的客户端。tdxman 具体实现见 `src/tdxman/ex/client.py`、`src/tdxman/ex/mac_client.py`。
