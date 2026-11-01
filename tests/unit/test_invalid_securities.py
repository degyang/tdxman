import json

import duckdb

from aspool.securities import active_securities, publish_directory


def test_explicit_invalid_code_survives_directory_refresh(tmp_path):
    root = tmp_path / "pool"
    root.mkdir()
    (root / "invalid-securities.json").write_text(json.dumps({"920162.BJ": {"reason": "no_kline"}}))
    entries = [
        dict(symbol="920162.BJ", code="920162", market="BJ", name="excluded"),
        dict(symbol="000001.SZ", code="000001", market="SZ", name="included"),
    ]
    for _ in range(2):
        publish_directory(root, entries, asset_type="stock")
        assert [row[0] for row in active_securities(root, "stock")] == ["000001.SZ"]
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
        assert conn.execute(
            "SELECT active FROM securities WHERE symbol='920162.BJ'"
        ).fetchone() == (False,)
