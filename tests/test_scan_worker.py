import importlib.util
import json
import multiprocessing
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


def test_worker_pool_exposes_best_effort_rss_for_budget_enforcement(tmp_path):
    root = Path(__file__).parents[1]
    validator = _load(root / "validator.py", "library_doctor_worker_rss_validator")
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_rss_backend",
    )
    package = _valid_package(tmp_path, 7)
    pool = worker.ValidationProcessPool(
        max_workers=1,
        validator_version=validator.VALIDATOR_VERSION,
    )
    try:
        future = pool.submit(package, package.name, False)
        future.result(timeout=30)
        usage = pool.memory_usage()
    finally:
        pool.shutdown()

    assert usage
    assert all(pid > 0 and rss > 0 for pid, rss in usage.items())


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


def test_validation_worker_write_guard_denies_package_mutation(tmp_path):
    root = Path(__file__).parents[1]
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_write_guard_backend",
    )
    package = tmp_path / "Song.feedpak"
    package.mkdir()
    manifest = package / "manifest.yaml"
    manifest.write_bytes(b"original")
    outside = tmp_path / "worker-diagnostic.txt"

    def attempts_write(path, package_name, **_options):
        outside.write_text(package_name, encoding="utf-8")
        (path / "manifest.yaml").write_text("mutated", encoding="utf-8")
        return {"package": package_name}

    try:
        worker._install_write_guard()
        worker._pause_event = threading.Event()
        worker._cancel_event = threading.Event()
        worker._validator = SimpleNamespace(validate_feedpak=attempts_write)

        result = worker._validate_task((str(package), "Song.feedpak", False))

        assert result["outcome"] == "error"
        assert result["error_type"] == "PermissionError"
        assert manifest.read_bytes() == b"original"
        assert outside.read_text(encoding="utf-8") == "Song.feedpak"
        assert worker._protected_package_path is None
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


def test_pool_shutdown_forcibly_terminates_a_non_cooperative_process(monkeypatch):
    root = Path(__file__).parents[1]
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_shutdown_backend",
    )

    class StuckProcess:
        def __init__(self):
            self.sentinel = object()
            self.alive = True
            self.terminated = False
            self.killed = False

        def join(self, _timeout):
            pass

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def kill(self):
            self.killed = True
            self.alive = False

    process = StuckProcess()
    monkeypatch.setattr(
        worker, "wait_for_process_exit",
        lambda handles, _timeout: [] if process.alive else handles,
    )

    class Executor:
        def __init__(self):
            self._processes = {1: process}
            self.shutdown_call = None

        def shutdown(self, **options):
            self.shutdown_call = options

    pool = object.__new__(worker.ValidationProcessPool)
    pool._pause_event = threading.Event()
    pool._cancel_event = threading.Event()
    pool._executor = Executor()

    pool.shutdown(force=True, timeout_seconds=0.01)

    assert pool._cancel_event.is_set()
    assert pool._executor.shutdown_call == {
        "wait": False,
        "cancel_futures": True,
    }
    assert process.terminated is True
    assert process.killed is False


@pytest.mark.parametrize(
    ("force", "expected_timeouts"),
    (
        (False, [0.6, 0.2, 0.2]),
        (True, [0.5, 0.5]),
    ),
)
def test_pool_shutdown_reserves_time_to_observe_async_termination(
    monkeypatch, force, expected_timeouts
):
    root = Path(__file__).parents[1]
    worker = _load(
        root / "library_doctor_scan_worker.py",
        "library_doctor_worker_async_shutdown_backend",
    )

    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock.now)

    class AsyncProcess:
        def __init__(self):
            self.sentinel = object()
            self.alive = True
            self.terminated = False
            self.killed = False
            self.wait_timeouts = []

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = AsyncProcess()

    def wait(handles, timeout):
        if timeout > 0:
            process.wait_timeouts.append(timeout)
        clock.now += timeout
        if timeout > 0 and process.killed:
            process.alive = False
        return [] if process.alive else handles

    monkeypatch.setattr(worker, "wait_for_process_exit", wait)

    class Executor:
        def __init__(self):
            self._processes = {1: process}

        @staticmethod
        def shutdown(**_options):
            return None

    pool = object.__new__(worker.ValidationProcessPool)
    pool._pause_event = threading.Event()
    pool._cancel_event = threading.Event()
    pool._executor = Executor()

    pool.shutdown(force=force, timeout_seconds=1.0)

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_timeouts == pytest.approx(expected_timeouts)
    assert clock.now == pytest.approx(1.0)


def _shutdown_test_pool(worker, processes):
    class Executor:
        def __init__(self):
            self._processes = dict(enumerate(processes))
            self.shutdown_calls = 0

        def shutdown(self, **_options):
            self.shutdown_calls += 1
            self._processes = None

    pool = object.__new__(worker.ValidationProcessPool)
    pool._pause_event = threading.Event()
    pool._cancel_event = threading.Event()
    pool._executor = Executor()
    return pool


