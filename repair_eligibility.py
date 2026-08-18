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
from typing import Callable, Iterator


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
)
MAX_HOPO_REVIEW_CANDIDATES = 2_000
REVIEW_DIFFICULTY_SCOPES = frozenset({"full_only", "all_authored"})


@dataclass(frozen=True)
class HopoNeighbour:
    """One unambiguous adjacent same-string onset used as review evidence."""

    time: float
    fret: int
    effective_fret: int
    sustain: float
    link_next: bool
    slide_to: int | None
    slide_unpitch_to: int | None
    location: str
    path: tuple[str | int, ...] | None
    writable: bool
    hammer_on: bool
    pull_off: bool
    tap: bool
    malformed_techniques: bool
    context_kind: str

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "fret": self.fret,
            "effective_fret": self.effective_fret,
            "sustain": self.sustain,
            "sustain_end": round(self.time + self.sustain, 4),
            "location": self.location,
            "writable": self.writable,
            "techniques": {
                "hammer_on": self.hammer_on,
                "pull_off": self.pull_off,
                "tap": self.tap,
                "link_next": self.link_next,
                "slide_to": self.slide_to,
                "slide_unpitch_to": self.slide_unpitch_to,
            },
            "malformed_techniques": self.malformed_techniques,
            "context_kind": self.context_kind,
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
    stream_level_count: int | None
    stream_level_ordinal: int | None
    stream_is_full_difficulty: bool
    target_path: tuple[str | int, ...]
    location: str
    context_kind: str
    time: float
    string: int
    fret: int
    visual_target_ambiguous: bool
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

    @property
    def review_item_id(self) -> str:
        """Stable occurrence identity independent of the stored technique flags."""
        stable_identity = {
            "member_path": self.member_path,
            "target_path": list(self.target_path),
            "time": self.time,
            "string": self.string,
            "fret": self.fret,
        }
        return _candidate_digest(stable_identity).replace(
            "hopo-", "hopo-item-", 1
        )

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
        stream_context = {
            "kind": "top_level",
            "phrase_index": None,
            "difficulty_index": None,
            "difficulty_count": None,
            "difficulty_ordinal": None,
            "difficulty_scope": "full",
            "is_full_difficulty": True,
            "mastery_fraction": 1.0,
        }
        if (
            len(self.stream_path) == 4
            and self.stream_path[0] == "phrases"
            and isinstance(self.stream_path[1], int)
            and self.stream_path[2] == "levels"
            and isinstance(self.stream_path[3], int)
        ):
            level_count = max(1, int(self.stream_level_count or 1))
            level_ordinal = max(0, int(self.stream_level_ordinal or 0))
            difficulty_scope = (
                "full" if self.stream_is_full_difficulty else "lower"
            )
            stream_label = (
                f"Phrase {self.stream_path[1] + 1}, "
                f"{'full difficulty' if self.stream_is_full_difficulty else 'lower difficulty'} "
                f"(level {level_ordinal + 1} of {level_count})"
            )
            stream_context = {
                "kind": "phrase_level",
                "phrase_index": self.stream_path[1],
                "difficulty_index": self.stream_path[3],
                "difficulty_count": level_count,
                "difficulty_ordinal": level_ordinal,
                "difficulty_scope": difficulty_scope,
                "is_full_difficulty": self.stream_is_full_difficulty,
                "mastery_fraction": (
                    1.0
                    if self.stream_is_full_difficulty
                    else min(1.0, (level_ordinal + 0.5) / level_count)
                ),
            }
        target_tail = self.target_path[len(self.stream_path):]
        runtime_locator = {
            "kind": self.context_kind,
            "note_index": None,
            "chord_index": None,
            "chord_note_index": None,
        }
        if (
            len(target_tail) == 2
            and target_tail[0] == "notes"
            and isinstance(target_tail[1], int)
        ):
            runtime_locator["note_index"] = target_tail[1]
        elif (
            len(target_tail) == 4
            and target_tail[0] == "chords"
            and isinstance(target_tail[1], int)
            and target_tail[2] == "notes"
            and isinstance(target_tail[3], int)
        ):
            runtime_locator["chord_index"] = target_tail[1]
            runtime_locator["chord_note_index"] = target_tail[3]
        result = {
            "candidate_id": self.candidate_id,
            "review_item_id": self.review_item_id,
            "location": self.location,
            "member_path": self.member_path,
            "context_kind": self.context_kind,
            "stream": stream_label,
            "time": self.time,
            "string": self.string,
            "fret": self.fret,
            "visual_target_ambiguous": self.visual_target_ambiguous,
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
            "stream_context": stream_context,
            "runtime_locator": runtime_locator,
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
    difficulty_scope: str
    full_candidate_count: int
    lower_candidate_count: int


@dataclass(frozen=True)
class _HopoEvent:
    time: float
    time_tick: int
    string: int
    fret: int
    sustain: float
    slide_to: int | None
    slide_unpitch_to: int | None
    location: str
    path: tuple[str | int, ...] | None
    writable: bool
    hammer_on: bool
    pull_off: bool
    tap: bool
    link_next: bool
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
        sustain = _hopo_number(raw.get("sus", 0.0))
        if sustain is None or sustain < 0:
            sustain = 0.0
        slide_to = _hopo_integer(raw.get("sl"))
        if slide_to is None or slide_to < 0:
            slide_to = None
        slide_unpitch_to = _hopo_integer(raw.get("slu"))
        if slide_unpitch_to is None or slide_unpitch_to < 0:
            slide_unpitch_to = None
        malformed = any(
            field in raw and not isinstance(raw.get(field), bool)
            for field in ("ho", "po", "tp")
        )
        events.append(_HopoEvent(
            time=event_time,
            time_tick=round(event_time * 10_000),
            string=string,
            fret=fret,
            sustain=sustain,
            slide_to=slide_to,
            slide_unpitch_to=slide_unpitch_to,
            location=_display_path(member_path, location_path),
            path=path,
            writable=writable,
            hammer_on=technique_values[0] is True,
            pull_off=technique_values[1] is True,
            tap=technique_values[2] is True,
            link_next=raw.get("ln") is True,
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


def _hopo_effective_fret(event: _HopoEvent) -> int:
    """Return the fret at which an incoming same-string gesture begins."""
    return event.slide_to if event.slide_to is not None else event.fret


def _hopo_links_to_fret(event: _HopoEvent, fret: int) -> bool:
    """Whether strict LinkNext makes ``fret`` a non-restruck destination."""
    return bool(
        event.link_next
        and (event.fret == fret or event.slide_to == fret)
    )


def _neighbour(
    onset: list[_HopoEvent] | None,
    *,
    compare_slide_landings: bool = False,
) -> tuple[HopoNeighbour | None, str]:
    if not onset:
        return None, "missing"
    frets = {event.fret for event in onset}
    if len(frets) != 1:
        return None, "ambiguous"
    effective_frets = {_hopo_effective_fret(event) for event in onset}
    if compare_slide_landings and len(effective_frets) != 1:
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
        effective_fret=_hopo_effective_fret(representative),
        sustain=representative.sustain,
        link_next=representative.link_next,
        slide_to=representative.slide_to,
        slide_unpitch_to=representative.slide_unpitch_to,
        location=location,
        path=path,
        writable=len(writable) == 1,
        hammer_on=representative.hammer_on,
        pull_off=representative.pull_off,
        tap=representative.tap,
        malformed_techniques=representative.malformed_techniques,
        context_kind=representative.context_kind,
    ), "usable"


def find_hopo_review_candidates(
    document: dict,
    *,
    member_path: str = "",
    max_candidates: int = MAX_HOPO_REVIEW_CANDIDATES,
    difficulty_scope: str = "all_authored",
) -> list[HopoReviewCandidate]:
    """Classify authored HO/PO ambiguity without changing the document.

    Each top-level arrangement and phrase difficulty is an independent stream.
    The immediately preceding same-string onset determines incoming HO/PO
    direction. A valid pitched slide target is the predecessor's effective
    fret. The next onset is deliberately evidence only and long gaps by
    themselves do not suppress a candidate. A strict authored LinkNext on the
    immediately preceding same-fret or matching pitched-slide onset identifies
    a held continuation.
    """
    page = page_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=0,
        limit=max_candidates,
        difficulty_scope=difficulty_scope,
    )
    return list(page.candidates)


def page_hopo_review_candidates(
    document: dict,
    *,
    member_path: str = "",
    offset: int = 0,
    limit: int = MAX_HOPO_REVIEW_CANDIDATES,
    difficulty_scope: str = "all_authored",
) -> HopoReviewPage:
    """Scan every candidate while retaining only one deterministic page."""
    return _scan_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=offset,
        limit=limit,
        selected_ids=None,
        selected_paths=None,
        difficulty_scope=difficulty_scope,
    )


