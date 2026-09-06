import copy
import json
import logging
import sys
import zipfile
from dataclasses import FrozenInstanceError
from importlib import util
from pathlib import Path

import pytest


RULE_CODE = "timeline.repeated-measure-markers"


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).parents[1] / "repair.py"
    name = "library_doctor_measure_marker_repair_tests"
    spec = util.spec_from_file_location(name, path)
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _raw(document):
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _beats(measures):
    return [
        {
            "time": index * 0.5,
            "measure": measure,
            "future": {"index": index, "preserved": True},
        }
        for index, measure in enumerate(measures)
    ]


def _plan(repair, document, *, member_path="song_timeline.json"):
    return repair.plan_json_member(
        _raw(document),
        member_path=member_path,
        source_kind="timeline",
        validator_version="rules-test",
        rule_code=RULE_CODE,
    )


def _resign(repair, plan):
    action = plan["actions"][0]
    action_without_id = {
        key: value for key, value in action.items() if key != "action_id"
    }
    action["action_id"] = repair._digest_json(
        {
            "source_sha256": plan["source"]["sha256"],
            **action_without_id,
        }
    )
    unsigned = {key: value for key, value in plan.items() if key != "plan_id"}
    plan["plan_id"] = repair._digest_json(unsigned)
    return plan


def _service(repair, tmp_path, library):
    return repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-measure-marker-tests"),
    )


def _write_directory_package(tmp_path, manifest, members):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(manifest, encoding="utf-8")
    originals = {}
    for member_path, content in members.items():
        target = package.joinpath(*member_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = content if isinstance(content, bytes) else _raw(content)
        target.write_bytes(raw)
        originals[member_path] = raw
    return library, package, originals


def test_measure_marker_plan_applies_only_exact_repetitions_and_preserves_data(
    repair,
):
    beats = _beats([-1, 1, 1, 1, 2, 2, 3])
    document = {
        "version": 1,
        "beats": beats,
        "sections": [{"time": 0.0, "name": "intro", "future": [1, 2]}],
        "future_root": {"preserved": "verbatim"},
    }
    original = copy.deepcopy(document)
    expected = copy.deepcopy(document)
    for index in (2, 3, 5):
        expected["beats"][index]["measure"] = -1

    plan = _plan(repair, document)
    action = plan["actions"][0]
    operation = action["operations"][0]

    assert plan["catalog_version"] == "repairs-24"
    assert action["rule_code"] == RULE_CODE
    assert action["action_kind"] == "normalize_repeated_measure_markers"
    assert action["change_kind"] == "normalize_measure_markers"
    assert action["change_count"] == 3
    assert action["removed_count"] == 0
    assert action["arrays_affected"] == 1
    assert action["musical_positions"] == 3
    assert operation == {
        "operation": "normalize_repeated_measure_markers",
        "beat_array_path": ["beats"],
        "expected_length": len(beats),
        "original_sha256": repair._digest_json(beats),
        "result_sha256": repair._digest_json(expected["beats"]),
        "changes": [
            {
                "beat_index": index,
                "expected_measure": beats[index]["measure"],
                "replacement_measure": -1,
                "entry_sha256": repair._digest_json(beats[index]),
            }
            for index in (2, 3, 5)
        ],
    }

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )

    assert repaired == expected
    assert document == original
    assert [item["time"] for item in repaired["beats"]] == [
        item["time"] for item in original["beats"]
    ]
    assert [item["future"] for item in repaired["beats"]] == [
        item["future"] for item in original["beats"]
    ]


def test_measure_marker_operation_is_frozen_and_serializes_without_aliases(repair):
    document = {"beats": _beats([1, 1, 1, 2, 2])}
    operation = repair._measure_marker.plan_operation(
        document,
        assess=repair.assess_repeated_measure_markers,
        error_type=repair.RepairPlanningError,
    )

    assert operation is not None
    assert isinstance(operation.beat_array_path, tuple)
    assert isinstance(operation.changes, tuple)
    assert all(
        isinstance(change, repair._measure_marker.RepeatedMeasureMarkerChange)
        for change in operation.changes
    )
    with pytest.raises(FrozenInstanceError):
        operation.expected_length = 0
    with pytest.raises(FrozenInstanceError):
        operation.changes[0].replacement_measure = 0

    serialized = operation.to_dict()
    serialized["beat_array_path"][0] = "changed"
    serialized["changes"][0]["replacement_measure"] = 0
    assert operation.beat_array_path == ("beats",)
    assert operation.changes[0].replacement_measure == -1
    assert operation.to_dict() != serialized


@pytest.mark.parametrize(
    "measures",
    (
        [],
        [-1, -1],
        [1, 2, 3, 4],
        [-1, 1, -1, -1, 2, -1, 3],
    ),
)
def test_measure_marker_plan_is_a_no_op_for_correct_or_unaffected_grids(
    repair, measures,
):
    plan = _plan(repair, {"version": 1, "beats": _beats(measures)})

    assert plan["actions"] == []


