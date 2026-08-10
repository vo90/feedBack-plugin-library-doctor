import copy
import hashlib
import importlib.util
import json
import logging
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).parents[1] / "repair.py"
    name = "library_doctor_repair_tests"
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
            "arrangements/lead.json"
            if source_kind == "arrangement"
            else "lyrics.json"
            if source_kind == "lyrics"
            else "song_timeline.json"
            if source_kind == "timeline"
            else "drums.json"
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
        "chart.zero-length-handshape",
        "chart.note-duplicates-chord",
        "chart.bend-points-out-of-order",
        "lyrics.out-of-order",
        "timeline.duplicate-beat",
        "timeline.duplicate-section",
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
    zero_length_repair = repair.repair_for_rule("chart.zero-length-handshape")
    assert zero_length_repair["item_name"] == "zero-length handshape"
    assert zero_length_repair["change_kind"] == "remove_redundant"
    assert (
        repair.repair_for_rule("chart.note-duplicates-chord")["item_name"]
        == "standalone note"
    )
    bend_repair = repair.repair_for_rule("chart.bend-points-out-of-order")
    assert bend_repair["item_name"] == "bend curve"
    assert bend_repair["change_kind"] == "reorder"
    lyric_repair = repair.repair_for_rule("lyrics.out-of-order")
    assert lyric_repair["source_kind"] == "lyrics"
    assert lyric_repair["item_name"] == "lyric timeline"
    assert lyric_repair["change_kind"] == "reorder"
    beat_repair = repair.repair_for_rule("timeline.duplicate-beat")
    assert beat_repair["source_kind"] == "timeline"
    assert beat_repair["item_name"] == "beat marker"
    section_repair = repair.repair_for_rule("timeline.duplicate-section")
    assert section_repair["source_kind"] == "timeline"
    assert section_repair["item_name"] == "section marker"
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


def test_removes_only_zero_length_handshapes_redundant_with_exact_chords(repair):
    chord = {"t": 10.0, "id": 2, "notes": [{"s": 0, "f": 3}]}
    handshape = {
        "chord_id": 2,
        "start_time": 10.0,
        "end_time": 10.0,
        "arp": False,
    }
    document = {
        "chords": [chord],
        "handshapes": [handshape, dict(handshape)],
        "phrases": [{
            "levels": [{
                "chords": [copy.deepcopy(chord)],
                "handshapes": [copy.deepcopy(handshape)],
            }],
        }],
    }

    plan = _plan(
        repair,
        document,
        rule_code="chart.zero-length-handshape",
    )
    action = plan["actions"][0]

    assert action["change_kind"] == "remove_redundant"
    assert action["removed_count"] == 3
    assert action["arrays_affected"] == 2
    assert action["musical_positions"] == 1
    assert [operation["remove_indices"] for operation in action["operations"]] == [
        [1, 0],
        [0],
    ]
    assert action["operations"][0]["chord_array_path"] == ["chords"]
    assert action["operations"][1]["handshape_array_path"] == [
        "phrases", 0, "levels", 0, "handshapes",
    ]

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired["handshapes"] == []
    assert repaired["phrases"][0]["levels"][0]["handshapes"] == []
    assert repaired["chords"] == [chord]
    assert repaired["phrases"][0]["levels"][0]["chords"] == [chord]


@pytest.mark.parametrize(
    ("handshape", "chords"),
    [
        ({"chord_id": 2, "start_time": 10.0, "end_time": 10.0}, []),
        (
            {"chord_id": 2, "start_time": 10.0, "end_time": 10.0},
            [{"t": 10.0, "id": 2}, {"t": 10.0, "id": 2}],
        ),
        (
            {"chord_id": 2, "start_time": 10.0, "end_time": 10.0, "arp": True},
            [{"t": 10.0, "id": 2}],
        ),
        (
            {
                "chord_id": 2,
                "start_time": 10.0,
                "end_time": 10.0,
                "future": {"meaning": "unknown"},
            },
            [{"t": 10.0, "id": 2}],
        ),
        (
            {"chord_id": 2, "start_time": 10.0, "end_time": 10.0},
            [{"t": 10.000001, "id": 2}],
        ),
        (
            {"chord_id": 2, "start_time": 10.0, "end_time": 10.0},
            [{"t": 10.0, "id": 3}],
        ),
    ],
)
def test_zero_length_handshape_repair_blocks_any_shape_with_possible_meaning(
    repair, handshape, chords,
):
    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            {"chords": chords, "handshapes": [handshape]},
            rule_code="chart.zero-length-handshape",
        )

    assert caught.value.code == "zero_length_handshape_requires_review"


