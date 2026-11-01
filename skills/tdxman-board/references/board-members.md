# `tdxman board-members`

```bash
tdxman board-members 881001 --count 20 --sort CHANGE_PCT --order DESC --format table
tdxman board-members 000699 --count 20 --format json --output output/000699.json
```

`BOARD_SYMBOL` 是通达信板块代码或支持的标准指数代码，不是 `CODE.MARKET` 标的格式。`--count` 默认 100000；`--sort` 默认 `CHANGE_PCT`，常用 `CODE`、`PRICE`、`VOLUME`；`--order` 默认 `DESC`，可用 `ASC`。CLI 调用 `MacClient.get_board_members(board_symbol, count, sort_type, sort_order)`，API 自动分页；CLI 没有 `--start`。返回成分的**当前报价**，不应作为历史日期的成分表。请求大成分集时落盘并核对行数及重复代码。CLI：`src/tdxman/cli/cmd_board.py`；API：`src/tdxman/mac/client.py#get_board_members`；示例：`README.md`「指数类别与发现方式」。
