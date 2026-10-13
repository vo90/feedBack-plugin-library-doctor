"""A detected tail is not permission to repair its package."""

import copy
import stat
import zipfile
from collections import Counter

import pytest

from test_scanner import _make_scanner, _report, _run, scanner_module as scanner_module
from test_terminal_beat_repair import RULE, build, modules as modules


def terminal_eligibility(report):
    return report["features"]["repair_eligibility"][RULE]


@pytest.mark.parametrize("archive", [False, True])
def test_clean_reference_is_explicitly_approved_by_scan(tmp_path, modules, archive):
    _, package, _ = build(tmp_path, modules, archive=archive)
    report = modules[1].validate_feedpak(package)
    item = terminal_eligibility(report)
    assert item["status"] == "automatic"
    assert item["assessment_version"]
    assert item["evidence_mode"]
    findings = [finding for finding in report["findings"] if finding["code"] == RULE]
    assert len(findings) == 3
    assert all(finding["rule"]["repairability"] == "safe_candidate" for finding in findings)


def test_all_affected_grids_use_explicit_exact_redundancy_proof(tmp_path, modules):
    def mutate(_manifest, documents):
        beats = documents["arrangements/bass.json"]["beats"]
        beats.extend(copy.deepcopy(beats[-2:] + beats[-2:-1]))

    _, package, _ = build(tmp_path, modules, mutate=mutate)
    report = modules[1].validate_feedpak(package)
    assert terminal_eligibility(report)["status"] == "automatic"
    assert terminal_eligibility(report)["evidence_mode"] == "shared_prefix_exact_redundancy_v1"
    findings = [finding for finding in report["findings"] if finding["code"] == RULE]
    assert len(findings) == 4
    assert all(finding["rule"]["repairability"] == "safe_candidate" for finding in findings)


@pytest.mark.parametrize("change", [
    "duplicate_json", "nonfinite_json", "overflow_json", "invalid_json",
    "duplicate_yaml", "jsonc", "missing_lyrics", "missing_rigs", "unknown_metadata",
])
def test_unverified_inputs_never_receive_scan_approval(tmp_path, modules, change):
    def mutate(manifest, documents):
        if change == "jsonc":
            manifest["arrangements"][0]["file"] = "arrangements/bass.jsonc"
            documents["arrangements/bass.jsonc"] = documents.pop("arrangements/bass.json")
        elif change == "missing_lyrics":
            manifest["lyrics"] = "lyrics.json"
        elif change == "missing_rigs":
            manifest["rigs"] = "rigs.json"
        elif change == "unknown_metadata":
            documents["arrangements/bass.json"]["future"] = {"position": 20}

    _, package, _ = build(tmp_path, modules, mutate=mutate)
    chart = package / "arrangements/bass.json"
    if change == "duplicate_json":
        chart.write_bytes(chart.read_bytes().replace(b'"notes":', b'"notes": [], "notes":', 1))
    elif change == "nonfinite_json":
        chart.write_bytes(chart.read_bytes().replace(b'"t": 2.0', b'"t": NaN', 1))
    elif change == "overflow_json":
        chart.write_bytes(chart.read_bytes().replace(b'"t": 2.0', b'"t": 1e999', 1))
    elif change == "invalid_json":
        chart.write_bytes(b"{")
    elif change == "duplicate_yaml":
        manifest_path = package / "manifest.yaml"
        manifest_path.write_bytes(manifest_path.read_bytes() + b"duration: 9.25\n")
    report = modules[1].validate_feedpak(package)
    assert terminal_eligibility(report)["status"] != "automatic"
    findings = [finding for finding in report["findings"] if finding["code"] == RULE]
    assert findings
    assert all(finding["rule"]["repairability"] == "manual" for finding in findings)


@pytest.mark.parametrize("change", ["duplicate", "case_collision", "unsafe", "symlink"])
def test_ambiguous_archive_inventory_blocks_scan_approval(tmp_path, modules, change):
    _, package, before = build(tmp_path, modules, archive=True)
    with zipfile.ZipFile(package, "a") as archive:
        if change == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("arrangements/bass.json", before["arrangements/bass.json"])
        elif change == "case_collision":
            archive.writestr("arrangements/BASS.json", before["arrangements/bass.json"])
        elif change == "unsafe":
            archive.writestr("../outside.json", b"{}")
        else:
            info = zipfile.ZipInfo("linked.json")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"arrangements/bass.json")
    report = modules[1].validate_feedpak(package)
    assert terminal_eligibility(report)["status"] == "unavailable"


def test_scan_reuses_json_inventory_without_additional_audio_reads(tmp_path, modules, monkeypatch):
    def mutate(manifest, documents):
        manifest["lyrics"] = "lyrics.json"
        documents["lyrics.json"] = [{"t": 1.0, "d": 1.0, "w": "hello"}]
        manifest["rigs"] = "rigs.json"
        documents["rigs.json"] = {}

    _, package, _ = build(tmp_path, modules, mutate=mutate)
    validator = modules[1]
    reads = Counter()
    original = validator._PackageReader.open_binary

    def count_reads(reader, path):
        reads[path] += 1
        return original(reader, path)

    assessed = []
    original_assess = validator._terminal.assess_repair

    def capture(manifest, documents, inventory):
        assessed.append(set(documents))
        assert "lyrics.json" in documents and "rigs.json" in documents
        before = dict(reads)
        result = original_assess(manifest, documents, inventory)
        assert dict(reads) == before
        return result

    monkeypatch.setattr(validator._PackageReader, "open_binary", count_reads)
    monkeypatch.setattr(validator._terminal, "assess_repair", capture)
    monkeypatch.setattr(
        validator._terminal, "package_inventory",
        lambda _package: pytest.fail("Scan must reuse its bounded inventory"),
    )
    validator.validate_feedpak(package)
    assert len(assessed) == 1
    assert all(reads[path] == 1 for path in assessed[0])


