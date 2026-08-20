import copy
import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
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
        "chart.negative-muted-fret",
        "chart.empty-phrases-key",
        "timeline.empty-arrangement-tempos-key",
        "timeline.duplicate-tempo",
        "timeline.tempos-out-of-order",
        "timeline.duplicate-time-signature",
        "timeline.time-signatures-out-of-order",
        "tones.duplicate-change",
        "tones.changes-out-of-order",
        "chart.duplicate-note",
        "chart.duplicate-chord-note",
        "chart.duplicate-chord",
        "chart.duplicate-anchor",
        "chart.duplicate-handshape",
        "chart.zero-length-handshape",
        "chart.invalid-handshape-span",
        "chart.note-duplicates-chord",
        "chart.bend-points-out-of-order",
        "lyrics.out-of-order",
        "timeline.duplicate-beat",
        "timeline.beats-out-of-order",
        "timeline.duplicate-section",
        "timeline.sections-out-of-order",
        "drums.duplicate-hit",
        "media.preview-missing",
        "media.preview-too-short",
        "media.preview-too-long",
        "media.preview-regenerate",
    ]
    assert {item["safety"] for item in catalog} == {
        "safe_automatic", "review_required",
    }
    assert sum(item["safety"] == "safe_automatic" for item in catalog) == 24
    structural_repairs = {
        item["rule_code"]: (
            item["action_kind"], item["source_kind"], item["change_kind"]
        )
        for item in catalog
        if item["rule_code"] in {
            "chart.empty-phrases-key",
            "timeline.empty-arrangement-tempos-key",
            "timeline.duplicate-tempo",
            "timeline.tempos-out-of-order",
            "timeline.duplicate-time-signature",
            "timeline.time-signatures-out-of-order",
            "tones.duplicate-change",
            "tones.changes-out-of-order",
        }
    }
    assert structural_repairs == {
        "chart.empty-phrases-key": (
            "omit_empty_phrases_key", "arrangement", "omit_empty"
        ),
        "timeline.empty-arrangement-tempos-key": (
            "omit_empty_arrangement_tempos_key", "arrangement", "omit_empty"
        ),
        "timeline.duplicate-tempo": (
            "remove_exact_duplicate_tempo_events", "timeline", "remove_duplicates"
        ),
        "timeline.tempos-out-of-order": (
            "reorder_tempo_events", "timeline", "reorder"
        ),
        "timeline.duplicate-time-signature": (
            "remove_exact_duplicate_time_signature_events",
            "timeline",
            "remove_duplicates",
        ),
        "timeline.time-signatures-out-of-order": (
            "reorder_time_signature_events", "timeline", "reorder"
        ),
        "tones.duplicate-change": (
            "remove_exact_duplicate_tone_changes", "arrangement", "remove_duplicates"
        ),
        "tones.changes-out-of-order": (
            "reorder_tone_changes", "arrangement", "reorder"
        ),
    }
    assert repair._ALL_SAFE_RULE_ORDER[:8] == (
        "chart.empty-phrases-key",
        "timeline.empty-arrangement-tempos-key",
        "timeline.duplicate-tempo",
        "timeline.tempos-out-of-order",
        "timeline.duplicate-time-signature",
        "timeline.time-signatures-out-of-order",
        "tones.duplicate-change",
        "tones.changes-out-of-order",
    )
    mute_repair = repair.repair_for_rule("chart.negative-muted-fret")
    assert mute_repair["source_kind"] == "arrangement"
    assert mute_repair["item_name"] == "muted note fret"
    assert mute_repair["change_kind"] == "normalize"
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
    reversed_repair = repair.repair_for_rule("chart.invalid-handshape-span")
    assert reversed_repair["item_name"] == "reversed handshape"
    assert reversed_repair["change_kind"] == "remove_redundant"
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
    beat_order_repair = repair.repair_for_rule("timeline.beats-out-of-order")
    assert beat_order_repair["item_name"] == "beat timeline"
    assert beat_order_repair["change_kind"] == "reorder"
    section_repair = repair.repair_for_rule("timeline.duplicate-section")
    assert section_repair["source_kind"] == "timeline"
    assert section_repair["item_name"] == "section marker"
    section_order_repair = repair.repair_for_rule(
        "timeline.sections-out-of-order"
    )
    assert section_order_repair["item_name"] == "section timeline"
    assert section_order_repair["change_kind"] == "reorder"
    assert repair.repair_for_rule("drums.duplicate-hit")["item_name"] == "drum hit"
    preview_repair = repair.repair_for_rule("media.preview-too-long")
    assert preview_repair["source_kind"] == "full_mix"
    assert preview_repair["change_kind"] == "replace_media"
    assert preview_repair["safety"] == "review_required"
    assert repair.repair_for_rule("chart.string-conflict") is None


def test_normalizes_all_negative_exact_string_mutes_and_nothing_else(repair):
    root_mute = {
        "t": 1.0, "s": 0, "f": -1, "sus": 0.25, "mt": True,
        "future": {"preserved": True},
    }
    fhm_only = {"t": 2.0, "s": 1, "f": -2, "fhm": True}
    palm_only = {"t": 3.0, "s": 2, "f": -3, "pm": True}
    malformed_mt = {"t": 4.0, "s": 3, "f": -4, "mt": "true"}
    chord_mute = {"s": 1, "f": -5, "mt": True, "fhm": True}
    level_mute = {"t": 6.0, "s": 2, "f": -6, "mt": True}
    document = {
        "notes": [root_mute, fhm_only, palm_only, malformed_mt],
        "chords": [{"t": 5.0, "notes": [chord_mute]}],
        "phrases": [{
            "levels": [{
                "notes": [level_mute],
                "chords": [{"t": 7.0, "notes": [{"s": 3, "f": -7, "mt": True}]}],
            }],
        }],
    }

    plan = _plan(
        repair, document, rule_code="chart.negative-muted-fret"
    )
    action = plan["actions"][0]

    assert action["change_kind"] == "normalize"
    assert action["change_count"] == 4
    assert action["removed_count"] == 0
    assert action["arrays_affected"] == 4
    assert action["musical_positions"] == 4
    assert [operation["note_array_path"] for operation in action["operations"]] == [
        ["notes"],
        ["phrases", 0, "levels", 0, "notes"],
        ["chords", 0, "notes"],
        ["phrases", 0, "levels", 0, "chords", 0, "notes"],
    ]

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired["notes"][0] == {**root_mute, "f": 0}
    assert repaired["notes"][1:] == [fhm_only, palm_only, malformed_mt]
    assert repaired["chords"][0]["notes"][0] == {**chord_mute, "f": 0}
    assert repaired["phrases"][0]["levels"][0]["notes"][0]["f"] == 0
    assert repaired["phrases"][0]["levels"][0]["chords"][0]["notes"][0]["f"] == 0


def test_muted_fret_normalization_plan_rejects_tampered_eligibility(repair):
    document = {"notes": [{"t": 1.0, "s": 0, "f": -2, "mt": True}]}
    raw = _raw(document)
    plan = _plan(
        repair, document, rule_code="chart.negative-muted-fret"
    )
    tampered = copy.deepcopy(plan)
    change = tampered["actions"][0]["operations"][0]["changes"][0]
    change["replacement_fret"] = 1
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)

    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(raw, tampered)

    assert caught.value.code == "source_changed"


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


