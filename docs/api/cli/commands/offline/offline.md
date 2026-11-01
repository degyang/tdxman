# `tdxman offline`

```bash
tdxman offline 600519.SH --period DAILY --count 30 --format table
tdxman offline 000001.SZ --period 5MIN --vipdoc /path/to/vipdoc --format json --output output/000001.json
```

标的用 `CODE.SH` / `CODE.SZ` 或 `SH CODE` / `SZ CODE`。`--period` 仅接受 `DAILY`（默认）、`1MIN`、`5MIN`；`--count` 默认 800、至少 1；`--start` 默认 0、至少 0，是距本地最新记录的偏移。`--vipdoc` 必须指向已存在目录；不传时按 `TDXMAN_VIPDOC`、配置文件 `offline.vipdoc`、`TDX_HOME` / 常见安装目录寻找；`TDXMAN_SETTINGS` 可改配置文件路径。当前文件定位器只支持 SH/SZ，BJ 会报不支持市场。

CLI 分别调用 `find_daily_bar_file` + `read_daily_bars`、`find_lc1_bar_file` + `read_lc_min_bars`、`find_5min_bar_file` + `read_5min_bars`，随后从本地已读记录尾部按 `--start/--count` 截取。5 分钟路径是 `.5` 文件，1 分钟是 `.lc1`；Python 离线模块另有 `.lc5` 读取能力，但此 CLI 未开放。缺文件报错；空文件可能输出 `[]`，需核对文件存在、大小及下载日期。离线文件不能保证当日最新或已收盘。

CLI：`src/tdxman/cli/cmd_offline.py`；API：`src/tdxman/offline/{paths,finders,daily_bar,min_bar}.py`；配置：[项目 README](../../../../../README.md)「CLI 参考」「离线数据读取」。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
