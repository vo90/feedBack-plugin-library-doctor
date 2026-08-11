import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture(scope="module")
def validator():
    path = (
        Path(__file__).parents[1]
        / "validator.py"
    )
    name = "library_doctor_validator_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _manifest(**overrides):
    data = {
        "feedpak_version": "1.19.0",
        "title": "Test Song",
        "artist": "Test Artist",
        "duration": 60.0,
        "arrangements": [
            {"id": "lead", "name": "Lead", "file": "arrangements/lead.json"}
        ],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    data.update(overrides)
    return data


def _package(tmp_path, *, manifest=None, arrangement=None, files=None, name="song.feedpak"):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest or _manifest(), sort_keys=False), encoding="utf-8"
    )
    arrangement_path = root / "arrangements" / "lead.json"
    arrangement_path.parent.mkdir(parents=True)
    arrangement_path.write_text(
        json.dumps(arrangement or {"notes": [], "chords": []}), encoding="utf-8"
    )
    stem_path = root / "stems" / "full.ogg"
    stem_path.parent.mkdir(parents=True)
    stem_path.write_bytes(b"full mix audio")
    for relpath, content in (files or {}).items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return root


def _codes(report):
    return [finding["code"] for finding in report["findings"]]


def _lyrics(count, *, breaks=(), fallback_gap_after=None):
    result = []
    current_time = 1.0
    for index in range(count):
        result.append({
            "t": current_time,
            "d": 0.1,
            "w": "la+" if index in breaks else "la",
        })
        current_time += 0.2
        if index == fallback_gap_after:
            current_time += 4.1
    return result


def _vorbis_ogg(*, duration=60.0, serial=1, payload=b"encoded audio"):
    sample_rate = 48_000
    body = (
        b"\x01vorbis"
        + (0).to_bytes(4, "little")
        + b"\x02"
        + sample_rate.to_bytes(4, "little")
        + b"\x00" * 12
        + payload
    )
    assert len(body) < 255
    header = (
        b"OggS"
        + b"\x00\x06"
        + round(duration * sample_rate).to_bytes(8, "little")
        + serial.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (serial * 17).to_bytes(4, "little")
        + b"\x01"
    )
    return header + bytes([len(body)]) + body


def test_structural_json_key_preserves_exact_json_comparison_semantics(validator):
    left = {"b": [True, 1, 1.0, None], "a": {"value": "x"}}
    reordered = {"a": {"value": "x"}, "b": [True, 1, 1.0, None]}

    assert validator._exact_json_key(left) == validator._exact_json_key(reordered)
    assert validator._exact_json_key(True) != validator._exact_json_key(1)
    assert validator._exact_json_key(1) != validator._exact_json_key(1.0)
    assert validator._exact_json_key(-0.0) != validator._exact_json_key(0.0)
    assert validator._exact_json_key([1, 2]) != validator._exact_json_key([2, 1])
    assert validator._exact_json_key(float("nan")) is None
    assert validator._exact_json_key(
        float("nan"), allow_nonfinite=True
    ) == ("float", "nan")


def test_preview_duration_probe_reuses_bounded_ogg_container_parser(validator):
    source = _vorbis_ogg(duration=31.25)

    assert validator.probe_ogg_duration(source) == pytest.approx(31.25)
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        validator.probe_ogg_duration(b"not an ogg stream")


def test_valid_minimal_package_is_healthy_without_optional_media(tmp_path, validator):
    report = validator.validate_feedpak(_package(tmp_path), "song.feedpak")

    assert report["status"] == "healthy"
    assert report["package"] == "song.feedpak"
    assert report["title"] == "Test Song"
    assert report["counts"] == {"error": 0, "warning": 0, "info": 0}
    assert report["features"] == {
        "lyrics_declared": False,
        "lyrics_entries": 0,
        "preview_declared": False,
        "preview_available": False,
        "preview_source_available": False,
        "repair_eligibility": {
            "media.preview-missing": {
                "status": "unavailable",
                "reason_code": "full_mix_unavailable",
                "message": (
                    "Automatic preview creation needs one unambiguous "
                    "manifest-declared Ogg full mix of usable size. This "
                    "package does not provide one."
                ),
            },
            "media.preview-too-short": {
                "status": "unavailable",
                "reason_code": "full_mix_unavailable",
                "message": (
                    "Automatic preview creation needs one unambiguous "
                    "manifest-declared Ogg full mix of usable size. This "
                    "package does not provide one."
                ),
            },
            "media.preview-too-long": {
                "status": "unavailable",
                "reason_code": "full_mix_unavailable",
                "message": (
                    "Automatic preview creation needs one unambiguous "
                    "manifest-declared Ogg full mix of usable size. This "
                    "package does not provide one."
                ),
            },
        },
        "deep_audio_checked": False,
        "deep_audio_files": 0,
        "deep_audio_skipped": 0,
        "deep_audio_unsupported": 0,
    }


