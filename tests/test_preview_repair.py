import importlib.util
import logging
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture(scope="module")
def preview_repair():
    path = Path(__file__).parents[1] / "preview_repair.py"
    name = "library_doctor_preview_repair_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).parents[1] / "repair.py"
    name = "library_doctor_preview_repair_service_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


class PlanningError(ValueError):
    def __init__(self, code, message, *, file_state="unchanged"):
        super().__init__(message)
        self.code = code
        self.file_state = file_state


def _engine(preview_repair, members, *, finding="media.preview-too-long"):
    source = members["stems/full.ogg"]
    candidate = b"OggS" + b"short-preview" * 200

    def validate(_path, package_name, *, deep_audio=False):
        return {
            "package": package_name,
            "findings": [{"code": finding}],
            "features": {
                "deep_audio_checked": deep_audio,
                "preview_declared": True,
                "preview_available": True,
            },
        }

    def probe(raw):
        return 180.0 if raw == source else 30.0

    def render(raw, start, target_duration):
        assert raw == source
        assert start >= 0
        assert target_duration == 30.0
        return candidate

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=PlanningError,
        log=logging.getLogger("preview-repair-test"),
        probe_duration=probe,
        render_preview=render,
    )

    def read_member(path, limit):
        raw = members[path]
        assert len(raw) <= limit
        return raw

    return engine, read_member, candidate


def test_ffmpeg_resolution_prefers_feedback_desktop_bundle(
    tmp_path, monkeypatch, preview_repair
):
    python = tmp_path / "resources" / "python" / "python.exe"
    tools = tmp_path / "resources" / "bin"
    python.parent.mkdir(parents=True)
    tools.mkdir(parents=True)
    python.write_bytes(b"")
    (tools / "vgmstream-cli.exe").write_bytes(b"")
    ffmpeg = tools / "ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    monkeypatch.delenv("FEEDBACK_FFMPEG", raising=False)
    monkeypatch.setattr(preview_repair.sys, "executable", str(python))
    monkeypatch.setattr(preview_repair.shutil, "which", lambda _name: None)

    assert Path(preview_repair._resolve_ffmpeg()) == ffmpeg


def test_ffmpeg_resolution_honors_an_explicit_existing_converter(
    tmp_path, monkeypatch, preview_repair
):
    ffmpeg = tmp_path / "custom-ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    monkeypatch.setenv("FEEDBACK_FFMPEG", str(ffmpeg))

    assert Path(preview_repair._resolve_ffmpeg()) == ffmpeg.resolve()


def test_manifest_preview_update_preserves_or_safely_rebuilds_yaml(preview_repair):
    preserved = preview_repair._manifest_with_preview(
        b"title: Song\r\npreview: old.ogg # keep this\r\n",
        "preview.ogg",
        {"title": "Song", "preview": "old.ogg"},
    )
    assert preserved == b"title: Song\r\npreview: preview.ogg # keep this\r\n"

    rebuilt = preview_repair._manifest_with_preview(
        b"title: Song\n",
        "preview.ogg",
        {"title": "Song", "preview": "old.ogg"},
    )
    assert yaml.safe_load(rebuilt) == {"title": "Song", "preview": "preview.ogg"}

    inserted = preview_repair._manifest_with_preview(
        b"title: Song\n...\n",
        "preview.ogg",
        {"title": "Song"},
    )
    assert inserted == b"title: Song\npreview: preview.ogg\n...\n"

    without_final_newline = preview_repair._manifest_with_preview(
        b"title: Song",
        "preview.ogg",
        {"title": "Song"},
    )
    assert without_final_newline == b"title: Song\npreview: preview.ogg\n"

    with pytest.raises(ValueError, match="not UTF-8"):
        preview_repair._manifest_with_preview(
            b"\xff", "preview.ogg", {"title": "Song"}
        )


def test_loudness_selector_chooses_the_stronger_contiguous_window(
    monkeypatch, preview_repair
):
    quiet = (1).to_bytes(2, "little", signed=True) * 400
    loud = (100).to_bytes(2, "little", signed=True) * 400
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        Path(command[-1]).write_bytes(quiet + loud)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: "ffmpeg-test")
    monkeypatch.setattr(preview_repair.subprocess, "run", run)

    start = preview_repair._loudest_start_with_ffmpeg(
        b"OggS-test-audio", duration=2.0, target_duration=1.0
    )

    assert start == 1.0
    assert observed["command"][0] == "ffmpeg-test"
    assert str(preview_repair.LOUDNESS_SAMPLE_RATE) in observed["command"]
    assert str(preview_repair.MAX_LOUDNESS_PCM_BYTES) in observed["command"]
    assert observed["kwargs"]["stdout"] is preview_repair.subprocess.DEVNULL
    assert observed["kwargs"]["stderr"] is preview_repair.subprocess.DEVNULL
    assert "capture_output" not in observed["kwargs"]
    assert observed["kwargs"]["timeout"] == 120


