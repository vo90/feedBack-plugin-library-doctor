"""Small, identity-free diagnostics payload for FeedBack support bundles."""

from __future__ import annotations

import json
import os
from pathlib import Path


DIAGNOSTICS_SCHEMA = "library_doctor.diagnostics.v1"
MAX_STATE_FILE_BYTES = 512 * 1024
MAX_DIRECTORY_ENTRIES = 10_000


def _bounded_json(path: Path) -> tuple[dict | None, str]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_STATE_FILE_BYTES + 1)
        if len(raw) > MAX_STATE_FILE_BYTES:
            return None, "oversized"
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unreadable"
    return (payload, "readable") if isinstance(payload, dict) else (None, "unreadable")


def _count_files(path: Path, suffix: str) -> tuple[int, bool, bool]:
    count = 0
    try:
        with os.scandir(path) as entries:
            for scanned, entry in enumerate(entries):
                if scanned >= MAX_DIRECTORY_ENTRIES:
                    return count, True, True
                if entry.name.endswith(suffix) and entry.is_file(follow_symlinks=False):
                    count += 1
    except FileNotFoundError:
        return 0, True, False
    except OSError:
        return 0, False, False
    return count, True, False


def _plugin_version() -> str:
    payload, state = _bounded_json(Path(__file__).with_name("plugin.json"))
    version = payload.get("version") if state == "readable" and payload else None
    return version if isinstance(version, str) and version else "unknown"


def _history_summary(data_dir: Path) -> dict:
    payload, state = _bounded_json(data_dir / "repair_history.json")
    items = payload.get("items") if payload else None
    return {
        "state": state,
        "record_count": min(len(items), 10_000) if isinstance(items, list) else 0,
    }


def collect(ctx: dict) -> dict:
    """Return bounded operational facts without song, package, path, or error text."""
    result = {
        "schema": DIAGNOSTICS_SCHEMA,
        "plugin_version": _plugin_version(),
        "state": "unavailable",
        "scan_database_present": False,
        "history": {"state": "missing", "record_count": 0},
        "recovery": {
            "backup_count": 0,
            "backup_directory_readable": True,
            "backup_count_capped": False,
            "pending_transaction_count": 0,
            "transaction_directory_readable": True,
            "transaction_count_capped": False,
        },
        "batch": {
            "result_state": "missing",
            "checkpoint_state": "missing",
        },
    }
    try:
        config_dir = ctx.get("config_dir") if isinstance(ctx, dict) else None
        if not isinstance(config_dir, (str, os.PathLike)):
            return result
        data_dir = Path(config_dir) / "library_doctor"
        result["state"] = "present" if data_dir.is_dir() else "missing"
        result["scan_database_present"] = (data_dir / "library_doctor.db").is_file()
        result["history"] = _history_summary(data_dir)
        backup_count, backup_readable, backup_capped = _count_files(
            data_dir / "repair_backups", ".zip"
        )
        transaction_count, transaction_readable, transaction_capped = _count_files(
            data_dir / "repair_transactions", ".json"
        )
        result["recovery"] = {
            "backup_count": backup_count,
            "backup_directory_readable": backup_readable,
            "backup_count_capped": backup_capped,
            "pending_transaction_count": transaction_count,
            "transaction_directory_readable": transaction_readable,
            "transaction_count_capped": transaction_capped,
        }
        _batch_result, batch_result_state = _bounded_json(data_dir / "batch_result.json")
        _batch_checkpoint, checkpoint_state = _bounded_json(
            data_dir / "batch_checkpoint.json"
        )
        result["batch"] = {
            "result_state": batch_result_state,
            "checkpoint_state": checkpoint_state,
        }
    except (OSError, TypeError, ValueError):
        # The host also isolates callable failures, but this boundary deliberately
        # produces a useful, identity-free payload even for damaged local state.
        result["state"] = "unavailable"
    return result
