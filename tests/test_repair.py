import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).parents[1] / "repair.py"
    name = "library_health_repair_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _raw(document):
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _plan(
    repair,
    document,
    *,
    source_kind="arrangement",
    path=None,
    rule_code=None,
):
    return repair.plan_json_member(
        _raw(document),
        member_path=path or (
            "arrangements/lead.json" if source_kind == "arrangement" else "drums.json"
        ),
        source_kind=source_kind,
        validator_version="rules-test",
        rule_code=rule_code,
    )


def test_catalog_is_an_explicit_allowlist(repair):
    catalog = repair.repair_catalog()

    assert [item["rule_code"] for item in catalog] == [
        "chart.duplicate-note",
        "chart.duplicate-chord-note",
        "chart.duplicate-chord",
        "chart.duplicate-anchor",
        "chart.duplicate-handshape",
        "chart.note-duplicates-chord",
        "drums.duplicate-hit",
    ]
    assert {item["safety"] for item in catalog} == {"safe_automatic"}
    assert repair.repair_for_rule("chart.duplicate-note")["source_kind"] == "arrangement"
    assert repair.repair_for_rule("chart.duplicate-note")["item_name"] == "note"
    assert (
        repair.repair_for_rule("chart.duplicate-chord-note")["item_name"]
        == "chord note"
    )
    assert repair.repair_for_rule("chart.duplicate-chord")["item_name"] == "chord"
    assert repair.repair_for_rule("chart.duplicate-anchor")["item_name"] == "anchor"
    assert (
        repair.repair_for_rule("chart.duplicate-handshape")["item_name"]
        == "handshape"
    )
    assert (
        repair.repair_for_rule("chart.note-duplicates-chord")["item_name"]
        == "standalone note"
    )
    assert repair.repair_for_rule("drums.duplicate-hit")["item_name"] == "drum hit"
    assert repair.repair_for_rule("chart.string-conflict") is None


def test_plans_only_exact_top_level_note_duplicates(repair):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 0.5, "future": {"x": 1}},
            {"future": {"x": 1}, "f": 3, "s": 0, "sus": 0.5, "t": 1.0},
            {"t": 1.00001, "s": 0, "f": 3, "sus": 0.5, "future": {"x": 1}},
            {"t": 2.0, "s": 1, "f": 5, "sus": 0.25},
            {"t": 2.0, "s": 1, "f": 5, "sus": 1.0},
        ],
        "chords": [],
    }

    plan = _plan(repair, document)

    assert len(plan["actions"]) == 1
    action = plan["actions"][0]
    assert action["rule_code"] == "chart.duplicate-note"
    assert action["removed_count"] == 1
    assert action["arrays_affected"] == 1
    assert action["musical_positions"] == 1
    assert action["operations"] == [{
        "operation": "delete_array_items",
        "array_path": ["notes"],
        "expected_length": 5,
        "remove_indices": [1],
        "duplicate_groups": [{
            "keep_index": 0,
            "remove_indices": [1],
            "entry_sha256": action["operations"][0]["duplicate_groups"][0]["entry_sha256"],
        }],
    }]


def test_consolidates_multiple_duplicate_groups_before_indexes_can_shift(repair):
    first = {"t": 1.0, "s": 0, "f": 3}
    second = {"t": 2.0, "s": 1, "f": 5}
    document = {
        "notes": [first, second, copy.deepcopy(first), copy.deepcopy(second), copy.deepcopy(first)]
    }

    operation = _plan(repair, document)["actions"][0]["operations"][0]

    assert operation["remove_indices"] == [4, 3, 2]
    assert operation["duplicate_groups"][0]["remove_indices"] == [4, 2]
    assert operation["duplicate_groups"][1]["remove_indices"] == [3]


