import concurrent.futures
import importlib.util
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def scanner_module():
    path = Path(__file__).parents[1] / "scanner.py"
    name = "library_doctor_scanner_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _report(package, *, status="healthy", lyrics=False, preview=False):
    errors = int(status == "error")
    warnings = int(status == "warning")
    reviews = int(status == "review")
    findings = []
    if errors or warnings or reviews:
        severity = "error" if errors else "warning" if warnings else "info"
        findings.append({
            "severity": severity,
            "code": f"test.{severity}",
            "message": f"A {severity} for testing.",
            "category": "authoring_review" if reviews else "validation",
            "location": "",
            "arrangement_id": None,
            "time": None,
            "string": None,
        })
    return {
        "schema": "library_doctor.package.v1",
        "validator_version": "test-v1",
        "package": package,
        "title": Path(package).stem,
        "artist": "Test Artist",
        "status": status,
        "counts": {"error": errors, "warning": warnings, "info": reviews},
        "features": {
            "lyrics_declared": lyrics,
            "lyrics_entries": int(lyrics),
            "preview_declared": preview,
            "preview_available": preview,
            "deep_audio_checked": False,
            "deep_audio_files": 0,
            "deep_audio_skipped": 0,
            "deep_audio_unsupported": 0,
        },
        "findings": findings,
    }


def _make_scanner(scanner_module, tmp_path, validator, **scanner_options):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    instance = scanner_module.LibraryScanner(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validator,
        validator_version="test-v1",
        log=logging.getLogger("library-doctor-tests"),
        **scanner_options,
    )
    return instance, library


def _run(instance, *, force=False, **target):
    assert instance.start(force=force, **target) is True
    instance.join(5)
    status = instance.status()
    assert status["running"] is False
    return status


def test_completed_scan_exposes_aggregate_performance_telemetry(
    scanner_module, tmp_path,
):
    instance, library = _make_scanner(
        scanner_module,
        tmp_path,
        lambda _path, package, **_kwargs: _report(package),
    )
    (library / "Song.feedpak").mkdir()

    status = _run(instance, force=True)

    expected = {
        "discovery_seconds",
        "signature_seconds",
        "cache_lookup_seconds",
        "validation_seconds",
        "parallel_validation_wall_seconds",
        "worker_validation_seconds",
        "worker_queue_seconds",
        "source_recheck_seconds",
        "source_change_retries",
        "parallel_fallbacks",
        "cache_write_seconds",
        "scope_update_seconds",
    }
    assert set(status["performance"]) == expected
    assert all(value >= 0 for value in status["performance"].values())
    assert status["last_scan"]["performance"] == status["performance"]


def test_worker_policy_uses_cpu_memory_task_and_user_limits(scanner_module):
    policy = scanner_module.choose_worker_policy(
        200,
        deep_audio=True,
        requested_max=40,
        logical_cpus=256,
        physical_cpus=128,
        total_memory=256 * 1024**3,
        available_memory=200 * 1024**3,
        platform="win32",
        environment={"FEEDBACK_MAX_SCAN_WORKERS": "80"},
    )

    assert policy["mode"] == "custom"
    assert policy["limits"]["packages"] == 200
    assert policy["limits"]["physical_cpu"] == 128
    assert policy["limits"]["platform"] == 61
    assert policy["limits"]["feedback"] == 80
    assert policy["limits"]["user"] == 40
    assert policy["selected_workers"] == 40


def test_worker_policy_stays_sequential_for_a_small_scope(scanner_module):
    policy = scanner_module.choose_worker_policy(
        2,
        deep_audio=False,
        logical_cpus=32,
        physical_cpus=16,
        total_memory=64 * 1024**3,
        available_memory=48 * 1024**3,
        platform="linux",
        environment={},
    )

    assert policy["selected_workers"] == 1
    assert policy["reason"] == "small_scope"


def test_worker_policy_honors_memory_and_feedback_caps(scanner_module):
    policy = scanner_module.choose_worker_policy(
        100,
        deep_audio=True,
        logical_cpus=64,
        physical_cpus=32,
        total_memory=8 * 1024**3,
        available_memory=3 * 1024**3,
        platform="linux",
        environment={"FEEDBACK_MAX_SCAN_WORKERS": "12"},
    )

    assert policy["limits"]["memory"] == 2
    assert policy["selected_workers"] == 2