def test_removes_only_reversed_handshapes_redundant_with_playable_chords(repair):
    chord = {
        "t": 10.0,
        "id": 0,
        "notes": [{"s": 0, "f": 3}, {"s": 1, "f": 5}],
    }
    handshape = {
        "chord_id": 0,
        "start_time": 10.0,
        "end_time": 9.75,
        "arp": False,
    }
    document = {
        "chords": [chord],
        "handshapes": [handshape],
        "templates": [{"name": "C", "frets": [3, 5], "fingers": [1, 3]}],
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
        rule_code="chart.invalid-handshape-span",
    )
    action = plan["actions"][0]

    assert action["change_kind"] == "remove_redundant"
    assert action["removed_count"] == 2
    assert action["arrays_affected"] == 2
    assert action["musical_positions"] == 1
    assert {
        operation["span_kind"] for operation in action["operations"]
    } == {"reversed"}

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired["handshapes"] == []
    assert repaired["phrases"][0]["levels"][0]["handshapes"] == []
    assert repaired["chords"] == [chord]
    assert repaired["phrases"][0]["levels"][0]["chords"] == [chord]


@pytest.mark.parametrize(
    ("handshape", "chords", "templates"),
    [
        ({"chord_id": 0, "start_time": 10.0}, [], [{}]),
        (
            {"chord_id": 0, "start_time": -1.0, "end_time": -2.0},
            [{"t": -1.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
            [{}],
        ),
        (
            {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
            [],
            [{}],
        ),
        (
            {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
            [
                {"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]},
                {"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]},
            ],
            [{}],
        ),
        (
            {
                "chord_id": 0,
                "start_time": 10.0,
                "end_time": 9.0,
                "arp": True,
            },
            [{"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
            [{}],
        ),
        (
            {
                "chord_id": 0,
                "start_time": 10.0,
                "end_time": 9.0,
                "future": {"meaning": "unknown"},
            },
            [{"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
            [{}],
        ),
        (
            {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
            [{"t": 10.0, "id": 0, "notes": []}],
            [{}],
        ),
        (
            {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
            [{"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
            [{"name": "C arpeggio"}],
        ),
        (
            {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
            [{"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
            [],
        ),
    ],
)
def test_reversed_handshape_repair_blocks_any_shape_with_possible_meaning(
    repair, handshape, chords, templates,
):
    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            {
                "chords": chords,
                "handshapes": [handshape],
                "templates": templates,
            },
            rule_code="chart.invalid-handshape-span",
        )

    assert caught.value.code == "reversed_handshape_requires_review"


@pytest.mark.parametrize(
    "template",
    [
        {"arp": True},
        {"arpeggio": "true"},
        {"displayName": "C-arp"},
        {"name": "C (arp)"},
    ],
)
def test_reversed_handshape_repair_blocks_all_template_arpeggio_markers(
    repair, template,
):
    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            {
                "chords": [{
                    "t": 10.0,
                    "id": 0,
                    "notes": [{"s": 0, "f": 3}],
                }],
                "handshapes": [{
                    "chord_id": 0,
                    "start_time": 10.0,
                    "end_time": 9.0,
                }],
                "templates": [template],
            },
            rule_code="chart.invalid-handshape-span",
        )

    assert caught.value.code == "reversed_handshape_requires_review"


def test_one_unsafe_invalid_handshape_blocks_eligible_siblings(repair):
    chord = {"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}
    document = {
        "chords": [chord],
        "handshapes": [
            {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
            {"chord_id": 1, "start_time": 11.0, "end_time": 10.0},
        ],
        "templates": [{}, {}],
    }

    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            document,
            rule_code="chart.invalid-handshape-span",
        )

    assert caught.value.code == "reversed_handshape_requires_review"
    assert document["handshapes"] == [
        {"chord_id": 0, "start_time": 10.0, "end_time": 9.0},
        {"chord_id": 1, "start_time": 11.0, "end_time": 10.0},
    ]


def test_invalid_handshape_repair_ignores_valid_and_zero_length_spans(repair):
    document = {
        "chords": [{"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
        "handshapes": [
            {"chord_id": 0, "start_time": 10.0, "end_time": 10.0},
            {"chord_id": 0, "start_time": 10.0, "end_time": 11.0},
        ],
        "templates": [{}],
    }

    plan = _plan(
        repair,
        document,
        rule_code="chart.invalid-handshape-span",
    )

    assert plan["actions"] == []


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


def test_omits_only_empty_optional_arrangement_keys(repair):
    original = {
        "version": 1,
        "phrases": [],
        "tempos": [],
        "notes": [{"t": 1.0, "s": 0, "f": 3}],
        "future": {"preserved": True},
    }
    phrase_plan = _plan(
        repair, original, rule_code="chart.empty-phrases-key"
    )
    phrase_action = phrase_plan["actions"][0]
    assert phrase_action["change_kind"] == "omit_empty"
    assert phrase_action["change_count"] == 1
    assert phrase_action["removed_count"] == 0
    assert phrase_action["musical_positions"] == 0
    assert phrase_action["operations"][0]["array_path"] == ["phrases"]

    after_phrase_raw = repair.apply_json_member(_raw(original), phrase_plan)
    after_phrase = json.loads(after_phrase_raw.decode("utf-8"))
    assert "phrases" not in after_phrase
    assert after_phrase["tempos"] == []
    assert after_phrase["notes"] == original["notes"]
    assert after_phrase["future"] == original["future"]

    tempo_plan = repair.plan_json_member(
        after_phrase_raw,
        member_path="arrangements/lead.json",
        source_kind="arrangement",
        validator_version="rules-test",
        rule_code="timeline.empty-arrangement-tempos-key",
    )
    tempo_action = tempo_plan["actions"][0]
    assert tempo_action["change_kind"] == "omit_empty"
    assert tempo_action["change_count"] == 1
    assert tempo_action["removed_count"] == 0
    assert tempo_action["musical_positions"] == 0
    after_both = json.loads(
        repair.apply_json_member(after_phrase_raw, tempo_plan).decode("utf-8")
    )
    assert "phrases" not in after_both
    assert "tempos" not in after_both
    assert after_both == {
        "version": 1,
        "notes": original["notes"],
        "future": original["future"],
    }

    for value in (None, {}, "", [{"start_time": 0, "end_time": 1, "levels": []}]):
        assert _plan(
            repair,
            {"phrases": value},
            rule_code="chart.empty-phrases-key",
        )["actions"] == []
    for value in (None, {}, "", [{"time": 0, "bpm": 120}]):
        assert _plan(
            repair,
            {"tempos": value},
            rule_code="timeline.empty-arrangement-tempos-key",
        )["actions"] == []
    assert _plan(
        repair,
        {"version": 1, "tempos": []},
        source_kind="timeline",
        rule_code="timeline.duplicate-tempo",
    )["actions"] == []

    tampered = copy.deepcopy(phrase_plan)
    operation = tampered["actions"][0]["operations"][0]
    operation["field"] = "tempos"
    operation["array_path"] = ["tempos"]
    action = tampered["actions"][0]
    action["action_id"] = repair._digest_json({
        "source_sha256": tampered["source"]["sha256"],
        **{key: value for key, value in action.items() if key != "action_id"},
    })
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)
    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(_raw(original), tampered)
    assert caught.value.code == "invalid_plan"


def test_removes_only_exact_tempo_meter_and_tone_duplicates(repair):
    cases = [
        (
            "timeline.duplicate-tempo",
            "timeline",
            {
                "tempos": [
                    {"time": 0, "bpm": 120, "future": {"x": 1}},
                    {"future": {"x": 1}, "bpm": 120, "time": 0},
                    {"time": 0, "bpm": 121, "future": {"x": 1}},
                    {"time": 0, "bpm": 120.0, "future": {"x": 1}},
                ]
            },
            "tempos",
        ),
        (
            "timeline.duplicate-time-signature",
            "timeline",
            {
                "time_signatures": [
                    {"time": 0, "ts": [4, 4], "future": "kept"},
                    {"future": "kept", "ts": [4, 4], "time": 0},
                    {"time": 0, "ts": [3, 4], "future": "kept"},
                ]
            },
            "time_signatures",
        ),
        (
            "tones.duplicate-change",
            "arrangement",
            {
                "tones": {
                    "base": "Clean",
                    "changes": [
                        {"t": 1, "name": "Lead", "rig": "lead", "future": 1},
                        {"future": 1, "rig": "lead", "name": "Lead", "t": 1},
                        {"t": 1, "name": "Lead 2", "rig": "lead", "future": 1},
                    ],
                }
            },
            "tones.changes",
        ),
    ]
    for rule_code, source_kind, document, field in cases:
        plan = _plan(
            repair,
            document,
            source_kind=source_kind,
            rule_code=rule_code,
        )
        action = plan["actions"][0]
        assert action["removed_count"] == 1
        assert action["change_count"] == 1
        repaired = json.loads(
            repair.apply_json_member(_raw(document), plan).decode("utf-8")
        )
        values = (
            repaired["tones"]["changes"]
            if field == "tones.changes"
            else repaired[field]
        )
        originals = (
            document["tones"]["changes"]
            if field == "tones.changes"
            else document[field]
        )
        assert values == [originals[0], *originals[2:]]

    malformed = {
        "tempos": [
            {"time": 0, "bpm": 120},
            {"time": 0, "bpm": 120},
            {"time": 1, "bpm": "fast"},
        ]
    }
    with pytest.raises(repair.RepairPlanningError) as blocked:
        _plan(
            repair,
            malformed,
            source_kind="timeline",
            rule_code="timeline.duplicate-tempo",
        )
    assert blocked.value.code == "malformed_timed_events"


def test_stably_orders_valid_tempo_meter_and_tone_streams(repair):
    cases = [
        (
            "timeline.tempos-out-of-order",
            "timeline",
            "tempos",
            {
                "tempos": [
                    {"time": 2, "bpm": 120, "id": "equal-first"},
                    {"time": 1, "bpm": 110, "id": "earlier"},
                    {"time": 2, "bpm": 130, "id": "equal-second"},
                ]
            },
            ["earlier", "equal-first", "equal-second"],
        ),
        (
            "timeline.time-signatures-out-of-order",
            "timeline",
            "time_signatures",
            {
                "time_signatures": [
                    {"time": 3, "ts": [4, 4], "id": "late-first"},
                    {"time": 1, "ts": [3, 4], "id": "early"},
                    {"time": 3, "ts": [6, 8], "id": "late-second"},
                ]
            },
            ["early", "late-first", "late-second"],
        ),
        (
            "tones.changes-out-of-order",
            "arrangement",
            "tones.changes",
            {
                "tones": {
                    "changes": [
                        {"t": 4, "name": "A", "id": "late-first"},
                        {"t": 2, "name": "B", "id": "early"},
                        {"t": 4, "name": "C", "id": "late-second"},
                    ]
                }
            },
            ["early", "late-first", "late-second"],
        ),
    ]
    for rule_code, source_kind, field, document, expected_ids in cases:
        plan = _plan(
            repair,
            document,
            source_kind=source_kind,
            rule_code=rule_code,
        )
        action = plan["actions"][0]
        assert action["change_count"] == 1
        assert action["removed_count"] == 0
        operation = action["operations"][0]
        assert operation["sorted_indices"] == [1, 0, 2]
        repaired = json.loads(
            repair.apply_json_member(_raw(document), plan).decode("utf-8")
        )
        values = (
            repaired["tones"]["changes"]
            if field == "tones.changes"
            else repaired[field]
        )
        assert [value["id"] for value in values] == expected_ids

    malformed = {
        "tempos": [
            {"time": 3, "bpm": 120},
            {"time": 2, "bpm": "fast"},
        ]
    }
    with pytest.raises(repair.RepairPlanningError) as blocked:
        _plan(
            repair,
            malformed,
            source_kind="timeline",
            rule_code="timeline.tempos-out-of-order",
        )
    assert blocked.value.code == "malformed_timed_events"

    oversized_number = {
        "tempos": [
            {"time": 3, "bpm": 120},
            {"time": 2, "bpm": 110},
            {"time": 10**1000, "bpm": 100},
        ]
    }
    with pytest.raises(repair.RepairPlanningError) as blocked:
        _plan(
            repair,
            oversized_number,
            source_kind="timeline",
            rule_code="timeline.tempos-out-of-order",
        )
    assert blocked.value.code == "malformed_timed_events"

    source = cases[0][3]
    plan = _plan(
        repair,
        source,
        source_kind="timeline",
        rule_code="timeline.tempos-out-of-order",
    )
    tampered = copy.deepcopy(plan)
    tampered["actions"][0]["operations"][0]["sorted_indices"] = [True, 0, 2]
    action = tampered["actions"][0]
    action["action_id"] = repair._digest_json({
        "source_sha256": tampered["source"]["sha256"],
        **{key: value for key, value in action.items() if key != "action_id"},
    })
    unsigned = {key: value for key, value in tampered.items() if key != "plan_id"}
    tampered["plan_id"] = repair._digest_json(unsigned)
    with pytest.raises(repair.RepairPlanningError) as caught:
        repair.apply_json_member(_raw(source), tampered)
    assert caught.value.code == "invalid_plan"


@pytest.mark.parametrize(
    ("field", "rule_code", "markers"),
    [
        (
            "beats",
            "timeline.beats-out-of-order",
            [
                {"time": 5.0, "measure": 2, "future": {"id": "last"}},
                {"time": 1.0, "measure": 1, "future": {"id": "equal-first"}},
                {"time": 1.0, "measure": -1, "future": {"id": "equal-second"}},
                {"time": 3.0, "measure": -1, "future": {"id": "middle"}},
            ],
        ),
        (
            "sections",
            "timeline.sections-out-of-order",
            [
                {"time": 5.0, "name": "Outro", "number": 1, "future": "last"},
                {"time": 1.0, "name": "Intro", "number": 1, "future": "equal-first"},
                {"time": 1.0, "name": "Count-in", "number": 2, "future": "equal-second"},
                {"time": 3.0, "name": "Verse", "number": 1, "future": "middle"},
            ],
        ),
    ],
)
def test_timeline_repair_stably_orders_markers_without_changing_data(
    repair, field, rule_code, markers,
):
    document = {"version": 1, "beats": [], "sections": [], field: markers}
    plan = _plan(
        repair,
        document,
        source_kind="timeline",
        rule_code=rule_code,
    )
    action = plan["actions"][0]

    assert action["change_kind"] == "reorder"
    assert action["change_count"] == 1
    assert action["removed_count"] == 0
    assert action["arrays_affected"] == 1
    assert action["musical_positions"] == 3
    assert action["operations"] == [{
        "operation": "stable_sort_timeline_markers",
        "array_path": [field],
        "field": field,
        "expected_length": 4,
        "original_sha256": action["operations"][0]["original_sha256"],
        "sorted_sha256": action["operations"][0]["sorted_sha256"],
        "sorted_indices": [1, 2, 3, 0],
        "moved_count": 4,
    }]

    repaired = json.loads(
        repair.apply_json_member(_raw(document), plan).decode("utf-8")
    )
    assert repaired[field] == [markers[1], markers[2], markers[3], markers[0]]
    assert sorted(
        repaired[field], key=lambda marker: json.dumps(marker, sort_keys=True)
    ) == sorted(markers, key=lambda marker: json.dumps(marker, sort_keys=True))


@pytest.mark.parametrize(
    ("field", "rule_code", "invalid_marker", "error_code"),
    [
        (
            "beats",
            "timeline.beats-out-of-order",
            {"time": 2.0, "measure": "unknown"},
            "invalid_beat_timeline",
        ),
        (
            "sections",
            "timeline.sections-out-of-order",
            {"time": 2.0, "name": 42},
            "invalid_section_timeline",
        ),
    ],
)
def test_timeline_repair_refuses_invalid_mixed_marker_lists(
    repair, field, rule_code, invalid_marker, error_code,
):
    document = {
        "beats": [],
        "sections": [],
        field: [
            {"time": 3.0, **({"measure": 1} if field == "beats" else {"name": "Later"})},
            invalid_marker,
            {"time": 1.0, **({"measure": 0} if field == "beats" else {"name": "Earlier"})},
        ],
    }
    with pytest.raises(repair.RepairPlanningError) as caught:
        _plan(
            repair,
            document,
            source_kind="timeline",
            rule_code=rule_code,
        )
    assert caught.value.code == error_code


def test_timeline_repair_rejects_tampered_ordering_instructions(repair):
    document = {
        "beats": [
            {"time": 2.0, "measure": 2},
            {"time": 1.0, "measure": 1},
        ],
        "sections": [],
    }
    raw = _raw(document)
    plan = _plan(
        repair,
        document,
        source_kind="timeline",
        rule_code="timeline.beats-out-of-order",
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


def test_tempo_meter_and_tone_source_resolution_matches_runtime(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True)
    manifest_path = package / "manifest.yaml"
    manifest_path.write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "  - id: rhythm\n"
        "    file: arrangements/rhythm.json\n"
        "  - id: hidden\n"
        "    file: arrangements/hidden.json\n"
        "    tones:\n"
        "      base: Manifest\n"
        "      changes:\n"
        "        - {t: 0, name: Clean}\n"
        "song_timeline: song_timeline.json\n",
        encoding="utf-8",
    )
    tempo = {"time": 0, "bpm": 120, "future": "kept"}
    (arrangements / "lead.json").write_bytes(_raw({
        "tempos": [tempo, dict(tempo)],
    }))
    tone = {"t": 1, "name": "Lead", "future": "kept"}
    (arrangements / "rhythm.json").write_bytes(_raw({
        "tones": {"changes": [tone, dict(tone)]},
    }))
    (arrangements / "hidden.json").write_bytes(_raw({
        "tones": {"changes": [tone, dict(tone)]},
    }))
    meter = {"time": 0, "ts": [4, 4], "future": "kept"}
    (package / "song_timeline.json").write_bytes(_raw({
        "version": 1,
        "tempos": [tempo, dict(tempo)],
        "time_signatures": [meter, dict(meter)],
    }))
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-structural-source-tests"),
    )

    tempo_preview = service.preview("Song.feedpak", "timeline.duplicate-tempo")
    assert tempo_preview["available"] is True
    assert [item["member_path"] for item in tempo_preview["member_plans"]] == [
        "arrangements/lead.json",
        "song_timeline.json",
    ]
    meter_preview = service.preview(
        "Song.feedpak", "timeline.duplicate-time-signature"
    )
    assert meter_preview["available"] is True
    assert [item["member_path"] for item in meter_preview["member_plans"]] == [
        "song_timeline.json"
    ]
    tone_preview = service.preview("Song.feedpak", "tones.duplicate-change")
    assert tone_preview["available"] is True
    assert [item["member_path"] for item in tone_preview["member_plans"]] == [
        "arrangements/rhythm.json"
    ]

    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "        - {t: 0, name: Clean}\n",
            "        - {t: 0, name: Clean}\n"
            "        - {t: 0, name: Clean}\n",
        ),
        encoding="utf-8",
    )
    blocked = service.preview("Song.feedpak", "tones.duplicate-change")
    assert blocked["available"] is False
    assert blocked["member_plans"] == []
    assert blocked["blockers"] == [{
        "member_path": "manifest.yaml",
        "code": "manifest_tones_require_manual_edit",
        "message": (
            "This tone issue is stored in manifest.yaml, which cannot be "
            "rewritten losslessly yet."
        ),
    }]
    assert not (
        tmp_path / "config" / "library_doctor" / "repair_backups"
    ).exists()


def test_fix_all_skips_an_unavailable_tone_rule_but_keeps_other_safe_repairs(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "    tones:\n"
        "      changes:\n"
        "        - {t: 0, name: Clean}\n"
        "        - {t: 0, name: Clean}\n",
        encoding="utf-8",
    )
    note = {"t": 1.0, "s": 0, "f": 3}
    (arrangements / "lead.json").write_bytes(_raw({
        "notes": [note, dict(note)],
        "chords": [],
    }))
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-conditional-fix-all-tests"),
    )

    all_safe = service.preview_all("Song.feedpak")

    assert all_safe["available"] is True
    assert all_safe["blockers"] == []
    assert all_safe["rule_codes"] == ["chart.duplicate-note"]
    assert all_safe["removed_count"] == 1


def test_batch_planning_recalculates_only_scan_reported_safe_rules(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangement_dir = package / "arrangements"
    arrangement_dir.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    note = {"t": 1.0, "s": 0, "f": 3}
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    source_raw = _raw({
        "notes": [note, dict(note)],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    })
    (arrangement_dir / "lead.json").write_bytes(source_raw)
    validation_reports = iter([
        {"findings": [{"code": "chart.duplicate-anchor"}]},
        {"findings": []},
    ])
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: next(validation_reports),
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-selected-batch-plan-tests"),
    )

    selected = service.preview_selected(
        "Song.feedpak", ["chart.duplicate-anchor"]
    )
    complete = service.preview_all("Song.feedpak")

    assert selected["requested_rule_codes"] == ["chart.duplicate-anchor"]
    assert selected["rule_codes"] == ["chart.duplicate-anchor"]
    assert selected["removed_count"] == 1
    assert "chart.duplicate-note" in complete["rule_codes"]
    assert complete["removed_count"] == 2
    with pytest.raises(repair.RepairPlanningError) as unsupported:
        service.preview_selected("Song.feedpak", ["chart.string-conflict"])
    assert unsupported.value.code == "unsupported_repair"

    result = service.apply_selected(
        "Song.feedpak", ["chart.duplicate-anchor"]
    )
    repaired = json.loads(
        (arrangement_dir / "lead.json").read_text(encoding="utf-8")
    )
    assert result["rule_codes"] == ["chart.duplicate-anchor"]
    assert len(repaired["anchors"]) == 1
    assert len(repaired["notes"]) == 2


def test_combined_repair_parses_and_renders_each_member_once(
    repair, tmp_path, monkeypatch,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangement_dir = package / "arrangements"
    arrangement_dir.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    note = {"t": 1.0, "s": 0, "f": 3}
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    source_raw = _raw({
        "notes": [note, dict(note)],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    })
    (arrangement_dir / "lead.json").write_bytes(source_raw)
    sequential = source_raw
    for rule_code in ("chart.duplicate-note", "chart.duplicate-anchor"):
        step = repair.plan_json_member(
            sequential,
            member_path="arrangements/lead.json",
            source_kind="arrangement",
            validator_version="rules-test",
            rule_code=rule_code,
        )
        sequential = repair.apply_json_member(sequential, step)
    calls = {"parse": 0, "render": 0}
    original_parse = repair._parse_json
    original_render = repair._render_json

    def counted_parse(raw):
        calls["parse"] += 1
        return original_parse(raw)

    def counted_render(document, raw):
        calls["render"] += 1
        return original_render(document, raw)

    monkeypatch.setattr(repair, "_parse_json", counted_parse)
    monkeypatch.setattr(repair, "_render_json", counted_render)
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-single-parse-tests"),
    )

    internal = service._plan_all_package(package, "Song.feedpak")

    assert internal["rule_codes"] == [
        "chart.duplicate-note",
        "chart.duplicate-anchor",
    ]
    assert internal["_members"][0]["replacement"] == sequential
    assert calls == {"parse": 1, "render": 1}


def test_fix_all_orders_omission_duplicates_and_timed_sorts(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    late_tempo = {"time": 2, "bpm": 120, "future": "tempo"}
    late_tone = {"t": 2, "name": "Lead", "future": "tone"}
    (arrangements / "lead.json").write_bytes(_raw({
        "phrases": [],
        "tempos": [late_tempo, dict(late_tempo), {"time": 1, "bpm": 110}],
        "tones": {
            "changes": [
                late_tone,
                dict(late_tone),
                {"t": 1, "name": "Clean"},
            ]
        },
    }))
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-structural-order-tests"),
    )

    internal = service._plan_all_package(package, "Song.feedpak")

    assert internal["available"] is True
    assert internal["rule_codes"] == [
        "chart.empty-phrases-key",
        "timeline.duplicate-tempo",
        "timeline.tempos-out-of-order",
        "tones.duplicate-change",
        "tones.changes-out-of-order",
    ]
    assert [
        step["rule_code"] for step in internal["_members"][0]["steps"]
    ] == internal["rule_codes"]
    repaired = json.loads(
        internal["_members"][0]["replacement"].decode("utf-8")
    )
    assert "phrases" not in repaired
    assert repaired["tempos"] == [
        {"time": 1, "bpm": 110},
        late_tempo,
    ]
    assert repaired["tones"]["changes"] == [
        {"t": 1, "name": "Clean"},
        late_tone,
    ]


@pytest.mark.parametrize("entrypoint", ["selected", "all", "single"])
def test_song_data_repair_reuses_signature_bound_deep_audio_findings(
    repair, tmp_path, entrypoint,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    source = tmp_path / "source"
    arrangement_dir = source / "arrangements"
    arrangement_dir.mkdir(parents=True)
    (source / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    (arrangement_dir / "lead.json").write_bytes(_raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    }))
    library.mkdir()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(source / "manifest.yaml", "manifest.yaml")
        archive.write(arrangement_dir / "lead.json", "arrangements/lead.json")
    validated = []

    def validate(_path, _package, *, deep_audio=False):
        validated.append(deep_audio)
        return {
            "validator_version": "rules-test",
            "features": {
                "deep_audio_checked": deep_audio,
                "deep_audio_files": 0,
                "deep_audio_skipped": 0,
                "deep_audio_unsupported": 0,
            },
            "findings": [],
            "counts": {"error": 0, "warning": 0, "info": 0},
            "status": "healthy",
            "title": "Song",
            "artist": "Artist",
        }

    before = {
        "validator_version": "rules-test",
        "features": {
            "deep_audio_checked": True,
            "deep_audio_files": 2,
            "deep_audio_skipped": 0,
            "deep_audio_unsupported": 0,
        },
        "findings": [
            {"code": "chart.duplicate-anchor", "severity": "warning"},
            {"code": "media.audio-longer-than-manifest", "severity": "warning"},
        ],
        "counts": {"error": 0, "warning": 2, "info": 0},
        "status": "warning",
    }
    guard_calls = []
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-deep-reuse-tests"),
    )

    options = {
        "deep_audio": True,
        "verified_before_report": before,
        "source_guard": lambda: guard_calls.append(True) or True,
    }
    if entrypoint == "selected":
        result = service.apply_selected(
            "Song.feedpak",
            ["chart.duplicate-anchor"],
            **options,
        )
    elif entrypoint == "all":
        plan = service.preview_all("Song.feedpak")
        result = service.apply_all(
            "Song.feedpak",
            plan["plan_id"],
            **options,
        )
    else:
        plan = service.preview("Song.feedpak", "chart.duplicate-anchor")
        result = service.apply(
            "Song.feedpak",
            "chart.duplicate-anchor",
            plan["plan_id"],
            **options,
        )

    with zipfile.ZipFile(package, "r") as archive:
        repaired = json.loads(archive.read("arrangements/lead.json"))
    assert len(repaired["anchors"]) == 1
    assert validated == [False]
    assert len(guard_calls) == 2
    assert result["deep_audio_reused"] is True
    assert result["verified_scan_report_reused"] is True
    assert result["performance"] == {
        "elapsed_seconds": result["performance"]["elapsed_seconds"],
        "deep_audio_requested": True,
        "verified_scan_report_reused": True,
        "deep_audio_reused": True,
    }
    assert result["performance"]["elapsed_seconds"] >= 0
    assert service.history(limit=1)["items"][0]["performance"] == (
        result["performance"]
    )
    assert result["report"]["features"]["deep_audio_checked"] is True
    assert result["report"]["features"]["deep_audio_files"] == 2
    assert {
        finding["code"] for finding in result["report"]["findings"]
    } == {"media.audio-longer-than-manifest"}


def test_single_repair_rejects_stale_verified_report_without_changing_package(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    source = tmp_path / "source"
    arrangement_dir = source / "arrangements"
    arrangement_dir.mkdir(parents=True)
    (source / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    (arrangement_dir / "lead.json").write_bytes(_raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    }))
    library.mkdir()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(source / "manifest.yaml", "manifest.yaml")
        archive.write(arrangement_dir / "lead.json", "arrangements/lead.json")
    original = package.read_bytes()
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=lambda *_args, **_kwargs: {},
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-stale-deep-reuse-tests"),
    )
    plan = service.preview("Song.feedpak", "chart.duplicate-anchor")

    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply(
            "Song.feedpak",
            "chart.duplicate-anchor",
            plan["plan_id"],
            deep_audio=True,
            verified_before_report={
                "validator_version": "rules-test",
                "features": {"deep_audio_checked": True},
                "findings": [],
            },
            source_guard=lambda: False,
        )

    assert raised.value.code == "source_changed"
    assert package.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()


def test_unpacked_song_data_repair_runs_fresh_deep_audio_validation(
    repair, tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangement_dir = package / "arrangements"
    arrangement_dir.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    (arrangement_dir / "lead.json").write_bytes(_raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    }))
    validated = []

    validation_count = 0

    def validate(_path, _package, *, deep_audio=False):
        nonlocal validation_count
        validation_count += 1
        validated.append(deep_audio)
        return {
            "validator_version": "rules-test",
            "features": {
                "deep_audio_checked": deep_audio,
                "deep_audio_files": 0,
                "deep_audio_skipped": 0,
                "deep_audio_unsupported": 0,
            },
            "findings": (
                [{"code": "chart.duplicate-anchor", "severity": "warning"}]
                if validation_count == 1 else []
            ),
            "counts": {
                "error": 0,
                "warning": 1 if validation_count == 1 else 0,
                "info": 0,
            },
            "status": "warning" if validation_count == 1 else "healthy",
            "title": "Song",
            "artist": "Artist",
        }

    cached = {
        "validator_version": "rules-test",
        "features": {"deep_audio_checked": True},
        "findings": [],
    }
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-directory-deep-tests"),
    )

    result = service.apply_selected(
        "Song.feedpak",
        ["chart.duplicate-anchor"],
        deep_audio=True,
        verified_before_report=cached,
        source_guard=lambda: True,
    )

    assert validated == [True, True]
    assert result["deep_audio_reused"] is False
    assert result["performance"]["deep_audio_requested"] is True
    assert result["performance"]["verified_scan_report_reused"] is False
    assert result["performance"]["deep_audio_reused"] is False
    assert result["performance"]["elapsed_seconds"] >= 0


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


def _phase0_directory_service(repair, tmp_path, *, barrier=None):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True, exist_ok=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n"
        "  - id: rhythm\n"
        "    file: arrangements/rhythm.json\n",
        encoding="utf-8",
    )
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    original = _raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    })
    for name in ("lead.json", "rhythm.json"):
        target = arrangements / name
        if not target.exists():
            target.write_bytes(original)

    def validate(path, package_name, *, deep_audio=False):
        duplicate = False
        for name in ("lead.json", "rhythm.json"):
            document = json.loads((path / "arrangements" / name).read_bytes())
            duplicate = duplicate or len(document.get("anchors", [])) > 1
        findings = (
            [{"code": "chart.duplicate-anchor", "severity": "warning"}]
            if duplicate else []
        )
        return {
            "schema": "library_doctor.package.v1",
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": bool(deep_audio)},
            "findings": findings,
        }

    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-phase0-transaction-tests"),
        transaction_barrier=barrier,
    )
    return service, package, original, validate


def _phase6_archive_service(repair, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    arrangement = _raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    })
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml",
            "arrangements:\n"
            "  - id: lead\n"
            "    file: arrangements/lead.json\n",
        )
        archive.writestr("arrangements/lead.json", arrangement)
        archive.writestr("cover.bin", b"unchanged-cover" * 64)

    def validate(path, package_name, *, deep_audio=False):
        with zipfile.ZipFile(path) as archive:
            document = json.loads(archive.read("arrangements/lead.json"))
        findings = (
            [{"code": "chart.duplicate-anchor", "severity": "warning"}]
            if len(document["anchors"]) > 1 else []
        )
        return {
            "schema": "library_doctor.package.v1",
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": bool(deep_audio)},
            "findings": findings,
        }

    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-phase6-archive-fault-tests"),
    )
    return service, package


def _link_directory_for_containment_test(link: Path, target: Path):
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError:
            pass
    pytest.skip("This platform cannot create a temporary directory link.")


def test_final_source_guard_preserves_an_external_edit_after_backup(
    repair, tmp_path,
):
    edited = _raw({"notes": [], "chords": [], "anchors": [], "external": True})
    package_holder = {}

    def barrier(name, context):
        if name == "before_member_replace" and context["member_index"] == 2:
            (package_holder["path"] / "arrangements" / "rhythm.json").write_bytes(
                edited
            )

    service, package, original, _validate = _phase0_directory_service(
        repair, tmp_path, barrier=barrier
    )
    package_holder["path"] = package
    plan = service.preview_all("Song.feedpak")

    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "source_changed"
    assert (package / "arrangements" / "lead.json").read_bytes() == original
    assert (package / "arrangements" / "rhythm.json").read_bytes() == edited
    backup_dir = tmp_path / "config" / "library_doctor" / "repair_backups"
    assert not list(backup_dir.glob("*.zip"))
    transaction_dir = (
        tmp_path / "config" / "library_doctor" / "repair_transactions"
    )
    assert not list(transaction_dir.glob("*.json"))


def test_repair_reopens_and_rejects_a_corrupted_durable_backup(
    repair, tmp_path,
):
    config_dir = tmp_path / "config"

    def barrier(name, context):
        if name != "backup_durable":
            return
        backup = (
            config_dir
            / "library_doctor"
            / "repair_backups"
            / f"{context['backup_id']}.zip"
        )
        backup.write_bytes(b"corrupted-after-durable-rename")

    service, package, original, _validate = _phase0_directory_service(
        repair, tmp_path, barrier=barrier
    )
    plan = service.preview_all("Song.feedpak")

    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "backup_failed"
    assert raised.value.file_state == "unchanged"
    assert (package / "arrangements" / "lead.json").read_bytes() == original
    assert (package / "arrangements" / "rhythm.json").read_bytes() == original
    assert not list(
        (config_dir / "library_doctor" / "repair_backups").glob("*.zip")
    )
    assert not list(
        (config_dir / "library_doctor" / "repair_transactions").glob("*.json")
    )


def test_directory_candidate_verifies_every_unchanged_member(
    repair, tmp_path, monkeypatch
):
    service, package, _original, _validate = _phase0_directory_service(
        repair, tmp_path
    )
    untouched = b"cover bytes that validation does not normally read"
    cover = package / "cover.bin"
    cover.write_bytes(untouched)
    real_copytree = repair.shutil.copytree

    def corrupt_unrelated_member(source, destination, *args, **kwargs):
        result = real_copytree(source, destination, *args, **kwargs)
        if Path(source) != package:
            return result
        candidate_cover = Path(destination) / "cover.bin"
        replacement = candidate_cover.with_suffix(".corrupted")
        replacement.write_bytes(b"same-size corruption".ljust(len(untouched), b"!"))
        os.replace(replacement, candidate_cover)
        return result

    monkeypatch.setattr(repair.shutil, "copytree", corrupt_unrelated_member)

    with pytest.raises(repair.RepairPlanningError) as raised:
        service._candidate(
            package,
            {"arrangements/lead.json": b'{"notes":[],"chords":[],"anchors":[]}'},
        )

    assert raised.value.code == "candidate_integrity_failed"
    assert cover.read_bytes() == untouched
    assert not list(package.parent.glob(".library-doctor-work-*"))


def test_directory_candidate_integrity_allows_planned_add_change_and_delete(
    repair, tmp_path
):
    service, package, _original, _validate = _phase0_directory_service(
        repair, tmp_path
    )
    deleted = package / "obsolete.bin"
    deleted.write_bytes(b"remove me")
    replacements = {
        "arrangements/lead.json": b'{"notes":[],"chords":[],"anchors":[]}',
        "generated/new.bin": b"new member",
        "obsolete.bin": None,
    }

    candidate, cleanup = service._candidate(package, replacements)
    try:
        assert candidate.name == "candidate"
        assert candidate.suffix.lower() not in {".feedpak", ".sloppak"}
        assert (candidate / "arrangements" / "lead.json").read_bytes() == replacements[
            "arrangements/lead.json"
        ]
        assert (candidate / "generated" / "new.bin").read_bytes() == b"new member"
        assert not (candidate / "obsolete.bin").exists()
        assert (package / "obsolete.bin").read_bytes() == b"remove me"
    finally:
        cleanup()


def test_archive_final_source_guard_binds_the_complete_package_bytes(
    repair, tmp_path,
):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    arrangement = _raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    })
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml",
            "arrangements:\n"
            "  - id: lead\n"
            "    file: arrangements/lead.json\n",
        )
        archive.writestr("arrangements/lead.json", arrangement)

    def validate(path, package_name, *, deep_audio=False):
        with zipfile.ZipFile(path) as archive:
            document = json.loads(archive.read("arrangements/lead.json"))
        findings = (
            [{"code": "chart.duplicate-anchor", "severity": "warning"}]
            if len(document["anchors"]) > 1 else []
        )
        return {
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": bool(deep_audio)},
            "findings": findings,
        }

    def barrier(name, _context):
        if name == "before_archive_replace":
            with zipfile.ZipFile(package, "a", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("external-edit.txt", b"preserve me")

    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-phase0-archive-guard-tests"),
        transaction_barrier=barrier,
    )
    plan = service.preview_all("Song.feedpak")

    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "source_changed"
    with zipfile.ZipFile(package) as archive:
        assert archive.read("external-edit.txt") == b"preserve me"
        assert archive.read("arrangements/lead.json") == arrangement
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob(
            "*.zip"
        )
    )


