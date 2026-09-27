"""Verify transferred public-domain files and representative existing SDK reads."""

import argparse
import hashlib
import json
from pathlib import Path

from aspool import DataPool


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify(root):
    manifest = json.loads((root / "_reports/public-data-manifest.json").read_text())
    for entry in manifest["files"]:
        path = root / entry["path"]
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError(f"Public-domain file differs: {entry['path']}")
    references = json.loads((root / "_reports/legacy-reference-candidates.json").read_text())
    if digest(root / references["path"]) != references["sha256"]:
        raise ValueError("Legacy reference evidence differs")
    pool = DataPool(root)
    fields = ["symbol", "date", "close", "amount"]
    samples = {
        "index": pool.read_index_daily(symbols="000001.SH", end="2026-09-24",
                                       lookback=5, fields=fields),
        "etf": pool.read_etf_daily(symbols="159915.SZ", end="2026-09-24",
                                   lookback=5, fields=fields),
        "security": pool.read_security_info(symbols=["000001.SZ"]),
        "calendar": pool.read_trading_calendar(start="2026-09-18", end="2026-09-24"),
    }
    if any(frame.empty for frame in samples.values()):
        raise ValueError("A required public-domain smoke sample is empty")
    return {"status": "verified", "files": len(manifest["files"]),
            "bytes": manifest["bytes"], "legacy_reference_rows": references["rows"],
            "sample_rows": {name: len(frame) for name, frame in samples.items()},
            "samples": {name: json.loads(frame.to_json(orient="records", date_format="iso",
                                                       double_precision=15))
                        for name, frame in samples.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.target_root.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}))


if __name__ == "__main__":
    main()
