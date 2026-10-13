"""Closed, source-bound operations for lyric ordering and phrase endings.

The caller supplies the shared canonicalization, action and error types. This
module only plans or changes an in-memory document; package I/O, backup and
transaction handling remain in the repair service.
"""

from __future__ import annotations

import copy
import hashlib


def plan_line_breaks(document: list, *, assess, digest_json, action_type, error_type) -> list:
    assessment = assess(document)
    if assessment["status"] == "blocked":
        raise error_type(assessment["blocker_code"], assessment["message"])
    if not assessment["eligible"]:
        return []
    indices = tuple(assessment["append_plus_indices"])
    repaired = copy.deepcopy(document)
    for index in indices:
        repaired[index]["w"] += "+"
    return [action_type(
        expected_length=len(document),
        original_sha256=digest_json(document),
        repaired_sha256=digest_json(repaired),
        cue_indices=indices,
    )]


def apply_line_breaks(document, operation: dict, *, assess, digest_json,
                      canonical_json, action_type, error_type) -> None:
    # Rebuild the complete closed operation from current content. Comparing
    # canonical bytes also rejects bool/int aliases and extra instructions.
    if not isinstance(document, list) or operation.get("original_sha256") != digest_json(document):
        raise error_type(
            "source_changed",
            "The lyrics changed after this preview. Review the safe fix again before applying it.",
        )
    expected = plan_line_breaks(
        document, assess=assess, digest_json=digest_json,
        action_type=action_type, error_type=error_type,
    )
    if len(expected) != 1 or canonical_json(operation) != canonical_json(expected[0].to_dict()):
        raise error_type("invalid_plan", "The repair preview is invalid.")
    for index in expected[0].cue_indices:
        document[index]["w"] += "+"


def plan_cue_order(document: list, *, finite_number, canonical_json,
                   action_type, error_type) -> list:
    parsed_times = [
        cue.get("t")
        for cue in document
        if isinstance(cue, dict) and finite_number(cue.get("t"))
    ]
    if not any(
        current < previous
        for previous, current in zip(parsed_times, parsed_times[1:])
    ):
        return []

    if not all(
        isinstance(cue, dict)
        and finite_number(cue.get("t"))
        and finite_number(cue.get("d"))
        and isinstance(cue.get("w"), str)
        for cue in document
    ):
        raise error_type(
            "invalid_lyric_timeline",
            "The out-of-order lyric timeline also contains an invalid cue, so "
            "Library Doctor will not guess how to reorder it.",
        )

    sorted_indices = tuple(sorted(
        range(len(document)), key=lambda index: document[index]["t"]
    ))
    moved_count = sum(
        index != original_index
        for index, original_index in enumerate(sorted_indices)
    )
    if not moved_count:
        return []
    sorted_cues = [document[index] for index in sorted_indices]
    return [action_type(
        expected_length=len(document),
        original_sha256=hashlib.sha256(canonical_json(document)).hexdigest(),
        sorted_sha256=hashlib.sha256(canonical_json(sorted_cues)).hexdigest(),
        sorted_indices=sorted_indices,
        moved_count=moved_count,
    )]


def apply_cue_order(document, operation: dict, reordered: set[tuple[str | int, ...]],
                    *, finite_number, integer, canonical_json, error_type) -> None:
    if operation.get("array_path") != [] or () in reordered:
        raise error_type("invalid_plan", "The repair preview is invalid.")
    if (
        not isinstance(document, list)
        or operation.get("expected_length") != len(document)
        or len(document) < 2
    ):
        raise error_type(
            "source_changed",
            "The lyrics changed after this preview. Review the safe fix again before applying it.",
        )
    if not all(
        isinstance(cue, dict)
        and finite_number(cue.get("t"))
        and finite_number(cue.get("d"))
        and isinstance(cue.get("w"), str)
        for cue in document
    ):
        raise error_type(
            "source_changed",
            "The lyrics changed after this preview. Review the safe fix again before applying it.",
        )
    original_digest = hashlib.sha256(canonical_json(document)).hexdigest()
    if operation.get("original_sha256") != original_digest:
        raise error_type(
            "source_changed",
            "The lyrics changed after this preview. Review the safe fix again before applying it.",
        )
    sorted_indices = list(sorted(
        range(len(document)), key=lambda index: document[index]["t"]
    ))
    declared_indices = operation.get("sorted_indices")
    moved_count = sum(
        index != original_index
        for index, original_index in enumerate(sorted_indices)
    )
    if (
        not moved_count
        or not isinstance(declared_indices, list)
        or any(not integer(index) for index in declared_indices)
        or declared_indices != sorted_indices
        or not integer(operation.get("moved_count"))
        or operation["moved_count"] != moved_count
    ):
        raise error_type("invalid_plan", "The repair preview is invalid.")
    sorted_cues = [document[index] for index in sorted_indices]
    sorted_digest = hashlib.sha256(canonical_json(sorted_cues)).hexdigest()
    if operation.get("sorted_sha256") != sorted_digest:
        raise error_type("invalid_plan", "The repair preview is invalid.")
    document[:] = sorted_cues
    reordered.add(())
