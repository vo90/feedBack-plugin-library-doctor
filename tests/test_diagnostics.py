import io
import json
import logging
import zipfile

import diagnostics
from privacy import PrivacySafeLog


def _write_private_state(config_dir):
    data_dir = config_dir / "library_doctor"
    (data_dir / "repair_backups").mkdir(parents=True)
    (data_dir / "repair_transactions").mkdir()
    private_package = "Private Artist/Unreleased Song.feedpak"
    (data_dir / "repair_history.json").write_text(
        json.dumps({
            "schema": "library_doctor.repair_history.v1",
            "items": [{"package": private_package, "outcome": "success"}],
        }),
        encoding="utf-8",
    )
    (data_dir / "batch_result.json").write_text(
        json.dumps({"packages": [{"title": "Unreleased Song"}]}),
        encoding="utf-8",
    )
    (data_dir / "repair_backups" / "Private Artist.zip").write_bytes(b"private")
    (data_dir / "repair_transactions" / "Unreleased Song.json").write_text(
        json.dumps({"package": private_package}), encoding="utf-8"
    )
    (data_dir / "library_doctor.db").write_bytes(b"")
    return private_package


def test_diagnostics_report_operational_counts_without_song_identity(tmp_path):
    private_package = _write_private_state(tmp_path)

    report = diagnostics.collect({"plugin_id": "library_doctor", "config_dir": tmp_path})
    encoded = json.dumps(report, sort_keys=True)

    assert report["schema"] == diagnostics.DIAGNOSTICS_SCHEMA
    assert report["plugin_version"] == "0.44.0"
    assert report["state"] == "present"
    assert report["scan_database_present"] is True
    assert report["history"] == {"state": "readable", "record_count": 1}
    assert report["recovery"]["backup_count"] == 1
    assert report["recovery"]["backup_count_capped"] is False
    assert report["recovery"]["pending_transaction_count"] == 1
    assert report["recovery"]["transaction_count_capped"] is False
    assert private_package not in encoded
    assert "Private Artist" not in encoded
    assert "Unreleased Song" not in encoded
    assert str(tmp_path) not in encoded


def test_bundle_shaped_export_contains_no_private_plugin_data(tmp_path):
    private_package = _write_private_state(tmp_path)
    stream = io.StringIO()
    logger = logging.getLogger("library-doctor-phase7-bundle")
    logger.handlers[:] = []
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    PrivacySafeLog(logger, salt=b"phase-7-bundle").warning(
        "Validation failed for %s at %s", private_package, tmp_path / private_package
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("logs/server.log", stream.getvalue())
        bundle.writestr(
            "plugins/library_doctor/callable.json",
            json.dumps(diagnostics.collect({"config_dir": tmp_path}), sort_keys=True),
        )
    bundle_bytes = output.getvalue()

    assert b"Private Artist" not in bundle_bytes
    assert b"Unreleased Song" not in bundle_bytes
    assert b".feedpak" not in bundle_bytes.lower()
    assert str(tmp_path).encode() not in bundle_bytes
    assert b"library_doctor.diagnostics.v1" in bundle_bytes


def test_diagnostics_degrades_safely_for_invalid_or_damaged_state(tmp_path):
    assert diagnostics.collect(None)["state"] == "unavailable"
    data_dir = tmp_path / "library_doctor"
    data_dir.mkdir()
    (data_dir / "repair_history.json").write_bytes(b"not-json")

    report = diagnostics.collect({"config_dir": tmp_path})

    assert report["state"] == "present"
    assert report["history"] == {"state": "unreadable", "record_count": 0}


def test_diagnostics_reads_state_files_with_a_hard_byte_ceiling(tmp_path):
    data_dir = tmp_path / "library_doctor"
    data_dir.mkdir()
    (data_dir / "repair_history.json").write_bytes(
        b" " * (diagnostics.MAX_STATE_FILE_BYTES + 1)
    )

    report = diagnostics.collect({"config_dir": tmp_path})

    assert report["history"] == {"state": "oversized", "record_count": 0}


def test_diagnostics_caps_recovery_directory_enumeration(monkeypatch, tmp_path):
    class Entry:
        name = "unrelated.tmp"

        @staticmethod
        def is_file(*, follow_symlinks):
            assert follow_symlinks is False
            return True

    class Entries:
        def __enter__(self):
            return (Entry() for _ in range(diagnostics.MAX_DIRECTORY_ENTRIES + 1))

        def __exit__(self, *_args):
            return False

    with monkeypatch.context() as patcher:
        patcher.setattr(diagnostics.os, "scandir", lambda _path: Entries())
        count, readable, capped = diagnostics._count_files(tmp_path, ".zip")

    assert (count, readable, capped) == (0, True, True)
