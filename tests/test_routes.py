import importlib.util
import json
import logging
import sys
import threading
import time
import zipfile
from pathlib import Path

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


def _client(tmp_path, *, with_library=True, validator_hook=None):
    root = Path(__file__).parents[1]
    loaded = {}

    def load_sibling(name):
        if name not in loaded:
            loaded[name] = _load(
                root / f"{name}.py", f"library_doctor_routes_test_{name}_{id(loaded)}"
            )
            if name == "validator" and validator_hook is not None:
                validator_hook(loaded[name])
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

    assert invalid.status_code == 400
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
    assert "inside the configured song library" in response.json()["detail"]
    assert str(outside) not in response.text
    client.close()


def test_unknown_result_filter_is_rejected(tmp_path):
    client, _library = _client(tmp_path)

    response = client.get("/api/plugins/library_doctor/results?filter=unknown")

    assert response.status_code == 400
    assert "Unknown result filter" in response.json()["detail"]
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

    assert catalog["items"][0]["rule_code"] == "chart.duplicate-note"
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
        assert metadata["schema"] == "library_doctor.repair_backup.v2"
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
    assert backups[0].exists()
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


def test_fix_all_safe_issues_is_one_validated_reversible_package_transaction(tmp_path):
    client, library = _client(tmp_path)
    package = _valid_package(library)
    manifest_path = package / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["drum_tab"] = "drums.json"
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
    arrangement = {
        "notes": [note, dict(note)],
        "chords": [chord, json.loads(json.dumps(chord))],
        "anchors": [anchor, dict(anchor)],
        "handshapes": [handshape, dict(handshape)],
        "templates": [{
            "frets": [-1, 5, 7, -1, -1, -1],
            "fingers": [-1, 1, 3, -1, -1, -1],
        }],
    }
    arrangement_path = package / "arrangements" / "lead.json"
    original_arrangement = json.dumps(arrangement).encode("utf-8")
    arrangement_path.write_bytes(original_arrangement)
    hit = {"t": 4.0, "p": "snare", "v": 100}
    original_drums = json.dumps({
        "version": 1, "hits": [hit, dict(hit)]
    }).encode("utf-8")
    drums_path = package / "drums.json"
    drums_path.write_bytes(original_drums)

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
        "drums.duplicate-hit",
    ]
    assert plan["rule_count"] == 7
    assert plan["removed_count"] == 8
    assert plan["member_count"] == 2

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
    assert len(json.loads(drums_path.read_text(encoding="utf-8"))["hits"]) == 1
    backups = list(
        (tmp_path / "config" / "library_doctor" / "repair_backups").glob("*.zip")
    )
    assert len(backups) == 1
    with zipfile.ZipFile(backups[0], "r") as backup:
        metadata = json.loads(backup.read("repair.json"))
        assert metadata["rule_code"] == "package.all-safe"
        assert metadata["rule_codes"] == plan["rule_codes"]
        assert len(metadata["members"]) == 2
        assert len(metadata["summary"]["repair_summaries"]) == 7

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
    assert drums_path.read_bytes() == original_drums
    assert backups[0].exists()
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
    first_path.write_text(json.dumps({
        "notes": [first_note, dict(first_note)],
        "chords": [],
        "anchors": [first_anchor, dict(first_anchor)],
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
    assert preview["eligible_count"] == 2
    assert preview["blocked_count"] == 1
    assert preview["no_longer_needed_count"] == 0
    assert preview["removed_count"] == 3
    assert {item["package"] for item in preview["packages"]} == {
        "First.feedpak", "Second.feedpak",
    }
    assert preview["blocked"][0]["package"] == "Blocked.feedpak"
    assert len(preview["batch_plan_id"]) == 64

    applied = client.post(
        "/api/plugins/library_doctor/repair/batch/apply",
        json={"batch_plan_id": preview["batch_plan_id"]},
    )
    completed = _wait_for_batch(client, "completed")
    result = completed["result"]

    assert applied.status_code == 202
    assert result["planned_count"] == 2
    assert result["successful_count"] == 2
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0
    assert result["backup_count"] == 2
    assert result["removed_count"] == 3
    assert len(json.loads(first_path.read_text(encoding="utf-8"))["notes"]) == 1
    assert len(json.loads(first_path.read_text(encoding="utf-8"))["anchors"]) == 1
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
    assert all(
        item["file_state"] == "restored"
        for item in current_batch["outcomes"]
    )
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
    assert preview["change_count"] == 1
    assert preview["removed_count"] == 0
    assert preview["packages"][0]["change_count"] == 1
    assert preview["rule_summaries"] == [{
        "rule_code": "chart.bend-points-out-of-order",
        "title": "Put bend points in chronological order",
        "item_name": "bend curve",
        "change_kind": "reorder",
        "package_count": 1,
        "change_count": 1,
        "removed_count": 0,
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
    assert missing_plan.status_code == 400
    assert "Review Undo all" in missing_plan.json()["detail"]
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
    assert "Complete the current scan scope" in response.json()["detail"]
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
    assert "Exit the song player" in response.json()["detail"]
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
    assert "configured" in response.json()["detail"].lower()
    client.close()
