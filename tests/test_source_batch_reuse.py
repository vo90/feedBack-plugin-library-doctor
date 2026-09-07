"""Saved reviews retain evidence; imported source choices require fresh review."""
import copy
import json
import shutil

import pytest

from test_source_recovery import load, read_all
from test_source_recovery_batch import batch, finish, preview, setup, snapshot


def restored(manager, engine, scanner):
    return batch.SourceRecoveryBatchManager(config_dir=manager._path.parents[1],
        scanner=scanner, recovery=engine, index_factory=manager.index_factory, log=manager.log)


def test_ready_review_survives_restart_without_source_search_or_candidate(tmp_path):
    manager, scanner, engine, service, package, source, members, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    engine.archive.read_source_charts = lambda *_a, **_k: pytest.fail("Restart must not decode sources")
    service._candidate = lambda *_a, **_k: pytest.fail("Restart must not rebuild packages")
    fresh = restored(manager, engine, scanner)
    assert fresh.status()["phase"] == "ready"
    assert fresh.status()["preview"] == plan
    assert not fresh.status()["running"]
    assert read_all(package) == members


@pytest.mark.parametrize("changed", ["source", "package", "scan"])
def test_restored_preview_never_applies_changed_inputs(tmp_path, changed):
    manager, scanner, engine, _, package, source, members, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    if changed == "source":
        source.write_bytes(b"different original source")
    elif changed == "package":
        with package.open("ab") as stream:
            stream.write(b"changed package identity")
    else:
        scanner.source_recovery_scope_matches = lambda _: False
    fresh = restored(manager, engine, scanner)
    if changed == "scan":
        assert fresh.status()["phase"] == "stale"
        with pytest.raises(batch.SourceRecoveryBatchError):
            fresh.start_apply(plan["batch_plan_id"])
    else:
        fresh.start_apply(plan["batch_plan_id"])
        result = finish(fresh, "completed")["result"]
        assert result["success_count"] == 0 and result["blocked_count"] == 1
    assert read_all(package) == members


def test_corrupt_ready_review_preserves_existing_result_and_invalidation_is_durable(tmp_path):
    manager, scanner, engine, _, package, source, _, _ = setup(tmp_path)
    preview(manager, scanner, source, package)
    payload = json.loads(manager._path.read_text())
    payload["last_result"] = {"outcomes": [], "test": "retained"}
    payload["ready_preview"]["digest"] = "0" * 64
    manager._path.write_text(json.dumps(payload))
    fresh = restored(manager, engine, scanner)
    assert fresh.status()["phase"] == "stale" and fresh._storage_error is None
    assert fresh.status()["last_result"] == payload["last_result"]
    preview(fresh, scanner, source, package)
    assert fresh.invalidate_ready("New scan selected")
    assert restored(fresh, engine, scanner).status()["preview"] is None


def test_reused_legacy_source_choice_skips_folder_walk_and_recomputes_review(tmp_path, monkeypatch):
    manager, scanner, engine, _, package, source, members, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    report = manager.status()
    # Legacy public reports do not have private bindings or the new metadata.
    report["preview"].pop("provenance")
    report["preview"].pop("validation_scope")
    report["preview"]["source_index"].pop("scope")
    report["preview"]["batch_plan_id"] = "untrusted old review identity"
    source.with_name("Unrelated.psarc").write_bytes(b"must not be discovered")
    index = load("source_folder_index")
    monkeypatch.setattr(index.os, "scandir", lambda *_: pytest.fail("Do not walk the source folder again"))
    calls = []
    old_preview = engine.preview
    engine.preview = lambda *args, **kwargs: (calls.append(args), old_preview(*args, **kwargs))[1]
    manager.start_preview(snapshot(scanner, package), "", reuse_report=report)
    fresh = finish(manager, "ready")["preview"]
    assert len(calls) == 1 and fresh["eligible_count"] == 1
    assert fresh["change_count"] == plan["change_count"]
    assert fresh["batch_plan_id"] != report["preview"]["batch_plan_id"]
    assert fresh["provenance"] == "reused_selected_sources"
    assert fresh["source_index"]["archive_count"] == 1
    assert fresh["source_index"]["scope"] == "selected_sources"
    assert read_all(package) == members and not manager.status()["result"]
    # A selected-source review can itself be rechecked, still only as hints.
    manager.start_preview(snapshot(scanner, package), "", reuse_report=manager.status())
    assert finish(manager, "ready")["preview"]["eligible_count"] == 1


def test_reuse_hints_do_not_bypass_exact_match_or_claim_new_folder_uniqueness(tmp_path):
    manager, scanner, _, _, package, source, members, entries = setup(tmp_path)
    preview(manager, scanner, source, package)
    report = manager.status()
    source.write_bytes(b"different content and different chart")
    entries[0]["song"].levels[0].notes[0].fret = 8
    manager.start_preview(snapshot(scanner, package), "", reuse_report=report)
    plan = finish(manager, "ready")["preview"]
    assert plan["eligible_count"] == 0 and plan["blocked_count"] == 1
    assert read_all(package) == members


def test_duplicate_archives_retain_selected_alias_for_both_audio_variants(tmp_path):
    manager, scanner, _, _, package, source, _, _ = setup(tmp_path)
    alias = source.with_name("Other.psarc")
    shutil.copyfile(source, alias)
    variant = package.with_name("Song No Guitar.feedpak")
    shutil.copyfile(package, variant)
    preview(manager, scanner, source, package, variant)
    report = manager.status()
    report["preview"]["packages"][1]["source_path"] = str(alias)
    manager.start_preview(snapshot(scanner, package, variant), "", reuse_report=report)
    plan = finish(manager, "ready")["preview"]
    assert plan["eligible_count"] == 2
    assert {r["source_path"] for r in plan["packages"]} == {str(source), str(alias)}
    assert plan["source_index"]["duplicate_archive_count"] == 1


def test_invalid_reuse_report_does_not_destroy_current_ready_preview(tmp_path):
    manager, scanner, _, _, package, source, _, _ = setup(tmp_path)
    plan = preview(manager, scanner, source, package)
    report = copy.deepcopy(manager.status())
    report["running"] = True
    with pytest.raises(ValueError):
        manager.start_preview(snapshot(scanner, package), "", reuse_report=report)
    assert manager.status()["preview"] == plan


def test_selected_source_cannot_escape_reviewed_folder(tmp_path):
    manager, scanner, _, _, package, source, members, _ = setup(tmp_path)
    preview(manager, scanner, source, package)
    report = manager.status()
    outside = source.parent.parent / "Outside.psarc"
    shutil.copyfile(source, outside)
    report["preview"]["packages"][0]["source_path"] = str(outside)
    manager.start_preview(snapshot(scanner, package), "", reuse_report=report)
    status = finish(manager, "ready")
    assert status["preview"]["eligible_count"] == 0 and status["preview"]["blocked_count"] == 1
    assert status["preview"]["packages"][0]["code"] == "selected_source_unavailable"
    with pytest.raises(batch.SourceRecoveryBatchError):
        manager.start_apply(status["preview"]["batch_plan_id"])
    assert read_all(package) == members