def test_archive_candidate_short_read_is_detected_before_backup_or_commit(
    repair, tmp_path, monkeypatch,
):
    service, package = _phase6_archive_service(repair, tmp_path)
    original = package.read_bytes()
    plan = service.preview_all("Song.feedpak")

    def short_copy(source, destination, length=0):
        del length
        destination.write(source.read(2))

    monkeypatch.setattr(repair.shutil, "copyfileobj", short_copy)
    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "candidate_integrity_failed"
    assert package.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()


def test_archive_candidate_disk_full_is_fail_closed_before_backup_or_commit(
    repair, tmp_path, monkeypatch,
):
    service, package = _phase6_archive_service(repair, tmp_path)
    original = package.read_bytes()
    plan = service.preview_all("Song.feedpak")

    def disk_full(_source, _destination, length=0):
        del length
        raise OSError("simulated disk full")

    monkeypatch.setattr(repair.shutil, "copyfileobj", disk_full)
    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "candidate_failed"
    assert package.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()


def test_directory_containment_guard_does_not_rollback_into_a_replaced_path(
    repair, tmp_path,
):
    paths = {}

    def barrier(name, context):
        if name != "member_committed" or context["member_index"] != 1:
            return
        package = paths["package"]
        package.rename(paths["interrupted"])
        paths["replacement"].rename(package)

    service, package, _original, _validate = _phase0_directory_service(
        repair, tmp_path, barrier=barrier
    )
    replacement = tmp_path / "library" / "replacement-package"
    shutil.copytree(package, replacement)
    external = _raw({
        "notes": [],
        "chords": [],
        "anchors": [],
        "external_package": True,
    })
    (replacement / "arrangements" / "lead.json").write_bytes(external)
    (replacement / "arrangements" / "rhythm.json").write_bytes(external)
    paths.update({
        "package": package,
        "interrupted": tmp_path / "library" / "interrupted-package",
        "replacement": replacement,
    })
    plan = service.preview_all("Song.feedpak")

    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "save_failed"
    assert raised.value.file_state == "recovery_required"
    assert (package / "arrangements" / "lead.json").read_bytes() == external
    assert (package / "arrangements" / "rhythm.json").read_bytes() == external
    receipt = service.history(limit=1)["items"][0]
    assert receipt["file_state"] == "recovery_required"


