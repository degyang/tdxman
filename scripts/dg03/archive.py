#!/usr/bin/env python3
"""Archive small raw evidence and bind it to source; never copy lab databases."""

import json
import shutil
import subprocess
from pathlib import Path

from bench import LAB, SNAPSHOT, file_sha256

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/evidence/data-remediation/20260927-dg03"


def main():
    DEST.mkdir(exist_ok=True, parents=True)
    for path in LAB.iterdir():
        if path.is_file() and path.suffix in {".json", ".jsonl", ".log"}:
            assert path.stat().st_size < 32 * 1024**2, path
            shutil.copy2(path, DEST / path.name)
    for path in Path("/tmp").glob("dg03-tests*.txt"):
        shutil.copy2(path, DEST / (path.stem + ".log"))
    shutil.copy2("/tmp/tdxman-dg00-constraints.txt", DEST / "constraints.txt")
    sources = sorted(
        [
            *ROOT.glob("src/aspool/dg03*.py"),
            *ROOT.glob("scripts/dg03/*.py"),
            *ROOT.glob("tests/unit/test_dg03*.py"),
        ]
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tests = {
        str(p.relative_to(ROOT)): [
            line.split("(")[0].removeprefix("def ")
            for line in p.read_text().splitlines()
            if line.startswith("def test_")
        ]
        for p in ROOT.glob("tests/unit/test_dg03*.py")
    }
    schema = LAB / "month-clustered/source-schema.json"
    inventory = dict(
        archive_head=head,
        snapshot=str(SNAPSHOT),
        source_manifest_sha256=file_sha256(SNAPSHOT.parent / "manifest.json"),
        import_expected=str(ROOT / "src/aspool/__init__.py"),
        environment=str(ROOT / ".venv"),
        code={str(p.relative_to(ROOT)): file_sha256(p) for p in sources},
        tests=tests,
        validation_policy=(
            "Incremental affected checks; no claim of one final full-suite run; DG00-02 not rerun"
        ),
        test_logs=[p.name for p in DEST.glob("*test*.log")],
        raw={
            p.name: dict(bytes=p.stat().st_size, sha256=file_sha256(p))
            for p in sorted(DEST.iterdir())
            if p.is_file() and p.name != "inventory.json"
        },
        source_schema=dict(
            path=str(schema), bytes=schema.stat().st_size, sha256=file_sha256(schema)
        ),
        large_databases=[
            dict(path=str(p), bytes=p.stat().st_size) for p in sorted(LAB.glob("*/catalog.duckdb"))
        ],
        monthly_build_manifests={},
    )
    for path in sorted(LAB.glob("monthly-*x/build-manifest.json")):
        name = path.parent.name + "-build-manifest.json"
        shutil.copy2(path, DEST / name)
        inventory["monthly_build_manifests"][name] = dict(
            source=str(path), sha256=file_sha256(path)
        )
    (DEST / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                destination=str(DEST),
                head=head,
                source_files=len(sources),
                test_cases=sum(map(len, tests.values())),
                raw_files=len(inventory["raw"]),
            )
        )
    )


if __name__ == "__main__":
    main()
