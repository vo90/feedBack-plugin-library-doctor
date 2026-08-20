"""Same-volume repair candidate workspaces that never look like song packages."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


WORKSPACE_PREFIX = ".library-doctor-work-"
LEGACY_WORKSPACE_PREFIX = ".library-doctor-repair-"
WORKSPACE_PREFIXES = (WORKSPACE_PREFIX, LEGACY_WORKSPACE_PREFIX)
WORKSPACE_SCHEMA = "library_doctor.repair_workspace.v1"
RECEIPT_SCHEMA = "library_doctor.repair_workspace_receipt.v1"
MARKER_NAME = "workspace.json"
CANDIDATE_NAME = "candidate"
MAX_WORKSPACE_STATE_BYTES = 16 * 1024
MAX_WORKSPACE_RECEIPTS = 256
STALE_WORKSPACE_SECONDS = 7 * 24 * 60 * 60


class WorkspaceError(RuntimeError):
    """A candidate workspace could not be created or reconciled safely."""


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(
            getattr(os.path, "isjunction", lambda _path: False)(path)
        )
    except OSError:
        return True


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: dict) -> None:
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    if len(raw) > MAX_WORKSPACE_STATE_BYTES:
        raise WorkspaceError("The repair workspace record is too large.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _bounded_json(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_WORKSPACE_STATE_BYTES + 1)
        if len(raw) > MAX_WORKSPACE_STATE_BYTES:
            raise WorkspaceError("The repair workspace record is too large.")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("The repair workspace record is unreadable.") from exc
    if not isinstance(payload, dict):
        raise WorkspaceError("The repair workspace record is invalid.")
    return payload


def _registry_dir(config_dir: Path) -> Path:
    return Path(config_dir) / "library_doctor" / "repair_workspaces"


def _receipt_path(config_dir: Path, workspace_id: str) -> Path:
    return _registry_dir(config_dir) / f"{workspace_id}.json"


def _remove_receipt(config_dir: Path, workspace_id: str) -> None:
    receipt = _receipt_path(config_dir, workspace_id)
    receipt.unlink(missing_ok=True)
    if receipt.parent.is_dir():
        _fsync_directory(receipt.parent)


def _workspace_marker(root: Path) -> dict:
    marker_path = root / MARKER_NAME
    if _is_link_or_junction(root) or _is_link_or_junction(marker_path):
        raise WorkspaceError("The repair workspace contains an unsafe link.")
    marker = _bounded_json(marker_path)
    workspace_id = root.name[len(WORKSPACE_PREFIX):]
    if (
        not workspace_id
        or marker.get("schema") != WORKSPACE_SCHEMA
        or marker.get("workspace_id") != workspace_id
        or marker.get("candidate_name") != CANDIDATE_NAME
    ):
        raise WorkspaceError("The repair workspace marker does not match its directory.")
    return marker


def remove_owned_workspace(root: Path, *, expected_id: str | None = None) -> bool:
    """Remove one conclusively owned workspace without following external links."""
    root = Path(root)
    if not root.name.startswith(WORKSPACE_PREFIX) or not root.is_dir():
        return False
    if _is_link_or_junction(root):
        return False
    try:
        marker = _workspace_marker(root)
        workspace_id = marker["workspace_id"]
        if expected_id is not None and workspace_id != expected_id:
            return False
        allowed = {MARKER_NAME, CANDIDATE_NAME}
        entries = list(root.iterdir())
        if any(entry.name not in allowed for entry in entries):
            return False
        candidate = root / CANDIDATE_NAME
        if candidate.exists() and _is_link_or_junction(candidate):
            return False
        shutil.rmtree(root)
        return not root.exists()
    except (OSError, WorkspaceError):
        return False


@dataclass
class CandidateWorkspace:
    root: Path
    candidate: Path
    workspace_id: str
    config_dir: Path
    _closed: bool = False

    def cleanup(self) -> None:
        if self._closed:
            return
        removed = remove_owned_workspace(self.root, expected_id=self.workspace_id)
        if removed or not self.root.exists():
            _remove_receipt(self.config_dir, self.workspace_id)
        self._closed = True


def create_candidate_workspace(
    *, config_dir: Path, package_path: Path
) -> CandidateWorkspace:
    """Create a registered candidate beside the package without a package suffix."""
    package_path = Path(package_path)
    parent = package_path.parent
    try:
        parent = parent.resolve(strict=True)
        if not parent.is_dir() or _is_link_or_junction(parent):
            raise WorkspaceError("The package parent is not a safe directory.")
        root = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=parent))
    except (OSError, WorkspaceError) as exc:
        raise WorkspaceError("A repair workspace could not be created.") from exc

    workspace_id = root.name[len(WORKSPACE_PREFIX):]
    created_at = time.time()
    marker = {
        "schema": WORKSPACE_SCHEMA,
        "workspace_id": workspace_id,
        "candidate_name": CANDIDATE_NAME,
        "package_name": package_path.name,
        "created_at": created_at,
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "workspace_id": workspace_id,
        "workspace": str(root),
        "created_at": created_at,
    }
    try:
        _atomic_json(root / MARKER_NAME, marker)
        _atomic_json(_receipt_path(config_dir, workspace_id), receipt)
    except (OSError, WorkspaceError) as exc:
        shutil.rmtree(root, ignore_errors=True)
        _remove_receipt(config_dir, workspace_id)
        raise WorkspaceError("A repair workspace could not be registered safely.") from exc
    return CandidateWorkspace(
        root=root,
        candidate=root / CANDIDATE_NAME,
        workspace_id=workspace_id,
        config_dir=Path(config_dir),
    )


def reconcile_stale_workspaces(
    config_dir: Path,
    *,
    now: float | None = None,
    stale_after_seconds: float = STALE_WORKSPACE_SECONDS,
) -> dict[str, int | bool]:
    """Bound and reconcile old registered workspaces without exposing identities."""
    registry = _registry_dir(config_dir)
    current_time = time.time() if now is None else float(now)
    result: dict[str, int | bool] = {
        "pending": 0,
        "removed": 0,
        "unreadable": 0,
        "capped": False,
    }
    try:
        receipts = sorted(registry.glob("*.json"))
    except OSError:
        result["unreadable"] = 1
        return result
    if len(receipts) > MAX_WORKSPACE_RECEIPTS:
        result["capped"] = True
        receipts = receipts[:MAX_WORKSPACE_RECEIPTS]
    for receipt_path in receipts:
        try:
            receipt = _bounded_json(receipt_path)
            workspace_id = receipt.get("workspace_id")
            root_value = receipt.get("workspace")
            created_at = receipt.get("created_at")
            if (
                receipt.get("schema") != RECEIPT_SCHEMA
                or not isinstance(workspace_id, str)
                or not workspace_id
                or not isinstance(root_value, str)
                or not isinstance(created_at, (int, float))
            ):
                raise WorkspaceError("The repair workspace receipt is invalid.")
            root = Path(root_value)
            if root.name != f"{WORKSPACE_PREFIX}{workspace_id}":
                raise WorkspaceError("The repair workspace receipt path is invalid.")
            if not root.exists():
                receipt_path.unlink(missing_ok=True)
                continue
            age = current_time - float(created_at)
            if age >= stale_after_seconds and remove_owned_workspace(
                root, expected_id=workspace_id
            ):
                receipt_path.unlink(missing_ok=True)
                result["removed"] = int(result["removed"]) + 1
            else:
                result["pending"] = int(result["pending"]) + 1
        except (OSError, WorkspaceError, ValueError, OverflowError):
            result["unreadable"] = int(result["unreadable"]) + 1
    return result
