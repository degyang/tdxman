# `tdxman server-info`

```bash
tdxman server-info --format table
```

无业务参数。CLI 通过 `MacClient.get_server_info()` 读取当前连接服务器配置的交易时段，不是交易日历，也不证明当前市场开放。接受共用输出选项，写入目录时默认文件名 `server-info`。要判断历史或当天是否在已取得的交易日范围内，使用 [workdays](../market/workdays.md)。依据：`src/tdxman/cli/cmd_info.py`、`src/tdxman/mac/client.py#get_server_info`。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
