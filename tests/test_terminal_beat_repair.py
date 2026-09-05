import copy
import importlib.util
import json
import logging
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from test_validator import _vorbis_ogg


ROOT = Path(__file__).parents[1]
RULE = "timeline.terminal-duplicate-beats"


@pytest.fixture(scope="module")
def modules():
    result = []
    for name in ("repair", "validator"):
        spec = importlib.util.spec_from_file_location("tail_test_" + name, ROOT / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result.append(module)
    return result


def raw(value):
    return json.dumps(value, allow_nan=False).encode()


def members():
    beats = [{"time": i * 0.5, "measure": i // 4 + 1 if i % 4 == 0 else -1}
             for i in range(20)]
    base = {"notes": [{"t": 2.0, "s": 0, "f": 3, "sus": 1.0}], "chords": [],
            "beats": beats, "sections": [{"time": 0, "name": "intro"}]}
    manifest = {"feedpak_version": "1.14.0", "title": "Terminal fixture", "artist": "Test",
                "duration": 9.25, "arrangements": [],
                "stems": [{"id": "full", "file": "stems/full.ogg"}]}
    data = {}
    for name, count in (("bass", 0), ("lead", 1), ("rhythm", 1), ("vocals", 3)):
        path = "arrangements/" + name + ".json"
        manifest["arrangements"].append({"id": name, "name": name, "file": path})
        doc = copy.deepcopy(base)
        doc["beats"] += [copy.deepcopy(beats[-2 + i % 2]) for i in range(count)]
        data[path] = doc
    return manifest, data


def build(tmp_path, modules, *, archive=False, mutate=None):
    repair, validator = modules
    manifest, documents = members()
    if mutate:
        mutate(manifest, documents)
    payloads = {p: raw(d) for p, d in documents.items()}
    payloads["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False).encode()
    payloads["stems/full.ogg"] = _vorbis_ogg(duration=9.25)
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Fixture.feedpak"
    if archive:
        with zipfile.ZipFile(package, "w") as z:
            for p, b in payloads.items():
                z.writestr(p, b)
    else:
        for p, b in payloads.items():
            target = package / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b)
    service = repair.RepairService(config_dir=tmp_path / "config", get_dlc_dir=lambda: library,
                                   validate_feedpak=validator.validate_feedpak,
                                   validator_version=validator.VALIDATOR_VERSION,
                                   log=logging.getLogger("terminal-tests"))
    return service, package, payloads


def read(package, member):
    if package.is_dir():
        return (package / member).read_bytes()
    with zipfile.ZipFile(package) as z:
        return z.read(member)


@pytest.mark.parametrize("archive", [False, True])
def test_terminal_transaction_preserves_events_and_undo(tmp_path, modules, archive):
    service, package, before = build(tmp_path, modules, archive=archive)
    report = modules[1].validate_feedpak(package)
    assert sum(f["affected_count"] for f in report["findings"] if f["code"] == RULE) == 5
    assert not any(f["code"] in {"timeline.duplicate-beat", "timeline.beats-out-of-order"}
                   for f in report["findings"])
    preview = service.preview(package.name, RULE)
    assert preview["available"] and preview["removed_count"] == 5
    assert preview["member_count"] == 3
    result = service.apply(package.name, RULE, preview["plan_id"])
    assert result["applied"] and result["undo_available"]
    for p, original in before.items():
        after = read(package, p)
        if p in {"arrangements/lead.json", "arrangements/rhythm.json", "arrangements/vocals.json"}:
            expected = json.loads(original)
            expected["beats"] = expected["beats"][:20]
            assert json.loads(after) == expected
            times = [b["time"] for b in expected["beats"]]
            assert all(b > a for a, b in zip(times, times[1:]))
        else:
            assert after == original
    assert not service.preview(package.name, RULE)["available"]
    assert not any(f["code"] == RULE for f in modules[1].validate_feedpak(package)["findings"])
    assert service.restore(package.name, result["backup_id"])["outcome"] == "restored"
    assert all(read(package, p) == original for p, original in before.items())


@pytest.mark.parametrize("mutation", [
    lambda m, d: d["arrangements/bass.json"]["beats"].append({"time": 9, "measure": -1}),
    lambda m, d: d["arrangements/lead.json"].update(beatIndex=20),
    lambda m, d: d["arrangements/lead.json"].update(future={"index": 20}),
    lambda m, d: d["arrangements/lead.json"]["beats"][-1].update(mask=3),
    lambda m, d: d["arrangements/lead.json"]["notes"][0].update(sus=7.1),
    lambda m, d: d["arrangements/lead.json"]["notes"][0].update(t=9),
    lambda m, d: d["arrangements/lead.json"]["notes"][0].update(sus=-1),
    lambda m, d: d["arrangements/lead.json"]["beats"][5].update(time=0.5),
    lambda m, d: m.update(duration=10),
    lambda m, d: m.update(notation="unknown.json"),
    lambda m, d: d["arrangements/lead.json"]["beats"].extend(d["arrangements/lead.json"]["beats"][-3:]),
    lambda m, d: d["arrangements/lead.json"].update(ext={"custom": {"reference": 20}}),
    lambda m, d: d["arrangements/lead.json"]["notes"][0].update(bnv=[{"t": 7.1, "v": 1}]),
    lambda m, d: d["arrangements/lead.json"].update(chords=[{"t": 8, "id": 0, "notes": [{"s": 0, "f": 3, "sus": 1.1}]}]),
    lambda m, d: d["arrangements/lead.json"].update(phrases=[{"start_time": 0, "end_time": 9.25, "levels": [{"difficulty": 1, "notes": [{"t": 8, "s": 0, "f": 3, "sus": 1.1}]}]}]),
    lambda m, d: d["arrangements/lead.json"].update(phrases=[{"start_time": 0, "end_time": 99}]),
    lambda m, d: d.update({"extra.json": {"references": [20]}}),
])
def test_terminal_package_blocks_ambiguous_inputs(tmp_path, modules, mutation):
    service, package, before = build(tmp_path, modules, mutate=mutation)
    preview = service.preview(package.name, RULE)
    assert not preview["available"]
    assert preview["blockers"]
    assert all(read(package, p) == original for p, original in before.items())


def test_corroborating_input_changes_invalidate_preview(tmp_path, modules):
    service, package, _ = build(tmp_path, modules)
    preview = service.preview(package.name, RULE)
    clean = package / "arrangements/bass.json"
    clean.write_bytes(clean.read_bytes() + b" ")
    with pytest.raises(modules[0].RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert len(json.loads(read(package, "arrangements/lead.json"))["beats"]) == 21


@pytest.mark.parametrize("sidecar", [False, True])
def test_every_stored_grid_is_diagnosed_independent_of_precedence(tmp_path, modules, sidecar):
    def mutate(m, d):
        m["arrangements"].reverse()
        if sidecar:
            m["song_timeline"] = "song_timeline.json"
            d["song_timeline.json"] = {"beats": d["arrangements/bass.json"]["beats"], "sections": []}
    _, package, _ = build(tmp_path, modules, mutate=mutate)
    findings = modules[1].validate_feedpak(package)["findings"]
    assert len([f for f in findings if f["code"] == RULE]) == 3


def test_other_stored_timing_errors_remain_manual(tmp_path, modules):
    def mutate(m, d):
        d["arrangements/lead.json"]["beats"][5]["time"] = 0.5
    _, package, _ = build(tmp_path, modules, mutate=mutate)
    assert any(f["code"] == "timeline.stored-beats-invalid" and "lead" in f["location"]
               for f in modules[1].validate_feedpak(package)["findings"])


@pytest.mark.parametrize("rule", ["timeline.duplicate-beat", "timeline.beats-out-of-order"])
def test_generic_rules_cannot_bypass_terminal_gate(tmp_path, modules, rule):
    def mutate(m, d):
        m["arrangements"].reverse()
    service, package, _ = build(tmp_path, modules, mutate=mutate)
    assert not service.preview(package.name, rule)["available"]


def test_combined_repair_obeys_package_gate(tmp_path, modules):
    service, package, _ = build(tmp_path, modules)
    preview = service._plan_all_package(package, package.name, rule_codes=[RULE])
    assert preview["available"] and preview["removed_count"] == 5
    assert "arrangements/bass.json" in preview["terminal_evidence"]
    clean = package / "arrangements/bass.json"
    d = json.loads(clean.read_bytes())
    d["beats"][0]["time"] = 0.01
    clean.write_bytes(raw(d))
    preview = service._plan_all_package(package, package.name, rule_codes=[RULE])
    assert not preview["available"] and preview["blockers"]


def test_implicit_combined_repair_and_undo(tmp_path, modules):
    service, package, before = build(tmp_path, modules)
    preview = service.preview_all(package.name)
    assert preview["available"] and RULE in preview["rule_codes"]
    result = service.apply_all(package.name, preview["plan_id"])
    assert result["applied"]
    assert not service.preview(package.name, RULE)["available"]
    service.restore(package.name, result["backup_id"])
    assert all(read(package, p) == b for p, b in before.items())


@pytest.mark.parametrize("stage", ["source_captured", "before_member_replace"])
def test_clean_grid_edit_during_apply_stops_commit(tmp_path, modules, stage):
    service, package, before = build(tmp_path, modules)
    preview = service.preview(package.name, RULE)
    def barrier(name, context):
        if name == stage:
            (package / "arrangements/bass.json").write_bytes(before["arrangements/bass.json"] + b" ")
    service._transaction_barrier = barrier
    with pytest.raises(modules[0].RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert all(read(package, p) == b for p, b in before.items() if p != "arrangements/bass.json")


def test_directory_failure_rolls_back_every_changed_member(tmp_path, modules):
    service, package, before = build(tmp_path, modules)
    preview = service.preview(package.name, RULE)
    def barrier(name, context):
        if name == "before_member_replace" and context["member_index"] == 2:
            raise OSError("simulated write failure")
    service._transaction_barrier = barrier
    with pytest.raises(modules[0].RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert all(read(package, p) == b for p, b in before.items())


def test_unreadable_backup_prevents_terminal_repair(tmp_path, modules):
    service, package, before = build(tmp_path, modules, archive=True)
    preview = service.preview(package.name, RULE)
    def barrier(name, context):
        if name == "backup_durable":
            path = tmp_path / "config/library_doctor/repair_backups" / (context["backup_id"] + ".zip")
            path.write_bytes(b"damaged backup")
    service._transaction_barrier = barrier
    with pytest.raises(modules[0].RepairPlanningError) as error:
        service.apply(package.name, RULE, preview["plan_id"])
    assert error.value.code == "backup_failed"
    assert all(read(package, p) == b for p, b in before.items())


def test_candidate_validation_failure_preserves_original(tmp_path, modules):
    service, package, before = build(tmp_path, modules)
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
    assert all(read(package, p) == b for p, b in before.items())


def test_member_action_cannot_delete_an_interior_beat(tmp_path, modules):
    repair = modules[0]
    _, documents = members()
    doc = documents["arrangements/lead.json"]
    plan = repair.plan_json_member(raw(doc), member_path="arrangements/lead.json",
                                  source_kind="timeline", validator_version="test", rule_code=RULE)
    from test_measure_marker_repair import _resign
    operation = plan["actions"][0]["operations"][0]
    operation["duplicate_groups"][0]["remove_indices"] = [3]
    _resign(repair, plan)
    with pytest.raises(repair.RepairPlanningError):
        repair.apply_json_member(raw(doc), plan)


def test_sidecar_and_two_entry_tail_are_repaired_together(tmp_path, modules):
    def mutate(m, d):
        m["song_timeline"] = "song_timeline.json"
        beats = copy.deepcopy(d["arrangements/bass.json"]["beats"])
        d["song_timeline.json"] = {"version": 1, "beats": beats + beats[-2:], "sections": []}
    service, package, before = build(tmp_path, modules, mutate=mutate)
    preview = service.preview(package.name, RULE)
    assert preview["available"], preview["blockers"]
    assert preview["removed_count"] == 7 and preview["member_count"] == 4
    result = service.apply(package.name, RULE, preview["plan_id"])
    assert len(json.loads(read(package, "song_timeline.json"))["beats"]) == 20
    service.restore(package.name, result["backup_id"])
    assert all(read(package, p) == b for p, b in before.items())


def test_late_lyric_blocks_repair(tmp_path, modules):
    def mutate(m, d):
        m["lyrics"] = "lyrics.json"
        d["lyrics.json"] = [{"t": 8, "d": 1.1, "w": "last"}]
    service, package, _ = build(tmp_path, modules, mutate=mutate)
    assert not service.preview(package.name, RULE)["available"]


def test_new_undeclared_member_during_apply_stops_commit(tmp_path, modules):
    service, package, before = build(tmp_path, modules)
    preview = service.preview(package.name, RULE)
    def barrier(name, context):
        if name == "before_member_replace":
            (package / "new-reference.json").write_bytes(b'{"index":20}')
    service._transaction_barrier = barrier
    with pytest.raises(modules[0].RepairPlanningError):
        service.apply(package.name, RULE, preview["plan_id"])
    assert all(read(package, p) == b for p, b in before.items())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "9"])
def test_shape_rejects_nonfinite_or_non_numeric_times(modules, value):
    _, d = members()
    beats = d["arrangements/lead.json"]["beats"]
    beats[-1]["time"] = value
    assert modules[0]._terminal.tail_start(beats) is None