def test_commit_refuses_a_parent_junction_swap_outside_the_library(
    repair, tmp_path,
):
    library = tmp_path / "library"
    artist = library / "Artist"
    package = artist / "Song.feedpak"
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    anchor = {"time": 0.0, "fret": 1, "width": 4}
    original = _raw({
        "notes": [],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    })
    (arrangements / "lead.json").write_bytes(original)

    outside_artist = tmp_path / "outside" / "Artist"
    outside_package = outside_artist / "Song.feedpak"
    shutil.copytree(package, outside_package)
    outside_chart = outside_package / "arrangements" / "lead.json"
    external = _raw({"anchors": [], "external": "must survive"})
    outside_chart.write_bytes(external)
    held_artist = tmp_path / "held-artist"

    def validate(path, package_name, *, deep_audio=False):
        document = json.loads(
            (path / "arrangements" / "lead.json").read_bytes()
        )
        findings = (
            [{"code": "chart.duplicate-anchor", "severity": "warning"}]
            if len(document.get("anchors", [])) > 1 else []
        )
        return {
            "schema": "library_doctor.package.v1",
            "validator_version": "rules-test",
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "status": "warning" if findings else "healthy",
            "counts": {"error": 0, "warning": len(findings), "info": 0},
            "features": {"deep_audio_checked": bool(deep_audio)},
            "findings": findings,
        }

    def barrier(name, _context):
        if name != "before_member_replace":
            return
        artist.rename(held_artist)
        _link_directory_for_containment_test(artist, outside_artist)

    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-parent-junction-tests"),
        transaction_barrier=barrier,
    )
    plan = service.preview_all("Artist/Song.feedpak")
    try:
        with pytest.raises(repair.RepairPlanningError) as raised:
            service.apply_all("Artist/Song.feedpak", plan["plan_id"])
        assert raised.value.file_state == "unchanged"
        assert outside_chart.read_bytes() == external
        assert (
            held_artist / "Song.feedpak" / "arrangements" / "lead.json"
        ).read_bytes() == original
        assert not list(
            (tmp_path / "config" / "library_doctor" / "repair_backups").glob(
                "*.zip"
            )
        )
    finally:
        if artist.is_symlink():
            artist.unlink()
        elif artist.exists() or getattr(artist, "is_junction", lambda: False)():
            os.rmdir(artist)
        if held_artist.exists():
            held_artist.rename(artist)


