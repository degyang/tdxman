"""Independent complete-replica checks using only small temporary fixtures."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_data_replica", REPOSITORY / "scripts/ops/verify_data_replica.py"
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _write(path, value):
    path.write_text(json.dumps(value))


def _seal(root, manifest_path, evidence_path):
    names = ("stocks.sqlite", "catalog.duckdb", "indices.sqlite", "etfs.sqlite")
    paths = [root / name for name in names]
    entries = [
        dict(
            path=path.relative_to(root).as_posix(),
            bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
        if path.is_file()
    ]
    _write(
        manifest_path,
        dict(format_version=1, files=entries, bytes=sum(item["bytes"] for item in entries)),
    )
    stock = entries[0]
    _write(
        evidence_path,
        dict(
            database_sha256=stock["sha256"],
            database_bytes=stock["bytes"],
            integrity_check="ok",
            daily_calculation_complete=True,
            rows=dict(daily_bars=1, daily_features=1, corporate_actions=0, market_daily_summary=1),
        ),
    )


@pytest.fixture
def replica(tmp_path):
    root = tmp_path / "replica"
    root.mkdir()
    (root / "catalog.duckdb").write_bytes(b"closed catalog fixture; only the digest is checked")
    for name in ("indices.sqlite", "etfs.sqlite"):
        with sqlite3.connect(root / name) as conn:
            conn.execute("PRAGMA user_version=1")
    with sqlite3.connect(root / "stocks.sqlite") as conn:
        conn.executescript((REPOSITORY / "src/aspool/stocks_schema.sql").read_text())
        conn.execute("""INSERT INTO daily_bars
            (symbol,trade_date,open,high,low,close,amount,updated_at)
            VALUES ('000001.SZ','2024-01-02',10,11,9,10,1000,100)""")
        conn.execute("""INSERT INTO daily_features
            (symbol,trade_date,pre_close,is_st,calc_status,limit_status,streak_known,updated_at)
            VALUES ('000001.SZ','2024-01-02',9,0,'TRADED','UNKNOWN',0,100)""")
        conn.execute("""INSERT INTO market_daily_summary
            (frequency,period_key,scope,period_start,period_end,as_of,session_count,
             trading_count,updated_at)
            VALUES ('D','2024-01-02','all_stocks','2024-01-02','2024-01-02',
                    '2024-01-02',1,1,100)""")
    manifest, evidence = tmp_path / "manifest.json", tmp_path / "derived-result.json"
    _seal(root, manifest, evidence)
    return root, manifest, evidence


def test_verified_replica_reuses_evidence_and_performs_only_bounded_reads(replica, monkeypatch):
    root, manifest, evidence = replica
    before = {path: verifier._signature(path.stat()) for path in root.rglob("*") if path.is_file()}
    statements = []
    original_connect = sqlite3.connect

    def connect(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro&immutable=1") and kwargs == {"uri": True}
        conn = original_connect(database_uri, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(verifier.sqlite3, "connect", connect)
    result = verifier.verify(*replica)
    assert result["status"] == "verified" and result["files"] == 4
    assert result["bytes"] == json.loads(manifest.read_text())["bytes"]
    assert result["rows"] == json.loads(evidence.read_text())["rows"]
    assert (
        result["integrity_check_origin"]
        == result["rows_origin"]
        == "source_reused_after_sha256_match"
    )
    assert result["local_row_counts_run"] is False
    assert result["local_full_integrity_run"] is False
    assert result["samples"]["daily_bars"][0]["close"] == 10
    assert result["samples"]["daily_features"][0]["pre_close"] == 9
    assert result["samples"]["market_daily_summary"][0]["frequency"] == "D"
    assert all(
        "integrity_check" not in sql.lower() and "count(" not in sql.lower() for sql in statements
    )
    data_selects = [
        sql for sql in statements if sql.startswith("SELECT") and "sqlite_master" not in sql
    ]
    assert len(data_selects) == 3 and all("LIMIT 5" in sql for sql in data_selects)
    assert before == {
        path: verifier._signature(path.stat()) for path in root.rglob("*") if path.is_file()
    }
    assert not (root / "stocks.sqlite-wal").exists()
    assert not (root / "stocks.sqlite-shm").exists()


@pytest.mark.parametrize("corruption", ["hash", "size", "manifest_total"])
def test_manifest_hash_and_size_mismatch_fail(replica, corruption):
    root, manifest, _ = replica
    target = root / "etfs.sqlite"
    if corruption == "hash":
        original = target.read_bytes()
        target.write_bytes(b"X" + original[1:])
        message = "SHA-256 differs"
    elif corruption == "size":
        target.write_bytes(target.read_bytes() + b"extra")
        message = "size differs"
    else:
        document = json.loads(manifest.read_text())
        document["bytes"] += 1
        _write(manifest, document)
        message = "total bytes"
    with pytest.raises(ValueError, match=message):
        verifier.verify(*replica)


@pytest.mark.parametrize("extra", ["scratch/extra.bin", "unexpected.duckdb"])
def test_extra_business_file_rejected(replica, extra):
    path = replica[0] / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unlisted business file")
    with pytest.raises(ValueError, match="extra business files"):
        verifier.verify(*replica)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/tmp/outside",
        "nested/../outside",
        "nested//x",
        "nested/./x",
        "C:/outside",
        "C:\\outside",
        "",
    ],
)
def test_manifest_path_traversal_and_noncanonical_paths_rejected(replica, path):
    document = json.loads(replica[1].read_text())
    document["files"][2]["path"] = path
    _write(replica[1], document)
    with pytest.raises(ValueError, match="manifest path|relative POSIX path"):
        verifier.verify(*replica)


def test_duplicate_manifest_path_and_missing_mandatory_file_rejected(replica):
    document = json.loads(replica[1].read_text())
    duplicate = dict(document, files=[*document["files"], document["files"][0]])
    duplicate["bytes"] += document["files"][0]["bytes"]
    _write(replica[1], duplicate)
    with pytest.raises(ValueError, match="Duplicate manifest path"):
        verifier.verify(*replica)
    document["files"] = document["files"][1:]
    document["bytes"] = sum(item["bytes"] for item in document["files"])
    _write(replica[1], document)
    with pytest.raises(ValueError, match="must contain stocks.sqlite"):
        verifier.verify(*replica)


@pytest.mark.parametrize("kind", ["file_inside", "file_outside", "directory", "root"])
def test_symlink_file_directory_or_root_rejected(replica, tmp_path, kind):
    root, manifest, evidence = replica
    if kind == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        call = alias, manifest, evidence
    elif kind == "directory":
        (root / "link").symlink_to(tmp_path, target_is_directory=True)
        call = replica
    else:
        outside = tmp_path / "outside"
        outside.write_bytes(b"outside")
        target = root / "catalog.duckdb" if kind == "file_inside" else outside
        (root / "link").symlink_to(target)
        call = replica
    with pytest.raises(ValueError, match="Symlink"):
        verifier.verify(*call)


@pytest.mark.parametrize("database", ["stocks", "indices", "etfs"])
def test_nonempty_wal_rejected_but_existing_empty_sidecars_remain_unchanged(replica, database):
    root = replica[0]
    wal = root / f"{database}.sqlite-wal"
    shm = root / f"{database}.sqlite-shm"
    wal.write_bytes(b"uncheckpointed data")
    with pytest.raises(ValueError, match="Nonempty SQLite WAL"):
        verifier.verify(*replica)
    wal.write_bytes(b"")
    shm.write_bytes(b"existing shared memory file")
    before = (verifier._signature(wal.stat()), verifier._signature(shm.stat()))
    assert verifier.verify(*replica)["status"] == "verified"
    assert before == (verifier._signature(wal.stat()), verifier._signature(shm.stat()))


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_sha256", "0" * 64),
        ("database_bytes", 1),
        ("integrity_check", "failed"),
        ("daily_calculation_complete", False),
        ("daily_calculation_complete", 1),
        ("rows", {}),
        (
            "rows",
            dict(daily_bars=1, daily_features=1, corporate_actions=0, market_daily_summary=False),
        ),
    ],
)
def test_integrity_evidence_must_match_and_prove_complete_derivation(replica, field, value):
    document = json.loads(replica[2].read_text())
    document[field] = value
    _write(replica[2], document)
    with pytest.raises(ValueError):
        verifier.verify(*replica)


@pytest.mark.parametrize("hazard", ["version", "extra_table", "missing_table", "no_daily_summary"])
def test_schema_and_sample_failures_cannot_be_reported_verified(replica, hazard):
    root, manifest, evidence = replica
    with sqlite3.connect(root / "stocks.sqlite") as conn:
        conn.execute(
            {
                "version": "PRAGMA user_version=2",
                "extra_table": "CREATE TABLE extra(n INT)",
                "missing_table": "DROP TABLE daily_features",
                "no_daily_summary": "UPDATE market_daily_summary SET frequency='W'",
            }[hazard]
        )
    _seal(root, manifest, evidence)
    with pytest.raises(ValueError, match="user_version|schema|sample rows"):
        verifier.verify(*replica)


def test_stat_change_during_streaming_hash_is_rejected(replica, monkeypatch):
    target = replica[0] / "catalog.duckdb"
    original_hash = hashlib.sha256
    changed = False

    class MutatingHash:
        def __init__(self):
            self.inner = original_hash()

        def update(self, block):
            nonlocal changed
            self.inner.update(block)
            if not changed:
                changed = True
                target.write_bytes(target.read_bytes() + b"changed during read")

        def hexdigest(self):
            return self.inner.hexdigest()

    monkeypatch.setattr(verifier.hashlib, "sha256", MutatingHash)
    with pytest.raises(ValueError, match="changed while reading"):
        verifier.verify(*replica)


def test_extra_file_appearing_after_hashing_is_rejected(replica, monkeypatch):
    root = replica[0]
    original_samples = verifier._sqlite_samples

    def samples(path):
        result = original_samples(path)
        (root / "late-file").write_bytes(b"concurrent transfer")
        return result

    monkeypatch.setattr(verifier, "_sqlite_samples", samples)
    with pytest.raises(ValueError, match="changed during verification"):
        verifier.verify(*replica)


def test_control_file_in_data_root_is_still_required_in_manifest(replica):
    root, manifest, evidence = replica
    local_evidence = root / "derived-result.json"
    local_evidence.write_bytes(evidence.read_bytes())
    with pytest.raises(ValueError, match="extra business files"):
        verifier.verify(root, manifest, local_evidence)


@pytest.mark.parametrize("report_location", ["outside", "local_reports"])
def test_cli_success_then_failure_replaces_stale_success_report(
    replica,
    tmp_path,
    capsys,
    report_location,
):
    root, manifest, evidence = replica
    report = (
        tmp_path / "replica-verification.json"
        if report_location == "outside"
        else tmp_path / ".local/reports/replica-verification.json"
    )
    argv = [
        "--root",
        str(root),
        "--manifest",
        str(manifest),
        "--integrity-evidence",
        str(evidence),
        "--report",
        str(report),
    ]
    assert verifier.main(argv) == 0
    assert json.loads(report.read_text())["status"] == "verified"
    capsys.readouterr()
    (root / "unexpected").write_bytes(b"extra")
    assert verifier.main(argv) == 1
    assert json.loads(report.read_text())["status"] == "failed"
    captured = capsys.readouterr()
    assert captured.out == "" and '"status": "failed"' in captured.err


def test_cli_sqlite_read_exception_is_failure_and_report_cannot_overwrite_controls(
    replica,
    tmp_path,
    monkeypatch,
):
    root, manifest, evidence = replica
    report = tmp_path / "failed.json"
    argv = [
        "--root",
        str(root),
        "--manifest",
        str(manifest),
        "--integrity-evidence",
        str(evidence),
        "--report",
        str(report),
    ]

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected SQLite read failure")

    monkeypatch.setattr(verifier.sqlite3, "connect", fail)
    assert verifier.main(argv) == 1
    assert json.loads(report.read_text())["status"] == "failed"
    before = evidence.read_bytes()
    assert verifier.main([*argv[:-1], str(evidence)]) == 1
    assert evidence.read_bytes() == before


@pytest.mark.parametrize("hazard", ["manifested_report", "hardlinked_report"])
def test_cli_report_cannot_modify_a_replica_artifact(replica, tmp_path, hazard):
    root, manifest, evidence = replica
    if hazard == "manifested_report":
        report = root / "_reports/artifact.json"
        report.parent.mkdir()
        report.write_bytes(b"authoritative listed metadata")
        document = json.loads(manifest.read_text())
        document["files"].append(
            dict(
                path="_reports/artifact.json",
                bytes=report.stat().st_size,
                sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
            )
        )
        document["bytes"] += report.stat().st_size
        _write(manifest, document)
    else:
        report = tmp_path / "stock-alias.json"
        report.hardlink_to(root / "stocks.sqlite")
    before = {
        path: path.read_bytes() for path in (report, root / "stocks.sqlite", manifest, evidence)
    }
    assert (
        verifier.main(
            [
                "--root",
                str(root),
                "--manifest",
                str(manifest),
                "--integrity-evidence",
                str(evidence),
                "--report",
                str(report),
            ]
        )
        == 1
    )
    assert before == {path: path.read_bytes() for path in before}


def test_cli_cannot_write_report_back_to_data_root(replica):
    root, manifest, evidence = replica
    report = root / "_reports/rejected.json"
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert (
        verifier.main(
            [
                "--root",
                str(root),
                "--manifest",
                str(manifest),
                "--integrity-evidence",
                str(evidence),
                "--report",
                str(report),
            ]
        )
        == 1
    )
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before