def test_parallel_scanner_keeps_cache_writes_in_parent_and_reports_workers(
    scanner_module, tmp_path, monkeypatch,
):
    calls = []

    choose_worker_policy = scanner_module.choose_worker_policy

    def deterministic_worker_policy(
        pending_packages,
        *,
        deep_audio,
        requested_max,
        worker_backend_available,
    ):
        return choose_worker_policy(
            pending_packages,
            deep_audio=deep_audio,
            requested_max=requested_max,
            worker_backend_available=worker_backend_available,
            logical_cpus=8,
            physical_cpus=4,
            total_memory=16 * 1024**3,
            available_memory=12 * 1024**3,
            platform="linux",
            environment={},
        )

    monkeypatch.setattr(
        scanner_module, "choose_worker_policy", deterministic_worker_policy
    )

    def validate(_path, package, **_options):
        calls.append((package, threading.get_ident()))
        time.sleep(0.03)
        return _report(package)

    class ThreadValidationPool:
        def __init__(self, workers):
            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            self.cancelled = threading.Event()

        def submit(self, path, package, deep_audio):
            def run():
                started = time.perf_counter()
                if self.cancelled.is_set():
                    return {"outcome": "cancelled", "elapsed_seconds": 0.0}
                report = validate(path, package, deep_audio=deep_audio)
                return {
                    "outcome": "complete",
                    "report": report,
                    "elapsed_seconds": time.perf_counter() - started,
                }

            return self.executor.submit(run)

        def set_paused(self, _paused):
            pass

        def cancel(self):
            self.cancelled.set()

        def shutdown(self):
            self.executor.shutdown(wait=True, cancel_futures=True)

    pools = []

    def pool_factory(workers, _validator_version):
        pool = ThreadValidationPool(workers)
        pools.append(pool)
        return pool

    instance, library = _make_scanner(
        scanner_module,
        tmp_path,
        validate,
        worker_pool_factory=pool_factory,
    )
    for index in range(6):
        (library / f"song-{index}.feedpak").write_bytes(str(index).encode())

    status = _run(instance, force=True, max_workers=3)

    assert status["stage"] == "complete"
    assert status["scanned"] == 6
    assert status["worker_policy"]["selected_workers"] == 3
    assert status["performance"]["parallel_fallbacks"] == 0
    assert status["performance"]["worker_validation_seconds"] > 0
    assert len({thread_id for _package, thread_id in calls}) > 1
    assert len(instance.results()["items"]) == 6
    assert len(pools) == 1


def test_parallel_pool_start_failure_falls_back_to_sequential_validation(
    scanner_module, tmp_path,
):
    calls = []

    def validate(_path, package, **_options):
        calls.append(package)
        return _report(package)

    def unavailable_pool(*_args):
        raise RuntimeError("processes unavailable")

    instance, library = _make_scanner(
        scanner_module,
        tmp_path,
        validate,
        worker_pool_factory=unavailable_pool,
    )
    for index in range(3):
        (library / f"song-{index}.feedpak").write_bytes(str(index).encode())

    status = _run(instance, force=True, max_workers=3)

    assert status["stage"] == "complete"
    assert status["scanned"] == 3
    assert status["performance"]["parallel_fallbacks"] == 1
    assert sorted(calls) == [f"song-{index}.feedpak" for index in range(3)]


def test_scan_retries_when_a_feedpak_changes_during_validation(
    scanner_module, tmp_path,
):
    calls = []

    def validate(path, package, **_options):
        calls.append(path.read_bytes())
        if len(calls) == 1:
            path.write_bytes(b"second version")
        report = _report(package)
        report["title"] = path.read_bytes().decode()
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    package = library / "song.feedpak"
    package.write_bytes(b"first version")

    status = _run(instance, force=True, max_workers=1)
    report = instance.results()["items"][0]

    assert status["stage"] == "complete"
    assert status["performance"]["source_change_retries"] == 1
    assert len(calls) == 2
    assert report["title"] == "second version"


