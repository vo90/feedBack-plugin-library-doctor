"""Shared, read-only eligibility checks for conditional automatic repairs.

The scanner and the transactional repair planner both use these predicates.
Keeping them independent from mutation code prevents a scan from promising a
repair that the authoritative planner must later reject.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
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


HOPO_REVIEW_RULES = {
    "both_flags": "chart.conflicting-techniques",
    "direction_mismatch": "review.hopo-direction-mismatch",
    "same_fret": "review.same-fret-hopo",
    "no_usable_predecessor": "review.hopo-without-source",
}

HOPO_DECISION_NAMES = (
    "set_hammer_on",
    "set_pull_off",
    "convert_to_tap",
    "remove_hopo",
    "leave_unchanged",
)
MAX_HOPO_REVIEW_CANDIDATES = 2_000


@dataclass(frozen=True)
class HopoNeighbour:
    """One unambiguous adjacent same-string onset used as review evidence."""

    time: float
    fret: int
    location: str
    path: tuple[str | int, ...] | None
    writable: bool
    hammer_on: bool
    pull_off: bool
    tap: bool
    malformed_techniques: bool

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "fret": self.fret,
            "location": self.location,
            "writable": self.writable,
            "techniques": {
                "hammer_on": self.hammer_on,
                "pull_off": self.pull_off,
                "tap": self.tap,
            },
            "malformed_techniques": self.malformed_techniques,
        }


@dataclass(frozen=True)
class HopoReviewCandidate:
    """Mutation-free evidence for one authored HO/PO decision.

    Paths are retained for an authoritative repair planner. Public consumers
    receive only stable identifiers and display evidence through ``to_dict``.
    """

    candidate_id: str
    member_path: str
    stream_path: tuple[str | int, ...]
    target_path: tuple[str | int, ...]
    location: str
    context_kind: str
    time: float
    string: int
    fret: int
    hammer_on: bool
    pull_off: bool
    tap: bool
    reasons: tuple[str, ...]
    trigger_codes: tuple[str, ...]
    previous: HopoNeighbour | None
    next: HopoNeighbour | None
    predecessor_state: str
    next_state: str
    blockers: tuple[str, ...]
    decision_names: tuple[str, ...]

    def to_dict(self) -> dict:
        stream_label = "Top-level arrangement"
        if (
            len(self.stream_path) == 4
            and self.stream_path[0] == "phrases"
            and isinstance(self.stream_path[1], int)
            and self.stream_path[2] == "levels"
            and isinstance(self.stream_path[3], int)
        ):
            stream_label = (
                f"Phrase {self.stream_path[1] + 1}, "
                f"difficulty {self.stream_path[3] + 1}"
            )
        result = {
            "candidate_id": self.candidate_id,
            "location": self.location,
            "member_path": self.member_path,
            "context_kind": self.context_kind,
            "stream": stream_label,
            "time": self.time,
            "string": self.string,
            "fret": self.fret,
            "techniques": {
                "hammer_on": self.hammer_on,
                "pull_off": self.pull_off,
                "tap": self.tap,
            },
            "reasons": list(self.reasons),
            "trigger_codes": list(self.trigger_codes),
            "predecessor_state": self.predecessor_state,
            "next_state": self.next_state,
            "previous": self.previous.to_dict() if self.previous else None,
            "next": self.next.to_dict() if self.next else None,
            "blockers": list(self.blockers),
            "decision_names": list(self.decision_names),
            "previous_gap_seconds": (
                round(self.time - self.previous.time, 4)
                if self.previous else None
            ),
            "next_gap_seconds": (
                round(self.next.time - self.time, 4)
                if self.next else None
            ),
            "outgoing_match": bool(
                self.next
                and (
                    (self.hammer_on and self.fret < self.next.fret)
                    or (self.pull_off and self.fret > self.next.fret)
                )
            ),
        }
        return result


@dataclass(frozen=True)
class HopoReviewPage:
    """One bounded candidate page plus exact package-planning counts."""

    candidates: tuple[HopoReviewCandidate, ...]
    total_count: int
    blocked_count: int
    offset: int
    limit: int


@dataclass(frozen=True)
class _HopoEvent:
    time: float
    time_tick: int
    string: int
    fret: int
    location: str
    path: tuple[str | int, ...] | None
    writable: bool
    hammer_on: bool
    pull_off: bool
    tap: bool
    malformed_techniques: bool
    context_kind: str


def _hopo_number(value) -> float | None:
    if not _finite_number(value):
        return None
    return float(value)


def _hopo_integer(value) -> int | None:
    if not _integer(value):
        return None
    return value


def _display_path(member_path: str, path: tuple[str | int, ...]) -> str:
    rendered = member_path
    for index, part in enumerate(path):
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += (":" if rendered and index == 0 else "." if rendered else "") + part
    return rendered or "arrangement"


def _candidate_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "hopo-" + hashlib.sha256(encoded).hexdigest()[:24]


def _hopo_events_for_container(
    document: dict,
    container: dict,
    parent_path: tuple[str | int, ...],
    member_path: str,
) -> list[_HopoEvent]:
    events: list[_HopoEvent] = []

    def add_event(
        raw,
        *,
        time_value,
        path: tuple[str | int, ...] | None,
        location_path: tuple[str | int, ...],
        writable: bool,
        context_kind: str,
    ) -> None:
        if not isinstance(raw, dict):
            return
        event_time = _hopo_number(time_value)
        string = _hopo_integer(raw.get("s"))
        fret = _hopo_integer(raw.get("f"))
        if (
            event_time is None
            or string is None
            or string < 0
            or fret is None
            or fret < 0
        ):
            return
        technique_values = [raw.get(field) for field in ("ho", "po", "tp")]
        malformed = any(
            field in raw and not isinstance(raw.get(field), bool)
            for field in ("ho", "po", "tp")
        )
        events.append(_HopoEvent(
            time=event_time,
            time_tick=round(event_time * 10_000),
            string=string,
            fret=fret,
            location=_display_path(member_path, location_path),
            path=path,
            writable=writable,
            hammer_on=technique_values[0] is True,
            pull_off=technique_values[1] is True,
            tap=technique_values[2] is True,
            malformed_techniques=malformed,
            context_kind=context_kind,
        ))

    notes = container.get("notes")
    if isinstance(notes, list):
        for note_index, note in enumerate(notes):
            note_path = parent_path + ("notes", note_index)
            add_event(
                note,
                time_value=note.get("t") if isinstance(note, dict) else None,
                path=note_path,
                location_path=note_path,
                writable=True,
                context_kind="standalone_note",
            )

    chords = container.get("chords")
    templates = document.get("templates")
    if not isinstance(chords, list):
        return events
    for chord_index, chord in enumerate(chords):
        if not isinstance(chord, dict):
            continue
        chord_path = parent_path + ("chords", chord_index)
        chord_time = chord.get("t")
        chord_notes = chord.get("notes")
        if isinstance(chord_notes, list) and chord_notes:
            for note_index, note in enumerate(chord_notes):
                note_path = chord_path + ("notes", note_index)
                add_event(
                    note,
                    time_value=chord_time,
                    path=note_path,
                    location_path=note_path,
                    writable=True,
                    context_kind="chord_member",
                )
            continue
        template_id = _hopo_integer(chord.get("id"))
        template = (
            templates[template_id]
            if isinstance(templates, list)
            and template_id is not None
            and 0 <= template_id < len(templates)
            and isinstance(templates[template_id], dict)
            else None
        )
        frets = template.get("frets") if isinstance(template, dict) else None
        if not isinstance(frets, list):
            continue
        for string, raw_fret in enumerate(frets):
            fret = _hopo_integer(raw_fret)
            if fret is None or fret < 0:
                continue
            add_event(
                {"s": string, "f": fret},
                time_value=chord_time,
                path=None,
                location_path=chord_path,
                writable=False,
                context_kind="chord_template",
            )
    return events


def _neighbour(
    onset: list[_HopoEvent] | None,
) -> tuple[HopoNeighbour | None, str]:
    if not onset:
        return None, "missing"
    frets = {event.fret for event in onset}
    if len(frets) != 1:
        return None, "ambiguous"
    # Multiple authored events at an onset can still provide unambiguous fret
    # evidence. It is writable only when exactly one explicit target exists.
    writable = [event for event in onset if event.writable and event.path is not None]
    representative = onset[0]
    path = writable[0].path if len(writable) == 1 else None
    location = writable[0].location if len(writable) == 1 else representative.location
    return HopoNeighbour(
        time=representative.time,
        fret=representative.fret,
        location=location,
        path=path,
        writable=len(writable) == 1,
        hammer_on=representative.hammer_on,
        pull_off=representative.pull_off,
        tap=representative.tap,
        malformed_techniques=representative.malformed_techniques,
    ), "usable"


def find_hopo_review_candidates(
    document: dict,
    *,
    member_path: str = "",
    max_candidates: int = MAX_HOPO_REVIEW_CANDIDATES,
) -> list[HopoReviewCandidate]:
    """Classify authored HO/PO ambiguity without changing the document.

    Each top-level arrangement and phrase difficulty is an independent stream.
    The immediately preceding same-string onset determines incoming HO/PO
    direction. The next onset is deliberately evidence only and long gaps do
    not suppress a candidate.
    """
    page = page_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=0,
        limit=max_candidates,
    )
    return list(page.candidates)


def page_hopo_review_candidates(
    document: dict,
    *,
    member_path: str = "",
    offset: int = 0,
    limit: int = MAX_HOPO_REVIEW_CANDIDATES,
) -> HopoReviewPage:
    """Scan every candidate while retaining only one deterministic page."""
    return _scan_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=offset,
        limit=limit,
        selected_ids=None,
    )


def select_hopo_review_candidates(
    document: dict,
    *,
    member_path: str = "",
    candidate_ids: set[str] | frozenset[str],
) -> HopoReviewPage:
    """Retain only requested server IDs while still counting every candidate."""
    return _scan_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=0,
        limit=MAX_HOPO_REVIEW_CANDIDATES,
        selected_ids=frozenset(candidate_ids),
    )


def _scan_hopo_review_candidates(
    document: dict,
    *,
    member_path: str,
    offset: int,
    limit: int,
    selected_ids: frozenset[str] | None,
) -> HopoReviewPage:
    if (
        not isinstance(document, dict)
        or not _integer(offset)
        or offset < 0
        or not _integer(limit)
        or limit < 1
    ):
        return HopoReviewPage((), 0, 0, max(offset, 0) if _integer(offset) else 0, 0)
    candidates: list[HopoReviewCandidate] = []
    total_count = 0
    blocked_count = 0
    for parent_path, container in _arrangement_containers(document):
        events = _hopo_events_for_container(
            document, container, parent_path, member_path
        )
        lanes: dict[int, dict[int, list[_HopoEvent]]] = {}
        for event in events:
            lanes.setdefault(event.string, {}).setdefault(
                event.time_tick, []
            ).append(event)

        for string, onsets in sorted(lanes.items()):
            ticks = sorted(onsets)
            for tick_index, tick in enumerate(ticks):
                current_onset = onsets[tick]
                previous, predecessor_state = _neighbour(
                    onsets[ticks[tick_index - 1]] if tick_index else None
                )
                next_note, next_state = _neighbour(
                    onsets[ticks[tick_index + 1]]
                    if tick_index + 1 < len(ticks)
                    else None
                )
                current_frets = {event.fret for event in current_onset}
                for event in current_onset:
                    if not event.writable or event.path is None:
                        continue
                    if not (event.hammer_on or event.pull_off):
                        continue
                    reasons: list[str] = []
                    blockers: list[str] = []
                    if event.hammer_on and event.pull_off:
                        reasons.append("both_flags")
                    if len(current_frets) > 1:
                        blockers.append("same_time_string_conflict")
                    if predecessor_state == "ambiguous":
                        blockers.append("ambiguous_predecessor")
                    if event.malformed_techniques:
                        blockers.append("malformed_technique_value")
                    if predecessor_state != "usable" or previous is None:
                        reasons.append("no_usable_predecessor")
                    elif previous.fret == event.fret:
                        reasons.append("same_fret")
                    elif (
                        event.hammer_on
                        and not event.pull_off
                        and previous.fret > event.fret
                    ) or (
                        event.pull_off
                        and not event.hammer_on
                        and previous.fret < event.fret
                    ):
                        reasons.append("direction_mismatch")
                    if not reasons:
                        continue

                    decisions = list(HOPO_DECISION_NAMES)
                    can_move = bool(
                        next_note is not None
                        and next_note.writable
                        and next_note.path is not None
                        and next_note.fret != event.fret
                        and not next_note.malformed_techniques
                        and not next_note.hammer_on
                        and not next_note.pull_off
                        and not next_note.tap
                        and (
                            (event.hammer_on and event.fret < next_note.fret)
                            or (event.pull_off and event.fret > next_note.fret)
                        )
                    )
                    if can_move:
                        decisions.insert(-1, "move_to_next")
                    trigger_codes = tuple(
                        dict.fromkeys(HOPO_REVIEW_RULES[reason] for reason in reasons)
                    )
                    identity = {
                        "member_path": member_path,
                        "stream_path": list(parent_path),
                        "target_path": list(event.path),
                        "time": event.time,
                        "string": string,
                        "fret": event.fret,
                        "ho": event.hammer_on,
                        "po": event.pull_off,
                        "tp": event.tap,
                        "previous": (
                            [previous.time, previous.fret, predecessor_state]
                            if previous else [None, None, predecessor_state]
                        ),
                        "next": (
                            [next_note.time, next_note.fret, next_state]
                            if next_note else [None, None, next_state]
                        ),
                    }
                    candidate = HopoReviewCandidate(
                        candidate_id=_candidate_digest(identity),
                        member_path=member_path,
                        stream_path=parent_path,
                        target_path=event.path,
                        location=event.location,
                        context_kind=event.context_kind,
                        time=event.time,
                        string=string,
                        fret=event.fret,
                        hammer_on=event.hammer_on,
                        pull_off=event.pull_off,
                        tap=event.tap,
                        reasons=tuple(reasons),
                        trigger_codes=trigger_codes,
                        previous=previous,
                        next=next_note,
                        predecessor_state=predecessor_state,
                        next_state=next_state,
                        blockers=tuple(blockers),
                        decision_names=tuple(decisions),
                    )
                    if candidate.blockers:
                        blocked_count += 1
                    if selected_ids is not None:
                        if candidate.candidate_id in selected_ids:
                            candidates.append(candidate)
                    elif total_count >= offset and len(candidates) < limit:
                        candidates.append(candidate)
                    total_count += 1
    return HopoReviewPage(
        candidates=tuple(candidates),
        total_count=total_count,
        blocked_count=blocked_count,
        offset=offset,
        limit=limit,
    )


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
