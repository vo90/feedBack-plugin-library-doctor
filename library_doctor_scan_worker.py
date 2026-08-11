"""Spawn-safe, read-only Feedpak validation workers for Library Doctor.

The plugin host loads sibling modules under a private namespaced package.  A
spawned ``ProcessPoolExecutor`` cannot recreate that synthetic package, so the
small worker entry points below deliberately publish one plugin-specific,
importable module name.  Worker processes import only this file and the
read-only validator; they never open Library Doctor's SQLite cache or any
repair/recovery state.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import multiprocessing
import os
import sys
import time
from pathlib import Path


STABLE_MODULE_NAME = "library_doctor_scan_worker"
_validator = None
_pause_event = None
_cancel_event = None
_protected_package_path = None
_protected_package_is_dir = False
_write_guard_installed = False


_MUTATING_AUDIT_EVENTS = {
    "os.chmod",
    "os.chown",
    "os.link",
    "os.mkdir",
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.symlink",
    "os.truncate",
    "os.utime",
}


class _WorkerCancelled(Exception):
    pass


def _inside_protected_package(value) -> bool:
    protected = _protected_package_path
    if protected is None or isinstance(value, int):
        return False
    try:
        candidate = Path(value).resolve(strict=False)
        if _protected_package_is_dir:
            candidate.relative_to(protected)
            return True
        return candidate == protected
    except (OSError, TypeError, ValueError):
        return False


def _write_guard(event, args) -> None:
    """Deny Python-level mutation attempts against the active package."""
    if _protected_package_path is None:
        return
    paths = ()
    if event == "open" and args:
        path = args[0]
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        write_requested = (
            isinstance(mode, str) and any(marker in mode for marker in "wax+")
        ) or (
            isinstance(flags, int)
            and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
        )
        if write_requested:
            paths = (path,)
    elif event in _MUTATING_AUDIT_EVENTS:
        paths = tuple(args[:2])
    if any(_inside_protected_package(path) for path in paths):
        raise PermissionError(
            "Library Doctor validation workers are read-only for song packages."
        )


def _install_write_guard() -> None:
    global _write_guard_installed
    if _write_guard_installed:
        return
    sys.addaudithook(_write_guard)
    _write_guard_installed = True


def _load_validator(plugin_dir: str, expected_version: str):
    global _validator
    if _validator is not None:
        return _validator
    path = Path(plugin_dir).resolve() / "validator.py"
    module_name = "_library_doctor_process_validator"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("Library Doctor's validator worker could not be loaded.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    if getattr(module, "VALIDATOR_VERSION", None) != expected_version:
        raise RuntimeError("The validation worker does not match the active scanner version.")
    _validator = module
    return module


def _initialize_worker(
    plugin_dir: str,
    validator_version: str,
    pause_event,
    cancel_event,
) -> None:
    global _pause_event, _cancel_event
    _pause_event = pause_event
    _cancel_event = cancel_event
    _load_validator(plugin_dir, validator_version)
    _install_write_guard()


def _checkpoint() -> None:
    while _pause_event is not None and _pause_event.is_set():
        if _cancel_event is not None and _cancel_event.is_set():
            raise _WorkerCancelled
        time.sleep(0.05)
    if _cancel_event is not None and _cancel_event.is_set():
        raise _WorkerCancelled


def _validate_task(task: tuple[str, str, bool]) -> dict:
    global _protected_package_is_dir, _protected_package_path
    path, package, deep_audio = task
    started = time.perf_counter()
    try:
        _checkpoint()
        package_path = Path(path)
        _protected_package_path = package_path.resolve(strict=False)
        _protected_package_is_dir = package_path.is_dir()
        options = {"scan_checkpoint": _checkpoint}
        if deep_audio:
            options["deep_audio"] = True
        report = _validator.validate_feedpak(Path(path), package, **options)
        _checkpoint()
        return {
            "outcome": "complete",
            "report": report,
            "elapsed_seconds": max(0.0, time.perf_counter() - started),
        }
    except _WorkerCancelled:
        return {
            "outcome": "cancelled",
            "elapsed_seconds": max(0.0, time.perf_counter() - started),
        }
    except Exception as exc:  # A malformed third-party package is isolated here.
        return {
            "outcome": "error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1_000],
            "elapsed_seconds": max(0.0, time.perf_counter() - started),
        }
    finally:
        _protected_package_path = None
        _protected_package_is_dir = False


class ValidationProcessPool:
    """A spawned process pool with shared playback-pause and cancel signals."""

    def __init__(self, *, max_workers: int, validator_version: str) -> None:
        context = multiprocessing.get_context("spawn")
        self._pause_event = context.Event()
        self._cancel_event = context.Event()
        self._executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=context,
            initializer=_initialize_worker,
            initargs=(
                str(Path(__file__).resolve().parent),
                validator_version,
                self._pause_event,
                self._cancel_event,
            ),
        )

    def submit(self, path: Path, package: str, deep_audio: bool):
        return self._executor.submit(
            _validate_task,
            (str(path), package, bool(deep_audio)),
        )

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_event.set()
        else:
            self._pause_event.clear()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.clear()

    def memory_usage(self) -> dict[int, int]:
        """Return best-effort RSS by worker PID for parent-side budget checks."""
        try:
            import psutil
        except ImportError:  # The host normally provides psutil; scanning still works without it.
            return {}
        usage = {}
        for process in (getattr(self._executor, "_processes", None) or {}).values():
            pid = getattr(process, "pid", None)
            if not pid:
                continue
            try:
                usage[int(pid)] = int(psutil.Process(pid).memory_info().rss)
            except (psutil.Error, OSError, ValueError):
                continue
        return usage

    def shutdown(
        self,
        *,
        force: bool = False,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Stop workers within a bounded interval, terminating stragglers."""
        self._pause_event.clear()
        self._cancel_event.set()
        processes = list((getattr(self._executor, "_processes", None) or {}).values())
        self._executor.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))

        if not force:
            for process in processes:
                remaining = max(0.0, deadline - time.monotonic())
                process.join(remaining)

        alive = [process for process in processes if process.is_alive()]
        for process in alive:
            process.terminate()
        terminate_deadline = time.monotonic() + 1.0
        for process in alive:
            process.join(max(0.0, terminate_deadline - time.monotonic()))

        alive = [process for process in alive if process.is_alive()]
        for process in alive:
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
            else:  # pragma: no cover - current supported Python exposes kill.
                process.terminate()
        for process in alive:
            process.join(0.25)

    def terminate(self, *, timeout_seconds: float = 1.0) -> None:
        self.shutdown(force=True, timeout_seconds=timeout_seconds)


# ``load_sibling`` executes this file as ``plugin_<id>.<name>``.  Pickle uses a
# callable's ``__module__`` to import it in spawned children, so provide the
# stable file-backed alias while retaining namespaced loading in the host.
if __name__ != STABLE_MODULE_NAME:
    sys.modules[STABLE_MODULE_NAME] = sys.modules[__name__]
    _initialize_worker.__module__ = STABLE_MODULE_NAME
    _validate_task.__module__ = STABLE_MODULE_NAME
