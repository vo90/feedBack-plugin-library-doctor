"""Normal scan reuse stays bound to current scope, signature, and transaction checks."""
import copy
from pathlib import Path

import pytest

from test_scanner import _make_scanner, _report, _run, scanner_module as scanner_module
from test_source_recovery import build, read_all


def cached_report(service, package):
    return {"schema": "library_doctor.package.v1", "package": package.name,
            "validator_version": service._validator_version,
            "features": {"deep_audio_checked": False}, "findings": [],
            "counts": {"error": 0, "warning": 0, "info": 0}, "status": "healthy"}


def prepared(tmp_path, *, archive=True):
    module, service, engine, package, source, members, _ = build(tmp_path, archive=archive)
    plan = engine._plan(package, package.name, source)
    assert plan["available"]
    report = cached_report(service, package)
    calls = []
    original = service._validate_feedpak
    def validate(path, name, **options):
        calls.append((Path(path), options))
        return original(path, name, **options)
    service._validate_feedpak = validate
    return module, service, engine, package, source, members, plan, report, calls


def test_normal_source_report_skips_only_before_validation_and_keeps_crc_backup_undo(tmp_path):
    _, service, engine, package, source, members, plan, report, calls = prepared(tmp_path)
    original = copy.deepcopy(report)
    integrity, guards = [], []
    verify = service._verify_archive_candidate
    def checked(*args):
        integrity.append(args)
        return verify(*args)
    service._verify_archive_candidate = checked
    def guard():
        guards.append(True)
        return True
    receipt = engine.apply(package.name, source, plan["plan_id"], verified_before_report=report,
                           source_guard=guard)
    assert receipt["verified_scan_report_reused"] is True
    assert receipt["deep_audio_reused"] is False
    assert len(calls) == 1 and calls[0][0] != package
    assert calls[0][1]["deep_audio"] is False
    assert len(integrity) == 1 and len(guards) >= 3
    assert receipt["backup_id"] and receipt["undo_available"]
    assert report == original
    assert read_all(package)["audio.ogg"] == members["audio.ogg"]
    service.restore(package.name, receipt["backup_id"])
    assert read_all(package) == members


@pytest.mark.parametrize("mutate", [
    lambda report: report.update(validator_version="old"),
    lambda report: report.update(package="Different.feedpak"),
    lambda report: report.pop("schema"),
    lambda report: report.update(features={}),
    lambda report: report.update(findings=[{"code": "new", "severity": "warning"}]),
    lambda report: report.update(findings=[{"code": "invalid", "severity": []}]),
    lambda report: report.update(counts={"error": False, "warning": 0, "info": 0}),
])
def test_unusable_normal_report_falls_back_to_both_validations(tmp_path, mutate):
    _, service, engine, package, source, _, plan, report, calls = prepared(tmp_path)
    mutate(report)
    receipt = engine.apply(package.name, source, plan["plan_id"], verified_before_report=report,
                           source_guard=lambda: True)
    assert not receipt["verified_scan_report_reused"]
    assert len(calls) == 2 and calls[0][0] == package


def test_normal_report_requires_callable_live_guard(tmp_path):
    _, _, engine, package, source, _, plan, report, calls = prepared(tmp_path)
    receipt = engine.apply(package.name, source, plan["plan_id"], verified_before_report=report)
    assert not receipt["verified_scan_report_reused"] and len(calls) == 2


@pytest.mark.parametrize("fail_call", [1, 2, 3])
def test_changed_signature_before_validation_or_commit_prevents_write(tmp_path, fail_call):
    module, _, engine, package, source, members, plan, report, calls = prepared(tmp_path)
    guards = []
    def guard():
        guards.append(True)
        return len(guards) != fail_call
    with pytest.raises(module.RepairPlanningError, match="changed"):
        engine.apply(package.name, source, plan["plan_id"], verified_before_report=report,
                     source_guard=guard)
    assert read_all(package) == members
    assert len(calls) == (0 if fail_call == 1 else 1)


def test_new_after_validation_finding_still_blocks_cached_before_repair(tmp_path):
    module, service, engine, package, source, members, plan, report, _ = prepared(tmp_path)
    service._validate_feedpak = lambda *_args, **_kwargs: {
        **report, "findings": [{"code": "chart.new-error", "severity": "error"}],
        "counts": {"error": 1, "warning": 0, "info": 0}}
    with pytest.raises(module.RepairPlanningError):
        engine.apply(package.name, source, plan["plan_id"], verified_before_report=report,
                     source_guard=lambda: True)
    assert read_all(package) == members