@pytest.mark.parametrize(
    ("document", "blocker_code"),
    (
        ({"beats": _beats([1, 1, 2, 3])}, "insufficient_repeated_measure_runs"),
        ({"beats": _beats([1, 1, 3, 3])}, "non_consecutive_measure_runs"),
        ({"beats": _beats([1, 1, -1, 2, 2])}, "mixed_measure_marker_pattern"),
        ({"beats": _beats([0, 0, 1, 1])}, "unsupported_measure_marker"),
        ({"beats": [{"time": 0.0, "measure": "1"}]}, "malformed_beat_marker"),
        ({"beats": [{"time": 0.0, "measure": 1}, "bad"]}, "malformed_beat_marker"),
        (
            {
                "beats": [
                    {"time": 0.0, "measure": 1},
                    {"time": 0.0, "measure": 1},
                ]
            },
            "non_increasing_beat_times",
        ),
        ({}, "beats_not_array"),
    ),
)
def test_measure_marker_plan_fails_closed_for_ambiguous_or_malformed_grids(
    repair, document, blocker_code,
):
    with pytest.raises(repair.RepairPlanningError) as raised:
        _plan(repair, document)

    assert raised.value.code == blocker_code


@pytest.mark.parametrize(
    "tamper",
    (
        "path",
        "length",
        "original_hash",
        "result_hash",
        "index",
        "expected_measure",
        "replacement_measure",
        "entry_hash",
        "extra_field",
    ),
)
def test_fully_resigned_measure_marker_operation_tampering_is_rejected(
    repair, tamper,
):
    document = {"version": 1, "beats": _beats([1, 1, 1, 2, 2])}
    raw = _raw(document)
    plan = copy.deepcopy(_plan(repair, document))
    operation = plan["actions"][0]["operations"][0]
    if tamper == "path":
        operation["beat_array_path"] = ["sections"]
    elif tamper == "length":
        operation["expected_length"] += 1
    elif tamper == "original_hash":
        operation["original_sha256"] = "0" * 64
    elif tamper == "result_hash":
        operation["result_sha256"] = "0" * 64
    elif tamper == "index":
        operation["changes"][0]["beat_index"] = 0
    elif tamper == "expected_measure":
        operation["changes"][0]["expected_measure"] = 2
    elif tamper == "replacement_measure":
        operation["changes"][0]["replacement_measure"] = 0
    elif tamper == "entry_hash":
        operation["changes"][0]["entry_sha256"] = "0" * 64
    else:
        operation["unexpected"] = True
    _resign(repair, plan)

    with pytest.raises(repair.RepairPlanningError) as raised:
        repair.apply_json_member(raw, plan)

    assert raised.value.code == "invalid_plan"


def test_measure_marker_plan_rejects_source_changes_and_is_idempotent(repair):
    document = {"version": 1, "beats": _beats([1, 1, 1, 2, 2])}
    raw = _raw(document)
    plan = _plan(repair, document)

    with pytest.raises(repair.RepairPlanningError) as whitespace_change:
        repair.apply_json_member(raw + b" ", plan)
    assert whitespace_change.value.code == "source_changed"

    changed = copy.deepcopy(document)
    changed["beats"][0]["future"]["external"] = True
    with pytest.raises(repair.RepairPlanningError) as semantic_change:
        repair.apply_json_member(_raw(changed), plan)
    assert semantic_change.value.code == "source_changed"

    repaired_raw = repair.apply_json_member(raw, plan)
    second_plan = repair.plan_json_member(
        repaired_raw,
        member_path="song_timeline.json",
        source_kind="timeline",
        validator_version="rules-test",
        rule_code=RULE_CODE,
    )
    assert second_plan["actions"] == []


def test_service_preview_plans_every_eligible_declared_beat_copy_once(repair, tmp_path):
    affected = {"version": 1, "beats": _beats([1, 1, 1, 2, 2])}
    correct = {
        "version": 1,
        "beats": _beats([1, -1, -1, 2, -1]),
        "future_root": "must remain byte-identical",
    }
    manifest = (
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "  - id: lead-duplicate-declaration\n"
        "    file: arrangements/lead.json\n"
        "  - id: rhythm\n"
        "    file: arrangements/rhythm.json\n"
        "  - id: correct\n"
        "    file: arrangements/correct.json\n"
        "song_timeline: song_timeline.json\n"
    )
    library, package, originals = _write_directory_package(
        tmp_path,
        manifest,
        {
            "song_timeline.json": affected,
            "arrangements/lead.json": affected,
            "arrangements/rhythm.json": affected,
            "arrangements/correct.json": correct,
        },
    )
    service = _service(repair, tmp_path, library)

    preview = service.preview("Song.feedpak", RULE_CODE)

    assert preview["available"] is True
    assert preview["blockers"] == []
    assert [item["member_path"] for item in preview["member_plans"]] == [
        "song_timeline.json",
        "arrangements/lead.json",
        "arrangements/rhythm.json",
    ]
    assert preview["member_count"] == 3
    assert preview["change_count"] == 9
    assert preview["removed_count"] == 0
    assert preview["arrays_affected"] == 3
    assert preview["musical_positions"] == 9
    assert preview["change_kind"] == "normalize_measure_markers"
    for member_path, original in originals.items():
        assert package.joinpath(*member_path.split("/")).read_bytes() == original


