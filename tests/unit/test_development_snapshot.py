"""Safety and integrity checks for portable migration inputs."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "development_snapshot",
    Path(__file__).parents[2] / "scripts/ops/snapshot_development_data.py",
)
ops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ops)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "old"
    (root / "lake").mkdir(parents=True)
    (root / "catalog.duckdb").write_bytes(b"catalog")
    (root / "lake/bars.parquet").write_bytes(b"bars")
    (root / "reports").mkdir()
    (root / "reports/old.txt").write_text("not migration input")
    return root


def test_copy_verify_and_corruption(source, tmp_path):
    target = tmp_path / "new"
    result = ops.snapshot(source, target)
    assert result["files"] == 2
    assert ops.verify(target) == {"files": 2, "bytes": 11}
    assert not (target / "reports").exists()
    assert (source / "lake/bars.parquet").read_bytes() == b"bars"
    (target / "lake/bars.parquet").write_bytes(b"BARS")
    with pytest.raises(ValueError, match="differs"):
        ops.verify(target)


def test_refuses_existing_or_overlapping_target(source, tmp_path):
    with pytest.raises(ValueError, match="separate"):
        ops.snapshot(source, source / "copy")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        ops.snapshot(source, existing)


@pytest.mark.parametrize("hazard", ["wal", "pending", "symlink"])
def test_refuses_unstable_source(source, tmp_path, hazard):
    if hazard == "wal":
        (source / "catalog.duckdb.wal").touch()
    elif hazard == "pending":
        (source / "change-state/pending/op").mkdir(parents=True)
    else:
        (source / "lake/link").symlink_to(source / "catalog.duckdb")
    with pytest.raises(Exception):
        ops.snapshot(source, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_verify_refuses_extra_files(source, tmp_path):
    target = tmp_path / "new"
    ops.snapshot(source, target)
    (target / "unexpected").touch()
    with pytest.raises(ValueError, match="unexpected"):
        ops.verify(target)
