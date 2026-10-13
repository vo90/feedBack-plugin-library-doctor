"""Pure, conservative planning for persistent lyric phrase markers.

The grouping and soft targets mirror Core's ``static/js/lyric-lines.js``.
Unlike that display-only fallback, this planner requires an unambiguous ordered
document and a severe Library Doctor warning before proposing any edits. Its
only output edits are token indices at which the caller may append one ``+``.
No token, timing, unknown property, or input container is changed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


MAX_LYRIC_SYLLABLES_PER_LINE = 80
MAX_LYRIC_CHARACTERS_PER_LINE = 500
MAX_LYRIC_LINE_SECONDS = 45.0
FALLBACK_BREAK_GAP_SECONDS = 4.0

_OVERSIZED_WORDS = 16
_OVERSIZED_CHARACTERS = 100
_OVERSIZED_SECONDS = 12.0
_TARGET_WORDS = 8
_TARGET_CHARACTERS = 60
_TARGET_SECONDS = 6.0
_PHRASE_PAUSE_SECONDS = 0.45


def _visible_text(text: str) -> str:
    return text[:-1] if text.endswith(("+", "-")) else text


def visible_character_count(text: str) -> int:
    """Count the same UTF-16 units as JavaScript, excluding one final marker."""
    return len(_visible_text(text).encode("utf-16-le", errors="surrogatepass")) // 2


@dataclass(frozen=True)
class _Word:
    indices: tuple[int, ...]
    start: float
    end: float
    last_start: float
    prior_end: float
    characters: int
    final_text: str


@dataclass(frozen=True)
class _Line:
    words: tuple[_Word, ...]
    start: float
    end: float
    authored_end: bool = False

    @property
    def indices(self) -> tuple[int, ...]:
        return tuple(index for word in self.words for index in word.indices)


def _number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        return False


def _validate(document: object) -> str | None:
    if not isinstance(document, list):
        return "Lyrics must be a JSON array of timed token objects."
    previous_start = -1.0
    for index, token in enumerate(document):
        if not isinstance(token, dict):
            return f"Token {index} is not an object."
        start, duration, text = token.get("t"), token.get("d"), token.get("w")
        if not _number(start) or not _number(duration):
            return f"Token {index} needs finite, nonnegative numeric t and d."
        if not _number(start + duration):
            return f"Token {index} has a non-finite end time."
        if (not isinstance(text, str)
                or (text not in ("+", "-") and not _visible_text(text).strip())):
            return f"Token {index} needs nonempty visible text."
        if start < previous_start:
            return f"Token {index} is out of time order."
        previous_start = start
    return None


def _original_lines(document: list[dict]) -> list[_Line]:
    """Keep authored boundaries, running-end silences, and hyphen word joins."""
    lines: list[_Line] = []
    words: list[_Word] = []
    word_indices: list[int] = []
    line_start = line_end = None
    prefix_end = 0.0
    word_prior_end = 0.0

    def flush_word() -> None:
        if not word_indices:
            return
        members = [document[index] for index in word_indices]
        words.append(_Word(
            indices=tuple(word_indices),
            start=members[0]["t"],
            end=max(token["t"] + token["d"] for token in members),
            last_start=members[-1]["t"],
            prior_end=word_prior_end,
            characters=sum(visible_character_count(token["w"]) for token in members),
            final_text=_visible_text(members[-1]["w"]),
        ))
        word_indices.clear()

    def flush_line(authored_end: bool = False) -> None:
        nonlocal line_start, line_end
        flush_word()
        if words:
            lines.append(_Line(tuple(words), line_start, line_end, authored_end))
        words.clear()
        line_start = line_end = None

    for index, token in enumerate(document):
        start, end, raw = token["t"], token["t"] + token["d"], token["w"]
        if (line_start is not None and not word_indices
                and start - line_end > FALLBACK_BREAK_GAP_SECONDS):
            flush_line()
        if line_start is None:
            line_start = start
            line_end = end
        if not word_indices:
            word_prior_end = prefix_end
        word_indices.append(index)
        line_end = max(line_end, end)
        prefix_end = max(prefix_end, end)
        if not raw.endswith("-"):
            flush_word()
        if raw.endswith("+"):
            flush_line(True)
    flush_line()
    return lines


def _severe(line: _Line) -> bool:
    return (
        sum(len(word.indices) for word in line.words) > MAX_LYRIC_SYLLABLES_PER_LINE
        or sum(word.characters for word in line.words) > MAX_LYRIC_CHARACTERS_PER_LINE
        or line.end - line.start > MAX_LYRIC_LINE_SECONDS
    )


def _bounded_lines(line: _Line) -> list[_Line]:
    characters = sum(word.characters for word in line.words)
    if line.authored_end and not _severe(line):
        return [line]
    if (len(line.words) <= _OVERSIZED_WORDS
            and characters <= _OVERSIZED_CHARACTERS
            and (line.end - line.start <= _OVERSIZED_SECONDS or len(line.words) <= 1)):
        return [line]

    output: list[_Line] = []
    words: list[_Word] = []
    start = end = 0.0
    chunk_characters = 0

    def flush() -> None:
        nonlocal chunk_characters
        if words:
            output.append(_Line(tuple(words), start, end))
        words.clear()
        chunk_characters = 0

    for word in line.words:
        # Persistence is stricter than the display fallback for zero-duration
        # equal-start cues and overlaps across an existing authored boundary.
        safe_boundary = (
            words and word.start > words[-1].last_start
            and word.start > start and word.start >= max(end, word.prior_end)
        )
        if safe_boundary:
            natural_end = (
                (word.start - end >= _PHRASE_PAUSE_SECONDS
                 or words[-1].final_text.endswith((".", "!", "?", ";", ":")))
                and (len(words) >= 3 or end - start >= 2)
            )
            exceeds_target = (
                len(words) >= _TARGET_WORDS
                or chunk_characters + word.characters > _TARGET_CHARACTERS
                or max(end, word.end) - start > _TARGET_SECONDS
            )
            if natural_end or exceeds_target:
                flush()
        if not words:
            start, end = word.start, word.end
        words.append(word)
        end = max(end, word.end)
        chunk_characters += word.characters
    flush()
    return output


def _result(status: str, message: str, *, detected: bool = False,
            indices: list[int] | None = None, blocker_code: str | None = None,
            original_count: int = 0, planned_count: int = 0) -> dict:
    return {
        "status": status,
        "detected": detected,
        "eligible": status == "eligible",
        "append_plus_indices": indices or [],
        "blocker_code": blocker_code,
        "message": message,
        "original_group_count": original_count,
        "planned_group_count": planned_count,
    }


def assess_lyric_phrases(document: object) -> dict:
    """Propose only safe ``+`` appends; do not modify or return input tokens.

    A valid document without a severe original line is ``no_defect``. Once a
    severe line is detected, Core's display fallback is mirrored across this
    document, retaining healthy authored phrasing. Every split original block
    receives a final marker as well, so its shorter remainder is stable on a
    later scan. Any ambiguity or still-severe indivisible chunk returns
    ``blocked`` with *no* partial edit indices.
    """
    invalid = _validate(document)
    if invalid is not None:
        return _result("blocked", invalid, blocker_code="invalid-lyric-document")

    original = _original_lines(document)
    original_count = len(original)
    if not any(_severe(line) for line in original):
        return _result("no_defect", "No clearly excessive lyric phrase was found.",
                       original_count=original_count, planned_count=original_count)

    planned: list[_Line] = []
    indices: list[int] = []
    for line in original:
        chunks = _bounded_lines(line)
        planned.extend(chunks)
        if any(_severe(chunk) for chunk in chunks):
            return _result(
                "blocked", "An excessive phrase cannot be separated without splitting joined or overlapping words.",
                detected=True, blocker_code="indivisible-lyric-phrase",
                original_count=original_count, planned_count=len(planned),
            )
        if len(chunks) <= 1:
            continue
        for chunk in chunks:
            index = chunk.words[-1].indices[-1]
            if document[index]["w"].endswith("+"):
                continue
            if document[index]["w"].endswith("-"):
                return _result(
                    "blocked", "A split phrase ends with an unfinished joined word.",
                    detected=True, blocker_code="unfinished-lyric-word",
                    original_count=original_count, planned_count=len(planned),
                )
            indices.append(index)

    # Verify the exact proposed representation rather than assuming that a
    # marker append preserves grouping. All metadata and original objects stay
    # untouched; only these temporary shallow token copies receive markers.
    selected = set(indices)
    candidate = [dict(token, w=token["w"] + "+") if index in selected else token
                 for index, token in enumerate(document)]
    reparsed = _original_lines(candidate)
    if (not indices or any(_severe(line) for line in reparsed)
            or [line.indices for line in reparsed] != [line.indices for line in planned]):
        return _result(
            "blocked", "The proposed markers do not yield stable, bounded lyric phrases.",
            detected=True, blocker_code="unstable-lyric-boundaries",
            original_count=original_count, planned_count=len(planned),
        )
    return _result(
        "eligible", "Safe whole-word phrase markers can be added without changing timing or existing text.",
        detected=True, indices=indices, original_count=original_count,
        planned_count=len(planned),
    )