def test_keeps_difficulty_levels_as_separate_note_lists(repair):
    note = {"t": 1.0, "s": 0, "f": 3}
    document = {
        "notes": [],
        "phrases": [{
            "start_time": 0,
            "end_time": 10,
            "levels": [
                {"difficulty": 0, "notes": [note, copy.deepcopy(note)]},
                {"difficulty": 1, "notes": [copy.deepcopy(note)]},
                {"difficulty": 2, "notes": [note, copy.deepcopy(note), copy.deepcopy(note)]},
            ],
        }],
    }

    action = _plan(repair, document)["actions"][0]

    assert action["removed_count"] == 3
    assert [item["array_path"] for item in action["operations"]] == [
        ["phrases", 0, "levels", 0, "notes"],
        ["phrases", 0, "levels", 2, "notes"],
    ]
    assert action["operations"][1]["duplicate_groups"][0]["keep_index"] == 0
    assert action["operations"][1]["remove_indices"] == [2, 1]


def test_does_not_treat_chord_members_as_standalone_note_duplicates(repair):
    note = {"t": 1.0, "s": 0, "f": 3}
    document = {
        "notes": [note],
        "chords": [{"t": 1.0, "notes": [{"s": 0, "f": 3}, {"s": 0, "f": 3}]}],
    }

    assert _plan(repair, document)["actions"] == []


def test_removes_only_exact_duplicate_members_inside_each_chord(repair):
    member = {"s": 0, "f": 3, "sus": 0.5, "future": {"x": 1}}
    different = {"s": 0, "f": 3, "sus": 1.0, "future": {"x": 1}}
    document = {
        "chords": [{
            "t": 1.0,
            "notes": [member, copy.deepcopy(member), different],
        }],
        "phrases": [{
            "levels": [{
                "chords": [{
                    "t": 2.0,
                    "notes": [member, copy.deepcopy(member)],
                }],
            }],
        }],
    }

    plan = _plan(repair, document, rule_code="chart.duplicate-chord-note")
    action = plan["actions"][0]

    assert action["removed_count"] == 2
    assert action["arrays_affected"] == 2
    assert action["musical_positions"] == 2
    assert [operation["array_path"] for operation in action["operations"]] == [
        ["chords", 0, "notes"],
        ["phrases", 0, "levels", 0, "chords", 0, "notes"],
    ]

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired["chords"][0]["notes"] == [member, different]
    assert repaired["phrases"][0]["levels"][0]["chords"][0]["notes"] == [member]


@pytest.mark.parametrize(
    ("rule_code", "field", "duplicate", "different"),
    [
        (
            "chart.duplicate-chord",
            "chords",
            {"t": 1.0, "id": 0, "notes": [{"s": 0, "f": 3}]},
            {"t": 1.0, "id": 1, "notes": [{"s": 0, "f": 3}]},
        ),
        (
            "chart.duplicate-anchor",
            "anchors",
            {"time": 1.0, "fret": 3, "width": 4},
            {"time": 1.0, "fret": 3, "width": 5},
        ),
        (
            "chart.duplicate-handshape",
            "handshapes",
            {"start_time": 1.0, "end_time": 2.0, "chord_id": 0},
            {"start_time": 1.0, "end_time": 3.0, "chord_id": 0},
        ),
    ],
)
def test_removes_only_exact_duplicate_arrangement_events(
    repair, rule_code, field, duplicate, different,
):
    document = {
        field: [duplicate, copy.deepcopy(duplicate), different],
        "phrases": [{
            "levels": [{
                field: [copy.deepcopy(duplicate), copy.deepcopy(duplicate)],
            }],
        }],
    }

    plan = _plan(repair, document, rule_code=rule_code)
    action = plan["actions"][0]

    assert action["removed_count"] == 2
    assert action["arrays_affected"] == 2
    assert action["musical_positions"] == 1
    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired[field] == [duplicate, different]
    assert repaired["phrases"][0]["levels"][0][field] == [duplicate]


