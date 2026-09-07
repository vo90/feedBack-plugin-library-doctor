"""Exercise folder batch orchestration against the real package transaction engine."""
import copy
import hashlib
import json
import logging
import shutil
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from test_source_recovery import build, legacy_chart, load, read_all


batch = load("source_recovery_batch")


class Scanner:
    def __init__(self, root):
        self.root, self.busy = root, False
        self.playing = False
        self.waiting = threading.Event()
        self.recorded = []

    def current_repair_root(self):
        return self.root

    def signature(self, package):
        path = self.root / package
        if path.is_dir():
            return hashlib.sha256(b"".join(k.encode() + v for k, v in sorted(read_all(path).items()))).hexdigest()
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def package_matches_signature(self, package, expected):
        try:
            return self.signature(package) == expected
        except OSError:
            return False

    def begin_batch_operation(self):
        if self.busy:
            return False, "A scan or repair is active."
        self.busy = True
        return True, ""

    def finish_repair(self):
        self.busy = False

    def playback_active(self):
        return self.playing

    def wait_for_playback(self, event):
        while self.playing and not event.is_set():
            self.waiting.set()
            event.wait(.01)
        return not event.is_set()

    def record_repair_result(self, package, report, **options):
        self.recorded.append(package)


def setup(tmp_path, *, archive=True):
    module, service, engine, package, original, members, entries = build(tmp_path, archive=archive)
    scanner = Scanner(package.parent)
    original_folder = tmp_path / "sources"
    original_folder.mkdir()
    source = original_folder / original.name
    original.replace(source)
    read = engine.archive.read_source_charts
    def select(value, members=None):
        result = read(value)
        if members is not None:
            result["charts"] = [row for row in result["charts"] if row["member"] in members]
        return result
    engine.archive.read_source_charts = select
    def factory():
        return load("source_folder_index").SourceFolderIndex(archive=engine, chart=engine.chart)
    manager = batch.SourceRecoveryBatchManager(config_dir=tmp_path / "batch-config", scanner=scanner,
        recovery=engine, index_factory=factory, log=logging.getLogger("batch-test"))
    return manager, scanner, engine, service, package, source, members, entries


def snapshot(scanner, *packages):
    return {"schema": "library_doctor.source_recovery_scope.v1", "target": str(scanner.root),
            "validator_version": "test", "scanned_at": 123, "scope_package_count": len(packages),
            "candidates": [{"package": package.name, "title": package.stem, "artist": "Test",
                            "scan_signature": scanner.signature(package.name)} for package in packages]}


def finish(manager, phase, timeout=10):
    manager.join(timeout)
    status = manager.status()
    assert not status["running"], status
    assert status["phase"] == phase, status
    assert not manager.scanner.busy
    return status


def preview(manager, scanner, source, *packages):
    manager.start_preview(snapshot(scanner, *packages), str(source.parent))
    return finish(manager, "ready")["preview"]


@pytest.mark.parametrize("archive", [True, False])
def test_real_package_preview_apply_durable_result_and_exact_batch_undo(tmp_path, archive):
    manager, scanner, engine, service, package, source, members, _ = setup(tmp_path, archive=archive)
    plan = preview(manager, scanner, source, package)
    assert plan["eligible_count"] == 1 and plan["change_count"] == 6
    assert plan["scope_package_count"] == 1
    assert manager.preview_details(package.name)["candidate_validated"]
    assert read_all(package) == members
    manager.start_apply(plan["batch_plan_id"])
    result = finish(manager, "completed")["result"]
    assert result["success_count"] == 1 and result["outcomes"][0]["backup_id"]
    assert read_all(package)["audio.ogg"] == members["audio.ogg"]
    # A new manager has no applyable plan, but retains batch Undo after restart.
    restored_manager = batch.SourceRecoveryBatchManager(config_dir=tmp_path / "batch-config",
        scanner=scanner, recovery=engine, index_factory=manager.index_factory, log=manager.log)
    assert restored_manager.status()["preview"] is None
    with pytest.raises(batch.SourceRecoveryBatchError):
        restored_manager.start_apply(plan["batch_plan_id"])
    restored_manager.start_undo_preview()
    undo = finish(restored_manager, "undo_ready")["undo_preview"]
    assert undo["eligible_count"] == 1
    restored_manager.start_undo_apply(undo["undo_plan_id"])
    assert finish(restored_manager, "undo_completed")["result"]["success_count"] == 1
    assert read_all(package) == members
    assert scanner.recorded == [package.name, package.name]
    with pytest.raises(batch.SourceRecoveryBatchError, match="no retained repairs"):
        restored_manager.start_undo_preview()


