import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "tests" / "fixtures" / "transaction_process.py"
CRASH_EXIT_CODE = 86


@pytest.fixture(scope="module")
def repair_module():
    name = "library_doctor_real_transaction_process_tests"
    spec = importlib.util.spec_from_file_location(name, ROOT / "repair.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def batch_module():
    name = "library_doctor_real_batch_process_tests"
    spec = importlib.util.spec_from_file_location(name, ROOT / "batch_repair.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _create_named_package(library, package_name):
    package = library / package_name
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "  - id: rhythm\n"
        "    file: arrangements/rhythm.json\n",
        encoding="utf-8",
    )
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    original = json.dumps({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    }).encode()
    for name in ("lead.json", "rhythm.json"):
        (arrangements / name).write_bytes(original)
    return package


def _create_directory_package(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    package = _create_named_package(library, "Song.feedpak")
    return library, package


def _validate(path, package_name, *, deep_audio=False):
    duplicate = False
    for name in ("lead.json", "rhythm.json"):
        document = json.loads((path / "arrangements" / name).read_bytes())
        duplicate = duplicate or len(document.get("anchors", [])) > 1
    findings = (
        [{"code": "chart.duplicate-anchor", "severity": "warning"}]
        if duplicate else []
    )
    return {
        "schema": "library_doctor.package.v1",
        "validator_version": "rules-test",
        "package": package_name,
        "title": "Transaction Song",
        "artist": "Synthetic",
        "status": "warning" if findings else "healthy",
        "counts": {"error": 0, "warning": len(findings), "info": 0},
        "features": {"deep_audio_checked": bool(deep_audio)},
        "findings": findings,
    }


def _service(repair_module, tmp_path, library):
    return repair_module.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=_validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-real-transaction-tests"),
    )


def _run_crash(
    tmp_path,
    library,
    operation,
    barrier,
    *,
    member_index=None,
    backup_id=None,
    crash_package=None,
):
    command = [
        sys.executable,
        str(HELPER),
        operation,
        str(library),
        str(tmp_path / "config"),
        barrier,
    ]
    if member_index is not None:
        command.extend(("--member-index", str(member_index)))
    if backup_id is not None:
        command.extend(("--backup-id", backup_id))
    if crash_package is not None:
        command.extend(("--crash-package", crash_package))
    completed = subprocess.run(command, check=False, timeout=30)
    assert completed.returncode == CRASH_EXIT_CODE


def _anchor_counts(package):
    return tuple(
        len(json.loads((package / "arrangements" / name).read_bytes())["anchors"])
        for name in ("lead.json", "rhythm.json")
    )


def _discoverable_package_paths(library):
    return {
        path.relative_to(library).as_posix()
        for path in library.rglob("*")
        if path.name.lower().endswith((".feedpak", ".sloppak"))
    }


@pytest.mark.parametrize(
    ("barrier", "member_index"),
    [
        ("journal_durable", None),
        ("backup_durable", None),
        ("member_replaced", 1),
        ("member_committed", 1),
        ("package_committed", None),
    ],
)
def test_real_process_death_during_apply_reconciles_to_one_allowed_state(
    repair_module, tmp_path, barrier, member_index,
):
    library, package = _create_directory_package(tmp_path)
    _run_crash(
        tmp_path,
        library,
        "apply",
        barrier,
        member_index=member_index,
    )

    assert _discoverable_package_paths(library) == {"Song.feedpak"}

    restarted = _service(repair_module, tmp_path, library)
    counts = _anchor_counts(package)
    assert counts in {(2, 2), (1, 1)}
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_transactions").glob("*.json")
    )
    backups = list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip")
    )
    if counts == (2, 2):
        assert backups == []
    else:
        assert len(backups) == 1
        receipt = restarted.history(limit=1)["items"][0]
        assert receipt["outcome"] == "success"
        assert receipt["undo_available"] is True


@pytest.mark.parametrize(
    ("barrier", "member_index"),
    [
        ("journal_durable", None),
        ("member_replaced", 1),
        ("member_committed", 1),
        ("package_committed", None),
    ],
)
def test_real_process_death_during_undo_never_leaves_a_mixed_package(
    repair_module, tmp_path, barrier, member_index,
):
    library, package = _create_directory_package(tmp_path)
    service = _service(repair_module, tmp_path, library)
    plan = service.preview_all("Song.feedpak")
    applied = service.apply_all("Song.feedpak", plan["plan_id"])
    assert _anchor_counts(package) == (1, 1)

    _run_crash(
        tmp_path,
        library,
        "restore",
        barrier,
        member_index=member_index,
        backup_id=applied["backup_id"],
    )

    assert _discoverable_package_paths(library) == {"Song.feedpak"}

    restarted = _service(repair_module, tmp_path, library)
    counts = _anchor_counts(package)
    assert counts in {(1, 1), (2, 2)}
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_transactions").glob("*.json")
    )
    backup = (
        tmp_path
        / "config"
        / "library_doctor"
        / "repair_backups"
        / f"{applied['backup_id']}.zip"
    )
    if counts == (1, 1):
        assert backup.is_file()
    else:
        assert not backup.exists()
        assert restarted.history(limit=1)["items"][0]["outcome"] == "restored"


class _BatchScanner:
    def __init__(self):
        self.reserved = False

    def begin_batch_operation(self):
        if self.reserved:
            return False, "busy"
        self.reserved = True
        return True, ""

    def finish_repair(self):
        self.reserved = False

    @staticmethod
    def playback_active():
        return False

    @staticmethod
    def wait_for_playback(cancel_event):
        return not cancel_event.is_set()

    @staticmethod
    def record_repair_result(_package, _report, *, deep_audio=False):
        del deep_audio


def test_real_process_death_inside_a_batch_package_preserves_boundaries(
    repair_module, batch_module, tmp_path,
):
    library = tmp_path / "library"
    library.mkdir()
    packages = {
        name: _create_named_package(library, name)
        for name in ("One.feedpak", "Two.feedpak", "Three.feedpak")
    }

    _run_crash(
        tmp_path,
        library,
        "batch",
        "member_replaced",
        member_index=1,
        crash_package="Two.feedpak",
    )

    assert _discoverable_package_paths(library) == {
        "One.feedpak",
        "Two.feedpak",
        "Three.feedpak",
    }

    restarted_repair = _service(repair_module, tmp_path, library)
    assert _anchor_counts(packages["One.feedpak"]) == (1, 1)
    assert _anchor_counts(packages["Two.feedpak"]) in {(1, 1), (2, 2)}
    assert _anchor_counts(packages["Three.feedpak"]) == (2, 2)
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_transactions").glob("*.json")
    )

    restarted_batch = batch_module.BatchRepairManager(
        config_dir=tmp_path / "config",
        scanner=_BatchScanner(),
        repair_service=restarted_repair,
        repair_error_type=repair_module.RepairPlanningError,
        log=logging.getLogger("library-doctor-real-batch-restart-tests"),
    ).status()["last_result"]
    assert restarted_batch["recovered_from_checkpoint"] is True
    assert restarted_batch["completed_count"] == 1
    assert restarted_batch["remaining_count"] == 2
    assert [item["package"] for item in restarted_batch["outcomes"]] == [
        "One.feedpak"
    ]
