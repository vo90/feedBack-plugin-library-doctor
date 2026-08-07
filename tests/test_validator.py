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
    name = "library_health_validator_tests"
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
    }


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


def test_standalone_note_that_matches_a_chord_member_is_not_a_fault(tmp_path, validator):
    arrangement = {
        "notes": [{"t": 10.0, "s": 2, "f": 7}],
        "chords": [{"t": 10.0, "id": 0, "notes": [{"s": 2, "f": 7}]}],
        "templates": [{
            "frets": [-1, -1, 7, -1, -1, -1],
            "fingers": [-1, -1, 1, -1, -1, -1],
        }],
    }
    report = validator.validate_feedpak(_package(tmp_path, arrangement=arrangement))

    assert report["status"] == "healthy"
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
        "chart.no-op-slide",
        "chart.bend-points-out-of-order",
        "chart.bend-point-outside-sustain",
        "chart.bend-exceeds-peak",
    }.issubset(_codes(report))


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
        "chart.missing-handshape-template",
        "chart.handshape-after-duration",
    }.issubset(_codes(report))


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


def test_preview_identical_to_full_mix_is_a_warning(tmp_path, validator):
    manifest = _manifest(preview="preview.ogg")
    report = validator.validate_feedpak(
        _package(
            tmp_path,
            manifest=manifest,
            files={"preview.ogg": b"full mix audio"},
        )
    )

    assert report["features"]["preview_available"] is True
    assert "media.preview-is-full-mix" in _codes(report)


def test_different_preview_is_not_hashed_as_a_problem(tmp_path, validator):
    manifest = _manifest(preview="preview.ogg")
    report = validator.validate_feedpak(
        _package(tmp_path, manifest=manifest, files={"preview.ogg": b"short"})
    )

    assert "media.preview-is-full-mix" not in _codes(report)


def test_remuxed_ogg_preview_with_same_payload_is_a_warning(tmp_path, validator):
    manifest = _manifest(preview="preview.ogg")
    full = _vorbis_ogg(duration=60.0, serial=11)
    preview = _vorbis_ogg(duration=60.0, serial=22)

    report = validator.validate_feedpak(_package(
        tmp_path,
        manifest=manifest,
        files={"stems/full.ogg": full, "preview.ogg": preview},
    ))

    assert full != preview
    assert "media.preview-is-full-mix" in _codes(report)
    assert "media.preview-full-length" not in _codes(report)


def test_full_length_ogg_preview_with_different_payload_is_a_warning(
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

    assert "media.preview-is-full-mix" not in _codes(report)
    assert "media.preview-full-length" in _codes(report)


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

    assert "media.preview-is-full-mix" not in _codes(report)
    assert "media.preview-full-length" not in _codes(report)
    assert "media.preview-is-full-mix" not in _codes(short_report)
    assert "media.preview-full-length" not in _codes(short_report)


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
    assert report["title"] == "Test Song"
