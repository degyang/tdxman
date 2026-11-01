# `tdxman board-list`

```bash
tdxman board-list --type GN --count 200 --format table
tdxman board-list --type ZS --count 600 --format json --output output/zs.json
```

`--type` 默认 `ALL`；接受 `HY`/`HY2`（行业）、`GN`（概念）、`FG`（风格）、`DQ`（地域）、`OTHER`、`YJ_LEVEL1/2/3`、`ZS`。`--count` 默认 10000。板块类型调用 `MacClient.get_board_list(board_type, count)`，自动按最多 150 条每页拉取。`ZS` 是特殊路由：调用 `MacClient.get_stock_quotes_list(Category.ZS, count, CODE, ASC)`，得到标准指数目录与实时行情，并非板块协议。CLI 不提供起始偏移。

`board-list` 的通达信目录主要是板块/研究指数，不是所有标准指数；`880xxx` 旧行业或市场统计指数是否可用要以返回结果核对。大目录落盘并检查实际条数及代码范围。CLI：`src/tdxman/cli/cmd_board.py`；API：`src/tdxman/mac/client.py#get_board_list,get_stock_quotes_list`；口径：`README.md`「指数类别与发现方式」。
