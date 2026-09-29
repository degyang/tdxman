"""Live index directory collection for industry, concept, style, and standard indices."""

import json
from pathlib import Path

from tdxman.mac.enums import BoardType, Category, SortOrder, SortType

DEFAULT_LIST = Path(__file__).resolve().parents[2] / "settings/board_index.json"
BENCHMARK_SYMBOLS = {
    ("SH", "000001"),
    ("SH", "000016"),
    ("SH", "000300"),
    ("SH", "000905"),
    ("SH", "000852"),
    ("SH", "000688"),
    ("SH", "000698"),
    ("SZ", "399001"),
    ("SZ", "399006"),
    ("SZ", "399005"),
    ("SZ", "399106"),
    ("SZ", "399330"),
    ("SZ", "399673"),
}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def collect(client):
    records = {}
    excluded = []
    for source in ("HY2", "GN", "FG", "ZS"):
        frame = (
            client.get_stock_quotes_list(
                Category.ZS, count=10000, sort_type=SortType.CODE, sort_order=SortOrder.ASC
            )
            if source == "ZS"
            else client.get_board_list(BoardType[source], count=10000)
        )
        if frame.empty:
            raise ValueError(f"{source} 返回空清单，保留原配置")
        for row in frame.to_dict("records"):
            market = {0: "SZ", 1: "SH", 2: "BJ"}.get(row.get("market"))
            if market is None:
                raise ValueError(f"不支持的指数市场: {row['market']}")
            code = str(row.get("code", ""))
            name = str(row.get("name", ""))
            if len(code) != 6 or not code.isascii() or not code.isdigit() or not name:
                raise ValueError(f"{source} 返回无效指数身份")
            # TDX's live standard-index directory exposes Shanghai Composite as 999999.
            if source == "ZS" and (market, code) == ("SH", "999999"):
                code = "000001"
            key = (market, code)
            if source == "FG" and name.startswith("昨日"):
                excluded.append({"market": market, "code": key[1], "name": row["name"]})
                continue
            entry = records.setdefault(
                key, {"market": market, "code": key[1], "name": name, "source": []}
            )
            entry["source"].append(source)
    return {
        "schema_version": 1,
        "blacklist_name_prefixes": ["昨日"],
        "indices": [records[k] for k in sorted(records)],
    }, excluded


def difference(old, new):
    before = {(r["market"], r["code"]): r for r in old.get("indices", [])}
    after = {(r["market"], r["code"]): r for r in new["indices"]}
    return {
        "added": [after[k] for k in sorted(after.keys() - before.keys())],
        "removed": [before[k] for k in sorted(before.keys() - after.keys())],
        "changed": [
            {"before": before[k], "after": after[k]}
            for k in sorted(before.keys() & after.keys())
            if before[k] != after[k]
        ],
        "old_count": len(before),
        "new_count": len(after),
    }


def load_indices(path=DEFAULT_LIST):
    """Legacy Parquet-only helper; SQLite production sync never calls this function."""
    rows = json.loads(Path(path).read_text())["indices"]
    if not rows:
        raise ValueError("指数清单为空")
    return rows
