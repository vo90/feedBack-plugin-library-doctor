"""Source preview validates changed charts without materializing package media."""
import copy
import json
import zipfile

import pytest

from test_source_recovery import build, load, read_all


def setup(tmp_path, *, archive=True):
    result = build(tmp_path, archive=archive)
    result[1]._validate_reviewed_arrangement = load("validator").validate_reviewed_arrangement
    return result


def write_members(package, members):
    if package.is_dir():
        for name, data in members.items():
            (package / name).write_bytes(data)
    else:
        with zipfile.ZipFile(package, "w") as target:
            for name, data in members.items():
                target.writestr(name, data)


@pytest.mark.parametrize("archive", [True, False])
def test_preview_validates_changed_charts_without_package_candidate_or_audio_reads(tmp_path, monkeypatch, archive):
    _, service, engine, package, original, members, _ = setup(tmp_path, archive=archive)
    expected_id = engine._plan(package, package.name, original)["plan_id"]
    before = package.read_bytes() if archive else None
    monkeypatch.setattr(service, "_candidate", lambda *a, **k: pytest.fail("Preview rebuilt the package"))
    monkeypatch.setattr(service, "_validate_feedpak", lambda *a, **k: pytest.fail("Preview read the full package"))
    reads, validate_calls = [], []
    read = service._read_member
    validate = service._validate_reviewed_arrangement

    def read_chart(path, member, *args):
        assert member in {"manifest.yaml", "lead.json", "rhythm.json"}
        reads.append(member)
        return read(path, member, *args)

    def validate_chart(document, **context):
        validate_calls.append((context["relpath"], bool(document["notes"][0].get("bnv"))))
        return validate(document, **context)

    monkeypatch.setattr(service, "_read_member", read_chart)
    monkeypatch.setattr(service, "_validate_reviewed_arrangement", validate_chart)
    plan = engine.preview(package.name, original)
    assert plan["available"] and plan["chart_validated"] and plan["validation_scope"] == "arrangements"
    assert "candidate_validated" not in plan and plan["plan_id"] == expected_id
    assert validate_calls == [("lead.json", False), ("lead.json", True),
                              ("rhythm.json", False), ("rhythm.json", True)]
    assert reads and read_all(package) == members
    if archive:
        assert package.read_bytes() == before


def test_each_manifest_context_uses_duration_tuning_and_identity_and_is_compared_separately(tmp_path):
    repair, service, engine, package, original, members, _ = setup(tmp_path)
    members["manifest.yaml"] = (
        b"duration: 20\narrangements:\n"
        b"  - {id: lead, name: Main, type: lead, file: lead.json, tuning: [0, 0, 0, 0, 0, 0], capo: 0}\n"
        b"  - {id: alternate, name: Alt, type: rhythm, file: lead.json}\n"
        b"  - {id: rhythm, type: rhythm, file: rhythm.json}\n")
    write_members(package, members)
    contexts = []

    def validate(document, **context):
        contexts.append(copy.deepcopy(context))
        has_curve = bool(document["notes"][0].get("bnv"))
        # Totals across these two contexts are unchanged. The worsening alternate
        # declaration must still block instead of being offset by Main's improvement.
        count = (0 if has_curve else 1) if context["arrangement_id"] == "lead" else (1 if has_curve else 0)
        return {"findings": [{"severity": "warning", "code": "test.context", "affected_count": count}] if count else []}

    service._validate_reviewed_arrangement = validate
    with pytest.raises(repair.RepairPlanningError, match="new validation finding") as error:
        engine.preview(package.name, original)
    assert error.value.code == "verification_failed"
    assert [c["arrangement_id"] for c in contexts] == ["lead", "alternate", "lead", "alternate"]
    assert all(c["duration"] == 20 for c in contexts)
    assert contexts[0]["entry"] == {"id": "lead", "name": "Main", "type": "lead", "tuning": [0] * 6, "capo": 0}
    assert read_all(package) == members


def test_preview_validation_skips_unchanged_arrangements(tmp_path):
    _, service, engine, package, original, members, entries = setup(tmp_path)
    existing = json.loads(members["rhythm.json"])
    repaired = engine.chart.recovery_patch(existing, entries[0]["song"])[0]
    members["rhythm.json"] = json.dumps(repaired).encode()
    write_members(package, members)
    calls, validate = [], service._validate_reviewed_arrangement

    def changed_only(document, **context):
        calls.append(context["relpath"])
        return validate(document, **context)

    service._validate_reviewed_arrangement = changed_only
    plan = engine.preview(package.name, original)
    assert plan["available"] and plan["member_count"] == 1 and plan["change_count"] == 3
    assert calls == ["lead.json", "lead.json"] and read_all(package) == members


def test_increased_existing_finding_count_is_rejected(tmp_path):
    repair, service, engine, package, original, members, _ = setup(tmp_path)
    service._validate_reviewed_arrangement = lambda document, **context: {"findings": [
        {"severity": "warning", "code": "test.same-code", "affected_count": 3 if document["notes"][0].get("bnv") else 2}]}
    with pytest.raises(repair.RepairPlanningError) as error:
        engine.preview(package.name, original)
    assert error.value.code == "verification_failed" and read_all(package) == members


def test_missing_or_failing_chart_validator_does_not_claim_validation(tmp_path):
    repair, service, engine, package, original, members, _ = setup(tmp_path)
    service._validate_reviewed_arrangement = None
    with pytest.raises(repair.RepairPlanningError) as error:
        engine.preview(package.name, original)
    assert error.value.code == "reviewed_validation_unavailable"
    service._validate_reviewed_arrangement = lambda *a, **k: None
    with pytest.raises(repair.RepairPlanningError) as error:
        engine.preview(package.name, original)
    assert error.value.code == "reviewed_validation_failed" and read_all(package) == members


@pytest.mark.parametrize("changed_input", ["source", "manifest", "chart"])
def test_strict_input_guard_runs_after_in_memory_validation(tmp_path, changed_input):
    repair, service, engine, package, original, members, _ = setup(tmp_path)
    validate, changed = service._validate_reviewed_arrangement, False

    def validate_then_change(document, **context):
        nonlocal changed
        report = validate(document, **context)
        if document["notes"][0].get("bnv") and not changed:
            changed = True
            if changed_input == "source":
                original.write_bytes(original.read_bytes() + b" changed")
            else:
                member = "manifest.yaml" if changed_input == "manifest" else "rhythm.json"
                members[member] += b"\n"
                write_members(package, members)
        return report

    service._validate_reviewed_arrangement = validate_then_change
    with pytest.raises(repair.RepairPlanningError) as error:
        engine.preview(package.name, original)
    assert changed and error.value.code == "source_changed"
    assert read_all(package) == members


def test_apply_retains_full_package_validation_and_exact_undo_after_lightweight_preview(tmp_path):
    _, service, engine, package, original, members, _ = setup(tmp_path)
    calls, validate = [], service._validate_feedpak

    def full_validate(path, name, **options):
        calls.append(str(path))
        return validate(path, name, **options)

    service._validate_feedpak = full_validate
    plan = engine.preview(package.name, original)
    assert not calls
    receipt = engine.apply(package.name, original, plan["plan_id"])
    assert receipt["applied"] and receipt["undo_available"] and len(calls) >= 2
    assert read_all(package)["audio.ogg"] == members["audio.ogg"]
    assert json.loads(read_all(package)["lead.json"])["notes"][0]["bnv"]
    service.restore(package.name, receipt["backup_id"])
    assert read_all(package) == members
