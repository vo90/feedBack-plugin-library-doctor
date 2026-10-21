"""Automatic hand-shape repair through real findings and guarded transactions."""

import copy
import json
import logging
import sys
import zipfile
from importlib import util
from pathlib import Path

import pytest


RULE = "chart.stale-handshape-reference"
REVIEW_RULE = "chart.ambiguous-handshape-reference"
ROOT = Path(__file__).parents[1]


def load(name, filename):
    spec = util.spec_from_file_location(name, ROOT / filename)
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repair():
    return load("library_doctor_handshape_reference_service_tests", "repair.py")


@pytest.fixture(scope="module")
def validator():
    return load("library_doctor_handshape_reference_validator_tests", "validator.py")


def raw(document):
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def chart(*, ambiguous_only=False):
    """A stale previous-chord reference and an independent ambiguous guide."""
    container = {
        "notes": [{"t": 9, "s": 0, "f": 5, "pm": True, "sus": .25}],
        "chords": [
            {"t": 1, "id": 0, "notes": [{"s": 2, "f": 10}, {"s": 3, "f": 12}]},
            {"t": 2, "id": 1, "notes": [{"s": 3, "f": 7}, {"s": 4, "f": 7}]},
            {"t": 4, "id": 0, "notes": [{"s": 2, "f": 10}, {"s": 3, "f": 12}]},
        ],
        "anchors": [{"t": 0, "fret": 10, "width": 4}, {"t": 2, "fret": 7, "width": 4}],
        "handshapes": [
            {"start_time": 2, "end_time": 2.5, "chord_id": 0},
            {"start_time": 4, "end_time": 4.5, "chord_id": 1, "author_hint": "retain"},
        ],
    }
    if ambiguous_only:
        container["handshapes"].pop(0)
    return {
        "version": 1,
        "templates": [
            {"name": "old", "frets": [-1, -1, 10, 12, -1, -1], "fingers": [-1, -1, 1, 3, -1, -1]},
            {"name": "new", "frets": [-1, -1, -1, 7, 7, -1], "fingers": [-1, -1, -1, 1, 1, -1]},
        ],
        **copy.deepcopy(container),
        "phrases": [{
            "start_time": 0, "end_time": 10,
            "levels": [
                {"difficulty": 0, **copy.deepcopy(container)},
                {"difficulty": 100, **copy.deepcopy(container)},
            ],
        }],
        "ext": {"future_metadata": ["preserve", "å", 42]},
    }


def repaired_chart(document):
    expected = copy.deepcopy(document)
    for container in [expected, *expected["phrases"][0]["levels"]]:
        for handshape in container["handshapes"]:
            if handshape["start_time"] == 2:
                handshape["chord_id"] = 1
    return expected


def plan(repair, document):
    return repair.plan_json_member(
        raw(document), member_path="arrangements/lead.json", source_kind="arrangement",
        validator_version="test", rule_code=RULE,
    )


def report_for(validator, documents):
    findings, eligibility = validator._Findings(), {}
    for member_path, document in documents.items():
        validator._validate_arrangement_semantics(
            document, member_path, Path(member_path).stem, 60, findings,
            repair_eligibility=eligibility,
        )
    return {
        "validator_version": "test",
        "findings": [finding.to_dict() for finding in findings.items],
        "counts": findings.counts,
        "features": {"repair_eligibility": eligibility},
    }


def read_member(package, member_path):
    if Path(package).is_dir():
        return (Path(package) / member_path).read_bytes()
    with zipfile.ZipFile(package) as source:
        return source.read(member_path)


def write_package(package, members, archive):
    if archive:
        with zipfile.ZipFile(package, "w") as target:
            for name, data in members.items():
                target.writestr(name, data)
    else:
        for name, data in members.items():
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def package_service(repair, validator, tmp_path, archive):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    documents = {
        "arrangements/lead.json": chart(),
        "arrangements/rhythm.json": chart(ambiguous_only=True),
    }
    members = {
        "manifest.yaml": b"arrangements:\n  - id: lead\n    file: arrangements/lead.json\n  - id: rhythm\n    file: arrangements/rhythm.json\n",
        **{name: raw(document) for name, document in documents.items()},
        "stems/full.bin": b"unchanged audio\x00\xff",
        "cover.bin": b"unchanged artwork\x00\xfe",
        "song_timeline.json": raw({"beats": [{"t": 0, "m": 1}, {"t": 1}]}),
    }
    write_package(package, members, archive)

    def validate(path, _name, **_kwargs):
        return report_for(validator, {
            name: json.loads(read_member(path, name)) for name in documents
        })

    service = repair.RepairService(
        config_dir=tmp_path / "config", get_dlc_dir=lambda: library,
        validate_feedpak=validate, validator_version="test", log=logging.getLogger("test"),
    )
    return package, members, documents, service


