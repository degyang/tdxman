from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "settings" / "config.yaml"


def read_config(path: Path = DEFAULT_CONFIG) -> dict[str, str]:
    """Read the small project YAML subset without adding a YAML dependency."""
    values: dict[str, str] = {}
    sections: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip())
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        depth = indentation // 2
        sections = sections[:depth]
        if value.strip():
            values[".".join([*sections, key])] = value.strip().strip("\"'")
        else:
            sections.append(key)
    return values


def free_stockdb_root() -> Path:
    return Path(read_config()["aspool.free_stockdb.root"]).expanduser().resolve()
