"""The all-affected proof must delete only demonstrably redundant suffixes."""

import copy
import json
import logging
from pathlib import Path

import pytest

from test_batch_repair import _load_batch_module
from test_scanner import _run, scanner_module as scanner_module
from test_terminal_beat_repair import RULE, build, modules as modules, read


MODE = "shared_prefix_exact_redundancy_v1"


def all_affected(_manifest, documents):
    beats = documents["arrangements/bass.json"]["beats"]
    beats.extend(copy.deepcopy(beats[-2:] + beats[-2:-1]))


def exact_fixture(tmp_path, modules, *, archive=False, mutate=None):
    def changes(manifest, documents):
        all_affected(manifest, documents)
        if mutate:
            mutate(manifest, documents)
    return build(tmp_path, modules, archive=archive, mutate=changes)


def scan_for_batch(scanner_module, tmp_path, package, validator):
    scanner = scanner_module.LibraryScanner(
        config_dir=tmp_path / "scan-config", get_dlc_dir=lambda: package.parent,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        rule_metadata=validator.rule_metadata,
        log=logging.getLogger("terminal-exact-scan"),
    )
    _run(scanner)
    return scanner, scanner.repair_scope_snapshot([RULE])


@pytest.mark.parametrize("archive", [False, True])
def test_all_affected_scan_single_combined_batch_and_undo_agree(
    tmp_path, modules, scanner_module, archive,
):
    service, package, before = exact_fixture(tmp_path, modules, archive=archive)
    report = modules[1].validate_feedpak(package)
    assessment = report["features"]["repair_eligibility"][RULE]
    assert assessment["status"] == "automatic", assessment
    assert assessment["evidence_mode"] == MODE
    assert assessment["removed_count"] == 8
    assert len(assessment["members"]) == 4
    assert {member["retained_count"] for member in assessment["members"]} == {20}
    assert len({member["retained_grid_sha256"] for member in assessment["members"]}) == 1

    single = service.preview(package.name, RULE)
    combined = service.preview_selected(package.name, [RULE])
    for preview in (single, combined):
        assert preview["available"], preview["blockers"]
        assert preview["removed_count"] == 8
        assert preview["member_count"] == 4
        assert preview["terminal_assessment"] == assessment

    scanner, snapshot = scan_for_batch(scanner_module, tmp_path, package, modules[1])
    assert len(snapshot["candidates"]) == 1
    assert snapshot["candidates"][0]["rule_codes"] == [RULE]
    assert snapshot["excluded_operations"] == []
    _name, batch_module = _load_batch_module()
    manager = batch_module.BatchRepairManager(
        config_dir=tmp_path / "batch-config", scanner=scanner,
        repair_service=service, repair_error_type=modules[0].RepairPlanningError,
        log=logging.getLogger("terminal-exact-batch"), prepare_executor_factory=None,
    )
    manager.start_preview(snapshot)
    manager.join(5)
    preview = manager.status()["preview"]
    assert preview["eligible_count"] == 1
    assert all(read(package, path) == raw for path, raw in before.items())
    manager.start_apply(preview["batch_plan_id"])
    manager.join(10)
    result = manager.status()["result"]
    assert result["successful_count"] == 1, result
    assert result["failed_count"] == 0
    assert result["removed_count"] == 8
    for path, original in before.items():
        changed = read(package, path)
        if path.startswith("arrangements/"):
            expected = json.loads(original)
            expected["beats"] = expected["beats"][:20]
            assert json.loads(changed) == expected
        else:
            assert changed == original
    assert modules[1].validate_feedpak(package)["status"] == "healthy"
    assert not service.preview(package.name, RULE)["available"]
    backup_id = result["outcomes"][0]["backup_id"]
    assert service.restore(package.name, backup_id)["outcome"] == "restored"
    assert all(read(package, path) == raw for path, raw in before.items())