def test_scan_discovers_zip_and_directory_packages(scanner_module, tmp_path):
    seen = []

    def validate(path, package):
        seen.append((path, package))
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "Artist").mkdir()
    (library / "Artist" / "one.feedpak").write_bytes(b"zip placeholder")
    directory_pack = library / "two.sloppak"
    directory_pack.mkdir()
    (directory_pack / "manifest.yaml").write_text("title: two", encoding="utf-8")
    # A suffix-looking file inside a directory-form package is package content,
    # not a second library item.
    (directory_pack / "nested.feedpak").write_bytes(b"not another package")

    status = _run(instance)

    assert status["stage"] == "complete"
    assert status["total"] == 2
    assert status["scanned"] == 2
    assert status["scan_current"] is True
    assert status["scope_complete"] is True
    assert {item[1] for item in seen} == {"Artist/one.feedpak", "two.sloppak"}


def test_old_rule_scan_stays_visible_but_is_not_current_for_repairs(
    scanner_module, tmp_path,
):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(
            package, status="warning"
        )
    )
    (library / "one.feedpak").write_bytes(b"one")
    _run(instance)

    restarted = scanner_module.LibraryScanner(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="test-v2",
        log=logging.getLogger("library-doctor-stale-scan-tests"),
    )

    status = restarted.status()
    report = restarted.results()["items"][0]
    assert status["last_scan"]["complete"] is True
    assert status["scan_current"] is False
    assert status["scope_complete"] is False
    assert report["features"]["repair_scan_current"] is False


def test_unchanged_packages_reuse_cached_reports(scanner_module, tmp_path):
    calls = []

    def validate(_path, package):
        calls.append(package)
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    package = library / "one.feedpak"
    package.write_bytes(b"first")

    first = _run(instance)
    second = _run(instance)

    assert first["scanned"] == 1
    assert second["scanned"] == 0
    assert second["reused"] == 1
    assert calls == ["one.feedpak"]


def test_force_and_file_changes_bypass_cache(scanner_module, tmp_path):
    calls = []

    def validate(_path, package):
        calls.append(package)
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    package = library / "one.feedpak"
    package.write_bytes(b"first")

    _run(instance)
    _run(instance, force=True)
    package.write_bytes(b"a different size")
    third = _run(instance)

    assert third["scanned"] == 1
    assert calls == ["one.feedpak", "one.feedpak", "one.feedpak"]


def test_results_support_problem_and_coverage_filters(scanner_module, tmp_path):
    def validate(_path, package):
        if package.startswith("error"):
            return _report(package, status="error", lyrics=True, preview=True)
        if package.startswith("warn"):
            return _report(package, status="warning", lyrics=False, preview=True)
        if package.startswith("review"):
            return _report(package, status="review", lyrics=True, preview=False)
        report = _report(package, lyrics=False, preview=False)
        report["features"]["deep_audio_checked"] = True
        report["features"]["deep_audio_unsupported"] = 1
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    for name in ("error.feedpak", "warn.feedpak", "review.feedpak", "clean.feedpak"):
        (library / name).write_bytes(name.encode())
    _run(instance)

    assert instance.results(result_filter="problems")["total"] == 3
    assert instance.results(result_filter="errors")["total"] == 1
    assert instance.results(result_filter="warnings")["total"] == 1
    assert instance.results(result_filter="review")["total"] == 1
    assert instance.results(result_filter="healthy")["total"] == 1
    assert instance.results(result_filter="no_lyrics")["total"] == 2
    assert instance.results(result_filter="no_preview")["total"] == 2
    assert instance.results(result_filter="deep_audio_partial")["total"] == 1
    assert instance.status()["summary"]["reviews"] == 1
    assert instance.status()["summary"]["deep_audio_partial"] == 1
    assert instance.results(query="clean")["items"][0]["package"] == "clean.feedpak"
    assert instance.results(query="%")["total"] == 0
    assert instance.results(query="_")["total"] == 0


def test_completed_scan_removes_reports_for_deleted_packages(scanner_module, tmp_path):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    package = library / "one.feedpak"
    package.write_bytes(b"one")
    _run(instance)
    package.unlink()

    status = _run(instance)

    assert status["summary"]["total"] == 0
    assert instance.results()["items"] == []


