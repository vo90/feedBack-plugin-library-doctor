import concurrent.futures
import importlib.util
import json
import logging
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _valid_package(library: Path, name="Artist/Song.feedpak") -> Path:
    root = library / name
    root.mkdir(parents=True)
    manifest = {
        "feedpak_version": "1.19.0",
        "title": "Song",
        "artist": "Artist",
        "duration": 30.0,
        "arrangements": [{"id": "lead", "file": "arrangements/lead.json"}],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (root / "arrangements").mkdir()
    (root / "arrangements" / "lead.json").write_text(
        json.dumps({"notes": [], "chords": []}), encoding="utf-8"
    )
    (root / "stems").mkdir()
    (root / "stems" / "full.ogg").write_bytes(b"audio")
    return root


def _client(
    tmp_path,
    *,
    with_library=True,
    validator_hook=None,
    preview_hook=None,
    repair_hook=None,
    scanner_hook=None,
):
    root = Path(__file__).parents[1]
    loaded = {}

    def load_sibling(name):
        if name not in loaded:
            loaded[name] = _load(
                root / f"{name}.py", f"library_doctor_routes_test_{name}_{id(loaded)}"
            )
            if name == "validator" and validator_hook is not None:
                validator_hook(loaded[name])
            if name == "library_doctor_scan_worker" and validator_hook is not None:
                validator = loaded["validator"]

                class ThreadValidationPool:
                    def __init__(self, *, max_workers, validator_version):
                        assert validator_version == validator.VALIDATOR_VERSION
                        self.executor = concurrent.futures.ThreadPoolExecutor(
                            max_workers=max_workers
                        )
                        self.cancelled = threading.Event()

                    def submit(self, path, package, deep_audio):
                        def run():
                            started = time.perf_counter()
                            if self.cancelled.is_set():
                                return {
                                    "outcome": "cancelled",
                                    "elapsed_seconds": 0.0,
                                }
                            options = {"scan_checkpoint": lambda: None}
                            if deep_audio:
                                options["deep_audio"] = True
                            report = validator.validate_feedpak(
                                path, package, **options
                            )
                            return {
                                "outcome": "complete",
                                "report": report,
                                "elapsed_seconds": max(
                                    0.0, time.perf_counter() - started
                                ),
                            }

                        return self.executor.submit(run)

                    def set_paused(self, _paused):
                        pass

                    def cancel(self):
                        self.cancelled.set()

                    def shutdown(self, **_options):
                        self.executor.shutdown(wait=True, cancel_futures=True)

                loaded[name].ValidationProcessPool = ThreadValidationPool
            if name == "preview_repair" and preview_hook is not None:
                preview_hook(loaded[name])
            if name == "repair" and repair_hook is not None:
                repair_hook(loaded[name])
            if name == "scanner" and scanner_hook is not None:
                scanner_hook(loaded[name])
        return loaded[name]

    library = tmp_path / "library"
    if with_library:
        library.mkdir()
    routes = _load(root / "routes.py", f"library_doctor_routes_test_{id(tmp_path)}")
    app = FastAPI()
    routes.setup(app, {
        "config_dir": tmp_path / "config",
        "get_dlc_dir": lambda: library if with_library else None,
        "load_sibling": load_sibling,
        "log": logging.getLogger("library-doctor-routes-tests"),
    })
    return TestClient(app), library


def _wait_for_scan(client, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/plugins/library_doctor/status").json()
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("Library Doctor scan did not finish")


def _wait_for_batch(client, phases, timeout=10):
    expected = {phases} if isinstance(phases, str) else set(phases)
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = client.get(
            "/api/plugins/library_doctor/repair/batch/status"
        ).json()
        if status["phase"] in expected:
            return status
        time.sleep(0.01)
    raise AssertionError(
        f"Library Doctor batch did not reach {sorted(expected)}; last status: {status}"
    )


def test_audio_response_supports_browser_byte_ranges():
    root = Path(__file__).parents[1]
    routes = _load(root / "routes.py", "library_doctor_routes_range_test")
    payload = b"0123456789"

    full = routes._audio_response(payload)
    first = routes._audio_response(payload, "bytes=0-3")
    remainder = routes._audio_response(payload, "bytes=7-")
    suffix = routes._audio_response(payload, "bytes=-3")

    assert full.status_code == 200
    assert full.body == payload
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["cache-control"] == "no-store"
    assert first.status_code == 206
    assert first.body == b"0123"
    assert first.headers["content-range"] == "bytes 0-3/10"
    assert remainder.body == b"789"
    assert remainder.headers["content-range"] == "bytes 7-9/10"
    assert suffix.body == b"789"
    assert suffix.headers["content-range"] == "bytes 7-9/10"


@pytest.mark.parametrize(
    "range_header",
    ["items=0-1", "bytes=", "bytes=0-1,4-5", "bytes=20-", "bytes=5-2", "bytes=-0"],
)
def test_audio_response_rejects_invalid_or_unsupported_ranges(range_header):
    root = Path(__file__).parents[1]
    routes = _load(
        root / "routes.py",
        f"library_doctor_routes_invalid_range_test_{range_header}",
    )

    response = routes._audio_response(b"0123456789", range_header)

    assert response.status_code == 416
    assert response.body == b""
    assert response.headers["content-range"] == "bytes */10"


def test_preview_tool_status_does_not_require_a_library_doctor_scan(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library)

    missing = client.get(
        "/api/plugins/library_doctor/repair/media/tool/status",
        params={"package": "Artist/Song.feedpak"},
    )

    assert missing.status_code == 200
    payload = missing.json()
    assert payload["schema"] == "library_doctor.preview_tool_status.v1"
    assert payload["available"] is True
    assert payload["rule_code"] == "media.preview-missing"
    assert payload["current_preview_available"] is False
    client.close()


def test_scan_and_results_are_available_through_plugin_routes(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library)

    response = client.post("/api/plugins/library_doctor/scan")
    assert response.status_code == 202
    assert response.json()["started"] is True
    status = _wait_for_scan(client)
    results = client.get("/api/plugins/library_doctor/results").json()
    rules = client.get("/api/plugins/library_doctor/rules").json()
    exported = client.get("/api/plugins/library_doctor/export?format=json")

    assert status["stage"] == "complete"
    assert status["summary"]["total"] == 1
    assert results["total"] == 1
    assert results["items"][0]["package"] == "Artist/Song.feedpak"
    assert rules == {"schema": "library_doctor.rules.v1", "items": []}
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith('"library-doctor-report.json"')
    assert exported.json()["packages"][0]["package"] == "Artist/Song.feedpak"
    assert str(library) not in response.text
    assert str(library) not in json.dumps(results)
    client.close()


def test_deep_audio_option_is_forwarded_and_reported(tmp_path):
    observed = []

    def hook(module):
        original = module.validate_feedpak

        def observe(*args, **kwargs):
            observed.append(kwargs.get("deep_audio", False))
            return original(*args, **kwargs)

        module.validate_feedpak = observe

    client, library = _client(tmp_path, validator_hook=hook)
    _valid_package(library)

    response = client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "library", "deep_audio": True},
    )
    status = _wait_for_scan(client)

    assert response.status_code == 202
    assert status["deep_audio"] is True
    assert observed == [True]
    client.close()


def test_single_archive_repair_reuses_current_deep_audio_scan(tmp_path):
    client, library = _client(tmp_path)
    package = library / "Artist" / "Song.feedpak"
    package.parent.mkdir()
    note = {"t": 2.0, "s": 1, "f": 5}
    manifest = {
        "feedpak_version": "1.19.0",
        "title": "Song",
        "artist": "Artist",
        "duration": 30.0,
        "arrangements": [{"id": "lead", "file": "arrangements/lead.json"}],
    }
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.yaml",
            yaml.safe_dump(manifest, sort_keys=False),
        )
        archive.writestr(
            "arrangements/lead.json",
            json.dumps({"notes": [note, dict(note)], "chords": []}),
        )

    started = client.post(
        "/api/plugins/library_doctor/scan?force=true",
        json={"scope": "library", "deep_audio": True},
    )
    scan = _wait_for_scan(client)
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
        },
    ).json()
    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    )

    assert started.status_code == 202
    assert scan["stage"] == "complete"
    assert applied.status_code == 200
    result = applied.json()
    assert result["verified_scan_report_reused"] is True
    assert result["deep_audio_reused"] is True
    assert result["performance"]["deep_audio_requested"] is True
    assert result["performance"]["verified_scan_report_reused"] is True
    assert result["performance"]["deep_audio_reused"] is True
    assert result["performance"]["elapsed_seconds"] >= 0
    history = client.get(
        "/api/plugins/library_doctor/repair/history?limit=1"
    ).json()["items"][0]
    assert history["performance"] == result["performance"]
    with zipfile.ZipFile(package, "r") as archive:
        arrangement = json.loads(archive.read("arrangements/lead.json"))
    assert arrangement["notes"] == [note]
    client.close()


def test_playback_state_pauses_and_resumes_scan(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library)

    invalid = client.put(
        "/api/plugins/library_doctor/playback",
        json={"active": "yes"},
    )
    held = client.put(
        "/api/plugins/library_doctor/playback",
        json={"active": True},
    )
    started = client.post("/api/plugins/library_doctor/scan")
    deadline = time.monotonic() + 2
    status = started.json()["status"]
    while status["stage"] != "paused" and time.monotonic() < deadline:
        status = client.get("/api/plugins/library_doctor/status").json()
        time.sleep(0.01)

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_request"
    assert held.status_code == 200
    assert status["running"] is True
    assert status["playback_active"] is True
    assert status["playback_paused"] is True

    released = client.put(
        "/api/plugins/library_doctor/playback",
        json={"active": False},
    )
    complete = _wait_for_scan(client)

    assert released.status_code == 200
    assert complete["stage"] == "complete"
    assert complete["playback_active"] is False
    client.close()


def test_start_is_idempotent_while_scan_is_running(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def hook(module):
        original = module.validate_feedpak

        def slow_validate(*args, **kwargs):
            entered.set()
            release.wait(2)
            return original(*args, **kwargs)

        module.validate_feedpak = slow_validate

    client, library = _client(tmp_path, validator_hook=hook)
    _valid_package(library)

    first = client.post("/api/plugins/library_doctor/scan").json()
    assert entered.wait(2)
    second = client.post("/api/plugins/library_doctor/scan").json()

    assert first["started"] is True
    assert second["started"] is False
    release.set()
    _wait_for_scan(client)
    client.close()


def test_folder_scan_is_recursive_and_results_are_scoped(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library, "ACDC/Album/One.feedpak")
    _valid_package(library, "Elsewhere/Two.feedpak")

    response = client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "folder", "path": str(library / "ACDC")},
    )
    assert response.status_code == 202
    status = _wait_for_scan(client)
    results = client.get("/api/plugins/library_doctor/results").json()

    assert status["target"] == {"kind": "folder", "label": "ACDC"}
    assert status["summary"]["total"] == 1
    assert results["total"] == 1
    assert results["items"][0]["package"] == "ACDC/Album/One.feedpak"
    assert str(library) not in json.dumps(status)
    client.close()


