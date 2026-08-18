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


def test_hopo_difficulty_scope_uses_runtime_levels_and_keeps_lower_counts():
    document = {
        # FeedBack uses the phrase ladder while one exists, so this authored
        # root copy must not create a duplicate review candidate.
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "ho": True, "po": True},
        ],
        "chords": [],
        "phrases": [{
            "levels": [
                {
                    "notes": [
                        {"t": 1.0, "s": 0, "f": 3},
                        {"t": 2.0, "s": 0, "f": 5, "po": True},
                    ],
                    "chords": [],
                },
                {
                    "notes": [
                        {"t": 1.0, "s": 0, "f": 7},
                        {"t": 2.0, "s": 0, "f": 5, "ho": True},
                    ],
                    "chords": [],
                },
            ],
        }],
    }

    all_authored = repair_eligibility.page_hopo_review_candidates(
        document, difficulty_scope="all_authored"
    )
    full_only = repair_eligibility.page_hopo_review_candidates(
        document, difficulty_scope="full_only"
    )

    assert all_authored.total_count == 2
    assert all_authored.full_candidate_count == 1
    assert all_authored.lower_candidate_count == 1
    assert [
        candidate.to_dict()["stream_context"]["difficulty_scope"]
        for candidate in all_authored.candidates
    ] == ["lower", "full"]
    assert all_authored.candidates[0].to_dict()["stream_context"][
        "mastery_fraction"
    ] == 0.25
    assert all_authored.candidates[1].to_dict()["stream_context"][
        "mastery_fraction"
    ] == 1.0
    assert full_only.total_count == 1
    assert full_only.full_candidate_count == 1
    assert full_only.lower_candidate_count == 1
    assert len(full_only.candidates) == 1
    assert full_only.candidates[0].stream_is_full_difficulty is True


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


def test_link_next_same_fret_partial_chord_continuation_is_not_a_hopo_issue():
    document = {
        "chords": [{
            "t": 19.756001,
            "notes": [
                {"s": 1, "f": 0, "sus": 0.207, "ln": True},
                {"s": 2, "f": 0, "sus": 0.207, "ln": True},
                {"s": 3, "f": 1, "sus": 0.207, "ln": True},
            ],
        }],
        "notes": [
            {"t": 19.962999, "s": 1, "f": 2, "ho": True},
            {"t": 19.962999, "s": 2, "f": 2, "ho": True},
            {"t": 19.962999, "s": 3, "f": 1, "ho": True},
        ],
    }

    assert repair_eligibility.find_hopo_review_candidates(document) == []


@pytest.mark.parametrize(
    ("source_kind", "target_kind"),
    [
        ("standalone", "standalone"),
        ("standalone", "chord_member"),
        ("chord_member", "standalone"),
        ("chord_member", "chord_member"),
    ],
)
def test_link_next_matching_pitched_slide_destination_is_not_a_hopo_issue(
    source_kind,
    target_kind,
):
    document = {"notes": [], "chords": []}
    source = {
        "s": 0,
        "f": 3,
        "sus": 1.0,
        "sl": 7,
        "ln": True,
    }
    target = {"s": 0, "f": 7, "ho": True}
    if source_kind == "standalone":
        document["notes"].append({"t": 1.0, **source})
    else:
        document["chords"].append({"t": 1.0, "notes": [source]})
    if target_kind == "standalone":
        document["notes"].append({"t": 2.0, **target})
    else:
        document["chords"].append({"t": 2.0, "notes": [target]})

    assert repair_eligibility.find_hopo_review_candidates(document) == []


