import concurrent.futures
import copy
import importlib.util
import json
import logging
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def _load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def batch_module():
    name = "library_doctor_adaptive_batch_tests"
    module = _load_module("batch_repair.py", name)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def repair_module():
    name = "library_doctor_adaptive_repair_tests"
    module = _load_module("repair.py", name)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def scan_worker_module():
    name = "library_doctor_adaptive_scan_worker_tests"
    stable_name = "library_doctor_scan_worker"
    prior_stable = sys.modules.get(stable_name)
    module = _load_module("library_doctor_scan_worker.py", name)
    yield module
    sys.modules.pop(name, None)
    if prior_stable is None:
        sys.modules.pop(stable_name, None)
    else:
        sys.modules[stable_name] = prior_stable


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

    @staticmethod
    def package_matches_signature(package, expected):
        return expected == f"signature-{package}"

    def record_repair_result(self, package, report, *, deep_audio=False):
        self.cached.append((package, report, deep_audio))


class _DeepScanner(_Scanner):
    def __init__(self):
        super().__init__()
        self.signature_checks = []
        self.report_reads = []

    def package_matches_signature(self, package, expected):
        self.signature_checks.append((package, expected))
        return expected == f"signature-{package}"

    def deep_audio_report_for_signature(self, package, expected):
        self.report_reads.append((package, expected))
        return {
            "package": package,
            "validator_version": "rules-test",
            "features": {
                "deep_audio_checked": True,
                "deep_audio_files": 1,
                "deep_audio_skipped": 0,
                "deep_audio_unsupported": 0,
            },
            "findings": [{"code": "chart.duplicate-note"}],
            "counts": {"error": 0, "warning": 1, "info": 0},
            "status": "warning",
        }


class _ValidationPool:
    def __init__(self):
        self.cancelled = False
        self.paused = []
        self.shutdown_calls = []

    @staticmethod
    def submit(_path, package, deep_audio):
        future = concurrent.futures.Future()
        future.set_result({
            "outcome": "complete",
            "report": {
                "package": package,
                "features": {"deep_audio_checked": bool(deep_audio)},
            },
        })
        return future

    def set_paused(self, paused):
        self.paused.append(bool(paused))

    def cancel(self):
        self.cancelled = True

    def shutdown(self, *, force, timeout_seconds):
        self.shutdown_calls.append((bool(force), float(timeout_seconds)))


class _PlaybackScanner(_Scanner):
    def __init__(self):
        super().__init__()
        self.playing = threading.Event()
        self.resumed = threading.Event()

    def playback_active(self):
        return self.playing.is_set()

    def wait_for_playback(self, cancel_event):
        while self.playing.is_set() and not cancel_event.is_set():
            self.resumed.wait(0.05)
        return not cancel_event.is_set()


class _CountingValidationPool(_ValidationPool):
    def __init__(self):
        super().__init__()
        self.submit_count = 0
        self.pause_observed = threading.Event()

    def submit(self, path, package, deep_audio):
        self.submit_count += 1
        return super().submit(path, package, deep_audio)

    def set_paused(self, paused):
        super().set_paused(paused)
        if paused:
            self.pause_observed.set()


class _BlockingValidationPool(_CountingValidationPool):
    def __init__(self):
        super().__init__()
        self.future = concurrent.futures.Future()
        self.submitted = threading.Event()

    def submit(self, _path, _package, _deep_audio):
        self.submit_count += 1
        self.submitted.set()
        return self.future


class _ShutdownReleasingPool(_BlockingValidationPool):
    def __init__(self):
        super().__init__()
        self.forced_shutdown_complete = threading.Event()

    def shutdown(self, *, force, timeout_seconds):
        super().shutdown(force=force, timeout_seconds=timeout_seconds)
        if force:
            self.forced_shutdown_complete.set()
        if not self.future.done():
            self.future.set_exception(RuntimeError("worker terminated"))


class _InitiallyFailingShutdownPool(_ShutdownReleasingPool):
    def __init__(self):
        super().__init__()
        self.force_attempts = 0

    def shutdown(self, *, force, timeout_seconds):
        if force:
            self.force_attempts += 1
            if self.force_attempts == 1:
                self.shutdown_calls.append((True, float(timeout_seconds)))
                raise RuntimeError("first termination attempt failed")
        super().shutdown(force=force, timeout_seconds=timeout_seconds)


class _PersistentShutdownPool(_BlockingValidationPool):
    def __init__(self, *, initial_report=None):
        super().__init__()
        self.initial_report = initial_report
        self.candidate_submitted = threading.Event()
        self.allow_stop = False
        self.stopped = threading.Event()

    def submit(self, _path, _package, _deep_audio):
        self.submit_count += 1
        self.submitted.set()
        if self.submit_count == 1 and isinstance(self.initial_report, dict):
            future = concurrent.futures.Future()
            future.set_result({
                "outcome": "complete",
                "report": copy.deepcopy(self.initial_report),
            })
            return future
        self.candidate_submitted.set()
        return self.future

    def shutdown(self, *, force, timeout_seconds):
        self.shutdown_calls.append((bool(force), float(timeout_seconds)))
        if not self.allow_stop:
            raise RuntimeError("worker state unavailable")
        self.stopped.set()
        if not self.future.done():
            self.future.set_exception(RuntimeError("worker terminated"))


