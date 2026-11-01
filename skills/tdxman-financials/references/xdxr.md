# `tdxman xdxr`

```bash
tdxman xdxr 600519.SH --format json --output output/600519-xdxr.json
```

必须显式指定市场；无日期过滤或分页选项。CLI 调用标准协议 `TdxClient.get_xdxr_info(market, code)`（7709），读取服务端返回的除权除息及股本变动事件。事件含日期、分红、送转、配股等字段，是复权依据，**不是逐日复权因子**；要直接取复权 K 线用 `tdxman kline --adjust QFQ|HFQ`。检查返回日期和事件类别，不把空表直接解释为公司没有公司行动。CLI：`src/tdxman/cli/cmd_finance.py`；API：`src/tdxman/client.py#get_xdxr_info`、`docs/api_reference.md`「get_xdxr_info」。