def test_rollback_preserves_an_external_edit_to_an_already_committed_member(
    repair, tmp_path,
):
    paths = {}
    external = _raw({
        "notes": [],
        "chords": [],
        "anchors": [],
        "external_during_commit": True,
    })

    def barrier(name, context):
        if name == "member_committed" and context["member_index"] == 1:
            target = paths["package"].joinpath(
                *Path(context["member_path"]).parts
            )
            target.write_bytes(external)

    service, package, original, _validate = _phase0_directory_service(
        repair, tmp_path, barrier=barrier
    )
    paths["package"] = package
    plan = service.preview_all("Song.feedpak")

    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "save_failed"
    assert raised.value.file_state == "recovery_required"
    assert (package / "arrangements" / "lead.json").read_bytes() == external
    assert (package / "arrangements" / "rhythm.json").read_bytes() == original


@pytest.mark.parametrize(
    ("crash_barrier", "crash_after_member"),
    [
        ("member_replaced", 1),
        ("member_committed", 1),
        ("member_committed", 2),
    ],
)
def test_startup_reconciles_a_forced_stop_after_each_directory_member(
    repair, tmp_path, crash_barrier, crash_after_member,
):
    class SimulatedProcessDeath(BaseException):
        pass

    def barrier(name, context):
        if (
            name == crash_barrier
            and context["member_index"] == crash_after_member
        ):
            raise SimulatedProcessDeath

    service, package, _original, validate = _phase0_directory_service(
        repair, tmp_path, barrier=barrier
    )
    plan = service.preview_all("Song.feedpak")
    with pytest.raises(SimulatedProcessDeath):
        service.apply_all("Song.feedpak", plan["plan_id"])

    transaction_dir = (
        tmp_path / "config" / "library_doctor" / "repair_transactions"
    )
    assert len(list(transaction_dir.glob("*.json"))) == 1

    restarted = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: tmp_path / "library",
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-phase0-restart-tests"),
    )

    assert not list(transaction_dir.glob("*.json"))
    documents = [
        json.loads((package / "arrangements" / name).read_bytes())
        for name in ("lead.json", "rhythm.json")
    ]
    history = restarted.history(limit=1)["items"][0]
    if crash_after_member == 1:
        assert all(len(document["anchors"]) == 2 for document in documents)
        assert history["outcome"] == "restored"
        assert history["recovered_transaction"] is True
        assert not list(
            (tmp_path / "config" / "library_doctor" / "repair_backups").glob(
                "*.zip"
            )
        )
    else:
        assert all(len(document["anchors"]) == 1 for document in documents)
        assert history["outcome"] == "success"
        assert history["undo_available"] is True