def test_single_file_scan_accepts_one_feedpak(tmp_path):
    client, library = _client(tmp_path)
    staging = _valid_package(library, "staging")
    chosen = library / "Chosen.feedpak"
    with zipfile.ZipFile(chosen, "w") as archive:
        for member in staging.rglob("*"):
            if member.is_file():
                archive.write(member, member.relative_to(staging).as_posix())
    _valid_package(library, "Other.feedpak")

    response = client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "file", "path": str(chosen)},
    )
    assert response.status_code == 202
    status = _wait_for_scan(client)
    results = client.get("/api/plugins/library_doctor/results").json()

    assert status["target"] == {"kind": "file", "label": "Chosen.feedpak"}
    assert results["total"] == 1
    assert results["items"][0]["package"] == "Chosen.feedpak"
    client.close()


def test_scan_rejects_targets_outside_the_configured_library(tmp_path):
    client, _library = _client(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    response = client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "folder", "path": str(outside)},
    )

    assert response.status_code == 400
    assert (
        "inside the configured song library"
        in response.json()["detail"]["message"]
    )
    assert str(outside) not in response.text
    client.close()


def test_unknown_result_filter_is_rejected(tmp_path):
    client, _library = _client(tmp_path)

    response = client.get("/api/plugins/library_doctor/results?filter=unknown")

    assert response.status_code == 400
    assert "Unknown result filter" in response.json()["detail"]["message"]
    client.close()


def test_core_route_payloads_match_the_phase2_typed_contracts(tmp_path):
    root = Path(__file__).parents[1]
    contracts = _load(
        root / "api_contracts.py", f"library_doctor_api_contracts_{id(tmp_path)}"
    )
    client, _library = _client(tmp_path)

    status = client.get("/api/plugins/library_doctor/status")
    results = client.get("/api/plugins/library_doctor/results")
    repairs = client.get("/api/plugins/library_doctor/repairs")
    error = client.get("/api/plugins/library_doctor/results?filter=not-a-filter")

    assert status.status_code == results.status_code == repairs.status_code == 200
    contracts.StatusContract.model_validate(status.json())
    contracts.ResultsContract.model_validate(results.json())
    catalog = contracts.RepairCatalogContract.model_validate(repairs.json())
    assert catalog.items
    assert error.status_code == 400
    contracts.ErrorEnvelopeContract.model_validate(error.json())
    client.close()


def test_openapi_exposes_typed_mutations_and_one_error_contract(tmp_path):
    client, _library = _client(tmp_path)
    document = client.app.openapi()
    paths = {
        path: operations
        for path, operations in document["paths"].items()
        if path.startswith("/api/plugins/library_doctor")
    }

    expected_requests = {
        "/api/plugins/library_doctor/playback": "PlaybackStateRequestContract",
        "/api/plugins/library_doctor/repair/apply": "RepairApplyRequestContract",
        "/api/plugins/library_doctor/repair/media/automatic": (
            "AutomaticPreviewRequestContract"
        ),
        "/api/plugins/library_doctor/repair/all/apply": "AllSafeApplyRequestContract",
        "/api/plugins/library_doctor/repair/restore": "RecoveryMutationRequestContract",
        "/api/plugins/library_doctor/repair/recovery/finalize": (
            "RecoveryMutationRequestContract"
        ),
    }
    for path, schema_name in expected_requests.items():
        schema = paths[path]["post" if path != "/api/plugins/library_doctor/playback" else "put"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(f"/{schema_name}")

    for operations in paths.values():
        for operation in operations.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status in ("400", "404", "409", "422", "500", "503"):
                schema = operation["responses"][status]["content"]["application/json"][
                    "schema"
                ]
                assert schema["$ref"].endswith("/ErrorEnvelopeContract")
    client.close()


def test_unexpected_database_fault_stays_inside_the_error_contract(tmp_path):
    private_detail = "C:/Private Artist/Unreleased Song.feedpak database malformed"

    def scanner_hook(module):
        def fail_status(_scanner):
            raise module.sqlite3.DatabaseError(private_detail)

        module.LibraryScanner.status = fail_status

    client, _library = _client(tmp_path, scanner_hook=scanner_hook)

    response = client.get("/api/plugins/library_doctor/status")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "internal_plugin_error",
        "message": "Library Doctor could not complete the request safely.",
        "file_state": "unchanged",
        "retryable": True,
        "next_action": "retry_later",
    }
    assert private_detail not in response.text
    client.close()


def test_exact_duplicate_repair_requires_preview_backs_up_and_refreshes_report(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5, "sus": 0.5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    catalog = client.get("/api/plugins/library_doctor/repairs").json()
    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    )

    assert "chart.duplicate-note" in {
        item["rule_code"] for item in catalog["items"]
    }
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is True
    assert plan["removed_count"] == 1
    assert plan["musical_positions"] == 1

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    assert applied.json()["applied"] is True
    assert applied.json()["cache_updated"] is True
    assert applied.json()["receipt_saved"] is True
    assert applied.json()["outcome"] == "success"
    assert applied.json()["player_result"]
    assert applied.json()["user_value"]
    assert applied.json()["file_handling"]["duplicate_library_package_created"] is False
    assert len(json.loads(arrangement.read_text(encoding="utf-8"))["notes"]) == 1
    backups = list((tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip"))
    assert len(backups) == 1
    with zipfile.ZipFile(backups[0], "r") as backup:
        metadata = json.loads(backup.read("repair.json"))
        assert metadata["schema"] == "library_doctor.repair_backup.v3"
        assert metadata["rule_code"] == "chart.duplicate-note"
        assert metadata["summary"]["removed_count"] == 1
    results = client.get("/api/plugins/library_doctor/results").json()
    assert results["items"][0]["status"] == "healthy"
    assert not any(
        finding["code"] == "chart.duplicate-note"
        for finding in results["items"][0]["findings"]
    )

    history = client.get("/api/plugins/library_doctor/repair/history?limit=1").json()
    assert history["items"][0]["outcome"] == "success"
    assert history["items"][0]["backup_id"] == applied.json()["backup_id"]

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert restored.json()["outcome"] == "restored"
    assert restored.json()["cache_updated"] is True
    assert restored.json()["receipt_saved"] is True
    assert len(json.loads(arrangement.read_text(encoding="utf-8"))["notes"]) == 2
    assert restored.json()["file_handling"]["backup_removed"] is True
    assert not backups[0].exists()
    restored_results = client.get("/api/plugins/library_doctor/results").json()
    assert any(
        finding["code"] == "chart.duplicate-note"
        for finding in restored_results["items"][0]["findings"]
    )
    client.close()


def test_bend_point_order_repair_is_lossless_validated_and_reversible(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    original_points = [
        {"t": 0.5, "v": 1.0, "future": {"marker": "last"}},
        {"t": 0.0, "v": 0.0, "future": {"marker": "first"}},
        {"t": 0.25, "v": 0.5, "future": {"marker": "middle"}},
    ]
    original = json.dumps({
        "notes": [{
            "t": 2.0,
            "s": 1,
            "f": 5,
            "sus": 1.0,
            "bn": 1.0,
            "bnv": original_points,
        }],
        "chords": [],
    }).encode("utf-8")
    arrangement_path.write_bytes(original)
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    before = client.get("/api/plugins/library_doctor/results").json()
    assert any(
        finding["code"] == "chart.bend-points-out-of-order"
        for finding in before["items"][0]["findings"]
    )
    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.bend-points-out-of-order",
        },
    )
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is True
    assert plan["change_kind"] == "reorder"
    assert plan["change_count"] == 1
    assert plan["removed_count"] == 0

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.bend-points-out-of-order",
            "plan_id": plan["plan_id"],
        },
    )
    assert applied.status_code == 200
    result = applied.json()
    assert result["change_kind"] == "reorder"
    assert result["change_count"] == 1
    assert result["removed_count"] == 0
    repaired_points = json.loads(
        arrangement_path.read_text(encoding="utf-8")
    )["notes"][0]["bnv"]
    assert repaired_points == [original_points[1], original_points[2], original_points[0]]
    assert sorted(repaired_points, key=lambda point: point["future"]["marker"]) == sorted(
        original_points, key=lambda point: point["future"]["marker"]
    )
    refreshed = client.get("/api/plugins/library_doctor/results").json()
    assert not any(
        finding["code"] == "chart.bend-points-out-of-order"
        for finding in refreshed["items"][0]["findings"]
    )

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": result["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert restored.json()["change_count"] == 1
    assert arrangement_path.read_bytes() == original
    restored_results = client.get("/api/plugins/library_doctor/results").json()
    assert any(
        finding["code"] == "chart.bend-points-out-of-order"
        for finding in restored_results["items"][0]["findings"]
    )
    client.close()


def test_lyric_order_repair_is_lossless_validated_and_reversible(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["lyrics"] = "lyrics.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    cues = [
        {"t": 2.0, "d": 0.2, "w": "later", "future": {"id": 3}},
        {"t": 1.0, "d": 0.2, "w": "equal first", "future": {"id": 1}},
        {"t": 1.0, "d": 0.3, "w": "equal second", "future": {"id": 2}},
    ]
    lyrics_path = package / "lyrics.json"
    original = json.dumps(cues).encode("utf-8")
    lyrics_path.write_bytes(original)
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    before = client.get("/api/plugins/library_doctor/results").json()
    finding = next(
        item for item in before["items"][0]["findings"]
        if item["code"] == "lyrics.out-of-order"
    )
    assert finding["rule"]["repairability"] == "safe_candidate"

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "lyrics.out-of-order",
        },
    )
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is True
    assert plan["change_kind"] == "reorder"
    assert plan["change_count"] == 1
    assert plan["removed_count"] == 0

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "lyrics.out-of-order",
            "plan_id": plan["plan_id"],
        },
    )
    assert applied.status_code == 200
    result = applied.json()
    repaired = json.loads(lyrics_path.read_text(encoding="utf-8"))
    assert repaired == [cues[1], cues[2], cues[0]]
    assert sorted(repaired, key=lambda cue: cue["future"]["id"]) == sorted(
        cues, key=lambda cue: cue["future"]["id"]
    )
    refreshed = client.get("/api/plugins/library_doctor/results").json()
    assert not any(
        item["code"] == "lyrics.out-of-order"
        for item in refreshed["items"][0]["findings"]
    )

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": result["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert lyrics_path.read_bytes() == original
    restored_results = client.get("/api/plugins/library_doctor/results").json()
    assert any(
        item["code"] == "lyrics.out-of-order"
        for item in restored_results["items"][0]["findings"]
    )
    client.close()


