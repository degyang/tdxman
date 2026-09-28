"""Registry for the cross-market (ex) data domain."""

import json
from importlib.resources import files
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "settings/ex_assets.json"


def load_ex_categories(path=None):
    """Read wheel-contained defaults or an explicitly supplied configuration.

    Source checkouts keep settings/ex_assets.json as the build input; Hatch
    places the same file under aspool/resources in installed wheels.
    """
    if path is None:
        bundled = files("aspool").joinpath("resources", "ex_assets.json")
        text = (bundled if bundled.is_file() else DEFAULT_CONFIG).read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    document = json.loads(text)
    if document.get("domain") != "ex":
        raise ValueError("扩展市场配置 domain 必须为 ex")
    rows = document.get("categories", [])
    seen = set()
    for row in rows:
        code = row.get("code")
        if (
            code in seen
            or not code
            or row.get("asset_kind") not in {"security", "futures", "index"}
        ):
            raise ValueError(f"扩展市场类别无效或重复: {row}")
        if row.get("status") not in {"implemented", "planned"}:
            raise ValueError(f"扩展市场类别状态无效: {row}")
        seen.add(code)
    return rows


def ex_category(code):
    code = code.upper()
    for row in load_ex_categories():
        if row["code"] == code:
            return row
    raise ValueError(f"未知扩展市场类别: {code}")
