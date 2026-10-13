import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def phrases():
    name = "library_doctor_lyric_phrases_tests"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "lyric_phrases.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _tokens(count, *, start=0.0, step=0.1, duration=0.05, text="word"):
    return [
        {
            "t": round(start + index * step, 6),
            "d": duration,
            "w": text,
            "future": {"original_index": index, "labels": ["unchanged", "✓"]},
        }
        for index in range(count)
    ]


def _apply(document, plan):
    result = copy.deepcopy(document)
    for index in plan["append_plus_indices"]:
        result[index]["w"] += "+"
    return result


def _assert_safe_boundaries(document, indices):
    for index in indices:
        assert not document[index]["w"].endswith(("+", "-"))
        if index + 1 < len(document):
            assert document[index + 1]["t"] > document[index]["t"]
            assert document[index + 1]["t"] >= max(
                token["t"] + token["d"] for token in document[:index + 1]
            )


def test_plan_is_pure_and_only_appends_markers_without_retiming(phrases):
    document = _tokens(96, step=0.6, duration=0.2)
    original = copy.deepcopy(document)
    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)
    identities = [id(token) for token in document]

    plan = phrases.assess_lyric_phrases(document)

    assert plan["status"] == "eligible"
    assert plan["detected"] and plan["eligible"]
    assert plan["blocker_code"] is None
    assert plan["append_plus_indices"] == list(range(7, 96, 8))
    assert plan["original_group_count"] == 1
    assert plan["planned_group_count"] == 12
    assert document == original
    assert [id(token) for token in document] == identities
    assert json.dumps(document, ensure_ascii=False, sort_keys=True) == serialized
    _assert_safe_boundaries(document, plan["append_plus_indices"])

    repaired = _apply(document, plan)
    selected = set(plan["append_plus_indices"])
    for index, (before, after) in enumerate(zip(original, repaired)):
        assert list(after) == list(before)
        assert after == dict(before, w=before["w"] + ("+" if index in selected else ""))
    assert phrases.assess_lyric_phrases(repaired)["status"] == "no_defect"


def test_final_short_remainder_is_marked_and_repeat_has_no_edits(phrases):
    document = _tokens(81, text="a")
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert plan["append_plus_indices"] == list(range(7, 80, 8)) + [80]
    repaired = _apply(document, plan)
    repeated = phrases.assess_lyric_phrases(repaired)
    assert repeated["status"] == "no_defect"
    assert repeated["append_plus_indices"] == []
    assert repeated["original_group_count"] == plan["planned_group_count"] == 11


@pytest.mark.parametrize("document", [[], _tokens(16), _tokens(65, step=0.5)])
def test_valid_tracks_without_severe_warning_are_untouched(phrases, document):
    before = copy.deepcopy(document)
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "no_defect"
    assert not plan["eligible"] and not plan["detected"]
    assert plan["append_plus_indices"] == []
    assert document == before


def test_severe_track_uses_full_core_fallback_but_preserves_authored_lines(phrases):
    authored = _tokens(20)
    authored[-1]["w"] += "+"
    unmarked = _tokens(20, start=10)
    severe = _tokens(81, start=20)
    document = authored + unmarked + severe

    plan = phrases.assess_lyric_phrases(document)

    assert plan["status"] == "eligible"
    assert plan["original_group_count"] == 3
    assert plan["planned_group_count"] == 15
    assert all(index >= 20 for index in plan["append_plus_indices"])
    assert plan["append_plus_indices"][:3] == [27, 35, 39]
    assert _apply(document, plan)[:20] == authored
    assert phrases.assess_lyric_phrases(_apply(document, plan))["status"] == "no_defect"


def test_unsplit_healthy_final_tail_does_not_gain_marker(phrases):
    document = _tokens(81) + _tokens(3, start=20)
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert max(plan["append_plus_indices"]) == 80
    assert _apply(document, plan)[81:] == document[81:]


def test_healthy_authored_threshold_is_preserved_and_excess_is_repaired(phrases):
    healthy = _tokens(80, text="x")
    healthy[-1]["w"] += "+"
    assert phrases.assess_lyric_phrases(healthy)["status"] == "no_defect"
    excessive = _tokens(81, text="x")
    excessive[-1]["w"] += "+"
    plan = phrases.assess_lyric_phrases(excessive)
    assert plan["status"] == "eligible"
    assert 80 not in plan["append_plus_indices"]
    assert _apply(excessive, plan)[-1]["w"] == "x+"


def test_duration_warning_uses_true_end_and_target_seconds(phrases):
    document = _tokens(72, step=1, duration=1, text="x")
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert plan["append_plus_indices"] == list(range(5, 72, 6))


def test_whole_hyphenated_word_stays_together(phrases):
    document = _tokens(82, text="a")
    document[7]["w"] = "part-"
    document[8]["w"] = "end"
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert 7 not in plan["append_plus_indices"]
    assert 8 in plan["append_plus_indices"]
    _assert_safe_boundaries(document, plan["append_plus_indices"])


def test_standalone_control_cues_keep_exact_text_timing_and_join_semantics(phrases):
    document = _tokens(81, text="a")
    document[7]["w"] = "-"
    document.append({"t": 8.2, "d": 0, "w": "+", "future": "control"})
    before = copy.deepcopy(document)
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert 7 not in plan["append_plus_indices"]
    assert 8 in plan["append_plus_indices"]
    assert 81 not in plan["append_plus_indices"]
    after = _apply(document, plan)
    assert after[7] == before[7]
    assert after[-1] == before[-1]
    assert document == before
    assert phrases.assess_lyric_phrases(after)["status"] == "no_defect"