def test_ffmpeg_probe_and_render_use_bounded_audio_commands(
    monkeypatch, preview_repair
):
    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: "ffmpeg-test")
    probe_call = {}

    def probe_run(command, **kwargs):
        probe_call.update(command=command, kwargs=kwargs)
        return SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"Duration: 01:02:03.50, start: 0.000000",
        )

    monkeypatch.setattr(preview_repair.subprocess, "run", probe_run)
    assert preview_repair._probe_with_ffmpeg(b"OggS-source") == 3723.5
    assert probe_call["command"][0] == "ffmpeg-test"
    assert probe_call["kwargs"]["timeout"] == 30

    render_calls = []
    candidate = b"OggS" + (b"preview" * 200)

    def render_run(command, **kwargs):
        render_calls.append((command, kwargs))
        if len(render_calls) == 1:
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"codec")
        Path(command[-1]).write_bytes(candidate)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(preview_repair.subprocess, "run", render_run)
    rendered = preview_repair._render_with_ffmpeg(
        b"OggS-source", start=4.25, target_duration=30.0
    )

    assert rendered == candidate
    assert len(render_calls) == 2
    assert "libvorbis" in render_calls[0][0]
    assert "vorbis" in render_calls[1][0]
    assert "4.250" in render_calls[0][0]
    assert "30.000" in render_calls[0][0]
    assert all(call[1]["timeout"] == 180 for call in render_calls)


def test_ffmpeg_helpers_fail_clearly_when_the_converter_or_output_is_unusable(
    monkeypatch, preview_repair
):
    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: None)
    with pytest.raises(RuntimeError, match="could not find"):
        preview_repair._probe_with_ffmpeg(b"source")
    with pytest.raises(RuntimeError, match="could not find"):
        preview_repair._render_with_ffmpeg(b"source", 0.0, 30.0)

    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: "ffmpeg-test")
    monkeypatch.setattr(
        preview_repair.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"no duration here"
        ),
    )
    with pytest.raises(RuntimeError, match="duration could not be confirmed"):
        preview_repair._probe_with_ffmpeg(b"source")
    with pytest.raises(RuntimeError, match="could not encode"):
        preview_repair._render_with_ffmpeg(b"source", 0.0, 30.0)

    def cannot_start(*_args, **_kwargs):
        raise OSError("converter unavailable")

    monkeypatch.setattr(preview_repair.subprocess, "run", cannot_start)
    with pytest.raises(RuntimeError, match="could not inspect"):
        preview_repair._probe_with_ffmpeg(b"source")
    with pytest.raises(RuntimeError, match="could not generate"):
        preview_repair._render_with_ffmpeg(b"source", 0.0, 30.0)


def test_loudness_selection_handles_unavailable_or_insufficient_pcm(
    monkeypatch, preview_repair
):
    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: None)
    assert preview_repair._loudest_start_with_ffmpeg(b"source", 60.0, 30.0) == 0.0

    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: "ffmpeg-test")
    monkeypatch.setattr(
        preview_repair.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    assert preview_repair._loudest_start_with_ffmpeg(b"source", 60.0, 30.0) is None

    short_pcm = (1).to_bytes(2, "little", signed=True) * 10

    def short_run(command, **_kwargs):
        Path(command[-1]).write_bytes(short_pcm)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        preview_repair.subprocess,
        "run",
        short_run,
    )
    assert preview_repair._loudest_start_with_ffmpeg(b"source", 60.0, 30.0) == 0.0


def test_loudness_selection_rejects_oversized_pcm_and_cleans_timeout_files(
    monkeypatch, preview_repair
):
    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: "ffmpeg-test")
    observed_paths = []

    def oversized_run(command, **_kwargs):
        output = Path(command[-1])
        observed_paths.append(output)
        with output.open("wb") as stream:
            stream.seek(preview_repair.MAX_LOUDNESS_PCM_BYTES)
            stream.write(b"x")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(preview_repair.subprocess, "run", oversized_run)
    assert preview_repair._loudest_start_with_ffmpeg(b"source", 60.0, 30.0) is None
    assert observed_paths and not observed_paths[-1].parent.exists()

    def timeout_run(command, **_kwargs):
        output = Path(command[-1])
        observed_paths.append(output)
        output.write_bytes(b"partial")
        raise preview_repair.subprocess.TimeoutExpired(command, 120)

    monkeypatch.setattr(preview_repair.subprocess, "run", timeout_run)
    assert preview_repair._loudest_start_with_ffmpeg(b"source", 60.0, 30.0) is None
    assert not observed_paths[-1].parent.exists()


