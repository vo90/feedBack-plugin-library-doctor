import json
from pathlib import Path

from tools.verify_host_contract import verify_host_contract


ROOT = Path(__file__).parents[1]


def _compatible_host(tmp_path: Path) -> Path:
    contract = json.loads((ROOT / "host-contract.json").read_text(encoding="utf-8"))
    sources: dict[str, list[str]] = {}
    for capability in contract["capabilities"]:
        for evidence in capability.get("evidence", []):
            patterns = evidence["contains"]
            if isinstance(patterns, str):
                patterns = [patterns]
            sources.setdefault(evidence["path"], []).extend(patterns)
        alternatives = capability.get("evidenceAny", [])
        if alternatives:
            evidence = alternatives[0]
            patterns = evidence["contains"]
            if isinstance(patterns, str):
                patterns = [patterns]
            sources.setdefault(evidence["path"], []).extend(patterns)
    for relative, lines in sources.items():
        path = tmp_path.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    (tmp_path / "VERSION").write_text("0.3.0-alpha.1\n", encoding="utf-8")
    return tmp_path


def test_declared_minimum_capability_contract_passes_when_all_markers_exist(tmp_path):
    report = verify_host_contract(_compatible_host(tmp_path))

    assert report["compatible"] is True
    assert report["errors"] == []
    assert all(item["passed"] for item in report["capabilities"])
    assert report["minimum_compatible_commit"] == (
        "05be9ebdbe5f77310178772089655dab8f415246"
    )


def test_semantic_minimum_without_module_rails_is_rejected(tmp_path):
    host = _compatible_host(tmp_path)
    loader = host / "static" / "app.js"
    loader.write_text("classic plugin loader only", encoding="utf-8")

    report = verify_host_contract(host)

    assert report["compatible"] is False
    assert "capability_missing:plugin-native-modules-v1" in report["errors"]


def test_host_contract_rejects_evidence_that_escapes_checkout(tmp_path):
    host = _compatible_host(tmp_path / "host")
    contract = json.loads((ROOT / "host-contract.json").read_text(encoding="utf-8"))
    contract["capabilities"] = [{
        "id": "unsafe",
        "evidence": [{"path": "../secret.txt", "contains": "secret"}],
    }]
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    report = verify_host_contract(host, contract_path=contract_path)

    assert report["compatible"] is False
    assert "capability_missing:unsafe" in report["errors"]