def test_folder_scan_is_recursive_and_scopes_visible_results(scanner_module, tmp_path):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    artist = library / "Artist"
    nested = artist / "Album"
    nested.mkdir(parents=True)
    (artist / "one.feedpak").write_bytes(b"one")
    (nested / "two.sloppak").write_bytes(b"two")
    (library / "outside.feedpak").write_bytes(b"outside")
    _run(instance)

    status = _run(
        instance,
        target_kind="folder",
        selected_path=str(artist),
    )

    assert status["total"] == 2
    assert status["reused"] == 2
    assert status["summary"]["total"] == 2
    assert status["target"] == {"kind": "folder", "label": "Artist"}
    assert {item["package"] for item in instance.results()["items"]} == {
        "Artist/one.feedpak",
        "Artist/Album/two.sloppak",
    }

    restored = _run(instance)
    assert restored["summary"]["total"] == 3
    assert restored["reused"] == 3


def test_directory_discovery_honors_cooperative_cancellation(scanner_module, tmp_path):
    root = tmp_path / "library"
    for index in range(5):
        package = root / f"folder-{index}" / f"song-{index}.feedpak"
        package.mkdir(parents=True)
    checkpoints = []

    packages, errors = scanner_module.LibraryScanner._discover(
        root,
        scan_checkpoint=lambda: checkpoints.append(len(checkpoints)),
        cancelled=lambda: len(checkpoints) >= 2,
    )

    assert len(checkpoints) == 2
    assert packages == []
    assert errors == []


def test_single_file_scan_scopes_results_to_one_package(scanner_module, tmp_path):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    chosen = library / "chosen.feedpak"
    chosen.write_bytes(b"chosen")
    (library / "other.feedpak").write_bytes(b"other")
    _run(instance)

    status = _run(
        instance,
        target_kind="file",
        selected_path=str(chosen),
    )

    assert status["summary"]["total"] == 1
    assert status["target"] == {"kind": "file", "label": "chosen.feedpak"}
    assert instance.results()["items"][0]["package"] == "chosen.feedpak"


def test_selecting_library_root_as_a_folder_uses_library_scope(scanner_module, tmp_path):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    (library / "one.feedpak").write_bytes(b"one")

    status = _run(
        instance,
        target_kind="folder",
        selected_path=str(library),
    )

    assert status["target"] == {"kind": "library", "label": "Whole library"}


def test_existing_report_cache_is_migrated_into_visible_scope(scanner_module, tmp_path):
    database = tmp_path / "config" / "library_doctor" / "library_doctor.db"
    cache = scanner_module._ReportCache(database)
    legacy_report = _report("legacy.feedpak")
    legacy_report["features"]["deep_audio_checked"] = True
    legacy_report["features"]["deep_audio_unsupported"] = 1
    cache.put("legacy.feedpak", "f:1:1", "test-v1", legacy_report, 1.0)
    with cache._conn:
        cache._conn.execute(
            "UPDATE reports SET deep_audio_unsupported = 0 WHERE package = ?",
            ("legacy.feedpak",),
        )
        cache._conn.execute("DROP TABLE current_scope")
        cache._conn.execute("DROP TABLE cache_state")
    cache._conn.close()

    migrated = scanner_module._ReportCache(database)

    assert migrated.summary()["total"] == 1
    assert migrated.summary()["deep_audio_partial"] == 1
    assert migrated.current_target() == {"kind": "library", "label": "Whole library"}
    migrated._conn.close()


def test_selected_targets_must_stay_inside_the_library(scanner_module, tmp_path):
    instance, _library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="inside the configured song library"):
        instance.start(target_kind="folder", selected_path=str(outside))


def test_single_file_target_requires_a_supported_package(scanner_module, tmp_path):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    unsupported = library / "notes.txt"
    unsupported.write_text("not a package", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.feedpak or \.sloppak"):
        instance.start(target_kind="file", selected_path=str(unsupported))


def test_validator_exception_becomes_one_package_error(scanner_module, tmp_path):
    def validate(_path, _package):
        raise RuntimeError("broken rule")

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "one.feedpak").write_bytes(b"one")

    status = _run(instance)
    report = instance.results()["items"][0]

    assert status["stage"] == "complete"
    assert report["status"] == "error"
    assert report["findings"][0]["code"] == "scan.validation-failed"