class _SplitRepairService:
    validator_version = "rules-test"

    def __init__(self, *, hold_preparation=False, synchronize_first_pair=False):
        self.hold_preparation = hold_preparation
        self.synchronize_first_pair = synchronize_first_pair
        self.release = threading.Event()
        self.first_pair_started = threading.Event()
        self.events = []
        self.discarded = []
        self.commits = []
        self.source_options = []
        self._lock = threading.Lock()
        self._prepare_active = 0
        self._prepare_started = 0
        self._commit_active = 0
        self.max_prepare_active = 0
        self.max_commit_active = 0
        self.prepared_live = 0
        self.max_prepared_live = 0

    def _prepare(self, package, kind):
        with self._lock:
            self._prepare_active += 1
            self._prepare_started += 1
            sequence = self._prepare_started
            self.max_prepare_active = max(
                self.max_prepare_active, self._prepare_active
            )
            self.events.append(("prepare", kind, package))
            if self._prepare_started >= 2:
                self.first_pair_started.set()
        if self.hold_preparation:
            assert self.release.wait(5)
        elif self.synchronize_first_pair and sequence <= 2:
            assert self.first_pair_started.wait(5)
        time.sleep(0.01)
        with self._lock:
            self._prepare_active -= 1
            self.prepared_live += 1
            self.max_prepared_live = max(
                self.max_prepared_live, self.prepared_live
            )
        return {"package": package, "kind": kind}

    def prepare_selected(
        self,
        package,
        *,
        rule_codes,
        deep_audio=False,
        validation_runner=None,
        verified_before_report=None,
        source_guard=None,
    ):
        del rule_codes, validation_runner
        self.source_options.append({
            "package": package,
            "deep_audio": bool(deep_audio),
            "verified_before_report": verified_before_report,
            "source_guard": source_guard,
        })
        prepared = self._prepare(package, "safe")
        prepared["verified_before_report"] = verified_before_report
        return prepared

    def prepare_automatic_preview(
        self,
        package,
        rule_code,
        *,
        validation_runner=None,
        verified_before_report=None,
        source_guard=None,
    ):
        del rule_code, validation_runner
        self.source_options.append({
            "package": package,
            "deep_audio": True,
            "verified_before_report": verified_before_report,
            "source_guard": source_guard,
        })
        prepared = self._prepare(package, "preview")
        prepared["verified_before_report"] = verified_before_report
        return prepared

    def commit_prepared(self, prepared):
        package = prepared["package"]
        kind = prepared["kind"]
        with self._lock:
            self._commit_active += 1
            self.max_commit_active = max(
                self.max_commit_active, self._commit_active
            )
            self.events.append(("commit", kind, package))
        time.sleep(0.005)
        with self._lock:
            self._commit_active -= 1
            self.prepared_live -= 1
            self.commits.append((kind, package))
        common = {
            "report": {"package": package, "title": package, "artist": ""},
            "verified_scan_report_reused": False,
        }
        if kind == "safe":
            return {
                **common,
                "backup_id": f"backup-{package}",
                "change_count": 1,
                "removed_count": 1,
                "change_kind": "remove_duplicates",
                "rule_codes": ["chart.duplicate-note"],
                "repair_summaries": [],
                "deep_audio_reused": bool(
                    prepared.get("verified_before_report")
                ),
                "verified_scan_report_reused": bool(
                    prepared.get("verified_before_report")
                ),
            }
        return {
            **common,
            "backup_id": f"temporary-{package}",
            "media": {"estimated_package_savings_bytes": 128},
            "file_handling": {
                "backup_cleanup_required": False,
                "backup_size_bytes": 0,
            },
        }

    def discard_prepared(self, prepared):
        with self._lock:
            self.prepared_live -= 1
            self.discarded.append((prepared["kind"], prepared["package"]))
        return True

    @staticmethod
    def apply_selected(*_args, **_kwargs):
        raise AssertionError("The serial repair path must not be used.")

    @staticmethod
    def apply_automatic_preview(*_args, **_kwargs):
        raise AssertionError("The serial preview path must not be used.")


class _ValidationCallingService(_SplitRepairService):
    def __init__(self):
        super().__init__()
        self.validation_ready = threading.Event()
        self.allow_validation = threading.Event()

    def prepare_selected(
        self,
        package,
        *,
        rule_codes,
        deep_audio=False,
        validation_runner=None,
        verified_before_report=None,
        source_guard=None,
    ):
        del rule_codes, verified_before_report, source_guard
        self.validation_ready.set()
        assert self.allow_validation.wait(5)
        validation_runner(Path(package), package, deep_audio=deep_audio)
        return self._prepare(package, "safe")


class _WorkspaceValidationService(_ValidationCallingService):
    def __init__(self):
        super().__init__()
        self.workspace_active = 0
        self.cleanup_guard = None
        self.cleanup_saw_force_stop = None

    def prepare_selected(self, package, **options):
        self.workspace_active += 1
        try:
            return super().prepare_selected(package, **options)
        finally:
            if self.cleanup_guard is not None:
                self.cleanup_saw_force_stop = self.cleanup_guard.is_set()
            self.workspace_active -= 1


class _CancelAfterSafeService(_SplitRepairService):
    def __init__(self):
        super().__init__()
        self.cancel_callback = None

    def commit_prepared(self, prepared):
        result = super().commit_prepared(prepared)
        if prepared["kind"] == "safe":
            self.cancel_callback()
        return result


class _PlaybackAfterSafeService(_SplitRepairService):
    def __init__(self):
        super().__init__()
        self.playback_callback = None
        self.safe_committed = threading.Event()

    def commit_prepared(self, prepared):
        result = super().commit_prepared(prepared)
        if prepared["kind"] == "safe":
            self.playback_callback()
            self.safe_committed.set()
        return result


class _RecoveryService(_SplitRepairService):
    def __init__(self):
        super().__init__()
        self.restore_calls = []
        self.finalize_calls = []

    def restore(self, package, backup_id, *, deep_audio=False):
        self.restore_calls.append((package, backup_id, bool(deep_audio)))
        return {
            "package": package,
            "backup_id": backup_id,
            "title": package,
            "artist": "Artist",
            "change_count": 1,
            "report": {"package": package, "title": package, "artist": "Artist"},
            "file_handling": {"backup_removed": True},
        }

    def finalize_backup(self, package, backup_id):
        self.finalize_calls.append((package, backup_id))
        return {
            "package": package,
            "backup_id": backup_id,
            "title": package,
            "artist": "Artist",
            "package_state": "repaired",
            "completed_at": time.time(),
            "file_handling": {"recovery_bytes_freed": 128},
        }


def _plan(package, *, safe=True, preview=False):
    safe_codes = ["chart.duplicate-note"] if safe else []
    preview_code = "media.preview-missing" if preview else None
    return {
        "package": package,
        "scan_signature": f"signature-{package}",
        "title": package,
        "artist": "Artist",
        "safe_rule_codes": safe_codes,
        "preview_rule_code": preview_code,
        "rule_codes": safe_codes + ([preview_code] if preview_code else []),
    }


def _snapshot(*, include_preview_repairs=False, deep_audio=False):
    return {
        "target": {"kind": "folder", "label": "Disposable test folder"},
        "deep_audio": bool(deep_audio),
        "include_preview_repairs": include_preview_repairs,
    }


def _prime_apply(manager, plans, snapshot):
    batch_plan_id = "a" * 64
    manager._plans = copy.deepcopy(plans)
    manager._snapshot = copy.deepcopy(snapshot)
    manager._state.update({
        "phase": "ready",
        "running": False,
        "preview": {"batch_plan_id": batch_plan_id},
    })
    return batch_plan_id