@pytest.mark.parametrize(
    ("sibling_path", "sibling_content", "blocker_code"),
    (
        (
            "arrangements/ambiguous.json",
            {"beats": _beats([1, 1, 2, 3])},
            "insufficient_repeated_measure_runs",
        ),
        (
            "arrangements/commented.jsonc",
            b'{\n  // authored comment\n  "beats": []\n}\n',
            "jsonc_requires_lossless_writer",
        ),
    ),
)
def test_service_preview_blocks_the_whole_repair_for_an_uncertain_declared_copy(
    repair, tmp_path, sibling_path, sibling_content, blocker_code,
):
    manifest = (
        "arrangements:\n"
        "  - id: sibling\n"
        f"    file: {sibling_path}\n"
        "song_timeline: song_timeline.json\n"
    )
    library, package, originals = _write_directory_package(
        tmp_path,
        manifest,
        {
            "song_timeline.json": {"beats": _beats([1, 1, 1, 2, 2])},
            sibling_path: sibling_content,
        },
    )
    service = _service(repair, tmp_path, library)

    preview = service.preview("Song.feedpak", RULE_CODE)

    assert preview["available"] is False
    assert [item["member_path"] for item in preview["member_plans"]] == [
        "song_timeline.json"
    ]
    assert preview["blockers"] == [
        {
            "member_path": sibling_path,
            "code": blocker_code,
            "message": preview["blockers"][0]["message"],
        }
    ]
    assert preview["blockers"][0]["message"]
    for member_path, original in originals.items():
        assert package.joinpath(*member_path.split("/")).read_bytes() == original


@pytest.mark.parametrize("archive_package", (False, True))
def test_service_apply_and_undo_are_atomic_for_every_beat_copy(
    repair, tmp_path, archive_package,
):
    manifest = (
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "song_timeline: song_timeline.json\n"
    ).encode("utf-8")
    affected = _raw({
        "version": 1,
        "beats": _beats([1, 1, 1, 2, 2]),
        "sections": [],
    })
    members = {
        "manifest.yaml": manifest,
        "song_timeline.json": affected,
        "arrangements/lead.json": affected,
        "stems/full.bin": b"unrelated-audio-payload",
    }
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    library.mkdir()
    if archive_package:
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as output:
            for member_path, raw in members.items():
                output.writestr(member_path, raw)
    else:
        package.mkdir()
        for member_path, raw in members.items():
            target = package.joinpath(*member_path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    def read_member(path, member_path):
        if path.is_file():
            with zipfile.ZipFile(path) as source:
                return source.read(member_path)
        return path.joinpath(*member_path.split("/")).read_bytes()

    def validate(path, _package_name, *, deep_audio=False):
        timeline = json.loads(read_member(Path(path), "song_timeline.json"))
        assessment = repair.assess_repeated_measure_markers(timeline["beats"])
        findings = []
        if assessment["eligible"]:
            findings.append({
                "code": RULE_CODE,
                "severity": "warning",
                "affected_count": assessment["affected_count"],
            })
        return {
            "validator_version": "rules-test",
            "features": {"deep_audio_checked": deep_audio},
            "findings": findings,
            "counts": {
                "error": 0,
                "warning": len(findings),
                "info": 0,
            },
            "status": "warning" if findings else "healthy",
        }

    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-measure-marker-transaction-tests"),
    )
    preview = service.preview("Song.feedpak", RULE_CODE)

    result = service.apply("Song.feedpak", RULE_CODE, preview["plan_id"])

    assert result["applied"] is True
    assert result["undo_available"] is True
    assert result["change_count"] == 6
    assert result["member_count"] == 2
    for member_path in ("song_timeline.json", "arrangements/lead.json"):
        repaired = json.loads(read_member(package, member_path))
        assert [beat["measure"] for beat in repaired["beats"]] == [
            1, -1, -1, 2, -1,
        ]
    assert read_member(package, "stems/full.bin") == members["stems/full.bin"]

    restored = service.restore("Song.feedpak", result["backup_id"])

    assert restored["outcome"] == "restored"
    for member_path, original in members.items():
        assert read_member(package, member_path) == original
