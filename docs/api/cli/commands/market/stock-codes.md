# `tdxman stock-codes`

获取通达信当前沪深北股票清单，默认包含 CDR；不写入 aspool 数据库。

```bash
tdxman stock-codes --format json
tdxman stock-codes SH --format table --fields all
tdxman stock-codes --scope bse --format csv --output stock-codes.csv
tdxman stock-codes SZ --scope main --code 000001.SZ --name 平安银行
```

## 参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `MARKET` | `ALL` | `ALL/SH/SZ/BJ`，大小写均可 |
| `--scope` | `all` | `all/main/star/chinext/bse/cdr`，依次为全部、主板、科创板、创业板、北交所、CDR |
| `--code` | 不筛选 | 可重复或逗号分隔；接受 `000001`、`000001.SZ`、`sz000001`，保留前导零 |
| `--name` | 不筛选 | 简称精确匹配，可重复；名称内的逗号不拆分 |
| `--progress` | 关闭 | 显示连接、分页及耗时，输出至标准错误 |
| `--format` | `json` | `json/table/csv` |
| `--output` | 标准输出 | 文件或目录；目录内使用 `stock-codes` 文件名和对应扩展名 |
| `--fields` | `basic` | 表格用 `all` 展示全部字段；JSON/CSV 始终包含全部字段 |

各类筛选取交集，同一个 `--code` 或 `--name` 的多个值取并集。市场与范围冲突（如 `SH --scope bse`）在连接前报错。代码、名称在目录采集完成后筛选；结果为空是正常结果。

## 输出

每行一只证券，按 `symbol` 排序；默认 JSON 记录数组。默认只输出结果；添加 `--progress` 时向标准错误输出连接、分页进度与总耗时。

| 字段 | 示例 | 含义 |
| --- | --- | --- |
| `symbol` | `000001.SZ` | 规范证券代码 |
| `code` | `000001` | 六位字符串代码 |
| `market` | `SZ` | `SH/SZ/BJ` |
| `name` | `平安银行` | 来源当前简称 |
| `board` | `主板` | 主板/科创板/创业板/北交所/CDR |

## 来源与边界

沪深使用 [TdxClient](../../../hq/README.md) 的 `get_security_count(Market)` 与 `get_security_list(Market, start)` 分页读取证券目录，再按代码前缀筛选股票。北交所使用 [MacClient.get_stock_quotes_list](../../../mac/market.md)，参数为 `Category.BJ`、`count=80`、`sort_type=CODE`、`sort_order=ASC`、`fields=PresetField.NONE`，逐页读取至短页或空页。

当前分类规则：沪市 `600/601/603/605` 为主板、`688` 为科创板、`689` 为 CDR；深市 `000/001/002/003/004` 为主板、`300/301` 为创业板；北交所分类目录内 `43/83/87/88/92` 为北交所股票。其余代码排除，包括 ETF、指数、债券和 B 股。新增编码规则需要更新分类逻辑。

这是来源当前目录，不能证明上市日期、退市日期、当日可交易状态或历史证券全集。北交所依赖分类报价目录，短页即结束，没有独立总数校验。沪深校验返回总数并在结束时复查总数，但不提供跨请求原子快照保证。发现重复代码、无效身份/名称、沪深提前空页或总数变化时整体失败，不输出部分清单、不覆盖目标文件；来源没有匹配股票也报错。

实现：[cmd_stock_codes.py](../../../../../src/tdxman/cli/cmd_stock_codes.py)。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)

北交所目录遇到空首页、重复身份或通信失败时，会在已配置的 MAC 服务器中最多重试三台；每次从首页重新读取并校验。重试连接使用 3 秒 socket 超时并关闭自动重连。全部失败时整体报错，不将沪深结果冒充完整沪深北清单。
