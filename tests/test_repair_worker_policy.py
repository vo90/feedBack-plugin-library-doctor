import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "library_doctor_repair_policy.py"
MODULE_NAME = "library_doctor_repair_worker_policy_tests"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
policy = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = policy
SPEC.loader.exec_module(policy)


GIB = 1024**3


def choose(**overrides):
    options = {
        "pending_packages": 100,
        "logical_cpus": 16,
        "physical_cpus": 8,
        "total_memory": 32 * GIB,
        "available_memory": 24 * GIB,
        "storage_kind": "nvme",
    }
    options.update(overrides)
    return policy.choose_repair_worker_policy(**options)


def detect(path=r"D:\Songs", observation=None, **overrides):
    options = {
        "platform": "win32",
        "windows_probe": lambda _path: observation,
    }
    options.update(overrides)
    return policy.detect_storage_kind(path, **options)


@pytest.mark.parametrize(
    ("path", "observation", "expected"),
    [
        (r"\\server\share\Songs", None, "network"),
        (r"\\?\UNC\server\share\Songs", None, "network"),
        (r"Z:\Songs", {"drive_type": 4}, "network"),
        (r"E:\Songs", {"drive_type": 2}, "removable"),
        (
            r"D:\Songs",
            {"drive_type": 3, "incurs_seek_penalty": True, "bus_type": 11},
            "rotational",
        ),
        (
            r"D:\Songs",
            {"drive_type": 3, "incurs_seek_penalty": False, "bus_type": 11},
            "ssd",
        ),
        (
            r"D:\Songs",
            {"drive_type": 3, "incurs_seek_penalty": False, "bus_type": 17},
            "nvme",
        ),
        (r"D:\Songs", {"drive_type": 3, "bus_type": 17}, "nvme"),
    ],
)
def test_windows_storage_detection_classifies_bounded_volume_facts(
    path, observation, expected
):
    assert detect(path, observation) == expected


def test_unc_detection_does_not_invoke_the_windows_device_probe():
    def fail_probe(_path):
        raise AssertionError("UNC paths must not open a local volume handle")

    assert detect(r"\\server\share\Songs", windows_probe=fail_probe) == "network"


@pytest.mark.parametrize(
    "path,platform,probe",
    [
        (None, "win32", lambda _path: {"drive_type": 3}),
        ("", "win32", lambda _path: {"drive_type": 3}),
        (r"D:\Songs", "linux", lambda _path: {"drive_type": 3}),
        (r"D:\Songs", "win32", object()),
        (r"D:\Songs", "win32", lambda _path: None),
        (r"D:\Songs", "win32", lambda _path: []),
        (r"D:\Songs", "win32", lambda _path: {"drive_type": "fixed"}),
        (
            r"D:\Songs",
            "win32",
            lambda _path: {
                "drive_type": 3,
                "incurs_seek_penalty": "false",
                "bus_type": "17",
            },
        ),
    ],
)
def test_storage_detection_falls_back_without_raising(path, platform, probe):
    assert (
        policy.detect_storage_kind(
            path,
            platform=platform,
            windows_probe=probe,
        )
        == "unknown_local"
    )


def test_storage_detection_contains_probe_failures_and_returns_no_identity():
    private_path = r"D:\Private Person\Secret Songs"

    def denied(_path):
        raise OSError("access denied")

    detected = detect(private_path, windows_probe=denied)

    assert detected == "unknown_local"
    assert private_path not in detected


def test_storage_detection_contains_unexpected_path_protocol_failures():
    class BrokenPath:
        def __fspath__(self):
            raise LookupError("unavailable path binding")

    assert policy.detect_storage_kind(BrokenPath(), platform="win32") == "unknown_local"


def test_windows_descriptor_parsers_reject_partial_and_malformed_payloads():
    short = b"\x00" * 8
    oversized = b"\x00" * 4 + (99).to_bytes(4, "little") + b"\x00" * 24
    invalid_boolean = (
        b"\x00" * 4 + (12).to_bytes(4, "little") + b"\x02" + b"\x00" * 3
    )

    assert policy._seek_penalty(short) is None
    assert policy._seek_penalty(oversized) is None
    assert policy._seek_penalty(invalid_boolean) is None
    assert policy._storage_descriptor_value(short, 28) is None
    assert policy._storage_descriptor_value(oversized, 28) is None