def test_negative_string_mute_repair_normalizes_frets_and_is_reversible(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    arrangement = {
        "notes": [
            {"t": 1.0, "s": 0, "f": -1, "sus": 0.25, "mt": True},
            {"t": 2.0, "s": 1, "f": -3, "mt": True, "future": "keep"},
            {"t": 3.0, "s": 2, "f": -1, "fhm": True},
        ],
        "chords": [],
    }
    original = json.dumps(arrangement).encode("utf-8")
    arrangement_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    report = client.get("/api/plugins/library_doctor/results").json()["items"][0]
    findings = {item["code"]: item for item in report["findings"]}
    assert findings["chart.negative-muted-fret"]["affected_count"] == 2
    assert findings["chart.negative-muted-fret"]["rule"]["repairability"] == (
        "safe_candidate"
    )
    assert findings["chart.negative-fret"]["affected_count"] == 1

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.negative-muted-fret",
        },
    )
    plan = preview.json()
    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["change_kind"] == "normalize"
    assert plan["change_count"] == 2
    assert plan["removed_count"] == 0
    assert plan["musical_positions"] == 2
    assert "same pitchless muted strikes" in plan["player_result"].lower()

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.negative-muted-fret",
            "plan_id": plan["plan_id"],
        },
    )
    assert applied.status_code == 200
    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert [note["f"] for note in repaired["notes"]] == [0, 0, -1]
    assert repaired["notes"][0]["mt"] is True
    assert repaired["notes"][1]["future"] == "keep"
    assert repaired["notes"][2]["fhm"] is True
    applied_codes = {
        item["code"] for item in applied.json()["report"]["findings"]
    }
    assert "chart.negative-muted-fret" not in applied_codes
    assert "chart.negative-fret" in applied_codes

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert arrangement_path.read_bytes() == original
    assert "chart.negative-muted-fret" in {
        item["code"] for item in restored.json()["report"]["findings"]
    }
    client.close()


def test_scan_accepts_a_custom_worker_ceiling_and_rejects_invalid_values(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library)

    invalid = client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "library", "max_workers": True},
    )
    accepted = client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "library", "max_workers": 8},
    )
    status = _wait_for_scan(client)

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_request"
    assert accepted.status_code == 202
    assert status["worker_policy"]["mode"] == "custom"
    assert status["worker_policy"]["limits"]["user"] == 8
    assert status["worker_policy"]["selected_workers"] == 1
    assert status["worker_policy"]["reason"] == "small_scope"
    client.close()


def test_recovery_finalization_keeps_repaired_feedpak_and_removes_undo_copy(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()
    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    ).json()
    backup = (
        tmp_path / "config" / "library_doctor" / "repair_backups"
        / f"{applied['backup_id']}.zip"
    )
    repaired_bytes = arrangement.read_bytes()

    finalized = client.post(
        "/api/plugins/library_doctor/repair/recovery/finalize",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied["backup_id"],
        },
    )

    assert finalized.status_code == 200
    result = finalized.json()
    assert result["outcome"] == "finalized"
    assert result["package_state"] == "repaired"
    assert result["undo_available"] is False
    assert result["file_handling"]["recovery_bytes_freed"] > 0
    assert arrangement.read_bytes() == repaired_bytes
    assert not backup.exists()
    unavailable = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied["backup_id"],
        },
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "backup_unavailable"
    history = client.get("/api/plugins/library_doctor/repair/history?limit=1").json()
    assert history["items"][0]["outcome"] == "finalized"
    assert history["items"][0]["undo_available"] is False
    client.close()


def test_mutation_request_id_replays_apply_and_restore_receipts(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()
    apply_body = {
        "package": "Artist/Song.feedpak",
        "rule_code": "chart.duplicate-note",
        "plan_id": plan["plan_id"],
        "request_id": "phase4-route-apply-0001",
    }

    first = client.post("/api/plugins/library_doctor/repair/apply", json=apply_body)
    replay = client.post("/api/plugins/library_doctor/repair/apply", json=apply_body)

    assert first.status_code == replay.status_code == 200
    assert first.json()["idempotent_replay"] is False
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["backup_id"] == first.json()["backup_id"]
    assert len(json.loads(arrangement.read_text(encoding="utf-8"))["notes"]) == 1
    lookup = client.get(
        "/api/plugins/library_doctor/repair/receipt/phase4-route-apply-0001"
    )
    assert lookup.status_code == 200
    assert lookup.json()["state"] == "complete"
    assert lookup.json()["operation"] == "repair.apply"

    reused = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={**apply_body, "plan_id": "0" * 64},
    )
    mismatch = client.post(
        "/api/plugins/library_doctor/repair/apply",
        headers={"Idempotency-Key": "phase4-route-apply-other"},
        json=apply_body,
    )
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "idempotency_key_reused"
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"

    restore_body = {
        "package": "Artist/Song.feedpak",
        "backup_id": first.json()["backup_id"],
        "request_id": "phase4-route-restore-0001",
    }
    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json=restore_body,
    )
    restored_replay = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json=restore_body,
    )
    assert restored.status_code == restored_replay.status_code == 200
    assert restored_replay.json()["idempotent_replay"] is True
    assert restored_replay.json()["outcome"] == "restored"
    assert len(json.loads(arrangement.read_text(encoding="utf-8"))["notes"]) == 2
    client.close()


def test_finalization_reserves_mutation_lane_and_replays_after_backup_removal(tmp_path):
    finalize_entered = threading.Event()
    allow_finalize = threading.Event()

    def repair_hook(module):
        original = module.RepairService.finalize_backup

        def blocked_finalize(service, *args, **kwargs):
            finalize_entered.set()
            assert allow_finalize.wait(5)
            return original(service, *args, **kwargs)

        module.RepairService.finalize_backup = blocked_finalize

    client, library = _client(tmp_path, repair_hook=repair_hook)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()
    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    ).json()
    finalize_body = {
        "package": "Artist/Song.feedpak",
        "backup_id": applied["backup_id"],
        "request_id": "phase4-route-finalize-0001",
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.post,
            "/api/plugins/library_doctor/repair/recovery/finalize",
            json=finalize_body,
        )
        assert finalize_entered.wait(3)
        try:
            concurrent_undo = client.post(
                "/api/plugins/library_doctor/repair/restore",
                json={
                    "package": "Artist/Song.feedpak",
                    "backup_id": applied["backup_id"],
                },
            )
            assert concurrent_undo.status_code == 409
            assert concurrent_undo.json()["detail"]["code"] == "operation_busy"
            assert concurrent_undo.json()["detail"]["retryable"] is True
        finally:
            allow_finalize.set()
        finalized = future.result(timeout=5)

    assert finalized.status_code == 200
    assert finalized.json()["idempotent_replay"] is False
    replay = client.post(
        "/api/plugins/library_doctor/repair/recovery/finalize",
        json=finalize_body,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["backup_id"] == applied["backup_id"]
    client.close()


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        ("/repair/apply", {"package": "Song.feedpak", "unexpected": True}),
        ("/repair/restore", {"package": "Song.feedpak", "backup_id": 123}),
        ("/scan", {"scope": "library", "max_workers": True}),
    ),
)
def test_request_validation_uses_the_uniform_error_envelope(tmp_path, path, payload):
    root = Path(__file__).parents[1]
    contracts = _load(
        root / "api_contracts.py", f"library_doctor_phase4_contracts_{id(payload)}"
    )
    client, _library = _client(tmp_path)

    response = client.post(f"/api/plugins/library_doctor{path}", json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert set(detail) == {
        "code",
        "message",
        "file_state",
        "retryable",
        "next_action",
    }
    assert detail["code"] == "invalid_request"
    assert detail["file_state"] == "unchanged"
    assert detail["retryable"] is False
    assert detail["next_action"] == "correct_request"
    contracts.ErrorEnvelopeContract.model_validate(response.json())
    client.close()


def test_automatic_preview_route_finishes_without_retained_recovery(
    tmp_path,
):
    source = b"OggS" + (b"full-song" * 200)
    candidate = b"OggS" + (b"new-preview" * 150)

    def validator_hook(module):
        def validate(path, package_name, *, deep_audio=False, **_kwargs):
            manifest = yaml.safe_load((Path(path) / "manifest.yaml").read_bytes())
            preview_path = manifest.get("preview")
            preview_exists = bool(
                preview_path and (Path(path) / str(preview_path)).is_file()
            )
            return {
                "schema": "library_doctor.report.v1",
                "package": package_name,
                "title": manifest.get("title") or package_name,
                "artist": manifest.get("artist") or "",
                "status": "healthy",
                "counts": {"error": 0, "warning": 0, "info": 0},
                "findings": [],
                "features": {
                    "preview_declared": preview_exists,
                    "preview_available": preview_exists,
                    "preview_source_available": True,
                    "repair_eligibility": {
                        "media.preview-missing": {"status": "automatic"},
                    },
                    "lyrics_declared": False,
                    "deep_audio_checked": deep_audio,
                },
            }

        module.validate_feedpak = validate
        module.probe_ogg_duration = lambda raw: 60.0 if raw == source else 30.0

    def preview_hook(module):
        module._probe_with_ffmpeg = lambda raw: 60.0 if raw == source else 30.0
        module._render_with_ffmpeg = (
            lambda raw, _start, _duration: candidate if raw == source else b""
        )
        module._loudest_start_with_ffmpeg = (
            lambda _raw, _duration, _target: 15.0
        )

    client, library = _client(
        tmp_path,
        validator_hook=validator_hook,
        preview_hook=preview_hook,
    )
    package = _valid_package(library)
    (package / "stems" / "full.ogg").write_bytes(source)
    original_manifest = (package / "manifest.yaml").read_bytes()

    applied = client.post(
        "/api/plugins/library_doctor/repair/media/automatic",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "media.preview-missing",
        },
    )

    assert applied.status_code == 200
    result = applied.json()
    repaired_manifest = yaml.safe_load((package / "manifest.yaml").read_bytes())
    preview_path = repaired_manifest["preview"]
    assert result["outcome"] == "success"
    assert result["media"]["creates_preview"] is True
    assert result["cache_updated"] is True
    assert result["undo_available"] is False
    assert result["file_handling"]["backup_removed"] is True
    assert (package / preview_path).read_bytes() == candidate
    assert (package / "stems" / "full.ogg").read_bytes() == source

    current = client.get(
        "/api/plugins/library_doctor/repair/media/current",
        params={"package": "Artist/Song.feedpak"},
        headers={"Range": "bytes=0-7"},
    )
    assert current.status_code == 206
    assert current.headers["content-range"] == f"bytes 0-7/{len(candidate)}"
    assert current.content == candidate[:8]

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": result["backup_id"],
        },
    )
    assert restored.status_code == 409
    assert restored.json()["detail"]["code"] == "backup_unavailable"
    assert (package / "manifest.yaml").read_bytes() != original_manifest
    assert (package / preview_path).read_bytes() == candidate
    assert (package / "stems" / "full.ogg").read_bytes() == source
    client.close()


