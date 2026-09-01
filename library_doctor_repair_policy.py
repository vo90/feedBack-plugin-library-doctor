"""Conservative resource policy for concurrent Library Doctor repairs."""

from __future__ import annotations

import math
import ntpath
import os
import sys


MIN_PARALLEL_PACKAGES = 3
HARD_MAX_REPAIR_WORKERS = 4
AUTOMATIC_STANDARD_WORKERS = 3
AUTOMATIC_HEAVY_WORKERS = 2
STANDARD_WORKER_MEMORY_BYTES = 1024 * 1024 * 1024
HEAVY_WORKER_MEMORY_BYTES = 1536 * 1024 * 1024
MIN_SYSTEM_MEMORY_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SYSTEM_MEMORY_RESERVE_BYTES = 6 * 1024 * 1024 * 1024
SYSTEM_MEMORY_RESERVE_RATIO = 0.20
MIN_CANDIDATE_STORAGE_BYTES = 512 * 1024 * 1024
MIN_FREE_STORAGE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_FREE_STORAGE_RESERVE_BYTES = 16 * 1024 * 1024 * 1024
FREE_STORAGE_RESERVE_RATIO = 0.05

_STORAGE_ALIASES = {
    "hdd": "rotational",
    "rotational": "rotational",
    "network": "network",
    "remote": "network",
    "removable": "removable",
    "ssd": "ssd",
    "sata_ssd": "ssd",
    "sata-ssd": "ssd",
    "solid_state": "ssd",
    "solid-state": "ssd",
    "nvme": "nvme",
    "unknown": "unknown_local",
    "unknown_local": "unknown_local",
    "unknown-local": "unknown_local",
}
_STORAGE_LIMITS = {
    "rotational": 1,
    "network": 1,
    "removable": 1,
    "ssd": 2,
    "unknown_local": 2,
    "nvme": 4,
}

_DRIVE_REMOVABLE = 2
_DRIVE_FIXED = 3
_DRIVE_REMOTE = 4
_STORAGE_DEVICE_PROPERTY = 0
_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY = 7
_BUS_TYPE_NVME = 17
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400


def _is_unc_path(value: str) -> bool:
    normalized = value.replace("/", "\\")
    upper = normalized.upper()
    return upper.startswith("\\\\?\\UNC\\") or (
        normalized.startswith("\\\\")
        and not upper.startswith(("\\\\?\\", "\\\\.\\"))
    )


def _storage_descriptor_value(raw: bytes | None, offset: int) -> int | None:
    minimum_size = offset + 4
    if not isinstance(raw, bytes) or len(raw) < minimum_size:
        return None
    descriptor_size = int.from_bytes(raw[4:8], "little")
    if descriptor_size < minimum_size or descriptor_size > len(raw):
        return None
    return int.from_bytes(raw[offset:minimum_size], "little")


def _seek_penalty(raw: bytes | None) -> bool | None:
    if not isinstance(raw, bytes) or len(raw) < 9:
        return None
    descriptor_size = int.from_bytes(raw[4:8], "little")
    if descriptor_size < 9 or descriptor_size > len(raw) or raw[8] not in {0, 1}:
        return None
    return bool(raw[8])


def _query_windows_storage_property(kernel32, handle, property_id: int) -> bytes | None:
    import ctypes
    from ctypes import wintypes

    query = (ctypes.c_ubyte * 12)()
    ctypes.c_uint32.from_buffer(query).value = property_id
    output = ctypes.create_string_buffer(1024)
    returned = wintypes.DWORD()
    if not kernel32.DeviceIoControl(
        handle,
        _IOCTL_STORAGE_QUERY_PROPERTY,
        ctypes.byref(query),
        ctypes.sizeof(query),
        ctypes.byref(output),
        ctypes.sizeof(output),
        ctypes.byref(returned),
        None,
    ):
        return None
    size = int(returned.value)
    if size <= 0 or size > ctypes.sizeof(output):
        return None
    return bytes(output.raw[:size])


def _win32_signature(function, argtypes, restype) -> None:
    function.argtypes = argtypes
    function.restype = restype


