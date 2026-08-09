import importlib.util
import logging
import sys
import threading
from pathlib import Path


def _load_batch_module():
    path = Path(__file__).parents[1] / "batch_repair.py"
    name = "library_doctor_batch_repair_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name, module


class _RepairError(ValueError):
    def __init__(self, code, message, *, file_state="unchanged"):
        super().__init__(message)
        self.code = code
        self.file_state = file_state


class _Scanner:
    def __init__(self):
        self.reserved = False
        self.finish_count = 0
        self.cached = []

    def begin_batch_operation(self):
        if self.reserved:
            return False, "busy"
        self.reserved = True
        return True, ""

    def finish_repair(self):
        self.reserved = False
        self.finish_count += 1

    @staticmethod
    def playback_active():
        return False

    @staticmethod
    def wait_for_playback(cancel_event):
        return not cancel_event.is_set()

    def record_repair_result(self, package, report, *, deep_audio=False):
        self.cached.append((package, report, deep_audio))


def test_last_batch_reader_preserves_pre_rename_receipt(tmp_path):
    name, module = _load_batch_module()
    try:
        result_path = (
            tmp_path / "config" / "library_doctor" / "batch_result.json"
        )
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            '{"schema":"library_health.batch_result.v1","outcomes":[]}',
            encoding="utf-8",
        )
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=_Scanner(),
            repair_service=object(),
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-legacy-batch-tests"),
            legacy_schemas={
                "batch_result": {"library_health.batch_result.v1"}
            },
        )

        assert manager.status()["last_result"]["schema"] == module.BATCH_RESULT_SCHEMA
    finally:
        sys.modules.pop(name, None)


class _BlockingRepairService:
    def __init__(self):
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.apply_calls = []

    @staticmethod
    def preview_all(package):
        return {
            "available": True,
            "plan_id": ("a" if package == "one.feedpak" else "b") * 64,
            "rule_codes": ["chart.duplicate-note"],
            "rule_count": 1,
            "removed_count": 1,
            "member_count": 1,
            "repair_summaries": [{
                "rule_code": "chart.duplicate-note",
                "title": "Remove exact duplicate notes",
                "item_name": "note",
                "removed_count": 1,
            }],
            "blockers": [],
        }

    def apply_all(self, package, plan_id, *, deep_audio=False):
        self.apply_calls.append((package, plan_id, deep_audio))
        if len(self.apply_calls) == 1:
            self.first_started.set()
            assert self.release_first.wait(5)
        return {
            "backup_id": f"backup-{package}",
            "removed_count": 1,
            "rule_codes": ["chart.duplicate-note"],
            "repair_summaries": [],
            "report": {"package": package, "title": package, "artist": ""},
        }


class _BlockingUndoService(_BlockingRepairService):
    def __init__(self):
        super().__init__()
        self.release_first.set()
        self.restore_started = threading.Event()
        self.release_restore = threading.Event()
        self.restore_calls = []

    @staticmethod
    def preview_restore(package, backup_id, *, deep_audio=False):
        return {
            "available": True,
            "plan_id": ("c" if package == "one.feedpak" else "d") * 64,
            "package": package,
            "backup_id": backup_id,
            "member_count": 1,
        }

    def restore(self, package, backup_id, *, deep_audio=False):
        self.restore_calls.append((package, backup_id, deep_audio))
        if len(self.restore_calls) == 1:
            self.restore_started.set()
            assert self.release_restore.wait(5)
        return {
            "package": package,
            "backup_id": backup_id,
            "title": package,
            "artist": "",
            "report": {"package": package, "title": package, "artist": ""},
        }

def test_batch_cancellation_finishes_current_feedpak_and_keeps_its_receipt(tmp_path):
    name, module = _load_batch_module()
    try:
        scanner = _Scanner()
        repairs = _BlockingRepairService()
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=scanner,
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-batch-tests"),
        )
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "folder", "label": "Test folder"},
            "deep_audio": False,
            "validator_version": "rules-test",
            "scope_package_count": 2,
            "candidates": [
                {
                    "package": "one.feedpak",
                    "title": "One",
                    "artist": "Artist",
                    "rule_codes": ["chart.duplicate-note"],
                },
                {
                    "package": "two.feedpak",
                    "title": "Two",
                    "artist": "Artist",
                    "rule_codes": ["chart.duplicate-note"],
                },
            ],
        }

        manager.start_preview(snapshot)
        manager.join(5)
        preview = manager.status()["preview"]
        manager.start_apply(preview["batch_plan_id"])
        assert repairs.first_started.wait(5)
        assert manager.cancel() is True
        repairs.release_first.set()
        manager.join(5)
        status = manager.status()
        result = status["result"]

        assert status["phase"] == "cancelled"
        assert result["completed_count"] == 1
        assert result["remaining_count"] == 1
        assert result["successful_count"] == 1
        assert result["backup_count"] == 1
        assert [call[0] for call in repairs.apply_calls] == ["one.feedpak"]
        assert scanner.cached[0][0] == "one.feedpak"
        assert scanner.finish_count == 2
        assert (tmp_path / "config" / "library_doctor" / "batch_result.json").is_file()
    finally:
        sys.modules.pop(name, None)


def test_batch_undo_cancellation_finishes_current_restore_and_keeps_later_package(tmp_path):
    name, module = _load_batch_module()
    try:
        scanner = _Scanner()
        repairs = _BlockingUndoService()
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=scanner,
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-batch-undo-tests"),
        )
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "folder", "label": "Test folder"},
            "deep_audio": False,
            "validator_version": "rules-test",
            "scope_package_count": 2,
            "candidates": [
                {
                    "package": "one.feedpak",
                    "title": "One",
                    "artist": "Artist",
                    "rule_codes": ["chart.duplicate-note"],
                },
                {
                    "package": "two.feedpak",
                    "title": "Two",
                    "artist": "Artist",
                    "rule_codes": ["chart.duplicate-note"],
                },
            ],
        }
        manager.start_preview(snapshot)
        manager.join(5)
        manager.start_apply(manager.status()["preview"]["batch_plan_id"])
        manager.join(5)
        assert manager.status()["phase"] == "completed"

        manager.start_undo_preview()
        manager.join(5)
        undo_preview = manager.status()["undo_preview"]
        assert undo_preview["eligible_count"] == 2
        manager.start_undo_apply(undo_preview["undo_plan_id"])
        assert repairs.restore_started.wait(5)
        assert manager.cancel() is True
        repairs.release_restore.set()
        manager.join(5)
        status = manager.status()

        assert status["phase"] == "undo_cancelled"
        assert status["undo_result"]["restored_count"] == 1
        assert status["undo_result"]["remaining_count"] == 1
        assert status["result"]["currently_repaired_count"] == 1
        assert status["result"]["restored_count"] == 1
        assert [call[0] for call in repairs.restore_calls] == ["one.feedpak"]
        assert scanner.finish_count == 4

        reloaded = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=_Scanner(),
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-batch-undo-reload-tests"),
        )
        persisted = reloaded.status()["last_result"]
        assert persisted["currently_repaired_count"] == 1
        assert persisted["restored_count"] == 1
        assert persisted["latest_undo_result"]["outcome"] == "cancelled"
    finally:
        sys.modules.pop(name, None)