def test_batch_can_optionally_create_flagged_previews_without_retained_backups(
    tmp_path,
):
    source = b"OggS" + (b"batch-full-song" * 200)
    candidate = b"OggS" + (b"batch-preview" * 150)

    def validator_hook(module):
        def validate(path, package_name, *, deep_audio=False, **_kwargs):
            manifest = yaml.safe_load((Path(path) / "manifest.yaml").read_bytes())
            preview_path = manifest.get("preview")
            preview_exists = bool(
                preview_path and (Path(path) / str(preview_path)).is_file()
            )
            arrangement = json.loads(
                (Path(path) / "arrangements" / "lead.json").read_text(
                    encoding="utf-8"
                )
            )
            notes = arrangement.get("notes") or []
            duplicate_notes = len(notes) > 1 and notes[0] == notes[1]
            findings = ([{
                "severity": "warning",
                "code": "chart.duplicate-note",
                "message": "An exact duplicate note is stored twice.",
                "category": "validation",
                "location": "arrangements/lead.json:notes[1]",
                "arrangement_id": "lead",
                "time": 2.0,
                "string": 1,
                "affected_count": 1,
            }] if duplicate_notes else [])
            return {
                "schema": "library_doctor.package.v1",
                "package": package_name,
                "title": manifest.get("title") or package_name,
                "artist": manifest.get("artist") or "",
                "status": "warning" if findings else "healthy",
                "counts": {
                    "error": 0,
                    "warning": len(findings),
                    "info": 0,
                },
                "findings": findings,
                "features": {
                    "preview_declared": preview_exists,
                    "preview_available": preview_exists,
                    "preview_source_available": True,
                    "repair_eligibility": {
                        "media.preview-missing": {"status": "automatic"},
                    },
                    "lyrics_declared": False,
                    "deep_audio_checked": deep_audio,
                },
            }

        module.validate_feedpak = validate
        module.probe_ogg_duration = lambda raw: 90.0 if raw == source else 30.0

    def preview_hook(module):
        module._probe_with_ffmpeg = lambda raw: 90.0 if raw == source else 30.0
        module._render_with_ffmpeg = (
            lambda raw, _start, _duration: candidate if raw == source else b""
        )
        module._loudest_start_with_ffmpeg = (
            lambda _raw, _duration, _target: 20.0
        )

    client, library = _client(
        tmp_path,
        validator_hook=validator_hook,
        preview_hook=preview_hook,
    )
    package = _valid_package(library)
    (package / "stems" / "full.ogg").write_bytes(source)
    arrangement_path = package / "arrangements" / "lead.json"
    duplicate = {"t": 2.0, "s": 1, "f": 5}
    arrangement_path.write_text(
        json.dumps({"notes": [duplicate, dict(duplicate)], "chords": []}),
        encoding="utf-8",
    )
    preview_only = _valid_package(library, "PreviewOnly.feedpak")
    (preview_only / "stems" / "full.ogg").write_bytes(source)
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    default_started = client.post(
        "/api/plugins/library_doctor/repair/batch/preview"
    )
    default_preview = _wait_for_batch(client, "ready")["preview"]
    assert default_started.status_code == 202
    assert default_preview["include_preview_repairs"] is False
    assert default_preview["preview_repair_count"] == 0
    assert default_preview["eligible_count"] == 1

    started = client.post(
        "/api/plugins/library_doctor/repair/batch/preview",
        json={"include_preview_repairs": True},
    )
    ready = _wait_for_batch(client, "ready")
    preview = ready["preview"]

    assert started.status_code == 202
    assert preview["include_preview_repairs"] is True
    assert preview["eligible_count"] == 2
    assert preview["safe_repair_package_count"] == 1
    assert preview["preview_repair_count"] == 2
    assert preview["mixed_repair_package_count"] == 1
    assert all(
        item["preview_rule_code"] == "media.preview-missing"
        for item in preview["packages"]
    )

    applied = client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    completed = _wait_for_batch(client, "completed")
    result = completed["result"]
    repaired_manifest = yaml.safe_load((package / "manifest.yaml").read_bytes())

    assert applied.status_code == 202
    assert result["successful_count"] == 2
    assert result["preview_successful_count"] == 2
    assert result["preview_failed_count"] == 0
    assert result["backup_count"] == 1
    assert result["undoable_count"] == 1
    mixed = next(
        item for item in result["outcomes"]
        if item["package"] == "Artist/Song.feedpak"
    )
    finalized = next(
        item for item in result["outcomes"]
        if item["package"] == "PreviewOnly.feedpak"
    )
    assert mixed["outcome"] == "success"
    assert mixed["preview_repaired"] is True
    assert finalized["outcome"] == "finalized"
    assert finalized["preview_repaired"] is True
    assert len(json.loads(arrangement_path.read_text(encoding="utf-8"))["notes"]) == 1
    assert (package / repaired_manifest["preview"]).read_bytes() == candidate
    backup_root = tmp_path / "config" / "library_doctor" / "repair_backups"
    assert len(list(backup_root.glob("*.zip"))) == 1
    client.close()


def test_zero_length_handshape_repair_preserves_matching_chords_and_is_reversible(
    tmp_path,
):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    chord = {"t": 10.0, "id": 0, "notes": [{"s": 0, "f": 3}]}
    handshape = {"chord_id": 0, "start_time": 10.0, "end_time": 10.0}
    arrangement = {
        "notes": [],
        "chords": [chord],
        "anchors": [],
        "handshapes": [handshape],
        "templates": [{"frets": [3], "fingers": [1]}],
        "phrases": [{
            "start_time": 0.0,
            "end_time": 20.0,
            "max_difficulty": 1,
            "levels": [{
                "difficulty": 1,
                "notes": [],
                "chords": [json.loads(json.dumps(chord))],
                "anchors": [],
                "handshapes": [dict(handshape)],
            }],
        }],
    }
    original = json.dumps(arrangement).encode("utf-8")
    arrangement_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    report = client.get("/api/plugins/library_doctor/results").json()["items"][0]
    finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.zero-length-handshape"
    )
    assert finding["affected_count"] == 1
    assert finding["rule"]["repairability"] == "safe_candidate"

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.zero-length-handshape",
        },
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["change_kind"] == "remove_redundant"
    assert plan["removed_count"] == 2
    assert plan["musical_positions"] == 1
    assert "matching authored chord remains" in plan["player_result"].lower()

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.zero-length-handshape",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert repaired["chords"] == [chord]
    assert repaired["handshapes"] == []
    level = repaired["phrases"][0]["levels"][0]
    assert level["chords"] == [chord]
    assert level["handshapes"] == []
    assert "chart.zero-length-handshape" not in {
        item["code"] for item in applied.json()["report"]["findings"]
    }

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert arrangement_path.read_bytes() == original
    assert "chart.zero-length-handshape" in {
        item["code"] for item in restored.json()["report"]["findings"]
    }
    client.close()


def test_zero_length_handshape_that_may_supply_a_chord_is_blocked(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    original = json.dumps({
        "notes": [],
        "chords": [],
        "handshapes": [{
            "chord_id": 0,
            "start_time": 10.0,
            "end_time": 10.0,
        }],
        "templates": [{"frets": [3], "fingers": [1]}],
    }).encode("utf-8")
    arrangement_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.zero-length-handshape",
        },
    )

    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is False
    assert plan["blockers"][0]["code"] == (
        "zero_length_handshape_requires_review"
    )
    assert "could supply a chord" in plan["blockers"][0]["message"]
    assert arrangement_path.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_reversed_handshape_repair_preserves_matching_chords_and_is_reversible(
    tmp_path,
):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    chord = {
        "t": 10.0,
        "id": 0,
        "notes": [{"s": 0, "f": 3}, {"s": 1, "f": 5}],
    }
    handshape = {"chord_id": 0, "start_time": 10.0, "end_time": 9.75}
    arrangement = {
        "notes": [],
        "chords": [chord],
        "anchors": [],
        "handshapes": [handshape],
        "templates": [{"name": "C", "frets": [3, 5], "fingers": [1, 3]}],
        "phrases": [{
            "start_time": 0.0,
            "end_time": 20.0,
            "max_difficulty": 1,
            "levels": [{
                "difficulty": 1,
                "notes": [],
                "chords": [json.loads(json.dumps(chord))],
                "anchors": [],
                "handshapes": [dict(handshape)],
            }],
        }],
    }
    original = json.dumps(arrangement).encode("utf-8")
    arrangement_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    report = client.get("/api/plugins/library_doctor/results").json()["items"][0]
    finding = next(
        item for item in report["findings"]
        if item["code"] == "chart.invalid-handshape-span"
    )
    assert finding["affected_count"] == 1
    assert finding["rule"]["repairability"] == "safe_candidate"

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.invalid-handshape-span",
        },
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["change_kind"] == "remove_redundant"
    assert plan["removed_count"] == 2
    assert plan["musical_positions"] == 1
    assert "matching authored chord remains" in plan["player_result"].lower()

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.invalid-handshape-span",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert repaired["chords"] == [chord]
    assert repaired["handshapes"] == []
    level = repaired["phrases"][0]["levels"][0]
    assert level["chords"] == [chord]
    assert level["handshapes"] == []
    assert "chart.invalid-handshape-span" not in {
        item["code"] for item in applied.json()["report"]["findings"]
    }

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert arrangement_path.read_bytes() == original
    assert "chart.invalid-handshape-span" in {
        item["code"] for item in restored.json()["report"]["findings"]
    }
    client.close()