def test_identical_source_copies_are_one_match_and_variants_reuse_decode(tmp_path):
    manager, scanner, engine, _, package, source, _, _ = setup(tmp_path)
    shutil.copy2(source, source.with_name("Duplicate.psarc"))
    variant = package.with_name("Song (No Guitar).feedpak")
    shutil.copy2(package, variant)
    count = []
    read = engine.archive.read_source_charts
    engine.archive.read_source_charts = lambda value, **options: (count.append(str(value)), read(value, **options))[1]
    plan = preview(manager, scanner, source, package, variant)
    assert plan["eligible_count"] == 2
    assert plan["source_index"]["duplicate_archive_count"] == 1
    # The index reads the whole tiny source; exact recovery reads its selected
    # member set once for both packages and both byte-identical source paths.
    assert len(count) == 2


def test_different_source_content_with_same_exact_chart_is_ambiguous(tmp_path):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path)
    source.with_name("Other version.psarc").write_bytes(b"different original source bytes")
    plan = preview(manager, scanner, source, package)
    assert plan["blocked_count"] == 1 and plan["eligible_count"] == 0
    assert plan["packages"][0]["code"] == "source_match_ambiguous"
    assert read_all(package) == members


def test_wrong_arrangement_is_not_a_match(tmp_path):
    manager, scanner, _, _, package, source, members, entries = setup(tmp_path)
    entries[0]["song"].levels[0].notes[0].fret = 8
    plan = preview(manager, scanner, source, package)
    assert plan["eligible_count"] == 0 and plan["blocked_count"] == 1
    assert read_all(package) == members


def test_incomplete_source_index_never_claims_uniqueness(tmp_path):
    manager, scanner, engine, _, package, source, members, _ = setup(tmp_path)
    bad = source.with_name("Unreadable.psarc")
    bad.write_bytes(b"unreadable different source")
    read = engine.archive.read_source_charts
    def selected(value, **options):
        if Path(value) == bad:
            raise ValueError("source cannot be decoded")
        return read(value, **options)
    engine.archive.read_source_charts = selected
    plan = preview(manager, scanner, source, package)
    assert plan["blocked_count"] == 1 and plan["eligible_count"] == 0
    assert plan["source_errors"] and not plan["source_index"]["complete"]
    assert read_all(package) == members


@pytest.mark.parametrize("invalid_name", ["A-invalid.psarc", "Z-invalid.psarc"])
def test_matching_invalid_archive_cannot_be_ignored_for_unique_recovery(tmp_path, invalid_name):
    manager, scanner, engine, _, package, source, members, entries = setup(tmp_path)
    invalid_path = source.with_name(invalid_name)
    invalid_path.write_bytes(b"another original with invalid matching bend data")
    invalid = copy.deepcopy(entries[0])
    invalid["song"].levels[0].notes[0].bends = [NS(time=10.8, step=2), NS(time=10.2, step=0)]
    read = engine.archive.read_source_charts
    def selected(value, **options):
        result = read(value, **options)
        return {**result, "charts": [invalid]} if Path(value) == invalid_path else result
    engine.archive.read_source_charts = selected
    plan = preview(manager, scanner, source, package)
    assert plan["source_index"]["complete"] and not plan["source_errors"]
    assert plan["blocked_count"] == 1 and plan["eligible_count"] == 0
    row = plan["packages"][0]
    assert row["code"] == "source_bend_invalid"
    assert invalid_name in row["reason"] and "chronological" in row["reason"]
    assert read_all(package) == members


