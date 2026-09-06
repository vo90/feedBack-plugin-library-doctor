import copy
import hashlib
import json
import logging
import sys
import zipfile
from importlib import util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).parents[1]


def load(name):
    key = "doctor_source_tests_" + name
    if key not in sys.modules:
        spec = util.spec_from_file_location(key, ROOT / (name + ".py"))
        module = util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def song():
    note = NS(time=10.0, string=1, fret=7, sustain=1.0, mask=0, bends=[NS(time=10.4, step=2)],
        slideTo=-1, slideUnpitchTo=-1, bend_time=2, chordId=4294967295)
    return NS(levels=[NS(difficulty=0, notes=[note]), NS(difficulty=1, notes=[copy.deepcopy(note)])],
              phraseIterations=[NS(time=0, endTime=20, phraseId=0)], phrases=[NS(maxDifficulty=1)],
              chordTemplates=[], chordNotes=[], metadata=NS(capo=-1, tuning=[0] * 6))


def legacy_chart(source):
    doc = load("source_chart").source_document(source)
    def clean(value):
        if isinstance(value, list):
            return [clean(x) for x in value]
        if not isinstance(value, dict):
            return value
        result = {k: clean(v) for k, v in value.items() if not k.startswith("_")}
        if value.get("_declared_peak"):
            result["bn"] = value["_declared_peak"]
        return result
    return clean(doc)


def test_single_source_target_recovered_in_every_difficulty_without_release():
    chart = load("source_chart")
    source = song()
    doc = legacy_chart(source)
    before = copy.deepcopy(doc)
    fixed, changes, excluded = chart.recovery_patch(doc, source)
    assert doc == before and len(changes) == 3 and not excluded
    assert fixed["notes"][0]["bnv"] == [{"t": 0.0, "v": 0.0}, {"t": .4, "v": 2.0}]
    assert chart.recovery_patch(fixed, source)[1] == []


def test_existing_curve_edit_excludes_every_copy_of_same_note():
    source = song()
    doc = legacy_chart(source)
    doc["notes"][0]["bnv"] = [{"t": 0, "v": 1}, {"t": .7, "v": 2}]
    fixed, changes, excluded = load("source_chart").recovery_patch(doc, source)
    assert fixed == doc and not changes and len(excluded) == 3


def test_scalar_with_no_authored_source_points_is_never_reconstructed():
    source = song()
    for level in source.levels:
        level.notes[0].bends = []
    doc = legacy_chart(source)
    fixed, changes, _ = load("source_chart").recovery_patch(doc, source)
    assert fixed == doc and not changes


def test_pre_onset_source_curve_is_clipped_only_with_explicit_source_evidence():
    source = song()
    for level in source.levels:
        level.notes[0].bends = [NS(time=9.9, step=0), NS(time=10.1, step=2)]
    doc = legacy_chart(source)
    for container in [doc, *doc["phrases"][0]["levels"]]:
        container["notes"][0]["bnv"] = [{"t": 9.9, "v": 0}, {"t": 10.1, "v": 2}]
    fixed, changes, excluded = load("source_chart").recovery_patch(doc, source)
    assert not excluded and len(changes) == 3
    assert changes[0]["adjustments"] == ["pre-onset"]
    assert fixed["notes"][0]["bnv"][0] == {"t": 0.0, "v": 1.0}


@pytest.mark.parametrize("mutate", [lambda d: d["notes"][0].update(f=8),
    lambda d: d["phrases"][0]["levels"][0]["notes"][0].update(sus=.5),
    lambda d: d.update(tuning=[-2] * 6), lambda d: d["notes"][0].update(hm=True)])
def test_wrong_version_or_arrangement_topology_cannot_match(mutate):
    source = song()
    doc = legacy_chart(source)
    mutate(doc)
    with pytest.raises(ValueError, match="match"):
        load("source_chart").recovery_patch(doc, source)


