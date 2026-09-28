#!/usr/bin/env python3
"""Migrate legacy universe/lifecycle rows to catalog.securities."""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from aspool.securities import ensure_securities


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    source = root / "catalog.duckdb"
    if not source.is_file():
        parser.error(f"catalog does not exist: {source}")
    backup = args.backup or (
        root.parent / ".local" / "recovery" /
        (
            "catalog-before-securities-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + ".duckdb"
        )
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        parser.error(f"backup already exists: {backup}")
    shutil.copy2(source, backup)
    result = ensure_securities(root)
    print(json.dumps({**result, "catalog": str(source), "backup": str(backup)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