def select_hopo_review_candidates(
    document: dict,
    *,
    member_path: str = "",
    candidate_ids: set[str] | frozenset[str],
    difficulty_scope: str = "all_authored",
) -> HopoReviewPage:
    """Retain only requested server IDs while still counting every candidate."""
    return _scan_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=0,
        limit=MAX_HOPO_REVIEW_CANDIDATES,
        selected_ids=frozenset(candidate_ids),
        selected_paths=None,
        difficulty_scope=difficulty_scope,
    )


def select_hopo_review_candidates_at_paths(
    document: dict,
    *,
    member_path: str = "",
    target_paths: set[tuple[str | int, ...]] | frozenset[tuple[str | int, ...]],
    difficulty_scope: str = "all_authored",
) -> HopoReviewPage:
    """Retain candidates at exact mutable paths while still counting all issues.

    Reviewed-repair outcome checks mutate only a bounded set of explicit note
    objects. Selecting by path lets those checks detect newly created issues
    without retaining an unbounded package-wide candidate list.
    """
    return _scan_hopo_review_candidates(
        document,
        member_path=member_path,
        offset=0,
        limit=MAX_HOPO_REVIEW_CANDIDATES,
        selected_ids=None,
        selected_paths=frozenset(target_paths),
        difficulty_scope=difficulty_scope,
    )