def test_oversized_input_keeps_terminal_diagnosis_but_blocks_approval(tmp_path, modules, monkeypatch):
    _, package, before = build(tmp_path, modules)
    chart = package / "arrangements/bass.json"
    chart.write_bytes(before["arrangements/bass.json"] + b" " * 4000)
    monkeypatch.setattr(modules[1], "MAX_TEXT_BYTES", 2000)
    report = modules[1].validate_feedpak(package)
    assert terminal_eligibility(report)["status"] == "unavailable"
    assert any(finding["code"] == RULE for finding in report["findings"])


@pytest.mark.parametrize("eligibility", [
    None, [], "automatic", {}, {"status": None}, {"status": []},
    {"status": "future"}, {"status": "author_review", "message": "Needs a clean reference."},
    {"status": "unavailable"},
])
@pytest.mark.parametrize("independent_safe", [False, True])
def test_batch_requires_explicit_terminal_approval_without_hiding_other_fixes(
    scanner_module, tmp_path, eligibility, independent_safe,
):
    def validate(_path, package):
        report = _report(package, status="warning")
        report["findings"] = [{"code": RULE, "severity": "warning"}]
        if independent_safe:
            report["findings"].append({"code": "test.safe", "severity": "warning"})
        report["features"]["repair_eligibility"] = {RULE: eligibility}
        return report

    scanner, library = _make_scanner(scanner_module, tmp_path, validate)
    (library / "Fixture.feedpak").write_bytes(b"fixture")
    _run(scanner)
    snapshot = scanner.repair_scope_snapshot([RULE, "test.safe"])
    assert len(snapshot["candidates"]) == int(independent_safe)
    if independent_safe:
        assert snapshot["candidates"][0]["rule_codes"] == ["test.safe"]
    assert len(snapshot["excluded_operations"]) == 1
    assert snapshot["excluded_operations"][0]["rule_code"] == RULE
    assert snapshot["excluded_operations"][0]["message"]


@pytest.mark.parametrize("cached_version", ["test-v1", "rules-38:old"])
@pytest.mark.parametrize("eligibility", [None, [], {"status": []}, {"status": "automatic"}])
def test_cached_terminal_approval_needs_current_explicit_evidence(
    scanner_module, tmp_path, modules, cached_version, eligibility,
):
    scanner, _ = _make_scanner(scanner_module, tmp_path, lambda *_args: None,
                               rule_metadata=modules[1].rule_metadata)
    report = _report("Fixture.feedpak", status="warning")
    report["validator_version"] = cached_version
    report["findings"] = [{"code": RULE, "severity": "warning"}]
    report["features"]["repair_eligibility"] = {RULE: eligibility}
    scanner._enrich_report(report)
    approved = cached_version == "test-v1" and eligibility == {"status": "automatic"}
    assert report["findings"][0]["rule"]["repairability"] == (
        "safe_candidate" if approved else "manual"
    )


def test_terminal_review_message_is_not_replaced_by_hopo_player_review(
    scanner_module, tmp_path, modules,
):
    scanner, _ = _make_scanner(scanner_module, tmp_path, lambda *_args: None,
                               rule_metadata=modules[1].rule_metadata)
    report = _report("Fixture.feedpak", status="warning")
    report["findings"] = [{"code": RULE, "severity": "warning"}]
    report["features"]["repair_eligibility"] = {RULE: {
        "status": "author_review", "message": "No clean timeline corroborates this ending."
    }}
    scanner._enrich_report(report)
    rule = report["findings"][0]["rule"]
    assert rule["repairability"] == "manual"
    assert rule["guidance"] == "No clean timeline corroborates this ending."


def test_validator_version_invalidates_pre_fix_cached_scan_approvals(modules):
    assert modules[1].VALIDATOR_VERSION.startswith("rules-41:")
    assert modules[1].rule_metadata(RULE)["repairability"] == "manual"


def test_scan_eligibility_helpers_are_read_only_and_keep_unrelated_rules(scanner_module):
    helper = scanner_module._eligibility
    report = {"package": "Fixture.feedpak", "features": {"repair_eligibility": {
        RULE: {"status": "author_review", "message": "Review this ending."}
    }}}
    original = copy.deepcopy(report)
    excluded = helper.excluded_terminal_operation(report, [RULE], current=True)
    assert excluded["message"] == "Review this ending."
    assert helper.excluded_terminal_operation(report, ["test.safe"], current=True) is None
    assert helper.scan_operation_is_automatic("test.safe", None, current=False)
    assert not helper.scan_operation_is_automatic(RULE, {"status": "automatic"}, current=False)
    assert helper.scan_repair_presentation("test.safe", None, {}) == {}
    assert helper.scan_repair_presentation("test.safe", {"status": "future"}, {}) == {}
    assert report == original


def test_current_terminal_approval_is_not_listed_as_excluded(scanner_module):
    helper = scanner_module._eligibility
    report = {"features": {"repair_eligibility": {RULE: {"status": "automatic"}}}}
    assert helper.excluded_terminal_operation(report, [RULE], current=True) is None
    assert helper.excluded_terminal_operation(report, [RULE], current=False)["status"] == "unavailable"