def test_findings_include_user_facing_metadata_and_structured_evidence(
    tmp_path, validator
):
    arrangement = {
        "notes": [
            {"t": 5.0, "s": 1, "f": 3},
            {"t": 5.0, "s": 1, "f": 3},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    finding = next(
        item for item in report["findings"] if item["code"] == "chart.duplicate-note"
    )

    assert finding["rule"] == {
        "title": "Identical duplicate note",
        "area": "Tab",
        "confidence": "high",
        "repairability": "safe_candidate",
        "guidance": (
            "The repeated entries appear identical. Keep one copy in the source "
            "and scan it again."
        ),
        "player_impact": (
            "FeedBack may process or draw the same gem more than once at one position, "
            "even though the copies look like a single note."
        ),
        "fix_benefit": (
            "One intended note remains with the same timing and techniques, removing "
            "redundant chart data and preventing duplicate-note behavior."
        ),
    }
    assert finding["affected_count"] == 1
    assert finding["evidence"]["time"] == 5.0
    assert finding["evidence"]["string"] == 1


@pytest.mark.parametrize(
    ("code", "severity", "category"),
    [
        ("chart.fret-beyond-highway", "warning", "feedback_compatibility"),
        ("review.extreme-chord-span", "info", "authoring_review"),
        ("lyrics.after-duration", "warning", "validation"),
        ("package.missing-file", "error", "validation"),
        ("future.unknown-check", "warning", "validation"),
    ],
)
def test_every_rule_metadata_path_explains_player_impact_and_fix_value(
    validator, code, severity, category
):
    metadata = validator.rule_metadata(code, severity, category)

    assert metadata["player_impact"].strip()
    assert metadata["fix_benefit"].strip()


def test_missing_manifest_pointer_is_an_error(tmp_path, validator):
    manifest = _manifest(
        arrangements=[{"id": "lead", "file": "arrangements/missing.json"}]
    )
    report = validator.validate_feedpak(_package(tmp_path, manifest=manifest))

    assert report["status"] == "error"
    assert "package.missing-file" in _codes(report)


def test_schema_errors_are_reported_with_a_location(tmp_path, validator):
    manifest = _manifest(duration="one minute")
    report = validator.validate_feedpak(_package(tmp_path, manifest=manifest))

    finding = next(item for item in report["findings"] if item["code"] == "spec.schema")
    assert "manifest.yaml:duration" in finding["location"]


def test_schema_error_reporting_is_bounded_per_file(tmp_path, validator):
    arrangement = {"notes": [{} for _index in range(150)], "chords": []}

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert report["counts"]["error"] == validator.MAX_SCHEMA_ERRORS_PER_FILE
    assert "spec.schema-errors-truncated" in _codes(report)


def test_standalone_note_that_exactly_matches_a_chord_member_is_repairable(tmp_path, validator):
    arrangement = {
        "notes": [{"t": 10.0, "s": 2, "f": 7}],
        "chords": [{"t": 10.0, "id": 0, "notes": [{"s": 2, "f": 7}]}],
        "templates": [{
            "frets": [-1, -1, 7, -1, -1, -1],
            "fingers": [-1, -1, 1, -1, -1, -1],
        }],
    }
    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.note-duplicates-chord"
    )
    assert report["status"] == "warning"
    assert finding["rule"]["repairability"] == "safe_candidate"
    assert finding["affected_count"] == 1
    assert "complete chord" in finding["rule"]["guidance"]


def test_standalone_note_and_chord_member_with_different_properties_are_preserved(
    tmp_path, validator,
):
    arrangement = {
        "notes": [{"t": 10.0, "s": 2, "f": 7, "sus": 0.5}],
        "chords": [{"t": 10.0, "id": 0, "notes": [{"s": 2, "f": 7}]}],
        "templates": [{
            "frets": [-1, -1, 7, -1, -1, -1],
            "fingers": [-1, -1, 1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.note-duplicates-chord" not in _codes(report)


def test_standalone_note_with_multiple_matching_chords_is_not_auto_repairable(
    tmp_path, validator,
):
    arrangement = {
        "notes": [{"t": 10.0, "s": 2, "f": 7}],
        "chords": [
            {"t": 10.0, "id": 0, "notes": [{"s": 2, "f": 7}]},
            {"t": 10.0, "id": 0, "notes": [{"s": 2, "f": 7}]},
        ],
        "templates": [{
            "frets": [-1, -1, 7, -1, -1, -1],
            "fingers": [-1, -1, 1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.duplicate-chord" in _codes(report)
    assert "chart.coincident-chords" not in _codes(report)
    assert "chart.note-duplicates-chord" not in _codes(report)


def test_exact_duplicate_chord_data_has_dedicated_safe_findings(
    tmp_path, validator,
):
    chord_note = {"s": 0, "f": 3, "sus": 0.5, "future": {"x": 1}}
    chord = {
        "t": 10.0,
        "id": 0,
        "notes": [chord_note, dict(chord_note)],
    }
    anchor = {"time": 10.0, "fret": 3, "width": 4}
    handshape = {"chord_id": 0, "start_time": 10.0, "end_time": 11.0}
    arrangement = {
        "notes": [],
        "chords": [chord, json.loads(json.dumps(chord))],
        "anchors": [anchor, dict(anchor)],
        "handshapes": [handshape, dict(handshape)],
        "templates": [{
            "frets": [3, -1, -1, -1, -1, -1],
            "fingers": [1, -1, -1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    findings = {
        finding["code"]: finding
        for finding in report["findings"]
    }

    expected = {
        "chart.duplicate-chord-note",
        "chart.duplicate-chord",
        "chart.duplicate-anchor",
        "chart.duplicate-handshape",
    }
    assert expected.issubset(findings)
    assert all(findings[code]["rule"]["repairability"] == "safe_candidate" for code in expected)
    assert all(findings[code]["severity"] == "warning" for code in expected)
    assert "chart.chord-string-duplicate" not in findings
    assert "chart.coincident-chords" not in findings


def test_nonidentical_same_string_chord_members_remain_review_only(
    tmp_path, validator,
):
    arrangement = {
        "notes": [],
        "chords": [{
            "t": 10.0,
            "id": 0,
            "notes": [
                {"s": 0, "f": 3, "sus": 0.5},
                {"s": 0, "f": 3, "sus": 1.0},
            ],
        }],
        "templates": [{
            "frets": [3, -1, -1, -1, -1, -1],
            "fingers": [1, -1, -1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.chord-string-duplicate" in _codes(report)
    assert "chart.duplicate-chord-note" not in _codes(report)


def test_duplicate_members_in_a_chord_without_valid_timing_are_not_safe_candidates(
    tmp_path, validator,
):
    member = {"s": 0, "f": 3}
    arrangement = {
        "notes": [],
        "chords": [{"id": 0, "notes": [member, dict(member)]}],
        "templates": [{
            "frets": [3, -1, -1, -1, -1, -1],
            "fingers": [1, -1, -1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "spec.schema" in _codes(report)
    assert "chart.duplicate-chord-note" not in _codes(report)


def test_nearby_note_and_chord_times_are_not_an_exact_repair_candidate(
    tmp_path, validator,
):
    arrangement = {
        "notes": [{"t": 10.0, "s": 2, "f": 7}],
        "chords": [{"t": 10.0000001, "id": 0, "notes": [{"s": 2, "f": 7}]}],
        "templates": [{
            "frets": [-1, -1, 7, -1, -1, -1],
            "fingers": [-1, -1, 1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.note-duplicates-chord" not in _codes(report)


def test_tab_bounds_order_sustain_and_technique_types_are_checked(tmp_path, validator):
    arrangement = {
        "tuning": [0] * 6,
        "notes": [
            {"t": 2.0, "s": 8, "f": 25, "sus": -1.0, "ho": "false"},
            {"t": 1.0, "s": 0, "f": -1},
        ],
        "chords": [],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "name": "Lead", "type": "guitar",
        "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert {
        "chart.notes-out-of-order",
        "chart.negative-fret",
        "chart.fret-beyond-highway",
        "chart.string-beyond-highway",
        "chart.string-without-tuning",
        "chart.negative-sustain",
        "chart.technique-not-boolean",
    }.issubset(_codes(report))
    finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.string-beyond-highway"
    )
    assert finding["arrangement_id"] == "lead"
    assert finding["time"] == 2.0
    assert finding["string"] == 8
    assert finding["category"] == "feedback_compatibility"


def test_only_exact_string_mutes_make_negative_frets_safe_to_normalize(
    tmp_path, validator,
):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": -1, "mt": True},
            {"t": 2.0, "s": 1, "f": -1, "fhm": True},
            {"t": 3.0, "s": 2, "f": -1, "pm": True},
            {"t": 4.0, "s": 3, "f": -2, "mt": True},
            {"t": 5.0, "s": 4, "f": -1, "mt": "true"},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    muted_negative = next(
        item for item in report["findings"]
        if item["code"] == "chart.negative-muted-fret"
    )
    negative = next(
        item for item in report["findings"]
        if item["code"] == "chart.negative-fret"
    )

    assert muted_negative["severity"] == "warning"
    assert muted_negative["affected_count"] == 2
    assert "pitchless mute behavior" in muted_negative["message"]
    assert negative["affected_count"] == 3
    assert "exact string-mute flag" in negative["message"]
    assert "chart.technique-not-boolean" in _codes(report)


def test_slide_and_bend_curves_are_checked_against_highway_behavior(tmp_path, validator):
    arrangement = {
        "notes": [
            {
                "t": 1.0, "s": 0, "f": 0, "sus": 0,
                "sl": 30, "slu": 3, "bn": 0.5,
                "bnv": [{"t": 1.0, "v": 1.0}, {"t": 0.0, "v": 0.0}],
            },
            {"t": 2.0, "s": 1, "f": 3, "sus": 0.5, "sl": 3},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert {
        "chart.ambiguous-slide",
        "chart.slide-beyond-highway",
        "chart.slide-without-sustain",
        "chart.open-string-slide",
        "review.same-fret-slide",
        "chart.bend-points-out-of-order",
        "chart.bend-point-outside-sustain",
        "chart.bend-exceeds-peak",
    }.issubset(_codes(report))
    bend_finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.bend-points-out-of-order"
    )
    assert bend_finding["rule"]["title"] == "Bend points out of order"
    assert bend_finding["rule"]["repairability"] == "safe_candidate"
    assert bend_finding["rule"]["guidance"] == (
        "Put the existing bend points in chronological order. Preserve every "
        "point and keep equal-time points in their authored order."
    )
    assert "without deleting or inventing any points" in (
        bend_finding["rule"]["fix_benefit"]
    )


def test_isolated_same_fret_slide_requests_authoring_review(tmp_path, validator):
    arrangement = {
        "notes": [{"t": 1.0, "s": 1, "f": 7, "sus": 0.5, "sl": 7}],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    finding = next(
        item for item in report["findings"]
        if item["code"] == "review.same-fret-slide"
    )
    assert "chart.no-op-slide" not in _codes(report)
    assert finding["severity"] == "info"
    assert finding["category"] == "authoring_review"
    assert finding["rule"]["repairability"] == "manual"
    assert finding["rule"]["confidence"] == "medium"
    assert "without a linked slide or partial chord-slide context" in finding["message"]


@pytest.mark.parametrize(
    "notes",
    [
        # A held leg flowing into a real outgoing slide, as used by the B'z
        # chart that motivated the contextual rule.
        [
            {"t": 1.0, "s": 0, "f": 14, "sus": 0.2, "sl": 14},
            {"t": 1.2, "s": 0, "f": 14, "sus": 0.3, "sl": 12},
        ],
        # A stationary leg immediately after a real incoming slide.
        [
            {"t": 1.0, "s": 0, "f": 12, "sus": 0.5, "sl": 14},
            {"t": 1.5, "s": 0, "f": 14, "sus": 0.25, "sl": 14},
        ],
        # An explicitly linked same-fret continuation.
        [
            {"t": 1.0, "s": 0, "f": 14, "sus": 0.2, "sl": 14, "ln": True},
            {"t": 1.2, "s": 0, "f": 14, "sus": 0.3},
        ],
    ],
)
def test_linked_same_fret_slide_legs_are_not_reported(tmp_path, validator, notes):
    report = validator.validate_feedpak(_package(
        tmp_path,
        arrangement={"notes": notes, "chords": []},
    ))

    assert "review.same-fret-slide" not in _codes(report)


def test_stationary_member_of_partial_chord_slide_is_not_reported(
    tmp_path, validator,
):
    arrangement = {
        "notes": [],
        "chords": [{
            "t": 1.0,
            "notes": [
                {"s": 1, "f": 5, "sus": 0.5, "sl": 7},
                {"s": 2, "f": 7, "sus": 0.5, "sl": 7},
            ],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "review.same-fret-slide" not in _codes(report)


def test_slide_away_and_back_uses_two_real_legs_without_review(tmp_path, validator):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 14, "sus": 0.5, "sl": 12},
            {"t": 1.5, "s": 0, "f": 12, "sus": 0.5, "sl": 14},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "review.same-fret-slide" not in _codes(report)


def test_invalid_slide_and_negative_bend_values_are_errors(tmp_path, validator):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sl": -2, "bn": -0.5},
            {"t": 2.0, "s": 1, "f": 5, "slu": -3, "bnv": [{"t": 0.0, "v": -1.0}]},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert report["status"] == "error"
    assert {"chart.invalid-slide-target", "chart.negative-bend"}.issubset(_codes(report))


def test_bend_rounding_tolerance_and_harmonic_pair_are_supported(tmp_path, validator):
    arrangement = {
        "notes": [{
            "t": 1.0, "s": 0, "f": 3, "sus": 1.0,
            "hm": True, "hp": True,
            "bn": 1.0, "bnv": [{"t": 1.004, "v": 1.0}],
        }],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.conflicting-techniques" not in _codes(report)
    assert "chart.bend-point-outside-sustain" not in _codes(report)


def test_capo_tuning_and_tempo_highway_limits_are_checked(tmp_path, validator):
    arrangement = {
        "tuning": [0] * 9,
        "capo": 25,
        "notes": [],
        "chords": [],
        "tempos": [
            {"time": 10.0, "bpm": 90},
            {"time": 2.0, "bpm": 100},
            {"time": 2.0, "bpm": 120},
        ],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert {
        "chart.capo-beyond-highway",
        "chart.tuning-beyond-highway",
        "timeline.tempos-out-of-order",
        "timeline.conflicting-tempos",
    }.issubset(_codes(report))
    tempo_order = next(
        finding for finding in report["findings"]
        if finding["code"] == "timeline.tempos-out-of-order"
    )
    assert tempo_order["severity"] == "warning"


def test_numerically_equivalent_timeline_values_do_not_conflict(
    tmp_path, validator
):
    timeline = {
        "version": 1,
        "beats": [],
        "sections": [],
        "tempos": [
            {"time": 0.0, "bpm": 120},
            {"time": 0.0, "bpm": 120.0},
        ],
    }
    manifest = _manifest(song_timeline="timeline.json")

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"timeline.json": json.dumps(timeline)},
    ))

    assert "timeline.conflicting-tempos" not in _codes(report)


def test_chord_template_references_and_shapes_are_checked(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [
            {"t": 1.0, "id": 8, "notes": []},
            {"t": 2.0, "id": 0, "notes": [{"s": 0, "f": 5}]},
        ],
        "templates": [{"frets": [3], "fingers": [5, 1]}],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert {
        "chart.missing-chord-template",
        "chart.invisible-chord",
        "chart.chord-template-mismatch",
        "chart.invalid-template-finger",
        "chart.template-array-mismatch",
    }.issubset(_codes(report))


def test_template_mute_mismatch_and_more_than_eight_strings_are_checked(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [{"t": 1.0, "id": 0, "notes": [{"s": 0, "f": 3}]}],
        "templates": [{"frets": [-1, 3, 3, 3, 3, 3, 3, 3, 3], "fingers": [-1] * 9}],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert "chart.chord-template-mismatch" in _codes(report)
    assert "chart.template-beyond-highway" in _codes(report)


def test_authoring_review_separates_impossible_fingering_and_wide_chords(
    tmp_path, validator
):
    arrangement = {
        "notes": [],
        "chords": [{"t": 1.0, "id": 0, "notes": []}],
        "templates": [{"frets": [1, 9, -1, -1, -1, -1], "fingers": [1, 1, -1, -1, -1, -1]}],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert report["status"] == "review"
    assert report["counts"] == {"error": 0, "warning": 0, "info": 2}
    assert {
        "review.impossible-chord-fingering",
        "review.extreme-chord-span",
    }.issubset(_codes(report))
    assert all(
        finding["category"] == "authoring_review"
        for finding in report["findings"]
    )


def test_open_or_unassigned_finger_zero_is_not_impossible_fingering(
    tmp_path, validator
):
    arrangement = {
        "notes": [],
        "chords": [],
        "templates": [{
            "frets": [3, 5, -1, -1, -1, -1],
            "fingers": [0, 0, -1, -1, -1, -1],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "review.impossible-chord-fingering" not in _codes(report)


def test_barres_open_strings_and_seven_fret_spans_are_not_review_findings(
    tmp_path, validator
):
    arrangement = {
        "notes": [],
        "chords": [{"t": 1.0, "id": 0, "notes": []}],
        "templates": [{"frets": [0, 1, 8, 8, -1, -1], "fingers": [-1, 1, 2, 2, -1, -1]}],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert not any(code.startswith("review.") for code in _codes(report))
    assert report["status"] == "healthy"


def test_template_only_chord_times_are_checked(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [
            {"t": -1.0, "id": 0, "notes": []},
            {"t": 66.0, "id": 0, "notes": []},
        ],
        "templates": [{"frets": [3, -1, -1, -1, -1, -1], "fingers": [1, -1, -1, -1, -1, -1]}],
    }
    manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, arrangement=arrangement,
    ))

    assert {"chart.negative-time", "chart.event-after-duration"}.issubset(_codes(report))


def test_anchor_and_handshape_geometry_is_checked(tmp_path, validator):
    arrangement = {
        "notes": [{"t": 1.0, "s": 0, "f": 3}],
        "chords": [],
        "templates": [{"frets": [3], "fingers": [1]}],
        "anchors": [
            {"time": 2.0, "fret": 23, "width": 3},
            {"time": 1.0, "fret": 2, "width": 4},
            {"time": 1.0, "fret": 4, "width": 4},
            {"time": 3.0, "fret": 1, "width": 0},
        ],
        "handshapes": [
            {"chord_id": 9, "start_time": 4.0, "end_time": 4.0},
            {"chord_id": 0, "start_time": 8.0, "end_time": 7.0},
            {"chord_id": 0, "start_time": 59.0, "end_time": 61.0},
        ],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert {
        "chart.anchors-out-of-order",
        "chart.anchor-beyond-highway",
        "chart.conflicting-anchors",
        "chart.invalid-anchor",
        "chart.invalid-handshape-span",
        "chart.zero-length-handshape",
        "chart.missing-handshape-template",
        "chart.handshape-after-duration",
    }.issubset(_codes(report))
    severities = {item["code"]: item["severity"] for item in report["findings"]}
    assert severities["chart.zero-length-handshape"] == "warning"
    assert severities["chart.invalid-handshape-span"] == "error"
    invalid_span = next(
        item for item in report["findings"]
        if item["code"] == "chart.invalid-handshape-span"
    )
    assert invalid_span["rule"]["repairability"] == "review_required"
    assert "authoring data" in invalid_span["rule"]["guidance"]
    zero_length = next(
        item for item in report["findings"]
        if item["code"] == "chart.zero-length-handshape"
    )
    assert zero_length["rule"]["repairability"] == "review_required"
    assert "could supply a chord" in zero_length["rule"]["guidance"]


def test_scan_marks_only_unambiguous_handshapes_as_automatic(tmp_path, validator):
    chord = {"t": 4.0, "id": 0, "notes": [{"s": 0, "f": 3}]}
    report = validator.validate_feedpak(_package(tmp_path, arrangement={
        "notes": [],
        "chords": [chord],
        "templates": [{"frets": [3], "fingers": [1]}],
        "handshapes": [
            {"chord_id": 0, "start_time": 4.0, "end_time": 4.0}
        ],
    }))

    eligibility = report["features"]["repair_eligibility"][
        "chart.zero-length-handshape"
    ]
    finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.zero-length-handshape"
    )
    assert eligibility["status"] == "automatic"
    assert eligibility["safe_count"] == 1
    assert eligibility["unsafe_count"] == 0
    assert finding["rule"]["repairability"] == "safe_candidate"


def test_scan_records_preview_source_eligibility_without_deep_audio(
    tmp_path, validator
):
    report = validator.validate_feedpak(_package(
        tmp_path,
        files={
            "stems/full.ogg": (
                _vorbis_ogg(duration=60.0, serial=41, payload=b"source")
                + b"\x00" * 1_000
            )
        },
    ))

    assert report["features"]["deep_audio_checked"] is False
    assert report["features"]["preview_source_available"] is True
    assert report["features"]["repair_eligibility"][
        "media.preview-missing"
    ]["status"] == "automatic"


def test_root_and_mastery_copies_do_not_inflate_chart_event_counts(
    tmp_path, validator
):
    note = {"t": 1.0, "s": 0, "f": -2, "mt": True}
    chord = {"t": 1.0, "id": 0, "notes": []}
    handshape = {"chord_id": 0, "start_time": 2.0, "end_time": 1.5}
    level = {
        "difficulty": 0,
        "notes": [note],
        "chords": [chord],
        "handshapes": [handshape],
    }
    arrangement = {
        "notes": [note],
        "chords": [chord],
        "handshapes": [handshape],
        "templates": [{"frets": [-1] * 6, "fingers": [-1] * 6}],
        "phrases": [{
            "start_time": 0.0,
            "end_time": 10.0,
            "max_difficulty": 1,
            "levels": [level, {**level, "difficulty": 1}],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    messages = {item["code"]: item["message"] for item in report["findings"]}

    assert "1 string-muted note(s)" in messages["chart.negative-muted-fret"]
    assert "1 chord event(s)" in messages["chart.invisible-chord"]
    assert "1 handshape(s)" in messages["chart.invalid-handshape-span"]


def test_phrase_difficulty_structure_and_timeline_are_checked(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "phrases": [
            {
                "start_time": 10.0,
                "end_time": 20.0,
                "max_difficulty": 1,
                "levels": [
                    {
                        "difficulty": 1,
                        "notes": [
                            {"t": 15.0, "s": 0, "f": 3},
                            {"t": 14.0, "s": 0, "f": 4},
                            {"t": 25.0, "s": 0, "f": 5},
                        ],
                    },
                    {"difficulty": 0, "notes": []},
                    {"difficulty": 0, "notes": []},
                ],
            },
            {"start_time": 5.0, "end_time": 4.0, "levels": []},
        ],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert {
        "chart.phrases-out-of-order",
        "chart.phrase-events-out-of-order",
        "chart.phrase-event-outside-window",
        "chart.phrase-levels-out-of-order",
        "chart.duplicate-difficulty-level",
        "chart.invalid-phrase-span",
        "chart.overlapping-phrases",
        "chart.empty-phrase-levels",
    }.issubset(_codes(report))


def test_empty_out_of_order_phrase_is_a_structural_warning(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "phrases": [
            {
                "start_time": 10.0,
                "end_time": 20.0,
                "max_difficulty": 0,
                "levels": [{
                    "difficulty": 0,
                    "notes": [{"t": 15.0, "s": 0, "f": 3}],
                }],
            },
            {
                "start_time": 5.0,
                "end_time": 6.0,
                "max_difficulty": 0,
                "levels": [{"difficulty": 0, "notes": []}],
            },
        ],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    severities = {item["code"]: item["severity"] for item in report["findings"]}

    assert severities["chart.phrases-out-of-order"] == "warning"
    assert "chart.mastery-events-out-of-order" not in _codes(report)
    assert report["status"] == "warning"


def test_playable_out_of_order_phrase_stream_is_an_error(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "phrases": [
            {
                "start_time": 10.0,
                "end_time": 20.0,
                "max_difficulty": 0,
                "levels": [{
                    "difficulty": 0,
                    "notes": [{"t": 15.0, "s": 0, "f": 3}],
                }],
            },
            {
                "start_time": 5.0,
                "end_time": 9.0,
                "max_difficulty": 0,
                "levels": [{
                    "difficulty": 0,
                    "notes": [{"t": 6.0, "s": 0, "f": 5}],
                }],
            },
        ],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.mastery-events-out-of-order"
    )

    assert finding["severity"] == "error"
    assert finding["time"] == 6.0
    assert report["status"] == "error"


def test_intermediate_mastery_only_stream_disorder_is_detected(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "phrases": [
            {
                "start_time": 10.0,
                "end_time": 20.0,
                "max_difficulty": 1,
                "levels": [
                    {"difficulty": 0, "notes": [{"t": 15.0, "s": 0, "f": 3}]},
                    {"difficulty": 1, "notes": [{"t": 15.0, "s": 0, "f": 3}]},
                ],
            },
            {
                "start_time": 5.0,
                "end_time": 9.0,
                "max_difficulty": 2,
                "levels": [
                    {"difficulty": 0, "notes": []},
                    {"difficulty": 1, "notes": [{"t": 6.0, "s": 0, "f": 5}]},
                    {"difficulty": 2, "notes": []},
                ],
            },
        ],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.mastery-events-out-of-order" in _codes(report)
    assert report["status"] == "error"


def test_same_string_conflicts_are_checked_inside_each_difficulty_level(
    tmp_path, validator
):
    within_level = {
        "notes": [],
        "chords": [],
        "phrases": [{
            "start_time": 0.0,
            "end_time": 10.0,
            "max_difficulty": 1,
            "levels": [
                {
                    "difficulty": 0,
                    "notes": [
                        {"t": 1.0, "s": 0, "f": 3},
                        {"t": 1.00001, "s": 0, "f": 5},
                    ],
                },
                {"difficulty": 1, "notes": []},
            ],
        }],
    }
    alternative_levels = {
        "notes": [],
        "chords": [],
        "phrases": [{
            "start_time": 0.0,
            "end_time": 10.0,
            "max_difficulty": 1,
            "levels": [
                {"difficulty": 0, "notes": [{"t": 1.0, "s": 0, "f": 3}]},
                {"difficulty": 1, "notes": [{"t": 1.0, "s": 0, "f": 5}]},
            ],
        }],
    }

    conflict_report = validator.validate_feedpak(_package(
        tmp_path / "conflict", arrangement=within_level,
    ))
    alternatives_report = validator.validate_feedpak(_package(
        tmp_path / "alternatives", arrangement=alternative_levels,
    ))

    assert "chart.string-conflict" in _codes(conflict_report)
    assert "chart.string-conflict" not in _codes(alternatives_report)


def test_same_string_collision_findings_are_aggregated_per_stream(tmp_path, validator):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 1.0, "s": 0, "f": 5},
            {"t": 2.0, "s": 1, "f": 7},
            {"t": 2.0, "s": 1, "f": 9},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    conflicts = [finding for finding in report["findings"] if finding["code"] == "chart.string-conflict"]
    assert len(conflicts) == 1
    assert "2 timestamp(s)" in conflicts[0]["message"]


def test_mastery_levels_do_not_inflate_repeated_issue_counts(tmp_path, validator):
    duplicated = [
        {"t": 1.0, "s": 0, "f": 3},
        {"t": 1.0, "s": 0, "f": 3},
    ]
    arrangement = {
        "notes": [],
        "chords": [],
        "phrases": [{
            "start_time": 0.0,
            "end_time": 10.0,
            "max_difficulty": 1,
            "levels": [
                {"difficulty": 0, "notes": duplicated},
                {"difficulty": 1, "notes": duplicated},
            ],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))
    duplicate = next(
        finding for finding in report["findings"]
        if finding["code"] == "chart.duplicate-note"
    )

    assert "1 timestamp(s)" in duplicate["message"]


def test_identical_and_conflicting_duplicate_notes_are_separate_rules(
    tmp_path, validator
):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 0.5},
            {"t": 1.0, "s": 0, "f": 3, "sus": 0.5},
            {"t": 2.0, "s": 1, "f": 5, "sus": 0.25},
            {"t": 2.0, "s": 1, "f": 5, "sus": 1.0},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.duplicate-note" in _codes(report)
    conflict = next(
        finding for finding in report["findings"]
        if finding["code"] == "chart.conflicting-duplicate-note"
    )
    assert "sus" in conflict["message"]
    assert "cannot be safely deduplicated" in conflict["message"]


def test_duplicate_note_comparison_uses_the_highway_time_key(tmp_path, validator):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 0.5},
            {"t": 1.00001, "s": 0, "f": 3, "sus": 0.5},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.duplicate-note" in _codes(report)
    assert "chart.conflicting-duplicate-note" not in _codes(report)


def test_near_simultaneous_and_overlapping_string_notes_request_review(
    tmp_path, validator
):
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 1.0},
            {"t": 1.009, "s": 0, "f": 5},
        ],
        "chords": [],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert report["status"] == "review"
    assert {
        "review.near-simultaneous-string-notes",
        "review.same-string-sustain-overlap",
    }.issubset(_codes(report))


@pytest.mark.parametrize(
    "first, second",
    [
        ({"t": 1.0, "s": 0, "f": 3, "sus": 1.0, "ln": True}, {"t": 1.5, "s": 0, "f": 5}),
        ({"t": 1.0, "s": 0, "f": 3, "sus": 1.0, "sl": 5}, {"t": 1.5, "s": 0, "f": 5}),
    ],
)
def test_linked_or_matching_slide_transitions_do_not_request_overlap_review(
    tmp_path, validator, first, second
):
    report = validator.validate_feedpak(_package(
        tmp_path, arrangement={"notes": [first, second], "chords": []},
    ))

    assert "review.same-string-sustain-overlap" not in _codes(report)


def test_same_time_and_eleven_millisecond_onsets_are_not_near_onset_reviews(
    tmp_path, validator
):
    exact_report = validator.validate_feedpak(_package(
        tmp_path / "exact",
        arrangement={
            "notes": [{"t": 1.0, "s": 0, "f": 3}, {"t": 1.0, "s": 0, "f": 3}],
            "chords": [],
        },
    ))
    separated_report = validator.validate_feedpak(_package(
        tmp_path / "separated",
        arrangement={
            "notes": [{"t": 1.0, "s": 0, "f": 3}, {"t": 1.011, "s": 0, "f": 5}],
            "chords": [],
        },
    ))

    assert "review.near-simultaneous-string-notes" not in _codes(exact_report)
    assert "review.near-simultaneous-string-notes" not in _codes(separated_report)


def test_song_timeline_order_and_duration_are_checked(tmp_path, validator):
    manifest = _manifest(song_timeline="song_timeline.json")
    timeline = {
        "version": 1,
        "beats": [
            {"time": 10.0, "measure": 1},
            {"time": -1.0, "measure": -1},
            {"time": 66.0, "measure": 2},
        ],
        "sections": [
            {"name": "Verse", "time": 20.0},
            {"name": "Intro", "time": 5.0},
            {"name": "Outro", "time": 67.0},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"song_timeline.json": json.dumps(timeline)},
    ))

    assert {
        "timeline.beats-out-of-order",
        "timeline.beat-after-duration",
        "timeline.sections-out-of-order",
        "timeline.section-after-duration",
    }.issubset(_codes(report))
    beat_order = next(
        item for item in report["findings"]
        if item["code"] == "timeline.beats-out-of-order"
    )
    assert beat_order["severity"] == "error"
    assert beat_order["time"] == -1.0
    assert beat_order["location"] == "song_timeline.json:beats[1]"
    assert beat_order["rule"]["repairability"] == "safe_candidate"
    assert "Preserve every marker" in beat_order["rule"]["guidance"]
    section_order = next(
        item for item in report["findings"]
        if item["code"] == "timeline.sections-out-of-order"
    )
    assert section_order["rule"]["repairability"] == "safe_candidate"
    assert "equal-time markers" in section_order["rule"]["guidance"]


def test_legacy_embedded_timeline_is_checked_per_arrangement(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "beats": [
            {"time": 2.0, "measure": 1},
            {"time": 1.0, "measure": -1},
        ],
        "sections": [
            {"name": "Intro", "time": 0.0},
            {"name": "Late", "time": 66.0},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path, arrangement=arrangement,
    ))

    assert "timeline.beats-out-of-order" in _codes(report)
    assert "timeline.section-after-duration" in _codes(report)
    finding = next(
        item for item in report["findings"]
        if item["code"] == "timeline.beats-out-of-order"
    )
    assert finding["arrangement_id"] == "lead"


def test_negative_preroll_and_equal_timeline_times_are_allowed(tmp_path, validator):
    manifest = _manifest(song_timeline="song_timeline.json")
    timeline = {
        "version": 1,
        "beats": [
            {"time": -2.0, "measure": 0},
            {"time": 0.0, "measure": 1},
            {"time": 0.0, "measure": -1},
        ],
        "sections": [
            {"name": "Count-in", "time": -2.0},
            {"name": "Intro", "time": 0.0},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"song_timeline.json": json.dumps(timeline)},
    ))

    assert not any(code.startswith("timeline.") for code in _codes(report))


def test_timeline_distinguishes_exact_duplicates_from_conflicting_repeated_times(
    tmp_path, validator,
):
    manifest = _manifest(song_timeline="song_timeline.json")
    timeline = {
        "version": 1,
        "beats": [
            {"time": 1.0, "measure": 1},
            {"time": 2.0, "measure": 1},
            {"time": 1.0, "measure": 1},
            {"time": 2.0, "measure": 2},
        ],
        "sections": [
            {"name": "Intro", "time": 1.0, "number": 1},
            {"name": "Verse", "time": 5.0, "number": 1},
            {"name": "Intro", "time": 1.0, "number": 2},
            {"name": "Verse", "time": 5.0, "number": 1},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"song_timeline.json": json.dumps(timeline)},
    ))
    findings = {item["code"]: item for item in report["findings"]}

    assert findings["timeline.duplicate-beat"]["affected_count"] == 1
    assert findings["timeline.duplicate-beat"]["location"].endswith("beats[2]")
    assert findings["timeline.duplicate-beat"]["rule"]["repairability"] == (
        "safe_candidate"
    )
    assert findings["timeline.repeated-beat-time"]["affected_count"] == 1
    assert findings["timeline.repeated-beat-time"]["severity"] == "error"
    assert findings["timeline.repeated-section-time"]["affected_count"] == 1
    assert findings["timeline.duplicate-section"]["affected_count"] == 1
    assert findings["timeline.duplicate-section"]["location"].endswith(
        "sections[3]"
    )
    assert findings["timeline.duplicate-section"]["rule"]["repairability"] == (
        "safe_candidate"
    )
    assert "will not guess" in findings[
        "timeline.repeated-beat-time"
    ]["rule"]["guidance"]


def test_small_song_timeline_overrun_is_allowed(tmp_path, validator):
    manifest = _manifest(song_timeline="song_timeline.json")
    timeline = {
        "version": 1,
        "beats": [{"time": 62.5, "measure": 1}],
        "sections": [{"name": "Outro", "time": 64.9}],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"song_timeline.json": json.dumps(timeline)},
    ))

    assert "timeline.beat-after-duration" not in _codes(report)
    assert "timeline.section-after-duration" not in _codes(report)


def test_small_phrase_tail_overrun_is_allowed(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "phrases": [{
            "start_time": 55.0,
            "end_time": 64.9,
            "max_difficulty": 0,
            "levels": [{
                "difficulty": 0,
                "notes": [],
                "chords": [],
                "anchors": [],
                "handshapes": [],
            }],
        }],
    }

    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.phrase-after-duration" not in _codes(report)


def test_authoritative_sidecar_suppresses_unused_embedded_timeline(tmp_path, validator):
    arrangement = {
        "notes": [],
        "chords": [],
        "beats": [
            {"time": 2.0, "measure": 1},
            {"time": 1.0, "measure": -1},
        ],
        "sections": [
            {"name": "Late", "time": 61.0},
        ],
    }
    timeline = {
        "version": 1,
        "beats": [{"time": 0.0, "measure": 1}],
        "sections": [{"name": "Intro", "time": 0.0}],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=_manifest(song_timeline="song_timeline.json"),
        arrangement=arrangement,
        files={"song_timeline.json": json.dumps(timeline)},
    ))

    assert not any(code.startswith("timeline.") for code in _codes(report))


def test_only_explicit_guitar_or_bass_empty_arrangements_are_flagged(tmp_path, validator):
    guitar_manifest = _manifest(arrangements=[{
        "id": "lead", "type": "guitar", "file": "arrangements/lead.json",
    }])
    vocal_manifest = _manifest(arrangements=[{
        "id": "vocals", "type": "vocals", "file": "arrangements/lead.json",
    }])

    guitar_report = validator.validate_feedpak(_package(
        tmp_path / "guitar", manifest=guitar_manifest,
    ))
    vocal_report = validator.validate_feedpak(_package(
        tmp_path / "vocals", manifest=vocal_manifest,
    ))

    assert "chart.empty-fretted-arrangement" in _codes(guitar_report)
    assert "chart.empty-fretted-arrangement" not in _codes(vocal_report)
    assert vocal_report["status"] == "healthy"


def test_notes_with_different_frets_on_one_lane_are_an_error(tmp_path, validator):
    arrangement = {
        "notes": [
            {"t": 5.0, "s": 1, "f": 3},
            {"t": 5.00001, "s": 1, "f": 5},
        ],
        "chords": [],
    }
    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert report["status"] == "error"
    assert "chart.string-conflict" in _codes(report)


def test_keyboard_chart_allows_multiple_pitches_on_a_synthetic_string(tmp_path, validator):
    arrangement = {
        "name": "Keys",
        "notes": [],
        "chords": [{
            "t": 5.0,
            "id": 0,
            "notes": [{"s": 2, "f": 4}, {"s": 2, "f": 12}],
        }],
    }
    manifest = _manifest(arrangements=[{
        "id": "keys",
        "name": "Keys",
        "file": "arrangements/lead.json",
        "notation": "notation_keys.json",
    }])
    notation = {
        "version": 1,
        "instrument": "piano",
        "staves": [],
        "measures": [],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        arrangement=arrangement,
        files={"notation_keys.json": json.dumps(notation)},
    ))

    assert report["status"] == "healthy"
    assert "chart.string-conflict" not in _codes(report)
    assert "chart.chord-string-duplicate" not in _codes(report)


def test_explicit_guitar_type_wins_over_a_keyboard_like_name(tmp_path, validator):
    arrangement = {
        "name": "Synth Guitar",
        "notes": [
            {"t": 5.0, "s": 2, "f": 4},
            {"t": 5.0, "s": 2, "f": 12},
        ],
        "chords": [],
    }
    manifest = _manifest(arrangements=[{
        "id": "synth-guitar",
        "name": "Synth Guitar",
        "type": "guitar",
        "file": "arrangements/lead.json",
    }])

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        arrangement=arrangement,
    ))

    assert report["status"] == "error"
    assert "chart.string-conflict" in _codes(report)


def test_notes_outside_the_same_time_key_are_not_deduplicated(tmp_path, validator):
    arrangement = {
        "notes": [
            {"t": 5.0, "s": 1, "f": 3},
            {"t": 5.001, "s": 1, "f": 3},
        ],
        "chords": [],
    }
    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert "chart.duplicate-note" not in _codes(report)
    assert "chart.string-conflict" not in _codes(report)


def test_lyric_timeline_checks_are_separate_from_schema_validation(tmp_path, validator):
    lyrics = [
        {"t": 2.0, "d": 0.2, "w": "later"},
        {"t": -1.0, "d": 0.2, "w": "early"},
        {"t": 59.9, "d": 1.0, "w": "late"},
    ]
    manifest = _manifest(lyrics="lyrics.json")
    report = validator.validate_feedpak(
        _package(
            tmp_path,
            manifest=manifest,
            files={"lyrics.json": json.dumps(lyrics)},
        )
    )

    assert report["features"]["lyrics_declared"] is True
    assert report["features"]["lyrics_entries"] == 3
    assert {
        "lyrics.negative-time",
        "lyrics.out-of-order",
        "lyrics.after-duration",
    }.issubset(_codes(report))
    finding = next(
        item for item in report["findings"]
        if item["code"] == "lyrics.out-of-order"
    )
    assert finding["rule"]["repairability"] == "safe_candidate"
    assert finding["affected_count"] == 1
    assert finding["location"] == "lyrics.json:[1]"
    assert finding["time"] == -1.0


def test_empty_declared_lyrics_are_a_warning(tmp_path, validator):
    manifest = _manifest(lyrics="lyrics.json")
    report = validator.validate_feedpak(
        _package(tmp_path, manifest=manifest, files={"lyrics.json": "[]"})
    )

    assert report["features"]["lyrics_declared"] is True
    assert report["features"]["lyrics_entries"] == 0
    assert "lyrics.empty" in _codes(report)


def test_excessively_long_lyric_line_is_a_warning(tmp_path, validator):
    lyrics = _lyrics(validator.MAX_LYRIC_SYLLABLES_PER_LINE + 1)
    manifest = _manifest(lyrics="lyrics.json")

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"lyrics.json": json.dumps(lyrics)},
    ))

    finding = next(
        item for item in report["findings"]
        if item["code"] == "lyrics.too-few-line-breaks"
    )
    assert finding["severity"] == "warning"
    assert "0 authored '+' line break marker(s)" in finding["message"]


def test_standalone_lyric_controls_are_not_reported_as_empty_text(
    tmp_path, validator
):
    lyrics = _lyrics(60)
    for entry in lyrics:
        entry["w"] = "abcdefghij"
    lyrics[4]["w"] = "+"
    lyrics[5]["w"] = "-"
    manifest = _manifest(lyrics="lyrics.json")

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"lyrics.json": json.dumps(lyrics)},
    ))

    assert "lyrics.empty-text" not in _codes(report)
    assert "lyrics.too-few-line-breaks" in _codes(report)


def test_genuinely_blank_lyric_text_is_reported(tmp_path, validator):
    lyrics = _lyrics(6)
    lyrics[1]["w"] = ""
    lyrics[2]["w"] = "   "
    lyrics[3]["w"] = " +"
    manifest = _manifest(lyrics="lyrics.json")

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"lyrics.json": json.dumps(lyrics)},
    ))

    finding = next(
        item for item in report["findings"]
        if item["code"] == "lyrics.empty-text"
    )
    assert finding["affected_count"] == 3
    assert finding["location"] == "lyrics.json:[1].w"


def test_authored_and_renderer_fallback_lyric_breaks_limit_line_length(
    tmp_path, validator
):
    max_line = validator.MAX_LYRIC_SYLLABLES_PER_LINE
    authored = _lyrics(max_line * 2, breaks={max_line - 1, max_line * 2 - 1})
    fallback = _lyrics(max_line * 2, fallback_gap_after=max_line - 1)
    manifest = _manifest(
        duration=120.0,
        lyrics="lyrics.json",
        lyric_tracks=[{
            "id": "fallback",
            "file": "fallback.json",
            "language": "und",
            "kind": "original",
        }],
    )

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={
            "lyrics.json": json.dumps(authored),
            "fallback.json": json.dumps(fallback),
        },
    ))

    assert "lyrics.too-few-line-breaks" not in _codes(report)


def test_manifest_cross_references_and_full_mix_contract_are_checked(tmp_path, validator):
    manifest = _manifest(
        stems=[
            {"id": "full", "file": "stems/full.ogg", "default": True},
            {"id": "guitar", "file": "stems/guitar.ogg", "default": True},
        ],
        lyric_tracks=[
            {
                "id": "main", "file": "lyrics-a.json", "language": "en",
                "kind": "original", "stem": "vocals",
            },
            {
                "id": "main", "file": "lyrics-b.json", "language": "en",
                "kind": "translation",
            },
        ],
    )
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={
            "stems/guitar.ogg": b"guitar",
            "lyrics-a.json": "[]",
            "lyrics-b.json": "[]",
        },
    ))

    assert {
        "manifest.full-mix-default-not-off",
        "manifest.duplicate-lyric-track-id",
        "manifest.lyric-track-missing-stem",
    }.issubset(_codes(report))
    full_mix = next(
        finding for finding in report["findings"]
        if finding["code"] == "manifest.full-mix-default-not-off"
    )
    assert full_mix["severity"] == "error"


def test_new_separated_feedpak_requires_a_retained_full_mix(tmp_path, validator):
    manifest = _manifest(stems=[
        {"id": "guitar", "file": "stems/guitar.ogg"},
        {"id": "drums", "file": "stems/drums.ogg"},
    ])
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"stems/guitar.ogg": b"guitar", "stems/drums.ogg": b"drums"},
    ))

    assert "manifest.missing-full-mix" in _codes(report)
    assert report["status"] == "error"


def test_disabled_full_mix_is_valid_beside_separated_stems(tmp_path, validator):
    manifest = _manifest(stems=[
        {"id": "full", "file": "stems/full.ogg", "default": False},
        {"id": "guitar", "file": "stems/guitar.ogg", "default": True},
    ])
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"stems/guitar.ogg": b"guitar"},
    ))

    assert "manifest.full-mix-default-not-off" not in _codes(report)


def test_timed_sidecar_semantics_are_checked(tmp_path, validator):
    manifest = _manifest(
        drum_tab="drums.json",
        vocal_pitch="vocal-pitch.json",
        vocal_pitch_contour="contour.json",
        keys="keys.json",
        harmony="harmony.json",
    )
    files = {
        "drums.json": json.dumps({
            "version": 1,
            "kit": [{"id": "snare"}, {"id": "snare"}],
            "hits": [
                {"t": 2.0, "p": "snare"},
                {"t": -1.0, "p": "kick", "k": -0.5},
                {"t": 61.0, "p": "kick"},
            ],
        }),
        "vocal-pitch.json": json.dumps({
            "version": 1,
            "notes": [
                {"t": 2.0, "d": 0.2, "midi": 60},
                {"t": -1.0, "d": -0.2, "midi": 61},
                {"t": 59.9, "d": 1.0, "midi": 62},
            ],
        }),
        "contour.json": json.dumps({
            "version": 1,
            "samples": [{"t": 2.0, "hz": 440}, {"t": -1.0, "hz": 441}],
        }),
        "keys.json": json.dumps({
            "version": 1,
            "events": [{"t": 2.0, "key": "C"}, {"t": 1.0, "key": " "}],
        }),
        "harmony.json": json.dumps({
            "version": 1,
            "events": [{"t": 66.0, "root": "G"}, {"t": 1.0, "root": "C"}],
        }),
    }

    report = validator.validate_feedpak(_package(
        tmp_path, manifest=manifest, files=files,
    ))

    assert {
        "drums.events-out-of-order",
        "drums.negative-time",
        "drums.negative-duration",
        "drums.event-after-duration",
        "drums.duplicate-kit-id",
        "vocal-pitch.events-out-of-order",
        "vocal-pitch.negative-time",
        "vocal-pitch.negative-duration",
        "vocal-pitch.event-after-duration",
        "vocal-pitch-contour.events-out-of-order",
        "vocal-pitch-contour.negative-time",
        "keys.events-out-of-order",
        "keys.empty-key",
        "harmony.events-out-of-order",
        "harmony.event-after-duration",
    }.issubset(_codes(report))


def test_duplicate_and_conflicting_drum_hits_are_distinguished(tmp_path, validator):
    manifest = _manifest(drum_tab="drums.json")
    drums = {
        "version": 1,
        "hits": [
            {"t": 4.0, "p": "snare", "v": 100},
            {"t": 4.0, "p": "snare", "v": 100},
            {"t": 8.0, "p": "kick", "v": 80},
            {"t": 8.0, "p": "kick", "v": 120},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"drums.json": json.dumps(drums)},
    ))

    assert "drums.duplicate-hit" in _codes(report)
    assert "drums.conflicting-hit" in _codes(report)


def test_notation_relationships_and_timeline_are_checked(tmp_path, validator):
    manifest = _manifest(arrangements=[{
        "id": "lead",
        "file": "arrangements/lead.json",
        "notation": "notation.json",
    }])
    notation = {
        "version": 1,
        "staves": [{"id": "treble", "clef": "G2"}, {"id": "treble", "clef": "G2"}],
        "measures": [
            {"idx": 2, "t": 5.0},
            {
                "idx": 1,
                "t": 3.0,
                "staves": {
                    "missing": {
                        "voices": [
                            {"v": 1, "beats": [{"t": 65.0, "dur": 4, "rest": True}]},
                            {"v": 1, "beats": []},
                        ]
                    }
                },
            },
            {"idx": 1, "t": 7.0},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"notation.json": json.dumps(notation)},
    ))

    assert {
        "notation.duplicate-staff-id",
        "notation.duplicate-measure-index",
        "notation.measure-indices-out-of-order",
        "notation.measures-out-of-order",
        "notation.unknown-staff-reference",
        "notation.duplicate-voice-id",
        "notation.beat-outside-song",
    }.issubset(_codes(report))


def test_rig_tone_graph_assets_hashes_and_automation_are_checked(
    tmp_path, validator
):
    asset = b"rig asset"
    manifest = _manifest(
        rigs="rigs.json",
        arrangements=[{
            "id": "lead",
            "file": "arrangements/lead.json",
            "tones": {
                "base_rig": "missing-base",
                "changes": [
                    {"t": 5.0, "rig": "amp"},
                    {"t": -1.0, "rig": "missing-change"},
                    {"t": 61.0, "rig": "amp"},
                ],
            },
        }],
    )
    rigs = {
        "version": 1,
        "rigs": [
            {
                "id": "amp",
                "blocks": [
                    {
                        "id": "gain",
                        "realizations": [
                            {"engine": "ir", "ref": "assets/missing.wav"},
                            {"engine": "ir", "ref": "assets/present.wav", "sha256": "0" * 64},
                        ],
                        "automation": [{
                            "param": "mix",
                            "points": [
                                {"t": 2.0, "v": 1.0},
                                {"t": -1.0, "v": 0.0},
                                {"t": 61.0, "v": 0.5},
                            ],
                        }],
                    },
                    {"id": "gain"},
                ],
                "graph": {
                    "nodes": ["input", "gain", "gain", "ghost", "output"],
                    "edges": [["input", "gain"], ["ghost", "output"]],
                },
            },
            {"id": "amp", "blocks": []},
        ],
    }

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"rigs.json": json.dumps(rigs), "assets/present.wav": asset},
    ))

    assert {
        "rigs.duplicate-rig-id",
        "rigs.duplicate-block-id",
        "rigs.missing-realization-file",
        "rigs.realization-hash-mismatch",
        "rigs.automation-out-of-order",
        "rigs.automation-negative-time",
        "rigs.automation-after-duration",
        "rigs.duplicate-graph-node",
        "rigs.invalid-graph-reference",
        "tones.missing-rig",
        "tones.changes-out-of-order",
        "tones.negative-time",
        "tones.change-after-duration",
    }.issubset(_codes(report))


def test_cover_image_header_and_extension_are_checked(tmp_path, validator):
    png = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (16).to_bytes(4, "big")
        + (16).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    mismatch = validator.validate_feedpak(_package(
        tmp_path / "mismatch",
        manifest=_manifest(cover="cover.jpg"),
        files={"cover.jpg": png},
    ))
    invalid = validator.validate_feedpak(_package(
        tmp_path / "invalid",
        manifest=_manifest(cover="cover.png"),
        files={"cover.png": b"not an image"},
    ))

    assert "media.cover-extension-mismatch" in _codes(mismatch)
    assert "media.invalid-cover-image" in _codes(invalid)


def test_package_member_budget_stops_pathological_packages(
    tmp_path, validator, monkeypatch
):
    package = _package(tmp_path)
    monkeypatch.setattr(validator, "MAX_PACKAGE_MEMBERS", 2)

    report = validator.validate_feedpak(package)

    assert "package.validation-budget-exceeded" in _codes(report)


def test_yaml_alias_budget_stops_excessive_expansion(tmp_path, validator, monkeypatch):
    package = _package(tmp_path)
    (package / "manifest.yaml").write_text(
        """feedpak_version: 1.19.0
title: Alias Test
artist: Test Artist
duration: 60
arrangement: &arrangement
  id: lead
  file: arrangements/lead.json
arrangements:
  - *arrangement
  - *arrangement
stems:
  - id: full
    file: stems/full.ogg
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "MAX_YAML_ALIASES", 1)

    report = validator.validate_feedpak(package)

    assert "package.validation-budget-exceeded" in _codes(report)


def test_preview_without_readable_duration_is_not_classified_by_payload(
    tmp_path, validator
):
    manifest = _manifest(preview="preview.ogg")
    report = validator.validate_feedpak(
        _package(
            tmp_path,
            manifest=manifest,
            files={"preview.ogg": b"full mix audio"},
        )
    )

    assert report["features"]["preview_available"] is True
    assert "media.preview-too-long" not in _codes(report)


def test_unreadable_preview_duration_is_not_guessed_from_bytes(tmp_path, validator):
    manifest = _manifest(preview="preview.ogg")
    report = validator.validate_feedpak(
        _package(tmp_path, manifest=manifest, files={"preview.ogg": b"short"})
    )

    assert "media.preview-too-long" not in _codes(report)


def test_deep_audio_reports_invalid_containers_and_duration_mismatch(tmp_path, validator):
    invalid = validator.validate_feedpak(_package(tmp_path / "invalid"), deep_audio=True)
    valid_full = _vorbis_ogg(duration=40.0, payload=b"valid full")
    mismatch = validator.validate_feedpak(_package(
        tmp_path / "mismatch",
        files={"stems/full.ogg": valid_full},
    ), deep_audio=True)

    assert invalid["features"]["deep_audio_checked"] is True
    assert "media.invalid-ogg-container" in _codes(invalid)
    assert "media.audio-shorter-than-manifest" in _codes(mismatch)


def test_validation_checkpoints_during_deep_audio_reads(tmp_path, validator):
    checkpoints = []
    report = validator.validate_feedpak(
        _package(
            tmp_path,
            files={"stems/full.ogg": _vorbis_ogg(duration=60.0)},
        ),
        deep_audio=True,
        scan_checkpoint=lambda: checkpoints.append(len(checkpoints)),
    )

    assert report["features"]["deep_audio_files"] == 1
    assert len(checkpoints) >= 4


def test_deep_audio_tolerates_modest_audio_padding_but_flags_large_overrun(
    tmp_path, validator
):
    padded = validator.validate_feedpak(_package(
        tmp_path / "padded",
        files={"stems/full.ogg": _vorbis_ogg(duration=68.0)},
    ), deep_audio=True)
    excessive = validator.validate_feedpak(_package(
        tmp_path / "excessive",
        files={"stems/full.ogg": _vorbis_ogg(duration=75.0)},
    ), deep_audio=True)

    assert "media.audio-longer-than-manifest" not in _codes(padded)
    assert "media.audio-longer-than-manifest" in _codes(excessive)


def test_deep_audio_reports_non_ogg_files_as_partial_coverage(tmp_path, validator):
    manifest = _manifest(
        stems=[{"id": "full", "file": "stems/full.mp3"}],
        preview="preview.wav",
    )
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"stems/full.mp3": b"mp3", "preview.wav": b"wav"},
    ), deep_audio=True)

    assert report["status"] == "healthy"
    assert report["features"]["deep_audio_files"] == 0
    assert report["features"]["deep_audio_unsupported"] == 2
    assert "media.invalid-ogg-container" not in _codes(report)


def test_deep_audio_reports_unknown_ogg_codec_as_partial_coverage(
    tmp_path, validator
):
    unknown = _vorbis_ogg(duration=60.0).replace(b"\x01vorbis", b"unknown", 1)
    report = validator.validate_feedpak(_package(
        tmp_path,
        files={"stems/full.ogg": unknown},
    ), deep_audio=True)

    assert report["features"]["deep_audio_files"] == 1
    assert report["features"]["deep_audio_unsupported"] == 1
    assert "media.invalid-ogg-container" not in _codes(report)


def test_deep_audio_finds_duplicate_separated_stem_payloads(tmp_path, validator):
    payload = _vorbis_ogg(duration=60.0, payload=b"same separated audio")
    manifest = _manifest(stems=[
        {"id": "full", "file": "stems/full.ogg", "default": "off"},
        {"id": "guitar", "file": "stems/guitar.ogg", "default": "on"},
        {"id": "bass", "file": "stems/bass.ogg", "default": "on"},
    ])
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={
            "stems/full.ogg": _vorbis_ogg(duration=60.0, payload=b"full mix"),
            "stems/guitar.ogg": payload,
            "stems/bass.ogg": payload,
        },
    ), deep_audio=True)

    assert "media.duplicate-stem-audio" in _codes(report)


def test_overlong_preview_uses_only_its_duration_even_when_payload_matches(
    tmp_path, validator
):
    manifest = _manifest(preview="preview.ogg")
    full = _vorbis_ogg(duration=60.0, serial=11)
    preview = _vorbis_ogg(duration=60.0, serial=22)

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"stems/full.ogg": full, "preview.ogg": preview},
    ))

    assert full != preview
    assert "media.preview-too-long" in _codes(report)
    finding = next(
        item for item in report["findings"]
        if item["code"] == "media.preview-too-long"
    )
    assert "60.0s long" in finding["message"]
    assert "payload" not in finding["message"]


def test_overlong_preview_message_does_not_compare_it_with_song_length(
    tmp_path, validator
):
    manifest = _manifest(preview="preview.ogg")
    full = _vorbis_ogg(duration=60.0, serial=11, payload=b"full payload aaa")
    preview = _vorbis_ogg(duration=58.0, serial=22, payload=b"preview payload")

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"stems/full.ogg": full, "preview.ogg": preview},
    ))

    assert "media.preview-too-long" in _codes(report)
    finding = next(
        item for item in report["findings"]
        if item["code"] == "media.preview-too-long"
    )
    assert "58.0s long" in finding["message"]
    assert "%" not in finding["message"]
    assert finding["rule"]["repairability"] == "manual"
    assert "full mix" in finding["rule"]["guidance"]


@pytest.mark.parametrize("preview_duration", [20.0, 27.0, 35.0])
def test_preview_lengths_from_twenty_through_thirty_five_seconds_are_accepted(
    tmp_path, validator, preview_duration
):
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=_manifest(preview="preview.ogg"),
        files={
            "stems/full.ogg": _vorbis_ogg(duration=60.0, serial=11),
            "preview.ogg": _vorbis_ogg(
                duration=preview_duration,
                serial=22,
                payload=b"dedicated preview",
            ),
        },
    ))

    assert "media.preview-too-short" not in _codes(report)
    assert "media.preview-too-long" not in _codes(report)


def test_full_mix_payload_is_accepted_when_preview_duration_is_in_range(
    tmp_path, validator
):
    full = _vorbis_ogg(duration=30.0, serial=11, payload=b"same audio")
    preview = _vorbis_ogg(duration=30.0, serial=22, payload=b"same audio")
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=_manifest(duration=30.0, preview="preview.ogg"),
        files={"stems/full.ogg": full, "preview.ogg": preview},
    ))

    assert "media.preview-too-short" not in _codes(report)
    assert "media.preview-too-long" not in _codes(report)


def test_preview_outside_the_accepted_duration_range_is_reported(
    tmp_path, validator
):
    short = validator.validate_feedpak(_package(
        tmp_path / "short",
        manifest=_manifest(preview="preview.ogg"),
        files={"preview.ogg": _vorbis_ogg(duration=19.0, serial=22)},
    ))
    long = validator.validate_feedpak(_package(
        tmp_path / "long",
        manifest=_manifest(preview="preview.ogg"),
        files={
            "stems/full.ogg": _vorbis_ogg(
                duration=180.0, serial=11, payload=b"full" * 20
            ),
            "preview.ogg": _vorbis_ogg(
                duration=36.0, serial=22, payload=b"different"
            ),
        },
    ))

    assert "media.preview-too-short" in _codes(short)
    assert "media.preview-too-long" in _codes(long)


def test_under_twenty_second_preview_is_accepted_for_a_short_song(
    tmp_path, validator
):
    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=_manifest(duration=18.0, preview="preview.ogg"),
        files={
            "stems/full.ogg": _vorbis_ogg(duration=18.0, serial=11),
            "preview.ogg": _vorbis_ogg(
                duration=17.0, serial=22, payload=b"short-song-preview"
            ),
        },
    ))

    assert "media.preview-too-short" not in _codes(report)


def test_deep_audio_compares_preview_duration_even_when_file_is_much_smaller(
    tmp_path, validator
):
    manifest = _manifest(preview="preview.ogg")
    full = _vorbis_ogg(duration=60.0, serial=11, payload=b"x" * 180)
    preview = _vorbis_ogg(duration=58.0, serial=22, payload=b"y")
    package = _package(
        tmp_path,
        manifest=manifest,
        files={"stems/full.ogg": full, "preview.ogg": preview},
    )

    normal = validator.validate_feedpak(package)
    deep = validator.validate_feedpak(package, deep_audio=True)

    assert "media.preview-too-long" in _codes(normal)
    assert "media.preview-too-long" in _codes(deep)
    deep_finding = next(
        item for item in deep["findings"]
        if item["code"] == "media.preview-too-long"
    )
    assert "58.0s long" in deep_finding["message"]
    assert "%" not in deep_finding["message"]


def test_short_ogg_preview_and_short_song_are_not_media_warnings(tmp_path, validator):
    manifest = _manifest(duration=60.0, preview="preview.ogg")
    full = _vorbis_ogg(duration=60.0, serial=11, payload=b"full payload")
    preview = _vorbis_ogg(
        duration=30.0,
        serial=22,
        payload=b"short preview payload that is deliberately similar in size",
    )
    report = validator.validate_feedpak(_package(
        tmp_path / "normal",
        manifest=manifest,
        files={"stems/full.ogg": full, "preview.ogg": preview},
    ))
    short_full = _vorbis_ogg(duration=25.0, serial=31)
    short_preview = _vorbis_ogg(duration=25.0, serial=32)
    short_report = validator.validate_feedpak(_package(
        tmp_path / "short-song",
        manifest=_manifest(duration=25.0, preview="preview.ogg"),
        files={"stems/full.ogg": short_full, "preview.ogg": short_preview},
    ))

    assert "media.preview-too-short" not in _codes(report)
    assert "media.preview-too-long" not in _codes(report)
    assert "media.preview-too-short" not in _codes(short_report)
    assert "media.preview-too-long" not in _codes(short_report)


def test_zip_packages_are_read_without_extraction(tmp_path, validator):
    root = _package(tmp_path / "source")
    archive = tmp_path / "song.feedpak"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())

    report = validator.validate_feedpak(archive)

    assert report["status"] == "healthy"
    assert not (tmp_path / "arrangements").exists()


def test_unsafe_archive_member_does_not_abort_other_checks(tmp_path, validator):
    root = _package(tmp_path / "source")
    archive = tmp_path / "song.feedpak"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
        zf.writestr("../outside.txt", "unsafe")

    report = validator.validate_feedpak(archive)

    assert "package.unsafe-archive-path" in _codes(report)


def test_case_colliding_archive_members_are_reported(tmp_path, validator):
    root = _package(tmp_path / "source")
    archive = tmp_path / "song.feedpak"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
        zf.writestr("ARRANGEMENTS/lead.json", '{"notes": [], "chords": []}')

    report = validator.validate_feedpak(archive)

    assert "package.case-colliding-archive-member" in _codes(report)
    assert report["title"] == "Test Song"
