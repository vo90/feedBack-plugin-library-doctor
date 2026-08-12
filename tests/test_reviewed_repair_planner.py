import copy
import json
import logging

import pytest

import repair
import repair_eligibility


def _raw(document):
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _candidate(document, *, index=0):
    return repair_eligibility.find_hopo_review_candidates(
        document, member_path="arrangements/lead.json"
    )[index]


def _plan(document, decision, *, index=0):
    candidate = _candidate(document, index=index)
    return repair.plan_reviewed_json_member(
        _raw(document),
        member_path="arrangements/lead.json",
        adapter_id="review.hopo-techniques",
        validator_version="rules-test",
        decisions=[{
            "candidate_id": candidate.candidate_id,
            "decision": decision,
        }],
    )


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("set_hammer_on", {"ho": True}),
        ("set_pull_off", {"po": True}),
        ("convert_to_tap", {"tp": True}),
        ("remove_hopo", {}),
    ],
)
def test_reviewed_hopo_decisions_change_only_closed_technique_fields(
    decision, expected
):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 0.25},
            {
                "t": 2.0,
                "s": 0,
                "f": 5,
                "sus": 1.5,
                "ho": True,
                "po": True,
                "custom": "preserve me",
            },
        ],
        "chords": [],
    }
    plan = _plan(document, decision)

    repaired = json.loads(repair.apply_json_member(_raw(document), plan))

    note = repaired["notes"][1]
    assert {key: note[key] for key in ("t", "s", "f", "sus", "custom")} == {
        "t": 2.0,
        "s": 0,
        "f": 5,
        "sus": 1.5,
        "custom": "preserve me",
    }
    assert {key: note[key] for key in ("ho", "po", "tp") if key in note} == expected
    assert plan["repair_mode"] == "reviewed"
    assert plan["actions"][0]["safety"] == "review_required"


def test_move_to_next_relocates_directional_flag_to_unambiguous_explicit_note():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 5},
            {"t": 2.0, "s": 0, "f": 5, "ho": True},
            {"t": 3.0, "s": 0, "f": 8, "sus": 0.5},
        ],
        "chords": [],
    }
    plan = _plan(document, "move_to_next")

    repaired = json.loads(repair.apply_json_member(_raw(document), plan))

    assert "ho" not in repaired["notes"][1]
    assert "po" not in repaired["notes"][1]
    assert repaired["notes"][2]["ho"] is True
    assert "po" not in repaired["notes"][2]
    assert repaired["notes"][2]["sus"] == 0.5


def test_leave_unchanged_is_source_bound_but_has_no_mutation_action():
    document = {
        "notes": [{"t": 1.0, "s": 0, "f": 5, "ho": True}],
        "chords": [],
    }
    plan = _plan(document, "leave_unchanged")

    assert plan["decisions"][0]["decision"] == "leave_unchanged"
    assert plan["actions"] == []
    assert repair.apply_json_member(_raw(document), plan) == _raw(document)


def test_reviewed_plan_rejects_stale_candidate_and_duplicate_decision():
    document = {
        "notes": [{"t": 1.0, "s": 0, "f": 5, "ho": True}],
        "chords": [],
    }
    candidate = _candidate(document)

    with pytest.raises(repair.RepairPlanningError) as stale:
        repair.plan_reviewed_json_member(
            _raw(document),
            member_path="arrangements/other.json",
            adapter_id="review.hopo-techniques",
            validator_version="rules-test",
            decisions=[{
                "candidate_id": candidate.candidate_id,
                "decision": "remove_hopo",
            }],
        )
    assert stale.value.code == "candidate_changed"

    with pytest.raises(repair.RepairPlanningError) as duplicate:
        repair.plan_reviewed_json_member(
            _raw(document),
            member_path="arrangements/lead.json",
            adapter_id="review.hopo-techniques",
            validator_version="rules-test",
            decisions=[
                {"candidate_id": candidate.candidate_id, "decision": "remove_hopo"},
                {"candidate_id": candidate.candidate_id, "decision": "convert_to_tap"},
            ],
        )
    assert duplicate.value.code == "invalid_decisions"