def test_reversed_handshape_that_may_supply_a_chord_is_blocked(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    original = json.dumps({
        "notes": [],
        "chords": [],
        "handshapes": [{
            "chord_id": 0,
            "start_time": 10.0,
            "end_time": 9.75,
        }],
        "templates": [{"frets": [3], "fingers": [1]}],
    }).encode("utf-8")
    arrangement_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.invalid-handshape-span",
        },
    )

    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is False
    assert plan["blockers"][0]["code"] == "reversed_handshape_requires_review"
    assert "could supply a chord" in plan["blockers"][0]["message"]
    assert arrangement_path.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_fix_all_safe_issues_is_one_validated_reversible_package_transaction(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["drum_tab"] = "drums.json"
    manifest["lyrics"] = "lyrics.json"
    manifest["arrangements"].append({
        "id": "rhythm",
        "file": "arrangements/rhythm.json",
    })
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    chord_note = {"s": 1, "f": 5, "sus": 0.5}
    chord = {
        "t": 2.0,
        "id": 0,
        "notes": [chord_note, dict(chord_note), {"s": 2, "f": 7}],
    }
    note = {"t": 2.0, **chord_note}
    anchor = {"time": 1.0, "fret": 3, "width": 4}
    handshape = {"start_time": 1.0, "end_time": 3.0, "chord_id": 0}
    zero_handshape = {"start_time": 2.0, "end_time": 2.0, "chord_id": 0}
    beat = {"time": 1.0, "measure": 0}
    arrangement = {
        "notes": [note, dict(note)],
        "chords": [chord, json.loads(json.dumps(chord))],
        "anchors": [anchor, dict(anchor)],
        "handshapes": [
            handshape,
            dict(handshape),
            zero_handshape,
            dict(zero_handshape),
        ],
        "beats": [beat, dict(beat)],
        "sections": [],
        "templates": [{
            "frets": [-1, 5, 7, -1, -1, -1],
            "fingers": [-1, 1, 3, -1, -1, -1],
        }],
    }
    arrangement_path = package / "arrangements" / "lead.json"
    original_arrangement = json.dumps(arrangement).encode("utf-8")
    arrangement_path.write_bytes(original_arrangement)
    inactive_beat = {"time": 9.0, "measure": 9}
    section = {"name": "Intro", "time": 0.0, "number": 1}
    rhythm_path = package / "arrangements" / "rhythm.json"
    rhythm_original = json.dumps({
        "notes": [],
        "chords": [],
        "beats": [inactive_beat, dict(inactive_beat)],
        "sections": [section, dict(section)],
    }).encode("utf-8")
    rhythm_path.write_bytes(rhythm_original)
    hit = {"t": 4.0, "p": "snare", "v": 100}
    original_drums = json.dumps({
        "version": 1, "hits": [hit, dict(hit)]
    }).encode("utf-8")
    drums_path = package / "drums.json"
    drums_path.write_bytes(original_drums)
    original_lyrics = json.dumps([
        {"t": 2.0, "d": 0.2, "w": "later"},
        {"t": 1.0, "d": 0.2, "w": "earlier"},
    ]).encode("utf-8")
    lyrics_path = package / "lyrics.json"
    lyrics_path.write_bytes(original_lyrics)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    catalog = client.get("/api/plugins/library_doctor/repairs").json()
    preview = client.post(
        "/api/plugins/library_doctor/repair/all/preview",
        json={"package": "Artist/Song.feedpak"},
    )

    assert catalog["combined"]["rule_code"] == "package.all-safe"
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is True
    assert plan["rule_codes"] == [
        "chart.duplicate-chord-note",
        "chart.duplicate-chord",
        "chart.duplicate-note",
        "chart.note-duplicates-chord",
        "chart.duplicate-anchor",
        "chart.duplicate-handshape",
        "chart.zero-length-handshape",
        "lyrics.out-of-order",
        "timeline.duplicate-beat",
        "timeline.duplicate-section",
        "drums.duplicate-hit",
    ]
    assert plan["rule_count"] == 11
    assert plan["removed_count"] == 12
    assert plan["member_count"] == 4

    applied = client.post(
        "/api/plugins/library_doctor/repair/all/apply",
        json={
            "package": "Artist/Song.feedpak",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    result = applied.json()
    assert result["rule_code"] == "package.all-safe"
    assert result["rule_codes"] == plan["rule_codes"]
    assert result["cache_updated"] is True
    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert repaired["notes"] == []
    assert len(repaired["chords"]) == 1
    assert repaired["chords"][0]["notes"] == [
        chord_note,
        {"s": 2, "f": 7},
    ]
    assert len(repaired["anchors"]) == 1
    assert len(repaired["handshapes"]) == 1
    assert repaired["beats"] == [beat]
    repaired_rhythm = json.loads(rhythm_path.read_text(encoding="utf-8"))
    assert repaired_rhythm["beats"] == [inactive_beat, inactive_beat]
    assert repaired_rhythm["sections"] == [section]
    assert len(json.loads(drums_path.read_text(encoding="utf-8"))["hits"]) == 1
    assert [cue["t"] for cue in json.loads(
        lyrics_path.read_text(encoding="utf-8")
    )] == [1.0, 2.0]
    backups = list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip")
    )
    assert len(backups) == 1
    with zipfile.ZipFile(backups[0], "r") as backup:
        metadata = json.loads(backup.read("repair.json"))
        assert metadata["rule_code"] == "package.all-safe"
        assert metadata["rule_codes"] == plan["rule_codes"]
        assert len(metadata["members"]) == 4
        assert len(metadata["summary"]["repair_summaries"]) == 11

    refreshed = client.get("/api/plugins/library_doctor/results").json()
    refreshed_codes = {
        finding["code"] for finding in refreshed["items"][0]["findings"]
    }
    assert not (set(plan["rule_codes"]) & refreshed_codes)

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": result["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert restored.json()["rule_codes"] == plan["rule_codes"]
    assert arrangement_path.read_bytes() == original_arrangement
    assert rhythm_path.read_bytes() == rhythm_original
    assert drums_path.read_bytes() == original_drums
    assert lyrics_path.read_bytes() == original_lyrics
    assert restored.json()["file_handling"]["backup_removed"] is True
    assert not backups[0].exists()
    client.close()


def test_duplicate_beat_repair_uses_active_sidecar_and_leaves_conflicts_for_review(
    tmp_path,
):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["song_timeline"] = "song_timeline.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    arrangement_path = package / "arrangements" / "lead.json"
    dormant_beat = {"time": 9.0, "measure": 9}
    arrangement_path.write_text(
        json.dumps({
            "notes": [],
            "chords": [],
            "beats": [dormant_beat, dict(dormant_beat)],
            "sections": [],
        }),
        encoding="utf-8",
    )
    dormant_original = arrangement_path.read_bytes()

    first = {"time": 0.0, "measure": 0}
    repeated_time = {"time": 1.0, "measure": 0}
    conflict = {"time": 1.0, "measure": 1}
    timeline = {
        "version": 1,
        "beats": [
            first,
            repeated_time,
            dict(first),
            {"time": 2.0, "measure": 0},
            conflict,
        ],
        "sections": [],
    }
    timeline_path = package / "song_timeline.json"
    timeline_original = json.dumps(timeline).encode("utf-8")
    timeline_path.write_bytes(timeline_original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    findings = {
        item["code"]: item
        for item in client.get(
            "/api/plugins/library_doctor/results"
        ).json()["items"][0]["findings"]
    }
    assert findings["timeline.duplicate-beat"]["rule"]["repairability"] == (
        "safe_candidate"
    )
    assert "timeline.repeated-beat-time" in findings

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.duplicate-beat",
        },
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["removed_count"] == 1
    assert plan["member_count"] == 1
    assert "Conflicting beat markers" in plan["player_result"]

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.duplicate-beat",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    repaired = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert repaired["beats"] == [
        first,
        repeated_time,
        {"time": 2.0, "measure": 0},
        conflict,
    ]
    assert arrangement_path.read_bytes() == dormant_original
    refreshed_codes = {
        finding["code"]
        for finding in applied.json()["report"]["findings"]
    }
    assert "timeline.duplicate-beat" not in refreshed_codes
    assert "timeline.repeated-beat-time" in refreshed_codes

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert timeline_path.read_bytes() == timeline_original
    assert arrangement_path.read_bytes() == dormant_original
    client.close()


def test_duplicate_section_repair_uses_active_sidecar_and_leaves_conflicts_for_review(
    tmp_path,
):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["song_timeline"] = "song_timeline.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    arrangement_path = package / "arrangements" / "lead.json"
    dormant_section = {"name": "Dormant", "time": 9.0, "number": 9}
    arrangement_path.write_text(
        json.dumps({
            "notes": [],
            "chords": [],
            "beats": [],
            "sections": [dormant_section, dict(dormant_section)],
        }),
        encoding="utf-8",
    )
    dormant_original = arrangement_path.read_bytes()

    intro = {"name": "Intro", "time": 0.0, "number": 1}
    verse = {"name": "Verse", "time": 1.0, "number": 1}
    bridge = {"name": "Bridge", "time": 2.0, "number": 1}
    conflict = {"name": "Chorus", "time": 1.0, "number": 2}
    timeline = {
        "version": 1,
        "beats": [],
        "sections": [intro, verse, dict(intro), bridge, conflict],
    }
    timeline_path = package / "song_timeline.json"
    timeline_original = json.dumps(timeline).encode("utf-8")
    timeline_path.write_bytes(timeline_original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    findings = {
        item["code"]: item
        for item in client.get(
            "/api/plugins/library_doctor/results"
        ).json()["items"][0]["findings"]
    }
    assert findings["timeline.duplicate-section"]["rule"]["repairability"] == (
        "safe_candidate"
    )
    assert "timeline.repeated-section-time" in findings

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.duplicate-section",
        },
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["removed_count"] == 1
    assert plan["member_count"] == 1
    assert "Conflicting section markers" in plan["player_result"]

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.duplicate-section",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    repaired = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert repaired["sections"] == [intro, verse, bridge, conflict]
    assert arrangement_path.read_bytes() == dormant_original
    refreshed_codes = {
        finding["code"] for finding in applied.json()["report"]["findings"]
    }
    assert "timeline.duplicate-section" not in refreshed_codes
    assert "timeline.repeated-section-time" in refreshed_codes

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert timeline_path.read_bytes() == timeline_original
    assert arrangement_path.read_bytes() == dormant_original
    client.close()


@pytest.mark.parametrize(
    ("field", "rule_code", "markers"),
    [
        (
            "beats",
            "timeline.beats-out-of-order",
            [
                {"time": 5.0, "measure": 2},
                {"time": 1.0, "measure": 1},
                {"time": 1.0, "measure": -1},
                {"time": 3.0, "measure": -1},
            ],
        ),
        (
            "sections",
            "timeline.sections-out-of-order",
            [
                {"time": 5.0, "name": "Outro", "number": 1},
                {"time": 1.0, "name": "Intro", "number": 1},
                {"time": 1.0, "name": "Count-in", "number": 2},
                {"time": 3.0, "name": "Verse", "number": 1},
            ],
        ),
    ],
)
def test_timeline_order_repair_is_lossless_validated_and_reversible(
    tmp_path, field, rule_code, markers,
):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["song_timeline"] = "song_timeline.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    timeline = {"version": 1, "beats": [], "sections": [], field: markers}
    timeline_path = package / "song_timeline.json"
    original = json.dumps(timeline).encode("utf-8")
    timeline_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    report = client.get("/api/plugins/library_doctor/results").json()["items"][0]
    finding = next(item for item in report["findings"] if item["code"] == rule_code)
    assert finding["rule"]["repairability"] == "safe_candidate"

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": rule_code},
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["change_kind"] == "reorder"
    assert plan["change_count"] == 1
    assert plan["removed_count"] == 0
    assert plan["arrays_affected"] == 1
    assert plan["musical_positions"] == 3

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": rule_code,
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    result = applied.json()
    repaired = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert repaired[field] == [markers[1], markers[2], markers[3], markers[0]]
    assert not any(
        item["code"] == rule_code for item in result["report"]["findings"]
    )
    backups = list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip")
    )
    assert len(backups) == 1
    with zipfile.ZipFile(backups[0]) as backup:
        metadata = json.loads(backup.read("repair.json"))
    assert metadata["summary"]["change_count"] == 1
    assert metadata["summary"]["removed_count"] == 0

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": result["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert timeline_path.read_bytes() == original
    assert any(
        item["code"] == rule_code
        for item in restored.json()["report"]["findings"]
    )
    client.close()


