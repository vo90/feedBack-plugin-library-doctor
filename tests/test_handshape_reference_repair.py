import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


eligibility = load("handshape_reference_test_eligibility", "repair_eligibility.py")
operations = load("handshape_reference_test_operations", "handshape_reference_repair.py")
assess = eligibility.assess_handshape_references


class PlanningError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def chart():
    # Exact Dream Stealer screenshot shapes, with shortened synthetic timing.
    return {
        "templates": [
            {"name": "G#5", "frets": [-1, -1, 10, 12, -1, -1],
             "fingers": [-1, -1, 1, 3, -1, -1]},
            {"name": "A#3", "frets": [-1, -1, -1, 7, 7, -1],
             "fingers": [-1, -1, -1, 1, 1, -1]},
        ],
        "chords": [
            {"t": 1, "id": 0, "notes": [{"s": 2, "f": 10}, {"s": 3, "f": 12}]},
            {"t": 2, "id": 1, "notes": [{"s": 3, "f": 7}, {"s": 4, "f": 7}]},
            {"t": 2.25, "id": 1, "hd": True,
             "notes": [{"s": 3, "f": 7}, {"s": 4, "f": 7}]},
        ],
        "notes": [],
        "handshapes": [{"start_time": 2, "end_time": 2.5, "chord_id": 0}],
        "anchors": [{"time": 2, "fret": 7, "width": 4}],
        "ext": {"retained": "unchanged"},
    }


def plan(document):
    return operations.plan_operation(document, assess=assess, error_type=PlanningError)


def apply(document, operation):
    return operations.apply_operation(document, operation, assess=assess, error_type=PlanningError)


def test_exact_previous_shape_is_repaired_without_changing_any_other_content():
    document = chart()
    before = copy.deepcopy(document)
    assessment = assess(document)
    assert document == before
    assert assessment["safe_count"] == assessment["affected_count"] == 1
    assert assessment["ambiguous_count"] == 0
    action = plan(document)
    assert action.change_count == 1
    apply(document, action.to_dict())
    expected = copy.deepcopy(before)
    expected["handshapes"][0]["chord_id"] = 1
    assert document == expected
    assert plan(document) is None
    assert assess(document)["status"] == "no_defect"


@pytest.mark.parametrize("mutate", [
    lambda d: d["handshapes"][0].update(arp=True),
    lambda d: d["handshapes"][0].update(arp=0),
    lambda d: d["handshapes"][0].update(author_hint="custom"),
    lambda d: d["templates"][0].update(arp=True),
    lambda d: d["templates"][1].update(arpeggio=True),
    lambda d: d["templates"][1].update(displayName="A#3-arp"),
    lambda d: d["templates"][0].update(name="G#5 (arp)"),
    lambda d: d["templates"][1].update(voicing="alternate"),
    lambda d: d["templates"][1].update(unknown={"meaning": 1}),
    lambda d: d["templates"].append(copy.deepcopy(d["templates"][1])),
    lambda d: d["templates"].append({"frets": [-1, -1, -1, 7.0, 7.0, -1]}),
    lambda d: d["templates"].append({"frets": [-1, -1, -1, 7, 7, -1, -1]}),
    lambda d: d["chords"][1]["notes"].pop(),
    lambda d: d["chords"][1]["notes"].append({"s": 3, "f": 7}),
    lambda d: d["chords"][1]["notes"][0].update(pm=True),
    lambda d: d["chords"][1]["notes"][0].update(sus=0),
    lambda d: d["chords"][1]["notes"][0].update(sl=8),
    lambda d: d["chords"][1]["notes"][0].update(fg=3),
    lambda d: d["chords"][1]["notes"][0].update(f=True),
    lambda d: d["chords"][1].update(unknown="metadata"),
    lambda d: d["chords"][1].update(hd=1),
    lambda d: d["chords"][1].update(id=True),
    lambda d: d["chords"].append(copy.deepcopy(d["chords"][1])),
    lambda d: d["chords"].append(copy.deepcopy(d["chords"][0])),
    lambda d: d["chords"][2].update(id=0, notes=copy.deepcopy(d["chords"][0]["notes"])),
    lambda d: d["notes"].append({"t": 2.125, "s": 3, "f": 7}),
    lambda d: d["notes"].append({"t": 2, "s": 0, "f": 0}),
    lambda d: d["notes"].append({"t": 1.5, "s": 0, "f": 0, "sus": 1}),
    lambda d: d["notes"].append({"t": 0, "s": 2, "f": 10, "ln": True}),
    lambda d: d["chords"].insert(0, {"t": 0.5, "id": 0, "notes": [
        {"s": 2, "f": 10, "sus": 2}, {"s": 3, "f": 12}]}),
    lambda d: d["chords"][1].update(t=2.000001),
    lambda d: d["chords"][0].update(id=1, notes=copy.deepcopy(d["chords"][1]["notes"])),
    lambda d: d["chords"].pop(0),
    lambda d: d["handshapes"].append({"start_time": 2.25, "end_time": 3, "chord_id": 1}),
    lambda d: d["handshapes"].append({"start_time": 1.5, "end_time": 2.1, "chord_id": 1}),
    lambda d: d["handshapes"].append(copy.deepcopy(d["handshapes"][0])),
])
def test_ambiguous_cases_are_left_unchanged(mutate):
    document = chart()
    mutate(document)
    before = copy.deepcopy(document)
    assessment = assess(document)
    assert assessment["changes"] == []
    assert assessment["ambiguous_count"] >= 1
    assert plan(document) is None
    assert document == before


