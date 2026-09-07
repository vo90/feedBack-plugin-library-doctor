import hashlib
import logging
import shutil
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_source_recovery import build, load, read_all


BASE = "/api/plugins/library_doctor"
BATCH = BASE + "/source-recovery/batch"


@pytest.fixture
def batch_client(tmp_path, monkeypatch):
    _, _, _, package, original, members, entries = build(tmp_path)
    duplicate = package.with_name("Song-NoGuitar.feedpak")
    shutil.copyfile(package, duplicate)
    sources = tmp_path / "source-folder"
    sources.mkdir()
    original.rename(sources / original.name)
    archive = load("source_archive")
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    monkeypatch.setattr(archive, "read_source_charts", lambda value, **_kwargs: {
        "path": Path(value), "sha256": digest(value), "charts": entries,
    })
    def inspect(value, on_chart):
        for entry in entries:
            if on_chart(entry) is False:
                return {"path": Path(value), "sha256": digest(value), "complete": False, "cancelled": True}
        return {"path": Path(value), "sha256": digest(value), "complete": True,
                "chart_count": len(entries), "expanded_bytes": 0}
    monkeypatch.setattr(archive, "inspect_source_charts", inspect)
    monkeypatch.setattr(archive, "source_hash", digest)
    app = FastAPI()
    load("routes").setup(app, {
        "config_dir": tmp_path / "route-config",
        "get_dlc_dir": lambda: package.parent,
        "load_sibling": load,
        "log": logging.getLogger("source-batch-route-tests"),
    })
    with TestClient(app) as client:
        yield client, sources, (package, duplicate), members


def wait_idle(client, path, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        status = response.json()
        if not status["running"]:
            return status
        time.sleep(.02)
    pytest.fail(f"Background operation did not finish: {status}")


def scan(client):
    response = client.post(BASE + "/scan", json={"scope": "library"})
    assert response.status_code == 202, response.text
    status = wait_idle(client, BASE + "/status")
    assert status["last_scan"]["complete"]


def test_batch_api_preview_apply_and_undo_preserve_both_package_versions(batch_client):
    client, sources, packages, members = batch_client
    scan(client)
    response = client.post(BATCH + "/preview", json={"source_folder": str(sources)})
    assert response.status_code == 202, response.text
    status = wait_idle(client, BATCH + "/status")
    preview = status["preview"]
    assert preview["scope_package_count"] == 2
    assert preview["eligible_count"] == 2, status
    assert all(read_all(package) == members for package in packages)


    details = client.get(BATCH + "/details", params={"package": packages[0].name})
    assert details.status_code == 200, details.text
    assert details.json()["change_count"] == 6

    response = client.post(BATCH + "/apply", json={"batch_plan_id": preview["batch_plan_id"]})
    assert response.status_code == 202, response.text
    status = wait_idle(client, BATCH + "/status")
    result = status["result"]
    assert result["success_count"] == 2, status
    for package in packages:
        after = read_all(package)
        assert after["audio.ogg"] == members["audio.ogg"]
        assert after["manifest.yaml"] == members["manifest.yaml"]
        assert after["lead.json"] != members["lead.json"]
    assert client.post(BATCH + "/apply", json={
        "batch_plan_id": preview["batch_plan_id"],
    }).status_code == 409

    assert client.post(BATCH + "/undo/preview", json={}).status_code == 202
    undo_status = wait_idle(client, BATCH + "/status")
    undo = undo_status["undo_preview"]
    assert undo["eligible_count"] == 2, undo_status
    response = client.post(BATCH + "/undo/apply", json={"undo_plan_id": undo["undo_plan_id"]})
    assert response.status_code == 202, response.text
    wait_idle(client, BATCH + "/status")
    assert all(read_all(package) == members for package in packages)


def test_new_scan_invalidates_previously_reviewed_source_scope(batch_client):
    client, sources, packages, members = batch_client
    scan(client)
    assert client.post(BATCH + "/preview", json={"source_folder": str(sources)}).status_code == 202
    preview = wait_idle(client, BATCH + "/status")["preview"]
    assert preview["eligible_count"] == 2
    response = client.post(BASE + "/scan", json={"scope": "file", "path": str(packages[0])})
    assert response.json()["started"]
    wait_idle(client, BASE + "/status")
    assert client.post(BATCH + "/apply", json={"batch_plan_id": preview["batch_plan_id"]}).status_code == 409
    assert all(read_all(package) == members for package in packages)


def test_batch_api_rejects_forged_scope_and_missing_scan(batch_client):
    client, sources, packages, members = batch_client
    response = client.post(BATCH + "/preview", json={"source_folder": str(sources)})
    assert response.status_code == 409
    for path, payload in (
        ("/preview", {"source_folder": str(sources), "packages": ["../../unscanned.feedpak"]}),
        ("/apply", {"batch_plan_id": "forged", "source_folder": str(sources)}),
        ("/undo/apply", {"undo_plan_id": "forged", "backup_id": "unreviewed"}),
    ):
        response = client.post(BATCH + path, json=payload)
        assert response.status_code == 422, response.text
    assert client.post(BATCH + "/apply", json={"batch_plan_id": "forged"}).status_code == 409
    assert client.post(BATCH + "/undo/apply", json={"undo_plan_id": "forged"}).status_code == 409
    assert all(read_all(package) == members for package in packages)


def test_source_batch_reserves_scan_and_other_repairs_while_paused(batch_client):
    client, sources, packages, members = batch_client
    scan(client)
    assert client.put(BASE + "/playback", json={"active": True}).status_code == 200
    response = client.post(BATCH + "/preview", json={"source_folder": str(sources)})
    assert response.status_code == 202, response.text
    assert client.get(BATCH + "/status").json()["running"]
    assert client.post(BASE + "/scan", json={"scope": "library"}).json()["started"] is False
    assert client.post(BASE + "/repair/batch/preview", json={}).status_code == 409
    assert client.post(BATCH + "/cancel", json={}).status_code == 202
    client.put(BASE + "/playback", json={"active": False})
    wait_idle(client, BATCH + "/status")
    assert not client.get(BASE + "/status").json()["repairing"]
    assert all(read_all(package) == members for package in packages)
