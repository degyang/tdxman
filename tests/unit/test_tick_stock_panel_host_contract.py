"""Read-only host contracts; never import its application or start its lifespan."""

from __future__ import annotations

import ast
import logging
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from tdxman.integrations.tick_stock_panel.plugin import provider


@pytest.fixture
def host():
    root = os.getenv("TICK_STOCK_PANEL_ROOT")
    if not root:
        pytest.skip("Set TICK_STOCK_PANEL_ROOT for read-only host contract tests")
    return Path(root) / "backend" / "app"


def test_real_registration_and_matrix(host, monkeypatch):
    monkeypatch.setenv("TDXMAN_TICK_STOCK_PANEL_MODE", "online")
    manifest_text = Path(provider.__file__).with_name("plugin.yaml").read_text()
    import re
    datasets = [v.strip() for v in re.search(r"datasets: \[(.*?)\]", manifest_text)[1].split(",")]
    manifest = {"name": "tdxman", "datasets": datasets, "entry": "tdxman",
                "check": "tdxman", "runtime": "none"}
    ns = {"_NAME_RE": re.compile(r"^[a-z0-9_]+$"), "_call_check": lambda _: provider.availability(),
          "_PLUGIN_STATUS": {}, "_PROVIDERS": {}, "_plugin_key_masked": lambda *args: "",
          "_load_entry": lambda _: provider.TdxmanProvider, "logger": logging.getLogger(__name__)}
    tree = ast.parse((host / "data_providers/custom/loader.py").read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_register_one_plugin", "provider_has_dataset"}]
    exec(compile(ast.Module(functions, type_ignores=[]), "host-loader", "exec"), ns)
    ns["_register_one_plugin"](manifest)
    plugin = ns["_PROVIDERS"]["tdxman"]
    assert set(plugin.config.datasets) == set(datasets)
    for dataset in datasets:
        assert ns["provider_has_dataset"]("tdxman", dataset)
    matrix_ns = {"custom_sources": SimpleNamespace(
        list_plugins=lambda: list(ns["_PLUGIN_STATUS"].values()), list_sources=lambda: [])}
    matrix = ast.parse((host / "data_providers/capabilities.py").read_text())
    matrix.body = [node for node in matrix.body
                   if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(matrix, "host-matrix", "exec"), matrix_ns)
    current = {f"{dataset}_data_provider": "tdxman" for dataset in datasets}
    result = matrix_ns["build_capability_matrix"](current)
    for row in result["capabilities"]:
        if row["id"] in datasets:
            assert row["usable"]
            assert "tdxman" in {candidate["name"] for candidate in row["candidates"]}
    plugin.close()


def test_reproduce_repository_500_and_verify_revert(host, tmp_path):
    source = (host / "tickflow/repository.py").read_text()
    tree = ast.parse(source)
    repository = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == "KlineRepository")
    names = {"_has_minute_parquet", "_minute_glob_for", "get_minute",
             "get_minute_batch", "get_minute_range"}
    repository.body = [node for node in repository.body
                       if isinstance(node, ast.FunctionDef) and node.name in names]
    repository.bases = []
    if not any(node.name == "_has_minute_parquet" for node in repository.body):
        # Keep the regression reproducible after the host's bad change is reverted.
        helper = ast.parse(
            "def _has_minute_parquet(self, asset_type):\n"
            "    return self._has_parquet('kline_minute')\n"
        ).body[0]
        repository.body.append(helper)
        guard = ast.parse(
            "if not self._has_minute_parquet(asset_type):\n"
            "    return pl.DataFrame()\n"
        ).body[0]
        for method in repository.body:
            if method.name.startswith("get_minute"):
                method.body.insert(0, guard)
    ns = {"pl": pl, "date": date, "logger": logging.getLogger(__name__),
          "guarded_collect": lambda lf, **kwargs: lf.collect()}
    exec(compile(ast.Module([repository], type_ignores=[]), "broken-repository", "exec"), ns)
    broken = ns["KlineRepository"]()
    with pytest.raises(AttributeError, match="_has_parquet"):
        broken.get_minute("605006.SH", date(2026, 9, 21))

    # Exactly mirror the reviewable revert patch, without touching the host file.
    repository.body = [node for node in repository.body if node.name != "_has_minute_parquet"]
    for method in repository.body:
        method.body = [node for node in method.body if not (
            isinstance(node, ast.If) and "_has_minute_parquet" in ast.unparse(node.test))]
    exec(compile(ast.Module([repository], type_ignores=[]), "reverted-repository", "exec"), ns)
    restored = ns["KlineRepository"]()
    restored._minute_glob = str(tmp_path / "stock" / "**" / "*.parquet")
    restored._etf_minute_glob = str(tmp_path / "etf" / "**" / "*.parquet")
    for asset in ("stock", "etf"):
        assert restored.get_minute("605006.SH", date(2026, 9, 21), asset).is_empty()
        assert restored.get_minute_batch(["605006.SH"], date(2026, 9, 21), asset).is_empty()
        assert restored.get_minute_range(["605006.SH"], date(2026, 9, 21),
                                         date(2026, 9, 21), asset).is_empty()
