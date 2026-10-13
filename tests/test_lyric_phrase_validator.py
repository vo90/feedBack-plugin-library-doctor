"""Synthetic full-package scan coverage for conditional lyric phrase repair."""
import copy
import json
import zipfile

import pytest

from test_validator import validator as validator, _package, _manifest, _lyrics, _codes


RULE = "lyrics.too-few-line-breaks"


def report_for(tmp_path, validator, primary, *, secondary=None, secondary_path="other.json", path="lyrics.json"):
    files = {path: primary if isinstance(primary, str) else json.dumps(primary)}
    manifest = _manifest(duration=300.0, lyrics=path)
    if secondary is not None:
        manifest["lyric_tracks"] = [{"id": "other", "file": secondary_path,
                                     "language": "und", "kind": "original"}]
        files[secondary_path] = secondary if isinstance(secondary, str) else json.dumps(secondary)
    return validator.validate_feedpak(_package(tmp_path, manifest=manifest, files=files))


def assert_repairability(report, status):
    finding = next(item for item in report["findings"] if item["code"] == RULE)
    assert finding["severity"] == "warning"
    assessment = report["features"]["repair_eligibility"][RULE]
    assert assessment["status"] == status
    assert finding["rule"]["repairability"] == ("safe_candidate" if status == "automatic" else "manual")
    if status == "unavailable":
        assert assessment["reason_code"]
        assert assessment["message"]
        assert finding["rule"]["guidance"] == assessment["message"]
    return assessment


def test_oversized_valid_track_is_visible_as_an_automatic_scan_candidate(tmp_path, validator):
    report = report_for(tmp_path, validator, _lyrics(100))
    assessment = assert_repairability(report, "automatic")
    assert assessment["safe_count"] == 1
    assert assessment["unsafe_count"] == 0
    assert report["validator_version"].startswith("rules-42:")


def test_healthy_authored_tracks_and_empty_track_do_not_offer_phrase_repair(tmp_path, validator):
    healthy = _lyrics(14, breaks={13})
    healthy[-1]["d"] = 13
    report = report_for(tmp_path, validator, healthy, secondary=[])
    assert RULE not in _codes(report)
    assert RULE not in report["features"]["repair_eligibility"]


def test_healthy_secondary_track_does_not_block_affected_primary(tmp_path, validator):
    report = report_for(tmp_path, validator, _lyrics(100), secondary=_lyrics(14, breaks={13}))
    assessment = assert_repairability(report, "automatic")
    assert assessment["reported_count"] == 1


def test_one_ambiguous_affected_track_blocks_whole_package_in_either_order(tmp_path, validator):
    overlap = [{"t": 1.0, "d": 1.0, "w": "synthetic"} for _ in range(100)]
    for index, (primary, secondary) in enumerate([(_lyrics(100), overlap), (overlap, _lyrics(100))]):
        report = report_for(tmp_path / str(index), validator, primary, secondary=secondary)
        assessment = assert_repairability(report, "unavailable")
        assert assessment["safe_count"] == assessment["unsafe_count"] == 1


@pytest.mark.parametrize("mutate", [
    lambda rows: rows.__setitem__(4, {"t": -1, "d": 0.2, "w": "invalid"}),
    lambda rows: rows[4].update(d=-0.1),
    lambda rows: rows[4].update(t=True),
    lambda rows: rows[4].update(d=float("nan")),
    lambda rows: rows[4].update(t=0.5),
    lambda rows: rows[4].update(w=42),
])
def test_malformed_or_unordered_affected_track_is_visible_but_unavailable(tmp_path, validator, mutate):
    lyrics = _lyrics(100)
    mutate(lyrics)
    assert_repairability(report_for(tmp_path, validator, lyrics), "unavailable")


@pytest.mark.parametrize("path, payload", [
    ("lyrics.jsonc", "// preserved comment\n" + json.dumps(_lyrics(100))),
    ("lyrics.txt", json.dumps(_lyrics(100))),
    ("lyrics.json", json.dumps(_lyrics(100)).replace('"t": 1.0', '"t": 0.0, "t": 1.0', 1)),
])
def test_unsupported_or_ambiguous_source_text_never_authorizes_repair(tmp_path, validator, path, payload):
    assert_repairability(report_for(tmp_path, validator, payload, path=path), "unavailable")


@pytest.mark.parametrize("secondary, path", [
    ([{"t": True, "d": 0.2, "w": "short+"}], "other.json"),
    ("{ broken", "other.json"),
    ("// valid but unsupported source\n[]", "other.jsonc"),
    ('[{"t": 0, "t": 1, "d": 0.2, "w": "short+"}]', "other.json"),
])
def test_invalid_secondary_member_blocks_package_candidate_even_without_its_own_size_warning(tmp_path, validator, secondary, path):
    report = report_for(tmp_path, validator, _lyrics(100), secondary=secondary, secondary_path=path)
    assert_repairability(report, "unavailable")


def test_missing_secondary_member_does_not_advertise_an_unusable_package_repair(tmp_path, validator):
    manifest = _manifest(duration=300.0, lyrics="lyrics.json", lyric_tracks=[
        {"id": "missing", "file": "missing.json", "language": "und", "kind": "original"}])
    report = validator.validate_feedpak(_package(tmp_path, manifest=manifest,
                                                files={"lyrics.json": json.dumps(_lyrics(100))}))
    assert "package.missing-file" in _codes(report)
    assert_repairability(report, "unavailable")