@pytest.mark.parametrize("scenario", ["directory", "deep_audio", "other_rule"])
def test_normal_reuse_does_not_expand_other_repair_paths(tmp_path, scenario):
    _, service, engine, package, source, _, plan, report, calls = prepared(tmp_path, archive=scenario != "directory")
    if scenario == "other_rule":
        plan["rule_code"] = "chart.duplicate-note"
        receipt = service._apply_internal(package, package.name, plan, deep_audio=False,
            verified_before_report=report, source_guard=lambda: True)
    else:
        receipt = engine.apply(package.name, source, plan["plan_id"],
            deep_audio=scenario == "deep_audio", verified_before_report=report, source_guard=lambda: True)
    assert not receipt["verified_scan_report_reused"] and len(calls) == 2


def source_scanner(scanner_module, tmp_path):
    def validator(_path, name, *, deep_audio=False):
        report = _report(name)
        report["features"]["deep_audio_checked"] = deep_audio
        return report
    scanner, library = _make_scanner(scanner_module, tmp_path, validator)
    package = library / "Song.feedpak"
    package.write_bytes(b"a complete scanned package")
    return scanner, library, package


@pytest.mark.parametrize("deep_audio", [False, True])
def test_source_cache_getter_reads_exact_current_scan_flavor(scanner_module, tmp_path, deep_audio):
    scanner, _, _ = source_scanner(scanner_module, tmp_path)
    _run(scanner, deep_audio=deep_audio)
    row, = scanner.source_recovery_scope_snapshot()["candidates"]
    report = scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"])
    assert report["features"]["deep_audio_checked"] is deep_audio
    assert report["package"] == row["package"]
    assert scanner.source_recovery_report_for_signature(row["package"], "stale") is None
    assert scanner.source_recovery_report_for_signature(row["package"], "") is None
    assert scanner.begin_batch_operation()[0]
    assert scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"])
    scanner.finish_repair()


def test_normal_scan_can_reuse_existing_deep_flavor_without_changing_deep_api(scanner_module, tmp_path):
    scanner, _, _ = source_scanner(scanner_module, tmp_path)
    _run(scanner, deep_audio=True)
    row, = scanner.source_recovery_scope_snapshot()["candidates"]
    deep_before = scanner.deep_audio_report_for_signature(row["package"], row["scan_signature"])
    _run(scanner, deep_audio=False)
    reused = scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"])
    assert reused == deep_before
    assert scanner.deep_audio_report_for_signature(row["package"], row["scan_signature"]) == deep_before


def test_source_cache_getter_rejects_outside_scope_old_version_and_busy_scan(scanner_module, tmp_path):
    scanner, library, package = source_scanner(scanner_module, tmp_path)
    other = library / "Other.feedpak"
    other.write_bytes(b"another package")
    _run(scanner)
    row = next(row for row in scanner.source_recovery_scope_snapshot()["candidates"] if row["package"] == other.name)
    _run(scanner, target_kind="file", selected_path=str(package))
    assert scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"]) is None
    selected, = scanner.source_recovery_scope_snapshot()["candidates"]
    scanner._status["running"] = True
    assert scanner.source_recovery_report_for_signature(selected["package"], selected["scan_signature"]) is None
    scanner._status["running"] = False
    scanner._validator_version = "future"
    assert scanner.source_recovery_report_for_signature(selected["package"], selected["scan_signature"]) is None


def test_source_cache_getter_rejects_incomplete_scan_and_mismatched_report_flavor(scanner_module, tmp_path):
    scanner, _, _ = source_scanner(scanner_module, tmp_path)
    _run(scanner)
    row, = scanner.source_recovery_scope_snapshot()["candidates"]
    scan = scanner._cache.last_scan()
    scanner._cache.record_scan({**scan, "complete": False})
    assert scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"]) is None
    scanner._cache.record_scan(scan)
    report = scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"])
    report["features"]["deep_audio_checked"] = True
    scanner._cache.put(row["package"], row["scan_signature"], "test-v1:standard", report, 123)
    assert scanner.source_recovery_report_for_signature(row["package"], row["scan_signature"]) is None