@pytest.mark.parametrize("force", [False, True])
def test_shutdown_observes_os_exit_despite_stale_process_returncode(force):
    """A ready exit handle wins over metadata awaiting the manager's reap."""
    worker = _load(
        Path(__file__).parents[1] / "library_doctor_scan_worker.py",
        "library_doctor_worker_stale_reap_backend",
    )
    reader, writer = multiprocessing.Pipe(duplex=False)

    class ReapedButUnpublishedProcess:
        sentinel = reader
        returncode = None
        joins = 0
        polls = 0
        signals = 0

        def join(self, _timeout):
            self.joins += 1

        def is_alive(self):
            self.polls += 1
            return True  # The concurrent waitpid loser observes ECHILD/None.

        def terminate(self):
            self.signals += 1

        kill = terminate

    process = ReapedButUnpublishedProcess()
    pool = _shutdown_test_pool(worker, [process])
    writer.close()  # Real OS handle becomes ready, as an exited POSIX child does.
    try:
        pool.shutdown(force=force, timeout_seconds=0.05)
        pool.shutdown(force=force, timeout_seconds=0.05)
    finally:
        reader.close()

    assert process.returncode is None
    assert (process.joins, process.polls, process.signals) == (0, 0, 0)
    assert pool._shutdown_complete is True
    assert pool._executor.shutdown_calls == 1


@pytest.mark.parametrize("failure", ["missing", "unreadable"])
def test_shutdown_refuses_unobservable_exit_and_retains_it_on_retry(
    monkeypatch, failure
):
    worker = _load(
        Path(__file__).parents[1] / "library_doctor_scan_worker.py",
        "library_doctor_worker_unobservable_backend",
    )

    class Process:
        signals = 0

        @property
        def sentinel(self):
            if failure == "missing":
                raise ValueError("sentinel unavailable")
            return object()

        def is_alive(self):
            return False  # Unverified cached state cannot authorize completion.

        def terminate(self):
            self.signals += 1

        kill = terminate

    def unreadable(_handles, _timeout):
        raise OSError("handle unreadable")

    monkeypatch.setattr(worker, "wait_for_process_exit", unreadable)
    process = Process()
    pool = _shutdown_test_pool(worker, [process])
    for _ in range(2):
        with pytest.raises(RuntimeError, match="1 validation worker.*did not stop"):
            pool.shutdown(force=True, timeout_seconds=0)
        assert not getattr(pool, "_shutdown_complete", False)
    assert pool._shutdown_processes == [process]
    assert process.signals == 4
    assert pool._executor.shutdown_calls == 2


def test_shutdown_one_unreadable_handle_does_not_signal_confirmed_exit(monkeypatch):
    worker = _load(
        Path(__file__).parents[1] / "library_doctor_scan_worker.py",
        "library_doctor_worker_mixed_handles_backend",
    )

    class Process:
        def __init__(self):
            self.sentinel = object()
            self.signals = 0

        def terminate(self):
            self.signals += 1

        kill = terminate

    stopped, unknown = Process(), Process()

    def wait(handles, _timeout):
        if unknown.sentinel in handles:
            raise OSError("handle unreadable")
        return handles

    monkeypatch.setattr(worker, "wait_for_process_exit", wait)
    pool = _shutdown_test_pool(worker, [stopped, unknown])
    with pytest.raises(RuntimeError, match="1 validation worker.*did not stop"):
        pool.shutdown(force=True, timeout_seconds=0)
    assert stopped.signals == 0
    assert unknown.signals == 2


def test_shutdown_live_workers_share_one_absolute_deadline(monkeypatch):
    worker = _load(
        Path(__file__).parents[1] / "library_doctor_scan_worker.py",
        "library_doctor_worker_deadline_backend",
    )
    clock = SimpleNamespace(now=0.0)
    waits = []
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock.now)

    class Process:
        def __init__(self):
            self.sentinel = object()
            self.signals = 0

        def terminate(self):
            self.signals += 1

        kill = terminate

    def wait(handles, timeout):
        waits.append((len(handles), timeout))
        clock.now += timeout
        return []

    monkeypatch.setattr(worker, "wait_for_process_exit", wait)
    processes = [Process(), Process(), Process()]
    pool = _shutdown_test_pool(worker, processes)
    with pytest.raises(RuntimeError, match="3 validation worker.*did not stop"):
        pool.shutdown(timeout_seconds=1)
    assert [size for size, _timeout in waits] == [3, 3, 3]
    assert [timeout for _size, timeout in waits] == pytest.approx([0.6, 0.2, 0.2])
    assert clock.now == pytest.approx(1.0)
    assert all(process.signals == 2 for process in processes)
