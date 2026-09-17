---
name: tdxman
description: Use the tdxman command-line client to retrieve TongDaXin A-share, Hong Kong, US, and futures market data. Use when a task needs this repository's CLI rather than direct Python API calls.
---

# tdxman

`tdxman` is the CLI provided by this repository. It fetches online TongDaXin market data and writes JSON by default.

## Run the CLI

From the repository root, activate the project environment and install the editable package when the command entry point is missing or has changed:

```bash
source .venv/bin/activate
python -m pip install -e .
rehash
tdxman --help
```

Use `SH`, `SZ`, or `BJ` for A-share markets. `SH 000001` is the Shanghai Composite Index; `SZ 000001` is Ping An Bank.
Run `tdxman markets` to list these codes and examples.

All data commands write JSON to stdout by default. Use `--format json|table|csv` to select
the format. `--format table` keeps the terminal grid table; when written to a file it creates
a Markdown table (`.md`). Use `--output` for a file or directory. With a directory, the default
file name is the symbol; without `--format`, files default to JSON.

```bash
tdxman finance SZ 006324 --format json --output data/006324.json
tdxman kline SH 600519 --format csv --output data
tdxman quote "SZ 000001,SH 600519" --format table --output data/quotes
```

For multi-symbol `quote`, `--output` must be a directory; it creates one response file per symbol.
Tables show common fields by default; pass `--fields all` for every returned field. JSON and CSV always include every field.

```bash
tdxman quote "SZ 000001,SH 600519" --format table
tdxman kline SH 600519 --period DAILY --count 30 --format table
tdxman tick SH 600519 --days 5 --format table
```

CLI output normalizes numeric presentation: prices, amounts, market caps, finance amounts, and fund-flow amounts use two decimal places; quantities and counts use integers; other ratios retain up to four decimal places. JSON values remain numeric, so insignificant trailing zeroes may not appear.

## Commands

```bash
# Connectivity and general market data
tdxman ping --timeout 3 --format table
tdxman version
tdxman quote "SZ 000001,SH 600519"
# quote-list categories: SH/SZ=Shanghai/Shenzhen A shares, A=all A shares, B=B shares,
# KCB=STAR Market, CYB=ChiNext, BJ=Beijing Stock Exchange, ETF/LOF=funds,
# HGT/SGT=Shanghai/Shenzhen Stock Connect constituents, FXJS=risk warning, ZS=standard indices.
tdxman quote-list A --count 20 --sort CHANGE_PCT --order DESC --format table
tdxman kline SZ 000001 --period 5MIN --count 30 --adjust QFQ
tdxman tick SZ 000001 --date 20250115
tdxman transaction SH 600519 --count 100 --format table

# Index categories and discovery
# TongDaXin sector/research indices (mostly 881xxx): all / level-1 and level-2
# industry / concept / style. This is not the full standard-index directory.
tdxman board-list --format table
tdxman board-list --type HY --format table
tdxman board-list --type HY2 --format table
tdxman board-list --type GN --format table
tdxman board-list --type FG --format table
tdxman board-list --type DQ --format table
# OTHER and YJ_LEVEL1/2/3 are named board categories.
tdxman board-list --type OTHER --format table
tdxman board-list --type YJ_LEVEL1 --format table
tdxman board-list --type YJ_LEVEL2 --format table
tdxman board-list --type YJ_LEVEL3 --format table
tdxman board-members 881001 --format table  # live component quotes
tdxman board-members 000699 --count 20 --format table  # supported standard-index constituents
tdxman belong-board SZ 000001 --format table # security's boards

# Standard A-share indices: Shanghai, Shenzhen, and CNI indices
# (for example 000300, 000688, 399001, 399006)
tdxman board-list --type ZS --count 600 --format table
tdxman quote-list ZS --count 600 --format table
tdxman kline SH 000300 --period DAILY --count 120 --format table
tdxman kline SZ 399001 --period DAILY --count 120 --format table
# Standard-index K lines include up_count/down_count under the index's own breadth scope.

# Extended index categories: discover code first, then run `ex kline MARKET CODE ...`
tdxman ex quote-list CSI_INDEX --count 600 --format table       # CSI
tdxman ex quote-list SZSE_INDEX --count 600 --format table      # CNI
tdxman ex quote-list HK_INDEX --count 600 --format table        # Hong Kong
tdxman ex quote-list INTL_INDEX --count 600 --format table      # international
tdxman ex quote-list FUTURES_INDEX --count 600 --format table   # commodity
tdxman ex quote-list RISK_CONTROL_INDEX --count 600 --format table
tdxman ex quote-list HUAZHENG_INDEX --count 600 --format table
tdxman ex quote-list EXTENDED_SECTOR_INDEX --count 600 --format table
tdxman ex kline CSI_INDEX 000300 --period DAILY --count 120 --format table

tdxman capital-flow SH 600519 --format table
tdxman auction SZ 000001 --format table
tdxman unusual SH --count 100 --format table
tdxman market-stat --format table
tdxman server-info --format table
tdxman symbol-info SZ 000001 --format table

# Financial data
tdxman finance SH 600519 --format table
tdxman fund-flow SZ 000001 --count 20 --closed-only --format table

# Hong Kong, US, and futures markets
tdxman ex markets
tdxman ex kline HK_MAIN_BOARD 00700 --count 30 --format table
tdxman ex quote US_STOCK TSLA --format table
tdxman ex quote-list SH_FUTURES --count 100 --format table
tdxman ex tick HK_MAIN_BOARD 00700 --format table
```

`finance` returns the latest structured financial summary, including share capital, balance sheet, profit, and per-share fields. It does not return the complete text-based F10 document catalogue.

`fund-flow` returns historical daily flows grouped into `super_*`, `large_*`, `medium_*`, and `small_*` fields, in yuan. `--start` is the offset from the newest record, not a date; the server returns the selected rows in chronological order. Use `--closed-only` to omit the current trading day's potentially incomplete row. It is for individual securities, not indices: index transactions have neutral direction and cannot produce directional fund flows.

## Protocol and local data boundaries

The CLI uses MAC protocol clients for normal A-share and extended-market queries. `finance` and `fund-flow` use the standard protocol APIs; the historical fund-flow fallback uses MAC daily bars when standard day-bar responses are incomplete.

Use `tdxman offline MARKET CODE --period DAILY|1MIN|5MIN` for local K lines. The default vipdoc path is `offline.vipdoc` in `settings/config.yaml`; use `--vipdoc PATH` to override it. Python module `tdxman.offline` also supports `.lc1`, `.lc5`, block, capital-change, and historical finance files.
