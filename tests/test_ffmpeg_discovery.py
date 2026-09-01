import concurrent.futures
import importlib.util
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture()
def preview_repair():
    path = Path(__file__).parents[1] / "preview_repair.py"
    name = "library_doctor_ffmpeg_discovery_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _fake_imageio(monkeypatch, preview_repair, getter):
    real_import = preview_repair.importlib.import_module
    fake = SimpleNamespace(get_ffmpeg_exe=getter)

    def import_module(name):
        return fake if name == "imageio_ffmpeg" else real_import(name)

    monkeypatch.setattr(preview_repair.importlib, "import_module", import_module)


def test_portable_dependency_supplies_ffmpeg_without_desktop_bundle_or_path(
    tmp_path, monkeypatch, preview_repair
):
    portable = tmp_path / "imageio-ffmpeg.exe"
    portable.write_bytes(b"portable")
    monkeypatch.delenv("FEEDBACK_FFMPEG", raising=False)
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(preview_repair, "_resolve_bundled_ffmpeg", lambda: None)
    monkeypatch.setattr(preview_repair.shutil, "which", lambda _name: None)
    _fake_imageio(monkeypatch, preview_repair, lambda: str(portable))

    assert preview_repair._resolve_ffmpeg() == str(portable.resolve())


def test_invalid_feedback_override_falls_through_to_portable_dependency(
    tmp_path, monkeypatch, preview_repair
):
    portable = tmp_path / "portable-ffmpeg"
    portable.write_bytes(b"portable")
    monkeypatch.setenv("FEEDBACK_FFMPEG", str(tmp_path / "missing-ffmpeg"))
    monkeypatch.setattr(preview_repair, "_resolve_bundled_ffmpeg", lambda: None)
    monkeypatch.setattr(preview_repair.shutil, "which", lambda _name: None)
    _fake_imageio(monkeypatch, preview_repair, lambda: str(portable))

    assert preview_repair._resolve_ffmpeg() == str(portable.resolve())


def test_feedback_bundle_wins_before_portable_dependency(
    tmp_path, monkeypatch, preview_repair
):
    bundled = tmp_path / "ffmpeg.exe"
    bundled.write_bytes(b"bundled")
    monkeypatch.delenv("FEEDBACK_FFMPEG", raising=False)
    monkeypatch.setattr(
        preview_repair, "_resolve_bundled_ffmpeg", lambda: str(bundled.resolve())
    )

    def unexpected_import(_name):
        raise AssertionError("portable fallback should not be imported")

    monkeypatch.setattr(preview_repair.importlib, "import_module", unexpected_import)

    assert preview_repair._resolve_ffmpeg() == str(bundled.resolve())


def test_unusable_portable_candidate_falls_back_to_system_path(
    tmp_path, monkeypatch, preview_repair
):
    system = tmp_path / "system-ffmpeg"
    system.write_bytes(b"system")
    monkeypatch.delenv("FEEDBACK_FFMPEG", raising=False)
    monkeypatch.setattr(preview_repair, "_resolve_bundled_ffmpeg", lambda: None)
    monkeypatch.setattr(
        preview_repair.shutil,
        "which",
        lambda name: str(system) if name == "ffmpeg" else None,
    )
    _fake_imageio(monkeypatch, preview_repair, lambda: str(tmp_path))

    assert preview_repair._resolve_ffmpeg() == str(system.resolve())


def test_existing_invalid_feedback_override_falls_through_and_caches_portable(
    tmp_path, monkeypatch, preview_repair
):
    configured = tmp_path / "configured-ffmpeg.exe"
    portable = tmp_path / "portable-ffmpeg.exe"
    configured.write_bytes(b"not-ffmpeg")
    portable.write_bytes(b"portable")
    monkeypatch.setenv("FEEDBACK_FFMPEG", str(configured))
    monkeypatch.setattr(preview_repair, "_resolve_bundled_ffmpeg", lambda: None)
    monkeypatch.setattr(preview_repair.shutil, "which", lambda _name: None)
    _fake_imageio(monkeypatch, preview_repair, lambda: str(portable))
    checked = []

    def run(command, **_kwargs):
        checked.append(command[0])
        identity = (
            b"not an audio converter"
            if command[0] == str(configured.resolve())
            else b"ffmpeg version portable-test"
        )
        return SimpleNamespace(returncode=0, stdout=identity, stderr=b"")

    monkeypatch.setattr(preview_repair.subprocess, "run", run)

    assert preview_repair._require_ffmpeg("inspect the song audio") == str(
        portable.resolve()
    )
    assert preview_repair._require_ffmpeg("inspect the song audio") == str(
        portable.resolve()
    )
    assert checked == [
        str(configured.resolve()),
        str(portable.resolve()),
        str(configured.resolve()),
    ]


