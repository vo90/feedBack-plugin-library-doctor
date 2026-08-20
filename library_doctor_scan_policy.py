"""Resource policy for Library Doctor validation workers."""

import os
import sys


MIN_PARALLEL_PACKAGES = 3
WINDOWS_PROCESS_POOL_LIMIT = 61
STANDARD_WORKER_MEMORY_BYTES = 384 * 1024 * 1024
DEEP_AUDIO_WORKER_MEMORY_BYTES = 768 * 1024 * 1024
MIN_SYSTEM_MEMORY_RESERVE_BYTES = 1024 * 1024 * 1024
MAX_SYSTEM_MEMORY_RESERVE_BYTES = 4 * 1024 * 1024 * 1024


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def choose_worker_policy(
    pending_packages: int,
    *,
    deep_audio: bool,
    requested_max: int | None = None,
    worker_backend_available: bool = True,
    logical_cpus: int | None = None,
    physical_cpus: int | None = None,
    total_memory: int | None = None,
    available_memory: int | None = None,
    platform: str | None = None,
    environment: dict | None = None,
) -> dict:
    """Choose a bounded worker maximum from work, CPU, RAM, host and user limits."""
    pending = max(0, int(pending_packages or 0))
    env = os.environ if environment is None else environment
    platform_name = sys.platform if platform is None else platform

    if logical_cpus is None:
        logical_cpus = os.cpu_count() or 1
    if physical_cpus is None or total_memory is None or available_memory is None:
        try:
            import psutil  # FeedBack dependency; optional for standalone tests.

            if physical_cpus is None:
                physical_cpus = psutil.cpu_count(logical=False)
            memory = psutil.virtual_memory()
            if total_memory is None:
                total_memory = int(memory.total)
            if available_memory is None:
                available_memory = int(memory.available)
        except (ImportError, OSError, RuntimeError, ValueError):
            pass

    logical = max(1, _positive_int(logical_cpus) or 1)
    physical = max(1, _positive_int(physical_cpus) or max(1, (logical + 1) // 2))
    global_value = env.get("FEEDBACK_MAX_SCAN_WORKERS") or env.get(
        "SCAN_MAX_WORKERS"
    )
    global_limit = _positive_int(global_value)
    user_limit = _positive_int(requested_max)
    platform_limit = (
        WINDOWS_PROCESS_POOL_LIMIT if platform_name == "win32" else physical
    )

    memory_per_worker = (
        DEEP_AUDIO_WORKER_MEMORY_BYTES if deep_audio else STANDARD_WORKER_MEMORY_BYTES
    )
    memory_limit = physical
    if _positive_int(total_memory) and _positive_int(available_memory):
        reserve = min(
            MAX_SYSTEM_MEMORY_RESERVE_BYTES,
            max(MIN_SYSTEM_MEMORY_RESERVE_BYTES, int(total_memory * 0.15)),
        )
        usable = max(0, int(available_memory) - reserve)
        memory_limit = max(1, usable // memory_per_worker)

    limits = {
        "packages": max(1, pending),
        "physical_cpu": physical,
        "memory": max(1, memory_limit),
        "platform": max(1, platform_limit),
    }
    if global_limit is not None:
        limits["feedback"] = global_limit
    if user_limit is not None:
        limits["user"] = user_limit

    selected = min(limits.values())
    reason = "automatic"
    if not worker_backend_available:
        selected = 1
        reason = "worker_backend_unavailable"
    elif pending < MIN_PARALLEL_PACKAGES:
        selected = 1
        reason = "small_scope"
    elif selected <= 1:
        reason = "limited_to_one"

    return {
        "schema": "library_doctor.worker_policy.v1",
        "mode": "custom" if user_limit is not None else "automatic",
        "reason": reason,
        "selected_workers": max(1, selected),
        "pending_packages": pending,
        "deep_audio": bool(deep_audio),
        "logical_cpus": logical,
        "physical_cpus": physical,
        "memory_per_worker_bytes": memory_per_worker,
        "total_memory_bytes": _positive_int(total_memory),
        "available_memory_bytes": _positive_int(available_memory),
        "limits": limits,
    }
