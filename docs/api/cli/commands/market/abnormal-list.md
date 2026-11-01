# `tdxman abnormal-list`

当前 ST／*ST 或来源标记为停牌的股票列表。同一证券只输出一行，不入库。

```bash
tdxman abnormal-list SH --format table
tdxman abnormal-list --type st --format table
tdxman abnormal-list --type suspended --format json
tdxman abnormal-list SZ --code 000004.SZ --format csv --output abnormal.csv
```

## 参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `MARKET` | `ALL` | `ALL/SH/SZ/BJ` |
| `--type` | `all` | `all`：ST/*ST 与停牌并集；`st`：仅 ST/*ST；`suspended`：仅停牌 |
| `--scope` | `all` | `all/main/star/chinext/bse/cdr`，同 [stock-codes](stock-codes.md) |
| `--code` | 不筛选 | 可重复或逗号分隔；六位代码、CODE.MARKET、市场前缀格式 |
| `--name` | 不筛选 | 精确匹配，可重复 |
| `--progress` | 关闭 | 显示连接、分页及耗时，输出至标准错误 |
| `--format` | `json` | `json/table/csv` |
| `--output` | 标准输出 | 文件或目录；目录内文件名为 `abnormal-list` 加对应扩展名 |
| `--fields` | `basic` | 表格用 `all` 展示所有字段；JSON/CSV 包含全部字段 |

先按市场、板别、代码、名称筛选股票池，再查异常。默认只输出结果；添加 `--progress` 时向标准错误输出连接、分页、检查进度与耗时。

## 输出与判定

保留 `stock-codes` 的 `symbol/code/market/name/board`，增加：

| 字段 | 含义 |
| --- | --- |
| `st_type` | `ST`、`*ST` 或空；按当前简称前缀判定，兼容 `SST/S*ST` |
| `is_st` | 是否命中上述 ST 名称规则 |
| `is_suspended` | 是否命中来源停牌位；`--type st` 不读取报价，故该值为空而不是 false |
| `trading_status` | 命中停牌时为 `SUSPENDED`，其他为空 |
| `trading_status_raw` | 命中停牌时保留来源原始整数状态字 |
| `checked_at` | 本次检查完成时间（含时区）；不是行情交易日或异常生效日 |

股票池复用 [stock-codes](stock-codes.md)；ST 分类采用当前简称，不提供历史风险警示记录。停牌读取 `TdxClient.get_security_quotes` 标准 `0x053e` 报价，每批最多 80 只，用 `trading_status_raw & 0x20` 判定，口径参考 AxData `stock_suspensions_tdx`。不以零成交量、零价格或非交易日判停牌；未命中停牌位也不据此宣称当前正在交易。

所有请求证券必须返回唯一匹配身份和有效状态字；缺失、重复或无效时整体报错，不覆盖输出文件、不返回部分清单。全部检查成功且没有异常时才返回空数组。数据源跨请求不保证原子快照，也没有在本命令中提供行情日期验证。

停牌查询使用独立的 `TdxClient(quote_session=True, heartbeat_interval=0)` 连接，与 AxData 一致只发送一次 `0x000D` 握手（载荷 `01`）；目录等其他标准接口沿用默认三步握手。自动重连保留报价连接模式。

2026-10-06 本机在线验证：同一服务器、同一只 `600000.SH`，旧三步握手返回 0 条报价；单步握手返回 1 条，状态字为 `1302`。此前首批 `80/80` 缺失来自握手差异，不能归结为数据源整体不可用。修正后全市场停牌查询约 6 秒完成，返回 11 只（SH 5、SZ 6、BJ 0），全部请求证券通过状态完整性检查；命中状态字为 `32800`（`0x8020`）或 `49184`（`0xc020`），均包含 `0x20`。这些是检查时来源提供的状态，不代表已验证交易日期的官方停牌事件。

2026-10-05 的 ST 查询返回 201 只（SH 83、SZ 115、BJ 3），约 5 秒完成。以上数量均为当时验证记录，不代表未来实时数量。

实现：[CLI](../../../../../src/tdxman/cli/cmd_stock_status.py)、[标准报价解析](../../../../../src/tdxman/commands/security_quotes.py)。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)

北交所目录遇到空首页、重复身份或通信失败时，会在已配置的 MAC 服务器中最多重试三台；每次从首页重新读取并校验。重试连接使用 3 秒 socket 超时并关闭自动重连。全部失败时整体报错，不将沪深结果冒充完整沪深北清单。