def _apply_signature_fault(scanner, item, fault):
    if fault == "missing":
        item.pop("scan_signature", None)
    elif fault == "non_string":
        item["scan_signature"] = 42
    elif fault == "missing_checker":
        scanner.package_matches_signature = None
    else:
        scanner.package_matches_signature = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("signature cache unavailable")
        )


def _manager(
    batch_module,
    tmp_path,
    scanner,
    service,
    *,
    policy=None,
    process_pool_factory=None,
):
    if policy is None:
        def policy(_pending, **_options):
            return {
                "selected_workers": 2,
                "active_worker_limit": 2,
                "recommended_workers": 2,
                "manual_max_workers": 4,
            }
    return batch_module.BatchRepairManager(
        config_dir=tmp_path / "config",
        scanner=scanner,
        repair_service=service,
        repair_error_type=_RepairError,
        log=logging.getLogger("library-doctor-adaptive-pipeline-tests"),
        prepare_worker_policy=policy,
        prepare_process_pool_factory=(
            process_pool_factory or (lambda _workers, _version: _ValidationPool())
        ),
    )


def _seed_retained_result_checkpoint(batch_module, tmp_path):
    service = _RecoveryService()
    manager = _manager(batch_module, tmp_path, _Scanner(), service)
    real_writer = manager._write_last_result
    manager._write_last_result = lambda _result: False
    batch_plan_id = _prime_apply(
        manager, [_plan("one.feedpak")], _snapshot()
    )
    manager.start_apply(batch_plan_id)
    manager.join(5)
    assert manager.status()["phase"] == "completed"
    assert manager._checkpoint_path.is_file()
    assert service.commits == [("safe", "one.feedpak")]
    return manager, service, real_writer


def test_mixed_batch_prepares_in_parallel_and_commits_in_plan_order(
    batch_module, tmp_path
):
    scanner = _Scanner()
    service = _SplitRepairService(synchronize_first_pair=True)
    manager = _manager(batch_module, tmp_path, scanner, service)
    plans = [
        _plan("one.feedpak", safe=True, preview=True),
        _plan("two.feedpak", safe=False, preview=True),
    ]
    batch_plan_id = _prime_apply(
        manager, plans, _snapshot(include_preview_repairs=True)
    )

    status = manager.start_apply(batch_plan_id)
    assert status["worker_policy"]["selected_workers"] == 2
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "completed"
    assert [item["package"] for item in status["result"]["outcomes"]] == [
        "one.feedpak",
        "two.feedpak",
    ]
    assert service.max_prepare_active == 2
    assert service.max_prepared_live <= 2
    assert service.max_commit_active == 1
    assert service.commits == [
        ("safe", "one.feedpak"),
        ("preview", "one.feedpak"),
        ("preview", "two.feedpak"),
    ]
    safe_commit = service.events.index(("commit", "safe", "one.feedpak"))
    combined_preview_prepare = service.events.index(
        ("prepare", "preview", "one.feedpak")
    )
    combined_preview_commit = service.events.index(
        ("commit", "preview", "one.feedpak")
    )
    other_preview_prepare = service.events.index(
        ("prepare", "preview", "two.feedpak")
    )
    assert other_preview_prepare < safe_commit
    assert safe_commit < combined_preview_prepare < combined_preview_commit
    assert status["prepared_completed"] == 3
    assert status["prepared_discarded"] == 0
    assert status["prepare_in_flight"] == 0
    assert status["prepared_ready"] == 0
    assert status["commit_in_flight"] == 0
    assert status["result"]["worker_policy"] == status["worker_policy"]
    assert scanner.finish_count == 1


@pytest.mark.parametrize(
    "fault", ("missing", "non_string", "missing_checker", "raises")
)
def test_batch_preview_fails_closed_without_a_verified_scan_signature(
    batch_module, tmp_path, fault
):
    scanner = _Scanner()
    service = _SplitRepairService()
    candidate = _plan("one.feedpak")
    _apply_signature_fault(scanner, candidate, fault)
    manager = _manager(batch_module, tmp_path, scanner, service)

    manager.start_preview({**_snapshot(), "candidates": [candidate]})
    manager.join(5)
    preview = manager.status()["preview"]

    assert preview["eligible_count"] == 0
    assert preview["blocked_count"] == 1
    assert preview["blocked"][0]["code"] == "package_changed"
    assert service.commits == []


@pytest.mark.parametrize(
    "fault", ("missing", "non_string", "missing_checker", "raises")
)
def test_batch_apply_fails_closed_without_a_verified_scan_signature(
    batch_module, tmp_path, fault
):
    scanner = _Scanner()
    service = _SplitRepairService()
    plan = _plan("one.feedpak")
    _apply_signature_fault(scanner, plan, fault)
    manager = _manager(batch_module, tmp_path, scanner, service)
    batch_plan_id = _prime_apply(manager, [plan], _snapshot())

    manager.start_apply(batch_plan_id)
    manager.join(5)
    outcome = manager.status()["result"]["outcomes"][0]

    assert outcome["code"] == "source_changed"
    assert outcome["file_state"] == "unchanged"
    assert service.commits == []
    assert service.events == []


def test_pipeline_rechecks_the_scan_signature_before_preparation(
    batch_module, tmp_path
):
    scanner = _Scanner()
    checks = 0

    def signature_changes_after_apply_precheck(package, expected):
        nonlocal checks
        checks += 1
        return checks == 1 and expected == f"signature-{package}"

    scanner.package_matches_signature = signature_changes_after_apply_precheck
    service = _SplitRepairService()
    manager = _manager(batch_module, tmp_path, scanner, service)
    batch_plan_id = _prime_apply(
        manager, [_plan("one.feedpak")], _snapshot()
    )

    manager.start_apply(batch_plan_id)
    manager.join(5)
    outcome = manager.status()["result"]["outcomes"][0]

    assert checks == 2
    assert outcome["code"] == "source_changed"
    assert service.commits == []
    assert service.events == []