def test_invalid_song_is_isolated_while_audio_variants_apply_and_undo(tmp_path):
    manager, scanner, engine, _, package, source, members, entries = setup(tmp_path)
    invalid_path = source.with_name("Invalid song.psarc")
    invalid_path.write_bytes(b"another song's immutable original")
    invalid = copy.deepcopy(entries[0])
    for level in invalid["song"].levels:
        level.notes[0].fret = 8
    invalid_document = json.dumps(legacy_chart(invalid["song"])).encode()
    invalid_members = {**members, "lead.json": invalid_document, "rhythm.json": invalid_document}
    invalid_package = package.with_name("Invalid song.feedpak")
    variant = package.with_name("Song (No Guitar).feedpak")
    variant_members = {**members, "audio.ogg": b"alternate MinusMix audio"}
    for path, payload in ((invalid_package, invalid_members), (variant, variant_members)):
        with zipfile.ZipFile(path, "w") as target:
            for name, raw in payload.items():
                target.writestr(name, raw)
    invalid["song"].levels[0].notes[0].bends = [NS(time=10.8, step=2), NS(time=10.2, step=0)]
    read = engine.archive.read_source_charts
    def selected(value, **options):
        result = read(value, **options)
        return {**result, "charts": [invalid]} if Path(value) == invalid_path else result
    engine.archive.read_source_charts = selected
    plan = preview(manager, scanner, source, package, variant, invalid_package)
    assert plan["source_index"]["complete"] and plan["source_index"]["indexed_archive_count"] == 2
    assert plan["eligible_count"] == 2 and plan["blocked_count"] == 1
    assert next(row for row in plan["packages"] if row["package"] == invalid_package.name)["code"] == "source_bend_invalid"
    manager.start_apply(plan["batch_plan_id"])
    assert finish(manager, "completed")["result"]["success_count"] == 2
    assert read_all(package)["audio.ogg"] == members["audio.ogg"]
    assert read_all(variant)["audio.ogg"] == variant_members["audio.ogg"]
    assert read_all(invalid_package) == invalid_members
    manager.start_undo_preview()
    undo = finish(manager, "undo_ready")["undo_preview"]
    manager.start_undo_apply(undo["undo_plan_id"])
    assert finish(manager, "undo_completed")["result"]["success_count"] == 2
    assert read_all(package) == members and read_all(variant) == variant_members
    assert read_all(invalid_package) == invalid_members


@pytest.mark.parametrize("change", ["package", "source", "root"])
def test_changed_inputs_are_excluded_after_preview(tmp_path, change):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    if change == "package":
        with package.open("ab") as stream:
            stream.write(b"changed package bytes")
    elif change == "source":
        source.write_bytes(b"changed source content")
    else:
        scanner.root = tmp_path
    before = package.read_bytes()
    manager.start_apply(plan["batch_plan_id"])
    result = finish(manager, "completed")["result"]
    assert result["success_count"] == 0 and result["blocked_count"] == 1
    assert package.read_bytes() == before


def test_new_mutation_inputs_are_guarded_after_candidate_validation(tmp_path):
    manager, scanner, _, service, package, source, members, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    def barrier(stage, context):
        if stage == "candidate_validated":
            manager.cancel()
    service._transaction_barrier = barrier
    manager.start_apply(plan["batch_plan_id"])
    result = finish(manager, "cancelled")["result"]
    assert result["success_count"] == 0
    assert read_all(package) == members


def test_cancelled_preview_and_playback_never_produce_applyable_plan(tmp_path):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path)
    scanner.playing = True
    manager.start_preview(snapshot(scanner, package), str(source.parent))
    assert scanner.waiting.wait(2)
    assert manager.status()["phase"] == "paused"
    assert manager.cancel()
    assert finish(manager, "cancelled")["preview"] is None
    assert read_all(package) == members


def test_playback_pause_resumes_without_requiring_a_new_preview(tmp_path):
    manager, scanner, _, _, package, source, _, _ = setup(tmp_path)
    scanner.playing = True
    manager.start_preview(snapshot(scanner, package), str(source.parent))
    assert scanner.waiting.wait(2)
    scanner.playing = False
    assert finish(manager, "ready")["preview"]["eligible_count"] == 1


def test_scan_reservation_and_early_index_failure_release(tmp_path):
    manager, scanner, _, _, package, source, _, _ = setup(tmp_path)
    scanner.busy = True
    with pytest.raises(batch.SourceRecoveryBatchError, match="scan or repair"):
        manager.start_preview(snapshot(scanner, package), str(source.parent))
    scanner.busy = False
    manager.start_preview(snapshot(scanner, package), str(source.parent / "missing"))
    finish(manager, "failed")


def test_existing_curve_edits_are_excluded_not_overwritten(tmp_path):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path, archive=False)
    for member in ("lead.json", "rhythm.json"):
        document = json.loads((package / member).read_bytes())
        document["notes"][0]["bnv"] = [{"t": 0, "v": 1}, {"t": .7, "v": 2}]
        (package / member).write_text(json.dumps(document))
    before = read_all(package)
    plan = preview(manager, scanner, source, package)
    assert plan["eligible_count"] == 0 and plan["unchanged_count"] == 1
    assert plan["packages"][0]["excluded_count"] == 6
    assert read_all(package) == before