def test_hyphen_join_is_not_broken_by_long_silence(phrases):
    document = _tokens(81, text="a-")
    document[-1]["w"] = "end"
    for token in document[40:]:
        token["t"] += 10
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "blocked"
    assert plan["blocker_code"] == "indivisible-lyric-phrase"
    assert plan["original_group_count"] == 1
    assert plan["append_plus_indices"] == []


def test_unfinished_join_blocks_whole_plan_instead_of_partial_repair(phrases):
    document = _tokens(96)
    document[-1]["w"] = "unfinished-"
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "blocked"
    assert plan["blocker_code"] == "unfinished-lyric-word"
    assert plan["append_plus_indices"] == []


@pytest.mark.parametrize("case", ["held_word", "continuous_overlap", "equal_starts"])
def test_indivisible_overlap_or_equal_start_block_is_not_false_success(phrases, case):
    document = _tokens(81)
    if case == "held_word":
        document[0]["d"] = 100
    elif case == "continuous_overlap":
        for token in document:
            token["d"] = 0.2
    else:
        for token in document:
            token["t"] = 0
            token["d"] = 0
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "blocked"
    assert plan["detected"] and not plan["eligible"]
    assert plan["blocker_code"] == "indivisible-lyric-phrase"
    assert plan["append_plus_indices"] == []


def test_zero_duration_equal_start_cues_never_get_separated(phrases):
    document = _tokens(81, duration=0, text="a")
    document[8]["t"] = document[7]["t"]
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert 7 not in plan["append_plus_indices"]
    assert 8 in plan["append_plus_indices"]
    _assert_safe_boundaries(document, plan["append_plus_indices"])


def test_small_overlap_moves_boundary_to_first_safe_whole_word(phrases):
    document = _tokens(81, duration=0, text="a")
    document[7]["d"] = 0.25
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert plan["append_plus_indices"][0] == 9
    _assert_safe_boundaries(document, plan["append_plus_indices"])


def test_running_end_prevents_false_silence_while_earlier_word_sounds(phrases):
    document = _tokens(81, step=0.1)
    document[0]["d"] = 100
    for token in document[40:]:
        token["t"] += 10
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "blocked"
    assert plan["original_group_count"] == 1


def test_existing_authored_overlap_does_not_allow_new_overlap_boundary(phrases):
    authored = [{"t": 0, "d": 30, "w": "held+"}]
    document = authored + _tokens(90, start=1, step=0.5, text="a")
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert plan["append_plus_indices"][0] == 58
    _assert_safe_boundaries(document, plan["append_plus_indices"])
    assert _apply(document, plan)[0] == authored[0]


def test_natural_pause_and_punctuation_choose_safe_shorter_phrases(phrases):
    document = _tokens(81, duration=0.01, text="a")
    document[2]["w"] = "a."
    for token in document[6:]:
        token["t"] += 0.5
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert plan["append_plus_indices"][:2] == [2, 5]
    _assert_safe_boundaries(document, plan["append_plus_indices"])


def test_unicode_visible_character_counts_match_javascript_utf16(phrases):
    assert phrases.visible_character_count("😀+") == 2
    assert phrases.visible_character_count("😀-+") == 3
    assert phrases.visible_character_count("e\u0301-") == 2
    assert phrases.visible_character_count(" word ") == 6
    assert phrases.visible_character_count("a++") == 2
    document = _tokens(51, text="😀" * 5)
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "eligible"
    assert plan["append_plus_indices"] == list(range(5, 48, 6)) + [50]
    assert all(token["w"] == "😀" * 5 for token in document)


@pytest.mark.parametrize("text,duration", [("x" * 501, 1), ("😀" * 251, 1), ("held", 46)])
def test_one_oversized_word_is_unrepairable(phrases, text, duration):
    plan = phrases.assess_lyric_phrases([{"t": 0, "d": duration, "w": text}])
    assert plan["status"] == "blocked"
    assert plan["blocker_code"] == "indivisible-lyric-phrase"
    assert plan["append_plus_indices"] == []


@pytest.mark.parametrize("document", [
    None, {}, (), "lyrics", [None], ["token"], [{}],
    [{"t": 0, "d": 1}],
    [{"t": "0", "d": 1, "w": "a"}],
    [{"t": True, "d": 1, "w": "a"}],
    [{"t": 0, "d": False, "w": "a"}],
    [{"t": -1, "d": 1, "w": "a"}],
    [{"t": 0, "d": -1, "w": "a"}],
    [{"t": float("nan"), "d": 1, "w": "a"}],
    [{"t": 0, "d": float("inf"), "w": "a"}],
    [{"t": 1e308, "d": 1e308, "w": "a"}],
    [{"t": 10 ** 1000, "d": 1, "w": "a"}],
    [{"t": 0, "d": 1, "w": None}],
    [{"t": 0, "d": 1, "w": ""}],
    [{"t": 0, "d": 1, "w": "  "}],
    [{"t": 1, "d": 1, "w": "a"}, {"t": 0, "d": 1, "w": "b"}],
])
def test_malformed_or_unordered_document_is_blocked_before_any_proposal(phrases, document):
    plan = phrases.assess_lyric_phrases(document)
    assert plan["status"] == "blocked"
    assert not plan["eligible"]
    assert plan["blocker_code"] == "invalid-lyric-document"
    assert plan["append_plus_indices"] == []