def test_timeline_order_repair_blocks_an_invalid_marker_without_a_backup(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["song_timeline"] = "song_timeline.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    timeline_path = package / "song_timeline.json"
    original = json.dumps({
        "version": 1,
        "beats": [
            {"time": 3.0, "measure": 1},
            {"time": 2.0, "measure": "invalid"},
            {"time": 1.0, "measure": 0},
        ],
        "sections": [],
    }).encode("utf-8")
    timeline_path.write_bytes(original)
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.beats-out-of-order",
        },
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is False
    assert plan["blockers"][0]["code"] == "invalid_beat_timeline"
    assert timeline_path.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_fix_all_removes_timeline_duplicates_before_reordering(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["song_timeline"] = "song_timeline.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    later = {"time": 2.0, "measure": 2}
    earlier = {"time": 0.0, "measure": 1}
    timeline_path = package / "song_timeline.json"
    original = json.dumps({
        "version": 1,
        "beats": [later, earlier, dict(later)],
        "sections": [],
    }).encode("utf-8")
    timeline_path.write_bytes(original)
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    preview = client.post(
        "/api/plugins/library_doctor/repair/all/preview",
        json={"package": "Artist/Song.feedpak"},
    )
    plan = preview.json()

    assert preview.status_code == 200
    assert plan["available"] is True
    assert plan["rule_codes"] == [
        "timeline.duplicate-beat",
        "timeline.beats-out-of-order",
    ]
    assert plan["change_count"] == 2
    assert plan["removed_count"] == 1

    applied = client.post(
        "/api/plugins/library_doctor/repair/all/apply",
        json={"package": "Artist/Song.feedpak", "plan_id": plan["plan_id"]},
    )
    assert applied.status_code == 200
    assert json.loads(timeline_path.read_text(encoding="utf-8"))["beats"] == [
        earlier,
        later,
    ]
    assert not ({
        "timeline.duplicate-beat",
        "timeline.beats-out-of-order",
    } & {item["code"] for item in applied.json()["report"]["findings"]})

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )
    assert restored.status_code == 200
    assert timeline_path.read_bytes() == original
    client.close()


def test_undo_restores_exact_original_when_related_timeline_findings_return(
    tmp_path,
):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["song_timeline"] = "song_timeline.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    first = {"time": 0.0, "measure": 0}
    second = {"time": 1.0, "measure": 0}
    timeline = {
        "version": 1,
        "beats": [first, second, dict(first)],
        "sections": [],
    }
    timeline_path = package / "song_timeline.json"
    original = json.dumps(timeline).encode("utf-8")
    timeline_path.write_bytes(original)

    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    original_codes = {
        finding["code"]
        for finding in client.get(
            "/api/plugins/library_doctor/results"
        ).json()["items"][0]["findings"]
    }
    assert {
        "timeline.duplicate-beat",
        "timeline.beats-out-of-order",
    } <= original_codes

    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.duplicate-beat",
        },
    ).json()
    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "timeline.duplicate-beat",
            "plan_id": plan["plan_id"],
        },
    )
    assert applied.status_code == 200
    assert applied.json()["report"]["findings"] == []

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied.json()["backup_id"],
        },
    )

    assert restored.status_code == 200
    result = restored.json()
    assert result["returning_finding_codes"] == [
        "timeline.beats-out-of-order",
        "timeline.duplicate-beat",
    ]
    assert result["returning_finding_count"] == 2
    assert "related findings may return" in result["player_result"]
    assert timeline_path.read_bytes() == original
    assert {
        finding["code"] for finding in result["report"]["findings"]
    } == original_codes
    refreshed_codes = {
        finding["code"]
        for finding in client.get(
            "/api/plugins/library_doctor/results"
        ).json()["items"][0]["findings"]
    }
    assert refreshed_codes == original_codes
    client.close()


def test_fix_all_safe_issues_refuses_a_stale_preview_without_changing_files(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    anchor = {"time": 1.0, "fret": 3, "width": 4}
    document = {
        "notes": [note, dict(note)],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    }
    arrangement_path.write_text(json.dumps(document), encoding="utf-8")
    preview = client.post(
        "/api/plugins/library_doctor/repair/all/preview",
        json={"package": "Artist/Song.feedpak"},
    ).json()
    changed = {**document, "author_edit": True}
    arrangement_path.write_text(json.dumps(changed), encoding="utf-8")

    response = client.post(
        "/api/plugins/library_doctor/repair/all/apply",
        json={
            "package": "Artist/Song.feedpak",
            "plan_id": preview["plan_id"],
        },
    )

    assert preview["rule_count"] == 2
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_changed"
    assert json.loads(arrangement_path.read_text(encoding="utf-8")) == changed
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_fix_all_safe_issues_does_not_partially_apply_when_a_chart_is_blocked(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["arrangements"].append({
        "id": "rhythm",
        "file": "arrangements/missing.json",
    })
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    arrangement_path = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    anchor = {"time": 1.0, "fret": 3, "width": 4}
    original = json.dumps({
        "notes": [note, dict(note)],
        "chords": [],
        "anchors": [anchor, dict(anchor)],
    }).encode("utf-8")
    arrangement_path.write_bytes(original)

    preview = client.post(
        "/api/plugins/library_doctor/repair/all/preview",
        json={"package": "Artist/Song.feedpak"},
    )
    plan = preview.json()
    response = client.post(
        "/api/plugins/library_doctor/repair/all/apply",
        json={
            "package": "Artist/Song.feedpak",
            "plan_id": plan["plan_id"],
        },
    )

    assert preview.status_code == 200
    assert plan["available"] is False
    assert plan["rule_count"] == 2
    assert plan["blockers"][0]["member_path"] == "arrangements/missing.json"
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "nothing_to_repair"
    assert arrangement_path.read_bytes() == original
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_batch_preview_and_apply_repair_each_eligible_feedpak_separately(tmp_path):
    client, library = _client(tmp_path)
    first = _valid_package(library, "First.feedpak")
    first_path = first / "arrangements" / "lead.json"
    first_note = {"t": 2.0, "s": 1, "f": 5}
    first_anchor = {"time": 1.0, "fret": 3, "width": 4}
    first_beat = {"time": 0.0, "measure": 0}
    first_section = {"name": "Intro", "time": 0.0, "number": 1}
    first_chord = {"t": 4.0, "id": 0, "notes": [{"s": 0, "f": 3}]}
    first_zero_handshape = {
        "start_time": 4.0,
        "end_time": 4.0,
        "chord_id": 0,
    }
    first_reversed_handshape = {
        "start_time": 4.0,
        "end_time": 3.75,
        "chord_id": 0,
    }
    first_path.write_text(json.dumps({
        "notes": [first_note, dict(first_note)],
        "chords": [first_chord],
        "anchors": [first_anchor, dict(first_anchor)],
        "handshapes": [first_zero_handshape, first_reversed_handshape],
        "templates": [{"frets": [3], "fingers": [1]}],
        "beats": [first_beat, dict(first_beat)],
        "sections": [first_section, dict(first_section)],
    }), encoding="utf-8")

    second = _valid_package(library, "Second.feedpak")
    second_path = second / "arrangements" / "lead.json"
    second_note = {"t": 3.0, "s": 2, "f": 7}
    second_path.write_text(json.dumps({
        "notes": [second_note, dict(second_note)],
        "chords": [],
    }), encoding="utf-8")

    blocked = _valid_package(library, "Blocked.feedpak")
    blocked_path = blocked / "arrangements" / "lead.json"
    blocked_path.write_text(json.dumps({
        "notes": [first_note, dict(first_note)],
        "chords": [],
    }), encoding="utf-8")
    blocked_manifest_path = blocked / "manifest.yaml"
    blocked_manifest = yaml.safe_load(
        blocked_manifest_path.read_text(encoding="utf-8")
    )
    blocked_manifest["arrangements"].append({
        "id": "missing",
        "file": "arrangements/missing.json",
    })
    blocked_manifest_path.write_text(
        yaml.safe_dump(blocked_manifest, sort_keys=False), encoding="utf-8"
    )

    _valid_package(library, "Healthy.feedpak")
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    started = client.post("/api/plugins/library_doctor/repair/batch/preview")
    preview_status = _wait_for_batch(client, "ready")
    preview = preview_status["preview"]

    assert started.status_code == 202
    assert preview["scope_package_count"] == 4
    assert preview["candidate_count"] == 3
    assert preview["eligible_count"] == 3
    assert preview["blocked_count"] == 0
    assert preview["no_longer_needed_count"] == 0
    assert preview["reported_affected_count"] == 8
    assert {item["package"] for item in preview["packages"]} == {
        "First.feedpak", "Second.feedpak", "Blocked.feedpak",
    }
    assert preview["blocked"] == []
    assert len(preview["batch_plan_id"]) == 64

    applied = client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    completed = _wait_for_batch(client, "completed")
    result = completed["result"]

    assert applied.status_code == 202
    assert result["planned_count"] == 3
    assert result["successful_count"] == 2
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert result["backup_count"] == 2
    assert result["removed_count"] == 7
    repaired_first = json.loads(first_path.read_text(encoding="utf-8"))
    assert len(repaired_first["notes"]) == 1
    assert len(repaired_first["anchors"]) == 1
    assert repaired_first["chords"] == [first_chord]
    assert repaired_first["handshapes"] == []
    assert repaired_first["beats"] == [first_beat]
    assert repaired_first["sections"] == [first_section]
    assert len(json.loads(second_path.read_text(encoding="utf-8"))["notes"]) == 1
    assert len(json.loads(blocked_path.read_text(encoding="utf-8"))["notes"]) == 2
    backups = list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip")
    )
    assert len(backups) == 2
    assert (tmp_path / "config" / "library_doctor" / "batch_result.json").is_file()
    refreshed = client.get("/api/plugins/library_doctor/results?filter=all").json()
    by_package = {item["package"]: item for item in refreshed["items"]}
    assert not any(
        finding["code"].startswith("chart.duplicate")
        for finding in by_package["First.feedpak"]["findings"]
    )
    assert not any(
        finding["code"] == "timeline.duplicate-beat"
        for finding in by_package["First.feedpak"]["findings"]
    )
    assert not any(
        finding["code"] == "timeline.duplicate-section"
        for finding in by_package["First.feedpak"]["findings"]
    )
    assert not any(
        finding["code"] == "chart.duplicate-note"
        for finding in by_package["Second.feedpak"]["findings"]
    )
    assert any(
        finding["code"] == "chart.duplicate-note"
        for finding in by_package["Blocked.feedpak"]["findings"]
    )

    first_outcome = next(
        item for item in result["outcomes"] if item["package"] == "First.feedpak"
    )
    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": first_outcome["package"],
            "backup_id": first_outcome["backup_id"],
        },
    )
    batch_after_restore = client.get(
        "/api/plugins/library_doctor/repair/batch/status"
    ).json()

    assert restored.status_code == 200
    restored_outcome = next(
        item for item in batch_after_restore["result"]["outcomes"]
        if item["package"] == "First.feedpak"
    )
    assert restored_outcome["outcome"] == "restored"
    assert restored_outcome["file_state"] == "restored"
    assert restored_outcome["cache_updated"] is True
    assert batch_after_restore["result"]["restored_count"] == 1
    assert batch_after_restore["result"]["currently_repaired_count"] == 1
    assert batch_after_restore["result"]["current_removed_count"] == 1
    restored_first = json.loads(first_path.read_text(encoding="utf-8"))
    assert len(restored_first["notes"]) == 2
    assert len(restored_first["anchors"]) == 2
    assert restored_first["chords"] == [first_chord]
    assert restored_first["handshapes"] == [
        first_zero_handshape,
        first_reversed_handshape,
    ]
    assert restored_first["beats"] == [first_beat, first_beat]
    assert restored_first["sections"] == [first_section, first_section]

    undo_started = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/preview"
    )
    undo_ready = _wait_for_batch(client, "undo_ready")
    undo_preview = undo_ready["undo_preview"]

    assert undo_started.status_code == 202
    assert undo_preview["candidate_count"] == 1
    assert undo_preview["eligible_count"] == 1
    assert undo_preview["blocked_count"] == 0
    assert undo_preview["already_restored_count"] == 1
    assert undo_preview["entries_to_restore"] == 1
    assert undo_preview["packages"][0]["package"] == "Second.feedpak"

    undo_applied = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/apply",
        json={"undo_plan_id": undo_preview["undo_plan_id"]},
    )
    undo_completed = _wait_for_batch(client, "undo_completed")
    undo_result = undo_completed["undo_result"]
    current_batch = undo_completed["result"]

    assert undo_applied.status_code == 202
    assert undo_result["restored_count"] == 1
    assert undo_result["skipped_count"] == 0
    assert undo_result["failed_count"] == 0
    assert undo_result["restored_entry_count"] == 1
    assert current_batch["currently_repaired_count"] == 0
    assert current_batch["restored_count"] == 2
    assert current_batch["current_removed_count"] == 0
    restored_packages = {
        item["package"]
        for item in current_batch["outcomes"]
        if item["file_state"] == "restored"
    }
    assert restored_packages == {"First.feedpak", "Second.feedpak"}
    blocked_outcome = next(
        item for item in current_batch["outcomes"]
        if item["package"] == "Blocked.feedpak"
    )
    assert blocked_outcome["outcome"] == "skipped"
    assert blocked_outcome["file_state"] == "unchanged"
    assert len(json.loads(second_path.read_text(encoding="utf-8"))["notes"]) == 2
    after_undo_results = client.get(
        "/api/plugins/library_doctor/results?filter=all"
    ).json()
    after_undo_by_package = {
        item["package"]: item for item in after_undo_results["items"]
    }
    assert any(
        finding["code"] == "chart.duplicate-note"
        for finding in after_undo_by_package["Second.feedpak"]["findings"]
    )
    nothing_left = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/preview"
    )
    assert nothing_left.status_code == 409
    assert nothing_left.json()["detail"]["code"] == "nothing_to_restore"
    client.close()