def _scan_hopo_review_candidates(
    document: dict,
    *,
    member_path: str,
    offset: int,
    limit: int,
    selected_ids: frozenset[str] | None,
    selected_paths: frozenset[tuple[str | int, ...]] | None,
    difficulty_scope: str,
) -> HopoReviewPage:
    if (
        not isinstance(document, dict)
        or not _integer(offset)
        or offset < 0
        or not _integer(limit)
        or limit < 1
        or difficulty_scope not in REVIEW_DIFFICULTY_SCOPES
    ):
        return HopoReviewPage(
            (), 0, 0, max(offset, 0) if _integer(offset) else 0, 0,
            difficulty_scope if difficulty_scope in REVIEW_DIFFICULTY_SCOPES else "all_authored",
            0, 0,
        )
    candidates: list[HopoReviewCandidate] = []
    total_count = 0
    blocked_count = 0
    full_candidate_count = 0
    lower_candidate_count = 0
    for (
        parent_path,
        container,
        stream_level_count,
        stream_level_ordinal,
        stream_is_full_difficulty,
    ) in _hopo_arrangement_containers(document):
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
                previous_onset = (
                    onsets[ticks[tick_index - 1]] if tick_index else None
                )
                previous, predecessor_state = _neighbour(
                    previous_onset,
                    compare_slide_landings=True,
                )
                next_note, next_state = _neighbour(
                    onsets[ticks[tick_index + 1]]
                    if tick_index + 1 < len(ticks)
                    else None
                )
                current_frets = {event.fret for event in current_onset}
                visual_target_counts: dict[int, int] = {}
                for current_event in current_onset:
                    visual_target_counts[current_event.fret] = (
                        visual_target_counts.get(current_event.fret, 0) + 1
                    )
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
                    else:
                        # Strict LinkNext makes a same-fret continuation or a
                        # matching pitched-slide landing non-restruck. Do not
                        # infer a destination from an unpitched slide. Other
                        # reasons (for example both flags) remain reviewable.
                        linked_continuation = any(
                            _hopo_links_to_fret(source, event.fret)
                            for source in (previous_onset or ())
                        )
                        if linked_continuation:
                            pass
                        elif previous.effective_fret == event.fret:
                            reasons.append("same_fret")
                        elif (
                            event.hammer_on
                            and not event.pull_off
                            and previous.effective_fret > event.fret
                        ) or (
                            event.pull_off
                            and not event.hammer_on
                            and previous.effective_fret < event.fret
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
                        decisions.append("move_to_next")
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
                            [
                                previous.time,
                                previous.fret,
                                previous.effective_fret,
                                predecessor_state,
                            ]
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
                        stream_level_count=stream_level_count,
                        stream_level_ordinal=stream_level_ordinal,
                        stream_is_full_difficulty=stream_is_full_difficulty,
                        target_path=event.path,
                        location=event.location,
                        context_kind=event.context_kind,
                        time=event.time,
                        string=string,
                        fret=event.fret,
                        visual_target_ambiguous=(
                            visual_target_counts[event.fret] > 1
                        ),
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
                    if stream_is_full_difficulty:
                        full_candidate_count += 1
                    else:
                        lower_candidate_count += 1
                    if (
                        difficulty_scope == "full_only"
                        and not stream_is_full_difficulty
                    ):
                        continue
                    if candidate.blockers:
                        blocked_count += 1
                    if selected_ids is not None:
                        if candidate.candidate_id in selected_ids:
                            candidates.append(candidate)
                    elif selected_paths is not None:
                        if candidate.target_path in selected_paths:
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
        difficulty_scope=difficulty_scope,
        full_candidate_count=full_candidate_count,
        lower_candidate_count=lower_candidate_count,
    )


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _complete_json_value(value) -> bool:
    """Return whether *value* is a finite value from the JSON data model.

    ``json.dumps`` intentionally accepts a few convenient Python extensions,
    including tuples and non-string mapping keys. Repair identity must be
    stricter: it describes data that could have come from a Feedpak JSON
    document and that can be rendered again without coercion.
    """
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_complete_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _complete_json_value(item)
            for key, item in value.items()
        )
    return False