def test_old_shape_with_shared_note_is_not_wholly_contradicted():
    document = chart()
    document["templates"][0]["frets"][3] = 7
    document["chords"][0]["notes"][1]["f"] = 7
    assert assess(document)["skipped"][0]["reason_code"] == "source_shape_not_wholly_contradicted"
    assert plan(document) is None


def test_phrase_levels_must_supply_their_own_proof_and_preserve_difficulties():
    document = chart()
    level = {key: copy.deepcopy(document[key]) for key in ("notes", "chords", "handshapes", "anchors")}
    document["phrases"] = [{"start_time": 0, "end_time": 3,
                            "levels": [{"difficulty": 0, **copy.deepcopy(level)},
                                       {"difficulty": 1, **copy.deepcopy(level)}]}]
    # Lower level lacks its own old chord: never borrow the flat chart or next level.
    document["phrases"][0]["levels"][0]["chords"].pop(0)
    before = copy.deepcopy(document)
    assessment = assess(document)
    assert assessment["safe_count"] == 2 and assessment["ambiguous_count"] == 1
    assert [c["container_path"] for c in assessment["changes"]] == [(), ("phrases", 0, "levels", 1)]
    apply(document, plan(document).to_dict())
    before["handshapes"][0]["chord_id"] = 1
    before["phrases"][0]["levels"][1]["handshapes"][0]["chord_id"] = 1
    assert document == before
    assert plan(document) is None


def test_clipped_phrase_continuation_is_skipped_even_with_flat_chart_evidence():
    document = chart()
    level = {key: copy.deepcopy(document[key]) for key in ("notes", "chords", "handshapes")}
    level["chords"].pop(0)
    level["handshapes"][0]["start_time"] = 1.75
    document["phrases"] = [{"start_time": 1.75, "end_time": 3, "levels": [level]}]
    assessment = assess(document)
    assert assessment["safe_count"] == 1 and assessment["ambiguous_count"] == 1
    assert assessment["skipped"][0]["reason_code"] == "no_unique_exact_onset"


def test_ambiguous_independent_occurrence_does_not_suppress_safe_one():
    document = chart()
    document["chords"].append({"t": 4, "id": 0, "notes": copy.deepcopy(document["chords"][0]["notes"])})
    document["handshapes"].append({"start_time": 4, "end_time": 4.5, "chord_id": 1, "arp": True})
    assessment = assess(document)
    assert assessment["safe_count"] == assessment["ambiguous_count"] == 1
    apply(document, plan(document).to_dict())
    assert document["handshapes"][1]["chord_id"] == 1
    assert plan(document) is None


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(phrases=[None]),
    lambda d: d.update(phrases=[{"start_time": 0, "end_time": 3, "levels": [None]}]),
    lambda d: d.update(phrases=[{"start_time": 0, "end_time": 3, "levels": [{"notes": {}}]}]),
    lambda d: d.update(phrases=[{"levels": [{}]}]),
    lambda d: d.update(templates={}),
    lambda d: d["chords"].append({"t": float("nan"), "id": 0}),
    lambda d: d["chords"].append({"t": 10 ** 400, "id": 0}),
    lambda d: d["notes"].append({"t": 0, "s": 2, "f": 10, "ln": 1}),
    lambda d: d["notes"].append({"t": 8, "s": 0, "f": 0, "sus": -1}),
    lambda d: d["handshapes"].append({"start_time": "bad", "end_time": 9}),
])
def test_malformed_authored_context_blocks_all_earlier_changes(mutate):
    document = chart()
    mutate(document)
    assessment = assess(document)
    assert assessment["changes"] == []
    assert assessment["blocker_code"] == "malformed_handshape_reference_context"
    assert plan(document) is None