def test_concurrent_safe_preparation_reuses_signature_bound_deep_report(
    batch_module, tmp_path
):
    scanner = _DeepScanner()
    service = _SplitRepairService(synchronize_first_pair=True)
    manager = _manager(batch_module, tmp_path, scanner, service)
    plans = [_plan("one.feedpak"), _plan("two.feedpak")]
    for item in plans:
        item["scan_signature"] = f"signature-{item['package']}"
    batch_plan_id = _prime_apply(
        manager,
        plans,
        _snapshot(deep_audio=True),
    )

    manager.start_apply(batch_plan_id)
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "completed"
    assert service.max_prepare_active == 2
    assert all(item["deep_audio"] for item in service.source_options)
    assert all(
        isinstance(item["verified_before_report"], dict)
        for item in service.source_options
    )
    assert all(callable(item["source_guard"]) for item in service.source_options)
    assert all(item["source_guard"]() for item in service.source_options)
    assert status["result"]["performance"][
        "verified_scan_report_reused_packages"
    ] == 2
    assert status["result"]["performance"]["deep_audio_reused_packages"] == 2
    assert {package for package, _signature in scanner.report_reads} == {
        "one.feedpak",
        "two.feedpak",
    }


def test_process_validation_waits_while_playback_is_active(
    batch_module, tmp_path
):
    scanner = _PlaybackScanner()
    service = _ValidationCallingService()
    pool = _CountingValidationPool()
    manager = _manager(
        batch_module,
        tmp_path,
        scanner,
        service,
        process_pool_factory=lambda _workers, _version: pool,
    )
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak")],
        _snapshot(),
    )

    manager.start_apply(batch_plan_id)
    assert service.validation_ready.wait(5)
    scanner.playing.set()
    service.allow_validation.set()
    assert pool.pause_observed.wait(5)
    assert pool.submit_count == 0

    scanner.playing.clear()
    scanner.resumed.set()
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "completed"
    assert pool.submit_count == 1
    assert True in pool.paused
    assert pool.paused[-1] is False


def test_in_flight_process_validation_pauses_when_playback_starts(
    batch_module, tmp_path
):
    scanner = _PlaybackScanner()
    service = _ValidationCallingService()
    pool = _BlockingValidationPool()
    manager = _manager(
        batch_module,
        tmp_path,
        scanner,
        service,
        process_pool_factory=lambda _workers, _version: pool,
    )
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak")],
        _snapshot(),
    )

    manager.start_apply(batch_plan_id)
    assert service.validation_ready.wait(5)
    service.allow_validation.set()
    assert pool.submitted.wait(5)
    scanner.playing.set()
    assert pool.pause_observed.wait(5)

    scanner.playing.clear()
    scanner.resumed.set()
    pool.future.set_result({
        "outcome": "complete",
        "report": {"package": "one.feedpak", "findings": []},
    })
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "completed"
    assert pool.submit_count == 1
    assert True in pool.paused
    assert pool.paused[-1] is False


def test_cancel_force_stops_pool_before_waiting_for_prepare_threads(
    batch_module, tmp_path
):
    scanner = _Scanner()
    service = _WorkspaceValidationService()
    pool = _InitiallyFailingShutdownPool()
    service.cleanup_guard = pool.forced_shutdown_complete
    manager = _manager(
        batch_module,
        tmp_path,
        scanner,
        service,
        process_pool_factory=lambda _workers, _version: pool,
    )
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak")],
        _snapshot(),
    )

    manager.start_apply(batch_plan_id)
    assert service.validation_ready.wait(5)
    service.allow_validation.set()
    assert pool.submitted.wait(5)
    started = time.monotonic()
    assert manager.cancel() is True
    manager.join(5)
    elapsed = time.monotonic() - started
    status = manager.status()

    assert elapsed < 2
    assert status["phase"] == "cancelled"
    assert status["result"]["completed_count"] == 0
    assert service.commits == []
    assert service.workspace_active == 0
    assert service.cleanup_saw_force_stop is True
    assert pool.force_attempts == 2
    assert [call[0] for call in pool.shutdown_calls[:2]] == [True, True]


def test_persistent_validator_stop_fault_preserves_real_workspace_and_blocks_repair(
    batch_module, repair_module, tmp_path
):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    note = {"t": 1.0, "s": 0, "f": 3}
    arrangement = json.dumps({
        "notes": [note, dict(note)],
        "chords": [],
    }).encode("utf-8")
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml",
            "arrangements:\n  - id: lead\n    file: arrangements/lead.json\n",
        )
        archive.writestr("arrangements/lead.json", arrangement)

    def validate(path, package_name, *, deep_audio=False):
        del deep_audio
        with zipfile.ZipFile(path) as archive:
            document = json.loads(archive.read("arrangements/lead.json"))
        findings = (
            [{"code": "chart.duplicate-note", "severity": "warning"}]
            if len(document["notes"]) > 1
            else []
        )
        return {
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": False},
            "findings": findings,
        }

    config_dir = tmp_path / "config"
    service = repair_module.RepairService(
        config_dir=config_dir,
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-quarantined-workspace-tests"),
    )
    pool = _PersistentShutdownPool(
        initial_report=validate(package, "Song.feedpak")
    )
    manager = _manager(
        batch_module,
        tmp_path,
        _Scanner(),
        service,
        process_pool_factory=lambda _workers, _version: pool,
    )
    batch_plan_id = _prime_apply(
        manager, [_plan("Song.feedpak")], _snapshot()
    )

    manager.start_apply(batch_plan_id)
    assert pool.candidate_submitted.wait(5)
    started = time.monotonic()
    assert manager.cancel() is True
    manager.join(5)
    elapsed = time.monotonic() - started
    status = manager.status()

    workspaces = list(library.glob(".library-doctor-work-*"))
    receipts = list(
        (config_dir / "library_doctor" / "repair_workspaces").glob("*.json")
    )
    assert elapsed < 2
    assert status["phase"] == "cancelled"
    assert status["result"]["outcome"] == "cancelled"
    assert status["repair_backend_fault"]["restart_required"] is True
    assert status["result"]["repair_backend_fault"] == status[
        "repair_backend_fault"
    ]
    assert "Restart Library Doctor" in status["message"]
    assert service.history()["items"] == []
    backup_root = config_dir / "library_doctor" / "repair_backups"
    assert not backup_root.exists() or not any(backup_root.iterdir())
    assert workspaces and all(path.is_dir() for path in workspaces)
    assert receipts and all(path.is_file() for path in receipts)
    with zipfile.ZipFile(package) as archive:
        assert archive.read("arrangements/lead.json") == arrangement

    for start in (
        lambda: manager.start_apply(batch_plan_id),
        lambda: manager.start_undo_apply("u" * 64),
        lambda: manager.start_finalize_apply("f" * 64),
    ):
        with pytest.raises(batch_module.BatchRepairError) as blocked:
            start()
        assert blocked.value.code == "validation_pool_stop_unconfirmed"

    assert manager.start_preview({"candidates": []})["running"] is True
    manager.join(5)
    assert manager._quarantined_prepare_process_pool is pool
    assert manager.status()["repair_backend_fault"]["restart_required"] is True
    with pytest.raises(batch_module.BatchRepairError) as blocked_after_preview:
        manager.start_apply(batch_plan_id)
    assert blocked_after_preview.value.code == (
        "validation_pool_stop_unconfirmed"
    )

    pool.allow_stop = True
    assert manager.retry_quarantined_prepare_backend() is True
    assert pool.stopped.is_set()
    assert manager._quarantined_prepare_process_pool is None
    assert manager.status()["repair_backend_fault"] is None
    assert workspaces and receipts


