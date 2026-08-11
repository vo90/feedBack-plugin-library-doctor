import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_release_signoff_ledger_is_versioned_and_never_claims_missing_evidence():
    ledger = json.loads((ROOT / "release-signoff.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    assert ledger["schema"] == "library_doctor.release_signoff.v1"
    assert ledger["pluginVersion"] == manifest["version"] == "0.44.0"
    assert len(set(ledger["automatedGates"])) == len(ledger["automatedGates"])

    signoffs = {item["id"]: item for item in ledger["signoffs"]}
    assert set(signoffs) == {
        "nvda-windows",
        "windows-display-modes",
        "minimum-host-runtime",
        "novice-usability",
        "remote-ci",
        "clean-release-worktree",
    }
    for item in signoffs.values():
        assert item["required"] is True
        assert item["status"] in {"pending", "passed", "waived"}
        assert item["instructions"].startswith("docs/release-signoff-0.44.0.md#")
        if item["status"] == "pending":
            assert item["evidence"] is None
        else:
            assert isinstance(item["evidence"], str) and item["evidence"].strip()