def test_windows_descriptor_parsers_read_only_bounded_fields():
    seek = bytearray(12)
    seek[4:8] = (12).to_bytes(4, "little")
    seek[8] = 0
    device = bytearray(32)
    device[4:8] = (32).to_bytes(4, "little")
    device[28:32] = (17).to_bytes(4, "little")

    assert policy._seek_penalty(bytes(seek)) is False
    assert policy._storage_descriptor_value(bytes(device), 28) == 17


@pytest.mark.parametrize(
    ("observation", "expected_kind", "expected_limit"),
    [
        ({"drive_type": 4}, "network", 1),
        ({"drive_type": 2}, "removable", 1),
        (
            {"drive_type": 3, "incurs_seek_penalty": True, "bus_type": 11},
            "rotational",
            1,
        ),
        (
            {"drive_type": 3, "incurs_seek_penalty": False, "bus_type": 11},
            "ssd",
            2,
        ),
        (
            {"drive_type": 3, "incurs_seek_penalty": False, "bus_type": 17},
            "nvme",
            4,
        ),
        ({}, "unknown_local", 2),
    ],
)
def test_detected_storage_kind_controls_the_policy_ceiling(
    observation, expected_kind, expected_limit
):
    kind = detect(observation=observation)
    result = choose(storage_kind=kind)

    assert kind == expected_kind
    assert result["limits"]["storage"] == expected_limit
    assert result["manual_max_workers"] == expected_limit


def test_high_spec_nvme_scales_automatic_and_custom_workers():
    kind = detect(
        observation={
            "drive_type": 3,
            "incurs_seek_penalty": False,
            "bus_type": 17,
        }
    )

    automatic = choose(storage_kind=kind)
    custom = choose(storage_kind=kind, requested_max=4)
    heavy = choose(storage_kind=kind, include_preview_repairs=True)

    assert automatic["selected_workers"] == 3
    assert custom["selected_workers"] == 4
    assert heavy["selected_workers"] == 2


def test_cpu_ram_and_backend_limits_override_fast_storage():
    weak_cpu = choose(storage_kind="nvme", physical_cpus=2)
    low_ram = choose(
        storage_kind="nvme", total_memory=8 * GIB, available_memory=2 * GIB
    )
    unavailable = choose(storage_kind="nvme", worker_backend_available=False)

    assert weak_cpu["selected_workers"] == 1
    assert low_ram["selected_workers"] == 1
    assert unavailable["selected_workers"] == 1


def test_free_storage_ceiling_preserves_reserve_and_caps_workers():
    result = choose(
        storage_total_bytes=100 * GIB,
        storage_free_bytes=10 * GIB,
        largest_source_package_bytes=GIB,
        requested_max=4,
    )

    assert result["free_storage"] == {
        "metrics_available": True,
        "total_bytes": 100 * GIB,
        "free_bytes": 10 * GIB,
        "reserve_bytes": 5 * GIB,
        "usable_bytes": 5 * GIB,
        "largest_source_package_bytes": GIB,
        "candidate_bytes_per_worker": 2 * GIB,
        "worker_limit": 2,
    }
    assert result["limits"]["free_storage"] == 2
    assert result["manual_max_workers"] == 2
    assert result["selected_workers"] == 2


@pytest.mark.parametrize("free_bytes", [0, GIB])
def test_low_free_space_conservatively_uses_one_worker(free_bytes):
    result = choose(
        storage_total_bytes=100 * GIB,
        storage_free_bytes=free_bytes,
        largest_source_package_bytes=GIB,
    )

    assert result["free_storage"]["metrics_available"] is True
    assert result["free_storage"]["worker_limit"] == 1
    assert result["selected_workers"] == 1


def test_huge_source_package_reduces_parallel_candidate_count():
    result = choose(
        storage_total_bytes=500 * GIB,
        storage_free_bytes=20 * GIB,
        largest_source_package_bytes=3 * GIB,
    )

    assert result["free_storage"]["reserve_bytes"] == 16 * GIB
    assert result["free_storage"]["candidate_bytes_per_worker"] == 6 * GIB
    assert result["free_storage"]["worker_limit"] == 1
    assert result["selected_workers"] == 1


