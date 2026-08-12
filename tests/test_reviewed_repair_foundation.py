import copy

import pytest

import repair_eligibility
import reviewed_repair


def _by_fret(candidates):
    return {candidate.fret: candidate for candidate in candidates}


def test_hopo_classifier_covers_conflict_direction_same_fret_and_missing_source():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
            {"t": 3.0, "s": 0, "f": 5, "ho": True},
            {"t": 4.0, "s": 0, "f": 7, "ho": True, "po": True},
            {"t": 1000.0, "s": 1, "f": 9, "po": True},
        ],
        "chords": [],
    }
    original = copy.deepcopy(document)

    candidates = repair_eligibility.find_hopo_review_candidates(
        document, member_path="arrangements/lead.json"
    )

    assert document == original
    assert len(candidates) == 4
    by_fret = _by_fret(candidates)
    assert by_fret[5].reasons in {
        ("direction_mismatch",),
        ("same_fret",),
    }
    assert {candidate.reasons for candidate in candidates if candidate.fret == 5} == {
        ("direction_mismatch",),
        ("same_fret",),
    }
    assert by_fret[7].reasons == ("both_flags",)
    assert by_fret[9].reasons == ("no_usable_predecessor",)
    assert candidates[-1].time == 1000.0  # Long gaps never suppress review.


def test_hopo_classifier_uses_previous_note_and_next_note_only_as_evidence():
    document = {
        "notes": [
            {"t": 1.0, "s": 2, "f": 5},
            {"t": 2.0, "s": 2, "f": 5, "ho": True},
            {"t": 3.0, "s": 2, "f": 8},
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.previous.fret == 5
    assert candidate.next.fret == 8
    assert candidate.reasons == ("same_fret",)
    assert "move_to_next" in candidate.decision_names
    assert candidate.next.path == ("notes", 2)


def test_hopo_classifier_keeps_phrase_difficulties_independent():
    document = {
        "notes": [{"t": 1.0, "s": 0, "f": 3}],
        "chords": [],
        "phrases": [{
            "levels": [
                {
                    "notes": [
                        {"t": 1.0, "s": 0, "f": 8},
                        {"t": 2.0, "s": 0, "f": 6, "ho": True},
                    ],
                    "chords": [],
                },
                {
                    "notes": [
                        {"t": 2.0, "s": 0, "f": 6, "po": True},
                    ],
                    "chords": [],
                },
            ]
        }],
    }

    candidates = repair_eligibility.find_hopo_review_candidates(document)

    assert len(candidates) == 2
    assert candidates[0].reasons == ("direction_mismatch",)
    assert candidates[1].reasons == ("no_usable_predecessor",)
    assert candidates[0].stream_path != candidates[1].stream_path


def test_hopo_candidate_id_is_stable_and_bound_to_member_and_context():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
        ],
        "chords": [],
    }

    first = repair_eligibility.find_hopo_review_candidates(
        document, member_path="arrangements/a.json"
    )[0]
    repeated = repair_eligibility.find_hopo_review_candidates(
        copy.deepcopy(document), member_path="arrangements/a.json"
    )[0]
    other_member = repair_eligibility.find_hopo_review_candidates(
        document, member_path="arrangements/b.json"
    )[0]

    assert first.candidate_id == repeated.candidate_id
    assert first.candidate_id != other_member.candidate_id


def test_reviewed_registry_declares_closed_hopo_adapter_contract():
    catalog = reviewed_repair.reviewed_repair_catalog()

    assert catalog == [
        reviewed_repair.reviewed_repair_definition(
            "review.hopo-techniques"
        ).to_dict()
    ]
    definition = catalog[0]
    assert definition["safety"] == "review_required"
    assert definition["mutable_fields"] == ["ho", "po", "tp"]
    assert definition["candidate_id_prefix"] == "hopo"
    assert definition["operation_name"] == "review_hopo_techniques"
    assert set(definition["blocker_codes"]) == {
        "same_time_string_conflict",
        "ambiguous_predecessor",
        "malformed_technique_value",
    }
    assert "only_declared_fields_change" in definition["postconditions"]
    assert set(definition["trigger_rule_codes"]) == {
        "chart.conflicting-techniques",
        "review.hopo-direction-mismatch",
        "review.same-fret-hopo",
        "review.hopo-without-source",
    }
    assert {item["name"] for item in definition["decisions"]} == {
        "set_hammer_on",
        "set_pull_off",
        "convert_to_tap",
        "remove_hopo",
        "move_to_next",
        "leave_unchanged",
    }


def test_correct_direction_lone_hopo_is_not_a_review_candidate():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "ho": True},
            {"t": 3.0, "s": 0, "f": 2, "po": True},
        ],
        "chords": [],
    }

    assert repair_eligibility.find_hopo_review_candidates(document) == []


@pytest.mark.parametrize(
    ("previous_fret", "current_fret", "expected_extra"),
    [
        (3, 5, ()),
        (7, 5, ()),
        (5, 5, ("same_fret",)),
    ],
)
def test_both_flags_are_reviewed_for_every_incoming_direction(
    previous_fret, current_fret, expected_extra
):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": previous_fret},
            {"t": 2.0, "s": 0, "f": current_fret, "ho": True, "po": True},
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.reasons == ("both_flags", *expected_extra)


def test_template_chord_is_context_but_explicit_chord_member_is_writable():
    document = {
        "templates": [{"frets": [3, -1, -1, -1, -1, -1]}],
        "notes": [],
        "chords": [
            {"t": 1.0, "id": 0},
            {"t": 2.0, "notes": [{"s": 0, "f": 5, "po": True}]},
        ],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.previous.fret == 3
    assert candidate.previous.writable is False
    assert candidate.context_kind == "chord_member"
    assert candidate.target_path == ("chords", 1, "notes", 0)


def test_ambiguous_predecessor_and_malformed_tap_are_visible_blockers():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 1.0, "s": 0, "f": 5},
            {"t": 2.0, "s": 0, "f": 7, "ho": True, "tp": "false"},
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.reasons == ("no_usable_predecessor",)
    assert candidate.predecessor_state == "ambiguous"
    assert set(candidate.blockers) == {
        "ambiguous_predecessor",
        "malformed_technique_value",
    }


@pytest.mark.parametrize(
    "next_notes",
    [
        [{"t": 3.0, "s": 0, "f": 8, "tp": True}],
        [
            {"t": 3.0, "s": 0, "f": 8},
            {"t": 3.0, "s": 0, "f": 9},
        ],
    ],
)
def test_move_to_next_is_not_offered_for_tap_or_ambiguous_target(next_notes):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 5},
            {"t": 2.0, "s": 0, "f": 5, "ho": True},
            *next_notes,
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert "move_to_next" not in candidate.decision_names


def test_hopo_candidate_pages_are_bounded_disjoint_and_exactly_counted():
    document = {
        "notes": [
            {"t": 1.0, "s": string, "f": 5, "ho": True}
            for string in range(2_005)
        ],
        "chords": [],
    }

    first = repair_eligibility.page_hopo_review_candidates(
        document, offset=0, limit=2_000
    )
    second = repair_eligibility.page_hopo_review_candidates(
        document, offset=2_000, limit=2_000
    )

    assert first.total_count == second.total_count == 2_005
    assert len(first.candidates) == 2_000
    assert len(second.candidates) == 5
    assert {
        candidate.candidate_id for candidate in first.candidates
    }.isdisjoint(candidate.candidate_id for candidate in second.candidates)
