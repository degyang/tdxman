# EX 扩展行情：黄金、原油与期货接口问答

整理日期：2026-10-01。本文基于当前代码核对接口，不代表已在交易时段验证具体合约覆盖、报价时效或服务器字段完整性。

## 1. 能读取石油、黄金期货价格吗？

可以使用 EX 扩展行情客户端查询。具体能读取哪些黄金、原油合约，需要先查询服务器品种目录，再使用返回的市场与合约代码获取行情。

目前没有独立的 `get_gold_price()`、`get_oil_price()` 接口，也没有统一固定的黄金、WTI 或布伦特代码。国内合约、海外合约、主力合约及连续合约应按目录中的名称与描述区分，不能直接互换。

## 2. 有没有类似 `board-list` 的入口？

有：`tdxman ex quote-list MARKET`。它按市场列出商品／合约目录，而 `board-list` 列出 A 股行业、概念等板块。

尽管命令名包含 `quote`，**`ex quote-list` 当前调用的是 `goods_list()`，返回目录，不包含实时报价**。目录字段包括 `market`、`code`、`name`、`category`、`desc`。

常用市场分类如下：

| 市场参数 | 代码 | 分类 |
|---|---:|---|
| `COMEX_FUTURES` | 16 | 纽约 COMEX，可用于查找黄金等合约 |
| `NYMEX_FUTURES` | 17 | 纽约 NYMEX，可用于查找原油等合约 |
| `CBOT_FUTURES` | 18 | 芝加哥 CBOT |
| `ZZ_FUTURES` | 28 | 郑州商品 |
| `DL_FUTURES` | 29 | 大连商品 |
| `SH_FUTURES` | 30 | 上海期货 |
| `FUTURES_INDEX` | 42 | 商品指数 |
| `SH_GOLD` | 46 | 上海黄金 |
| `CFFEX_FUTURES` | 47 | 中金所期货 |
| `MAIN_FUTURES_CONTRACT` | 60 | 主力期货合约 |
| `GZ_FUTURES` | 66 | 广州期货 |

这些是客户端内置分类，具体合约覆盖以服务器返回为准。`SH_GOLD` 是上海黄金分类，不能据此认定它是原油期货市场。

```bash
# 查看客户端内置市场分类；此命令不查询服务器实际覆盖
tdxman ex markets --format table

# 查询合约目录
tdxman ex quote-list COMEX_FUTURES --format table
tdxman ex quote-list NYMEX_FUTURES --format table
tdxman ex quote-list SH_FUTURES --format table
tdxman ex quote-list MAIN_FUTURES_CONTRACT --format table

# 分页读取目录
tdxman ex quote-list COMEX_FUTURES --start 0 --count 100 --format table
```

## 3. 拿到合约代码后，如何读取价格？

以下命令中的 `CONTRACT_CODE` 需替换成目录实际返回的代码：

```bash
tdxman ex quote COMEX_FUTURES CONTRACT_CODE --format table
tdxman ex kline COMEX_FUTURES CONTRACT_CODE --period DAILY --count 30 --format table
tdxman ex tick COMEX_FUTURES CONTRACT_CODE --format table
```

Python 中可使用 MAC 扩展客户端，目录、报价和 K 线均返回 DataFrame：

```python
from tdxman import ExMarket, MacExClient

with MacExClient() as client:
    contracts = client.goods_list(ExMarket.COMEX_FUTURES, start=0, count=100)
    print(contracts)

    # Replace with a contract code returned by the directory.
    code = "CONTRACT_CODE"
    quotes = client.goods_quotes([(ExMarket.COMEX_FUTURES, code)])
    bars = client.goods_kline(ExMarket.COMEX_FUTURES, code, count=30)
```

## 4. 两类 EX 客户端分别提供哪些接口？

| 用途 | `ExTdxClient` | `MacExClient` |
|---|---|---|
| 市场目录 | `get_markets()`：查询服务器市场列表 | CLI `ex markets` 列出内置枚举 |
| 商品数量 | `get_instrument_count()`：全局数量 | `goods_count()`：全局或指定市场数量 |
| 合约目录 | `get_instrument_info(start, count)`：全局分页 | `goods_list(market, start, count)`：指定市场分页 |
| 单合约／批量报价 | `get_instrument_quote(market, code)`：单合约 | `goods_quotes([(market, code), ...])`：批量，最多 80 只 |
| 分类报价列表 | `get_instrument_quote_list(market, category, ...)` | `goods_quotes_list(market, ...)`：目录加批量报价，每次最多 80 只 |
| K 线 | `get_instrument_bars(category, market, code, ...)` | `goods_kline(market, code, period, ...)` |

`ExTdxClient` 还提供当日／历史分时、当日／历史分笔成交，以及按日期范围读取历史 K 线。其返回值主要是数据模型对象或列表，与 MAC 接口的 DataFrame 返回形式不同。

`goods_quotes_list()` 的排序参数目前只是预留，代码尚未实现排序，不能将它直接当作期货涨幅排行榜。读取全部目录或报价时，也需要按数量和偏移分页／分批。

## 5. 有哪些数据维度？

| 数据类型 | 当前模型／接口包含的维度 |
|---|---|
| 合约目录 | 市场、分类、代码、名称、描述 |
| 传统 EX 五档报价 | 最新价、昨收、开盘、最高、最低、总量、现量、内外盘、持仓量、买卖五档价格与数量；另有 `kaicang` 原始字段 |
| 传统 EX K 线 | 时间、开高低收、持仓字段 `position`、成交字段 `trade`、金额字段 `amount` |
| 传统 EX 分时 | 时间、价格、均价、成交量、持仓量 |
| MAC 自定义报价 | 通过 `fields` 选择报价字段；可用字段和含义需结合期货品种及服务器返回验证 |

不同合约的计价单位、成交量单位、交易时段与价格参考口径可能不同。当前目录与报价接口没有统一补齐这些合约规格，不能仅凭列名直接比较不同品种的绝对价格或成交量。

## 6. 已核对与待验证事项

已核对：客户端接口、CLI 路由、市场枚举、返回模型、分页与批量限制。

待交易时段验证：服务器是否提供目标黄金／原油合约、主力与连续合约的具体代码、报价更新时间和延迟，以及各品种字段单位与完整性。不能用接口存在推断所有海外期货都有即时行情。

## 代码依据

- [CLI 扩展市场命令](../../src/tdxman/cli/cmd_ex.py)
- [MAC EX 客户端](../../src/tdxman/ex/mac_client.py)
- [传统 EX 客户端](../../src/tdxman/ex/client.py)
- [EX 数据模型](../../src/tdxman/ex/models.py)
- [扩展市场枚举](../../src/tdxman/mac/enums.py)