def test_plans_exact_standalone_notes_that_duplicate_explicit_chord_members(repair):
    matching_note = {"t": 1.0, "s": 0, "f": 3, "sus": 0.5, "future": {"x": 1}}
    chord = {
        "t": 1.0,
        "id": 9,
        "notes": [
            {"future": {"x": 1}, "f": 3, "s": 0, "sus": 0.5},
            {"s": 1, "f": 5},
        ],
    }
    level_note = {"t": 4.0, "s": 2, "f": 7}
    level_chord = {"t": 4.0, "notes": [{"s": 2, "f": 7}, {"s": 3, "f": 9}]}
    document = {
        "notes": [matching_note, copy.deepcopy(matching_note), {"t": 2.0, "s": 1, "f": 5}],
        "chords": [chord],
        "phrases": [{
            "levels": [{
                "notes": [level_note],
                "chords": [level_chord],
            }],
        }],
    }

    plan = _plan(
        repair,
        document,
        rule_code="chart.note-duplicates-chord",
    )

    action = plan["actions"][0]
    assert action["rule_code"] == "chart.note-duplicates-chord"
    assert action["removed_count"] == 3
    assert action["arrays_affected"] == 2
    assert action["musical_positions"] == 2
    assert [operation["remove_indices"] for operation in action["operations"]] == [
        [1, 0],
        [0],
    ]
    assert action["operations"][0]["match_groups"][0]["chord_index"] == 0
    assert action["operations"][0]["match_groups"][0]["chord_note_index"] == 0

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired["notes"] == [{"t": 2.0, "s": 1, "f": 5}]
    assert repaired["chords"] == [chord]
    assert repaired["phrases"][0]["levels"][0]["notes"] == []
    assert repaired["phrases"][0]["levels"][0]["chords"] == [level_chord]


def test_note_chord_repair_leaves_nonexact_and_ambiguous_matches_untouched(repair):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 0.5},
            {"t": 2.0, "s": 1, "f": 5},
            {"t": 3.0, "s": 2, "f": 7},
        ],
        "chords": [
            {"t": 1.0, "notes": [{"s": 0, "f": 3}]},
            {"t": 2.0, "notes": [{"s": 1, "f": 5}, {"s": 2, "f": 7}]},
            {"t": 2.0, "notes": [{"s": 1, "f": 5}, {"s": 3, "f": 9}]},
            # A member with its own time is malformed/ambiguous and not repairable.
            {"t": 3.0, "notes": [{"t": 3.0, "s": 2, "f": 7}]},
        ],
    }

    plan = _plan(
        repair,
        document,
        rule_code="chart.note-duplicates-chord",
    )

    assert plan["actions"] == []


def test_note_chord_repair_requires_the_matching_chord_to_remain_unchanged(repair):
    document = {
        "notes": [{"t": 1.0, "s": 0, "f": 3}],
        "chords": [{"t": 1.0, "notes": [{"s": 0, "f": 3}, {"s": 1, "f": 5}]}],
    }
    raw = _raw(document)
    plan = _plan(
        repair,
        document,
        rule_code="chart.note-duplicates-chord",
    )
    tampered = copy.deepcopy(plan)
    operation = tampered["actions"][0]["operations"][0]
    operation["match_groups"][0]["chord_note_index"] = 1
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)

    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(raw, tampered)

    assert caught.value.code == "source_changed"


def test_plans_only_exact_drum_hit_duplicates(repair):
    document = {
        "version": 1,
        "hits": [
            {"t": 4.0, "p": "snare", "v": 100},
            {"p": "snare", "v": 100, "t": 4.0},
            {"t": 4.00001, "p": "snare", "v": 100},
            {"t": 8.0, "p": "kick", "v": 80},
            {"t": 8.0, "p": "kick", "v": 120},
        ],
    }

    action = _plan(repair, document, source_kind="drum_tab")["actions"][0]

    assert action["rule_code"] == "drums.duplicate-hit"
    assert action["removed_count"] == 1
    assert action["operations"][0]["array_path"] == ["hits"]
    assert action["operations"][0]["remove_indices"] == [1]


