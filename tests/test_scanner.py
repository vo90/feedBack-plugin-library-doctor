import importlib.util
import logging
import sys
import threading
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def scanner_module():
    path = Path(__file__).parents[1] / "scanner.py"
    name = "library_health_scanner_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _report(package, *, status="healthy", lyrics=False, preview=False):
    errors = int(status == "error")
    warnings = int(status == "warning")
    findings = []
    if errors or warnings:
        severity = "error" if errors else "warning"
        findings.append({
            "severity": severity,
            "code": f"test.{severity}",
            "message": f"A {severity} for testing.",
            "location": "",
            "arrangement_id": None,
            "time": None,
            "string": None,
        })
    return {
        "schema": "library_health.package.v1",
        "validator_version": "test-v1",
        "package": package,
        "title": Path(package).stem,
        "artist": "Test Artist",
        "status": status,
        "counts": {"error": errors, "warning": warnings, "info": 0},
        "features": {
            "lyrics_declared": lyrics,
            "lyrics_entries": int(lyrics),
            "preview_declared": preview,
            "preview_available": preview,
        },
        "findings": findings,
    }


def _make_scanner(scanner_module, tmp_path, validator):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    instance = scanner_module.LibraryScanner(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validator,
        validator_version="test-v1",
        log=logging.getLogger("library-health-tests"),
    )
    return instance, library


def _run(instance, *, force=False, **target):
    assert instance.start(force=force, **target) is True
    instance.join(5)
    status = instance.status()
    assert status["running"] is False
    return status


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
    assert {item[1] for item in seen} == {"Artist/one.feedpak", "two.sloppak"}


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
        return _report(package, lyrics=False, preview=False)

    instance, library = _make_scanner(scanner_module, tmp_path, validate)
    for name in ("error.feedpak", "warn.feedpak", "clean.feedpak"):
        (library / name).write_bytes(name.encode())
    _run(instance)

    assert instance.results(result_filter="problems")["total"] == 2
    assert instance.results(result_filter="errors")["total"] == 1
    assert instance.results(result_filter="warnings")["total"] == 1
    assert instance.results(result_filter="healthy")["total"] == 1
    assert instance.results(result_filter="no_lyrics")["total"] == 2
    assert instance.results(result_filter="no_preview")["total"] == 1
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
    database = tmp_path / "config" / "library_health" / "library_health.db"
    cache = scanner_module._ReportCache(database)
    cache.put("legacy.feedpak", "f:1:1", "test-v1", _report("legacy.feedpak"), 1.0)
    with cache._conn:
        cache._conn.execute("DROP TABLE current_scope")
        cache._conn.execute("DROP TABLE cache_state")
    cache._conn.close()

    migrated = scanner_module._ReportCache(database)

    assert migrated.summary()["total"] == 1
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
        log=logging.getLogger("library-health-tests"),
    )

    with pytest.raises(ValueError, match="configured"):
        instance.start()
