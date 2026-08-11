import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _valid_package(root: Path, index: int) -> Path:
    package = root / f"song-{index}.feedpak"
    (package / "arrangements").mkdir(parents=True)
    (package / "stems").mkdir()
    manifest = {
        "feedpak_version": "1.19.0",
        "title": f"Song {index}",
        "artist": "Worker Test",
        "duration": 30.0,
        "arrangements": [{"id": "lead", "file": "arrangements/lead.json"}],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (package / "arrangements" / "lead.json").write_text(
        json.dumps({"notes": [], "chords": []}), encoding="utf-8"
    )
    (package / "stems" / "full.ogg").write_bytes(b"not-a-real-ogg")
    return package


def test_spawned_workers_match_the_in_process_validator(tmp_path):
    root = Path(__file__).parents[1]
    validator = _load(root / "validator.py", "library_doctor_worker_test_validator")
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_test_backend",
    )
    packages = [_valid_package(tmp_path, index) for index in range(3)]
    expected = [
        validator.validate_feedpak(path, path.name)
        for path in packages
    ]
    pool = worker.ValidationProcessPool(
        max_workers=2,
        validator_version=validator.VALIDATOR_VERSION,
    )
    try:
        futures = [pool.submit(path, path.name, False) for path in packages]
        results = [future.result(timeout=30) for future in futures]
    finally:
        pool.shutdown()

    assert [result["outcome"] for result in results] == ["complete"] * 3
    assert [result["report"] for result in results] == expected
    assert all(result["elapsed_seconds"] >= 0 for result in results)


def test_spawned_workers_honor_pause_before_reading_a_package(tmp_path):
    root = Path(__file__).parents[1]
    validator = _load(root / "validator.py", "library_doctor_worker_pause_validator")
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_pause_backend",
    )
    package = _valid_package(tmp_path, 1)
    pool = worker.ValidationProcessPool(
        max_workers=1,
        validator_version=validator.VALIDATOR_VERSION,
    )
    try:
        pool.set_paused(True)
        future = pool.submit(package, package.name, False)
        time.sleep(0.35)
        assert future.done() is False
        pool.set_paused(False)
        result = future.result(timeout=30)
    finally:
        pool.shutdown()

    assert result["outcome"] == "complete"


def test_spawned_workers_wake_from_pause_when_cancelled(tmp_path):
    root = Path(__file__).parents[1]
    validator = _load(root / "validator.py", "library_doctor_worker_cancel_validator")
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_cancel_backend",
    )
    package = _valid_package(tmp_path, 1)
    pool = worker.ValidationProcessPool(
        max_workers=1,
        validator_version=validator.VALIDATOR_VERSION,
    )
    try:
        pool.set_paused(True)
        future = pool.submit(package, package.name, False)
        time.sleep(0.2)
        pool.cancel()
        result = future.result(timeout=30)
    finally:
        pool.shutdown()

    assert result["outcome"] == "cancelled"


def test_worker_loader_rejects_a_validator_version_mismatch():
    root = Path(__file__).parents[1]
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_loader_backend",
    )
    worker._validator = None

    try:
        with pytest.raises(RuntimeError, match="does not match"):
            worker._load_validator(str(root), "not-the-active-version")

        validator = _load(
            root / "validator.py", "library_doctor_worker_loader_validator"
        )
        loaded = worker._load_validator(str(root), validator.VALIDATOR_VERSION)
        assert loaded.VALIDATOR_VERSION == validator.VALIDATOR_VERSION
        assert worker._load_validator(str(root), "ignored-after-load") is loaded
    finally:
        worker._validator = None


def test_direct_worker_task_reports_success_cancellation_and_isolated_errors():
    root = Path(__file__).parents[1]
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_task_backend",
    )
    pause = threading.Event()
    cancel = threading.Event()
    observed = {}

    def validate(path, package, **options):
        observed.update(path=path, package=package, options=options)
        options["scan_checkpoint"]()
        return {"package": package, "ok": True}

    try:
        worker._pause_event = pause
        worker._cancel_event = cancel
        worker._validator = SimpleNamespace(validate_feedpak=validate)

        result = worker._validate_task(("song.feedpak", "Song", True))
        assert result["outcome"] == "complete"
        assert result["report"] == {"package": "Song", "ok": True}
        assert observed["path"] == Path("song.feedpak")
        assert observed["package"] == "Song"
        assert observed["options"]["deep_audio"] is True
        assert callable(observed["options"]["scan_checkpoint"])

        cancel.set()
        cancelled = worker._validate_task(("song.feedpak", "Song", False))
        assert cancelled["outcome"] == "cancelled"

        cancel.clear()

        def fail(*_args, **_kwargs):
            raise LookupError("x" * 1_100)

        worker._validator = SimpleNamespace(validate_feedpak=fail)
        failed = worker._validate_task(("bad.feedpak", "Bad", False))
        assert failed["outcome"] == "error"
        assert failed["error_type"] == "LookupError"
        assert len(failed["error"]) == 1_000
    finally:
        worker._validator = None
        worker._pause_event = None
        worker._cancel_event = None


def test_worker_initialization_and_paused_checkpoint_share_control_events(
    monkeypatch,
):
    root = Path(__file__).parents[1]
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_checkpoint_backend",
    )
    pause = threading.Event()
    cancel = threading.Event()
    loaded = {}

    monkeypatch.setattr(
        worker,
        "_load_validator",
        lambda plugin_dir, version: loaded.update(
            plugin_dir=plugin_dir, version=version
        ),
    )
    worker._initialize_worker("plugin-root", "rules-test", pause, cancel)
    assert loaded == {"plugin_dir": "plugin-root", "version": "rules-test"}
    assert worker._pause_event is pause
    assert worker._cancel_event is cancel

    pause.set()
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: pause.clear())
    worker._checkpoint()

    pause.set()
    cancel.set()
    with pytest.raises(worker._WorkerCancelled):
        worker._checkpoint()

    worker._pause_event = None
    worker._cancel_event = None