REJECTIONS = [
    pytest.param(lambda m, d: d["arrangements/bass.json"]["beats"][0].update(time=0.01), id="prefix-time"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["beats"][1].update(measure=7), id="prefix-measure"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["beats"][-1].update(time=9.0000001), id="approximate-tail"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["beats"][-1].update(time=8.9), id="unique-tail"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["beats"].extend(copy.deepcopy(d["arrangements/bass.json"]["beats"][-2:])), id="long-tail"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["beats"][5].update(time=0.5), id="interior-regression"),
    pytest.param(lambda m, d: m.update(duration=8.9), id="duration-before-bracket"),
    pytest.param(lambda m, d: m.update(duration=10), id="duration-after-bracket"),
    pytest.param(lambda m, d: m.update(duration=0), id="zero-duration"),
    pytest.param(lambda m, d: m.update(duration=True), id="boolean-duration"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["notes"][0].update(t=9), id="event-at-boundary"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["notes"][0].update(sus=7), id="sustain-at-boundary"),
    pytest.param(lambda m, d: d["arrangements/bass.json"]["notes"][0].update(bnv=[{"t": 7, "v": 1}]), id="bend-at-boundary"),
    pytest.param(lambda m, d: d["arrangements/bass.json"].update(phrases=[{"start_time": 0, "end_time": 9.25, "levels": [{"difficulty": 1, "notes": [{"t": 8, "s": 0, "f": 3, "sus": 1}]}]}]), id="nested-difficulty-boundary"),
    pytest.param(lambda m, d: d["arrangements/bass.json"].update(beatIndex=20), id="beat-index-reference"),
    pytest.param(lambda m, d: d["arrangements/bass.json"].update(future={"index": 20}), id="unknown-extension"),
    pytest.param(lambda m, d: d["arrangements/bass.json"].update(stats={"events": {"index": 20}}), id="untyped-statistics"),
    pytest.param(lambda m, d: m.update(title={"index": 20}), id="untyped-manifest-metadata"),
    pytest.param(lambda m, d: d.update({"unknown.json": {"index": 20}}), id="undeclared-sidecar"),
    pytest.param(lambda m, d: d.pop("arrangements/bass.json"), id="missing-input"),
]


@pytest.mark.parametrize("mutate", REJECTIONS)
def test_unsafe_lookalikes_are_visible_but_never_offered_or_batched(
    tmp_path, modules, scanner_module, mutate,
):
    service, package, before = exact_fixture(tmp_path, modules, mutate=mutate)
    report = modules[1].validate_feedpak(package)
    assert report["features"]["repair_eligibility"][RULE]["status"] != "automatic"
    assert any(finding["code"] == RULE for finding in report["findings"])
    assert not service.preview(package.name, RULE)["available"]
    assert not service.preview_selected(package.name, [RULE])["available"]
    _scanner, snapshot = scan_for_batch(scanner_module, tmp_path, package, modules[1])
    assert snapshot["candidates"] == []
    assert len(snapshot["excluded_operations"]) == 1
    assert all(read(package, path) == raw for path, raw in before.items())


def with_sidecars(manifest, documents):
    manifest["lyrics"] = "lyrics.json"
    documents["lyrics.json"] = [{"t": 1, "d": 1, "w": "hello"}]
    manifest["rigs"] = "rigs.json"
    documents["rigs.json"] = {"version": 1, "rigs": []}


@pytest.mark.parametrize("path", ["manifest.yaml", "lyrics.json", "rigs.json", "arrangements/bass.json"])
@pytest.mark.parametrize("prepared", [False, True])
def test_all_proof_inputs_are_bound_to_preview_and_prepared_commit(
    tmp_path, modules, path, prepared,
):
    service, package, before = exact_fixture(tmp_path, modules, mutate=with_sidecars)
    if prepared:
        token = service.prepare_selected(package.name, [RULE])
    else:
        token = service.preview(package.name, RULE)
        assert token["available"], token["blockers"]
        assert path in token["terminal_evidence"]
    (package / path).write_bytes(before[path] + b" \n")
    with pytest.raises(modules[0].RepairPlanningError):
        if prepared:
            service.commit_prepared(token)
        else:
            service.apply(package.name, RULE, token["plan_id"])
    assert all(read(package, member) == raw for member, raw in before.items() if member != path)
    assert service.history()["items"] == []


def test_late_lyric_cannot_be_hidden_by_shared_prefixes(tmp_path, modules):
    def mutate(manifest, documents):
        with_sidecars(manifest, documents)
        documents["lyrics.json"][0].update(t=8, d=1)
    service, package, _ = exact_fixture(tmp_path, modules, mutate=mutate)
    assert modules[1].validate_feedpak(package)["features"]["repair_eligibility"][RULE]["status"] != "automatic"
    assert not service.preview(package.name, RULE)["available"]


def test_empty_declared_grid_is_unchanged_and_does_not_supply_proof(tmp_path, modules):
    def mutate(manifest, documents):
        manifest["arrangements"].append({"id": "empty", "file": "arrangements/empty.json"})
        documents["arrangements/empty.json"] = {"notes": [], "chords": [], "beats": [], "sections": []}
    service, package, before = exact_fixture(tmp_path, modules, mutate=mutate)
    preview = service.preview(package.name, RULE)
    assert preview["available"], preview["blockers"]
    assert preview["member_count"] == 4
    assert preview["removed_count"] == 8
    service.apply(package.name, RULE, preview["plan_id"])
    assert read(package, "arrangements/empty.json") == before["arrangements/empty.json"]


