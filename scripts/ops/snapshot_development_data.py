"""Copy a consistent legacy pool for migration/development without copying old reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from aspool.change_protocol import assert_readable
from aspool.pool import pool_lock


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify(root: Path) -> dict:
    manifest = json.loads((root / "development-snapshot.json").read_text())
    expected = set()
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid relative path: {relative}")
        path = root / relative
        if path.resolve().is_relative_to(root.resolve()) is False or path.is_symlink():
            raise ValueError(f"Unsafe snapshot path: {relative}")
        if relative.as_posix() in expected:
            raise ValueError(f"Duplicate snapshot path: {relative}")
        expected.add(relative.as_posix())
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError(f"Snapshot differs: {relative}")
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != "development-snapshot.json"
    }
    if actual != expected:
        raise ValueError("Snapshot contains unexpected or missing files")
    return {"files": len(expected), "bytes": sum(x["bytes"] for x in manifest["files"])}


def snapshot(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate trees")
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite snapshot: {target}")
    entries = []
    with pool_lock(source):
        assert_readable(source)
        if (source / "catalog.duckdb.wal").exists():
            raise RuntimeError("Close the legacy catalog writer before making a snapshot")
        paths = [source / "catalog.duckdb", *sorted((source / "lake").rglob("*"))]
        if any(path.is_symlink() for path in paths) or (source / "lake").is_symlink():
            raise ValueError("Snapshot does not follow symlinks")
        paths = [path for path in paths if path.is_file()]
        if not (source / "catalog.duckdb").is_file():
            raise FileNotFoundError("Source catalog is missing")
        total = sum(path.stat().st_size for path in paths)
        target.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(target.parent).free < total + 1024**3:
            raise RuntimeError("Insufficient free space for snapshot and 1 GiB headroom")
        target.mkdir()
        for index, path in enumerate(paths, 1):
            relative = path.relative_to(source)
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            before = path.stat()
            shutil.copyfile(path, output)
            checksum = digest(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ) or digest(output) != checksum:
                raise RuntimeError(f"Source changed or copy differs: {relative}")
            entries.append({"path": relative.as_posix(), "bytes": before.st_size,
                            "sha256": checksum})
            if index % 500 == 0:
                print(json.dumps({"copied_files": index, "total_files": len(paths)}), flush=True)
        manifest = {
            "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source), "purpose": "legacy development and SQLite migration input",
            "excludes": ["reports", "change-state/applied", "backups"],
            "read_only_source": True, "files": entries,
        }
        temporary = target / "development-snapshot.json.tmp"
        with temporary.open("w") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target / "development-snapshot.json")
    return {"files": len(entries), "bytes": total, "target": str(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not args.verify and args.source_root is None:
        parser.error("--source-root is required when creating a snapshot")
    result = (
        verify(args.target_root) if args.verify else snapshot(args.source_root, args.target_root)
    )
    print(json.dumps({"status": "verified" if args.verify else "copied_and_verified", **result}))


if __name__ == "__main__":
    main()