def test_validation_pool_shutdown_attempts_every_process_and_is_repeatable(
    scan_worker_module
):
    class Signal:
        def __init__(self):
            self.calls = 0

        def clear(self):
            self.calls += 1

        def set(self):
            self.calls += 1

    class Process:
        def __init__(self, *, terminate_fails=False):
            self.alive = True
            self.terminate_fails = terminate_fails
            self.terminate_calls = 0
            self.kill_calls = 0
            self.join_calls = 0

        def join(self, _timeout):
            self.join_calls += 1

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminate_calls += 1
            if self.terminate_fails:
                raise OSError("terminate failed")
            self.alive = False

        def kill(self):
            self.kill_calls += 1
            self.alive = False

    class Executor:
        def __init__(self, processes):
            self._processes = dict(enumerate(processes))
            self.shutdown_calls = 0

        def shutdown(self, **_options):
            self.shutdown_calls += 1
            self._processes = None
            raise RuntimeError("management thread failed")

    first = Process(terminate_fails=True)
    second = Process()
    pool = scan_worker_module.ValidationProcessPool.__new__(
        scan_worker_module.ValidationProcessPool
    )
    pool._pause_event = Signal()
    pool._cancel_event = Signal()
    pool._executor = Executor([first, second])
    pool._shutdown_lock = threading.Lock()
    pool._shutdown_processes = None
    pool._shutdown_complete = False

    pool.shutdown(force=True, timeout_seconds=0)
    pool.shutdown(force=True, timeout_seconds=0)

    assert first.terminate_calls == 1
    assert second.terminate_calls == 1
    assert first.kill_calls == 1
    assert second.kill_calls == 0
    assert pool._executor.shutdown_calls == 1
    assert pool._shutdown_complete is True


def test_validation_pool_retries_retained_processes_after_failed_stop(
    scan_worker_module
):
    class Signal:
        @staticmethod
        def clear():
            return None

        @staticmethod
        def set():
            return None

    class Process:
        def __init__(self):
            self.alive = True
            self.allow_stop = False
            self.terminate_calls = 0
            self.kill_calls = 0

        @staticmethod
        def join(_timeout):
            return None

        def is_alive(self):
            if not self.allow_stop:
                raise OSError("process state unavailable")
            return self.alive

        def terminate(self):
            self.terminate_calls += 1
            if not self.allow_stop:
                raise OSError("busy")
            self.alive = False

        def kill(self):
            self.kill_calls += 1
            if not self.allow_stop:
                raise OSError("busy")
            self.alive = False

    class Executor:
        def __init__(self, process):
            self._processes = {1: process}
            self.shutdown_calls = 0

        def shutdown(self, **_options):
            self.shutdown_calls += 1
            self._processes = None

    process = Process()
    pool = scan_worker_module.ValidationProcessPool.__new__(
        scan_worker_module.ValidationProcessPool
    )
    pool._pause_event = Signal()
    pool._cancel_event = Signal()
    pool._executor = Executor(process)
    pool._shutdown_lock = threading.Lock()
    pool._shutdown_processes = None
    pool._shutdown_complete = False

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="did not stop"):
        pool.shutdown(force=True, timeout_seconds=0)
    assert time.monotonic() - started < 0.5
    assert pool._shutdown_complete is False
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    with pytest.raises(RuntimeError, match="stopping"):
        pool.submit(Path("candidate"), "Song.feedpak", False)

    process.allow_stop = True
    pool.shutdown(force=True, timeout_seconds=0)

    assert process.terminate_calls == 2
    assert pool._executor.shutdown_calls == 2
    assert pool._shutdown_complete is True


def test_validation_pool_submit_and_shutdown_share_one_lifecycle_lock(
    scan_worker_module
):
    class Process:
        def __init__(self):
            self.alive = True
            self.terminated = False

        @staticmethod
        def join(_timeout):
            return None

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    class Executor:
        def __init__(self, process):
            self._processes = {}
            self.process = process
            self.submit_entered = threading.Event()
            self.release_submit = threading.Event()

        def submit(self, *_args):
            self.submit_entered.set()
            assert self.release_submit.wait(5)
            self._processes[1] = self.process
            return concurrent.futures.Future()

        @staticmethod
        def shutdown(**_options):
            return None

    process = Process()
    executor = Executor(process)
    pool = scan_worker_module.ValidationProcessPool.__new__(
        scan_worker_module.ValidationProcessPool
    )
    pool._pause_event = threading.Event()
    pool._cancel_event = threading.Event()
    pool._executor = executor
    pool._shutdown_lock = threading.Lock()
    pool._shutdown_processes = None
    pool._shutdown_complete = False
    pool._shutdown_started = False
    submitted = threading.Thread(
        target=pool.submit,
        args=(Path("candidate"), "Song.feedpak", False),
    )
    stopped = threading.Thread(
        target=pool.shutdown,
        kwargs={"force": True, "timeout_seconds": 0},
    )

    submitted.start()
    assert executor.submit_entered.wait(5)
    stopped.start()
    executor.release_submit.set()
    submitted.join(5)
    stopped.join(5)

    assert not submitted.is_alive()
    assert not stopped.is_alive()
    assert process.terminated is True
    assert pool._shutdown_processes == [process]
    with pytest.raises(RuntimeError, match="stopping"):
        pool.submit(Path("later"), "Song.feedpak", False)