def test_startup_recovery_never_overwrites_an_unknown_external_edit(
    repair, tmp_path,
):
    class SimulatedProcessDeath(BaseException):
        pass

    def barrier(name, context):
        if name == "member_committed" and context["member_index"] == 1:
            raise SimulatedProcessDeath

    service, package, _original, validate = _phase0_directory_service(
        repair, tmp_path, barrier=barrier
    )
    plan = service.preview_all("Song.feedpak")
    with pytest.raises(SimulatedProcessDeath):
        service.apply_all("Song.feedpak", plan["plan_id"])

    external = _raw({
        "notes": [],
        "chords": [],
        "anchors": [],
        "external_after_crash": True,
    })
    target = package / "arrangements" / "rhythm.json"
    target.write_bytes(external)
    restarted = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: tmp_path / "library",
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-phase0-external-recovery-tests"),
    )

    assert target.read_bytes() == external
    transaction_dir = (
        tmp_path / "config" / "library_doctor" / "repair_transactions"
    )
    assert len(list(transaction_dir.glob("*.json"))) == 1
    receipt = restarted.history(limit=1)["items"][0]
    assert receipt["outcome"] == "failure"
    assert receipt["file_state"] == "recovery_required"
    assert receipt["undo_available"] is False
    assert receipt["recovery_required"] is True
    assert receipt["resolution_actions"] == []
    assert receipt["manual_review_required"] is True
    assert "locked" in receipt["file_state_copy"].lower()

    state = restarted.recovery_state("Song.feedpak")
    assert state["required"] is True
    assert state["backup_id"] == receipt["backup_id"]
    assert state["next_action"] == "resolve_recovery"
    assert state["restore_available"] is False

    preview = restarted.preview_all("Song.feedpak")
    assert isinstance(preview, dict)
    with pytest.raises(repair.RepairPlanningError) as blocked:
        restarted.apply_all("Song.feedpak", "0" * 64)
    assert blocked.value.code == "recovery_required"
    assert blocked.value.file_state == "recovery_required"
    assert target.read_bytes() == external


