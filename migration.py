"""One-time compatibility migration for the Library Doctor identity.

The plugin shipped as ``library_health`` before version 0.15.0.  Keep every
reference to that retired identifier in this module so the active codebase can
use ``library_doctor`` consistently without abandoning existing scan history,
repair backups, batch receipts, or the user's disabled state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path


PLUGIN_ID = "library_doctor"
LEGACY_PLUGIN_ID = "library_health"
MAX_PLUGIN_STATE_BYTES = 1024 * 1024
LEGACY_SCHEMAS = {
    "repair_backup": frozenset({
        f"{LEGACY_PLUGIN_ID}.repair_backup.v1",
        f"{LEGACY_PLUGIN_ID}.repair_backup.v2",
    }),
    "repair_history": frozenset({f"{LEGACY_PLUGIN_ID}.repair_history.v1"}),
    "batch_result": frozenset({f"{LEGACY_PLUGIN_ID}.batch_result.v1"}),
    "package": frozenset({f"{LEGACY_PLUGIN_ID}.package.v1"}),
    "scan": frozenset({f"{LEGACY_PLUGIN_ID}.scan.v1"}),
}
CURRENT_SCHEMAS = {
    "repair_history": f"{PLUGIN_ID}.repair_history.v1",
    "batch_result": f"{PLUGIN_ID}.batch_result.v1",
    "package": f"{PLUGIN_ID}.package.v1",
    "scan": f"{PLUGIN_ID}.scan.v1",
}


class MigrationError(RuntimeError):
    """Raised when continuing could hide or overwrite existing user data."""


def _require_regular_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise MigrationError(
            f"Library Doctor cannot migrate {label} because {path} is not a "
            "regular directory. No data was changed."
        )


def _rename_database_files(data_dir: Path) -> bool:
    """Rename the SQLite database and sidecars without replacing any target."""

    changed = False
    for suffix in ("", "-wal", "-shm"):
        source = data_dir / f"{LEGACY_PLUGIN_ID}.db{suffix}"
        target = data_dir / f"{PLUGIN_ID}.db{suffix}"
        if not source.exists():
            continue
        if source.is_symlink() or not source.is_file():
            raise MigrationError(
                f"Library Doctor cannot migrate database file {source}. "
                "No existing file was overwritten."
            )
        if target.exists():
            raise MigrationError(
                f"Library Doctor found both {source.name} and {target.name}. "
                "It stopped to avoid choosing between two scan histories."
            )
        os.replace(source, target)
        changed = True
    return changed


def _write_json_atomically(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _migrate_plugin_state(config_dir: Path, log) -> bool:
    """Move the retired enable/disable entry while preserving all other keys."""

    state_path = config_dir / "plugin_state.json"
    try:
        stat = state_path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("Library Doctor could not inspect plugin state %s: %s", state_path, exc)
        return False

    if not state_path.is_file() or state_path.is_symlink():
        log.warning("Library Doctor left non-regular plugin state untouched: %s", state_path)
        return False
    if stat.st_size > MAX_PLUGIN_STATE_BYTES:
        log.warning("Library Doctor left unusually large plugin state untouched: %s", state_path)
        return False

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("Library Doctor left unreadable plugin state untouched: %s", exc)
        return False
    if not isinstance(state, dict) or LEGACY_PLUGIN_ID not in state:
        return False

    legacy_state = state.pop(LEGACY_PLUGIN_ID)
    state.setdefault(PLUGIN_ID, legacy_state)
    try:
        _write_json_atomically(state_path, state)
    except OSError as exc:
        log.warning("Library Doctor could not migrate plugin state %s: %s", state_path, exc)
        return False
    return True


def _migrate_json_schema(path: Path, schema_kind: str, log) -> bool:
    """Rewrite a small mutable receipt while leaving malformed data untouched."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("Library Doctor could not read migrated metadata %s: %s", path, exc)
        return False
    if len(raw) > 16 * 1024 * 1024:
        log.warning("Library Doctor left unusually large metadata untouched: %s", path)
        return False
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(payload, dict)
        or payload.get("schema") not in LEGACY_SCHEMAS[schema_kind]
    ):
        return False
    payload["schema"] = CURRENT_SCHEMAS[schema_kind]
    try:
        _write_json_atomically(path, payload)
    except OSError as exc:
        log.warning("Library Doctor could not update migrated metadata %s: %s", path, exc)
        return False
    return True