def test_cancellation_discards_every_uncommitted_candidate(
    batch_module, tmp_path
):
    scanner = _Scanner()
    service = _SplitRepairService(hold_preparation=True)
    manager = _manager(batch_module, tmp_path, scanner, service)
    plans = [_plan("one.feedpak"), _plan("two.feedpak")]
    batch_plan_id = _prime_apply(manager, plans, _snapshot())

    manager.start_apply(batch_plan_id)
    assert service.first_pair_started.wait(5)
    assert manager.cancel() is True
    service.release.set()
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "cancelled"
    assert status["result"]["completed_count"] == 0
    assert service.commits == []
    assert sorted(service.discarded) == [
        ("safe", "one.feedpak"),
        ("safe", "two.feedpak"),
    ]
    assert status["prepared_completed"] == 2
    assert status["prepared_discarded"] == 2
    assert status["prepare_in_flight"] == 0
    assert status["prepared_ready"] == 0
    assert status["commit_in_flight"] == 0


def test_checkpoint_and_pipeline_close_faults_still_release_batch_reservation(
    batch_module, tmp_path, monkeypatch
):
    scanner = _Scanner()
    service = _SplitRepairService(synchronize_first_pair=True)
    manager = _manager(batch_module, tmp_path, scanner, service)
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak"), _plan("two.feedpak")],
        _snapshot(),
    )
    checkpoint_attempts = []

    def fail_progress(**_updates):
        raise RuntimeError("progress state unavailable")

    def fail_checkpoint(result):
        checkpoint_attempts.append(copy.deepcopy(result))
        raise OSError("checkpoint storage unavailable")

    def fail_close(_pipeline):
        raise RuntimeError("pipeline cleanup unavailable")

    manager._progress = fail_progress
    manager._write_checkpoint = fail_checkpoint
    monkeypatch.setattr(
        batch_module._PreparedApplyPipeline,
        "close",
        fail_close,
    )

    manager.start_apply(batch_plan_id)
    manager.join(5)
    status = manager.status()

    assert status["running"] is False
    assert status["phase"] == "error"
    assert scanner.reserved is False
    assert scanner.finish_count == 1
    assert service.commits == [("safe", "one.feedpak")]
    assert len(checkpoint_attempts) == 1
    assert checkpoint_attempts[0]["outcome"] == "interrupted"
    assert checkpoint_attempts[0]["completed_count"] == 1
    assert checkpoint_attempts[0]["remaining_count"] == 1
    assert [
        item["package"] for item in checkpoint_attempts[0]["outcomes"]
    ] == ["one.feedpak"]


def test_final_result_write_failure_retains_full_checkpoint_for_restart(
    batch_module, tmp_path
):
    service = _SplitRepairService(synchronize_first_pair=True)
    manager = _manager(batch_module, tmp_path, _Scanner(), service)
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak"), _plan("two.feedpak")],
        _snapshot(),
    )
    manager._write_last_result = lambda _result: False

    manager.start_apply(batch_plan_id)
    manager.join(5)
    checkpoint = manager._checkpoint_path
    assert checkpoint.is_file()
    assert service.commits == [
        ("safe", "one.feedpak"),
        ("safe", "two.feedpak"),
    ]

    restarted = _manager(
        batch_module, tmp_path, _Scanner(), service
    )
    recovered = restarted.status()["last_result"]

    assert recovered["recovered_from_checkpoint"] is True
    assert recovered["outcome"] == "interrupted"
    assert recovered["completed_count"] == 2
    assert [item["package"] for item in recovered["outcomes"]] == [
        "one.feedpak",
        "two.feedpak",
    ]
    assert not checkpoint.exists()
    assert len(service.commits) == 2


def test_failed_checkpoint_promotion_keeps_checkpoint_for_next_restart(
    batch_module, tmp_path, monkeypatch
):
    service = _SplitRepairService()
    manager = _manager(batch_module, tmp_path, _Scanner(), service)
    batch_plan_id = _prime_apply(
        manager, [_plan("one.feedpak")], _snapshot()
    )
    manager._write_last_result = lambda _result: False
    manager.start_apply(batch_plan_id)
    manager.join(5)
    checkpoint = manager._checkpoint_path

    monkeypatch.setattr(
        batch_module.BatchRepairManager,
        "_write_last_result",
        lambda _self, _result: False,
    )
    restarted = _manager(
        batch_module, tmp_path, _Scanner(), service
    )

    assert restarted.status()["last_result"][
        "recovered_from_checkpoint"
    ] is True
    assert checkpoint.is_file()
    assert checkpoint.read_bytes() != b""
    assert service.commits == [("safe", "one.feedpak")]


@pytest.mark.parametrize("mutation", ["restore", "finalize"])
def test_direct_post_batch_mutation_refreshes_retained_checkpoint(
    batch_module, tmp_path, mutation
):
    manager, service, real_writer = _seed_retained_result_checkpoint(
        batch_module, tmp_path
    )
    backup_id = "backup-one.feedpak"
    if mutation == "restore":
        assert manager.mark_restored(
            "one.feedpak", backup_id, cache_updated=True
        ) is True
    else:
        assert manager.mark_finalized(
            "one.feedpak", backup_id, package_state="repaired"
        ) is True
    checkpoint = manager._checkpoint_path
    assert checkpoint.is_file()

    manager._write_last_result = real_writer
    real_delete = manager._delete_checkpoint
    manager._delete_checkpoint = lambda: False
    manager._persist_latest_result(
        copy.deepcopy(manager.status()["last_result"])
    )
    assert checkpoint.is_file()

    restarted = _manager(
        batch_module, tmp_path, _Scanner(), service
    )
    outcome = restarted.status()["last_result"]["outcomes"][0]
    if mutation == "restore":
        assert outcome["outcome"] == "restored"
        assert outcome["file_state"] == "restored"
    else:
        assert outcome["outcome"] == "finalized"
        assert outcome["undo_available"] is False
    assert not checkpoint.exists()
    assert service.commits == [("safe", "one.feedpak")]
    manager._delete_checkpoint = real_delete


