"""Safety refusals stay distinct from genuine input/storage failures."""

import logging

import pytest

from test_batch_repair import _load_batch_module, _Scanner, _RepairError
from test_terminal_beat_repair import build, modules as modules, RULE


def test_signed_single_and_combined_plans_explain_terminal_evidence(tmp_path, modules):
    service, package, _ = build(tmp_path, modules)
    single = service.preview(package.name, RULE)
    combined = service.preview_all(package.name)
    assessment = single["terminal_assessment"]
    assert assessment["status"] == "automatic"
    assert assessment["evidence_mode"] == "clean_reference_v1"
    assert assessment["removed_count"] == 5
    assert len(assessment["members"]) == 3
    assert all(m["retained_count"] == 20 for m in assessment["members"])
    assert combined["terminal_assessment"] == assessment
    summary = next(s for s in combined["repair_summaries"] if s["rule_code"] == RULE)
    assert summary["terminal_assessment"] == assessment


@pytest.mark.parametrize("failure, expected", [
    (OSError("unreadable inventory"), "terminal_input_read_failed"),
    (ValueError("unsupported inventory"), "terminal_input_invalid"),
])
def test_input_errors_are_not_safety_refusals(tmp_path, modules, monkeypatch, failure, expected):
    service, package, _ = build(tmp_path, modules)

    def fail(_package):
        raise failure

    monkeypatch.setattr(modules[0]._terminal, "package_inventory", fail)
    plan = service.preview(package.name, RULE)
    assert not plan["available"]
    assert expected in {b["code"] for b in plan["blockers"]}
    _, batch = _load_batch_module()
    assert expected not in batch._SKIPPED_REPAIR_CODES
    assert "terminal_tail_requires_review" not in batch._SKIPPED_REPAIR_CODES


def test_expected_safety_refusal_has_its_own_code(tmp_path, modules):
    def mutate(manifest, documents):
        manifest["duration"] = 99

    service, package, _ = build(tmp_path, modules, mutate=mutate)
    preview = service.preview(package.name, RULE)
    assert not preview["available"]
    assert "terminal_tail_safety_refusal" in {b["code"] for b in preview["blockers"]}
    _, batch = _load_batch_module()
    assert "terminal_tail_safety_refusal" in batch._SKIPPED_REPAIR_CODES


def test_batch_review_explains_terminal_only_excluded_operation(tmp_path):
    _, batch = _load_batch_module()
    manager = batch.BatchRepairManager(
        config_dir=tmp_path / "config", scanner=_Scanner(), repair_service=object(),
        repair_error_type=_RepairError, log=logging.getLogger("terminal-exclusion"),
    )
    exclusion = {"package": "manual.feedpak", "title": "Manual song", "rule_code": RULE,
                 "status": "author_review", "reason_code": "missing_clean_grid",
                 "message": "No existing clean grid exactly corroborates this tail."}
    manager.start_preview({"candidates": [], "scope_package_count": 1,
                           "excluded_operations": [exclusion]})
    manager.join(5)
    preview = manager.status()["preview"]
    assert preview["eligible_count"] == 0
    assert preview["candidate_count"] == 0
    assert preview["excluded_operation_count"] == 1
    assert preview["excluded_operations"] == [exclusion]