def test_candidate_estimate_has_floor_and_heavy_workload_overhead():
    floored = choose(
        storage_total_bytes=40 * GIB,
        storage_free_bytes=3 * GIB,
        largest_source_package_bytes=1024,
    )
    heavy = choose(
        storage_total_bytes=100 * GIB,
        storage_free_bytes=20 * GIB,
        largest_source_package_bytes=GIB,
        deep_audio=True,
    )

    assert (
        floored["free_storage"]["candidate_bytes_per_worker"]
        == policy.MIN_CANDIDATE_STORAGE_BYTES
    )
    assert heavy["free_storage"]["candidate_bytes_per_worker"] == 3 * GIB


@pytest.mark.parametrize(
    "storage_evidence",
    [
        {},
        {"storage_total_bytes": 100 * GIB},
        {
            "storage_total_bytes": 100 * GIB,
            "storage_free_bytes": 10 * GIB,
        },
        {
            "storage_total_bytes": 100 * GIB,
            "storage_free_bytes": 101 * GIB,
            "largest_source_package_bytes": GIB,
        },
        {
            "storage_total_bytes": -1,
            "storage_free_bytes": "unreadable",
            "largest_source_package_bytes": None,
        },
    ],
)
def test_missing_or_invalid_storage_metrics_leave_existing_caps_in_control(
    storage_evidence,
):
    result = choose(**storage_evidence)

    assert result["free_storage"]["metrics_available"] is False
    assert result["free_storage"]["worker_limit"] is None
    assert "free_storage" not in result["limits"]
    assert result["selected_workers"] == 3


def test_standard_auto_policy_preserves_headroom_and_reports_full_evidence():
    result = choose()

    assert result["schema"] == "library_doctor.repair_worker_policy.v1"
    assert result["mode"] == "automatic"
    assert result["reason"] == "automatic"
    assert result["workload"] == "standard"
    assert result["limits"] == {
        "packages": 100,
        "physical_cpu": 7,
        "memory": 18,
        "storage": 4,
        "hard_cap": 4,
        "worker_backend": 4,
    }
    assert result["manual_max_workers"] == 4
    assert result["recommended_workers"] == 3
    assert result["selected_workers"] == 3
    assert result["active_worker_limit"] == 3
    assert result["memory_reserve_bytes"] == 6 * GIB
    assert result["memory_per_worker_bytes"] == GIB
    assert result["limiting_factors"] == ["hard_cap", "storage", "worker_backend"]
    json.dumps(result, allow_nan=False)


def test_custom_setting_can_use_safe_max_but_never_bypass_hardware_cap():
    requested_max = choose(requested_max=4)
    excessive = choose(requested_max=99)

    assert requested_max["mode"] == "custom"
    assert requested_max["reason"] == "custom"
    assert requested_max["selected_workers"] == 4
    assert requested_max["active_worker_limit"] == 4
    assert excessive["reason"] == "custom_capped"
    assert excessive["requested_max_workers"] == 99
    assert excessive["selected_workers"] == 4


@pytest.mark.parametrize("heavy_option", ["deep_audio", "include_preview_repairs"])
def test_audio_workloads_use_two_cpu_units_and_a_lower_automatic_cap(heavy_option):
    result = choose(
        **{
            heavy_option: True,
            "total_memory": 16 * GIB,
            "available_memory": 12 * GIB,
        }
    )

    assert result["workload"] == "heavy"
    assert result["cpu_weight"] == 2
    assert result["limits"]["physical_cpu"] == 3
    assert result["memory_per_worker_bytes"] == 1536 * 1024**2
    assert result["manual_max_workers"] == 3
    assert result["automatic_max_workers"] == 2
    assert result["selected_workers"] == 2


def test_custom_heavy_policy_may_use_hardware_max_above_recommendation():
    result = choose(
        deep_audio=True,
        requested_max=4,
        total_memory=16 * GIB,
        available_memory=12 * GIB,
    )

    assert result["recommended_workers"] == 2
    assert result["manual_max_workers"] == 3
    assert result["selected_workers"] == 3
    assert result["reason"] == "custom_capped"


def test_missing_memory_metrics_is_a_conservative_one_worker_fallback():
    result = choose(total_memory=0, available_memory=0)

    assert result["memory_metrics_available"] is False
    assert result["memory_reserve_bytes"] is None
    assert result["usable_memory_bytes"] is None
    assert result["limits"]["memory"] == 1
    assert result["manual_max_workers"] == 1
    assert result["selected_workers"] == 1
    assert result["reason"] == "limited_to_one"


