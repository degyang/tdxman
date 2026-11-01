# `tdxman finance`

```bash
tdxman finance 600519.SH --format table
```

必须给出市场和六位代码，兼容 `SH 600519`。无时间、报告期或分页参数；CLI 调用标准协议 `TdxClient.get_finance_info(market, code)`，返回**最新**结构化财务摘要，包括股本、资产负债、利润及每股字段，不是完整 F10 文档或历史财报序列。若要详细报表使用 [f10](f10.md)，并核对服务端返回的报告期。默认 JSON；表格默认只显示常用列，完整列用 `--fields all` 或 JSON。CLI：`src/tdxman/cli/cmd_finance.py`；API：`src/tdxman/client.py#get_finance_info`、`docs/api/hq/finance-files.md` 的财务说明。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
