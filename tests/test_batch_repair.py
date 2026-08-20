import importlib.util
import json
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


def test_last_batch_reader_normalizes_stale_restored_file_state(tmp_path):
    name, module = _load_batch_module()
    try:
        result_path = (
            tmp_path / "config" / "library_doctor" / "batch_result.json"
        )
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            json.dumps({
                "schema": module.BATCH_RESULT_SCHEMA,
                "outcomes": [{
                    "package": "song.feedpak",
                    "outcome": "restored",
                    "file_state": "repaired",
                    "change_count": 8,
                }],
            }),
            encoding="utf-8",
        )

        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=_Scanner(),
            repair_service=object(),
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-stale-restored-state-tests"),
        )

        latest = manager.status()["last_result"]
        persisted = json.loads(result_path.read_text(encoding="utf-8"))
        assert latest["outcomes"][0]["file_state"] == "restored"
        assert latest["restored_count"] == 1
        assert latest["current_change_count"] == 0
        assert persisted["outcomes"][0]["file_state"] == "restored"
    finally:
        sys.modules.pop(name, None)


class _BlockingRepairService:
    def __init__(self):
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.apply_calls = []
        self.preview_calls = []

    def preview_selected(self, package, rule_codes):
        self.preview_calls.append((package, tuple(rule_codes)))
        raise AssertionError("Batch review must not reparse Feedpak song data.")

    def apply_selected(
        self,
        package,
        *,
        deep_audio=False,
        rule_codes=None,
    ):
        self.apply_calls.append(
            (package, deep_audio, tuple(rule_codes or ()))
        )
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


class _RecoveryAwareRepairService(_BlockingRepairService):
    @staticmethod
    def recovery_state(package):
        if package == "two.feedpak":
            return {
                "required": True,
                "message": "Resolve this interrupted repair before changing it.",
            }
        return {"required": False}


class _PreviewFailureAfterSafeRepair(_BlockingRepairService):
    def __init__(self):
        super().__init__()
        self.release_first.set()

    @staticmethod
    def preview_tool_status(_package):
        return {
            "preview_declared": False,
            "current_preview_available": False,
            "full_mix_available": True,
        }

    @staticmethod
    def apply_automatic_preview(_package, _rule_code):
        raise _RepairError(
            "audio_tool_failed",
            "The audio converter could not create this preview.",
        )


class _PreviewCleanupFailureRepairService:
    @staticmethod
    def preview_tool_status(_package):
        return {
            "preview_declared": False,
            "current_preview_available": False,
            "full_mix_available": True,
        }

    @staticmethod
    def apply_automatic_preview(package, rule_code):
        assert rule_code == "media.preview-missing"
        return {
            "backup_id": "preview-cleanup-backup",
            "report": {"package": package, "title": package, "artist": ""},
            "media": {"estimated_package_savings_bytes": 0},
            "file_handling": {
                "backup_cleanup_required": True,
                "backup_cleanup_error": "The recovery copy is temporarily locked.",
                "backup_size_bytes": 2048,
            },
        }


class _FinalizeService(_BlockingRepairService):
    def __init__(self):
        super().__init__()
        self.release_first.set()
        self.finalize_preview_calls = []
        self.finalize_calls = []

    def preview_finalize_backup(self, package, backup_id):
        self.finalize_preview_calls.append((package, backup_id))
        return {
            "plan_id": ("e" if package == "one.feedpak" else "f") * 64,
            "package": package,
            "backup_id": backup_id,
            "package_state": "repaired",
            "member_count": 1,
            "recovery_bytes": 1024,
            "rule_codes": ["chart.duplicate-note"],
        }

    def finalize_backup(self, package, backup_id):
        self.finalize_calls.append((package, backup_id))
        return {
            "package": package,
            "backup_id": backup_id,
            "title": package,
            "artist": "",
            "package_state": "repaired",
            "completed_at": 1234.0,
            "file_handling": {"recovery_bytes_freed": 1024},
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
        assert status["live_outcomes"] == {
            "completed": 1,
            "repaired": 1,
            "partial": 0,
            "skipped": 0,
            "failed": 0,
            "previews_repaired": 0,
        }
        assert result["backup_count"] == 1
        assert result["performance"]["elapsed_seconds"] >= 0
        assert result["performance"]["song_data_repair_seconds"] >= 0
        assert result["performance"]["checkpoint_seconds"] >= 0
        assert result["performance"]["verified_scan_report_reused_packages"] == 0
        assert repairs.preview_calls == []
        assert [call[0] for call in repairs.apply_calls] == ["one.feedpak"]
        assert repairs.apply_calls[0][2] == ("chart.duplicate-note",)
        assert scanner.cached[0][0] == "one.feedpak"
        assert scanner.finish_count == 2
        assert (tmp_path / "config" / "library_doctor" / "batch_result.json").is_file()
    finally:
        sys.modules.pop(name, None)


def test_batch_preview_excludes_packages_with_required_recovery(tmp_path):
    name, module = _load_batch_module()
    try:
        repairs = _RecoveryAwareRepairService()
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=_Scanner(),
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-batch-recovery-lock-tests"),
        )
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "folder", "label": "Test folder"},
            "deep_audio": False,
            "validator_version": "rules-test",
            "scope_package_count": 2,
            "candidates": [
                {
                    "package": package,
                    "title": package,
                    "artist": "Artist",
                    "rule_codes": ["chart.duplicate-note"],
                }
                for package in ("one.feedpak", "two.feedpak")
            ],
        }

        manager.start_preview(snapshot)
        manager.join(5)
        preview = manager.status()["preview"]

        assert preview["eligible_count"] == 1
        assert preview["blocked_count"] == 1
        assert preview["packages"][0]["package"] == "one.feedpak"
        assert preview["blocked"][0]["package"] == "two.feedpak"
        assert preview["blocked"][0]["code"] == "recovery_required"
    finally:
        sys.modules.pop(name, None)