@pytest.mark.parametrize("mutation", ["undo", "finalize"])
def test_batch_post_mutation_refreshes_retained_checkpoint(
    batch_module, tmp_path, mutation
):
    manager, service, _real_writer = _seed_retained_result_checkpoint(
        batch_module, tmp_path
    )
    backup_id = "backup-one.feedpak"
    plan = {
        "package": "one.feedpak",
        "title": "one.feedpak",
        "artist": "Artist",
        "backup_id": backup_id,
        "change_kind": "remove_duplicates",
        "change_count": 1,
        "removed_count": 1,
        "repair_summaries": [],
        "member_count": 1,
        "recovery_bytes": 128,
        "package_state": "repaired",
        "recovery_kind": "song_data",
    }
    if mutation == "undo":
        operation_id = "u" * 64
        manager._undo_plans = [plan]
        manager._state.update({
            "phase": "undo_ready",
            "undo_preview": {"undo_plan_id": operation_id},
        })
        manager.start_undo_apply(operation_id)
    else:
        operation_id = "f" * 64
        manager._finalize_plans = [plan]
        manager._state.update({
            "phase": "finalize_ready",
            "finalize_preview": {"finalize_plan_id": operation_id},
        })
        manager.start_finalize_apply(operation_id)
    manager.join(5)
    assert manager._checkpoint_path.is_file()

    restarted = _manager(
        batch_module, tmp_path, _Scanner(), service
    )
    latest = restarted.status()["last_result"]
    outcome = latest["outcomes"][0]
    if mutation == "undo":
        assert outcome["outcome"] == "restored"
        assert latest["latest_undo_result"]["restored_count"] == 1
        assert len(service.restore_calls) == 1
    else:
        assert outcome["outcome"] == "finalized"
        assert latest["latest_finalize_result"]["finalized_count"] == 1
        assert len(service.finalize_calls) == 1
    assert service.commits == [("safe", "one.feedpak")]


def test_start_apply_failure_preserves_prior_checkpoint_receipt(
    batch_module, tmp_path, monkeypatch
):
    manager, service, _real_writer = _seed_retained_result_checkpoint(
        batch_module, tmp_path
    )
    checkpoint = manager._checkpoint_path
    checkpoint_before = checkpoint.read_bytes()
    next_plan_id = _prime_apply(
        manager, [_plan("two.feedpak")], _snapshot()
    )

    def fail_start(_thread):
        raise RuntimeError("thread start unavailable")

    monkeypatch.setattr(batch_module.threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread start unavailable"):
        manager.start_apply(next_plan_id)

    assert checkpoint.read_bytes() == checkpoint_before
    assert manager._scanner.reserved is False
    restarted = _manager(
        batch_module, tmp_path, _Scanner(), service
    )
    recovered = restarted.status()["last_result"]
    assert [item["package"] for item in recovered["outcomes"]] == [
        "one.feedpak"
    ]
    assert service.commits == [("safe", "one.feedpak")]


def test_cancellation_after_safe_commit_does_not_start_combined_preview(
    batch_module, tmp_path
):
    scanner = _Scanner()
    service = _CancelAfterSafeService()
    manager = _manager(batch_module, tmp_path, scanner, service)
    service.cancel_callback = manager.cancel
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak", safe=True, preview=True)],
        _snapshot(include_preview_repairs=True),
    )

    manager.start_apply(batch_plan_id)
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "cancelled"
    assert service.commits == [("safe", "one.feedpak")]
    assert ("prepare", "preview", "one.feedpak") not in service.events
    assert status["result"]["outcomes"][0]["outcome"] == "partial"
    assert status["result"]["outcomes"][0]["code"] == "batch_cancelled"


def test_playback_after_safe_commit_delays_combined_preview_preparation(
    batch_module, tmp_path
):
    scanner = _PlaybackScanner()
    service = _PlaybackAfterSafeService()
    manager = _manager(batch_module, tmp_path, scanner, service)
    service.playback_callback = scanner.playing.set
    batch_plan_id = _prime_apply(
        manager,
        [_plan("one.feedpak", safe=True, preview=True)],
        _snapshot(include_preview_repairs=True),
    )

    manager.start_apply(batch_plan_id)
    assert service.safe_committed.wait(5)
    assert ("prepare", "preview", "one.feedpak") not in service.events

    scanner.playing.clear()
    scanner.resumed.set()
    manager.join(5)
    status = manager.status()

    assert status["phase"] == "completed"
    assert service.commits == [
        ("safe", "one.feedpak"),
        ("preview", "one.feedpak"),
    ]


def test_worker_policy_uses_actual_preview_work_and_backend_availability(
    batch_module, tmp_path
):
    observations = []

    def policy(pending, **options):
        observations.append((pending, options))
        return {
            "selected_workers": 3,
            "active_worker_limit": 3,
            "recommended_workers": 3,
            "manual_max_workers": 4,
        }

    service = _SplitRepairService()
    manager = _manager(
        batch_module, tmp_path / "available", _Scanner(), service, policy=policy
    )
    no_preview = manager._select_prepare_worker_policy(
        [_plan("one.feedpak"), _plan("two.feedpak")],
        _snapshot(include_preview_repairs=True),
        None,
    )
    mixed = manager._select_prepare_worker_policy(
        [_plan("one.feedpak", preview=True), _plan("two.feedpak")],
        _snapshot(include_preview_repairs=True),
        4,
    )

    unavailable = batch_module.BatchRepairManager(
        config_dir=tmp_path / "unavailable" / "config",
        scanner=_Scanner(),
        repair_service=service,
        repair_error_type=_RepairError,
        log=logging.getLogger("library-doctor-worker-policy-fallback-tests"),
        prepare_worker_policy=policy,
    )._select_prepare_worker_policy(
        [_plan("one.feedpak"), _plan("two.feedpak")],
        _snapshot(),
        None,
    )

    assert observations[0][1]["include_preview_repairs"] is False
    assert observations[0][1]["worker_backend_available"] is True
    assert observations[1][1]["include_preview_repairs"] is True
    assert observations[1][1]["requested_max"] == 4
    assert observations[2][1]["worker_backend_available"] is False
    assert no_preview["selected_workers"] == 2
    assert mixed["selected_workers"] == 2
    assert unavailable["selected_workers"] == 1
    assert unavailable["reason"] == "worker_backend_unavailable"
    assert unavailable["pipeline_enabled"] is True


