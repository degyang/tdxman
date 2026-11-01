# `tdxman quote-list`

```bash
tdxman quote-list A --count 20 --sort CHANGE_PCT --order DESC --format table
tdxman quote-list ZS --count 600 --format json --output output/indices.json
```

`CATEGORY` 默认 `A`。常用分类：`SH`、`SZ`、`A`、`B`、`KCB`、`CYB`、`BJ`、`ETF`、`LOF`、`HGT`、`SGT`、`FXJS`、`ZS`；完整当前取值以 `quote-list --help` 为准。`ZS` 给沪深标准指数目录与实时行情，通达信板块目录用 `board-list`。`--count` 默认 80；`--sort` 默认 `CHANGE_PCT`，可用字段包括 `CODE`、`PRICE`、`VOLUME`、`TOTAL_AMOUNT`、`TURNOVER_RATE`；`--order` 默认 `DESC`，可用 `ASC`。

CLI 调用 `MacClient.get_stock_quotes_list(category, count, sort_type, sort_order)`。API 有 `start` 偏移并自动分页，CLI 本命令没有 `--start`；想要稳定完整目录时核对返回条数和服务端覆盖，不把 `count` 当作已存在总数。默认输出 JSON；大分类用 `--output` 落盘。CLI：`src/tdxman/cli/cmd_quote.py`；API：`src/tdxman/mac/client.py#get_stock_quotes_list`；分类说明：`README.md`「行情」。