def test_matching_recovery_restore_resolves_the_package_lock(repair, tmp_path):
    service, package, _original, _validate = _phase0_directory_service(
        repair, tmp_path
    )
    plan = service.preview_all("Song.feedpak")
    applied = service.apply_all("Song.feedpak", plan["plan_id"])
    transaction = service._begin_transaction(
        "Song.feedpak",
        applied["backup_id"],
        operation="repair",
        target_state="repaired",
    )
    service._update_transaction(transaction, phase="recovery_required")

    assert service.recovery_state("Song.feedpak")["required"] is True
    restored = service.restore("Song.feedpak", applied["backup_id"])

    assert restored["outcome"] == "restored"
    assert service.recovery_state("Song.feedpak") == {"required": False}
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_transactions").glob(
            "*.json"
        )
    )
    documents = [
        json.loads((package / "arrangements" / name).read_bytes())
        for name in ("lead.json", "rhythm.json")
    ]
    assert all(len(document["anchors"]) == 2 for document in documents)


def test_matching_recovery_finalize_can_keep_a_complete_repaired_package(
    repair, tmp_path,
):
    service, _package, _original, _validate = _phase0_directory_service(
        repair, tmp_path
    )
    plan = service.preview_all("Song.feedpak")
    applied = service.apply_all("Song.feedpak", plan["plan_id"])
    transaction = service._begin_transaction(
        "Song.feedpak",
        applied["backup_id"],
        operation="repair",
        target_state="repaired",
    )
    service._update_transaction(transaction, phase="recovery_required")

    preview = service.preview_finalize_backup(
        "Song.feedpak", applied["backup_id"]
    )
    assert preview["package_state"] == "repaired"
    finalized = service.finalize_backup("Song.feedpak", applied["backup_id"])

    assert finalized["outcome"] == "finalized"
    assert service.recovery_state("Song.feedpak") == {"required": False}


def test_directory_repair_refuses_to_write_without_a_durable_journal(
    repair, tmp_path, monkeypatch,
):
    service, package, original, _validate = _phase0_directory_service(
        repair, tmp_path
    )
    plan = service.preview_all("Song.feedpak")

    def fail_journal(_transaction):
        raise OSError("simulated durable storage failure")

    monkeypatch.setattr(service, "_write_transaction", fail_journal)
    with pytest.raises(repair.RepairPlanningError) as raised:
        service.apply_all("Song.feedpak", plan["plan_id"])

    assert raised.value.code == "journal_failed"
    assert raised.value.file_state == "unchanged"
    assert (package / "arrangements" / "lead.json").read_bytes() == original
    assert (package / "arrangements" / "rhythm.json").read_bytes() == original
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob(
            "*.zip"
        )
    )