def test_zero_length_handshape_plan_is_bound_to_the_preserved_chord(repair):
    document = {
        "chords": [{"t": 10.0, "id": 2, "notes": [{"s": 0, "f": 3}]}],
        "handshapes": [{"chord_id": 2, "start_time": 10.0, "end_time": 10.0}],
    }
    raw = _raw(document)
    plan = _plan(
        repair,
        document,
        rule_code="chart.zero-length-handshape",
    )
    tampered = copy.deepcopy(plan)
    tampered["actions"][0]["operations"][0]["match_groups"][0][
        "chord_sha256"
    ] = "0" * 64
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)

    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(raw, tampered)

    assert caught.value.code == "source_changed"


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


def test_bend_repair_stably_orders_every_curve_without_losing_data(repair):
    top_points = [
        {"t": 0.5, "v": 1.0, "future": {"label": "last"}},
        {"t": 0.1, "v": 0.25, "future": {"label": "equal-first"}},
        {"t": 0.1, "v": 0.5, "future": {"label": "equal-second"}},
    ]
    level_points = [{"t": 0.4, "v": 0.8}, {"t": 0.0, "v": 0.0}]
    chord_points = [{"t": 0.2, "v": 0.5}, {"t": 0.1, "v": 0.25}]
    document = {
        "notes": [{"t": 10.0, "s": 0, "f": 3, "bnv": top_points}],
        "chords": [{
            "t": 30.0,
            "notes": [{"s": 2, "f": 7, "bnv": chord_points}],
        }],
        "phrases": [{
            "levels": [{
                "notes": [{"t": 20.0, "s": 1, "f": 5, "bnv": level_points}],
            }],
        }],
    }

    plan = _plan(
        repair,
        document,
        rule_code="chart.bend-points-out-of-order",
    )
    action = plan["actions"][0]

    assert action["change_kind"] == "reorder"
    assert action["change_count"] == 3
    assert action["removed_count"] == 0
    assert action["arrays_affected"] == 3
    assert action["musical_positions"] == 3
    assert [operation["sorted_indices"] for operation in action["operations"]] == [
        [1, 2, 0],
        [1, 0],
        [1, 0],
    ]

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired["notes"][0]["bnv"] == [
        top_points[1], top_points[2], top_points[0]
    ]
    assert repaired["phrases"][0]["levels"][0]["notes"][0]["bnv"] == [
        level_points[1], level_points[0]
    ]
    assert repaired["chords"][0]["notes"][0]["bnv"] == [
        chord_points[1], chord_points[0]
    ]


def test_bend_repair_ignores_ordered_curves_and_refuses_invalid_mixed_curves(repair):
    ordered = {
        "notes": [{
            "t": 1.0,
            "s": 0,
            "f": 3,
            "bnv": [{"t": 0.0, "v": 0.0}, {"t": 0.5, "v": 1.0}],
        }],
    }
    assert _plan(
        repair,
        ordered,
        rule_code="chart.bend-points-out-of-order",
    )["actions"] == []

    invalid = {
        "notes": [{
            "t": 1.0,
            "s": 0,
            "f": 3,
            "bnv": [
                {"t": 0.5, "v": 1.0},
                {"t": "unknown", "v": 0.5},
                {"t": 0.0, "v": 0.0},
            ],
        }],
    }
    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            invalid,
            rule_code="chart.bend-points-out-of-order",
        )
    assert caught.value.code == "invalid_bend_curve"


def test_bend_repair_rejects_tampered_ordering_instructions(repair):
    document = {
        "notes": [{
            "t": 1.0,
            "s": 0,
            "f": 3,
            "bnv": [{"t": 0.5, "v": 1.0}, {"t": 0.0, "v": 0.0}],
        }],
    }
    raw = _raw(document)
    plan = _plan(
        repair,
        document,
        rule_code="chart.bend-points-out-of-order",
    )
    tampered = copy.deepcopy(plan)
    tampered["actions"][0]["operations"][0]["sorted_indices"] = [0, 1]
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)

    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(raw, tampered)

    assert caught.value.code == "invalid_plan"

    boolean_indexes = copy.deepcopy(plan)
    boolean_indexes["actions"][0]["operations"][0]["sorted_indices"] = [
        True, False
    ]
    unsigned = {
        key: value for key, value in boolean_indexes.items() if key != "plan_id"
    }
    boolean_indexes["plan_id"] = repair._digest_json(unsigned)
    with pytest.raises(repair.RepairPlanningError) as boolean_caught:
        repair.apply_json_member(raw, boolean_indexes)
    assert boolean_caught.value.code == "invalid_plan"