def test_one_distinct_grid_cannot_manufacture_corroboration(tmp_path, modules):
    def mutate(manifest, documents):
        for entry in manifest["arrangements"]:
            entry["file"] = "arrangements/bass.json"
        for path in list(documents):
            if path != "arrangements/bass.json":
                del documents[path]
    service, package, _ = exact_fixture(tmp_path, modules, mutate=mutate)
    assert modules[1].validate_feedpak(package)["features"]["repair_eligibility"][RULE]["status"] != "automatic"
    assert not service.preview(package.name, RULE)["available"]


def test_silent_phrase_windows_within_retained_coverage_are_preserved(tmp_path, modules):
    def mutate(_manifest, documents):
        for document in documents.values():
            document["phrases"] = [{"start_time": 0, "end_time": 9.25}]
    service, package, before = exact_fixture(tmp_path, modules, mutate=mutate)
    preview = service.preview(package.name, RULE)
    assert preview["available"], preview["blockers"]
    service.apply(package.name, RULE, preview["plan_id"])
    for path in before:
        if path.startswith("arrangements/"):
            assert json.loads(read(package, path))["phrases"] == json.loads(before[path])["phrases"]


def test_empty_grids_do_not_count_toward_minimum_distinct_proof(tmp_path, modules):
    def mutate(_manifest, documents):
        for path, document in documents.items():
            if path != "arrangements/bass.json":
                document["beats"] = []
    service, package, _ = exact_fixture(tmp_path, modules, mutate=mutate)
    assert modules[1].validate_feedpak(package)["features"]["repair_eligibility"][RULE]["status"] != "automatic"
    assert not service.preview(package.name, RULE)["available"]


def test_missing_grid_is_not_treated_as_an_explicit_empty_grid(tmp_path, modules):
    def mutate(_manifest, documents):
        del documents["arrangements/bass.json"]["beats"]
    service, package, _ = exact_fixture(tmp_path, modules, mutate=mutate)
    assert not service.preview(package.name, RULE)["available"]


def test_all_affected_song_timeline_joins_same_exact_proof(tmp_path, modules):
    def mutate(manifest, documents):
        manifest["song_timeline"] = "song_timeline.json"
        beats = copy.deepcopy(documents["arrangements/bass.json"]["beats"][:20])
        documents["song_timeline.json"] = {"version": 1, "beats": beats + beats[-2:], "sections": []}
    service, package, before = exact_fixture(tmp_path, modules, mutate=mutate)
    preview = service.preview(package.name, RULE)
    assert preview["available"], preview["blockers"]
    assert preview["removed_count"] == 10
    assert preview["member_count"] == 5
    result = service.apply(package.name, RULE, preview["plan_id"])
    assert len(json.loads(read(package, "song_timeline.json"))["beats"]) == 20
    assert modules[1].validate_feedpak(package)["status"] == "healthy"
    service.restore(package.name, result["backup_id"])
    assert all(read(package, path) == raw for path, raw in before.items())


def test_exact_mode_candidate_validation_failure_preserves_every_original_member(tmp_path, modules):
    service, package, before = exact_fixture(tmp_path, modules)
    preview = service.preview(package.name, RULE)
    validate = service._validate_feedpak

    def reject(path, *args, **kwargs):
        report = validate(path, *args, **kwargs)
        if Path(path) != package:
            report["findings"].append({"code": "synthetic.failure", "severity": "error"})
        return report

    service._validate_feedpak = reject
    with pytest.raises(modules[0].RepairPlanningError) as error:
        service.apply(package.name, RULE, preview["plan_id"])
    assert error.value.code == "verification_failed"
    assert all(read(package, path) == raw for path, raw in before.items())


def test_exact_mode_partial_directory_commit_rolls_back_all_four_grids(tmp_path, modules):
    service, package, before = exact_fixture(tmp_path, modules)
    preview = service.preview(package.name, RULE)

    def barrier(stage, context):
        if stage == "before_member_replace" and context["member_index"] == 2:
            raise OSError("simulated exact-mode write failure")

    service._transaction_barrier = barrier
    with pytest.raises(modules[0].RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert all(read(package, path) == raw for path, raw in before.items())


def test_exact_mode_refuses_a_new_unknown_member_before_commit(tmp_path, modules):
    service, package, before = exact_fixture(tmp_path, modules)
    preview = service.preview(package.name, RULE)

    def barrier(stage, _context):
        if stage == "before_member_replace":
            (package / "new-reference.json").write_bytes(b'{"index":20}')

    service._transaction_barrier = barrier
    with pytest.raises(modules[0].RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert all(read(package, path) == raw for path, raw in before.items())