def test_low_available_memory_never_selects_zero_workers():
    result = choose(total_memory=8 * GIB, available_memory=2 * GIB)

    assert result["memory_reserve_bytes"] == 2 * GIB
    assert result["usable_memory_bytes"] == 0
    assert result["limits"]["memory"] == 1
    assert result["selected_workers"] == 1


def test_physical_cpu_fallback_is_derived_from_injected_logical_count():
    result = choose(logical_cpus=12, physical_cpus=0)

    assert result["logical_cpus"] == 12
    assert result["physical_cpus"] == 6
    assert result["physical_cpus_estimated"] is True
    assert result["limits"]["physical_cpu"] == 5


@pytest.mark.parametrize(
    ("storage_kind", "normalized", "expected_limit"),
    [
        ("hdd", "rotational", 1),
        ("network", "network", 1),
        ("removable", "removable", 1),
        ("sata-ssd", "ssd", 2),
        ("ssd", "ssd", 2),
        ("nvme", "nvme", 4),
        ("unknown-local", "unknown_local", 2),
        ("surprise-device", "unknown_local", 2),
        (None, "unknown_local", 2),
    ],
)
def test_storage_class_sets_a_bounded_stream_limit(
    storage_kind, normalized, expected_limit
):
    result = choose(storage_kind=storage_kind)

    assert result["storage_kind"] == normalized
    assert result["limits"]["storage"] == expected_limit
    assert result["manual_max_workers"] <= expected_limit
    assert result["storage_kind_source"] == (
        "provided"
        if storage_kind in {"hdd", "network", "removable", "sata-ssd", "ssd", "nvme", "unknown-local"}
        else "conservative_fallback"
    )


@pytest.mark.parametrize(
    ("pressure", "expected_level", "expected_active"),
    [
        ({"disk_busy_ratio": 0.90}, "high", 1),
        ({"disk_busy_ratio": 90}, "high", 1),
        ({"disk_busy_ratio": 0.75}, "moderate", 2),
        ({"disk_queue_depth": 4}, "high", 1),
        ({"disk_queue_depth": 2}, "moderate", 2),
        ({"disk_busy_ratio": 0.20, "disk_queue_depth": 0.5}, "low", 3),
    ],
)
def test_transient_disk_pressure_only_reduces_the_active_limit(
    pressure, expected_level, expected_active
):
    result = choose(**pressure)

    assert result["selected_workers"] == 3
    assert result["manual_max_workers"] == 4
    assert result["disk_pressure"]["level"] == expected_level
    assert result["active_worker_limit"] == expected_active
    assert result["active_limit_reason"] == (
        f"disk_pressure_{expected_level}"
        if expected_active < result["selected_workers"]
        else "selected_ceiling"
    )


def test_invalid_pressure_metrics_do_not_reduce_the_stable_ceiling():
    result = choose(disk_busy_ratio=101, disk_queue_depth=-1)

    assert result["disk_pressure"] == {
        "level": "unknown",
        "metrics_available": False,
        "busy_ratio": None,
        "queue_depth": None,
        "active_worker_limit": 4,
    }
    assert result["active_worker_limit"] == result["selected_workers"] == 3


def test_automatic_small_scope_stays_sequential_but_manual_ceiling_is_visible():
    automatic = choose(pending_packages=2, storage_kind="ssd")
    custom = choose(pending_packages=2, storage_kind="ssd", requested_max=2)

    assert automatic["manual_max_workers"] == 2
    assert automatic["recommended_workers"] == 1
    assert automatic["selected_workers"] == 1
    assert automatic["reason"] == "small_scope"
    assert custom["selected_workers"] == 2
    assert custom["reason"] == "custom"


def test_unavailable_backend_and_empty_scope_still_return_one_safe_worker():
    unavailable = choose(worker_backend_available=False)
    empty = choose(pending_packages=0)

    assert unavailable["limits"]["worker_backend"] == 1
    assert unavailable["manual_max_workers"] == 1
    assert unavailable["selected_workers"] == 1
    assert unavailable["reason"] == "worker_backend_unavailable"
    assert empty["limits"]["packages"] == 1
    assert empty["selected_workers"] == 1


@pytest.mark.parametrize("requested", [None, 0, -1, True, "not-a-number"])
def test_invalid_manual_limits_leave_policy_in_automatic_mode(requested):
    result = choose(requested_max=requested)

    assert result["mode"] == "automatic"
    assert result["requested_max_workers"] is None
    assert result["selected_workers"] == result["recommended_workers"]