def _windows_storage_observation(path: str) -> dict:
    """Return bounded Win32 volume facts without identifiers or source paths."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _win32_signature(
        kernel32.GetVolumePathNameW,
        [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.BOOL,
    )
    _win32_signature(
        kernel32.GetDriveTypeW, [wintypes.LPCWSTR], wintypes.UINT
    )
    _win32_signature(
        kernel32.GetVolumeNameForVolumeMountPointW,
        [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.BOOL,
    )
    _win32_signature(kernel32.CreateFileW, [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ], wintypes.HANDLE)
    _win32_signature(kernel32.DeviceIoControl, [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ], wintypes.BOOL)
    _win32_signature(kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)

    root_buffer = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetVolumePathNameW(path, root_buffer, len(root_buffer)):
        return {}
    volume_root = root_buffer.value
    drive_type = int(kernel32.GetDriveTypeW(volume_root))
    observation = {"drive_type": drive_type}
    if drive_type != _DRIVE_FIXED:
        return observation

    volume_buffer = ctypes.create_unicode_buffer(1024)
    if kernel32.GetVolumeNameForVolumeMountPointW(
        volume_root, volume_buffer, len(volume_buffer)
    ):
        handle_path = volume_buffer.value.rstrip("\\")
    else:
        drive, _tail = ntpath.splitdrive(volume_root)
        if len(drive) != 2 or not drive.endswith(":"):
            return observation
        handle_path = f"\\\\.\\{drive}"

    handle = kernel32.CreateFileW(
        handle_path,
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0,
        None,
    )
    if handle in {None, ctypes.c_void_p(-1).value}:
        return observation
    try:
        seek_raw = _query_windows_storage_property(
            kernel32, handle, _STORAGE_DEVICE_SEEK_PENALTY_PROPERTY
        )
        device_raw = _query_windows_storage_property(
            kernel32, handle, _STORAGE_DEVICE_PROPERTY
        )
    finally:
        kernel32.CloseHandle(handle)
    observation["incurs_seek_penalty"] = _seek_penalty(seek_raw)
    observation["bus_type"] = _storage_descriptor_value(device_raw, 28)
    return observation


def _classify_windows_storage(observation) -> str:
    if not isinstance(observation, dict):
        return "unknown_local"
    drive_type = observation.get("drive_type")
    if isinstance(drive_type, bool) or not isinstance(drive_type, int):
        return "unknown_local"
    if drive_type == _DRIVE_REMOTE:
        return "network"
    if drive_type == _DRIVE_REMOVABLE:
        return "removable"
    if drive_type != _DRIVE_FIXED:
        return "unknown_local"

    seek_penalty = observation.get("incurs_seek_penalty")
    bus_type = observation.get("bus_type")
    if seek_penalty is True:
        return "rotational"
    if (
        not isinstance(bus_type, bool)
        and isinstance(bus_type, int)
        and bus_type == _BUS_TYPE_NVME
    ):
        return "nvme"
    if seek_penalty is False:
        return "ssd"
    return "unknown_local"


def detect_storage_kind(
    path: os.PathLike[str] | str | None,
    *,
    platform: str | None = None,
    windows_probe=None,
) -> str:
    """Classify one repair source volume without exposing its identity.

    The injectable probe keeps Win32 access isolated for deterministic tests.
    Any unsupported host, unavailable path, denied query, or malformed result
    uses the conservative local fallback and never prevents a repair.
    """
    try:
        source = os.fspath(path) if path is not None else ""
    except Exception:
        return "unknown_local"
    if not isinstance(source, str) or not source.strip():
        return "unknown_local"
    if (sys.platform if platform is None else platform) != "win32":
        return "unknown_local"
    if _is_unc_path(source):
        return "network"
    probe = _windows_storage_observation if windows_probe is None else windows_probe
    if not callable(probe):
        return "unknown_local"
    try:
        return _classify_windows_storage(probe(source))
    except Exception:
        return "unknown_local"


def _positive_int(value, *, allow_zero: bool = False) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= (0 if allow_zero else 1) else None


def _nonnegative_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _busy_ratio(value) -> float | None:
    parsed = _nonnegative_float(value)
    if parsed is None:
        return None
    if parsed > 1:
        parsed = parsed / 100 if parsed <= 100 else math.nan
    return parsed if math.isfinite(parsed) and parsed <= 1 else None


def _storage_kind(value) -> tuple[str, str]:
    if isinstance(value, str):
        normalized = _STORAGE_ALIASES.get(value.strip().lower())
        if normalized is not None:
            return normalized, "provided"
    return "unknown_local", "conservative_fallback"


def _host_resources(
    logical_cpus, physical_cpus, total_memory, available_memory
) -> tuple[int, int | None, int | None, int | None]:
    logical = logical_cpus
    physical = physical_cpus
    total = total_memory
    available = available_memory
    if logical is None:
        logical = os.cpu_count() or 1
    if physical is None or total is None or available is None:
        try:
            import psutil  # FeedBack dependency; optional in focused tests.

            if physical is None:
                physical = psutil.cpu_count(logical=False)
            memory = psutil.virtual_memory()
            if total is None:
                total = int(memory.total)
            if available is None:
                available = int(memory.available)
        except (ImportError, OSError, RuntimeError, ValueError):
            pass
    return (
        max(1, _positive_int(logical) or 1),
        _positive_int(physical),
        _positive_int(total),
        _positive_int(available),
    )


def _free_storage_policy(total_value, free_value, largest_value, heavy: bool) -> dict:
    total = _positive_int(total_value)
    free = _positive_int(free_value, allow_zero=True)
    largest = _positive_int(largest_value, allow_zero=True)
    available = total is not None and free is not None and largest is not None and free <= total
    reserve = min(MAX_FREE_STORAGE_RESERVE_BYTES, max(MIN_FREE_STORAGE_RESERVE_BYTES, int(total * FREE_STORAGE_RESERVE_RATIO))) if available else None
    per_worker = max(MIN_CANDIDATE_STORAGE_BYTES, largest * (3 if heavy else 2)) if available else None
    usable = max(0, free - reserve) if available else None
    worker_limit = max(1, min(HARD_MAX_REPAIR_WORKERS, usable // per_worker)) if available else None
    return {"metrics_available": available, "total_bytes": total, "free_bytes": free, "reserve_bytes": reserve, "usable_bytes": usable, "largest_source_package_bytes": largest, "candidate_bytes_per_worker": per_worker, "worker_limit": worker_limit}


def choose_repair_worker_policy(
    pending_packages: int,
    *,
    deep_audio: bool = False,
    include_preview_repairs: bool = False,
    requested_max: int | None = None,
    worker_backend_available: bool = True,
    logical_cpus: int | None = None,
    physical_cpus: int | None = None,
    total_memory: int | None = None,
    available_memory: int | None = None,
    storage_kind: str | None = None,
    storage_total_bytes: int | None = None,
    storage_free_bytes: int | None = None,
    largest_source_package_bytes: int | None = None,
    disk_busy_ratio: float | None = None,
    disk_queue_depth: float | None = None,
) -> dict:
    """Return worker ceilings and the evidence used to derive them.

    Storage observations can be injected by the host. Unknown local storage is
    deliberately treated like a two-stream SSD, while missing RAM metrics
    force one worker because preparation must not induce paging pressure.
    Transient disk pressure changes ``active_worker_limit`` without changing
    the stable selected/manual ceilings.
    """
    pending = max(0, int(pending_packages or 0))
    logical, observed_physical, total, available = _host_resources(
        logical_cpus, physical_cpus, total_memory, available_memory
    )
    physical_estimated = observed_physical is None
    physical = observed_physical or max(1, (logical + 1) // 2)
    heavy = bool(deep_audio or include_preview_repairs)
    cpu_weight = 2 if heavy else 1
    cpu_limit = max(1, max(1, physical - 1) // cpu_weight)

    memory_per_worker = (
        HEAVY_WORKER_MEMORY_BYTES if heavy else STANDARD_WORKER_MEMORY_BYTES
    )
    memory_metrics_available = total is not None and available is not None
    memory_reserve = (
        min(
            MAX_SYSTEM_MEMORY_RESERVE_BYTES,
            max(
                MIN_SYSTEM_MEMORY_RESERVE_BYTES,
                int(total * SYSTEM_MEMORY_RESERVE_RATIO),
            ),
        )
        if total is not None
        else None
    )
    usable_memory = (
        max(0, available - memory_reserve)
        if memory_metrics_available and memory_reserve is not None
        else None
    )
    memory_limit = (
        max(1, usable_memory // memory_per_worker)
        if usable_memory is not None
        else 1
    )

    normalized_storage, storage_source = _storage_kind(storage_kind)
    storage_limit = _STORAGE_LIMITS[normalized_storage]
    free_storage = _free_storage_policy(
        storage_total_bytes, storage_free_bytes, largest_source_package_bytes, heavy
    )
    busy = _busy_ratio(disk_busy_ratio)
    queue = _nonnegative_float(disk_queue_depth)
    pressure_available = busy is not None or queue is not None
    if (busy is not None and busy >= 0.85) or (queue is not None and queue >= 4):
        pressure_level = "high"
        pressure_limit = 1
    elif (busy is not None and busy >= 0.70) or (queue is not None and queue >= 2):
        pressure_level = "moderate"
        pressure_limit = 2
    else:
        pressure_level = "low" if pressure_available else "unknown"
        pressure_limit = storage_limit

    packages_limit = max(1, pending)
    backend_limit = HARD_MAX_REPAIR_WORKERS if worker_backend_available else 1
    limits = {
        "packages": packages_limit,
        "physical_cpu": cpu_limit,
        "memory": max(1, memory_limit),
        "storage": storage_limit,
        "hard_cap": HARD_MAX_REPAIR_WORKERS,
        "worker_backend": backend_limit,
    }
    if free_storage["worker_limit"] is not None:
        limits["free_storage"] = free_storage["worker_limit"]
    manual_max = min(limits.values())
    automatic_cap = (
        AUTOMATIC_HEAVY_WORKERS if heavy else AUTOMATIC_STANDARD_WORKERS
    )
    automatic_max = (
        1
        if pending < MIN_PARALLEL_PACKAGES
        else min(manual_max, automatic_cap)
    )
    requested = _positive_int(requested_max)
    mode = "custom" if requested is not None else "automatic"
    selected = min(requested, manual_max) if requested is not None else automatic_max
    active_limit = min(selected, pressure_limit)

    if not worker_backend_available:
        reason = "worker_backend_unavailable"
    elif mode == "automatic" and pending < MIN_PARALLEL_PACKAGES:
        reason = "small_scope"
    elif manual_max <= 1:
        reason = "limited_to_one"
    elif requested is not None and requested > manual_max:
        reason = "custom_capped"
    elif requested is not None:
        reason = "custom"
    else:
        reason = "automatic"

    return {
        "schema": "library_doctor.repair_worker_policy.v1",
        "mode": mode,
        "reason": reason,
        "selected_workers": max(1, selected),
        "active_worker_limit": max(1, active_limit),
        "recommended_workers": max(1, automatic_max),
        "automatic_max_workers": max(1, automatic_max),
        "manual_max_workers": max(1, manual_max),
        "hard_max_workers": HARD_MAX_REPAIR_WORKERS,
        "requested_max_workers": requested,
        "pending_packages": pending,
        "workload": "heavy" if heavy else "standard",
        "deep_audio": bool(deep_audio),
        "include_preview_repairs": bool(include_preview_repairs),
        "cpu_weight": cpu_weight,
        "logical_cpus": logical,
        "physical_cpus": physical,
        "physical_cpus_estimated": physical_estimated,
        "total_memory_bytes": total,
        "available_memory_bytes": available,
        "memory_metrics_available": memory_metrics_available,
        "memory_reserve_bytes": memory_reserve,
        "usable_memory_bytes": usable_memory,
        "memory_per_worker_bytes": memory_per_worker,
        "storage_kind": normalized_storage,
        "storage_kind_source": storage_source,
        "free_storage": free_storage,
        "disk_pressure": {
            "level": pressure_level,
            "metrics_available": pressure_available,
            "busy_ratio": busy,
            "queue_depth": queue,
            "active_worker_limit": min(storage_limit, pressure_limit),
        },
        "active_limit_reason": (
            f"disk_pressure_{pressure_level}"
            if active_limit < selected
            else "selected_ceiling"
        ),
        "limits": limits,
        "limiting_factors": sorted(
            name for name, value in limits.items() if value == manual_max
        ),
    }
