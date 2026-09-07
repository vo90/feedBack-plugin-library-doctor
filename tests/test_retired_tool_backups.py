"""Old source-tool receipts remain usable by the ordinary recovery engine."""

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
    name = "library_doctor_retired_tool_backup_tests"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "repair.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("operation", ["restore", "finalize", "changed_restore"])
@pytest.mark.parametrize("archive", [False, True])
def test_retired_source_backup_uses_existing_history_and_recovery(repair, tmp_path, operation, archive):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    original = b'{"notes":[{"t":10,"s":0,"f":5,"sus":1,"bn":2}]}\n'
    repaired = b'{"notes":[{"t":10,"s":0,"f":5,"sus":1,"bn":2,"bnv":[{"t":0,"v":0},{"t":0.5,"v":2}]}]}\n'
    current = repaired + b" " if operation == "changed_restore" else repaired
    members = {"manifest.yaml": b"arrangements:\n  - id: lead\n    file: lead.json\n",
               "lead.json": current, "audio.ogg": b"unchanged MinusMix audio"}
    if archive:
        with zipfile.ZipFile(package, "w") as target:
            for name, data in members.items():
                target.writestr(name, data)
    else:
        package.mkdir()
        for name, data in members.items():
            (package / name).write_bytes(data)

    def package_members(path):
        if path.is_dir():
            return {name: (path / name).read_bytes() for name in members}
        with zipfile.ZipFile(path) as source:
            return {name: source.read(name) for name in source.namelist()}

    config = tmp_path / "config"
    state = config / "library_doctor"
    backups = state / "repair_backups"
    backups.mkdir(parents=True)
    backup_id = "20260907-120000-abcdef123456"
    # Literal v3 wire format written by the removed tool, independent of its code.
    metadata = {
        "schema": "library_doctor.repair_backup.v3", "backup_id": backup_id,
        "created_at": 1, "package": "Song.feedpak",
        "package_kind": "archive" if archive else "directory", "plan_id": "a" * 64,
        "rule_code": "source.bend-recovery", "rule_codes": ["source.bend-recovery"],
        "summary": {"title": "Song", "change_kind": "normalize_values",
                    "item_name": "bend field", "change_count": 1, "removed_count": 0},
        "members": [{"member_path": "lead.json", "backup_entry": "original/0.bin",
                     "original_present": True, "repaired_present": True,
                     "original_sha256": hashlib.sha256(original).hexdigest(),
                     "repaired_sha256": hashlib.sha256(repaired).hexdigest()}],
    }
    backup = backups / f"{backup_id}.zip"
    with zipfile.ZipFile(backup, "w") as target:
        target.writestr("repair.json", json.dumps(metadata))
        target.writestr("original/0.bin", original)
    history = {"schema": "library_doctor.repair_history.v1", "items": [{
        "id": "old-source-receipt", "action": "repair", "outcome": "success",
        "completed_at": 1, "package": "Song.feedpak", "backup_id": backup_id,
        "rule_code": "source.bend-recovery", **metadata["summary"],
    }]}
    (state / "repair_history.json").write_text(json.dumps(history), encoding="utf-8")
    checkpoint = state / "source_recovery_batch.json"
    checkpoint.write_bytes(b'{"old":"source batch report"}')

    def validate(path, _name, **_kwargs):
        restored = package_members(path)["lead.json"] == original
        return {"validator_version": "test", "findings": [
            {"code": "chart.original-warning", "severity": "warning"}] if restored else [],
            "counts": {"error": 0, "warning": int(restored), "info": 0}}

    service = repair.RepairService(config_dir=config, get_dlc_dir=lambda: library,
        validate_feedpak=validate, validator_version="test", log=logging.getLogger("test"))
    assert repair.repair_for_rule("source.bend-recovery") is None
    assert service.history()["items"][0]["undo_available"]
    assert checkpoint.read_bytes() == b'{"old":"source batch report"}'
    if operation == "changed_restore":
        with pytest.raises(repair.RepairPlanningError) as error:
            service.preview_restore("Song.feedpak", backup_id)
        assert error.value.code == "package_changed"
        assert backup.is_file()
    elif operation == "finalize":
        service.preview_finalize_backup("Song.feedpak", backup_id)
        assert service.finalize_backup("Song.feedpak", backup_id)["outcome"] == "finalized"
        assert not backup.exists()
    else:
        preview = service.preview_restore("Song.feedpak", backup_id)
        assert preview["available"] and preview["change_kind"] == "normalize_values"
        assert preview["returning_finding_codes"] == ["chart.original-warning"]
        assert service.restore("Song.feedpak", backup_id)["restored"]
        members["lead.json"] = original
        assert not backup.exists()
    assert package_members(package) == members
    assert checkpoint.read_bytes() == b'{"old":"source batch report"}'