def test_plan_changes_only_proven_references_in_every_difficulty(repair):
    document = chart()
    before = copy.deepcopy(document)
    preview = plan(repair, document)
    assert preview == plan(repair, document)
    fixed = json.loads(repair.apply_json_member(raw(document), preview))
    assert fixed == repaired_chart(document)
    assert document == before
    action = preview["actions"][0]
    assert action["change_count"] == 3
    assert action["removed_count"] == 0
    assert not plan(repair, fixed)["actions"]


def test_real_validator_separates_safe_and_ambiguous_findings(repair, validator):
    document = chart()
    before = report_for(validator, {"lead.json": document})
    codes = {finding["code"] for finding in before["findings"]}
    assert {RULE, REVIEW_RULE} <= codes
    assert before["features"]["repair_eligibility"][RULE]["status"] == "automatic"
    fixed = json.loads(repair.apply_json_member(raw(document), plan(repair, document)))
    after = report_for(validator, {"lead.json": fixed})
    assert RULE not in {finding["code"] for finding in after["findings"]}
    assert [finding for finding in after["findings"] if finding["code"] == REVIEW_RULE] == [
        finding for finding in before["findings"] if finding["code"] == REVIEW_RULE
    ]


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("mode", ["single", "all", "selected"])
def test_mixed_transaction_and_undo_preserve_notes_levels_and_media(
    repair, validator, tmp_path, archive, mode,
):
    package, members, documents, service = package_service(repair, validator, tmp_path, archive)
    before = {name: read_member(package, name) for name in members}
    preview = (
        service.preview_all(package.name) if mode == "all"
        else service.preview_selected(package.name, [RULE]) if mode == "selected"
        else service.preview(package.name, RULE)
    )
    assert preview["available"]
    assert {name: read_member(package, name) for name in members} == before
    result = (
        service.apply_all(package.name, preview["plan_id"]) if mode == "all"
        else service.apply_selected(package.name, [RULE], source_guard=lambda: True) if mode == "selected"
        else service.apply(package.name, RULE, preview["plan_id"])
    )
    assert result["applied"] and result["undo_available"]
    assert result["change_count"] == 3 and result["removed_count"] == 0
    assert RULE not in {finding["code"] for finding in result["report"]["findings"]}
    assert REVIEW_RULE in {finding["code"] for finding in result["report"]["findings"]}
    for name, original_bytes in members.items():
        if name == "arrangements/lead.json":
            assert json.loads(read_member(package, name)) == repaired_chart(documents[name])
        else:
            assert read_member(package, name) == original_bytes
    assert not service.preview(package.name, RULE)["available"]
    assert service.restore(package.name, result["backup_id"])["outcome"] == "restored"
    assert {name: read_member(package, name) for name in members} == before


@pytest.mark.parametrize("archive", [False, True])
def test_changed_source_rejects_saved_plan_without_replacing_package(repair, validator, tmp_path, archive):
    package, members, documents, service = package_service(repair, validator, tmp_path, archive)
    preview = service.preview(package.name, RULE)
    documents["arrangements/lead.json"]["chords"][1]["notes"][0]["f"] = 8
    members["arrangements/lead.json"] = raw(documents["arrangements/lead.json"])
    write_package(package, members, archive)
    with pytest.raises(repair.RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert {name: read_member(package, name) for name in members} == members


def test_forged_replacement_is_rechecked_against_chart(repair):
    document = chart()
    preview = plan(repair, document)
    forged = copy.deepcopy(preview)
    action = forged["actions"][0]
    action["operations"][0]["changes"][0]["target_chord_id"] = 0
    action["action_id"] = repair._digest_json({
        "source_sha256": forged["source"]["sha256"],
        **{key: value for key, value in action.items() if key != "action_id"},
    })
    forged["plan_id"] = repair._digest_json({key: value for key, value in forged.items() if key != "plan_id"})
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(document), forged)