def build(tmp_path, *, archive=True):
    repair = load("repair")
    source = song()
    original = tmp_path / "Original.psarc"
    original.write_bytes(b"immutable selected source")
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    entries = [{"member": "songs/bin/generic/lead.sng", "sha256": "1" * 64, "song": source}]
    adapter = NS(read_source_charts=lambda value: {"path": Path(value), "sha256": digest(value), "charts": entries}, source_hash=digest)
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    data = json.dumps(legacy_chart(source)).encode()
    members = {"manifest.yaml": b"arrangements:\n  - id: lead\n    file: lead.json\n  - id: rhythm\n    file: rhythm.json\n",
        "lead.json": data, "rhythm.json": data, "audio.ogg": b"different No Guitar audio stays unchanged"}
    if archive:
        with zipfile.ZipFile(package, "w") as target:
            for name, value in members.items():
                target.writestr(name, value)
    else:
        package.mkdir()
        for name, value in members.items():
            (package / name).write_bytes(value)
    def validate(path, name, **kw):
        return {"title": "Song", "validator_version": "test", "findings": [],
            "counts": {"error": 0, "warning": 0, "info": 0}}
    service = repair.RepairService(config_dir=tmp_path / "config", get_dlc_dir=lambda: library,
        validate_feedpak=validate, validator_version="test", log=logging.getLogger("source-test"))
    engine = load("source_recovery").SourceRecovery(repair=service, repair_module=repair,
        archive=adapter, chart=load("source_chart"))
    return repair, service, engine, package, original, members, entries


def read_all(package):
    if package.is_dir():
        return {p.name: p.read_bytes() for p in package.iterdir()}
    with zipfile.ZipFile(package) as src:
        return {name: src.read(name) for name in src.namelist()}


@pytest.mark.parametrize("archive", [True, False])
def test_full_candidate_apply_exact_undo_and_repeat(tmp_path, archive):
    _, service, engine, package, original, members, _ = build(tmp_path, archive=archive)
    preview = engine.preview(package.name, str(original))
    assert preview["available"] and preview["candidate_validated"]
    assert preview["change_count"] == 6
    assert read_all(package) == members
    receipt = engine.apply(package.name, str(original), preview["plan_id"])
    assert receipt["applied"] and receipt["undo_available"]
    assert read_all(package)["audio.ogg"] == members["audio.ogg"]
    assert not engine.preview(package.name, str(original))["available"]
    service.restore(package.name, receipt["backup_id"])
    assert read_all(package) == members


def test_duplicate_source_arrangements_block_without_guessing(tmp_path):
    _, _, engine, package, original, members, entries = build(tmp_path)
    entries.append(copy.deepcopy(entries[0]))
    preview = engine.preview(package.name, str(original))
    assert not preview["available"]
    assert preview["blockers"][0]["code"] == "source_match_ambiguous"
    assert read_all(package) == members


@pytest.mark.parametrize("stage", ["after_preview", "candidate_validated", "source_guarded"])
def test_changed_original_source_stops_commit(tmp_path, stage):
    repair, service, engine, package, original, members, _ = build(tmp_path)
    preview = engine.preview(package.name, str(original))
    def barrier(name, context):
        if name == stage:
            original.write_bytes(b"changed selected source")
    service._transaction_barrier = barrier
    if stage == "after_preview":
        original.write_bytes(b"changed selected source")
    with pytest.raises(repair.RepairPlanningError) as error:
        engine.apply(package.name, str(original), preview["plan_id"])
    assert error.value.code == "source_changed"
    assert read_all(package) == members


def test_interrupted_directory_write_rolls_back_all_members(tmp_path):
    repair, service, engine, package, original, members, _ = build(tmp_path, archive=False)
    preview = engine.preview(package.name, str(original))
    def barrier(name, context):
        if name == "before_member_replace" and context["member_index"] == 1:
            raise OSError("injected interruption")
    service._transaction_barrier = barrier
    with pytest.raises(repair.RepairPlanningError):
        engine.apply(package.name, str(original), preview["plan_id"])
    assert read_all(package) == members