def test_batch_keeps_safe_song_data_when_optional_preview_generation_fails(
    tmp_path,
):
    name, module = _load_batch_module()
    try:
        scanner = _Scanner()
        repairs = _PreviewFailureAfterSafeRepair()
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=scanner,
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-partial-preview-tests"),
        )
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "file", "label": "One"},
            "deep_audio": False,
            "include_preview_repairs": True,
            "validator_version": "rules-test",
            "scope_package_count": 1,
            "candidates": [{
                "package": "one.feedpak",
                "title": "One",
                "artist": "Artist",
                "rule_codes": ["chart.duplicate-note"],
                "preview_rule_code": "media.preview-missing",
            }],
        }

        manager.start_preview(snapshot)
        manager.join(5)
        preview = manager.status()["preview"]
        assert preview["mixed_repair_package_count"] == 1
        manager.start_apply(preview["batch_plan_id"])
        manager.join(5)
        status = manager.status()
        result = status["result"]
        outcome = result["outcomes"][0]

        assert result["successful_count"] == 0
        assert result["partial_count"] == 1
        assert result["failed_count"] == 1
        assert result["preview_failed_count"] == 1
        assert result["backup_count"] == 1
        assert result["undoable_count"] == 1
        assert outcome["outcome"] == "partial"
        assert outcome["backup_id"] == "backup-one.feedpak"
        assert outcome["preview_repaired"] is False
        assert outcome["file_state"] == "partially_repaired"
        assert "automatic preview repair" not in result["recovery_summary"]
        assert scanner.cached[0][0] == "one.feedpak"
    finally:
        sys.modules.pop(name, None)


def test_batch_surfaces_and_can_clear_temporary_preview_recovery_copy(tmp_path):
    name, module = _load_batch_module()
    try:
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=_Scanner(),
            repair_service=_PreviewCleanupFailureRepairService(),
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-preview-cleanup-tests"),
        )
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "file", "label": "One"},
            "deep_audio": False,
            "include_preview_repairs": True,
            "validator_version": "rules-test",
            "scope_package_count": 1,
            "candidates": [{
                "package": "one.feedpak",
                "title": "One",
                "artist": "Artist",
                "rule_codes": [],
                "preview_rule_code": "media.preview-missing",
            }],
        }

        manager.start_preview(snapshot)
        manager.join(5)
        review = manager.status()["preview"]
        manager.start_apply(review["batch_plan_id"])
        manager.join(5)
        status = manager.status()
        result = status["result"]
        outcome = result["outcomes"][0]

        assert result["successful_count"] == 1
        assert status["live_outcomes"] == {
            "completed": 1,
            "repaired": 1,
            "partial": 0,
            "skipped": 0,
            "failed": 0,
            "previews_repaired": 1,
        }
        assert result["preview_successful_count"] == 1
        assert result["preview_cleanup_required_count"] == 1
        assert result["undoable_count"] == 0
        assert outcome["outcome"] == "success"
        assert outcome["preview_repaired"] is True
        assert outcome["preview_finalized"] is False
        assert outcome["preview_cleanup_backup_id"] == "preview-cleanup-backup"
        assert manager.mark_finalized(
            "one.feedpak", "preview-cleanup-backup"
        ) is True

        updated = manager.status()["result"]
        updated_outcome = updated["outcomes"][0]
        assert updated["preview_cleanup_required_count"] == 0
        assert updated_outcome["outcome"] == "finalized"
        assert updated_outcome["preview_finalized"] is True
        assert updated_outcome["preview_cleanup_backup_id"] is None
    finally:
        sys.modules.pop(name, None)