def _migrate_cache_schemas(data_dir: Path, log) -> bool:
    """Update identity-only fields inside the retained SQLite scan cache."""

    database = data_dir / f"{PLUGIN_ID}.db"
    if not database.is_file() or database.is_symlink():
        return False
    try:
        with database.open("rb") as handle:
            header = handle.read(16)
    except OSError as exc:
        log.warning("Library Doctor could not inspect retained cache metadata: %s", exc)
        return False
    if header != b"SQLite format 3\x00":
        log.warning(
            "Library Doctor left an unrecognized retained cache and its sidecars untouched."
        )
        return False
    changed = False
    connection = None
    try:
        connection = sqlite3.connect(database, timeout=10)
        with connection:
            reports = connection.execute(
                "SELECT package, report_json FROM reports"
            ).fetchall()
            for package, encoded in reports:
                try:
                    report = json.loads(encoded)
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(report, dict)
                    and report.get("schema") in LEGACY_SCHEMAS["package"]
                ):
                    report["schema"] = CURRENT_SCHEMAS["package"]
                    connection.execute(
                        "UPDATE reports SET report_json = ? WHERE package = ?",
                        (json.dumps(report, ensure_ascii=False, separators=(",", ":")), package),
                    )
                    changed = True
            row = connection.execute(
                "SELECT value FROM cache_state WHERE key = 'last_scan'"
            ).fetchone()
            if row is not None:
                try:
                    last_scan = json.loads(row[0])
                except (TypeError, json.JSONDecodeError):
                    last_scan = None
                if (
                    isinstance(last_scan, dict)
                    and last_scan.get("schema") in LEGACY_SCHEMAS["scan"]
                ):
                    last_scan["schema"] = CURRENT_SCHEMAS["scan"]
                    connection.execute(
                        "UPDATE cache_state SET value = ? WHERE key = 'last_scan'",
                        (json.dumps(last_scan, ensure_ascii=False, separators=(",", ":")),),
                    )
                    changed = True
    except (OSError, sqlite3.Error) as exc:
        log.warning("Library Doctor could not update retained cache metadata: %s", exc)
        return False
    finally:
        if connection is not None:
            connection.close()
    return changed


def _migrate_mutable_schemas(data_dir: Path, log) -> bool:
    changed = _migrate_json_schema(
        data_dir / "repair_history.json", "repair_history", log
    )
    changed = _migrate_json_schema(
        data_dir / "batch_result.json", "batch_result", log
    ) or changed
    return _migrate_cache_schemas(data_dir, log) or changed


def migrate_legacy_state(config_dir: Path, log) -> dict:
    """Migrate pre-0.15 state before any current service opens its files.

    The operation is restart-safe: database names are changed first inside the
    legacy directory, then the complete directory is atomically moved. If both
    old and new directories exist, the plugin fails closed instead of merging
    data or silently selecting one history.
    """

    config_dir = Path(config_dir)
    legacy_dir = config_dir / LEGACY_PLUGIN_ID
    current_dir = config_dir / PLUGIN_ID
    data_migrated = False
    database_migrated = False

    legacy_exists = legacy_dir.exists() or legacy_dir.is_symlink()
    current_exists = current_dir.exists() or current_dir.is_symlink()
    if legacy_exists and current_exists:
        raise MigrationError(
            "Library Doctor found both its previous and current data folders. "
            "It stopped before opening either one so no scan history or recovery "
            "backup could be overwritten."
        )

    if legacy_exists:
        _require_regular_directory(legacy_dir, "its previous data folder")
        database_migrated = _rename_database_files(legacy_dir)
        try:
            os.replace(legacy_dir, current_dir)
        except OSError as exc:
            raise MigrationError(
                "Library Doctor could not move its previous data folder to the "
                "new identity. No package or recovery backup was deleted."
            ) from exc
        data_migrated = True
        current_exists = True

    if current_exists:
        _require_regular_directory(current_dir, "its data folder")
        database_migrated = _rename_database_files(current_dir) or database_migrated
        _migrate_mutable_schemas(current_dir, log)

    plugin_state_migrated = _migrate_plugin_state(config_dir, log)
    if data_migrated:
        log.info(
            "Library Doctor preserved the existing scan cache, repair backups, "
            "and batch history under its new internal identity."
        )

    return {
        "data_migrated": data_migrated,
        "database_migrated": database_migrated,
        "plugin_state_migrated": plugin_state_migrated,
    }