def test_bounded_reader_rejects_large_table_before_parser_allocation(tmp_path):
    pytest.importorskip("construct")
    pytest.importorskip("cryptography")
    path = tmp_path / "Bad.psarc"
    path.write_bytes(b"PSAR" + b"\xff" * 28)
    with pytest.raises(ValueError, match="table"):
        load("source_archive").read_source_charts(path)


def test_excluded_existing_curves_do_not_hang_or_overwrite(tmp_path):
    _, _, engine, package, original, members, _ = build(tmp_path, archive=False)
    document = json.loads(members["lead.json"])
    document["notes"][0]["bnv"] = [{"t": .1, "v": 1}]
    (package / "lead.json").write_text(json.dumps(document))
    before = read_all(package)
    preview = engine.preview(package.name, str(original))
    assert preview["available"] and preview["excluded_count"] == 3
    assert preview["change_count"] == 3
    engine.apply(package.name, str(original), preview["plan_id"])
    assert read_all(package)["lead.json"] == before["lead.json"]


def test_explicit_vocal_arrangement_is_preserved_without_sng_matching(tmp_path):
    _, _, engine, package, original, _, _ = build(tmp_path, archive=False)
    manifest = package / "manifest.yaml"
    manifest.write_bytes(manifest.read_bytes() + b"  - id: vocals\n    type: vocals\n    file: vocals.json\n")
    (package / "vocals.json").write_bytes(b'{"cues":[]}')
    preview = engine.preview(package.name, str(original))
    assert preview["available"] and not preview["blockers"]
    engine.apply(package.name, str(original), preview["plan_id"])
    assert (package / "vocals.json").read_bytes() == b'{"cues":[]}'


@pytest.mark.parametrize("pointer", ["./unresolved.json", "../outside.json", "", "missing.json"])
def test_unresolved_playable_declaration_blocks_complete_package(tmp_path, pointer):
    repair, _, engine, package, original, _, _ = build(tmp_path, archive=False)
    manifest = package / "manifest.yaml"
    manifest.write_text(manifest.read_text() + f"  - id: other\n    type: guitar\n    file: '{pointer}'\n")
    before = read_all(package)
    with pytest.raises(repair.RepairPlanningError):
        engine.preview(package.name, str(original))
    assert read_all(package) == before


def test_complete_candidate_validation_error_prevents_preview(tmp_path):
    repair, service, engine, package, original, members, _ = build(tmp_path)
    validate = service._validate_feedpak
    def validation(path, name, **options):
        report = validate(path, name, **options)
        if Path(path) != package:
            report["findings"] = [{"severity": "error", "code": "test.new-error"}]
            report["counts"]["error"] = 1
        return report
    service._validate_feedpak = validation
    with pytest.raises(repair.RepairPlanningError):
        engine.preview(package.name, str(original))
    assert read_all(package) == members


def test_standalone_release_includes_reader_license_and_exact_dependencies(tmp_path):
    name = "doctor_source_release_test"
    spec = util.spec_from_file_location(name, ROOT / "tools/build_release.py")
    release = util.module_from_spec(spec)
    spec.loader.exec_module(release)
    archive = release.build_release(tmp_path / "doctor.zip")
    release.verify_release(archive)
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        for path in ("source_archive.py", "source_chart.py", "source_recovery.py", "source_bend_curves.py",
                     "source_psarc/sng.py", "source_psarc/crypto.py", "source_psarc/LICENSE",
                     "src/source-recovery-tool.js"):
            assert f"feedBack-plugin-library-doctor/{path}" in names
        requirements = source.read("feedBack-plugin-library-doctor/requirements.txt").decode()
        assert "construct==2.10.70" in requirements and "cryptography==50.0.0" in requirements