def test_scan_can_be_cancelled_without_deleting_cached_results(scanner_module, tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def validate(_path, package):
        entered.set()
        release.wait(2)
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    for index in range(3):
        (library / f"{index}.feedpak").write_bytes(str(index).encode())

    assert instance.start() is True
    assert entered.wait(2)
    assert instance.cancel() is True
    release.set()
    instance.join(5)

    status = instance.status()
    assert status["stage"] == "cancelled"
    assert status["cancelled"] is True
    assert status["done"] == 1
    assert status["summary"]["total"] == 1
    assert status["scope_complete"] is False
    assert status["last_scan"]["outcome"] == "cancelled"
    assert status["last_scan"]["complete"] is False
    assert status["last_scan"]["expected"] == 3
    assert status["last_scan"]["completed"] == 1

    restarted, _library = _make_scanner(scanner_module, tmp_path, validate)
    assert restarted.status()["last_scan"]["outcome"] == "cancelled"
    assert restarted.status()["last_scan"]["complete"] is False


def test_scan_waits_for_playback_and_resumes_automatically(scanner_module, tmp_path):
    entered = threading.Event()

    def validate(_path, package):
        entered.set()
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "one.feedpak").write_bytes(b"one")

    assert instance.set_playback_active(True) is True
    assert instance.start() is True
    deadline = time.monotonic() + 2
    while instance.status()["stage"] != "paused" and time.monotonic() < deadline:
        time.sleep(0.01)

    paused = instance.status()
    assert paused["running"] is True
    assert paused["playback_active"] is True
    assert paused["playback_paused"] is True
    assert entered.is_set() is False

    time.sleep(0.08)
    assert instance.set_playback_active(False) is True
    instance.join(5)

    complete = instance.status()
    assert complete["stage"] == "complete"
    assert complete["playback_active"] is False
    assert complete["playback_paused"] is False
    assert entered.is_set() is True
    assert complete["elapsed_seconds"] - complete["active_seconds"] >= 0.05


def test_scan_pauses_at_checkpoint_inside_large_package(scanner_module, tmp_path):
    entered = threading.Event()
    continue_validation = threading.Event()
    finished = threading.Event()

    def validate(_path, package, *, scan_checkpoint=None):
        entered.set()
        continue_validation.wait(2)
        scan_checkpoint()
        finished.set()
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "one.feedpak").write_bytes(b"one")

    assert instance.start() is True
    assert entered.wait(2)
    instance.set_playback_active(True)
    continue_validation.set()
    deadline = time.monotonic() + 2
    while instance.status()["stage"] != "paused" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert instance.status()["playback_paused"] is True
    assert finished.is_set() is False

    time.sleep(0.08)
    instance.set_playback_active(False)
    instance.join(5)
    assert finished.is_set() is True
    status = instance.status()
    assert status["stage"] == "complete"
    assert status["elapsed_seconds"] - status["active_seconds"] >= 0.05


def test_cancelling_a_playback_paused_scan_wakes_the_worker(scanner_module, tmp_path):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    (library / "one.feedpak").write_bytes(b"one")
    instance.set_playback_active(True)
    assert instance.start() is True
    deadline = time.monotonic() + 2
    while instance.status()["stage"] != "paused" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert instance.cancel() is True
    instance.join(5)

    status = instance.status()
    assert status["running"] is False
    assert status["stage"] == "cancelled"


def test_status_and_results_are_safe_while_scan_updates_cache(scanner_module, tmp_path):
    second_started = threading.Event()
    release = threading.Event()
    calls = 0

    def validate(_path, package):
        nonlocal calls
        calls += 1
        if calls == 2:
            second_started.set()
            release.wait(2)
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "a.feedpak").write_bytes(b"a")
    _run(instance)
    (library / "b.feedpak").write_bytes(b"b")

    assert instance.start() is True
    assert second_started.wait(2)

    # Until the new scan completes, readers keep seeing the last complete
    # result scope rather than a partially populated dashboard.
    assert instance.status()["summary"]["total"] == 1
    assert instance.results()["total"] == 1

    release.set()
    instance.join(5)
    assert instance.status()["stage"] == "complete"
    assert instance.results()["total"] == 2


def test_missing_library_is_a_user_facing_scan_error(scanner_module, tmp_path):
    instance = scanner_module.LibraryScanner(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: None,
        validate_feedpak=lambda *_args: None,
        validator_version="test-v1",
        log=logging.getLogger("library-doctor-tests"),
    )

    with pytest.raises(ValueError, match="configured"):
        instance.start()