def complete_json_identity(value) -> bytes | None:
    """Return canonical bytes for a complete JSON value, or ``None``.

    Object key order is ignored while array order and stored JSON scalar types
    remain significant. In particular, Python's otherwise-equal ``True``,
    ``1``, and ``1.0`` render differently. Unknown object properties are part
    of the identity rather than being discarded by an event-specific key.
    """
    try:
        if not _complete_json_value(value):
            return None
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None


def repairable_tempo_event(value) -> bool:
    """Whether one tempo event is safe to retain, deduplicate, or reorder."""
    return bool(
        isinstance(value, dict)
        and _finite_number(value.get("time"))
        and _finite_number(value.get("bpm"))
        and value["bpm"] > 0
    )


def repairable_time_signature_event(value) -> bool:
    """Whether one time-signature event has an unambiguous stored shape."""
    if not isinstance(value, dict) or not _finite_number(value.get("time")):
        return False
    signature = value.get("ts")
    return bool(
        isinstance(signature, list)
        and len(signature) == 2
        and all(_integer(part) and part > 0 for part in signature)
    )


def repairable_tone_change(value) -> bool:
    """Whether one tone change uses FeedBack's canonical, writable shape."""
    if not isinstance(value, dict) or not _finite_number(value.get("t")):
        return False
    if not isinstance(value.get("name"), str) or not value["name"]:
        return False
    if "rig" not in value:
        return True
    rig = value["rig"]
    return isinstance(rig, str) and bool(rig.strip())


def timed_event_stream_eligibility(
    items,
    predicate: Callable[[object], bool],
) -> bool:
    """Return whether every item can participate in one structural repair.

    Length is deliberately not part of this shared decision. Duplicate and
    ordering planners independently decide whether a valid stream contains an
    actual change. Empty and one-entry streams are therefore valid no-ops.
    """
    return bool(
        isinstance(items, list)
        and all(
            predicate(item) and complete_json_identity(item) is not None
            for item in items
        )
    )


def effective_tones_source(
    manifest_tones,
    arrangement_tones,
) -> tuple[str, dict | None]:
    """Select the tone block with the same precedence as FeedBack core.

    A nonempty manifest object overrides the arrangement JSON wholesale. An
    empty manifest object is treated as absent and falls back to the inline
    arrangement block.
    """
    if isinstance(manifest_tones, dict) and manifest_tones:
        return "manifest", manifest_tones
    if isinstance(arrangement_tones, dict):
        return "arrangement", arrangement_tones
    return "absent", None


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


def _hopo_arrangement_containers(
    document: dict,
) -> Iterator[
    tuple[tuple[str | int, ...], dict, int | None, int | None, bool]
]:
    """Yield only streams the Player can select through Master Difficulty.

    When a usable phrase ladder exists, FeedBack renders one authored level
    per phrase and does not use the arrangement-root note arrays. The root is
    the full/fallback stream only for arrangements without a usable ladder.
    Keeping that distinction here avoids reviewing the same max-difficulty
    event twice and gives every candidate a stable full-vs-lower label.
    """
    phrase_streams: list[
        tuple[int, list[tuple[int, dict]]]
    ] = []
    phrases = document.get("phrases")
    if isinstance(phrases, list):
        for phrase_index, phrase in enumerate(phrases):
            levels = phrase.get("levels") if isinstance(phrase, dict) else None
            if not isinstance(levels, list):
                continue
            authored = [
                (level_index, level)
                for level_index, level in enumerate(levels)
                if isinstance(level, dict)
            ]
            if authored:
                phrase_streams.append((phrase_index, authored))

    if not phrase_streams:
        yield (), document, None, None, True
        return

    for phrase_index, authored in phrase_streams:
        level_count = len(authored)
        for ordinal, (level_index, level) in enumerate(authored):
            yield (
                ("phrases", phrase_index, "levels", level_index),
                level,
                level_count,
                ordinal,
                ordinal == level_count - 1,
            )


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
