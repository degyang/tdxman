import json

from click.testing import CliRunner

from tdxman import bestip
from tdxman.cli import cli


def test_ranked_candidates_are_protocol_specific_and_atomic(tmp_path, monkeypatch):
    allip = tmp_path / "allip.json"
    best = tmp_path / "bestip.json"
    allip.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "endpoints": [
                    {"host": "standard", "port": 7709, "protocols": ["standard"]},
                    {"host": "mac", "port": 7709, "protocols": ["mac"]},
                    {"host": "ex-standard", "port": 7727, "protocols": ["ex"]},
                    {
                        "host": "ex-mac",
                        "port": 7720,
                        "protocols": ["ex"],
                        "dialect": "mac",
                    },
                ],
            }
        )
    )
    monkeypatch.setattr(bestip, "ALLIP_PATH", allip)
    monkeypatch.setattr(bestip, "BESTIP_PATH", best)
    bestip.save_rankings({"standard": [{"host": "ranked", "port": 7709, "latency_ms": 1.2}]})
    assert [item["host"] for item in bestip.preferred("standard")] == ["ranked", "standard"]
    assert [item["host"] for item in bestip.preferred("mac")] == ["mac"]
    assert [item["host"] for item in bestip.candidates("ex", dialect="standard")] == ["ex-standard"]
    assert [item["host"] for item in bestip.candidates("ex", dialect="mac")] == ["ex-mac"]
    assert not best.with_suffix(".tmp").exists()


def test_bestip_cli_keeps_other_groups_and_displays_rank(tmp_path, monkeypatch):
    allip = tmp_path / "allip.json"
    best = tmp_path / "bestip.json"
    allip.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "endpoints": [
                    {"host": "one", "port": 7709, "protocols": ["standard"]},
                    {"host": "two", "port": 7709, "protocols": ["standard"]},
                    {"host": "mac", "port": 7709, "protocols": ["mac"]},
                ],
            }
        )
    )
    monkeypatch.setattr(bestip, "ALLIP_PATH", allip)
    monkeypatch.setattr(bestip, "BESTIP_PATH", best)
    bestip.save_rankings({"mac": [{"host": "mac", "port": 7709, "latency_ms": 2.0}]})

    def probe(protocol, endpoint, timeout):
        assert protocol == "standard" and timeout == 1
        return {**endpoint, "latency_ms": 2 if endpoint["host"] == "one" else 1}

    monkeypatch.setattr("tdxman.cli.cmd_admin._probe_endpoint", probe)
    result = CliRunner().invoke(cli, ["bestip", "--protocol", "standard", "--timeout", "1"])
    assert result.exit_code == 0, result.output
    assert "standard：2/2 可用" in result.output
    assert [item["host"] for item in bestip.ranked("standard")] == ["two", "one"]
    assert bestip.ranked("mac")[0]["host"] == "mac"
