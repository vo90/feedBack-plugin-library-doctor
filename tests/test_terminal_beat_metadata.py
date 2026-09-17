"""Synthetic metadata only: no licensed song or local library paths."""

import copy
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("terminal_metadata_tests", ROOT / "terminal_beat_metadata.py")
METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METADATA)


def fixture():
    pedal = {"Type": "Amps", "Key": "SyntheticAmp", "PedalKey": "SyntheticAmp",
             "KnobValues": {"gain": 40, "enabled": True, "mode": "clean"}}
    tone = {"GearList": {"Amp": pedal}, "Key": "tone-a", "Name": "Tone A",
            "ToneDescriptors": ["clean"], "NameSeparator": " - ", "IsCustom": True,
            "Volume": "-18.250", "SortOrder": 3.0}
    block = {"id": "amp", "role": "amp", "name": "Synthetic Amp",
             "intent": {"kind": "amp", "family": "synthetic", "tags": ["test"],
                        "gm": {"program": 0, "percussion": False, "kit": 0}},
             "params": {"gain": 40.0, "enabled": True, "mode": "clean",
                        "frequency": {"value": 400, "unit": "Hz"}},
             "automation": [{"param": "gain", "points": [{"t": 1.0, "v": 30}, {"t": 2.0, "v": 40}]}],
             "ext": {"source": {"slot": "Amp", "pedal": copy.deepcopy(pedal)}}}
    rig = {"id": "rig-a", "name": "Rig A", "instrument": "guitar", "channels": 1,
           "blocks": [block], "graph": {"nodes": ["input", "amp", "output"],
                                       "edges": [["input", "amp"], ["amp", "output"]]},
           "ext": {"source": {"format": "psarc-tone2014", "tone_id": 0,
                              "definition": copy.deepcopy(tone)}}}
    manifest = {"title": "Synthetic Tail", "artist": "Test", "album": "Synthetic",
                "year": 2000, "feedpak_version": "1.14.0", "duration": 9.25,
                "arrangements": [{"id": "lead", "file": "lead.json", "event_count": 1, "note_count": 1}],
                "stems": [{"id": "full", "file": "full.ogg", "default": True}], "rigs": "rigs.json"}
    document = {"stats": {"events": 1, "notes": 1},
                "ext": {"source": {"format": "psarc-manifest2014", "arrangement_properties": {"pathLead": 1}}},
                "tones": {"base": "tone-a", "base_rig": "rig-a", "definitions": [tone],
                          "changes": [{"t": 3.0, "name": "tone-a", "rig": "rig-a"}]}}
    return manifest, {"lead.json": document, "rigs.json": {"version": 1, "rigs": [rig]}}