def test_batch_finalizes_every_verified_recovery_copy_without_changing_feedpaks(
    tmp_path,
):
    client, library = _client(tmp_path)
    package = _valid_package(library, "Finalize All.feedpak")
    arrangement_path = package / "arrangements" / "lead.json"
    duplicate = {"t": 3.0, "s": 2, "f": 7}
    arrangement_path.write_text(
        json.dumps({"notes": [duplicate, dict(duplicate)], "chords": []}),
        encoding="utf-8",
    )
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    client.post("/api/plugins/library_doctor/repair/batch/preview")
    preview = _wait_for_batch(client, "ready")["preview"]
    client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    completed = _wait_for_batch(client, "completed")
    repaired_bytes = arrangement_path.read_bytes()
    backup_dir = tmp_path / "config" / "library_doctor" / "repair_backups"
    assert len(list(backup_dir.glob("*.zip"))) == 1
    assert completed["result"]["undoable_count"] == 1

    reviewed = client.post(
        "/api/plugins/library_doctor/repair/batch/finalize/preview"
    )
    finalize_ready = _wait_for_batch(client, "finalize_ready")
    finalize_preview = finalize_ready["finalize_preview"]

    assert reviewed.status_code == 202
    assert finalize_preview["eligible_count"] == 1
    assert finalize_preview["blocked_count"] == 0
    assert finalize_preview["recovery_bytes_to_free"] > 0
    assert len(list(backup_dir.glob("*.zip"))) == 1
    assert arrangement_path.read_bytes() == repaired_bytes

    finalized = client.post(
        "/api/plugins/library_doctor/repair/batch/finalize/apply",
        json={"finalize_plan_id": finalize_preview["finalize_plan_id"]},
    )
    finalize_completed = _wait_for_batch(client, "finalize_completed")
    finalize_result = finalize_completed["finalize_result"]
    batch_result = finalize_completed["result"]

    assert finalized.status_code == 202
    assert finalize_result["finalized_count"] == 1
    assert finalize_result["skipped_count"] == 0
    assert finalize_result["failed_count"] == 0
    assert finalize_result["recovery_bytes_freed"] > 0
    assert list(backup_dir.glob("*.zip")) == []
    assert arrangement_path.read_bytes() == repaired_bytes
    assert batch_result["undoable_count"] == 0
    assert batch_result["finalized_count"] == 1
    assert batch_result["outcomes"][0]["outcome"] == "finalized"

    no_undo = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/preview"
    )
    assert no_undo.status_code == 409
    assert no_undo.json()["detail"]["code"] == "nothing_to_restore"
    client.close()


def test_batch_repair_counts_and_undo_include_lossless_reordering(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library, "Bends.feedpak")
    arrangement_path = package / "arrangements" / "lead.json"
    original_points = [{"t": 0.5, "v": 1.0}, {"t": 0.0, "v": 0.0}]
    original = json.dumps({
        "notes": [{
            "t": 2.0,
            "s": 1,
            "f": 5,
            "sus": 1.0,
            "bn": 1.0,
            "bnv": original_points,
        }],
        "chords": [],
    }).encode("utf-8")
    arrangement_path.write_bytes(original)
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    client.post("/api/plugins/library_doctor/repair/batch/preview")
    preview = _wait_for_batch(client, "ready")["preview"]

    assert preview["eligible_count"] == 1
    assert preview["reported_affected_count"] == 1
    assert preview["packages"][0]["reported_affected_count"] == 1
    assert preview["rule_summaries"] == [{
        "rule_code": "chart.bend-points-out-of-order",
        "title": "Bend points out of order",
        "package_count": 1,
        "finding_count": 1,
        "reported_affected_count": 1,
    }]

    client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    completed = _wait_for_batch(client, "completed")
    result = completed["result"]
    assert result["change_count"] == 1
    assert result["removed_count"] == 0
    assert result["current_change_count"] == 1
    assert result["current_removed_count"] == 0
    assert result["outcomes"][0]["change_count"] == 1
    assert json.loads(
        arrangement_path.read_text(encoding="utf-8")
    )["notes"][0]["bnv"] == [original_points[1], original_points[0]]

    client.post("/api/plugins/library_doctor/repair/batch/undo/preview")
    undo_preview = _wait_for_batch(client, "undo_ready")["undo_preview"]
    assert undo_preview["changes_to_restore"] == 1
    assert undo_preview["entries_to_restore"] == 0
    assert undo_preview["packages"][0]["change_kind"] == "combined"
    assert undo_preview["packages"][0]["change_count"] == 1

    client.post(
        "/api/plugins/library_doctor/repair/batch/undo/apply",
        json={"undo_plan_id": undo_preview["undo_plan_id"]},
    )
    undone = _wait_for_batch(client, "undo_completed")
    assert undone["undo_result"]["restored_change_count"] == 1
    assert undone["undo_result"]["restored_entry_count"] == 0
    assert undone["result"]["current_change_count"] == 0
    assert arrangement_path.read_bytes() == original
    client.close()


def test_batch_undo_excludes_a_package_changed_after_repair_and_continues(tmp_path):
    client, library = _client(tmp_path)
    first = _valid_package(library, "First.feedpak")
    second = _valid_package(library, "Second.feedpak")
    note = {"t": 2.0, "s": 1, "f": 5}
    first_path = first / "arrangements" / "lead.json"
    second_path = second / "arrangements" / "lead.json"
    original = {"notes": [note, dict(note)], "chords": []}
    first_path.write_text(json.dumps(original), encoding="utf-8")
    second_path.write_text(json.dumps(original), encoding="utf-8")
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    client.post("/api/plugins/library_doctor/repair/batch/preview")
    preview = _wait_for_batch(client, "ready")["preview"]
    client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    _wait_for_batch(client, "completed")
    changed = json.loads(first_path.read_text(encoding="utf-8"))
    changed["author_edit"] = True
    first_path.write_text(json.dumps(changed), encoding="utf-8")

    client.post("/api/plugins/library_doctor/repair/batch/undo/preview")
    undo_preview = _wait_for_batch(client, "undo_ready")["undo_preview"]

    assert undo_preview["eligible_count"] == 1
    assert undo_preview["blocked_count"] == 1
    assert undo_preview["packages"][0]["package"] == "Second.feedpak"
    assert undo_preview["blocked"][0]["package"] == "First.feedpak"
    assert undo_preview["blocked"][0]["code"] == "package_changed"

    client.post(
        "/api/plugins/library_doctor/repair/batch/undo/apply",
        json={"undo_plan_id": undo_preview["undo_plan_id"]},
    )
    completed = _wait_for_batch(client, "undo_completed")

    assert completed["undo_result"]["restored_count"] == 1
    assert completed["result"]["currently_repaired_count"] == 1
    assert completed["result"]["restored_count"] == 1
    assert json.loads(first_path.read_text(encoding="utf-8")) == changed
    assert len(json.loads(second_path.read_text(encoding="utf-8"))["notes"]) == 2
    client.close()


def test_batch_undo_requires_a_result_and_a_reviewed_plan(tmp_path):
    client, _library = _client(tmp_path)

    no_result = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/preview"
    )
    missing_plan = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/apply",
        json={},
    )
    invalid_plan = client.post(
        "/api/plugins/library_doctor/repair/batch/undo/apply",
        json={"undo_plan_id": "not-a-reviewed-plan"},
    )

    assert no_result.status_code == 409
    assert no_result.json()["detail"]["code"] == "batch_result_unavailable"
    assert missing_plan.status_code == 422
    assert missing_plan.json()["detail"]["code"] == "invalid_request"
    assert invalid_plan.status_code == 409
    assert invalid_plan.json()["detail"]["code"] == "invalid_undo_plan"
    client.close()


