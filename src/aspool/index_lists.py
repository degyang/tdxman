"""Manual, reviewable index universe maintenance."""

import json
from pathlib import Path

from tdxman.mac.enums import BoardType, Category, SortOrder, SortType

DEFAULT_LIST = Path(__file__).resolve().parents[2] / "settings/board_index.json"
COMMON_INDICES = {
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
    for source in ("HY", "HY2", "GN", "FG", "ZS"):
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
            market = {0: "SZ", 1: "SH"}.get(row["market"])
            if market is None and source == "ZS":
                continue
            if market is None:
                raise ValueError(f"不支持的指数市场: {row['market']}")
            key = (market, str(row["code"]))
            if source == "ZS" and key not in COMMON_INDICES:
                continue
            if row["name"].startswith("昨日"):
                excluded.append({"market": market, "code": key[1], "name": row["name"]})
                continue
            entry = records.setdefault(
                key, {"market": market, "code": key[1], "name": row["name"], "source": []}
            )
            entry["source"].append(source)
    missing = COMMON_INDICES - records.keys()
    if missing:
        # Some servers expose a capped ZS directory. Resolve configured codes explicitly.
        frame = client.get_stock_quotes([(1 if m == "SH" else 0, c) for m, c in sorted(missing)])
        for row in frame.to_dict("records"):
            key = ({0: "SZ", 1: "SH"}[row["market"]], str(row["code"]))
            if key in missing and row["name"]:
                records[key] = {
                    "market": key[0],
                    "code": key[1],
                    "name": row["name"],
                    "source": ["ZS"],
                }
        if COMMON_INDICES - records.keys():
            raise ValueError(f"常用指数缺失: {sorted(COMMON_INDICES - records.keys())}")
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
    rows = json.loads(Path(path).read_text())["indices"]
    seen = set()
    for row in rows:
        key = (row["market"], row["code"])
        if (
            key in seen
            or key[0] not in ("SH", "SZ")
            or len(key[1]) != 6
            or not key[1].isascii()
            or not key[1].isdigit()
            or not row["source"]
        ):
            raise ValueError(f"指数清单记录无效或重复: {row}")
        if row["name"].startswith("昨日"):
            raise ValueError(f"清单包含黑名单指数: {row}")
        seen.add(key)
    if not rows:
        raise ValueError("指数清单为空，请先运行 scripts/maintain_board_lists.py --write")
    return rows
