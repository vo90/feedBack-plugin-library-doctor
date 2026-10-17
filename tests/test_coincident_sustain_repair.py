import copy
import json
import logging
import sys
import zipfile
from importlib import util
from pathlib import Path

import pytest

RULE = "chart.coincident-chord-sustain"


def load(name, filename):
    spec = util.spec_from_file_location(name, Path(__file__).parents[1] / filename)
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repair():
    return load("library_doctor_coincident_tests", "repair.py")


def raw(doc):
    return json.dumps(doc, separators=(",", ":")).encode()


def chart():
    # The source chart's yellow C#5 root. Its blue fifth has no explicit hold.
    container = {"notes": [{"t": 30.393, "s": 1, "f": 5, "sus": .442}],
                 "chords": [{"t": 30.393, "id": 15, "hd": True,
                             "notes": [{"s": 1, "f": 5}, {"s": 2, "f": 7}]}],
                 "handshapes": [{"start_time": 29.726, "end_time": 31.505, "id": 15}]}
    return {**copy.deepcopy(container), "ext": {"unrelated": {"preserved": True}},
            "phrases": [{"start_time": 20.135, "end_time": 34.357,
                         "levels": [{"difficulty": 4, **copy.deepcopy(container)}]}]}


def plan(repair, doc, rule=RULE):
    return repair.plan_json_member(raw(doc), member_path="lead.json", source_kind="arrangement",
                                  validator_version="test", rule_code=rule)


def test_consolidates_real_yellow_shape_in_root_and_difficulty_without_changing_other_strings(repair):
    doc = chart()
    before = copy.deepcopy(doc)
    preview = plan(repair, doc)
    fixed = json.loads(repair.apply_json_member(raw(doc), preview))
    expected = copy.deepcopy(doc)
    for scope in (expected, expected["phrases"][0]["levels"][0]):
        scope["notes"] = []
        scope["chords"][0]["notes"][0]["sus"] = .442
    assert fixed == expected and doc == before
    action = preview["actions"][0]
    assert (action["change_count"], action["removed_count"], action["musical_positions"]) == (2, 2, 1)
    assert not plan(repair, fixed)["actions"]


@pytest.mark.parametrize("mutation", [
    lambda d: d["chords"][0]["notes"][0].update(sus=0),
    lambda d: d["chords"][0]["notes"][0].update(sus=.443),
    lambda d: d["chords"][0]["notes"][0].update(sus=False),
    lambda d: d["chords"][0]["notes"][0].update(fg=1),
    lambda d: d["notes"][0].update(fg=1),
    lambda d: d["notes"][0].update(sl=7),
    lambda d: d["notes"][0].update(unknown={"preserve": True}),
    lambda d: d["chords"][0]["notes"][0].update(unknown={"preserve": True}),
    lambda d: d["chords"][0].update(unknown={"preserve": True}),
    lambda d: d["notes"].append(copy.deepcopy(d["notes"][0])),
    lambda d: d["notes"].append({"t": 30.393, "s": 1, "f": 6}),
    lambda d: d["notes"].append({"t": 30.393, "s": 1, "f": 99}),
    lambda d: d["chords"][0]["notes"].append({"s": 1, "f": 5}),
    lambda d: d["chords"][0]["notes"].append({"s": 1, "f": 6}),
    lambda d: d["chords"].append(copy.deepcopy(d["chords"][0])),
    lambda d: d["chords"].append({"t": 30.393, "id": 15}),
    lambda d: d["chords"][0]["notes"].pop(),
    lambda d: d["chords"][0]["notes"].append({"s": 4}),
    lambda d: d["chords"][0]["notes"].append({"s": 4, "f": -1}),
    lambda d: d["chords"][0]["notes"].append(None),
    lambda d: d["phrases"][0]["levels"][0]["chords"][0]["notes"][0].update(sus=0),
])
def test_ambiguous_duration_metadata_or_multiplicity_requires_review_for_whole_arrangement(repair, mutation):
    doc = chart()
    mutation(doc)
    before = raw(doc)
    assessment = repair.assess_coincident_chord_sustains(doc)
    assert assessment["status"] == "blocked" and assessment["changes"] == []
    with pytest.raises(repair.RepairPlanningError) as error:
        plan(repair, doc)
    assert error.value.code == "ambiguous_coincident_chord_sustain"
    assert raw(doc) == before


def test_identical_unknown_data_still_requires_review_instead_of_moving_extensions(repair):
    doc = chart()
    doc["notes"][0]["future"] = doc["chords"][0]["notes"][0]["future"] = {"value": 1}
    assert repair.assess_coincident_chord_sustains(doc)["status"] == "blocked"