def test_batch_repair_skips_a_stale_package_and_continues_with_the_next(tmp_path):
    client, library = _client(tmp_path)
    first = _valid_package(library, "First.feedpak")
    second = _valid_package(library, "Second.feedpak")
    note = {"t": 2.0, "s": 1, "f": 5}
    first_path = first / "arrangements" / "lead.json"
    second_path = second / "arrangements" / "lead.json"
    original = {"notes": [note, dict(note)], "chords": []}
    first_path.write_text(json.dumps(original), encoding="utf-8")
    second_path.write_text(json.dumps(original), encoding="utf-8")
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    client.post("/api/plugins/library_doctor/repair/batch/preview")
    preview = _wait_for_batch(client, "ready")["preview"]
    changed = {**original, "author_edit": True}
    first_path.write_text(json.dumps(changed), encoding="utf-8")

    client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    result = _wait_for_batch(client, "completed")["result"]

    assert result["successful_count"] == 1
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert {item["outcome"] for item in result["outcomes"]} == {
        "success", "skipped",
    }
    skipped = next(
        item for item in result["outcomes"] if item["outcome"] == "skipped"
    )
    assert skipped["code"] == "source_changed"
    assert json.loads(first_path.read_text(encoding="utf-8")) == changed
    assert len(json.loads(second_path.read_text(encoding="utf-8"))["notes"]) == 1
    assert len(list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip")
    )) == 1
    client.close()


def test_batch_preview_pauses_for_gameplay_and_resumes(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    client.put("/api/plugins/library_doctor/playback", json={"active": True})

    started = client.post("/api/plugins/library_doctor/repair/batch/preview")
    paused = _wait_for_batch(client, "paused")
    client.put("/api/plugins/library_doctor/playback", json={"active": False})
    ready = _wait_for_batch(client, "ready")

    assert started.status_code == 202
    assert paused["running"] is True
    assert "paused while a song is open" in paused["message"]
    assert ready["preview"]["eligible_count"] == 1
    client.close()


def test_batch_preview_requires_a_complete_current_scan(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library)

    response = client.post("/api/plugins/library_doctor/repair/batch/preview")

    assert response.status_code == 409
    assert "Complete the current scan scope" in response.json()["detail"]["message"]
    client.close()


def test_note_that_duplicates_a_chord_uses_a_distinct_safe_repair(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    arrangement = {
        "notes": [{"t": 2.0, "s": 1, "f": 5, "sus": 0.5}],
        "chords": [{
            "t": 2.0,
            "id": 0,
            "notes": [
                {"s": 1, "f": 5, "sus": 0.5},
                {"s": 2, "f": 7},
            ],
        }],
        "templates": [{
            "frets": [-1, 5, 7, -1, -1, -1],
            "fingers": [-1, 1, 3, -1, -1, -1],
        }],
    }
    arrangement_path.write_text(json.dumps(arrangement), encoding="utf-8")
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    results = client.get("/api/plugins/library_doctor/results").json()
    assert any(
        finding["code"] == "chart.note-duplicates-chord"
        for finding in results["items"][0]["findings"]
    )
    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.note-duplicates-chord",
        },
    )

    assert preview.status_code == 200
    plan = preview.json()
    assert plan["available"] is True
    assert plan["item_name"] == "standalone note"
    assert plan["removed_count"] == 1
    assert plan["musical_positions"] == 1

    response = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.note-duplicates-chord",
            "plan_id": plan["plan_id"],
        },
    )

    assert response.status_code == 200
    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert repaired["notes"] == []
    assert repaired["chords"] == arrangement["chords"]
    refreshed = client.get("/api/plugins/library_doctor/results").json()
    assert not any(
        finding["code"] == "chart.note-duplicates-chord"
        for finding in refreshed["items"][0]["findings"]
    )
    client.close()


def test_exact_duplicate_chord_member_repair_refreshes_the_package_report(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement_path = package / "arrangements" / "lead.json"
    chord_member = {"s": 1, "f": 5, "sus": 0.5}
    arrangement = {
        "notes": [],
        "chords": [{
            "t": 2.0,
            "id": 0,
            "notes": [chord_member, dict(chord_member), {"s": 2, "f": 7}],
        }],
        "templates": [{
            "frets": [-1, 5, 7, -1, -1, -1],
            "fingers": [-1, 1, 3, -1, -1, -1],
        }],
    }
    arrangement_path.write_text(json.dumps(arrangement), encoding="utf-8")
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)

    before = client.get("/api/plugins/library_doctor/results").json()
    assert any(
        finding["code"] == "chart.duplicate-chord-note"
        for finding in before["items"][0]["findings"]
    )

    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-chord-note",
        },
    ).json()
    assert preview["available"] is True
    assert preview["removed_count"] == 1
    assert preview["item_name"] == "chord note"

    response = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-chord-note",
            "plan_id": preview["plan_id"],
        },
    )

    assert response.status_code == 200
    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert repaired["chords"][0]["notes"] == [
        chord_member,
        {"s": 2, "f": 7},
    ]
    refreshed = client.get("/api/plugins/library_doctor/results").json()
    assert not any(
        finding["code"] == "chart.duplicate-chord-note"
        for finding in refreshed["items"][0]["findings"]
    )
    client.close()


def test_restore_refuses_to_overwrite_chart_changes_made_after_repair(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}), encoding="utf-8"
    )
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()
    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    ).json()
    author_edit = {"notes": [note], "chords": [], "author_edit": True}
    arrangement.write_text(json.dumps(author_edit), encoding="utf-8")

    restored = client.post(
        "/api/plugins/library_doctor/repair/restore",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied["backup_id"],
        },
    )

    assert restored.status_code == 409
    assert restored.json()["detail"]["code"] == "package_changed"
    assert json.loads(arrangement.read_text(encoding="utf-8")) == author_edit
    finalized = client.post(
        "/api/plugins/library_doctor/repair/recovery/finalize",
        json={
            "package": "Artist/Song.feedpak",
            "backup_id": applied["backup_id"],
        },
    )
    assert finalized.status_code == 409
    assert finalized.json()["detail"]["code"] == "package_changed"
    assert (
        tmp_path / "config" / "library_doctor" / "repair_backups"
        / f"{applied['backup_id']}.zip"
    ).is_file()
    client.close()


def test_repair_refuses_a_stale_preview_without_changing_the_package(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    preview = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()
    changed = {"notes": [note, dict(note)], "chords": [], "author_edit": True}
    arrangement.write_text(json.dumps(changed), encoding="utf-8")

    response = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": preview["plan_id"],
        },
    )

    assert response.status_code == 409
    assert "changed after this preview" in response.json()["detail"]["message"]
    assert response.json()["detail"]["file_state"] == "unchanged"
    assert json.loads(arrangement.read_text(encoding="utf-8")) == changed
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_archive_repair_preserves_other_members_and_archive_comment(tmp_path):
    client, library = _client(tmp_path)
    staging = _valid_package(library, "staging")
    note = {"t": 3.0, "s": 2, "f": 7}
    (staging / "arrangements" / "lead.json").write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    chosen = library / "Chosen.feedpak"
    with zipfile.ZipFile(chosen, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b"preserve-me"
        for member in staging.rglob("*"):
            if member.is_file():
                archive.write(member, member.relative_to(staging).as_posix())
    original_audio = (staging / "stems" / "full.ogg").read_bytes()
    client.post(
        "/api/plugins/library_doctor/scan",
        json={"scope": "file", "path": str(chosen)},
    )
    _wait_for_scan(client)
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Chosen.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()

    applied = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Chosen.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    )

    assert applied.status_code == 200
    with zipfile.ZipFile(chosen, "r") as archive:
        repaired = json.loads(archive.read("arrangements/lead.json"))
        assert len(repaired["notes"]) == 1
        assert archive.read("stems/full.ogg") == original_audio
        assert archive.comment == b"preserve-me"
        assert len(archive.namelist()) == len(set(archive.namelist()))
    client.close()


def test_repair_is_blocked_while_playback_has_priority(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    arrangement.write_text(
        json.dumps({"notes": [note, dict(note)], "chords": []}),
        encoding="utf-8",
    )
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()
    client.put("/api/plugins/library_doctor/playback", json={"active": True})

    response = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    )

    assert response.status_code == 409
    assert "Exit the song player" in response.json()["detail"]["message"]
    assert len(json.loads(arrangement.read_text(encoding="utf-8"))["notes"]) == 2
    client.close()


def test_exact_duplicate_drum_hits_use_the_same_safe_repair_workflow(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["drum_tab"] = "drums.json"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    hit = {"t": 4.0, "p": "snare", "v": 100}
    drums_path = package / "drums.json"
    drums_path.write_text(
        json.dumps({"version": 1, "hits": [hit, dict(hit)]}), encoding="utf-8"
    )
    client.post("/api/plugins/library_doctor/scan")
    _wait_for_scan(client)
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "drums.duplicate-hit"},
    ).json()

    response = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "drums.duplicate-hit",
            "plan_id": plan["plan_id"],
        },
    )

    assert plan["available"] is True
    assert plan["item_name"] == "drum hit"
    assert response.status_code == 200
    assert len(json.loads(drums_path.read_text(encoding="utf-8"))["hits"]) == 1
    client.close()


def test_repair_preview_rejects_ambiguous_manifest_and_package_traversal(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\narrangements: []\n",
        encoding="utf-8",
    )

    ambiguous = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    )
    traversal = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "../Outside.feedpak", "rule_code": "chart.duplicate-note"},
    )

    assert ambiguous.status_code == 400
    assert "manifest cannot be read safely" in ambiguous.json()["detail"]["message"]
    assert traversal.status_code == 400
    assert str(library) not in traversal.text
    client.close()


def test_failed_candidate_validation_keeps_the_original_and_creates_no_backup(tmp_path):
    def hook(module):
        original = module.validate_feedpak

        def reject_candidate(path, *args, **kwargs):
            report = original(path, *args, **kwargs)
            if ".library-doctor-repair-" in str(path):
                report["findings"].append({
                    "severity": "error",
                    "code": "test.candidate-regression",
                    "message": "Injected candidate failure.",
                    "category": "validation",
                })
                report["status"] = "error"
            return report

        module.validate_feedpak = reject_candidate

    client, library = _client(tmp_path, validator_hook=hook)
    package = _valid_package(library)
    arrangement = package / "arrangements" / "lead.json"
    note = {"t": 2.0, "s": 1, "f": 5}
    original_document = {"notes": [note, dict(note)], "chords": []}
    arrangement.write_text(json.dumps(original_document), encoding="utf-8")
    plan = client.post(
        "/api/plugins/library_doctor/repair/preview",
        json={"package": "Artist/Song.feedpak", "rule_code": "chart.duplicate-note"},
    ).json()

    response = client.post(
        "/api/plugins/library_doctor/repair/apply",
        json={
            "package": "Artist/Song.feedpak",
            "rule_code": "chart.duplicate-note",
            "plan_id": plan["plan_id"],
        },
    )

    assert response.status_code == 409
    assert "introduced a new validation finding" in response.json()["detail"]["message"]
    assert json.loads(arrangement.read_text(encoding="utf-8")) == original_document
    assert not (tmp_path / "config" / "library_doctor" / "repair_backups").exists()
    client.close()


def test_missing_library_is_reported_in_status(tmp_path):
    client, _library = _client(tmp_path, with_library=False)

    response = client.post("/api/plugins/library_doctor/scan")

    assert response.status_code == 400
    assert "configured" in response.json()["detail"]["message"].lower()
    client.close()
