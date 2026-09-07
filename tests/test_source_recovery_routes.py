import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_source_recovery import build, load


def test_source_preview_apply_and_idempotent_replay_use_existing_receipts(tmp_path, monkeypatch):
    _, _, _, package, original, _, entries = build(tmp_path)
    archive = load("source_archive")
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    monkeypatch.setattr(archive, "read_source_charts", lambda value: {
        "path": Path(value), "sha256": digest(value), "charts": entries})
    monkeypatch.setattr(archive, "source_hash", digest)
    routes = load("routes")
    app = FastAPI()
    routes.setup(app, {"config_dir": tmp_path / "route-config",
        "get_dlc_dir": lambda: package.parent, "load_sibling": load,
        "log": logging.getLogger("source-route-tests")})
    with TestClient(app) as client:
        body = {"package": package.name, "source_path": str(original)}
        preview = client.post("/api/plugins/library_doctor/source-recovery/preview", json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()["chart_validated"]
        payload = {**body, "plan_id": preview.json()["plan_id"], "request_id": "source-test-apply-1"}
        applied = client.post("/api/plugins/library_doctor/source-recovery/apply", json=payload)
        assert applied.status_code == 200, applied.text
        assert applied.json()["applied"] and applied.json()["cache_updated"]
        replay = client.post("/api/plugins/library_doctor/source-recovery/apply", json=payload)
        assert replay.status_code == 200
        assert replay.json()["backup_id"] == applied.json()["backup_id"]
        invalid = client.post("/api/plugins/library_doctor/source-recovery/preview", json={**body, "extra": "untrusted"})
        assert invalid.status_code == 422