def test_interrupted_batch_checkpoint_becomes_a_recoverable_result(tmp_path):
    name, module = _load_batch_module()
    try:
        config = tmp_path / "config"
        manager = module.BatchRepairManager(
            config_dir=config,
            scanner=_Scanner(),
            repair_service=object(),
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-checkpoint-write-tests"),
        )
        partial = {
            "schema": module.BATCH_RESULT_SCHEMA,
            "id": "checkpoint-result",
            "batch_plan_id": "a" * 64,
            "outcome": "interrupted",
            "started_at": 10.0,
            "completed_at": 20.0,
            "planned_count": 2,
            "completed_count": 1,
            "remaining_count": 1,
            "successful_count": 1,
            "partial_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "change_count": 1,
            "removed_count": 1,
            "outcomes": [{
                "package": "one.feedpak",
                "outcome": "success",
                "backup_id": "backup-one",
                "change_count": 1,
                "removed_count": 1,
            }],
        }
        manager._write_checkpoint(partial)

        recovered = module.BatchRepairManager(
            config_dir=config,
            scanner=_Scanner(),
            repair_service=object(),
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-checkpoint-read-tests"),
        ).status()["last_result"]

        assert recovered["id"] == "checkpoint-result"
        assert recovered["outcome"] == "interrupted"
        assert recovered["recovered_from_checkpoint"] is True
        assert recovered["undoable_count"] == 1
        assert not (config / "library_doctor" / "batch_checkpoint.json").exists()
        assert (config / "library_doctor" / "batch_result.json").is_file()
    finally:
        sys.modules.pop(name, None)


def test_running_batch_checkpoints_only_after_complete_package_transactions(
    tmp_path,
):
    name, module = _load_batch_module()
    try:
        scanner = _Scanner()
        repairs = _BlockingRepairService()
        repairs.release_first.set()
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=scanner,
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-periodic-checkpoint-tests"),
        )
        checkpoints = []
        manager._write_checkpoint = lambda result: checkpoints.append(result)
        module.CHECKPOINT_PACKAGE_INTERVAL = 1
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "file", "label": "One"},
            "deep_audio": False,
            "validator_version": "rules-test",
            "scope_package_count": 1,
            "candidates": [{
                "package": "one.feedpak",
                "title": "One",
                "artist": "Artist",
                "rule_codes": ["chart.duplicate-note"],
            }],
        }

        manager.start_preview(snapshot)
        manager.join(5)
        manager.start_apply(manager.status()["preview"]["batch_plan_id"])
        manager.join(5)

        assert len(checkpoints) == 1
        assert checkpoints[0]["outcome"] == "interrupted"
        assert checkpoints[0]["completed_count"] == 1
        assert checkpoints[0]["outcomes"][0]["outcome"] == "success"
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
        restored_outcome = next(
            item for item in status["result"]["outcomes"]
            if item["outcome"] == "restored"
        )
        assert restored_outcome["file_state"] == "restored"
        assert restored_outcome["cache_updated"] is True
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
        persisted_restored = next(
            item for item in persisted["outcomes"]
            if item["outcome"] == "restored"
        )
        assert persisted_restored["file_state"] == "restored"
        assert persisted_restored["cache_updated"] is True
        assert persisted["latest_undo_result"]["outcome"] == "cancelled"
    finally:
        sys.modules.pop(name, None)


def test_batch_finalizes_all_verified_recovery_copies_and_updates_receipt(tmp_path):
    name, module = _load_batch_module()
    try:
        scanner = _Scanner()
        repairs = _FinalizeService()
        manager = module.BatchRepairManager(
            config_dir=tmp_path / "config",
            scanner=scanner,
            repair_service=repairs,
            repair_error_type=_RepairError,
            log=logging.getLogger("library-doctor-batch-finalize-tests"),
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

        manager.start_finalize_preview()
        manager.join(5)
        review = manager.status()["finalize_preview"]
        assert review["schema"] == module.BATCH_FINALIZE_PREVIEW_SCHEMA
        assert review["eligible_count"] == 2
        assert review["blocked_count"] == 0
        assert review["recovery_bytes_to_free"] == 2048
        assert len(repairs.finalize_calls) == 0

        manager.start_finalize_apply(review["finalize_plan_id"])
        manager.join(5)
        status = manager.status()
        result = status["result"]
        finalize_result = status["finalize_result"]

        assert status["phase"] == "finalize_completed"
        assert finalize_result["schema"] == module.BATCH_FINALIZE_RESULT_SCHEMA
        assert finalize_result["finalized_count"] == 2
        assert finalize_result["recovery_bytes_freed"] == 2048
        assert result["currently_repaired_count"] == 2
        assert result["finalized_count"] == 2
        assert result["undoable_count"] == 0
        assert all(item["outcome"] == "finalized" for item in result["outcomes"])
        assert all(item["undo_available"] is False for item in result["outcomes"])
        assert len(repairs.finalize_preview_calls) == 2
        assert len(repairs.finalize_calls) == 2
        assert scanner.finish_count == 4

        persisted = json.loads(
            (tmp_path / "config" / "library_doctor" / "batch_result.json")
            .read_text(encoding="utf-8")
        )
        assert persisted["undoable_count"] == 0
        assert persisted["latest_finalize_result"]["finalized_count"] == 2
    finally:
        sys.modules.pop(name, None)