def test_reviewed_apply_refuses_forged_generic_field_mutation():
    document = {
        "notes": [{"t": 1.0, "s": 0, "f": 5, "ho": True}],
        "chords": [],
    }
    plan = _plan(document, "remove_hopo")
    forged = copy.deepcopy(plan)
    change = forged["actions"][0]["operations"][0]["changes"][0]
    change["set_fields"] = {"tp": True}
    unsigned = {key: value for key, value in forged.items() if key != "plan_id"}
    forged["plan_id"] = repair._digest_json(unsigned)

    with pytest.raises(repair.RepairPlanningError) as error:
        repair.apply_json_member(_raw(document), forged)

    assert error.value.code == "invalid_plan"


def test_candidate_blocker_prevents_mutation_but_remains_visible():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
            {"t": 2.0, "s": 0, "f": 7},
        ],
        "chords": [],
    }
    candidate = _candidate(document)
    assert candidate.blockers == ("same_time_string_conflict",)

    with pytest.raises(repair.RepairPlanningError) as error:
        _plan(document, "remove_hopo")

    assert error.value.code == "candidate_blocked"


def test_move_to_next_is_not_offered_when_target_already_has_hopo():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 5},
            {"t": 2.0, "s": 0, "f": 5, "ho": True},
            {"t": 3.0, "s": 0, "f": 8, "po": True},
        ],
        "chords": [],
    }

    candidate = _candidate(document)

    assert "move_to_next" not in candidate.decision_names


def test_move_to_next_plan_fails_closed_when_target_gains_tap():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 5},
            {"t": 2.0, "s": 0, "f": 5, "ho": True},
            {"t": 3.0, "s": 0, "f": 8},
        ],
        "chords": [],
    }
    plan = _plan(document, "move_to_next")
    changed = copy.deepcopy(document)
    changed["notes"][2]["tp"] = True

    with pytest.raises(repair.RepairPlanningError) as error:
        repair.apply_json_member(_raw(changed), plan)

    assert error.value.code == "source_changed"


def test_reviewed_jsonc_is_explicitly_read_only():
    document = {
        "notes": [{"t": 1.0, "s": 0, "f": 5, "ho": True}],
        "chords": [],
    }

    with pytest.raises(repair.RepairPlanningError) as error:
        repair.plan_reviewed_json_member(
            _raw(document),
            member_path="arrangements/lead.jsonc",
            adapter_id="review.hopo-techniques",
            validator_version="rules-test",
            decisions=[{
                "candidate_id": "hopo-" + "a" * 24,
                "decision": "remove_hopo",
            }],
        )

    assert error.value.code == "jsonc_requires_lossless_writer"


def test_candidate_from_later_bounded_page_can_be_planned_and_applied():
    document = {
        "notes": [
            {"t": 1.0, "s": string, "f": 5, "ho": True}
            for string in range(2_001)
        ],
        "chords": [],
    }
    later_page = repair_eligibility.page_hopo_review_candidates(
        document,
        member_path="arrangements/lead.json",
        offset=2_000,
        limit=1,
    )
    candidate = later_page.candidates[0]

    plan = repair.plan_reviewed_json_member(
        _raw(document),
        member_path="arrangements/lead.json",
        adapter_id="review.hopo-techniques",
        validator_version="rules-test",
        decisions=[{
            "candidate_id": candidate.candidate_id,
            "decision": "remove_hopo",
        }],
    )
    repaired = json.loads(repair.apply_json_member(_raw(document), plan))

    assert "ho" not in repaired["notes"][2_000]
    assert repaired["notes"][1_999]["ho"] is True