def test_rule_summary_filter_and_exports_use_the_visible_scope(scanner_module, tmp_path):
    def validate(_path, package):
        report = _report(package, status="warning")
        if package.startswith("formula"):
            report["title"] = "=CMD()"
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "formula.feedpak").write_bytes(b"one")
    (library / "normal.feedpak").write_bytes(b"two")
    _run(instance)

    rules = instance.rules()["items"]
    assert rules == [{
        "code": "test.warning",
        "severity": "warning",
        "category": "validation",
        "package_count": 2,
        "finding_count": 2,
    }]
    assert instance.results(rule_code="test.warning")["total"] == 2
    assert instance.results(rule_code="not.present")["total"] == 0

    json_name, json_type, json_content = instance.export(
        export_format="json", rule_code="test.warning",
    )
    csv_name, csv_type, csv_content = instance.export(export_format="csv")
    assert json_name.endswith(".json") and json_type.startswith("application/json")
    assert json.loads(json_content)["filters"]["rule"] == "test.warning"
    assert csv_name.endswith(".csv") and csv_type.startswith("text/csv")
    assert "'=CMD()" in csv_content
    assert "deep_audio_unsupported" in csv_content.splitlines()[0]

    stream_name, stream_type, stream = instance.export_stream(
        export_format="json", rule_code="test.warning",
    )
    streamed = json.loads("".join(stream))
    assert stream_name == json_name
    assert stream_type == json_type
    assert [item["package"] for item in streamed["packages"]] == [
        "formula.feedpak", "normal.feedpak",
    ]
    _csv_name, _csv_type, csv_stream = instance.export_stream(export_format="csv")
    streamed_csv = "".join(csv_stream)
    assert streamed_csv.startswith("\ufeffpackage,")
    assert "'=CMD()" in streamed_csv


def test_rule_summary_counts_structured_affected_occurrences(scanner_module, tmp_path):
    def validate(_path, package):
        report = _report(package, status="warning")
        report["findings"][0]["affected_count"] = 4
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "one.feedpak").write_bytes(b"one")
    _run(instance)

    assert instance.rules()["items"][0]["finding_count"] == 4


def test_rule_catalog_enriches_cached_results_and_rule_summary(scanner_module, tmp_path):
    def metadata(code, severity, category):
        return {
            "title": "Test warning",
            "area": "Test",
            "confidence": "high",
            "repairability": "manual",
            "guidance": "Review it.",
        }

    library = tmp_path / "library"
    library.mkdir()
    instance = scanner_module.LibraryScanner(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda _path, package: _report(package, status="warning"),
        validator_version="test-v1",
        log=logging.getLogger("library-doctor-tests"),
        rule_metadata=metadata,
    )
    (library / "one.feedpak").write_bytes(b"one")
    _run(instance)

    finding = instance.results()["items"][0]["findings"][0]
    rule = instance.rules()["items"][0]
    assert finding["rule"]["title"] == "Test warning"
    assert finding["evidence"] == {}
    assert rule["rule"]["guidance"] == "Review it."


def test_json_rule_export_contains_only_the_selected_findings(scanner_module, tmp_path):
    def validate(_path, package):
        report = _report(package, status="warning")
        report["findings"].append({
            **report["findings"][0],
            "code": "test.other",
            "message": "Another finding.",
        })
        report["counts"]["warning"] = 2
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "one.feedpak").write_bytes(b"one")
    _run(instance)

    _name, _type, content = instance.export(
        export_format="json", rule_code="test.warning"
    )
    package = json.loads(content)["packages"][0]
    assert [finding["code"] for finding in package["findings"]] == ["test.warning"]


def test_cache_signature_detects_same_size_same_timestamp_content_change(
    scanner_module, tmp_path
):
    calls = []

    def validate(_path, package):
        calls.append(package)
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    package = library / "one.feedpak"
    package.write_bytes(b"first")
    original = package.stat()
    _run(instance)

    package.write_bytes(b"other")
    os.utime(package, ns=(original.st_atime_ns, original.st_mtime_ns))
    status = _run(instance)

    assert status["scanned"] == 1
    assert calls == ["one.feedpak", "one.feedpak"]


