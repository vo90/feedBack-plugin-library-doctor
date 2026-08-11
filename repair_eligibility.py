"""Shared, read-only eligibility checks for conditional automatic repairs.

The scanner and the transactional repair planner both use these predicates.
Keeping them independent from mutation code prevents a scan from promising a
repair that the authoritative planner must later reject.
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Iterator


HANDSHAPE_REVIEW_MESSAGES = {
    "zero_length": (
        "zero_length_handshape_requires_review",
        "At least one zero-length handshape could supply a chord, arpeggio "
        "intent, or additional authoring data. No zero-length handshapes in "
        "this arrangement file will be changed automatically.",
    ),
    "reversed": (
        "reversed_handshape_requires_review",
        "At least one invalid handshape has missing or negative timing, could "
        "supply a chord or arpeggio, lacks one unique playable matching chord, "
        "or contains additional authoring data. No invalid handshapes in this "
        "arrangement file will be changed automatically.",
    ),
}


def _finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _member_path(value) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return None
    path = PurePosixPath(value.strip())
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def preview_source_path(manifest: dict) -> str | None:
    """Match Preview Creator's unambiguous manifest-declared source choice."""
    stems = manifest.get("stems") if isinstance(manifest, dict) else None
    if not isinstance(stems, list):
        return None
    usable = []
    for entry in stems:
        if not isinstance(entry, dict):
            continue
        path = _member_path(entry.get("file"))
        if not path:
            continue
        usable.append(path)
        if entry.get("id") == "full":
            return path
    return usable[0] if len(usable) == 1 else None


def _arrangement_containers(
    document: dict,
) -> Iterator[tuple[tuple[str | int, ...], dict]]:
    yield (), document
    phrases = document.get("phrases")
    if not isinstance(phrases, list):
        return
    for phrase_index, phrase in enumerate(phrases):
        if not isinstance(phrase, dict):
            continue
        levels = phrase.get("levels")
        if not isinstance(levels, list):
            continue
        for level_index, level in enumerate(levels):
            if isinstance(level, dict):
                yield ("phrases", phrase_index, "levels", level_index), level


def reported_zero_length_handshape(value) -> bool:
    return (
        isinstance(value, dict)
        and _finite_number(value.get("start_time"))
        and value["start_time"] >= 0
        and _finite_number(value.get("end_time"))
        and value["end_time"] == value["start_time"]
    )


def reported_invalid_handshape_span(value) -> bool:
    if not isinstance(value, dict):
        return False
    start = value.get("start_time")
    end = value.get("end_time")
    return (
        not _finite_number(start)
        or not _finite_number(end)
        or start < 0
        or end < start
    )


def reported_reversed_handshape(value) -> bool:
    return (
        isinstance(value, dict)
        and _finite_number(value.get("start_time"))
        and value["start_time"] >= 0
        and _finite_number(value.get("end_time"))
        and value["end_time"] < value["start_time"]
    )


def redundant_handshape_is_plain(value, *, span_kind: str) -> bool:
    reported = (
        reported_zero_length_handshape(value)
        if span_kind == "zero_length"
        else reported_reversed_handshape(value)
        if span_kind == "reversed"
        else False
    )
    return bool(
        reported
        and _integer(value.get("chord_id"))
        and value["chord_id"] >= 0
        and value.get("arp", False) is False
        and set(value).issubset({"chord_id", "start_time", "end_time", "arp"})
    )


def chord_matches_handshape(chord, handshape) -> bool:
    return (
        isinstance(chord, dict)
        and _finite_number(chord.get("t"))
        and chord["t"] == handshape["start_time"]
        and _integer(chord.get("id"))
        and chord["id"] == handshape["chord_id"]
    )


def _authoring_flag_enabled(value) -> bool:
    if value is None or value is False or value == 0:
        return False
    if isinstance(value, str) and value.strip().lower() in {
        "", "0", "false", "no", "off",
    }:
        return False
    return True


def strict_reversed_handshape_context(document, handshape, chord) -> bool:
    notes = chord.get("notes") if isinstance(chord, dict) else None
    if not isinstance(notes, list) or not any(
        isinstance(note, dict)
        and _integer(note.get("s"))
        and note["s"] >= 0
        and _integer(note.get("f"))
        and note["f"] >= 0
        for note in notes
    ):
        return False

    chord_id = handshape.get("chord_id")
    templates = document.get("templates") if isinstance(document, dict) else None
    if (
        not _integer(chord_id)
        or chord_id < 0
        or not isinstance(templates, list)
        or chord_id >= len(templates)
        or not isinstance(templates[chord_id], dict)
    ):
        return False
    template = templates[chord_id]
    if _authoring_flag_enabled(template.get("arp")) or _authoring_flag_enabled(
        template.get("arpeggio")
    ):
        return False
    display_name = template.get("displayName")
    if isinstance(display_name, str) and "-arp" in display_name.lower():
        return False
    name = template.get("name")
    if isinstance(name, str):
        normalized = name.lower()
        if normalized.endswith("(arp)") or " arpeggio" in normalized:
            return False
    return True


def assess_redundant_handshapes(document: dict, *, span_kind: str) -> dict:
    """Return exact safe matches and any all-or-nothing author-review blocker."""
    if span_kind not in HANDSHAPE_REVIEW_MESSAGES:
        raise ValueError("unsupported redundant handshape span kind")
    matches = []
    reported_count = 0
    unsafe_count = 0
    for parent_path, container in _arrangement_containers(document):
        handshapes = container.get("handshapes")
        if not isinstance(handshapes, list):
            continue
        chords = container.get("chords")
        for handshape_index, handshape in enumerate(handshapes):
            if span_kind == "zero_length":
                reported = reported_zero_length_handshape(handshape)
            else:
                if not reported_invalid_handshape_span(handshape):
                    continue
                reported = reported_reversed_handshape(handshape)
            if not reported:
                if span_kind == "reversed":
                    reported_count += 1
                    unsafe_count += 1
                continue
            reported_count += 1
            if not redundant_handshape_is_plain(
                handshape, span_kind=span_kind
            ) or not isinstance(chords, list):
                unsafe_count += 1
                continue
            matching = [
                (chord_index, chord)
                for chord_index, chord in enumerate(chords)
                if chord_matches_handshape(chord, handshape)
            ]
            if len(matching) != 1:
                unsafe_count += 1
                continue
            chord_index, chord = matching[0]
            if span_kind == "reversed" and not strict_reversed_handshape_context(
                document, handshape, chord
            ):
                unsafe_count += 1
                continue
            matches.append({
                "parent_path": parent_path,
                "handshape_index": handshape_index,
                "chord_index": chord_index,
                "handshape": handshape,
                "chord": chord,
                "handshape_length": len(handshapes),
                "chord_length": len(chords),
            })
    code, message = HANDSHAPE_REVIEW_MESSAGES[span_kind]
    return {
        "eligible": bool(reported_count and not unsafe_count and matches),
        "reported_count": reported_count,
        "safe_count": len(matches),
        "unsafe_count": unsafe_count,
        "blocker_code": code if unsafe_count else None,
        "message": message if unsafe_count else "",
        "matches": matches,
    }
