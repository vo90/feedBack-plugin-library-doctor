"""Saved reviews are bounded evidence; imported reports are source hints only."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "source_recovery_state_tests", Path(__file__).parents[1] / "source_recovery_state.py")
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
VERSIONS = {"catalog_version": "repairs-test", "validator_version": "validator-test"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fixture():
    names = ["Artist/Recover.feedpak", "Unchanged.feedpak", "Missing.feedpak", "No bends.feedpak"]
    snapshot = {"schema": state.SCOPE_SCHEMA, "target": "configured-library", "validator_version": VERSIONS["validator_version"],
                "scanned_at": 1788764847.166163, "scope_package_count": len(names),
                "candidates": [{"package": name, "scan_signature": f"signature-{i}", "title": name, "artist": "Artist"}
                               for i, name in enumerate(names)]}
    rows = [{"package": name, "status": status, "source_path": source, "source_name": "Original.psarc" if source else "",
             "change_count": 2 if status == "eligible" else 0, "excluded_count": 0,
             "member_count": 1 if status == "eligible" else 0}
            for name, status, source in zip(names, ["eligible", "unchanged", "blocked", "unchanged"],
                                            ["C:\\Originals\\Original.psarc", "C:\\Originals\\Original.psarc", "", ""])]
    preview = {"source_folder": "C:\\Originals", "scope_package_count": len(names), "eligible_count": 1,
               "blocked_count": 1, "unchanged_count": 2, "change_count": 2, "packages": rows, "source_errors": [],
               "source_index": {"folder": "C:\\Originals", "complete": True, "cancelled": False,
                   "archive_count": 2, "indexed_archive_count": 2, "unique_archive_count": 1,
                   "duplicate_archive_count": 1, "chart_count": 2, "error_count": 0, "skipped_link_count": 0,
                   "errors": [], "skipped_links": [], "errors_truncated": False, "skipped_links_truncated": False,
                   "limit_reached": False}}
    bindings = {name: {**snapshot["candidates"][i], "source_path": rows[i]["source_path"], "source_sha256": "a" * 64,
                       "source_members": ["songs/bin/generic/lead.sng"], "plan_id": str(i) * 64, "available": i == 0}
                for i, name in enumerate(names[:2])}
    root = "C:\\Library"
    preview["batch_plan_id"] = digest({"root": root, "snapshot": snapshot, "bindings": bindings, "preview": preview})
    return root, snapshot, bindings, preview


def report(preview):
    return {"schema": state.BATCH_SCHEMA, "phase": "ready", "mode": "preview", "running": False,
            "done": preview["scope_package_count"], "total": preview["scope_package_count"], "preview": copy.deepcopy(preview)}


def checkpoint():
    return state.pack_ready(*fixture(), **VERSIONS)


def resign(payload, *, batch=False):
    if batch:
        preview = payload["preview"]
        preview["batch_plan_id"] = digest({"root": payload["root"], "snapshot": payload["snapshot"],
            "bindings": payload["bindings"], "preview": {key: value for key, value in preview.items() if key != "batch_plan_id"}})
    payload["digest"] = digest({key: value for key, value in payload.items() if key != "digest"})


def test_ready_roundtrip_preserves_exact_review_and_detaches_all_mutable_values():
    root, snapshot, bindings, preview = fixture()
    packed = state.pack_ready(root, snapshot, bindings, preview, **VERSIONS)
    saved = json.loads(json.dumps(packed))
    restored = state.unpack_ready(saved, **VERSIONS)
    assert restored == {"root": root, "snapshot": snapshot, "bindings": bindings, "preview": preview}
    restored["bindings"]["Artist/Recover.feedpak"]["source_members"].append("changed")
    preview["packages"][0]["change_count"] = 99
    assert packed == saved
    assert saved["preview"]["packages"][0]["change_count"] == 2


@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"), ("policy_version", "older-policy"), ("catalog_version", "older-catalog"),
    ("validator_version", "older-validator"),
])
def test_expired_checkpoint_contracts_do_not_restore_ready(field, value):
    saved = checkpoint()
    saved[field] = value
    resign(saved)
    with pytest.raises(ValueError, match="version"):
        state.unpack_ready(saved, **VERSIONS)


def test_changed_checkpoint_without_digest_update_is_rejected():
    saved = checkpoint()
    saved["preview"]["packages"][0]["source_path"] = "C:\\Other\\Original.psarc"
    with pytest.raises(ValueError, match="digest"):
        state.unpack_ready(saved, **VERSIONS)


def test_outer_digest_does_not_replace_batch_plan_integrity():
    saved = checkpoint()
    saved["root"] = "C:\\OtherLibrary"
    resign(saved)
    with pytest.raises(ValueError, match="batch plan ID"):
        state.unpack_ready(saved, **VERSIONS)


@pytest.mark.parametrize("change", [
    lambda p: p["bindings"].pop("Artist/Recover.feedpak"),
    lambda p: p["bindings"].update({"Missing.feedpak": copy.deepcopy(p["bindings"]["Artist/Recover.feedpak"])}),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(package="Unchanged.feedpak"),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(scan_signature="different"),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(source_path="C:\\Originals\\Other.psarc"),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(available=False),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(available=1),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(source_sha256="invalid"),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(plan_id="invalid"),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].pop("source_members"),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(source_members=["../songs/bin/lead.sng"]),
    lambda p: p["bindings"]["Artist/Recover.feedpak"].update(source_members=["songs/bin/lead.sng"] * 2),
])
def test_consistently_resigned_but_incoherent_bindings_are_rejected(change):
    saved = checkpoint()
    change(saved)
    resign(saved, batch=True)
    with pytest.raises(ValueError):
        state.unpack_ready(saved, **VERSIONS)


@pytest.mark.parametrize("change", [
    lambda p: p["preview"].update(eligible_count=2),
    lambda p: p["preview"].update(change_count=3),
    lambda p: p["preview"]["packages"][0].update(status={}),
    lambda p: p["preview"]["packages"][0].update(change_count=True),
    lambda p: p["preview"]["packages"][0].update(member_count=0),
    lambda p: p["preview"]["packages"][2].update(change_count=2),
    lambda p: p["preview"]["packages"][0].update(package="../Outside.feedpak"),
    lambda p: p["preview"]["packages"].append(copy.deepcopy(p["preview"]["packages"][0])),
    lambda p: p["snapshot"]["candidates"][0].update(scan_signature=None),
    lambda p: p["snapshot"].update(validator_version="other"),
    lambda p: p["snapshot"].update(scanned_at=float("inf")),
])
def test_pack_refuses_malformed_or_inconsistent_ready_state(change):
    root, snapshot, bindings, preview = fixture()
    value = {"root": root, "snapshot": snapshot, "bindings": bindings, "preview": preview}
    change(value)
    with pytest.raises(ValueError):
        state.pack_ready(**value, **VERSIONS)


def test_reused_source_choices_can_be_saved_without_a_new_uniqueness_claim():
    root, snapshot, bindings, preview = fixture()
    preview.update(provenance="reused_selected_sources")
    preview["source_index"]["scope"] = "selected_sources"
    preview.pop("batch_plan_id")
    preview["batch_plan_id"] = digest({"root": root, "snapshot": snapshot, "bindings": bindings, "preview": preview})
    saved = state.pack_ready(root, snapshot, bindings, preview, **VERSIONS)
    assert state.unpack_ready(saved, **VERSIONS)["preview"]["provenance"] == "reused_selected_sources"
    assert state.parse_reuse_report(report(preview), snapshot)["selections"]


def test_incomplete_automatic_index_cannot_produce_eligible_checkpoint():
    root, snapshot, bindings, preview = fixture()
    preview["source_index"]["complete"] = False
    with pytest.raises(ValueError, match="incomplete index"):
        state.pack_ready(root, snapshot, bindings, preview, **VERSIONS)


def test_legacy_import_extracts_only_existing_source_choices_and_ignores_write_instructions():
    _, snapshot, _, preview = fixture()
    previous = report(preview)
    previous["preview"].update(batch_plan_id="untrusted-old-id", replacements={"audio.ogg": "do not use"}, changes=["do not use"])
    previous["preview"]["packages"][2]["source_path"] = "C:\\Originals\\Unverified.psarc"
    before = copy.deepcopy(previous)
    hints = state.parse_reuse_report(previous, snapshot)
    assert hints == {"source_folder": "C:\\Originals", "selections": {
        "Artist/Recover.feedpak": "C:\\Originals\\Original.psarc"},
        "skipped": {"Unchanged.feedpak": {"status": "unchanged", "reason": ""},
                    "Missing.feedpak": {"status": "blocked", "reason": ""},
                    "No bends.feedpak": {"status": "unchanged", "reason": ""}},
        "report_digest": digest(previous)}
    assert previous == before
    hints["selections"].clear()
    assert previous == before


@pytest.mark.parametrize("change", [
    lambda r: r.update(schema="unknown"),
    lambda r: r.update(phase="previewing"),
    lambda r: r.update(running=True),
    lambda r: r.update(running=0),
    lambda r: r.update(mode="apply"),
    lambda r: r.update(done=3),
    lambda r: r["preview"].update(provenance="reused_selected_sources"),
    lambda r: r["preview"]["source_index"].update(scope="selected_sources"),
    lambda r: r["preview"].update(source_errors=[{"message": "unreadable"}]),
    lambda r: r["preview"]["source_index"].update(complete=False),
    lambda r: r["preview"]["source_index"].update(cancelled=True),
    lambda r: r["preview"]["source_index"].update(limit_reached=True),
    lambda r: r["preview"]["source_index"].update(errors_truncated=True),
    lambda r: r["preview"]["source_index"].update(error_count=1),
    lambda r: r["preview"]["source_index"].update(error_count=False),
    lambda r: r["preview"]["source_index"].update(errors=["unreadable"]),
    lambda r: r["preview"]["source_index"].update(skipped_link_count=1),
    lambda r: r["preview"]["source_index"].update(skipped_links=["linked"]),
    lambda r: r["preview"]["source_index"].update(indexed_archive_count=1),
    lambda r: r["preview"]["source_index"].update(unique_archive_count=2),
    lambda r: r["preview"]["source_index"].update(folder="C:\\Different"),
    lambda r: r["preview"]["packages"][0].update(source_path="relative.psarc"),
    lambda r: r["preview"]["packages"][0].update(source_path="C:\\Originals\\not-a-psarc.txt"),
])
def test_import_refuses_incomplete_or_misleading_report(change):
    _, snapshot, _, preview = fixture()
    previous = report(preview)
    change(previous)
    with pytest.raises(ValueError):
        state.parse_reuse_report(previous, snapshot)


@pytest.mark.parametrize("change", [
    lambda rows: rows.pop(),
    lambda rows: rows.append({"package": "Other.feedpak", "scan_signature": "other"}),
    lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0])),
])
def test_import_requires_exact_complete_current_package_set(change):
    _, snapshot, _, preview = fixture()
    change(snapshot["candidates"])
    snapshot["scope_package_count"] = len(snapshot["candidates"])
    with pytest.raises(ValueError):
        state.parse_reuse_report(report(preview), snapshot)


def test_inputs_are_bounded_before_nested_contents_are_processed(monkeypatch):
    saved = checkpoint()
    monkeypatch.setattr(state, "MAX_STATE_BYTES", 1024)
    with pytest.raises(ValueError, match="storage bound"):
        state.unpack_ready(saved, **VERSIONS)
    _, snapshot, _, preview = fixture()
    with pytest.raises(ValueError, match="storage bound"):
        state.parse_reuse_report(report(preview), snapshot)


@pytest.mark.parametrize("value", [float("nan"), 2 ** 100, object(), {1: "not a JSON key"}])
def test_non_json_or_unbounded_values_fail_with_valueerror(value):
    saved = checkpoint()
    saved["extra"] = value
    with pytest.raises(ValueError):
        state.unpack_ready(saved, **VERSIONS)


def test_cyclic_and_excessively_nested_objects_fail_without_recursion_errors():
    value = {}
    value["cycle"] = value
    with pytest.raises(ValueError, match="structure bounds"):
        state.unpack_ready(value, **VERSIONS)


def test_source_files_are_never_opened_by_the_pure_parser(monkeypatch):
    _, snapshot, _, preview = fixture()
    def forbidden(*_args, **_kwargs):
        pytest.fail("Pure checkpoint/report validation must not access the filesystem")
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    assert state.unpack_ready(checkpoint(), **VERSIONS)["preview"] == preview
    assert state.parse_reuse_report(report(preview), snapshot)["selections"]