def test_duplicate_yaml_properties_block_the_package_repair(tmp_path, validator):
    package = _package(tmp_path, manifest=_manifest(lyrics="lyrics.json"),
                       files={"lyrics.json": json.dumps(_lyrics(100))})
    manifest = package / "manifest.yaml"
    manifest.write_bytes(manifest.read_bytes() + b"duration: 120\n")
    assessment = assert_repairability(validator.validate_feedpak(package), "unavailable")
    assert "Repeated YAML" in assessment["message"]


def test_ambiguous_archive_inventory_blocks_the_package_repair(tmp_path, validator):
    package = _package(tmp_path, manifest=_manifest(lyrics="lyrics.json"),
                       files={"lyrics.json": json.dumps(_lyrics(100))})
    archive_path = tmp_path / "ambiguous.feedpak"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for source in package.rglob("*"):
            if source.is_file():
                archive.write(source, source.relative_to(package).as_posix())
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("lyrics.json", json.dumps(_lyrics(100)))
    assert_repairability(validator.validate_feedpak(archive_path), "unavailable")


def test_malformed_track_collection_blocks_an_affected_primary(tmp_path, validator):
    package = _package(tmp_path, manifest=_manifest(lyrics="lyrics.json", lyric_tracks={"file": "other.json"}),
                       files={"lyrics.json": json.dumps(_lyrics(100))})
    assert_repairability(validator.validate_feedpak(package), "unavailable")


@pytest.mark.parametrize("pointer", [None, 42, "", ".", "./other.json", "other//words.json",
                                    "../other.json", "other.json/", "C:other.json", "other\\words.json"])
def test_shared_lyric_source_gate_rejects_ambiguous_secondary_paths(tmp_path, validator, pointer):
    manifest = _manifest(lyrics="lyrics.json", lyric_tracks=[{"id": "other", "file": pointer}])
    before = copy.deepcopy(manifest)
    assessment = validator.assess_lyric_phrase_sources(manifest)
    assert assessment["status"] == "unavailable"
    assert assessment["member_paths"] == []
    assert manifest == before
    package = _package(tmp_path, manifest=manifest, files={"lyrics.json": json.dumps(_lyrics(100))})
    assert_repairability(validator.validate_feedpak(package), "unavailable")


def test_shared_lyric_source_gate_keeps_exact_paths_and_deduplicates_aliases(validator):
    manifest = {"lyrics": "words/main.json", "lyric_tracks": [
        {"id": "same", "file": "words/main.json"},
        {"id": "other", "file": "words/other.json"},
    ]}
    before = copy.deepcopy(manifest)
    assessment = validator.assess_lyric_phrase_sources(manifest)
    assert assessment["status"] == "automatic"
    assert assessment["member_paths"] == ["words/main.json", "words/other.json"]
    assert manifest == before
    assert validator.assess_lyric_phrase_sources({"lyrics": None, "lyric_tracks": None})["member_paths"] == []


def test_warning_does_not_treat_a_gap_inside_a_held_word_as_a_line_break(tmp_path, validator):
    lyrics = [{"t": 0, "d": 60, "w": "held"},
              {"t": 1, "d": 0.1, "w": "short"},
              {"t": 6, "d": 0.1, "w": "later"}]
    report = report_for(tmp_path, validator, lyrics)
    assert RULE in _codes(report)
    assert_repairability(report, "unavailable")


def test_warning_keeps_hyphenated_word_together_across_long_gaps(tmp_path, validator):
    lyrics = [{"t": i * 5, "d": 0.1, "w": "syllable-"} for i in range(12)]
    lyrics[-1]["w"] = "ending"
    assert_repairability(report_for(tmp_path, validator, lyrics), "unavailable")


def test_unicode_character_threshold_uses_the_same_utf16_count_as_core(tmp_path, validator):
    lyrics = [{"t": i * 0.5, "d": 0.2, "w": "\U0001f3b5" * 20} for i in range(13)]
    lyrics[-1]["w"] += "+"
    report = report_for(tmp_path, validator, lyrics)
    assert_repairability(report, "automatic")
    finding = next(item for item in report["findings"] if item["code"] == RULE)
    assert "520 visible characters" in finding["message"]


def test_assessed_plus_only_repair_clears_warning_and_is_idempotent(tmp_path, validator):
    original = _lyrics(100)
    assessment = validator._lyric_phrases.assess_lyric_phrases(original)
    assert assessment["status"] == "eligible"
    repaired = copy.deepcopy(original)
    for index in assessment["append_plus_indices"]:
        repaired[index]["w"] += "+"
    report = report_for(tmp_path, validator, repaired)
    assert RULE not in _codes(report)
    assert RULE not in report["features"]["repair_eligibility"]
    second = validator._lyric_phrases.assess_lyric_phrases(repaired)
    assert second["status"] == "no_defect"
    assert second["append_plus_indices"] == []
    for before, after in zip(original, repaired):
        assert before["t"] == after["t"] and before["d"] == after["d"]
        assert after["w"] in (before["w"], before["w"] + "+")