def set_path(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def test_recognized_metadata_is_read_only_and_accepts_finite_controls():
    manifest, documents = fixture()
    before = copy.deepcopy((manifest, documents))
    assert METADATA.assess_metadata(manifest, documents, 9.0) is None
    assert (manifest, documents) == before


def test_minimal_metadata_needs_no_legacy_source_tones_or_rigs():
    manifest, documents = fixture()
    manifest.pop("rigs")
    documents = {"lead.json": {}}
    assert METADATA.assess_metadata(manifest, documents, 9.0) is None


@pytest.mark.parametrize("path,replacement", [
    (("title",), {"index": 470}), (("artist",), [470]), (("album",), {"pointer": 470}),
    (("duration",), float("inf")), (("year",), True), (("feedpak_version",), "unknown"),
    (("arrangements", 0, "event_count"), {"index": 470}),
    (("arrangements", 0, "note_count"), -1), (("arrangements", 0, "tuning"), [False]),
    (("arrangements", 0, "centOffset"), float("nan")),
    (("arrangements", 0, "file"), "../other.json"),
    (("stems", 0, "default"), {"index": 470}), (("stems", 0, "file"), ["full.ogg"]),
    (("rigs",), None), (("preview",), {"index": 470}), (("lyrics",), ["lyrics.json"]),
    (("minus_mix",), {"unknown_reference": 470}), (("unknown",), "extension"),
    (("lyric_tracks",), [{"id": "a", "file": "lyrics.json", "language": "en", "kind": {"index": 470}}]),
])
def test_manifest_unknown_or_wrongly_typed_values_are_not_safe(path, replacement):
    manifest, documents = fixture()
    set_path(manifest, path, replacement)
    assert METADATA.assess_metadata(manifest, documents, 9.0)


@pytest.mark.parametrize("path,replacement", [
    (("stats", "events"), {"position": 470}), (("stats", "notes"), [470]),
    (("stats", "notes"), True), (("stats", "notes"), 1.5), (("stats", "other"), 470),
    (("ext", "source", "format"), "unknown"),
    (("ext", "source", "arrangement_properties", "pathLead"), 2),
    (("ext", "source", "arrangement_properties", "hidden"), 470),
    (("tones", "base_rig"), "missing-rig"), (("tones", "base"), {"index": 470}),
    (("tones", "changes", 0, "t"), 9.0), (("tones", "changes", 0, "t"), -1),
    (("tones", "changes", 0, "rig"), "missing-rig"),
    (("tones", "changes", 0, "index"), 470),
    (("tones", "definitions", 0, "Volume"), "NaN"),
    (("tones", "definitions", 0, "SortOrder"), float("inf")),
    (("tones", "definitions", 0, "ToneDescriptors"), [{"index": 470}]),
    (("tones", "definitions", 0, "IsCustom"), "true"),
    (("tones", "definitions", 0, "GearList", "Amp", "KnobValues", "gain"), {"index": 470}),
    (("tones", "definitions", 0, "GearList", "Amp", "KnobValues", "gain"), float("nan")),
    (("tones", "definitions", 0, "GearList", "Amp", "Key"), ["amp"]),
    (("tones", "definitions", 0, "GearList", "UnknownSlot"), {}),
])
def test_arrangement_metadata_unknown_or_ambiguous_values_are_not_safe(path, replacement):
    manifest, documents = fixture()
    set_path(documents["lead.json"], path, replacement)
    assert METADATA.assess_metadata(manifest, documents, 9.0)


@pytest.mark.parametrize("path,replacement", [
    (("version",), 2), (("other",), {"index": 470}),
    (("rigs", 0, "name"), {"position": 470}),
    (("rigs", 0, "ext", "source", "format"), "unknown"),
    (("rigs", 0, "ext", "source", "tone_id"), [470]),
    (("rigs", 0, "ext", "source", "definition", "unknown"), 470),
    (("rigs", 0, "blocks", 0, "params", "gain"), None),
    (("rigs", 0, "blocks", 0, "params", "gain"), float("inf")),
    (("rigs", 0, "blocks", 0, "params", "gain"), {"value": {"index": 470}}),
    (("rigs", 0, "blocks", 0, "params", "gain"), {"value": 1, "unknown": 470}),
    (("rigs", 0, "blocks", 0, "ext", "source", "pedal", "KnobValues", "gain"), [470]),
    (("rigs", 0, "blocks", 0, "intent", "gm", "program"), 128),
    (("rigs", 0, "blocks", 0, "automation", 0, "points", 0, "t"), 9.0),
    (("rigs", 0, "blocks", 0, "automation", 0, "points", 0, "t"), -1),
    (("rigs", 0, "blocks", 0, "automation", 0, "points", 0, "v"), {"index": 470}),
    (("rigs", 0, "blocks", 0, "automation", 0, "points", 0, "index"), 470),
    (("rigs", 0, "blocks", 0, "automation", 0, "points", 1, "t"), 1.0),
    (("rigs", 0, "blocks", 0, "realizations"), [{"engine": "plugin", "state": "opaque"}]),
    (("rigs", 0, "blocks", 0, "realizations"), [{"engine": "unknown"}]),
    (("rigs", 0, "graph", "nodes"), ["input", "amp", "output", "unknown"]),
    (("rigs", 0, "graph", "edges"), [["input", "unknown"]]),
    (("rigs", 0, "graph", "edges"), [["amp", "amp"]]),
    (("rigs", 0, "graph", "unknown"), 470),
])
def test_rig_extensions_values_references_and_automation_fail_closed(path, replacement):
    manifest, documents = fixture()
    set_path(documents["rigs.json"], path, replacement)
    assert METADATA.assess_metadata(manifest, documents, 9.0)


def test_duplicate_rig_and_block_ids_are_not_usable_references():
    manifest, documents = fixture()
    rig = documents["rigs.json"]["rigs"][0]
    documents["rigs.json"]["rigs"].append(copy.deepcopy(rig))
    assert METADATA.assess_metadata(manifest, documents, 9.0)
    documents["rigs.json"]["rigs"].pop()
    rig["blocks"].append(copy.deepcopy(rig["blocks"][0]))
    assert METADATA.assess_metadata(manifest, documents, 9.0)


def test_declared_rigs_and_arrangements_must_be_readable():
    manifest, documents = fixture()
    assert METADATA.assess_metadata(manifest, {"lead.json": {}}, 9.0)
    documents.pop("lead.json")
    assert METADATA.assess_metadata(manifest, documents, 9.0)


def test_manifest_tone_override_is_checked_against_rigs_and_boundary():
    manifest, documents = fixture()
    declaration = manifest["arrangements"][0]
    declaration["tones"] = copy.deepcopy(documents["lead.json"]["tones"])
    assert METADATA.assess_metadata(manifest, documents, 9.0) is None
    declaration["tones"]["changes"][0]["t"] = 9.0
    assert METADATA.assess_metadata(manifest, documents, 9.0)


def test_minus_mix_metadata_is_typed_including_reuse_receipt():
    manifest, documents = fixture()
    digest = "a" * 64
    manifest["minus_mix"] = {
        "generator": "minus_mix", "excluded_stems": ["guitar"], "source_title": "Synthetic",
        "audio_reuse": {"policy": "synthetic-v1", "plan_key": digest, "excluded_stems": ["guitar"],
                        "fresh_package_sha256": digest, "donor_package_sha256": digest,
                        "full_sha256": digest, "preview_sha256": None},
    }
    assert METADATA.assess_metadata(manifest, documents, 9.0) is None
    manifest["minus_mix"]["audio_reuse"]["full_sha256"] = {"index": 470}
    assert METADATA.assess_metadata(manifest, documents, 9.0)


@pytest.mark.parametrize("boundary", [None, True, -1, float("nan"), float("inf"), 10 ** 1000])
def test_metadata_needs_a_finite_usable_boundary(boundary):
    manifest, documents = fixture()
    assert METADATA.assess_metadata(manifest, documents, boundary)


def test_known_realization_scalar_params_and_explicit_units_are_allowed():
    manifest, documents = fixture()
    block = documents["rigs.json"]["rigs"][0]["blocks"][0]
    block["realizations"] = [{"engine": "builtin", "id": "synthetic", "params": {
        "gain": {"value": -3.5, "unit": "dB"}, "enabled": True,
    }}]
    assert METADATA.assess_metadata(manifest, documents, 9.0) is None