@pytest.mark.parametrize("mutate", [
    lambda o: o["changes"][0].update(handshape_index=False),
    lambda o: o["changes"][0].update(original_chord_id=False),
    lambda o: o["changes"][0].update(target_chord_id=True),
    lambda o: o["changes"][0].update(target_chord_id=1.0),
    lambda o: o["changes"][0].update(container_path=["templates"]),
    lambda o: o.update(extra="unauthorized"),
    lambda o: o.update(changes=[]),
])
def test_operation_tampering_is_rejected_before_any_mutation(mutate):
    document = chart()
    operation = plan(document).to_dict()
    mutate(operation)
    before = copy.deepcopy(document)
    with pytest.raises(PlanningError) as error:
        apply(document, operation)
    assert error.value.code == "source_changed"
    assert document == before


def test_whole_document_digest_rejects_changed_context():
    document = chart()
    operation = plan(document).to_dict()
    document["ext"]["retained"] = "changed since preview"
    before = copy.deepcopy(document)
    with pytest.raises(PlanningError):
        apply(document, operation)
    assert document == before


def test_half_open_interval_boundary_and_matching_fingers_are_preserved():
    document = chart()
    for chord in document["chords"]:
        for member in chord["notes"]:
            member["fg"] = document["templates"][chord["id"]]["fingers"][member["s"]]
    document["notes"].append({"t": 2.5, "s": 0, "f": 0})
    document["handshapes"].append({"start_time": 2.5, "end_time": 3, "chord_id": 0})
    assert assess(document)["safe_count"] == 1


def test_assessment_and_action_hold_scalar_snapshots_not_source_objects():
    document = chart()
    assessment, action = assess(document), plan(document)
    document["handshapes"][0]["chord_id"] = 1
    assert assessment["changes"][0]["original_chord_id"] == 0
    assert action.changes == (((), 0, 0, 1),)
    assert json.loads(json.dumps(action.to_dict())) == action.to_dict()


def test_no_handshapes_does_not_invent_reference_findings_for_unrelated_malformed_data():
    document = {"templates": [], "chords": [{"t": 10 ** 400}], "handshapes": []}
    assessment = assess(document)
    assert assessment["status"] == "no_defect"
    assert assessment["safe_count"] == assessment["ambiguous_count"] == 0


@pytest.mark.parametrize("held", [{"sus": 3}, {"ln": True}])
def test_incoming_hold_from_another_mastery_level_only_vetoes_phrase_repair(held):
    document = chart()
    level = {key: copy.deepcopy(document[key]) for key in ("notes", "chords", "handshapes")}
    document["phrases"] = [
        {"start_time": 0, "end_time": 1, "levels": [
            {"notes": [], "chords": [], "handshapes": []},
            {"notes": [{"t": 0.5, "s": 2, "f": 10, **held}], "chords": [], "handshapes": []}]},
        {"start_time": 1, "end_time": 3, "levels": [level]},
    ]
    assessment = assess(document)
    assert [c["container_path"] for c in assessment["changes"]] == [()]
    assert assessment["ambiguous_count"] == 1
    assert assessment["skipped"][0]["reason_code"].startswith("incoming_phrase_")


@pytest.mark.parametrize("start,end,previous,old_frets,new_frets,repeats", [
    (151.229004, 151.634995, 150.255997,
     [-1, -1, 10, 12, -1, -1], [-1, -1, -1, 7, 7, -1], [151.552994]),
    (170.850006, 172.108002, 170.526001,
     [-1, -1, 8, 10, -1, -1], [-1, -1, 10, 12, -1, -1], [171.337006, 171.822998]),
])
def test_dream_stealer_screenshot_intervals_repair_only_the_reference(
    start, end, previous, old_frets, new_frets, repeats
):
    def members(frets):
        return [{"s": s, "f": f} for s, f in enumerate(frets) if f >= 0]

    document = {
        "templates": [{"frets": old_frets}, {"frets": new_frets}], "notes": [],
        "chords": [{"t": previous, "id": 0, "notes": members(old_frets)}]
                  + [{"t": t, "id": 1, "notes": members(new_frets)} for t in [start, *repeats]],
        "handshapes": [{"start_time": start, "end_time": end, "chord_id": 0}],
    }
    expected = copy.deepcopy(document)
    expected["handshapes"][0]["chord_id"] = 1
    apply(document, plan(document).to_dict())
    assert document == expected
