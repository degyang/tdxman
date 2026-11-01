# 本地文件读取 API

[总入口](../README.md) · [类型与字段](../models.md)

从 `tdxman.offline` 导入下列函数。读取发生在本地文件系统，不需要行情服务器；函数返回模型列表或 `Path`，不是 DataFrame。文件需要先由通达信或其他合法来源准备好。与 [tdxpy Reader](https://tdxpy.readthedocs.io/zh-cn/latest/api/reader/)处理同类文件，但函数名与返回模型以本仓库为准。

## 定位文件

| 函数 | 输入 | 返回 |
| --- | --- | --- |
| `detect_tdx_home()` | 无 | 探测到的安装目录 `Path`，找不到时为 `None` |
| `resolve_vipdoc(path=None)` | 可显式传入 vipdoc 目录 | 有效 `Path`，找不到则抛 `TdxOfflineError` |
| `find_daily_bar_file(market, code, vipdoc=None)` | 沪深市场代码、股票代码及可选目录 | `sh/lday/shXXXXXX.day` 或 `sz/lday/szXXXXXX.day` 路径 |
| `find_5min_bar_file(market, code, vipdoc=None)` | 同上 | `.5` 路径 |
| `find_lc1_bar_file(market, code, vipdoc=None)` | 同上 | `.lc1` 路径 |
| `find_lc5_bar_file(market, code, vipdoc=None)` | 同上 | `.lc5` 路径 |

`resolve_vipdoc` 优先显式路径，其后为 `TDXMAN_VIPDOC` 或 `settings/config.yaml` 的 `offline.vipdoc`，再根据 `TDX_HOME`/常见安装路径寻找。路径查找函数构造目标路径；真正读取时才检查文件存在。目录定位只支持沪深市场码，`Market.BJ` 会报 `TdxOfflineError`。

## 读取内容

| 函数 | 文件类型 | 返回与解析要点 |
| --- | --- | --- |
| `read_daily_bars(filepath)` | 沪深 `.day` | `list[SecurityBar]`，按文件顺序；价格/量系数按文件名推断品种 |
| `read_5min_bars(filepath)` | `.5` | `list[SecurityBar]`，OHLC 整数除以 100 |
| `read_lc_min_bars(filepath)` | `.lc1` / `.lc5` | `list[SecurityBar]`，OHLC 直接读取浮点值 |
| `read_ex_daily_bars(filepath)` | `vipdoc/ds` 扩展市场 `.day` | `list[ExDailyBar]`，含结算价和港股量解释字段 |
| `read_block_dat(filepath)` | 板块 `.dat` | `list[TdxBlock]` |
| `read_customer_blocks(block_dir)` | 自定义板块目录 `blocknew.cfg` + `.blk` | `list[CustomerBlock]` |
| `read_gbbq(filepath)` | 股本变迁文件 | `list[GbbqRecord]` |
| `read_history_financial(filepath)` | `gpcw*.dat` 或 `gpcw*.zip` | `list[FinancialRecord]`；ZIP 读取首个 `.dat` |

`FinancialRecord` 含六位 `code`、`market`、`report_date`（`YYYYMMDD`）及浮点数组 `fields`；数组位置仍需结合通达信财报字段定义解释，不能直接按普通财务摘要列名使用。[完整模型字段索引](../schema-fields.md)还列出板块、股本变迁和扩展日线模型。

```python
from tdxman import Market
from tdxman.offline import find_daily_bar_file, read_daily_bars

path = find_daily_bar_file(Market.SH, "600519")
bars = read_daily_bars(path)
print(path, len(bars), bars[-1] if bars else None)
```

文件缺失抛 `TdxFileNotFoundError`；目录/格式问题可能抛 `TdxOfflineError`。`read_history_financial` 遇到未知市场字节会回退为 `Market.SZ`，因此涉及跨市场核对时应保留原始文件以复查。读取器不会自动更新本机 vipdoc。源码：`src/tdxman/offline/`。
