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
                root / f"{name}.py", f"library_health_routes_test_{name}_{id(loaded)}"
            )
            if name == "validator" and validator_hook is not None:
                validator_hook(loaded[name])
        return loaded[name]

    library = tmp_path / "library"
    if with_library:
        library.mkdir()
    routes = _load(root / "routes.py", f"library_health_routes_test_{id(tmp_path)}")
    app = FastAPI()
    routes.setup(app, {
        "config_dir": tmp_path / "config",
        "get_dlc_dir": lambda: library if with_library else None,
        "load_sibling": load_sibling,
        "log": logging.getLogger("library-health-routes-tests"),
    })
    return TestClient(app), library


def _wait_for_scan(client, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/plugins/library_health/status").json()
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("Library Health scan did not finish")


def test_scan_and_results_are_available_through_plugin_routes(tmp_path):
    client, library = _client(tmp_path)
    _valid_package(library)

    response = client.post("/api/plugins/library_health/scan")
    assert response.status_code == 202
    assert response.json()["started"] is True
    status = _wait_for_scan(client)
    results = client.get("/api/plugins/library_health/results").json()

    assert status["stage"] == "complete"
    assert status["summary"]["total"] == 1
    assert results["total"] == 1
    assert results["items"][0]["package"] == "Artist/Song.feedpak"
    assert str(library) not in response.text
    assert str(library) not in json.dumps(results)
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

    first = client.post("/api/plugins/library_health/scan").json()
    assert entered.wait(2)
    second = client.post("/api/plugins/library_health/scan").json()

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
        "/api/plugins/library_health/scan",
        json={"scope": "folder", "path": str(library / "ACDC")},
    )
    assert response.status_code == 202
    status = _wait_for_scan(client)
    results = client.get("/api/plugins/library_health/results").json()

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
        "/api/plugins/library_health/scan",
        json={"scope": "file", "path": str(chosen)},
    )
    assert response.status_code == 202
    status = _wait_for_scan(client)
    results = client.get("/api/plugins/library_health/results").json()

    assert status["target"] == {"kind": "file", "label": "Chosen.feedpak"}
    assert results["total"] == 1
    assert results["items"][0]["package"] == "Chosen.feedpak"
    client.close()


def test_scan_rejects_targets_outside_the_configured_library(tmp_path):
    client, _library = _client(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    response = client.post(
        "/api/plugins/library_health/scan",
        json={"scope": "folder", "path": str(outside)},
    )

    assert response.status_code == 400
    assert "inside the configured song library" in response.json()["detail"]
    assert str(outside) not in response.text
    client.close()


def test_unknown_result_filter_is_rejected(tmp_path):
    client, _library = _client(tmp_path)

    response = client.get("/api/plugins/library_health/results?filter=unknown")

    assert response.status_code == 400
    assert "Unknown result filter" in response.json()["detail"]
    client.close()


def test_missing_library_is_reported_in_status(tmp_path):
    client, _library = _client(tmp_path, with_library=False)

    response = client.post("/api/plugins/library_health/scan")

    assert response.status_code == 400
    assert "configured" in response.json()["detail"].lower()
    client.close()
