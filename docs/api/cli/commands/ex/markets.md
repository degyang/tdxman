# `tdxman ex markets`

```bash
tdxman ex markets --format table
```

无业务参数。CLI 枚举本地 `ExMarket`，输出数值 `code` 与名称 `name`，不连接服务器；列表表示客户端可识别的市场代码，不证明某个端点当前有数据。常用名称有 `HK_MAIN_BOARD`、`US_STOCK`、`SH_FUTURES`、`CSI_INDEX`、`SZSE_INDEX`、`HK_INDEX`、`INTL_INDEX`、`FUTURES_INDEX`。再用 `ex quote-list MARKET` 检查该市场实际商品。CLI：`src/tdxman/cli/cmd_ex.py`；API 枚举：`src/tdxman/mac/enums.py#ExMarket`。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