def test_successful_earlier_repair_requires_explicit_finalize_or_undo(tmp_path):
    manager, scanner, engine, _, package, source, _, _ = setup(tmp_path)
    individual = engine.preview(package.name, str(source))
    engine.apply(package.name, str(source), individual["plan_id"])
    plan = preview(manager, scanner, source, package)
    assert plan["blocked_count"] == 1
    assert "Finalize the earlier repair" in plan["packages"][0]["reason"]


def test_no_scalar_or_curve_is_unchanged_and_all_packages_remain_in_preview(tmp_path):
    manager, scanner, _, _, package, source, _, _ = setup(tmp_path, archive=False)
    for member in ("lead.json", "rhythm.json"):
        document = json.loads((package / member).read_bytes())
        for container in [document, *document["phrases"][0]["levels"]]:
            for note in container["notes"]:
                note.pop("bn", None)
                note.pop("bnv", None)
        (package / member).write_text(json.dumps(document))
    plan = preview(manager, scanner, source, package)
    assert plan["scope_package_count"] == plan["unchanged_count"] == 1
    assert len(plan["packages"]) == 1


def test_bend_potential_checks_stored_difficulties_and_chord_members():
    method = load("source_recovery").SourceRecovery._bend_potential
    assert method({"phrases": [{"levels": [{"chords": [{"notes": [{"bn": .5}]}]}]}]})
    assert method({"notes": [{"bnv": [{"t": .1, "v": 0}]}]})
    assert not method({"notes": [{"bn": 0}, {"bn": False}]})


def test_undo_preview_blocks_edited_repaired_members(tmp_path):
    manager, scanner, _, _, package, source, _, _ = setup(tmp_path, archive=False)
    plan = preview(manager, scanner, source, package)
    manager.start_apply(plan["batch_plan_id"])
    finish(manager, "completed")
    (package / "lead.json").write_text('{"user": "edited"}')
    manager.start_undo_preview()
    undo = finish(manager, "undo_ready")["undo_preview"]
    assert undo["eligible_count"] == 0 and undo["blocked_count"] == 1


def test_cancel_between_packages_retains_first_receipt_and_allows_undo(tmp_path):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path)
    second = package.with_name("Second.feedpak")
    shutil.copy2(package, second)
    plan = preview(manager, scanner, source, package, second)
    record = scanner.record_repair_result
    def stop(package, report, **options):
        record(package, report, **options)
        manager.cancel()
    scanner.record_repair_result = stop
    manager.start_apply(plan["batch_plan_id"])
    result = finish(manager, "cancelled")["result"]
    assert result["success_count"] == 1 and result["remaining_count"] == 1
    scanner.record_repair_result = record
    manager.start_undo_preview()
    undo = finish(manager, "undo_ready")["undo_preview"]
    assert undo["eligible_count"] == 1
    manager.start_undo_apply(undo["undo_plan_id"])
    finish(manager, "undo_completed")
    assert read_all(package) == read_all(second) == members


def test_interrupted_checkpoint_recovers_committed_receipt_without_reapplying(tmp_path):
    manager, scanner, engine, _, package, source, _, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    row = plan["packages"][0]
    manager._state.update(running=True, mode="apply")
    manager._new_result("apply", plan["batch_plan_id"], 1)
    active = manager._begin_mutation(row, manager._bindings[package.name]["plan_id"])
    receipt = engine.apply(package.name, str(source), manager._bindings[package.name]["plan_id"],
        request_id=active["request_id"], request_operation=active["operation"],
        request_fingerprint=active["fingerprint"])
    repaired = package.read_bytes()
    fresh = batch.SourceRecoveryBatchManager(config_dir=tmp_path / "batch-config", scanner=scanner,
        recovery=engine, index_factory=manager.index_factory, log=manager.log)
    status = fresh.status()
    assert status["phase"] == "interrupted" and not status["running"]
    assert status["last_result"]["outcomes"][0]["backup_id"] == receipt["backup_id"]
    assert status["last_result"]["success_count"] == status["done"] == 1
    assert package.read_bytes() == repaired
    fresh.start_undo_preview()
    assert finish(fresh, "undo_ready")["undo_preview"]["eligible_count"] == 1


