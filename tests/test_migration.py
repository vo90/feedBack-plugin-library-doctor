import importlib.util
import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def migration():
    path = Path(__file__).parents[1] / "migration.py"
    name = "library_doctor_migration_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _logger():
    return logging.getLogger("library-doctor-migration-tests")


def test_migrates_all_existing_data_and_disabled_state(migration, tmp_path):
    config = tmp_path / "config"
    legacy = config / migration.LEGACY_PLUGIN_ID
    legacy.mkdir(parents=True)
    original_files = {
        "library_health.db": b"database",
        "library_health.db-wal": b"write-ahead log",
        "library_health.db-shm": b"shared memory",
        "repair_history.json": b"history",
        "batch_result.json": b"batch",
        "repair_backups/backup.zip": b"recovery",
    }
    for relative, content in original_files.items():
        path = legacy / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    state_path = config / "plugin_state.json"
    state_path.write_text(
        json.dumps({
            "library_health": {"enabled": False},
            "another_plugin": {"enabled": False},
        }),
        encoding="utf-8",
    )

    result = migration.migrate_legacy_state(config, _logger())

    current = config / migration.PLUGIN_ID
    assert result == {
        "data_migrated": True,
        "database_migrated": True,
        "plugin_state_migrated": True,
    }
    assert not legacy.exists()
    assert (current / "library_doctor.db").read_bytes() == b"database"
    assert (current / "library_doctor.db-wal").read_bytes() == b"write-ahead log"
    assert (current / "library_doctor.db-shm").read_bytes() == b"shared memory"
    assert (current / "repair_history.json").read_bytes() == b"history"
    assert (current / "batch_result.json").read_bytes() == b"batch"
    assert (current / "repair_backups" / "backup.zip").read_bytes() == b"recovery"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "library_health" not in state
    assert state["library_doctor"] == {"enabled": False}
    assert state["another_plugin"] == {"enabled": False}


def test_migration_is_idempotent_after_success(migration, tmp_path):
    current = tmp_path / "config" / migration.PLUGIN_ID
    current.mkdir(parents=True)
    (current / "library_doctor.db").write_bytes(b"database")

    result = migration.migrate_legacy_state(tmp_path / "config", _logger())

    assert result == {
        "data_migrated": False,
        "database_migrated": False,
        "plugin_state_migrated": False,
    }
    assert (current / "library_doctor.db").read_bytes() == b"database"


def test_resumes_database_rename_after_interrupted_directory_move(migration, tmp_path):
    current = tmp_path / "config" / migration.PLUGIN_ID
    current.mkdir(parents=True)
    (current / "library_health.db").write_bytes(b"database")

    result = migration.migrate_legacy_state(tmp_path / "config", _logger())

    assert result["data_migrated"] is False
    assert result["database_migrated"] is True
    assert not (current / "library_health.db").exists()
    assert (current / "library_doctor.db").read_bytes() == b"database"


def test_both_data_directories_fail_closed_without_changes(migration, tmp_path):
    config = tmp_path / "config"
    legacy = config / migration.LEGACY_PLUGIN_ID
    current = config / migration.PLUGIN_ID
    legacy.mkdir(parents=True)
    current.mkdir()
    (legacy / "marker").write_text("old", encoding="utf-8")
    (current / "marker").write_text("new", encoding="utf-8")

    with pytest.raises(migration.MigrationError, match="both"):
        migration.migrate_legacy_state(config, _logger())

    assert (legacy / "marker").read_text(encoding="utf-8") == "old"
    assert (current / "marker").read_text(encoding="utf-8") == "new"


def test_database_name_collision_fails_before_directory_move(migration, tmp_path):
    config = tmp_path / "config"
    legacy = config / migration.LEGACY_PLUGIN_ID
    legacy.mkdir(parents=True)
    (legacy / "library_health.db").write_bytes(b"old")
    (legacy / "library_doctor.db").write_bytes(b"new")

    with pytest.raises(migration.MigrationError, match="both"):
        migration.migrate_legacy_state(config, _logger())

    assert legacy.is_dir()
    assert (legacy / "library_health.db").read_bytes() == b"old"
    assert (legacy / "library_doctor.db").read_bytes() == b"new"


def test_existing_current_plugin_state_wins_and_retired_key_is_removed(
    migration, tmp_path
):
    config = tmp_path / "config"
    config.mkdir()
    state_path = config / "plugin_state.json"
    state_path.write_text(
        json.dumps({
            "library_health": {"enabled": False},
            "library_doctor": {"enabled": True},
        }),
        encoding="utf-8",
    )

    result = migration.migrate_legacy_state(config, _logger())

    assert result["plugin_state_migrated"] is True
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "library_doctor": {"enabled": True}
    }


def test_invalid_plugin_state_is_left_byte_for_byte_unchanged(migration, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    state_path = config / "plugin_state.json"
    original = b"{invalid json"
    state_path.write_bytes(original)

    result = migration.migrate_legacy_state(config, _logger())

    assert result["plugin_state_migrated"] is False
    assert state_path.read_bytes() == original


def test_mutable_metadata_schemas_are_updated_after_the_move(migration, tmp_path):
    config = tmp_path / "config"
    current = config / migration.PLUGIN_ID
    current.mkdir(parents=True)
    (current / "repair_history.json").write_text(
        json.dumps({
            "schema": "library_health.repair_history.v1",
            "items": [{"id": "kept"}],
        }),
        encoding="utf-8",
    )
    (current / "batch_result.json").write_text(
        json.dumps({
            "schema": "library_health.batch_result.v1",
            "outcomes": [],
        }),
        encoding="utf-8",
    )
    database = current / "library_doctor.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE reports (package TEXT PRIMARY KEY, report_json TEXT)"
        )
        connection.execute(
            "CREATE TABLE cache_state (key TEXT PRIMARY KEY, value TEXT)"
        )
        connection.execute(
            "INSERT INTO reports VALUES (?, ?)",
            ("Song.feedpak", json.dumps({"schema": "library_health.package.v1"})),
        )
        connection.execute(
            "INSERT INTO cache_state VALUES (?, ?)",
            ("last_scan", json.dumps({"schema": "library_health.scan.v1"})),
        )

    migration.migrate_legacy_state(config, _logger())

    assert json.loads(
        (current / "repair_history.json").read_text(encoding="utf-8")
    )["schema"] == "library_doctor.repair_history.v1"
    assert json.loads(
        (current / "batch_result.json").read_text(encoding="utf-8")
    )["schema"] == "library_doctor.batch_result.v1"
    with sqlite3.connect(database) as connection:
        report = json.loads(connection.execute(
            "SELECT report_json FROM reports"
        ).fetchone()[0])
        last_scan = json.loads(connection.execute(
            "SELECT value FROM cache_state WHERE key = 'last_scan'"
        ).fetchone()[0])
    assert report["schema"] == "library_doctor.package.v1"
    assert last_scan["schema"] == "library_doctor.scan.v1"
