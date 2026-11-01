# `tdxman belong-board`

```bash
tdxman belong-board 000001.SZ --format table
```

标的可写 `000001.SZ` 或 `SZ 000001`，必须显式指定市场。命令无业务选项，调用 `MacClient.get_belong_board(market, code)`，返回当前服务端记录的所属板块列表，不带历史生效区间。单只标的可用表格查看；需完整字段时用 JSON。CLI：`src/tdxman/cli/cmd_board.py`；API：`src/tdxman/mac/client.py#get_belong_board`。