def test_plan_is_deterministic_and_bound_to_exact_source_bytes(repair):
    document = {"notes": [{"t": 1, "s": 0, "f": 3}] * 2}
    first = _plan(repair, document)
    second = _plan(repair, document)
    spaced = repair.plan_json_member(
        json.dumps(document, indent=2).encode("utf-8"),
        member_path="arrangements/lead.json",
        source_kind="arrangement",
        validator_version="rules-test",
    )

    assert first == second
    assert first["plan_id"] != spaced["plan_id"]
    assert first["source"]["sha256"] != spaced["source"]["sha256"]


def test_jsonc_is_refused_even_when_it_contains_no_comments(repair):
    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.plan_json_member(
            b'{"notes": []}',
            member_path="arrangements/lead.jsonc",
            source_kind="arrangement",
            validator_version="rules-test",
        )

    assert caught.value.code == "jsonc_requires_lossless_writer"


@pytest.mark.parametrize("member_path", [
    "../lead.json",
    "/arrangements/lead.json",
    "arrangements\\lead.json",
    "arrangements/../lead.json",
    "./arrangements/lead.json",
    "arrangements//lead.json",
    "",
])
def test_unsafe_member_paths_are_refused(repair, member_path):
    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.plan_json_member(
            b'{"notes": []}',
            member_path=member_path,
            source_kind="arrangement",
            validator_version="rules-test",
        )

    assert caught.value.code == "invalid_member_path"


def test_ambiguous_or_nonstandard_json_is_refused(repair):
    for raw, expected_code in [
        (b'{"notes": [], "notes": []}', "duplicate_json_key"),
        (b'{"notes": [{"t": NaN, "s": 0, "f": 3}]}', "invalid_json"),
        (b'\xff', "invalid_utf8"),
    ]:
        with pytest.raises(repair.RepairPlanningError) as caught:
            repair.plan_json_member(
                raw,
                member_path="arrangements/lead.json",
                source_kind="arrangement",
                validator_version="rules-test",
            )
        assert caught.value.code == expected_code


def test_planning_limits_and_required_inputs_are_enforced(repair, monkeypatch):
    with pytest.raises(TypeError):
        repair.plan_json_member(
            "{}",
            member_path="arrangements/lead.json",
            source_kind="arrangement",
            validator_version="rules-test",
        )

    with pytest.raises(ValueError):
        repair.plan_json_member(
            b"{}",
            member_path="arrangements/lead.json",
            source_kind="arrangement",
            validator_version="",
        )

    with pytest.raises(repair.RepairPlanningError) as shape:
        repair.plan_json_member(
            b"[]",
            member_path="arrangements/lead.json",
            source_kind="arrangement",
            validator_version="rules-test",
        )
    assert shape.value.code == "invalid_document_shape"

    monkeypatch.setattr(repair, "MAX_REPAIR_TEXT_BYTES", 1)
    with pytest.raises(repair.RepairPlanningError) as size:
        repair.plan_json_member(
            b"{}",
            member_path="arrangements/lead.json",
            source_kind="arrangement",
            validator_version="rules-test",
        )
    assert size.value.code == "source_too_large"


def test_structure_limit_and_unencodable_values_fail_closed(repair, monkeypatch):
    monkeypatch.setattr(repair, "MAX_REPAIR_STRUCTURE_ITEMS", 1)
    with pytest.raises(repair.RepairPlanningError) as complexity:
        _plan(repair, {"notes": []})
    assert complexity.value.code == "source_too_complex"

    monkeypatch.setattr(repair, "MAX_REPAIR_STRUCTURE_ITEMS", 2_000_000)
    raw = (
        b'{"notes":['
        b'{"t":1,"s":0,"f":3,"future":"\\ud800"},'
        b'{"t":1,"s":0,"f":3,"future":"\\ud800"}'
        b"]}"
    )
    with pytest.raises(repair.RepairPlanningError) as unsupported:
        repair.plan_json_member(
            raw,
            member_path="arrangements/lead.json",
            source_kind="arrangement",
            validator_version="rules-test",
        )
    assert unsupported.value.code == "unsupported_json_value"