def test_cache_signature_is_scoped_to_the_configured_library(
    scanner_module, tmp_path
):
    calls = []
    first_library = tmp_path / "first-library"
    second_library = tmp_path / "second-library"
    first_library.mkdir()
    second_library.mkdir()
    first_package = first_library / "one.feedpak"
    second_package = second_library / "one.feedpak"
    first_package.write_bytes(b"first")
    shutil.copy2(first_package, second_package)

    def validate(_path, package):
        calls.append(package)
        return _report(package)

    config = tmp_path / "config"
    first = scanner_module.LibraryScanner(
        config_dir=config,
        get_dlc_dir=lambda: first_library,
        validate_feedpak=validate,
        validator_version="test-v1",
        log=logging.getLogger("library-doctor-tests"),
    )
    _run(first)
    second = scanner_module.LibraryScanner(
        config_dir=config,
        get_dlc_dir=lambda: second_library,
        validate_feedpak=validate,
        validator_version="test-v1",
        log=logging.getLogger("library-doctor-tests"),
    )
    status = _run(second)

    assert status["scanned"] == 1
    assert calls == ["one.feedpak", "one.feedpak"]


def test_discovery_errors_make_the_scan_explicitly_incomplete(
    scanner_module, tmp_path
):
    instance, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, package: _report(package)
    )
    package = library / "one.feedpak"
    package.write_bytes(b"one")
    instance._discover_target = lambda *_args, **_kwargs: (
        [package], ["Could not read Locked: Access denied"]
    )

    status = _run(instance)

    assert status["stage"] == "incomplete"
    assert status["scope_complete"] is False
    assert status["summary"]["total"] == 1
    assert status["discovery_errors"] == ["Could not read Locked: Access denied"]
    assert status["last_scan"]["complete"] is False


def test_deep_audio_cache_profile_and_progress_estimate(scanner_module, tmp_path):
    calls = []

    def validate(_path, package, *, deep_audio=False):
        calls.append(deep_audio)
        return _report(package)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "one.feedpak").write_bytes(b"one")

    standard = _run(instance)
    deep = _run(instance, deep_audio=True)
    reused_deep = _run(instance)

    assert calls == [False, True]
    assert standard["packages_per_second"] > 0
    assert standard["eta_seconds"] == 0
    assert deep["deep_audio"] is True
    assert reused_deep["reused"] == 1


def test_current_deep_audio_repair_context_is_scope_and_scan_bound(
    scanner_module, tmp_path,
):
    def validate(_path, package, *, deep_audio=False):
        report = _report(package)
        report["features"]["deep_audio_checked"] = deep_audio
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    package = library / "one.feedpak"
    package.write_bytes(b"one")

    _run(instance, deep_audio=True)
    context = instance.current_deep_audio_repair_context("one.feedpak")

    assert context is not None
    assert context["report"]["features"]["deep_audio_checked"] is True
    assert instance.package_matches_signature(
        "one.feedpak", context["signature"]
    ) is True

    package.write_bytes(b"changed")
    assert instance.package_matches_signature(
        "one.feedpak", context["signature"]
    ) is False

    _run(instance, force=True, deep_audio=False)
    assert instance.current_deep_audio_repair_context("one.feedpak") is None


def test_repair_scope_excludes_scan_findings_that_are_not_automatic(
    scanner_module, tmp_path,
):
    def validate(_path, package):
        report = _report(package, status="warning")
        report["findings"] = [{
            "severity": "warning",
            "code": "chart.zero-length-handshape",
            "message": "Conditional handshape",
            "category": "validation",
            "affected_count": 1,
            "rule": {"title": "Zero-length handshape"},
        }]
        status = "automatic" if package.startswith("Safe") else "author_review"
        report["features"].update({
            "preview_source_available": False,
            "repair_eligibility": {
                "chart.zero-length-handshape": {
                    "status": status,
                    "reason_code": (
                        None if status == "automatic"
                        else "zero_length_handshape_requires_review"
                    ),
                },
                "media.preview-missing": {
                    "status": "unavailable",
                    "reason_code": "full_mix_unavailable",
                },
            },
        })
        return report

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "Safe.feedpak").write_bytes(b"safe")
    (library / "Review.feedpak").write_bytes(b"review")
    _run(instance)

    snapshot = instance.repair_scope_snapshot(
        ["chart.zero-length-handshape"],
        ["media.preview-missing"],
    )

    assert [item["package"] for item in snapshot["candidates"]] == [
        "Safe.feedpak"
    ]
    assert snapshot["candidates"][0]["rule_codes"] == [
        "chart.zero-length-handshape"
    ]
    assert snapshot["candidates"][0]["preview_rule_code"] is None
