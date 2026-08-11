import hashlib
import importlib.util
import json
import logging
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).parents[1] / "repair.py"
    name = "library_doctor_legacy_034_upgrade_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _fixture():
    path = Path(__file__).parent / "fixtures" / "library_doctor_0_34_state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _encoded(document):
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_v034_backup_and_history_without_journal_restore_end_to_end(
    repair, tmp_path
):
    fixture = _fixture()
    assert fixture["pluginVersion"] == "0.34.0"
    library = tmp_path / "library"
    package = library.joinpath(*fixture["package"].split("/"))
    member = package.joinpath(*fixture["memberPath"].split("/"))
    member.parent.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "title: Song\n"
        "artist: Artist\n"
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    original = _encoded(fixture["originalDocument"])
    repaired = _encoded(fixture["repairedDocument"])
    member.write_bytes(repaired)
    untouched = package / "cover.bin"
    untouched.write_bytes(b"v0.34 unrelated member")

    data_dir = tmp_path / "config" / "library_doctor"
    backup_dir = data_dir / "repair_backups"
    backup_dir.mkdir(parents=True)
    backup_path = backup_dir / f"{fixture['backupId']}.zip"
    backup_metadata = {
        "schema": "library_doctor.repair_backup.v3",
        "backup_id": fixture["backupId"],
        "created_at": fixture["createdAt"],
        "package": fixture["package"],
        "package_kind": "directory",
        "plan_id": "0" * 64,
        "rule_code": "chart.duplicate-anchor",
        "rule_codes": ["chart.duplicate-anchor"],
        "summary": {
            "title": "Song",
            "artist": "Artist",
            "item_name": "anchor",
            "change_kind": "remove_duplicates",
            "change_count": 1,
            "removed_count": 1,
            "musical_positions": 1,
        },
        "members": [{
            "member_path": fixture["memberPath"],
            "backup_entry": "original/0.bin",
            "original_present": True,
            "repaired_present": True,
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "repaired_sha256": hashlib.sha256(repaired).hexdigest(),
        }],
    }
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "repair.json",
            json.dumps(backup_metadata, ensure_ascii=False, indent=2),
        )
        archive.writestr("original/0.bin", original)
    (data_dir / "repair_history.json").write_text(
        json.dumps({
            "schema": "library_doctor.repair_history.v1",
            "items": [fixture["historyReceipt"]],
        }),
        encoding="utf-8",
    )
    assert not (data_dir / "repair_transactions").exists()

    def validate(path, package_name, *, deep_audio=False):
        document = json.loads(
            Path(path).joinpath(*fixture["memberPath"].split("/")).read_bytes()
        )
        findings = (
            [{"code": "chart.duplicate-anchor", "severity": "warning"}]
            if len(document["anchors"]) > 1 else []
        )
        return {
            "schema": "library_doctor.package.v1",
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": bool(deep_audio)},
            "findings": findings,
        }

    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-v034-upgrade-test"),
    )

    history = service.history(limit=10)
    assert history["items"][0]["id"] == "0034-repair-receipt"
    assert history["items"][0]["undo_available"] is True
    assert not list((data_dir / "repair_transactions").glob("*.json"))

    preview = service.preview_restore(fixture["package"], fixture["backupId"])
    assert preview["available"] is True
    assert preview["returning_finding_codes"] == ["chart.duplicate-anchor"]

    result = service.restore(fixture["package"], fixture["backupId"])

    assert result["outcome"] == "restored"
    assert member.read_bytes() == original
    assert untouched.read_bytes() == b"v0.34 unrelated member"
    assert not backup_path.exists()
    assert not list((data_dir / "repair_transactions").glob("*.json"))
    final_history = service.history(limit=10)["items"]
    assert any(item.get("id") == "0034-repair-receipt" for item in final_history)
    assert any(
        item.get("action") == "restore" and item.get("outcome") == "restored"
        for item in final_history
    )
