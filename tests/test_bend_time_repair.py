import copy
import json
import logging
import sys
import zipfile
from importlib import util
from pathlib import Path

import pytest

RULE = "chart.bend-time-coordinates"


@pytest.fixture(scope="module")
def repair():
    name = "library_doctor_bend_time_tests"
    spec = util.spec_from_file_location(name, Path(__file__).parents[1] / "repair.py")
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def raw(doc):
    return json.dumps(doc, separators=(",", ":")).encode()


def chart():
    note = {"t": 100, "s": 1, "f": 7, "sus": 1, "bn": 2, "bnv": [
        {"t": 100.25, "v": 0, "future": True}, {"t": 100.5, "v": 2}, {"t": 101, "v": 1}]}
    member = copy.deepcopy(note)
    member["t"] = 500  # Chord onset is authoritative.
    return {"notes": [note], "chords": [{"t": 100, "id": 0, "notes": [member]}],
            "phrases": [{"levels": [{"notes": [copy.deepcopy(note)]}]}]}


def plan(repair, doc):
    return repair.plan_json_member(raw(doc), member_path="lead.json", source_kind="arrangement",
                                  validator_version="test", rule_code=RULE)


def test_retained_curve_normalization_preserves_all_other_data(repair):
    doc = chart()
    before = copy.deepcopy(doc)
    preview = plan(repair, doc)
    fixed = json.loads(repair.apply_json_member(raw(doc), preview))
    expected = copy.deepcopy(doc)
    for note in (expected["notes"][0], expected["chords"][0]["notes"][0],
                 expected["phrases"][0]["levels"][0]["notes"][0]):
        for point in note["bnv"]:
            point["t"] -= 100
    assert fixed == expected and doc == before
    assert preview["actions"][0]["change_count"] == 9
    assert preview["actions"][0]["musical_positions"] == 3
    assert not plan(repair, fixed)["actions"]


@pytest.mark.parametrize("times", [[99.96, 100.5], [100.5, 101.02], [0, 100.5], [100.5, 100.25], [-.1, .4]])
def test_exceptional_or_mixed_lower_curve_blocks_entire_repair(repair, times):
    doc = chart()
    doc["phrases"][0]["levels"][0]["notes"][0]["bnv"] = [{"t": t, "v": 1} for t in times]
    with pytest.raises(repair.RepairPlanningError) as error:
        plan(repair, doc)
    assert error.value.code == "ambiguous_bend_time_coordinates"
    assert "Reconvert the original song with an updated converter" in str(error.value)
    assert "review and correct the original chart" in str(error.value)
    assert "select its original source" not in str(error.value)


@pytest.mark.parametrize("onset,sustain,times", [(0, 2, [0, .5]), (.25, 2, [.5, 1]),
    (100, 1, [0, .4, 1.00001]), (100, 1, [0])])
def test_valid_and_dual_interpretation_curves_are_unchanged(repair, onset, sustain, times):
    doc = {"notes": [{"t": onset, "s": 0, "f": 5, "sus": sustain,
                      "bnv": [{"t": t, "v": 1} for t in times]}]}
    assert not plan(repair, doc)["actions"]


def test_scalar_only_bend_is_never_reconstructed(repair):
    assert not plan(repair, {"notes": [{"t": 100, "s": 0, "f": 5, "bn": 2, "sus": 1}]})["actions"]


def test_source_change_rejects_apply(repair):
    doc = chart()
    preview = plan(repair, doc)
    doc["notes"][0]["bnv"][1]["v"] = 1
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(doc), preview)


def test_archive_apply_and_undo_preserve_audio_and_exact_bytes(repair, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    members = {"manifest.yaml": b"arrangements:\n  - id: lead\n    file: lead.json\n",
               "lead.json": raw(chart()), "audio.ogg": b"unchanged audio"}
    with zipfile.ZipFile(package, "w") as target:
        for name, data in members.items():
            target.writestr(name, data)
    def validate(path, _name, **_kwargs):
        with zipfile.ZipFile(path) as source:
            found = repair.assess_bend_time_coordinates(json.loads(source.read("lead.json")))["status"] == "eligible"
        return {"validator_version": "test", "findings": [{"code": RULE, "severity": "warning"}] if found else [],
                "counts": {"error": 0, "warning": int(found), "info": 0}}
    service = repair.RepairService(config_dir=tmp_path / "config", get_dlc_dir=lambda: library,
        validate_feedpak=validate, validator_version="test", log=logging.getLogger("test"))
    preview = service.preview("Song.feedpak", RULE)
    result = service.apply("Song.feedpak", RULE, preview["plan_id"])
    assert result["applied"] and result["undo_available"]
    assert result["change_count"] == 9
    service.restore("Song.feedpak", result["backup_id"])
    with zipfile.ZipFile(package) as source:
        assert {name: source.read(name) for name in members} == members
