import copy
import json
import logging
import zipfile
from importlib import util
from pathlib import Path
import sys

import pytest

RULE = "chart.muted-fret-sentinel"


@pytest.fixture(scope="module")
def repair():
    name = "library_doctor_muted_sentinel_tests"
    spec = util.spec_from_file_location(name, Path(__file__).parents[1] / "repair.py")
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def raw(doc):
    return json.dumps(doc, separators=(",", ":")).encode()


def chart():
    chord = {"t": 10, "id": 0, "notes": [{"s": 3, "f": 127, "mt": True, "fhm": True, "future": [1]}]}
    return {"version": 1, "templates": [{"frets": [-1, -1, -1, 127, -1, -1]}],
            "notes": [{"t": 12, "s": 1, "f": 127, "mt": True}], "chords": [chord],
            "handshapes": [{"start_time": 10, "end_time": 11, "chord_id": 0}],
            "phrases": [{"levels": [{"notes": [], "chords": [copy.deepcopy(chord)]}]}]}


def plan(repair, doc):
    return repair.plan_json_member(raw(doc), member_path="arrangements/lead.json",
                                  source_kind="arrangement", validator_version="test", rule_code=RULE)


def test_repair_all_copies_and_shared_templates(repair):
    doc = chart()
    original = copy.deepcopy(doc)
    preview = plan(repair, doc)
    fixed = json.loads(repair.apply_json_member(raw(doc), preview))
    expected = copy.deepcopy(doc)
    expected["templates"][0]["frets"][3] = 0
    expected["notes"][0]["f"] = 0
    expected["chords"][0]["notes"][0]["f"] = 0
    expected["phrases"][0]["levels"][0]["chords"][0]["notes"][0]["f"] = 0
    assert fixed == expected
    assert doc == original
    assert preview["actions"][0]["change_count"] == 4
    assert not plan(repair, fixed)["actions"]


@pytest.mark.parametrize("mutation", [
    lambda d: d["notes"][0].update(mt=False, pm=True),
    lambda d: d["notes"][0].update(mt=1),
    lambda d: d["notes"][0].update(mt=False, fhm=True),
    lambda d: d["phrases"][0]["levels"][0]["chords"][0]["notes"][0].update(mt=False),
    lambda d: d["phrases"][0]["levels"][0]["chords"][0].update(notes=[]),
    lambda d: d["handshapes"][0].update(start_time=20, end_time=21),
    lambda d: d["phrases"][0]["levels"][0]["chords"][0]["notes"][0].update(s=3.0, f=0),
])
def test_ambiguous_or_unmuted_use_blocks_whole_document(repair, mutation):
    doc = chart()
    mutation(doc)
    before = raw(doc)
    with pytest.raises(repair.RepairPlanningError, match="mute|template|handshape"):
        plan(repair, doc)
    assert raw(doc) == before


def test_other_out_of_range_frets_and_flags_are_untouched(repair):
    doc = {"notes": [{"t": 0, "s": 0, "f": 126, "mt": True},
                     {"t": 1, "s": 0, "f": 0, "fhm": True}]}
    assert not plan(repair, doc)["actions"]


def test_tampered_operations_and_stale_source_are_rejected(repair):
    doc = chart()
    preview = plan(repair, doc)
    forged = copy.deepcopy(preview)
    action = forged["actions"][0]
    action["operations"][0]["changes"][0]["replacement"] = 12
    action["action_id"] = repair._digest_json({"source_sha256": forged["source"]["sha256"],
        **{k: v for k, v in action.items() if k != "action_id"}})
    forged["plan_id"] = repair._digest_json({k: v for k, v in forged.items() if k != "plan_id"})
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(doc), forged)
    doc["notes"][0]["mt"] = False
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(doc), preview)


def test_validator_and_planner_agree_on_mixed_difficulty_blocker(repair):
    name = "library_doctor_muted_validator_tests"
    spec = util.spec_from_file_location(name, Path(__file__).parents[1] / "validator.py")
    validator = util.module_from_spec(spec)
    sys.modules[name] = validator
    spec.loader.exec_module(validator)
    for ambiguous in (False, True):
        doc = chart()
        if ambiguous:
            doc["phrases"][0]["levels"][0]["chords"][0]["notes"][0]["mt"] = False
        findings, eligibility = validator._Findings(), {}
        validator._validate_arrangement_semantics(doc, "arrangements/lead.json", "lead", 60,
            findings, repair_eligibility=eligibility)
        assert any(f.code == RULE for f in findings.items)
        assert (eligibility[RULE]["status"] == "automatic") is (not ambiguous)


@pytest.mark.parametrize("archive", [False, True])
def test_transaction_and_undo_cover_every_member(repair, tmp_path, archive):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    members = {"manifest.yaml": b"arrangements:\n  - id: lead\n    file: arrangements/lead.json\n  - id: rhythm\n    file: arrangements/rhythm.json\n",
               "arrangements/lead.json": raw(chart()), "arrangements/rhythm.json": raw(chart()),
               "stems/full.bin": b"unchanged audio"}
    if archive:
        with zipfile.ZipFile(package, "w") as target:
            for name, data in members.items():
                target.writestr(name, data)
    else:
        for name, data in members.items():
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    def read(path, name):
        if Path(path).is_dir():
            return (Path(path) / name).read_bytes()
        with zipfile.ZipFile(path) as src:
            return src.read(name)
    def validate(path, _name, **_kw):
        findings = [{"code": RULE, "severity": "warning"}] if repair.assess_muted_fret_sentinels(
            json.loads(read(path, "arrangements/lead.json")))["status"] == "eligible" else []
        return {"validator_version": "test", "findings": findings,
                "counts": {"error": 0, "warning": len(findings), "info": 0}}
    service = repair.RepairService(config_dir=tmp_path / "config", get_dlc_dir=lambda: library,
        validate_feedpak=validate, validator_version="test", log=logging.getLogger("test"))
    preview = service.preview("Song.feedpak", RULE)
    assert preview["available"]
    result = service.apply("Song.feedpak", RULE, preview["plan_id"])
    assert result["applied"] and result["undo_available"]
    assert result["change_count"] == 8
    for name in ("arrangements/lead.json", "arrangements/rhythm.json"):
        assert repair.assess_muted_fret_sentinels(json.loads(read(package, name)))["status"] == "no_defect"
    assert read(package, "stems/full.bin") == members["stems/full.bin"]
    assert service.restore("Song.feedpak", result["backup_id"])["outcome"] == "restored"
    for name, data in members.items():
        assert read(package, name) == data