def test_lyric_repair_stably_orders_cues_without_changing_content(repair):
    cues = [
        {"t": 2.0, "d": 0.25, "w": "later", "future": {"id": 3}},
        {"t": 1.0, "d": 0.5, "w": "equal first", "future": {"id": 1}},
        {"t": 1.0, "d": 0.75, "w": "equal second", "future": {"id": 2}},
    ]

    plan = _plan(
        repair,
        cues,
        source_kind="lyrics",
        rule_code="lyrics.out-of-order",
    )
    action = plan["actions"][0]

    assert action["change_kind"] == "reorder"
    assert action["change_count"] == 1
    assert action["removed_count"] == 0
    assert action["arrays_affected"] == 1
    assert action["musical_positions"] == 1
    assert action["operations"][0]["array_path"] == []
    assert action["operations"][0]["sorted_indices"] == [1, 2, 0]

    repaired = json.loads(
        repair.apply_json_member(_raw(cues), plan).decode("utf-8")
    )
    assert repaired == [cues[1], cues[2], cues[0]]
    assert sorted(repaired, key=lambda cue: cue["future"]["id"]) == sorted(
        cues, key=lambda cue: cue["future"]["id"]
    )


def test_lyric_repair_ignores_ordered_cues_and_refuses_invalid_mixed_timeline(repair):
    ordered = [
        {"t": 1.0, "d": 0.2, "w": "first"},
        {"t": 2.0, "d": 0.2, "w": "second"},
    ]
    assert _plan(
        repair,
        ordered,
        source_kind="lyrics",
        rule_code="lyrics.out-of-order",
    )["actions"] == []

    invalid = [
        {"t": 2.0, "d": 0.2, "w": "later"},
        {"t": "unknown", "d": 0.2, "w": "invalid"},
        {"t": 1.0, "d": 0.2, "w": "earlier"},
    ]
    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            invalid,
            source_kind="lyrics",
            rule_code="lyrics.out-of-order",
        )
    assert caught.value.code == "invalid_lyric_timeline"


def test_lyric_repair_rejects_tampered_ordering_instructions(repair):
    cues = [
        {"t": 2.0, "d": 0.2, "w": "later"},
        {"t": 1.0, "d": 0.2, "w": "earlier"},
    ]
    raw = _raw(cues)
    plan = _plan(
        repair,
        cues,
        source_kind="lyrics",
        rule_code="lyrics.out-of-order",
    )
    tampered = copy.deepcopy(plan)
    tampered["actions"][0]["operations"][0]["sorted_indices"] = [0, 1]
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)

    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(raw, tampered)
    assert caught.value.code == "invalid_plan"


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


def test_plans_only_exact_valid_beat_marker_duplicates(repair):
    first = {"time": 1.0, "measure": 0, "future": {"source": "author"}}
    exact_copy = {
        "future": {"source": "author"},
        "measure": 0,
        "time": 1.0,
    }
    conflicting = {"time": 1.0, "measure": 1, "future": {"source": "author"}}
    invalid = {"time": 2.0, "future": {"source": "author"}}
    document = {
        "version": 1,
        "beats": [first, exact_copy, conflicting, invalid, dict(invalid)],
        "sections": [],
    }

    plan = _plan(repair, document, source_kind="timeline")
    action = plan["actions"][0]

    assert action["rule_code"] == "timeline.duplicate-beat"
    assert action["removed_count"] == 1
    assert action["musical_positions"] == 1
    assert action["operations"][0]["array_path"] == ["beats"]
    assert action["operations"][0]["remove_indices"] == [1]

    repaired = json.loads(repair.apply_json_member(_raw(document), plan))
    assert repaired["beats"] == [first, conflicting, invalid, invalid]


def test_plans_only_exact_valid_section_marker_duplicates(repair):
    first = {
        "name": "Intro",
        "time": 1.0,
        "number": 1,
        "future": {"source": "author"},
    }
    exact_copy = {
        "future": {"source": "author"},
        "number": 1,
        "time": 1.0,
        "name": "Intro",
    }
    conflicting = {
        "name": "Verse",
        "time": 1.0,
        "number": 1,
        "future": {"source": "author"},
    }
    invalid = {"time": 2.0, "number": 2, "future": {"source": "author"}}
    document = {
        "version": 1,
        "beats": [],
        "sections": [first, exact_copy, conflicting, invalid, dict(invalid)],
    }

    plan = _plan(
        repair,
        document,
        source_kind="timeline",
        rule_code="timeline.duplicate-section",
    )
    action = plan["actions"][0]

    assert action["rule_code"] == "timeline.duplicate-section"
    assert action["removed_count"] == 1
    assert action["musical_positions"] == 1
    assert action["operations"][0]["array_path"] == ["sections"]
    assert action["operations"][0]["remove_indices"] == [1]

    repaired = json.loads(repair.apply_json_member(_raw(document), plan))
    assert repaired["sections"] == [first, conflicting, invalid, invalid]


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

    timeline_plan = _plan(
        repair,
        {
            "beats": [
                {"time": True, "measure": 0},
                {"time": True, "measure": 0},
                {"time": 1.0, "measure": False},
                {"time": 1.0, "measure": False},
            ]
        },
        source_kind="timeline",
    )
    assert timeline_plan["actions"] == []

    section_plan = _plan(
        repair,
        {
            "sections": [
                {"name": "Intro", "time": True},
                {"name": "Intro", "time": True},
                {"name": "Verse", "time": 1.0, "number": False},
                {"name": "Verse", "time": 1.0, "number": False},
                {"time": 2.0},
                {"time": 2.0},
            ]
        },
        source_kind="timeline",
        rule_code="timeline.duplicate-section",
    )
    assert section_plan["actions"] == []


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
            member_path="unknown.json",
            source_kind="unknown",
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