def test_reviewed_service_uses_candidate_validation_backup_history_and_undo(
    tmp_path,
):
    library = tmp_path / "library"
    package = library / "Song.feedpak"
    arrangements = package / "arrangements"
    arrangements.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        "title: Test Song\n"
        "artist: Test Artist\n"
        "arrangements:\n"
        "  - id: lead\n"
        "    file: arrangements/lead.json\n",
        encoding="utf-8",
    )
    arrangement_path = arrangements / "lead.json"
    source = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
        ],
        "chords": [],
    }
    source_raw = _raw(source)
    arrangement_path.write_bytes(source_raw)

    def validate(path, _package_name, *, deep_audio=False):
        candidate_path = path / "arrangements" / "lead.json"
        document = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidates = repair_eligibility.find_hopo_review_candidates(document)
        codes = sorted({
            code for candidate in candidates for code in candidate.trigger_codes
        })
        return {
            "validator_version": "rules-test",
            "features": {"deep_audio_checked": deep_audio},
            "findings": [
                {"code": code, "severity": "info"} for code in codes
            ],
            "counts": {"error": 0, "warning": 0, "info": len(codes)},
            "status": "review" if codes else "healthy",
            "title": "Test Song",
            "artist": "Test Artist",
        }

    barriers = []
    service = repair.RepairService(
        config_dir=tmp_path / "config",
        get_dlc_dir=lambda: library,
        validate_feedpak=validate,
        validator_version="rules-test",
        log=logging.getLogger("reviewed-repair-transaction-tests"),
        transaction_barrier=lambda name, _context: barriers.append(name),
    )

    inspection = service.inspect_reviewed(
        "Song.feedpak", "review.hopo-techniques"
    )
    assert inspection["candidate_count"] == 1
    assert inspection["available"] is True
    candidate_id = inspection["candidates"][0]["candidate_id"]
    decisions = [{
        "candidate_id": candidate_id,
        "decision": "set_hammer_on",
    }]
    preview = service.preview_reviewed(
        "Song.feedpak", "review.hopo-techniques", decisions
    )

    result = service.apply_reviewed(
        "Song.feedpak",
        "review.hopo-techniques",
        decisions,
        preview["plan_id"],
    )

    repaired = json.loads(arrangement_path.read_text(encoding="utf-8"))
    assert repaired["notes"][1]["ho"] is True
    assert "po" not in repaired["notes"][1]
    assert result["applied"] is True
    assert result["safety"] == "review_required"
    assert result["backup_id"]
    assert result["undo_available"] is True
    assert {
        "source_captured",
        "candidate_validated",
        "backup_durable",
        "member_committed",
        "package_committed",
    }.issubset(barriers)
    history = service.history()["items"][0]
    assert history["change_kind"] == "reviewed_decisions"
    assert history["selected_count"] == 1
    assert history["changing_count"] == 1
    assert history["remaining_review_count"] == 0
    assert history["decision_counts"]["set_hammer_on"] == 1

    restore_preview = service.preview_restore("Song.feedpak", result["backup_id"])
    assert restore_preview["available"] is True
    restored = service.restore(
        "Song.feedpak",
        result["backup_id"],
    )
    assert restored["restored"] is True
    assert arrangement_path.read_bytes() == source_raw


def test_reviewed_validation_allows_registered_review_outcomes_only():
    repair.RepairService._verify_reviewed_validation(
        {
            "findings": [{
                "code": "chart.conflicting-techniques",
                "affected_count": 1,
            }],
        },
        {
            "findings": [{
                "code": "review.hopo-direction-mismatch",
                "affected_count": 1,
            }],
        },
        {
            "chart.conflicting-techniques",
            "review.hopo-direction-mismatch",
        },
    )

    with pytest.raises(repair.RepairPlanningError) as error:
        repair.RepairService._verify_reviewed_validation(
            {
                "findings": [{
                    "code": "chart.string-conflict",
                    "affected_count": 1,
                }],
            },
            {
                "findings": [{
                    "code": "chart.string-conflict",
                    "affected_count": 2,
                }],
            },
            {"review.same-fret-hopo"},
        )

    assert error.value.code == "verification_failed"
