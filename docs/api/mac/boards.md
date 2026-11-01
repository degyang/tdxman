# MAC 行情：板块与监控

[MAC 入口](README.md) · [参数签名](../signatures.md)

| 方法 | 主要参数 | 返回 |
| --- | --- | --- |
| `get_board_list(board_type=BoardType.ALL, count=10000)` | 板块类型与期望数量 | 板块目录 DataFrame；内部按约 150 条分页 |
| `get_board_members(board_symbol, count=100000, sort_type=..., sort_order=..., fields=..., exclude_flags=None)` | 板块标识、排序和字段 | 成员报价 DataFrame；内部按约 80 条分页 |
| `get_belong_board(market, code)` | 证券市场与代码 | 所属板块 DataFrame |
| `get_capital_flow(market, code)` | 单只证券 | 资金流 DataFrame |
| `get_auction(market, code)` | 单只证券 | 集合竞价数据 DataFrame |
| `get_unusual(market, start=0, count=0)` | 市场与分页偏移 | 异动数据 DataFrame；`count=0` 交由服务端/实现处理 |

`BoardType` 包含行业、概念、风格、地区和业绩分类；`sort_type` 使用 `SortType`，方向使用 `SortOrder`。`board_symbol` 来自 `get_board_list` 的结果，不能用股票代码代替。板块目录或成员可能有未取完的页，检查 DataFrame 的 `attrs` 再决定是否用于全市场统计。

`get_board_members` 的 `count` 接受 1–100000，内部按约 80 只分页；`get_board_list` 按约 150 条分页。服务端分页重复标的会抛 `TdxDecodeError`。当前成员报价是查询时点的成分，不是历史某日成分表。

```python
from tdxman import BoardType, MacClient

with MacClient.from_best_host() as client:
    boards = client.get_board_list(BoardType.GN)
    print(boards.head(), boards.attrs)
```

竞价、异动等结果随行情时段和服务器而变化；空 DataFrame 只说明本次请求未得到可解析记录。源码：`src/tdxman/mac/client.py`、`mac/commands/board_list.py`、`board_members_quotes.py`、`symbol_auction.py`、`unusual.py`。