@pytest.mark.parametrize(
    ("target_fret", "technique", "expected_reasons"),
    [
        (5, {"ho": True}, ("direction_mismatch",)),
        (5, {"po": True}, None),
        (7, {"ho": True}, ("same_fret",)),
    ],
)
def test_pitched_slide_landing_fret_drives_incoming_hopo_direction(
    target_fret,
    technique,
    expected_reasons,
):
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3, "sus": 1.0, "sl": 7},
            {"t": 2.0, "s": 0, "f": target_fret, **technique},
        ],
        "chords": [],
    }

    candidates = repair_eligibility.find_hopo_review_candidates(document)

    if expected_reasons is None:
        assert candidates == []
        return
    assert len(candidates) == 1
    assert candidates[0].reasons == expected_reasons
    assert candidates[0].previous.fret == 3
    assert candidates[0].previous.effective_fret == 7
    assert candidates[0].previous.to_dict()["techniques"]["slide_to"] == 7


def test_unpitched_slide_does_not_claim_an_exact_link_next_destination():
    document = {
        "notes": [
            {
                "t": 1.0,
                "s": 0,
                "f": 3,
                "sus": 1.0,
                "slu": 7,
                "ln": True,
            },
            {"t": 2.0, "s": 0, "f": 7, "po": True},
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.reasons == ("direction_mismatch",)
    assert candidate.previous.effective_fret == 3
    assert candidate.previous.slide_unpitch_to == 7


@pytest.mark.parametrize("link_value", [None, "true", 1])
def test_same_fret_transition_without_strict_link_next_remains_reviewable(
    link_value,
):
    source = {"t": 1.0, "s": 0, "f": 5}
    if link_value is not None:
        source["ln"] = link_value
    document = {
        "notes": [
            source,
            {"t": 2.0, "s": 0, "f": 5, "ho": True},
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.reasons == ("same_fret",)


def test_link_next_does_not_hide_conflicting_or_changed_fret_hopo_issues():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 5, "ln": True},
            {"t": 2.0, "s": 0, "f": 5, "ho": True, "po": True},
            {"t": 1.0, "s": 1, "f": 3, "ln": True, "sl": 6},
            {"t": 2.0, "s": 1, "f": 7, "po": True},
            {"t": 1.0, "s": 2, "f": 3, "ln": True, "sl": 7},
            {"t": 2.0, "s": 2, "f": 7, "ho": True, "po": True},
        ],
        "chords": [],
    }

    candidates = repair_eligibility.find_hopo_review_candidates(document)

    assert candidates[0].reasons == ("both_flags",)
    assert candidates[1].reasons == ("direction_mismatch",)
    assert candidates[2].reasons == ("both_flags",)


def test_hopo_path_selection_retains_only_exact_mutable_targets():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
            {"t": 1.0, "s": 1, "f": 7, "ho": True},
        ],
        "chords": [],
    }

    selected = repair_eligibility.select_hopo_review_candidates_at_paths(
        document,
        target_paths=frozenset({("notes", 1)}),
    )

    assert selected.total_count == 2
    assert [item.target_path for item in selected.candidates] == [("notes", 1)]


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


@pytest.mark.parametrize(
    "coincident_chord",
    [
        {"t": 2.0, "notes": [{"s": 0, "f": 5}]},
        {"t": 2.0, "id": 0},
    ],
)
def test_hopo_candidate_marks_coincident_visual_representations_ambiguous(
    coincident_chord,
):
    document = {
        "templates": [{"frets": [5, -1, -1, -1, -1, -1]}],
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
        ],
        "chords": [coincident_chord],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.visual_target_ambiguous is True
    assert candidate.to_dict()["visual_target_ambiguous"] is True
    assert candidate.blockers == ()
    assert candidate.decision_names


def test_hopo_candidate_marks_unique_visual_representation_unambiguous():
    document = {
        "notes": [
            {"t": 1.0, "s": 0, "f": 3},
            {"t": 2.0, "s": 0, "f": 5, "po": True},
        ],
        "chords": [],
    }

    candidate = repair_eligibility.find_hopo_review_candidates(document)[0]

    assert candidate.visual_target_ambiguous is False
    assert candidate.to_dict()["visual_target_ambiguous"] is False


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