def test_decoded_source_cache_is_bounded_and_rechecks_changed_content(tmp_path):
    _, _, engine, _, _, source, _, _ = setup(tmp_path)
    count = []
    original = engine.archive.read_source_charts
    engine.archive.read_source_charts = lambda path: (count.append(str(path)), original(path))[1]
    for number in range(3):
        path = source.with_name(f"source{number}.psarc")
        path.write_bytes(f"source content {number}".encode())
        engine.read_source_charts(path)
    assert len(engine._source_cache) == 2 and len(count) == 3
    path.write_bytes(b"changed source content")
    engine.read_source_charts(path)
    assert len(engine._source_cache) == 2 and len(count) == 4


def test_scan_scope_rechecked_under_reservation_and_ready_preview_invalidated(tmp_path):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    scanner.source_recovery_scope_matches = lambda captured: False
    with pytest.raises(batch.SourceRecoveryBatchError, match="scan scope changed"):
        manager.start_apply(plan["batch_plan_id"])
    assert not scanner.busy
    assert manager.invalidate_ready("The scan scope changed.")
    assert manager.status()["phase"] == "stale"
    assert manager.status()["preview"] is None
    assert read_all(package) == members


def test_exception_after_committed_transaction_recovers_receipt_and_undo(tmp_path):
    manager, scanner, engine, _, package, source, _, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    apply = engine.apply
    def interrupted_response(*args, **options):
        apply(*args, **options)
        raise OSError("response interrupted after commit")
    engine.apply = interrupted_response
    manager.start_apply(plan["batch_plan_id"])
    result = finish(manager, "completed")["result"]
    assert result["success_count"] == 1
    assert result["outcomes"][0]["backup_id"]
    manager.start_undo_preview()
    assert finish(manager, "undo_ready")["undo_preview"]["eligible_count"] == 1


def test_checkpoint_write_failure_after_commit_is_recovered_on_restart(tmp_path):
    manager, scanner, engine, _, package, source, _, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    save = manager._save
    def fail_after_commit():
        if manager._state["done"]:
            raise OSError("owned checkpoint disk unavailable")
        save()
    manager._save = fail_after_commit
    manager.start_apply(plan["batch_plan_id"])
    finish(manager, "failed")
    fresh = batch.SourceRecoveryBatchManager(config_dir=tmp_path / "batch-config", scanner=scanner,
        recovery=engine, index_factory=manager.index_factory, log=manager.log)
    assert fresh.status()["phase"] == "interrupted"
    assert fresh.status()["last_result"]["success_count"] == 1
    fresh.start_undo_preview()
    assert finish(fresh, "undo_ready")["undo_preview"]["eligible_count"] == 1


def test_large_expanded_sources_are_not_cached(tmp_path):
    _, _, engine, _, _, source, _, _ = setup(tmp_path)
    read = engine.archive.read_source_charts
    engine.archive.read_source_charts = lambda value: {**read(value), "expanded_bytes": 20 * 1024 * 1024}
    engine.read_source_charts(source)
    assert not engine._source_cache


def test_three_hundred_package_batch_undo_survives_bounded_service_history(tmp_path):
    manager, scanner, engine, service, package, source, members, _ = setup(tmp_path)
    packages = [package]
    for number in range(299):
        duplicate = package.with_name(f"Song {number:03}.feedpak")
        shutil.copy2(package, duplicate)
        packages.append(duplicate)
    manager.start_preview(snapshot(scanner, *packages), str(source.parent))
    plan = finish(manager, "ready", timeout=60)["preview"]
    assert plan["eligible_count"] == 300
    manager.start_apply(plan["batch_plan_id"])
    result = finish(manager, "completed", timeout=60)["result"]
    assert result["success_count"] == 300
    assert len(service._read_history()) < 300
    fresh = batch.SourceRecoveryBatchManager(config_dir=tmp_path / "batch-config", scanner=scanner,
        recovery=engine, index_factory=manager.index_factory, log=manager.log)
    fresh.start_undo_preview()
    undo = finish(fresh, "undo_ready", timeout=60)["undo_preview"]
    assert undo["eligible_count"] == 300
    fresh.start_undo_apply(undo["undo_plan_id"])
    assert finish(fresh, "undo_completed", timeout=60)["result"]["success_count"] == 300
    assert all(read_all(path) == members for path in packages)