def test_nonlaunchable_feedback_bundle_falls_through_to_portable_dependency(
    tmp_path, monkeypatch, preview_repair
):
    bundled = tmp_path / "bundled-ffmpeg.exe"
    portable = tmp_path / "portable-ffmpeg.exe"
    bundled.write_bytes(b"wrong-architecture")
    portable.write_bytes(b"portable")
    monkeypatch.delenv("FEEDBACK_FFMPEG", raising=False)
    monkeypatch.setattr(
        preview_repair, "_resolve_bundled_ffmpeg", lambda: str(bundled.resolve())
    )
    monkeypatch.setattr(preview_repair.shutil, "which", lambda _name: None)
    _fake_imageio(monkeypatch, preview_repair, lambda: str(portable))

    def run(command, **_kwargs):
        if command[0] == str(bundled.resolve()):
            raise OSError("wrong executable architecture")
        return SimpleNamespace(
            returncode=0,
            stdout=b"ffmpeg version portable-test",
            stderr=b"",
        )

    monkeypatch.setattr(preview_repair.subprocess, "run", run)

    assert preview_repair._require_ffmpeg("inspect the song audio") == str(
        portable.resolve()
    )


def test_existing_invalid_portable_override_falls_through_to_system_path(
    tmp_path, monkeypatch, preview_repair
):
    portable = tmp_path / "portable-override.exe"
    system = tmp_path / "system-ffmpeg.exe"
    portable.write_bytes(b"not-ffmpeg")
    system.write_bytes(b"system")
    monkeypatch.delenv("FEEDBACK_FFMPEG", raising=False)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(portable))
    monkeypatch.setattr(preview_repair, "_resolve_bundled_ffmpeg", lambda: None)
    monkeypatch.setattr(
        preview_repair.shutil,
        "which",
        lambda name: str(system) if name == "ffmpeg" else None,
    )
    _fake_imageio(monkeypatch, preview_repair, lambda: str(portable))

    def run(command, **_kwargs):
        identity = (
            b"not an audio converter"
            if command[0] == str(portable.resolve())
            else b"ffmpeg version system-test"
        )
        return SimpleNamespace(returncode=0, stdout=identity, stderr=b"")

    monkeypatch.setattr(preview_repair.subprocess, "run", run)

    assert preview_repair._require_ffmpeg("inspect the song audio") == str(
        system.resolve()
    )


def test_all_invalid_candidates_report_sources_without_exposing_paths(
    tmp_path, monkeypatch, preview_repair
):
    configured = tmp_path / "configured-private-name.exe"
    bundled = tmp_path / "bundled-private-name.exe"
    portable = tmp_path / "portable-private-name.exe"
    system = tmp_path / "system-private-name.exe"
    for candidate in (configured, bundled, portable, system):
        candidate.write_bytes(b"not-ffmpeg")
    monkeypatch.setenv("FEEDBACK_FFMPEG", str(configured))
    monkeypatch.setattr(
        preview_repair, "_resolve_bundled_ffmpeg", lambda: str(bundled.resolve())
    )
    monkeypatch.setattr(
        preview_repair.shutil,
        "which",
        lambda name: str(system) if name == "ffmpeg" else None,
    )
    _fake_imageio(monkeypatch, preview_repair, lambda: str(portable))
    monkeypatch.setattr(
        preview_repair.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"not an audio converter",
            stderr=b"",
        ),
    )

    with pytest.raises(RuntimeError) as error:
        preview_repair._require_ffmpeg("inspect the song audio")

    message = str(error.value)
    assert "FEEDBACK_FFMPEG override" in message
    assert "FeedBack's bundled converter" in message
    assert "Library Doctor's portable converter" in message
    assert "system FFmpeg command" in message
    assert "clear FEEDBACK_FFMPEG" in message
    assert "reinstall Library Doctor" in message
    assert not any(candidate.name in message for candidate in (
        configured, bundled, portable, system
    ))


def test_missing_converter_message_identifies_stale_overrides(
    tmp_path, monkeypatch, preview_repair
):
    monkeypatch.setenv("FEEDBACK_FFMPEG", str(tmp_path / "missing-feedback"))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(tmp_path / "missing-imageio"))
    monkeypatch.setattr(preview_repair, "_resolve_ffmpeg", lambda: None)

    with pytest.raises(RuntimeError) as error:
        preview_repair._probe_with_ffmpeg(b"OggS-source")

    message = str(error.value)
    assert "working FFmpeg audio converter" in message
    assert "FEEDBACK_FFMPEG is set" in message
    assert "IMAGEIO_FFMPEG_EXE is set" in message
    assert "reinstall Library Doctor" in message
    assert "restart FeedBack" in message