def test_worker_policy_receives_path_free_storage_evidence_and_survives_failure(
    batch_module, tmp_path
):
    observations = []

    def policy(_pending, **options):
        observations.append(options)
        return {
            "selected_workers": 2,
            "active_worker_limit": 2,
            "recommended_workers": 2,
            "manual_max_workers": 2,
        }

    class StorageScanner(_Scanner):
        def repair_storage_evidence(self, packages):
            assert list(packages) == ["one.feedpak", "two.feedpak"]
            return {
                "storage_total_bytes": 10_000,
                "storage_free_bytes": 4_000,
                "largest_source_package_bytes": 500,
                "ignored_path": "must-not-be-forwarded",
            }

    manager = _manager(
        batch_module,
        tmp_path / "available",
        StorageScanner(),
        _SplitRepairService(),
        policy=policy,
    )
    manager._select_prepare_worker_policy(
        [_plan("one.feedpak"), _plan("two.feedpak")],
        _snapshot(),
        None,
    )

    class FailingStorageScanner(_Scanner):
        @staticmethod
        def repair_storage_evidence(_packages):
            raise OSError("unavailable")

    failed = _manager(
        batch_module,
        tmp_path / "failed",
        FailingStorageScanner(),
        _SplitRepairService(),
        policy=policy,
    )._select_prepare_worker_policy(
        [_plan("one.feedpak"), _plan("two.feedpak")],
        _snapshot(),
        None,
    )

    assert {
        key: observations[0][key]
        for key in (
            "storage_total_bytes",
            "storage_free_bytes",
            "largest_source_package_bytes",
        )
    } == {
        "storage_total_bytes": 10_000,
        "storage_free_bytes": 4_000,
        "largest_source_package_bytes": 500,
    }
    assert "ignored_path" not in observations[0]
    assert not any(key.startswith("storage_") for key in observations[1])
    assert failed["selected_workers"] == 2


def test_process_validation_errors_are_fail_closed(batch_module, tmp_path):
    manager = _manager(
        batch_module,
        tmp_path,
        _Scanner(),
        _SplitRepairService(),
    )
    pipeline = batch_module._PreparedApplyPipeline(
        manager,
        [_plan("one.feedpak")],
        _snapshot(),
        {"selected_workers": 1, "active_worker_limit": 1},
    )

    class FailingPool:
        @staticmethod
        def submit(*_args, **_kwargs):
            future = concurrent.futures.Future()
            future.set_exception(RuntimeError("worker exited"))
            return future

        @staticmethod
        def shutdown(*, force, timeout_seconds):
            del force, timeout_seconds

    pipeline._process_pool = FailingPool()
    with pytest.raises(_RepairError) as raised:
        pipeline._worker_validate(
            tmp_path / "candidate.feedpak",
            "one.feedpak",
            deep_audio=False,
        )
    assert raised.value.code == "candidate_validation_failed"
    assert raised.value.file_state == "unchanged"

    manager._cancel.set()
    with pytest.raises(_RepairError) as cancelled:
        pipeline._worker_validate(
            tmp_path / "candidate.feedpak",
            "one.feedpak",
            deep_audio=False,
        )
    assert cancelled.value.code == "batch_cancelled"
    assert cancelled.value.file_state == "unchanged"


def test_prepare_creates_no_recovery_state_and_rechecks_source_before_backup(
    repair_module, tmp_path
):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    note = {"t": 1.0, "s": 0, "f": 3}
    arrangement = json.dumps({
        "notes": [note, dict(note)],
        "chords": [],
    }).encode("utf-8")
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml",
            "arrangements:\n  - id: lead\n    file: arrangements/lead.json\n",
        )
        archive.writestr("arrangements/lead.json", arrangement)

    def validate(path, package_name, *, deep_audio=False):
        del deep_audio
        with zipfile.ZipFile(path) as archive:
            document = json.loads(archive.read("arrangements/lead.json"))
        findings = (
            [{"code": "chart.duplicate-note", "severity": "warning"}]
            if len(document["notes"]) > 1
            else []
        )
        return {
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": False},
            "findings": findings,
        }

    config_dir = tmp_path / "config"
    service = repair_module.RepairService(
        config_dir=config_dir,
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-split-source-guard-tests"),
    )

    prepared = service.prepare_selected(
        "Song.feedpak", ["chart.duplicate-note"]
    )
    candidate = prepared.candidate
    assert candidate.is_file()
    assert not (config_dir / "library_doctor" / "repair_backups").exists()
    assert not (config_dir / "library_doctor" / "repair_history.json").exists()

    with zipfile.ZipFile(package, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("external-edit.txt", b"preserve me")

    with pytest.raises(repair_module.RepairPlanningError) as raised:
        service.commit_prepared(prepared)

    assert raised.value.code == "source_changed"
    assert not candidate.exists()
    assert not (config_dir / "library_doctor" / "repair_backups").exists()
    assert not (config_dir / "library_doctor" / "repair_history.json").exists()
    with zipfile.ZipFile(package) as archive:
        assert archive.read("external-edit.txt") == b"preserve me"
        assert archive.read("arrangements/lead.json") == arrangement


@pytest.mark.parametrize("guard_failure", ["false", "raises"])
def test_directory_commit_rechecks_full_scan_guard_before_backup(
    repair_module, tmp_path, guard_failure
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangement_path = package / "arrangements" / "lead.json"
    arrangement_path.parent.mkdir(parents=True)
    note = {"t": 1.0, "s": 0, "f": 3}
    arrangement = json.dumps({
        "notes": [note, dict(note)],
        "chords": [],
    }).encode("utf-8")
    (package / "manifest.yaml").write_text(
        "arrangements:\n  - id: lead\n    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    arrangement_path.write_bytes(arrangement)
    unrelated = package / "cover.txt"
    unrelated.write_bytes(b"original")

    def validate(path, package_name, *, deep_audio=False):
        del deep_audio
        document = json.loads(
            (Path(path) / "arrangements" / "lead.json").read_bytes()
        )
        findings = (
            [{"code": "chart.duplicate-note", "severity": "warning"}]
            if len(document["notes"]) > 1
            else []
        )
        return {
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": False},
            "findings": findings,
        }

    guard_state = {"valid": True}

    def source_guard():
        if guard_state["valid"] == "raises":
            raise OSError("signature unavailable")
        return guard_state["valid"] is True

    config_dir = tmp_path / "config"
    service = repair_module.RepairService(
        config_dir=config_dir,
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-directory-source-guard-tests"),
    )
    prepared = service.prepare_selected(
        "Song.feedpak",
        ["chart.duplicate-note"],
        source_guard=source_guard,
    )
    candidate = prepared.candidate
    assert candidate.is_dir()

    unrelated.write_bytes(b"external edit")
    guard_state["valid"] = guard_failure
    with pytest.raises(repair_module.RepairPlanningError) as raised:
        service.commit_prepared(prepared)

    assert raised.value.code == "source_changed"
    assert raised.value.file_state == "unchanged"
    assert unrelated.read_bytes() == b"external edit"
    assert arrangement_path.read_bytes() == arrangement
    assert not candidate.exists()
    assert service.history()["items"] == []
    backup_root = config_dir / "library_doctor" / "repair_backups"
    assert not backup_root.exists() or not any(backup_root.iterdir())