def test_loudness_selection_refuses_unbounded_duration(monkeypatch, preview_repair):
    called = False

    def run(*_args, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: "ffmpeg-test")
    monkeypatch.setattr(preview_repair.subprocess, "run", run)

    assert preview_repair._loudest_start_with_ffmpeg(
        b"source",
        preview_repair.MAX_LOUDNESS_ANALYSIS_SECONDS + 1,
        30.0,
    ) is None
    assert called is False


def test_preview_candidate_uses_lyric_cue_and_is_source_bound(
    tmp_path, preview_repair
):
    source = b"OggS" + b"full-song-preview" * 500
    manifest = {
        "preview": "preview.ogg",
        "lyrics": "lyrics.json",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    members = {
        "manifest.yaml": yaml.safe_dump(manifest).encode(),
        "preview.ogg": source,
        "stems/full.ogg": source,
        "lyrics.json": b'[{"w":"Hello","t":12.0,"d":0.2}]',
    }
    engine, read_member, candidate = _engine(preview_repair, members)

    plan = engine.preview(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        "media.preview-too-long",
        read_member,
        catalog_version="repairs-test",
        validator_version="rules-test",
    )

    assert plan["available"] is True
    assert plan["change_kind"] == "replace_media"
    assert plan["safety"] == "review_required"
    assert plan["media"]["start_seconds"] == 10.0
    assert "first vocal" in plan["media"]["selection_reason"]
    assert plan["media"]["candidate_duration_seconds"] == 30.0
    assert engine.audio(plan["plan_id"]) == candidate
    claimed = engine.claim(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        "media.preview-too-long",
        plan["plan_id"],
        read_member,
    )
    assert claimed["_members"][0]["raw"] == source
    assert claimed["_members"][0]["replacement"] == candidate

    members["preview.ogg"] = source + b"changed"
    with pytest.raises(PlanningError, match="preview changed") as raised:
        engine.claim(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "media.preview-too-long",
            plan["plan_id"],
            read_member,
        )
    assert raised.value.code == "source_changed"


def test_user_selected_start_and_candidate_expiry(tmp_path, preview_repair):
    source = b"OggS" + b"full-song-preview" * 500
    members = {
        "manifest.yaml": yaml.safe_dump({
            "preview": "preview.ogg",
            "arrangements": [],
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode(),
        "preview.ogg": source,
        "stems/full.ogg": source,
    }
    engine, read_member, _candidate = _engine(
        preview_repair,
        members,
        finding="media.preview-too-long",
    )
    plan = engine.preview(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        "media.preview-too-long",
        read_member,
        catalog_version="repairs-test",
        validator_version="rules-test",
        start_seconds=90,
    )

    assert plan["media"]["start_seconds"] == 90.0
    assert plan["media"]["selection_reason"] == "user-selected position"

    with pytest.raises(PlanningError, match="between 0 and 150.0") as raised:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "media.preview-too-long",
            read_member,
            catalog_version="repairs-test",
            validator_version="rules-test",
            start_seconds=151,
        )
    assert raised.value.code == "invalid_preview_start"

    engine.discard(plan["plan_id"])
    with pytest.raises(PlanningError, match="expired") as raised:
        engine.audio(plan["plan_id"])
    assert raised.value.code == "preview_expired"


def test_preview_repair_requires_the_current_deep_audio_finding(
    tmp_path, preview_repair
):
    source = b"OggS" + b"full-song-preview" * 500
    members = {
        "manifest.yaml": yaml.safe_dump({
            "preview": "preview.ogg",
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode(),
        "preview.ogg": source,
        "stems/full.ogg": source,
    }

    def validate(_path, package_name, *, deep_audio=False):
        assert deep_audio is True
        return {"package": package_name, "findings": []}

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=PlanningError,
        log=logging.getLogger("preview-repair-test"),
        probe_duration=lambda _raw: 180.0,
        render_preview=lambda _raw, _start, _duration: b"OggS" + b"short" * 300,
    )

    with pytest.raises(PlanningError, match="no longer present") as raised:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "media.preview-too-long",
            lambda path, _limit: members[path],
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert raised.value.code == "nothing_to_repair"


def test_preview_repair_refuses_ambiguous_duplicate_manifest_keys(
    tmp_path, preview_repair
):
    source = b"OggS" + b"full-song-preview" * 500
    members = {
        "manifest.yaml": b"preview: preview.ogg\npreview: other.ogg\n",
        "preview.ogg": source,
        "stems/full.ogg": source,
    }
    engine, read_member, _candidate = _engine(preview_repair, members)

    with pytest.raises(PlanningError, match="cannot be read safely") as raised:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "media.preview-too-long",
            read_member,
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert raised.value.code == "manifest_unavailable"


def test_automatic_selection_uses_a_representative_section_before_audio_energy(
    tmp_path, preview_repair
):
    source = b"OggS" + b"full-song-preview" * 500
    candidate = b"OggS" + b"short-preview" * 200
    manifest = {
        "preview": "preview.ogg",
        "song_timeline": "timeline.json",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    members = {
        "manifest.yaml": yaml.safe_dump(manifest).encode(),
        "preview.ogg": source,
        "stems/full.ogg": source,
        "timeline.json": (
            b'{"sections":[{"time":2,"name":"Intro"},'
            b'{"time":42,"name":"Chorus"}]}'
        ),
    }
    energy_calls = []
    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=lambda _path, package_name, **_kwargs: {
            "package": package_name,
            "findings": [{"code": "media.preview-too-long"}],
            "features": {"preview_declared": True},
        },
        error_type=PlanningError,
        log=logging.getLogger("preview-repair-section-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda _raw, _start, _duration: candidate,
        select_loudest_start=lambda *_args: energy_calls.append(True),
    )

    plan = engine.preview(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        "media.preview-too-long",
        lambda path, _limit: members[path],
        catalog_version="repairs-test",
        validator_version="rules-test",
    )

    assert plan["media"]["start_seconds"] == 42.0
    assert "Chorus" in plan["media"]["selection_reason"]
    assert energy_calls == []


def test_automatic_selection_has_deterministic_fallback_and_short_song_target(
    tmp_path, preview_repair
):
    source = b"OggS" + b"short-song" * 200
    candidate = b"OggS" + b"short-preview" * 120
    members = {
        "manifest.yaml": yaml.safe_dump({
            "arrangements": [],
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode(),
        "stems/full.ogg": source,
    }
    rendered = []
    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=lambda _path, package_name, **_kwargs: {
            "package": package_name,
            "findings": [],
            "features": {"preview_declared": False},
        },
        error_type=PlanningError,
        log=logging.getLogger("preview-repair-fallback-test"),
        probe_duration=lambda raw: 12.0 if raw == source else 12.0,
        render_preview=lambda raw, start, target: (
            rendered.append((raw, start, target)) or candidate
        ),
        select_loudest_start=lambda *_args: None,
    )

    plan = engine.preview(
        tmp_path / "song.feedpak",
        "Artist/Short.feedpak",
        "media.preview-missing",
        lambda path, _limit: members[path],
        catalog_version="repairs-test",
        validator_version="rules-test",
    )

    assert rendered == [(source, 0.0, 12.0)]
    assert plan["media"]["candidate_duration_seconds"] == 12.0
    assert plan["media"]["max_start_seconds"] == 0.0
    assert "25% into the song" in plan["media"]["selection_reason"]


def test_preview_generation_refuses_missing_full_mix_and_invalid_candidate(
    tmp_path, preview_repair
):
    manifest_without_mix = yaml.safe_dump({"arrangements": [], "stems": []}).encode()
    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=lambda _path, package_name, **_kwargs: {
            "package": package_name,
            "findings": [],
            "features": {"preview_declared": False},
        },
        error_type=PlanningError,
        log=logging.getLogger("preview-repair-refusal-test"),
        probe_duration=lambda _raw: 60.0,
        render_preview=lambda _raw, _start, _target: b"not-an-ogg",
        select_loudest_start=lambda *_args: 10.0,
    )
    with pytest.raises(PlanningError, match="full mix") as missing:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Missing.feedpak",
            "media.preview-missing",
            lambda path, _limit: {"manifest.yaml": manifest_without_mix}[path],
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert missing.value.code == "full_mix_unavailable"

    source = b"OggS" + b"source" * 300
    members = {
        "manifest.yaml": yaml.safe_dump({
            "arrangements": [],
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode(),
        "stems/full.ogg": source,
    }
    with pytest.raises(PlanningError, match="did not pass") as invalid:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Invalid.feedpak",
            "media.preview-missing",
            lambda path, _limit: members[path],
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert invalid.value.code == "candidate_failed"


def test_valid_preview_can_be_regenerated_without_a_scanner_finding(
    tmp_path, preview_repair
):
    source = b"OggS" + b"full-song" * 500
    current = b"OggS" + b"current-preview" * 200
    candidate = b"OggS" + b"different-preview" * 200
    members = {
        "manifest.yaml": yaml.safe_dump({
            "preview": "preview.ogg",
            "arrangements": [],
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode(),
        "preview.ogg": current,
        "stems/full.ogg": source,
    }
    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=lambda _path, package_name, **_kwargs: {
            "package": package_name,
            "findings": [],
            "features": {
                "preview_declared": True,
                "preview_available": True,
            },
        },
        error_type=PlanningError,
        log=logging.getLogger("preview-regenerate-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda raw, _start, _duration: (
            candidate if raw == source else b""
        ),
        select_loudest_start=lambda _raw, _duration, _target: 32.0,
    )

    plan = engine.preview(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        "media.preview-regenerate",
        lambda path, _limit: members[path],
        catalog_version="repairs-test",
        validator_version="rules-test",
    )

    assert plan["available"] is True
    assert plan["title"] == "Create a different song preview"
    assert plan["media"]["start_seconds"] == 32.0
    assert plan["_members"][0]["raw"] == current
    assert plan["_members"][0]["replacement"] == candidate

    members["manifest.yaml"] = yaml.safe_dump({
        "preview": "preview.mp3",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }).encode()
    members["preview.mp3"] = current
    with pytest.raises(PlanningError) as unsupported:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "media.preview-regenerate",
            lambda path, _limit: members[path],
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert unsupported.value.code == "preview_unsupported"


def test_preview_tool_status_supports_missing_and_existing_previews(
    tmp_path, preview_repair
):
    manifest = {
        "title": "Song",
        "artist": "Artist",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    members = {
        "manifest.yaml": yaml.safe_dump(manifest).encode(),
        "stems/full.ogg": b"OggSfull",
    }

    def validate(*_args, **_kwargs):
        raise AssertionError("opening Preview Creator must not run a full validation")

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=PlanningError,
        log=logging.getLogger("preview-tool-status-test"),
    )
    def read_member(path, _limit):
        return members[path]

    def member_exists(path):
        return path in members

    missing = engine.tool_status(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        read_member,
        member_exists,
    )
    assert missing["available"] is True
    assert missing["rule_code"] == "media.preview-missing"
    assert missing["current_preview_available"] is False
    assert missing["title"] == "Song"
    assert missing["artist"] == "Artist"

    manifest["preview"] = "preview.ogg"
    members["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    members["preview.ogg"] = b"OggSpreview"
    existing = engine.tool_status(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        read_member,
        member_exists,
    )
    assert existing["available"] is True
    assert existing["rule_code"] == "media.preview-regenerate"
    assert existing["current_preview_available"] is True

    manifest["preview"] = "preview.mp3"
    members["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    members["preview.mp3"] = b"unsupported-preview"
    unsupported = engine.tool_status(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        read_member,
        member_exists,
    )
    assert unsupported["available"] is False
    assert unsupported["rule_code"] is None
    assert "format" in unsupported["message"]

    manifest["preview"] = "missing-preview.ogg"
    members["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    unavailable = engine.tool_status(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        read_member,
        member_exists,
    )
    assert unavailable["available"] is False
    assert "unavailable" in unavailable["message"]

    members.pop("stems/full.ogg")
    no_full_mix = engine.tool_status(
        tmp_path / "song.feedpak",
        "Artist/Song.feedpak",
        read_member,
        member_exists,
    )
    assert no_full_mix["available"] is False
    assert "full mix" in no_full_mix["message"]

    with pytest.raises(PlanningError, match="does not have") as raised:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "chart.duplicate-note",
            read_member,
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert raised.value.code == "unsupported_repair"


def test_valid_preview_regeneration_applies_and_finishes_without_recovery(
    tmp_path, preview_repair, repair
):
    library = tmp_path / "library"
    package = library / "Artist" / "Valid.feedpak"
    (package / "stems").mkdir(parents=True)
    source = b"OggS" + b"full-song-audio" * 500
    current = b"OggS" + b"current-preview" * 200
    candidate = b"OggS" + b"replacement-preview" * 200
    (package / "manifest.yaml").write_bytes(yaml.safe_dump({
        "feedpak_version": "1.19.0",
        "title": "Valid",
        "artist": "Artist",
        "duration": 180,
        "preview": "preview.ogg",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }, sort_keys=False).encode())
    (package / "preview.ogg").write_bytes(current)
    (package / "stems" / "full.ogg").write_bytes(source)

    def validate(path, package_name, *, deep_audio=False):
        preview_exists = (Path(path) / "preview.ogg").is_file()
        return {
            "package": package_name,
            "title": "Valid",
            "artist": "Artist",
            "findings": [],
            "features": {
                "deep_audio_checked": deep_audio,
                "preview_declared": True,
                "preview_available": preview_exists,
            },
        }

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=repair.RepairPlanningError,
        log=logging.getLogger("preview-regenerate-service-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda raw, _start, _duration: (
            candidate if raw == source else b""
        ),
        select_loudest_start=lambda _raw, _duration, _target: 40.0,
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("preview-regenerate-service-test"),
        preview_repair=engine,
    )

    plan = service.preview("Artist/Valid.feedpak", "media.preview-regenerate")
    result = service.apply(
        "Artist/Valid.feedpak",
        "media.preview-regenerate",
        plan["plan_id"],
    )

    assert result["outcome"] == "success"
    assert result["undo_available"] is False
    assert result["file_handling"]["backup_removed"] is True
    assert result["file_handling"]["backup_retained"] is False
    assert (package / "preview.ogg").read_bytes() == candidate
    assert service.current_preview_audio("Artist/Valid.feedpak") == candidate


def test_valid_preview_regeneration_rejects_an_identical_candidate(
    tmp_path, preview_repair
):
    source = b"OggS" + b"full-song" * 500
    current = b"OggS" + b"current-preview" * 200
    members = {
        "manifest.yaml": yaml.safe_dump({
            "preview": "preview.ogg",
            "arrangements": [],
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode(),
        "preview.ogg": current,
        "stems/full.ogg": source,
    }
    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=lambda _path, package_name, **_kwargs: {
            "package": package_name,
            "findings": [],
            "features": {
                "preview_declared": True,
                "preview_available": True,
            },
        },
        error_type=PlanningError,
        log=logging.getLogger("preview-identical-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda _raw, _start, _duration: current,
        select_loudest_start=lambda _raw, _duration, _target: 32.0,
    )

    with pytest.raises(PlanningError) as unchanged:
        engine.preview(
            tmp_path / "song.feedpak",
            "Artist/Song.feedpak",
            "media.preview-regenerate",
            lambda path, _limit: members[path],
            catalog_version="repairs-test",
            validator_version="rules-test",
        )
    assert unchanged.value.code == "preview_unchanged"


def test_repair_service_reports_cleanup_failure_without_hiding_success(
    tmp_path, monkeypatch, preview_repair, repair
):
    library = tmp_path / "library"
    package = library / "Artist" / "Song.feedpak"
    package.mkdir(parents=True)
    source = b"OggS" + b"full-song-preview" * 500
    candidate = b"OggS" + b"short-preview" * 200
    manifest_raw = yaml.safe_dump({
        "feedpak_version": "1.19.0",
        "title": "Song",
        "artist": "Artist",
        "duration": 180,
        "preview": "preview.ogg",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }).encode()
    (package / "manifest.yaml").write_bytes(manifest_raw)
    (package / "preview.ogg").write_bytes(source)
    (package / "stems").mkdir()
    (package / "stems" / "full.ogg").write_bytes(source)
    untouched = b"cover stays byte-identical"
    (package / "cover.jpg").write_bytes(untouched)

    def validate(path, package_name, *, deep_audio=False):
        raw = (Path(path) / "preview.ogg").read_bytes()
        findings = ([{"code": "media.preview-too-long"}]
                    if raw == source else [])
        return {
            "package": package_name,
            "title": "Song",
            "artist": "Artist",
            "findings": findings,
            "features": {
                "deep_audio_checked": deep_audio,
                "preview_declared": True,
                "preview_available": True,
            },
        }

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=repair.RepairPlanningError,
        log=logging.getLogger("preview-repair-service-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda raw, _start, _duration: candidate if raw == source else b"",
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("preview-repair-service-test"),
        preview_repair=engine,
    )

    plan = service.preview(
        "Artist/Song.feedpak", "media.preview-too-long"
    )
    assert (package / "preview.ogg").read_bytes() == source

    delete_backup = service._delete_backup

    def fail_cleanup(_backup_id):
        raise repair.RepairPlanningError(
            "backup_cleanup_failed", "Temporary recovery could not be removed."
        )

    monkeypatch.setattr(service, "_delete_backup", fail_cleanup)

    result = service.apply(
        "Artist/Song.feedpak",
        "media.preview-too-long",
        plan["plan_id"],
    )
    assert result["outcome"] == "success"
    assert result["change_kind"] == "replace_media"
    assert result["deep_audio"] is True
    assert result["undo_available"] is True
    assert result["file_handling"]["backup_removed"] is False
    assert result["file_handling"]["backup_retained"] is True
    assert result["file_handling"]["backup_cleanup_required"] is True
    assert (package / "preview.ogg").read_bytes() == candidate
    assert service.current_preview_audio("Artist/Song.feedpak") == candidate
    assert (package / "manifest.yaml").read_bytes() == manifest_raw
    assert (package / "cover.jpg").read_bytes() == untouched

    monkeypatch.setattr(service, "_delete_backup", delete_backup)
    finalized = service.finalize_backup(
        "Artist/Song.feedpak", result["backup_id"]
    )
    assert finalized["outcome"] == "finalized"
    assert finalized["file_handling"]["backup_removed"] is True

    with pytest.raises(repair.RepairPlanningError) as unavailable:
        service.restore("Artist/Song.feedpak", result["backup_id"], deep_audio=True)
    assert unavailable.value.code == "backup_unavailable"
    assert (package / "preview.ogg").read_bytes() == candidate
    assert (package / "manifest.yaml").read_bytes() == manifest_raw
    assert (package / "cover.jpg").read_bytes() == untouched


def test_missing_preview_is_added_and_temporary_recovery_is_removed(
    tmp_path, preview_repair, repair
):
    library = tmp_path / "library"
    package = library / "Artist" / "Missing.feedpak"
    (package / "stems").mkdir(parents=True)
    source = b"OggS" + b"full-song-audio" * 500
    candidate = b"OggS" + b"generated-preview" * 200
    manifest_raw = yaml.safe_dump({
        "feedpak_version": "1.19.0",
        "title": "Missing",
        "artist": "Artist",
        "duration": 180,
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }, sort_keys=False).encode()
    (package / "manifest.yaml").write_bytes(manifest_raw)
    (package / "stems" / "full.ogg").write_bytes(source)

    def validate(path, package_name, *, deep_audio=False):
        manifest = yaml.safe_load((Path(path) / "manifest.yaml").read_bytes())
        preview_path = manifest.get("preview")
        preview_exists = bool(preview_path and (Path(path) / preview_path).is_file())
        return {
            "package": package_name,
            "title": "Missing",
            "artist": "Artist",
            "findings": [] if preview_exists else [{"code": "media.preview-missing"}],
            "features": {
                "deep_audio_checked": deep_audio,
                "preview_declared": preview_exists,
                "preview_available": preview_exists,
            },
        }

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=repair.RepairPlanningError,
        log=logging.getLogger("preview-repair-missing-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda raw, _start, _duration: (
            candidate if raw == source else b""
        ),
        select_loudest_start=lambda _raw, _duration, _target: 45.0,
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("preview-repair-missing-test"),
        preview_repair=engine,
    )

    with pytest.raises(repair.RepairPlanningError) as unavailable:
        service.current_preview_audio("Artist/Missing.feedpak")
    assert unavailable.value.code == "preview_unavailable"

    unsupported_manifest = yaml.safe_load(manifest_raw)
    unsupported_manifest["preview"] = "preview.mp3"
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(unsupported_manifest, sort_keys=False), encoding="utf-8"
    )
    (package / "preview.mp3").write_bytes(b"OggS" + b"audio")
    with pytest.raises(repair.RepairPlanningError) as unsupported:
        service.current_preview_audio("Artist/Missing.feedpak")
    assert unsupported.value.code == "preview_unsupported"

    unsupported_manifest["preview"] = "preview.ogg"
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(unsupported_manifest, sort_keys=False), encoding="utf-8"
    )
    (package / "preview.ogg").write_bytes(b"not-ogg-audio")
    with pytest.raises(repair.RepairPlanningError) as invalid_audio:
        service.current_preview_audio("Artist/Missing.feedpak")
    assert invalid_audio.value.code == "preview_unavailable"

    (package / "preview.mp3").unlink()
    (package / "preview.ogg").unlink()
    (package / "manifest.yaml").write_bytes(manifest_raw)

    result = service.apply_automatic_preview(
        "Artist/Missing.feedpak", "media.preview-missing"
    )

    repaired_manifest = yaml.safe_load((package / "manifest.yaml").read_bytes())
    preview_path = repaired_manifest["preview"]
    assert result["outcome"] == "success"
    assert result["media"]["creates_preview"] is True
    assert result["media"]["selection_reason"] == (
        "loudest representative section of the full mix"
    )
    assert result["undo_available"] is False
    assert result["file_handling"]["backup_removed"] is True
    assert (package / preview_path).read_bytes() == candidate
    assert (package / "stems" / "full.ogg").read_bytes() == source

    with pytest.raises(repair.RepairPlanningError) as unavailable:
        service.restore("Artist/Missing.feedpak", result["backup_id"], deep_audio=True)
    assert unavailable.value.code == "backup_unavailable"
    assert yaml.safe_load((package / "manifest.yaml").read_bytes())["preview"] == preview_path
    assert (package / preview_path).read_bytes() == candidate
    assert (package / "stems" / "full.ogg").read_bytes() == source


def test_preview_pointer_to_full_mix_creates_a_separate_member(
    tmp_path, preview_repair, repair
):
    library = tmp_path / "library"
    package = library / "Artist" / "Shared.feedpak"
    (package / "stems").mkdir(parents=True)
    source = b"OggS" + b"full-song-audio" * 500
    candidate = b"OggS" + b"generated-preview" * 200
    manifest = {
        "feedpak_version": "1.19.0",
        "title": "Shared",
        "artist": "Artist",
        "duration": 180,
        "preview": "stems/full.ogg",
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (package / "stems" / "full.ogg").write_bytes(source)

    def validate(path, package_name, *, deep_audio=False):
        current = yaml.safe_load((Path(path) / "manifest.yaml").read_bytes())
        shared = current.get("preview") == "stems/full.ogg"
        return {
            "package": package_name,
            "title": "Shared",
            "artist": "Artist",
            "findings": ([{"code": "media.preview-too-long"}] if shared else []),
            "features": {"deep_audio_checked": deep_audio},
        }

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=repair.RepairPlanningError,
        log=logging.getLogger("preview-repair-shared-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda raw, _start, _duration: (
            candidate if raw == source else b""
        ),
        select_loudest_start=lambda _raw, _duration, _target: 20.0,
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("preview-repair-shared-test"),
        preview_repair=engine,
    )

    result = service.apply_automatic_preview(
        "Artist/Shared.feedpak", "media.preview-too-long"
    )

    repaired_manifest = yaml.safe_load((package / "manifest.yaml").read_bytes())
    assert repaired_manifest["preview"] != "stems/full.ogg"
    assert (package / repaired_manifest["preview"]).read_bytes() == candidate
    assert (package / "stems" / "full.ogg").read_bytes() == source
    assert result["media"]["source_path"] == "stems/full.ogg"
    assert result["undo_available"] is False
    assert result["file_handling"]["backup_removed"] is True

    generated_path = repaired_manifest["preview"]
    with pytest.raises(repair.RepairPlanningError) as unavailable:
        service.restore("Artist/Shared.feedpak", result["backup_id"], deep_audio=True)
    assert unavailable.value.code == "backup_unavailable"
    assert yaml.safe_load((package / "manifest.yaml").read_bytes())["preview"] == generated_path
    assert (package / generated_path).read_bytes() == candidate
    assert (package / "stems" / "full.ogg").read_bytes() == source


def test_automatic_archive_preview_reuses_verified_before_report(
    tmp_path, preview_repair, repair,
):
    library = tmp_path / "library"
    package = library / "Artist" / "Missing.feedpak"
    package.parent.mkdir(parents=True)
    source = b"OggS" + b"full-song-audio" * 500
    candidate = b"OggS" + b"generated-preview" * 200
    manifest = {
        "feedpak_version": "1.19.0",
        "title": "Missing",
        "artist": "Artist",
        "duration": 180,
        "arrangements": [],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml", yaml.safe_dump(manifest, sort_keys=False)
        )
        archive.writestr("stems/full.ogg", source)
    validated = []

    def validate(path, package_name, *, deep_audio=False):
        validated.append(deep_audio)
        with zipfile.ZipFile(path, "r") as archive:
            current = yaml.safe_load(archive.read("manifest.yaml"))
            preview_path = current.get("preview")
            preview_exists = bool(
                isinstance(preview_path, str)
                and preview_path in archive.namelist()
            )
        findings = [] if preview_exists else [{"code": "media.preview-missing"}]
        return {
            "package": package_name,
            "validator_version": "rules-test",
            "findings": findings,
            "counts": {
                "error": 0,
                "warning": len(findings),
                "info": 0,
            },
            "status": "warning" if findings else "healthy",
            "features": {
                "deep_audio_checked": deep_audio,
                "preview_declared": preview_exists,
                "preview_available": preview_exists,
                "preview_source_available": True,
            },
        }

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=repair.RepairPlanningError,
        log=logging.getLogger("preview-repair-verified-before-test"),
        probe_duration=lambda raw: 180.0 if raw == source else 30.0,
        render_preview=lambda raw, _start, _duration: (
            candidate if raw == source else b""
        ),
        select_loudest_start=lambda _raw, _duration, _target: 20.0,
    )
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("preview-repair-verified-before-test"),
        preview_repair=engine,
    )
    before = {
        "package": "Artist/Missing.feedpak",
        "validator_version": "rules-test",
        "findings": [{"code": "media.preview-missing"}],
        "counts": {"error": 0, "warning": 1, "info": 0},
        "status": "warning",
        "features": {
            "deep_audio_checked": True,
            "deep_audio_files": 1,
            "deep_audio_skipped": 0,
            "deep_audio_unsupported": 0,
            "preview_declared": False,
            "preview_available": False,
            "preview_source_available": True,
        },
    }
    guard_calls = []

    result = service.apply_automatic_preview(
        "Artist/Missing.feedpak",
        "media.preview-missing",
        verified_before_report=before,
        source_guard=lambda: guard_calls.append(True) or True,
    )

    assert validated == [True]
    assert len(guard_calls) == 3
    assert result["verified_scan_report_reused"] is True
    assert result["deep_audio_reused"] is False
    assert result["performance"]["deep_audio_requested"] is True
    assert result["performance"]["verified_scan_report_reused"] is True
    assert result["performance"]["deep_audio_reused"] is False
    assert result["performance"]["elapsed_seconds"] >= 0
    assert service.history(limit=1)["items"][0]["performance"] == (
        result["performance"]
    )
    with zipfile.ZipFile(package, "r") as archive:
        repaired_manifest = yaml.safe_load(archive.read("manifest.yaml"))
        assert archive.read(repaired_manifest["preview"]) == candidate
