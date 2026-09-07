import pytest

from test_scanner import _make_scanner, _report, _run, scanner_module as scanner_module


def test_source_recovery_scope_includes_unflagged_songs_and_bound_signatures(
    scanner_module, tmp_path,
):
    scanner, library = _make_scanner(
        scanner_module, tmp_path,
        lambda _path, name: _report(name, status="healthy"),
    )
    (library / "Quiet.feedpak").write_bytes(b"no scan findings")
    _run(scanner)

    snapshot = scanner.source_recovery_scope_snapshot()

    assert snapshot["schema"] == "library_doctor.source_recovery_scope.v1"
    assert snapshot["scope_package_count"] == 1
    assert snapshot["validator_version"] == "test-v1"
    assert snapshot["target"]["kind"] == "library"
    assert snapshot["scanned_at"] is not None
    row, = snapshot["candidates"]
    assert row["package"] == "Quiet.feedpak"
    assert row["title"] == "Quiet"
    assert scanner.package_matches_signature(row["package"], row["scan_signature"])
    assert scanner.source_recovery_scope_matches(snapshot)
    (library / "Quiet.feedpak").write_bytes(b"later edited chart")
    assert not scanner.package_matches_signature(row["package"], row["scan_signature"])


def test_source_recovery_scope_excludes_old_cached_scan_targets(scanner_module, tmp_path):
    scanner, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, name: _report(name),
    )
    chosen = library / "Selected"
    chosen.mkdir()
    (chosen / "Inside.feedpak").write_bytes(b"inside")
    (library / "Outside.feedpak").write_bytes(b"outside")
    _run(scanner)
    assert scanner.source_recovery_scope_snapshot()["scope_package_count"] == 2
    _run(scanner, target_kind="folder", selected_path=str(chosen))

    snapshot = scanner.source_recovery_scope_snapshot()

    assert snapshot["scope_package_count"] == 1
    assert [row["package"] for row in snapshot["candidates"]] == ["Selected/Inside.feedpak"]


def test_source_recovery_scope_requires_complete_current_scan(scanner_module, tmp_path):
    scanner, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, name: _report(name),
    )
    with pytest.raises(ValueError, match="[Ss]can"):
        scanner.source_recovery_scope_snapshot()
    package = library / "Song.feedpak"
    package.write_bytes(b"song")
    scanner._discover_target = lambda *_args, **_kwargs: ([package], ["Unreadable folder"])
    _run(scanner)
    with pytest.raises(ValueError, match="Complete"):
        scanner.source_recovery_scope_snapshot()


def test_source_recovery_scope_rejects_busy_or_outdated_scan(scanner_module, tmp_path):
    scanner, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, name: _report(name),
    )
    (library / "Song.feedpak").write_bytes(b"song")
    _run(scanner)
    assert scanner.begin_batch_operation()[0]
    with pytest.raises(ValueError, match="Wait"):
        scanner.source_recovery_scope_snapshot()
    scanner.finish_repair()
    scanner._validator_version = "new-validator"
    with pytest.raises(ValueError, match="current Library Doctor scan"):
        scanner.source_recovery_scope_snapshot()


def test_source_scope_provenance_is_checked_under_reservation(scanner_module, tmp_path):
    scanner, library = _make_scanner(
        scanner_module, tmp_path, lambda _path, name: _report(name),
    )
    package = library / "Song.feedpak"
    package.write_bytes(b"song")
    _run(scanner)
    old = scanner.source_recovery_scope_snapshot()
    assert scanner.begin_batch_operation()[0]
    assert scanner.source_recovery_scope_matches(old)
    scanner.finish_repair()
    _run(scanner, target_kind="file", selected_path=str(package))
    assert not scanner.source_recovery_scope_matches(old)