def test_converter_launch_failure_gives_source_specific_next_step(
    tmp_path, monkeypatch, preview_repair
):
    configured = tmp_path / "configured-ffmpeg.exe"
    configured.write_bytes(b"not-a-real-executable")
    monkeypatch.setenv("FEEDBACK_FFMPEG", str(configured))
    monkeypatch.setattr(
        preview_repair, "_resolve_ffmpeg", lambda: str(configured.resolve())
    )

    def cannot_start(command, **_kwargs):
        if "-version" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=b"ffmpeg version test-build",
                stderr=b"",
            )
        raise OSError("wrong executable architecture")

    monkeypatch.setattr(preview_repair.subprocess, "run", cannot_start)

    with pytest.raises(RuntimeError) as error:
        preview_repair._render_with_ffmpeg(b"OggS-source", 0.0, 30.0)

    message = str(error.value)
    assert "could not generate the proposed preview" in message
    assert "FEEDBACK_FFMPEG points to an executable" in message
    assert "matches this computer" in message
    assert "restart FeedBack" in message


def test_resolved_converter_must_identify_as_ffmpeg_before_audio_is_processed(
    tmp_path, monkeypatch, preview_repair
):
    impostor = tmp_path / "ffmpeg.exe"
    impostor.write_bytes(b"not-ffmpeg")
    monkeypatch.setattr(
        preview_repair, "_resolve_ffmpeg", lambda: str(impostor.resolve())
    )
    monkeypatch.setattr(
        preview_repair.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"some other executable",
            stderr=b"",
        ),
    )

    with pytest.raises(RuntimeError, match="did not pass FFmpeg's startup check"):
        preview_repair._probe_with_ffmpeg(b"OggS-source")


def test_loudness_analysis_uses_the_verified_converter_and_reuses_its_cache(
    tmp_path, monkeypatch, preview_repair
):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"converter")
    monkeypatch.setattr(
        preview_repair, "_resolve_ffmpeg", lambda: str(ffmpeg.resolve())
    )
    calls = []
    pcm = (25).to_bytes(2, "little", signed=True) * 800

    def run(command, **_kwargs):
        calls.append(command)
        if "-version" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=b"ffmpeg version verified-test",
                stderr=b"",
            )
        Path(command[-1]).write_bytes(pcm)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(preview_repair.subprocess, "run", run)

    assert preview_repair._loudest_start_with_ffmpeg(
        b"OggS-source", 2.0, 1.0
    ) == 0.0
    assert preview_repair._loudest_start_with_ffmpeg(
        b"OggS-source", 2.0, 1.0
    ) == 0.0
    assert sum("-version" in command for command in calls) == 1
    assert len(calls) == 3


def test_preview_generation_overlaps_for_distinct_packages_and_keeps_claimable_plans(
    tmp_path, preview_repair
):
    class PlanningError(ValueError):
        def __init__(self, code, message, *, file_state="unchanged"):
            super().__init__(message)
            self.code = code
            self.file_state = file_state

    barrier = threading.Barrier(2)
    rendered_sources = []
    render_guard = threading.Lock()

    def validate(_path, package_name, *, deep_audio=False):
        assert deep_audio is True
        return {
            "package": package_name,
            "findings": [{"code": "media.preview-regenerate"}],
            "features": {
                "preview_declared": True,
                "preview_available": True,
            },
        }

    def probe(raw):
        return 180.0 if b"full-source" in raw else 30.0

    def render(source, _start, _duration):
        with render_guard:
            rendered_sources.append(source)
        barrier.wait(timeout=5)
        return b"OggS" + (b"candidate-audio" * 100)

    engine = preview_repair.PreviewRepairEngine(
        validate_feedpak=validate,
        error_type=PlanningError,
        log=logging.getLogger("preview-concurrency-test"),
        probe_duration=probe,
        render_preview=render,
    )

    readers = {}
    for suffix in ("one", "two"):
        manifest = yaml.safe_dump({
            "preview": "preview.ogg",
            "stems": [{"id": "full", "file": "stems/full.ogg"}],
        }).encode()
        members = {
            "manifest.yaml": manifest,
            "stems/full.ogg": (f"OggS-full-source-{suffix}".encode() * 100),
            "preview.ogg": (f"OggS-current-preview-{suffix}".encode() * 100),
        }

        def read_member(path, limit, *, package_members=members):
            raw = package_members[path]
            assert len(raw) <= limit
            return raw

        readers[suffix] = read_member

    def generate(suffix):
        return engine.preview(
            tmp_path / f"{suffix}.feedpak",
            f"{suffix}.feedpak",
            "media.preview-regenerate",
            readers[suffix],
            catalog_version="catalog-test",
            validator_version="validator-test",
            start_seconds=0.0,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(generate, suffix) for suffix in ("one", "two")]
        plans = [future.result(timeout=10) for future in futures]

    assert len(rendered_sources) == 2
    assert len({plan["plan_id"] for plan in plans}) == 2
    for suffix, plan in zip(("one", "two"), plans, strict=True):
        claimed = engine.claim(
            tmp_path / f"{suffix}.feedpak",
            f"{suffix}.feedpak",
            "media.preview-regenerate",
            plan["plan_id"],
            readers[suffix],
        )
        assert claimed["plan_id"] == plan["plan_id"]
        assert engine.audio(plan["plan_id"]).startswith(b"OggS")