def test_lyric_member_discovery_includes_primary_and_additional_tracks_once(repair):
    manifest = {
        "arrangements": [],
        "lyrics": "lyrics/main.json",
        "lyric_tracks": [
            {"id": "main", "file": "lyrics/main.json"},
            {"id": "translation", "file": "lyrics/swedish.json"},
            {"id": "unsafe", "file": "../outside.json"},
        ],
    }

    assert repair.RepairService._repair_member_paths(manifest, "lyrics") == [
        "lyrics/main.json",
        "lyrics/swedish.json",
    ]


def test_ambiguous_declared_timeline_blocks_instead_of_editing_legacy_grid(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    (package / "arrangements").mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "song_timeline: song_timeline.json\n",
        encoding="utf-8",
    )
    legacy = {
        "beats": [
            {"time": 1.0, "measure": 0},
            {"time": 1.0, "measure": 0},
        ],
        "sections": [],
    }
    legacy_path = package / "arrangements" / "lead.json"
    legacy_path.write_bytes(_raw(legacy))
    legacy_original = legacy_path.read_bytes()
    (package / "song_timeline.json").write_bytes(
        b'{"version":1,"beats":[],"sections":[],"beats":[]}'
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-timeline-source-tests"),
    )

    preview = service.preview("Song.feedpak", "timeline.duplicate-beat")

    assert preview["available"] is False
    assert preview["blockers"] == [{
        "member_path": "song_timeline.json",
        "code": "duplicate_json_key",
        "message": (
            "The song file repeats a JSON property and cannot be repaired safely."
        ),
    }]
    assert legacy_path.read_bytes() == legacy_original


def test_history_reader_preserves_pre_rename_receipts(repair, tmp_path):
    history_path = (
        tmp_path / "config" / "library_doctor" / "repair_history.json"
    )
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps({
            "schema": "library_health.repair_history.v1",
            "items": [{"id": "preserved", "outcome": "success"}],
        }),
        encoding="utf-8",
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: tmp_path / "library",
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-legacy-history-tests"),
        legacy_schemas={
            "repair_history": {"library_health.repair_history.v1"}
        },
    )

    result = service.history()

    assert result["schema"] == repair.HISTORY_SCHEMA
    assert result["items"] == [{"id": "preserved", "outcome": "success"}]


@pytest.mark.parametrize(
    "legacy_schema",
    ["library_health.repair_backup.v1", "library_health.repair_backup.v2"],
)
def test_backup_reader_preserves_pre_rename_recovery_files(
    repair, tmp_path, legacy_schema
):
    backup_id = "20260809-120000-abcdef123456"
    package = "Artist/Song.feedpak"
    member = "arrangements/lead.json"
    original = b'{"notes":[]}'
    backup_path = (
        tmp_path
        / "config"
        / "library_doctor"
        / "repair_backups"
        / f"{backup_id}.zip"
    )
    backup_path.parent.mkdir(parents=True)
    metadata = {
        "schema": legacy_schema,
        "backup_id": backup_id,
        "package": package,
        "members": [{
            "member_path": member,
            "backup_entry": f"original/{member}",
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "repaired_sha256": "0" * 64,
        }],
    }
    with zipfile.ZipFile(backup_path, "w") as archive:
        archive.writestr("repair.json", json.dumps(metadata))
        archive.writestr(f"original/{member}", original)
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: tmp_path / "library",
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-legacy-backup-tests"),
        legacy_schemas={"repair_backup": {legacy_schema}},
    )

    restored_metadata, originals = service._read_backup(backup_id, package)

    assert restored_metadata["schema"] == legacy_schema
    assert originals == {member: original}


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
