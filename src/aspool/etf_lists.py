"""Manual, reviewable ETF directory maintenance."""

import json
from pathlib import Path

from tdxman.mac.enums import Category, SortOrder, SortType

DEFAULT_LIST = Path(__file__).resolve().parents[2] / "settings/etf_list.json"


def collect(client):
    frame = client.get_stock_quotes_list(
        Category.ETF, count=6000, sort_type=SortType.CODE, sort_order=SortOrder.ASC
    )
    if frame.empty:
        raise ValueError("ETF 返回空清单，保留原配置")
    markets = {0: "SZ", 1: "SH"}
    rows = {}
    for row in frame.to_dict("records"):
        market = markets.get(row.get("market"))
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        if market not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit() or not name:
            continue
        rows[(market, code)] = {
            "market": market,
            "code": code,
            "name": name,
            "source": ["ETF"],
        }
    if not rows:
        raise ValueError("ETF 返回清单没有可用的沪深代码")
    return {
        "schema_version": 1,
        "asset_type": "etf",
        "etfs": [rows[key] for key in sorted(rows)],
    }, []


def difference(old, new):
    before = {(r["market"], r["code"]): r for r in old.get("etfs", [])}
    after = {(r["market"], r["code"]): r for r in new["etfs"]}
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


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def load_etfs(path=DEFAULT_LIST):
    rows = json.loads(Path(path).read_text()).get("etfs", [])
    seen = set()
    for row in rows:
        key = (row.get("market"), row.get("code"))
        if (
            key in seen
            or key[0] not in {"SH", "SZ"}
            or not isinstance(key[1], str)
            or len(key[1]) != 6
            or not key[1].isdigit()
            or not row.get("name")
            or row.get("source") != ["ETF"]
        ):
            raise ValueError(f"ETF 清单记录无效或重复: {row}")
        seen.add(key)
    if not rows:
        raise ValueError("ETF 清单为空，请先运行 maintain_board_lists.py --target etf --write")
    return rows