def test_invalid_entries_are_not_considered_repairable_duplicates(repair):
    invalid_entries = [
        {"t": True, "s": 0, "f": 3},
        {"t": 1.0, "s": True, "f": 3},
        {"t": 1.0, "s": -1, "f": 3},
        {"t": 1.0, "s": 0, "f": False},
    ]
    document = {"notes": [item for item in invalid_entries for _ in range(2)]}

    assert _plan(repair, document)["actions"] == []

    chord_plan = _plan(
        repair,
        {
            "chords": [{
                "notes": [
                    {"s": 0, "f": 3},
                    {"s": 0, "f": 3},
                ],
            }],
        },
        rule_code="chart.duplicate-chord-note",
    )
    assert chord_plan["actions"] == []

    drum_plan = _plan(
        repair,
        {"hits": [None, None, {"t": 1.0, "p": ""}, {"t": 1.0, "p": ""}]},
        source_kind="drum_tab",
    )
    assert drum_plan["actions"] == []


def test_malformed_optional_phrase_containers_are_ignored_safely(repair):
    document = {
        "notes": "not a list",
        "phrases": [None, {"levels": "not a list"}, {"levels": [None]}],
    }

    assert _plan(repair, document)["actions"] == []
    assert _plan(repair, {"hits": "not a list"}, source_kind="drum_tab")["actions"] == []


def test_input_document_is_not_mutated(repair):
    document = {"notes": [{"t": 1, "s": 0, "f": 3}] * 2}
    before = copy.deepcopy(document)

    _plan(repair, document)

    assert document == before


def test_unknown_source_kind_and_wrong_extension_are_refused(repair):
    with pytest.raises(repair.RepairPlanningError) as unknown:
        repair.plan_json_member(
            b"{}",
            member_path="lyrics.json",
            source_kind="lyrics",
            validator_version="rules-test",
        )
    assert unknown.value.code == "unsupported_source_kind"

    with pytest.raises(repair.RepairPlanningError) as extension:
        repair.plan_json_member(
            b"{}",
            member_path="arrangements/lead.txt",
            source_kind="arrangement",
            validator_version="rules-test",
        )
    assert extension.value.code == "unsupported_text_format"


def test_apply_json_member_removes_only_planned_copies_and_preserves_newline_style(repair):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 1, "f": 5},
        ],
        "chords": [],
    }
    raw = json.dumps(document, indent=2).replace("\n", "\r\n").encode("utf-8") + b"\r\n"
    plan = repair.plan_json_member(
        raw,
        member_path="arrangements/lead.json",
        source_kind="arrangement",
        validator_version="rules-test",
    )

    rendered = repair.apply_json_member(raw, plan)

    assert rendered.endswith(b"\r\n")
    assert b"\n" not in rendered.replace(b"\r\n", b"")
    parsed = json.loads(rendered.decode("utf-8"))
    assert parsed["notes"] == [document["notes"][0], document["notes"][2]]


def test_apply_json_member_rejects_tampered_or_stale_plans(repair):
    document = {"notes": [{"t": 1, "s": 0, "f": 3}] * 2}
    raw = _raw(document)
    plan = _plan(repair, document)
    tampered = copy.deepcopy(plan)
    tampered["actions"][0]["operations"][0]["remove_indices"] = []

    with pytest.raises(repair.RepairPlanningError) as invalid:
        repair.apply_json_member(raw, tampered)
    assert invalid.value.code == "invalid_plan"

    with pytest.raises(repair.RepairPlanningError) as stale:
        repair.apply_json_member(raw + b" ", plan)
    assert stale.value.code == "source_changed"
