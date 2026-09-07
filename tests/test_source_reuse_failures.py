"""An unavailable explicit source must not block unrelated selected originals."""
import copy
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from test_source_recovery import load, read_all
from test_source_recovery_batch import batch, finish, preview, setup, snapshot
from test_source_recovery_state import VERSIONS, digest, fixture, state


def prepared(tmp_path):
    manager, scanner, engine, service, package, source, members, _ = setup(tmp_path)
    service._validate_reviewed_arrangement = load("validator").validate_reviewed_arrangement
    second = package.with_name("Other.feedpak")
    shutil.copyfile(package, second)
    other = source.with_name("Other.psarc")
    shutil.copyfile(source, other)
    preview(manager, scanner, source, package, second)
    report = manager.status()
    choices = {package.name: source, second.name: other}
    for row in report["preview"]["packages"]:
        row["source_path"] = str(choices[row["package"]])
    return manager, scanner, engine, package, source, members, second, other, report


@pytest.mark.parametrize("failure", ["missing", "unreadable", "outside"])
def test_failed_selected_source_is_local_and_good_choice_remains_freshly_reviewed(tmp_path, failure):
    manager, scanner, engine, package, source, members, second, other, report = prepared(tmp_path)
    if failure == "missing":
        other.unlink()
    elif failure == "outside":
        outside = source.parent.parent / "Outside.psarc"
        shutil.copyfile(other, outside)
        next(row for row in report["preview"]["packages"] if row["package"] == second.name)["source_path"] = str(outside)
    else:
        read = engine.read_source_charts

        def unreadable(value, **options):
            if Path(value) == other:
                raise ValueError("Unreadable selected chart table")
            return read(value, **options)

        engine.read_source_charts = unreadable
    before = copy.deepcopy(report)
    manager.start_preview(snapshot(scanner, package, second), "", reuse_report=report)
    result = finish(manager, "ready")["preview"]
    rows = {row["package"]: row for row in result["packages"]}
    assert result["eligible_count"] == result["blocked_count"] == 1
    assert not result["source_index"]["complete"] and result["source_index"]["error_count"] == 1
    assert rows[package.name]["status"] == "eligible" and rows[second.name]["status"] == "blocked"
    assert rows[second.name]["code"] == "selected_source_unavailable"
    assert set(manager._bindings) == {package.name} and manager._bindings[package.name]["source_indexed"] is True
    assert report == before and read_all(package) == read_all(second) == members
    # Individually proven bindings survive restart, but incomplete reports cannot
    # be imported as a new set of trusted source-choice hints.
    restored = batch.SourceRecoveryBatchManager(config_dir=manager._path.parents[1], scanner=scanner,
        recovery=engine, index_factory=manager.index_factory, log=manager.log)
    assert restored.status()["phase"] == "ready" and restored.status()["preview"] == result
    with pytest.raises(ValueError, match="complete original folder index"):
        manager.start_preview(snapshot(scanner, package, second), "", reuse_report=manager.status())


def test_selected_index_cancels_before_any_path_probe_or_decode(tmp_path, monkeypatch):
    index = load("source_folder_index").SourceFolderIndex(archive=NS(), chart=NS())
    checked = []
    monkeypatch.setattr(index, "_check_path", lambda *a: checked.append(a))
    paths = [str(tmp_path / f"missing-{i}.psarc") for i in range(10000)]
    stop = threading.Event()
    stop.set()
    result = index.build(tmp_path, cancel_event=stop, selected_paths=paths)
    assert result["cancelled"] and not result["complete"] and not checked


def test_selected_index_progress_can_cancel_before_path_probe(tmp_path, monkeypatch):
    index = load("source_folder_index").SourceFolderIndex(archive=NS(), chart=NS())
    monkeypatch.setattr(index, "_check_path", lambda *a: pytest.fail("Path checked after cancellation"))
    result = index.build(tmp_path, selected_paths=[str(tmp_path / "missing.psarc")],
                         on_progress=lambda progress: False)
    assert result["cancelled"] and result["indexed_archive_count"] == 0


def test_real_scan_target_dictionary_roundtrips_and_invalid_availability_is_rejected():
    root, scope, bindings, plan = fixture()
    scope["target"] = {"kind": "library", "label": "Whole library"}
    plan.pop("batch_plan_id")
    plan["batch_plan_id"] = digest({"root": root, "snapshot": scope, "bindings": bindings, "preview": plan})
    payload = state.pack_ready(root, scope, bindings, plan, **VERSIONS)
    assert state.unpack_ready(payload, **VERSIONS)["snapshot"]["target"] == scope["target"]
    scope["target"]["repairs_available"] = "false"
    with pytest.raises(ValueError, match="repair availability"):
        state.pack_ready(root, scope, bindings, plan, **VERSIONS)


def test_partial_selected_checkpoint_requires_individual_source_index_proof():
    root, scope, bindings, plan = fixture()
    plan["provenance"] = "reused_selected_sources"
    plan["source_index"].update(scope="selected_sources", complete=False, error_count=1)
    plan.pop("batch_plan_id")
    plan["batch_plan_id"] = digest({"root": root, "snapshot": scope, "bindings": bindings, "preview": plan})
    with pytest.raises(ValueError, match="incomplete index"):
        state.pack_ready(root, scope, bindings, plan, **VERSIONS)


@pytest.mark.parametrize("previous_status", ["unchanged", "blocked"])
def test_reuse_skips_unproposed_rows_before_any_package_or_source_reads(tmp_path, previous_status):
    manager, scanner, engine, package, source, members, second, other, report = prepared(tmp_path)
    prior = next(row for row in report["preview"]["packages"] if row["package"] == second.name)
    prior.update(status=previous_status, change_count=0, member_count=0, reason="Prior mismatch reason")
    report["preview"].update(eligible_count=1, change_count=6,
        unchanged_count=int(previous_status == "unchanged"), blocked_count=int(previous_status == "blocked"))
    current_scope = snapshot(scanner, package, second)
    other.unlink()
    second.unlink()  # A skipped row makes no claim about current file existence.
    inspect, package_current = engine.inspect_package, manager._package_current

    def inspect_selected(name):
        assert name != second.name
        return inspect(name)

    def guard_selected(row):
        assert row["package"] != second.name
        return package_current(row)

    engine.inspect_package, manager._package_current = inspect_selected, guard_selected
    manager.start_preview(current_scope, "", reuse_report=report)
    result = finish(manager, "ready")["preview"]
    skipped = next(row for row in result["packages"] if row["package"] == second.name)
    assert result["skipped_count"] == 1 and result["source_index"]["archive_count"] == 1
    assert skipped["status"] == previous_status and "not rechecked" in skipped["reason"].lower()
    assert skipped["source_path"] == skipped["source_name"] == "" and skipped["change_count"] == 0
    assert skipped["code"] == "previously_unproposed_not_rechecked"
    assert set(manager._bindings) == {package.name} and read_all(package) == members
    manager.start_preview(current_scope, "", reuse_report=manager.status())
    assert finish(manager, "ready")["preview"]["skipped_count"] == 1