def test_matching_fingering_survives_and_multiple_sustains_delete_in_reverse_order(repair):
    doc = chart()
    doc.pop("phrases")
    doc["notes"][0]["fg"] = doc["chords"][0]["notes"][0]["fg"] = 1
    doc["notes"].extend([{"t": 30.393, "s": 2, "f": 7, "sus": .2},
                         {"t": 31, "s": 0, "f": 1}])
    fixed = json.loads(repair.apply_json_member(raw(doc), plan(repair, doc)))
    assert fixed["notes"] == [{"t": 31, "s": 0, "f": 1}]
    assert fixed["chords"][0]["notes"] == [{"s": 1, "f": 5, "fg": 1, "sus": .442},
                                            {"s": 2, "f": 7, "sus": .2}]


def test_nearby_onsets_are_not_snapped_or_merged(repair):
    doc = chart()
    doc.pop("phrases")
    doc["notes"][0]["t"] += .000001
    assert not plan(repair, doc)["actions"]


def test_exact_positive_duplicates_remain_owned_by_existing_cleanup(repair):
    doc = chart()
    doc.pop("phrases")
    doc["chords"][0]["notes"][0]["sus"] = .442
    assert not plan(repair, doc)["actions"]
    fixed = json.loads(repair.apply_json_member(raw(doc), plan(repair, doc, "chart.note-duplicates-chord")))
    assert fixed["notes"] == [] and fixed["chords"] == doc["chords"]


def test_stale_source_and_forged_duration_are_rejected(repair):
    doc = chart()
    preview = plan(repair, doc)
    forged = copy.deepcopy(preview)
    action = forged["actions"][0]
    action["operations"][0]["changes"][0]["sustain"] = 99
    action["action_id"] = repair._digest_json({"source_sha256": forged["source"]["sha256"],
        **{k: v for k, v in action.items() if k != "action_id"}})
    forged["plan_id"] = repair._digest_json({k: v for k, v in forged.items() if k != "plan_id"})
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(doc), forged)
    doc["chords"][0]["notes"][0]["sus"] = 0
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(doc), preview)


def test_scan_and_planner_share_conditional_eligibility(repair):
    validator = load("library_doctor_coincident_validator_tests", "validator.py")
    for blocked in (False, True):
        doc = chart()
        if blocked:
            doc["phrases"][0]["levels"][0]["chords"][0]["notes"][0]["sus"] = 0
        findings, eligibility = validator._Findings(), {}
        validator._validate_arrangement_semantics(doc, "lead.json", "lead", 60,
            findings, repair_eligibility=eligibility)
        assert any(f.code == RULE for f in findings.items)
        assert (eligibility[RULE]["status"] == "automatic") is (not blocked)
        assert validator.rule_metadata(RULE)["title"] == "Sustain stored beside its chord"


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("mode", ["single", "all", "selected"])
def test_transaction_backup_undo_and_fix_all_preserve_unrelated_bytes(repair, tmp_path, archive, mode):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    members = {"manifest.yaml": b"arrangements:\n  - id: lead\n    file: lead.json\n",
               "lead.json": raw(chart()), "audio.ogg": b"unchanged audio"}
    if archive:
        with zipfile.ZipFile(package, "w") as target:
            for name, data in members.items():
                target.writestr(name, data)
    else:
        package.mkdir()
        for name, data in members.items():
            (package / name).write_bytes(data)
    def read(path, name):
        if Path(path).is_dir():
            return (Path(path) / name).read_bytes()
        with zipfile.ZipFile(path) as source:
            return source.read(name)
    def validate(path, _name, **_kwargs):
        found = repair.assess_coincident_chord_sustains(json.loads(read(path, "lead.json")))["status"] == "eligible"
        return {"validator_version": "test", "findings": [{"code": RULE, "severity": "warning"}] if found else [],
                "counts": {"error": 0, "warning": int(found), "info": 0}}
    service = repair.RepairService(config_dir=tmp_path / "config", get_dlc_dir=lambda: library,
        validate_feedpak=validate, validator_version="test", log=logging.getLogger("test"))
    preview = (service.preview_all("Song.feedpak") if mode == "all"
               else service.preview_selected("Song.feedpak", [RULE]) if mode == "selected"
               else service.preview("Song.feedpak", RULE))
    assert preview["available"]
    result = (service.apply_all("Song.feedpak", preview["plan_id"]) if mode == "all"
              else service.apply_selected("Song.feedpak", [RULE], source_guard=lambda: True) if mode == "selected"
              else service.apply("Song.feedpak", RULE, preview["plan_id"]))
    assert result["applied"] and result["undo_available"] and result["change_count"] == 2
    assert read(package, "audio.ogg") == members["audio.ogg"]
    assert repair.assess_coincident_chord_sustains(json.loads(read(package, "lead.json")))["status"] == "no_defect"
    assert service.restore("Song.feedpak", result["backup_id"])["outcome"] == "restored"
    assert {name: read(package, name) for name in members} == members
