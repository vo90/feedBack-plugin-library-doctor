"""Read-only Feedpak validation used by the Library Doctor plugin.

The official schemas establish format conformance.  The named semantic rules
below are deliberately separate: they identify chart/media mistakes that are
schema-valid but suspicious in practice.  Nothing in this module modifies or
extracts a package.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Iterator

import yaml
from jsonschema import Draft202012Validator


SPEC_REVISION = "52548b742f64c2a35052a141976ea1b7889f4b1a"
VALIDATOR_VERSION = f"rules-20:feedpak-{SPEC_REVISION}"
SUPPORTED_MAJOR = 1
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_MEDIA_INSPECTION_BYTES = 128 * 1024 * 1024
MAX_DEEP_AUDIO_INSPECTION_BYTES = 512 * 1024 * 1024
MAX_FINDINGS = 250
MAX_SCHEMA_ERRORS_PER_FILE = 100
MAX_MASTERY_PROFILES = 512
MAX_PACKAGE_MEMBERS = 50_000
# Declared package size is only a coarse archive-bomb guard. Keep it high enough
# for legitimate long songs with many high-quality stems; actual reads have a
# separate, much lower cumulative limit below.
MAX_PACKAGE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024 * 1024
MAX_PACKAGE_READ_BYTES = 4 * 1024 * 1024 * 1024
MAX_PACKAGE_STRUCTURE_ITEMS = 2_000_000
MAX_YAML_ALIASES = 100
MAX_IMAGE_HEADER_BYTES = 64 * 1024
MAX_REALIZATION_HASH_BYTES = 128 * 1024 * 1024
TIME_PRECISION = 10_000  # Match the highway safeguard's 0.0001-second key.
CHART_END_TOLERANCE_SECONDS = 0.05
TIMELINE_SIGNIFICANT_OVERRUN_SECONDS = 5.0
BEND_CURVE_TOLERANCE_SECONDS = 0.005
NEAR_SIMULTANEOUS_NOTE_SECONDS = 0.010
SUSTAIN_OVERLAP_TOLERANCE_SECONDS = 0.05
EXTREME_CHORD_SPAN_FRETS = 8
HIGHWAY_MAX_FRET = 24
HIGHWAY_MAX_STRINGS = 8
NOTE_BOOLEAN_FIELDS = (
    "ho", "po", "hm", "hp", "pm", "mt", "vb", "tr", "ac", "tp",
    "ln", "fhm", "plk", "slp", "ig",
)
LYRIC_FALLBACK_BREAK_GAP_SECONDS = 4.0  # Match FeedBack's lyric renderer.
MAX_LYRIC_SYLLABLES_PER_LINE = 80
MAX_LYRIC_CHARACTERS_PER_LINE = 500
MAX_LYRIC_LINE_SECONDS = 45.0
PREVIEW_INSPECTION_SIZE_RATIO = 0.8
PREVIEW_FULL_LENGTH_RATIO = 0.9
MAX_EXPECTED_PREVIEW_SECONDS = 35.0
AUDIO_SHORTFALL_TOLERANCE_SECONDS = 5.0
AUDIO_SHORTFALL_TOLERANCE_RATIO = 0.02
AUDIO_PADDING_TOLERANCE_SECONDS = 10.0
AUDIO_PADDING_TOLERANCE_RATIO = 0.05
_KEYBOARD_HINT_RE = re.compile(
    r"(?:^|\b)(?:keys?|piano|keyboard|organ|synth|rhodes|wurlitzer|clav|epiano)(?:\b|$)",
    re.IGNORECASE,
)

_RULE_TITLES = {
    "chart.duplicate-note": "Identical duplicate note",
    "chart.duplicate-chord-note": "Identical duplicate chord note",
    "chart.duplicate-chord": "Identical duplicate chord",
    "chart.duplicate-anchor": "Identical duplicate anchor",
    "chart.duplicate-handshape": "Identical duplicate handshape",
    "chart.zero-length-handshape": "Zero-length handshape",
    "chart.note-duplicates-chord": "Standalone note duplicates a chord",
    "chart.bend-points-out-of-order": "Bend points out of order",
    "chart.phrases-out-of-order": "Phrase windows out of order",
    "chart.conflicting-duplicate-note": "Conflicting notes on one string",
    "chart.string-conflict": "Overlapping notes on one string",
    "chart.coincident-chords": "Chords start at the same time",
    "chart.chord-string-duplicate": "Chord repeats a string",
    "review.impossible-chord-fingering": "Impossible chord fingering",
    "lyrics.too-few-line-breaks": "Lyrics may be missing line breaks",
    "lyrics.empty-text": "Lyric entry has no visible text",
    "lyrics.out-of-order": "Lyric cues out of order",
    "timeline.duplicate-beat": "Identical duplicate beat marker",
    "timeline.repeated-beat-time": "Repeated beat time has conflicting data",
    "timeline.duplicate-section": "Identical duplicate section marker",
    "timeline.repeated-section-time": "Repeated section time has conflicting data",
    "media.preview-is-full-mix": "Preview duplicates the full song audio",
    "media.preview-full-length": "Preview is almost the full song length",
    "media.preview-too-long": "Preview is unusually long",
    "media.invalid-cover-image": "Cover image cannot be read",
    "media.cover-extension-mismatch": "Cover filename and image type disagree",
    "media.unsupported-cover-image": "Cover image type is not supported",
    "manifest.missing-full-mix": "Full song mix is missing",
    "manifest.full-mix-default-not-off": "Full song mix has the wrong default",
    "package.validation-budget-exceeded": "Package is too complex to scan safely",
    "scan.findings-truncated": "More issues were found",
    "scan.validation-failed": "Package scan failed",
    "drums.duplicate-hit": "Identical duplicate drum hit",
    "drums.conflicting-hit": "Conflicting drum hits",
    "notation.unknown-staff-reference": "Notation references an unknown staff",
    "rigs.missing-realization-file": "Rig asset is missing",
    "rigs.realization-hash-mismatch": "Rig asset checksum does not match",
    "tones.missing-rig": "Tone references an unknown rig",
}

_RULE_AREAS = {
    "chart": "Tab",
    "review": "Playability",
    "lyrics": "Lyrics",
    "media": "Audio and artwork",
    "manifest": "Song information",
    "package": "Feedpak structure",
    "spec": "Feedpak format",
    "scan": "Scan",
    "timeline": "Song timeline",
    "drums": "Drums",
    "keys": "Keys",
    "harmony": "Harmony",
    "vocal-pitch": "Vocals",
    "vocal-pitch-contour": "Vocals",
    "notation": "Notation",
    "rigs": "Rigs",
    "tones": "Tones",
}

_SAFE_REPAIR_CANDIDATES = {
    "chart.duplicate-note",
    "chart.duplicate-chord-note",
    "chart.duplicate-chord",
    "chart.duplicate-anchor",
    "chart.duplicate-handshape",
    "chart.zero-length-handshape",
    "chart.invalid-handshape-span",
    "chart.note-duplicates-chord",
    "chart.bend-points-out-of-order",
    "lyrics.out-of-order",
    "timeline.duplicate-beat",
    "timeline.beats-out-of-order",
    "timeline.duplicate-section",
    "timeline.sections-out-of-order",
    "drums.duplicate-hit",
}

# These explanations deliberately describe observable player impact rather
# than validator implementation.  The finding message remains the precise
# evidence for developers; this catalog answers the two questions a player is
# more likely to have: "what might I notice?" and "why should I fix it?".
_RULE_EXPERIENCE = {
    "chart.duplicate-note": (
        "FeedBack may process or draw the same gem more than once at one position, even though the copies look like a single note.",
        "One intended note remains with the same timing and techniques, removing redundant chart data and preventing duplicate-note behavior.",
    ),
    "chart.note-duplicates-chord": (
        "The editor and highway may show the standalone gem on top of the identical note already contained in the chord.",
        "The complete chord remains, while removing the redundant standalone copy gives the player one clear instruction on that string.",
    ),
    "chart.duplicate-chord-note": (
        "One string of a chord may be processed or drawn more than once, making the chord data ambiguous even when the gems overlap visually.",
        "The chord keeps one identical member on that string, preserving its shape and techniques without redundant data.",
    ),
    "chart.duplicate-chord": (
        "FeedBack may process or draw the same complete chord more than once at one position.",
        "One complete chord remains with the same shape, timing, and techniques, removing only the redundant copy.",
    ),
    "chart.duplicate-anchor": (
        "The highway receives the same fret-window instruction more than once at one time.",
        "One identical anchor remains, giving the highway the same intended fret window without redundant transitions.",
    ),
    "chart.duplicate-handshape": (
        "The same chord-shape guide may be processed more than once over the same time span.",
        "One identical handshape remains with the same chord and duration, keeping the guidance clear and compact.",
    ),
    "chart.bend-points-out-of-order": (
        "FeedBack has to reorder the bend curve while loading it, so the saved Feedpak, editor view, and other tools may not agree on how it should progress.",
        "Saving the existing points in chronological order makes the same authored bend curve portable and predictable without deleting or inventing any points.",
    ),
    "drums.duplicate-hit": (
        "FeedBack may process or draw the same drum hit more than once at one position.",
        "One intended hit remains at each position, so the drum chart is clean without changing its musical timing.",
    ),
    "chart.conflicting-duplicate-note": (
        "One string has competing versions of a note at the same instant. The highway may show only one version or behave inconsistently.",
        "Choosing the intended version makes the gem, sustain, and techniques unambiguous in play.",
    ),
    "chart.string-conflict": (
        "The same physical string is asked to play incompatible notes at once, which cannot be performed as written and may display incorrectly.",
        "Resolving the conflict produces one playable instruction for that string and a predictable highway display.",
    ),
    "chart.invisible-chord": (
        "A chord event can exist in the song data without a playable fret shape, so the player may receive no useful highway instruction.",
        "Adding or correcting the intended shape makes the chord visible and playable.",
    ),
    "chart.negative-fret": (
        "A negative fret without FeedBack's supported -1 fret-hand-mute marker cannot be placed on the physical fretboard and may display incorrectly.",
        "Correcting the fret or marking an intentional fret-hand mute gives the player an unambiguous highway instruction.",
    ),
    "chart.invalid-handshape-span": (
        "The chord-shape guide may end before it begins, disappear, or cover the wrong part of the highway.",
        "Correcting the intended span, or safely removing only a redundant broken guide when its chord already exists, keeps the playable instruction predictable.",
    ),
    "chart.zero-length-handshape": (
        "The shape has no duration, so it cannot appear as a sustained hand-position guide; it may still contribute a chord at its start.",
        "When the matching chord already supplies that exact onset, removing only the redundant zero-length guide cleans the chart without changing the playable chord.",
    ),
    "timeline.beats-out-of-order": (
        "Beat and measure tracking can jump or become unreliable, affecting the visual rhythm grid and timing context.",
        "Chronological beats restore a stable rhythm grid and predictable measure progression.",
    ),
    "timeline.sections-out-of-order": (
        "Section names can appear in the wrong sequence or at unexpected moments during the song.",
        "Chronological sections make navigation and the displayed song structure match playback.",
    ),
    "timeline.duplicate-beat": (
        "FeedBack can receive the same rhythm-grid instruction more than once, creating a repeated or zero-length beat interval.",
        "Keeping one identical beat marker produces a clean timing grid without changing its intended position or measure.",
    ),
    "timeline.repeated-beat-time": (
        "A beat time reappears after the grid has already advanced, but its stored measure data disagrees with the earlier marker.",
        "Choosing the correct grid segment restores an unambiguous beat and measure progression.",
    ),
    "timeline.duplicate-section": (
        "FeedBack can process the same section boundary more than once, making navigation data redundant.",
        "Keeping one identical marker preserves the section boundary while removing redundant timeline data.",
    ),
    "timeline.repeated-section-time": (
        "A section time reappears after the song structure has advanced, but the marker data disagrees with the earlier entry.",
        "Choosing the intended marker gives FeedBack one clear section name and position at that moment.",
    ),
    "lyrics.empty-text": (
        "A lyric cue occurs but has nothing visible to show, which can create unexplained gaps in the lyrics.",
        "Removing the empty cue or restoring its text gives the player a continuous, meaningful lyric display.",
    ),
    "lyrics.too-few-line-breaks": (
        "Lyrics may appear as one very long block instead of readable lines that advance with the song.",
        "Adding sensible line endings makes lyrics easier to read and lets the display advance in useful phrases.",
    ),
    "lyrics.out-of-order": (
        "Lyric text can jump backward, appear late, or be skipped as playback moves forward.",
        "Putting cues in time order makes lyric progression predictable during the song.",
    ),
    "media.preview-is-full-mix": (
        "Browsing the library loads another complete copy of the song instead of a short preview, wasting storage and read time.",
        "A dedicated short preview makes browsing quicker and avoids storing the full mix twice.",
    ),
    "media.preview-full-length": (
        "The preview plays for nearly the whole song, making library previewing slow and using unnecessary package space.",
        "A short representative excerpt gives faster browsing and a smaller, cleaner package.",
    ),
    "media.preview-too-long": (
        "The library preview takes unusually long to reach its end and may use more storage than needed.",
        "A shorter representative excerpt makes song browsing faster while preserving a useful preview.",
    ),
    "media.audio-shorter-than-manifest": (
        "The declared song timeline continues after the audio has ended, leaving silence or chart events with no backing audio.",
        "Matching the timeline to the real audio keeps the progress display, ending, and chart content aligned.",
    ),
    "media.audio-longer-than-manifest": (
        "Audio continues beyond the declared song ending, so playback or chart content may stop before the recording does.",
        "Correct duration data lets FeedBack represent and play the intended ending of the song.",
    ),
    "tones.missing-rig": (
        "The requested guitar tone cannot be loaded, so the arrangement may use no tone or the wrong fallback sound.",
        "Connecting the tone to a valid rig restores the intended guitar sound changes.",
    ),
    "review.same-string-sustain-overlap": (
        "A new note starts while the same string is still meant to sustain, which can feel impossible or cut the sustain unexpectedly.",
        "Reviewing the overlap can preserve an intentional technique or produce a cleaner, physically playable transition.",
    ),
    "review.near-simultaneous-string-notes": (
        "Notes that are only milliseconds apart may look like a chord but require an unintended rapid stagger when played.",
        "Confirming or aligning them makes the intended chord or picking pattern clearer to the player.",
    ),
    "review.impossible-chord-fingering": (
        "The authored fingering asks one finger to occupy incompatible positions, so the chord cannot be held as instructed.",
        "A corrected fingering makes the chord physically playable without necessarily changing its notes.",
    ),
    "review.extreme-chord-span": (
        "The chord stretches across an unusually large fret range and may be uncomfortable or impossible for most players.",
        "Reviewing the voicing confirms an intentional challenge or yields a more realistically playable chord.",
    ),
}

_CONDITIONAL_REPAIR_SUFFIXES = (
    "out-of-order",
    "extension-mismatch",
    "default-not-off",
)

_HIGHWAY_COMPATIBILITY_RULES = {
    "chart.ambiguous-slide",
    "chart.anchor-beyond-highway",
    "chart.capo-beyond-highway",
    "chart.conflicting-techniques",
    "chart.fret-beyond-highway",
    "chart.open-string-slide",
    "chart.slide-beyond-highway",
    "chart.slide-without-sustain",
    "chart.string-beyond-highway",
    "chart.string-without-tuning",
    "chart.template-beyond-highway",
    "chart.template-fret-beyond-highway",
    "chart.tuning-beyond-highway",
}


def _rule_experience(
    code: str, severity: str, category: str, area: str
) -> tuple[str, str]:
    explicit = _RULE_EXPERIENCE.get(code)
    if explicit is not None:
        return explicit

    _prefix, _, suffix = code.partition(".")
    if "after-duration" in suffix:
        return (
            f"Some {area.lower()} content occurs after FeedBack believes the song has ended, so it may never be shown or played.",
            "Correcting the declared duration or the late event lets the complete intended content appear within playback.",
        )
    if "out-of-order" in suffix:
        return (
            f"{area} events may jump backward, appear late, or be skipped because they are not stored in playback order.",
            "Putting events in chronological order makes their display and playback predictable.",
        )
    if "beyond-highway" in suffix or "string-without-tuning" in suffix:
        return (
            "The Feedpak value is valid data, but the current FeedBack highway may omit, clamp, or misplace it.",
            "Adapting the chart to the current highway limits keeps the intended instruction visible and playable in FeedBack.",
        )
    if "duplicate" in suffix:
        return (
            f"FeedBack may process overlapping {area.lower()} entries at one position, with an ambiguous or repeated result.",
            "Keeping the intended entry makes playback and display deterministic while removing redundant data.",
        )
    if "conflict" in suffix:
        return (
            f"Competing {area.lower()} instructions can produce an ambiguous display or behavior during play.",
            "Resolving the competing instructions gives FeedBack one clear result to show and play.",
        )
    if category == "authoring_review":
        return (
            "This unusual tab pattern may be intentional, but it can feel awkward, misleading, or physically difficult when played.",
            "Reviewing it confirms the author’s intent or improves playability without automatically treating unusual music as broken.",
        )
    if category == "feedback_compatibility":
        return (
            "The value is allowed by the Feedpak format, but the current FeedBack display or playback path may not represent it correctly.",
            "A compatible representation keeps the intended content visible and behaving predictably in the current game.",
        )
    if area == "Lyrics":
        return (
            "Lyrics may be missing, difficult to read, or shown at an unexpected point during playback.",
            "Correct lyric data gives players a readable progression that follows the song.",
        )
    if area == "Audio and artwork":
        return (
            "The song’s audio, preview, or artwork may be missing, inefficient, or displayed incorrectly in FeedBack.",
            "Correct media makes library browsing and song playback complete and reliable.",
        )
    if area in {"Song timeline", "Tones", "Rigs"}:
        return (
            f"{area} may change at the wrong moment, fail to load, or behave differently from the author’s intent.",
            f"Correct {area.lower()} data restores predictable behavior during the song.",
        )
    if area in {"Feedpak structure", "Feedpak format", "Song information"}:
        return (
            "FeedBack may be unable to load the package completely, or different tools may interpret it differently.",
            "A valid, unambiguous package loads more reliably and is safer to move between FeedBack and authoring tools.",
        )
    if area == "Scan":
        return (
            "Library Doctor could not fully inspect this package, so additional problems may remain unreported.",
            "Completing the scan gives you a trustworthy health result before deciding whether the song needs repair.",
        )
    if severity == "error":
        return (
            f"This {area.lower()} problem can prevent part or all of the song from loading or behaving correctly.",
            "Correcting it improves the chance that the package loads and plays as authored.",
        )
    return (
        f"This {area.lower()} data may produce an unexpected result when the song is loaded or played.",
        "Reviewing and correcting it makes the package more predictable and portable, while preserving intentional authoring choices.",
    )


def rule_metadata(code: str, severity: str = "warning", category: str = "validation") -> dict:
    """Return stable, user-facing metadata for a validation rule.

    This catalog is also exposed by the rules endpoint. Keeping labels and
    guidance here prevents the browser UI from guessing meaning from internal
    machine codes and gives future repair tooling an explicit safety class.
    """
    prefix, _, suffix = code.partition(".")
    title = _RULE_TITLES.get(code)
    if title is None:
        title = suffix.replace("-", " ").strip().capitalize() or "Validation issue"
    if code == "chart.note-duplicates-chord":
        repairability = "safe_candidate"
        guidance = (
            "The standalone note exactly repeats a member of the chord. Keep the "
            "complete chord and remove only the matching standalone copy."
        )
    elif code == "chart.zero-length-handshape":
        repairability = "safe_candidate"
        guidance = (
            "Remove it automatically only when one non-arpeggio handshape has no "
            "additional properties and exactly one matching chord already exists at "
            "the same time. Otherwise review it because the handshape may supply a chord."
        )
    elif code == "chart.invalid-handshape-span":
        repairability = "safe_candidate"
        guidance = (
            "Remove it automatically only when the end is earlier than a valid "
            "start, the non-arpeggio handshape has no additional properties, and "
            "exactly one playable matching chord already exists at the same time. "
            "Missing or negative times and unmatched shapes require manual review."
        )
    elif code == "chart.bend-points-out-of-order":
        repairability = "safe_candidate"
        guidance = (
            "Put the existing bend points in chronological order. Preserve every "
            "point and keep equal-time points in their authored order."
        )
    elif code == "lyrics.out-of-order":
        repairability = "safe_candidate"
        guidance = (
            "Put the existing lyric cues in chronological order. Preserve every "
            "cue and keep equal-time cues in their authored order."
        )
    elif code == "chart.phrases-out-of-order":
        repairability = "conditional"
        guidance = (
            "Review the phrase windows and any overlap findings together. Do not "
            "simply sort overlapping phrases because they may repeat playable data."
        )
    elif code in {
        "timeline.beats-out-of-order", "timeline.sections-out-of-order"
    }:
        repairability = "safe_candidate"
        guidance = (
            "Put the existing markers in chronological order only when every marker "
            "is otherwise valid. Preserve every marker and property, and keep "
            "equal-time markers in their authored relative order."
        )
    elif code == "timeline.duplicate-beat":
        repairability = "safe_candidate"
        guidance = (
            "The stored beat markers are identical. Keep the first marker and "
            "remove only later exact copies; leave conflicting beat data for review."
        )
    elif code == "timeline.duplicate-section":
        repairability = "safe_candidate"
        guidance = (
            "The stored section markers are identical. Keep the first marker and "
            "remove only later exact copies; leave conflicting section data for review."
        )
    elif code in {
        "timeline.repeated-beat-time", "timeline.repeated-section-time"
    }:
        repairability = "manual"
        guidance = (
            "The repeated time has different stored data. Choose the intended marker "
            "in an authoring tool; Library Doctor will not guess."
        )
    elif code in _SAFE_REPAIR_CANDIDATES:
        repairability = "safe_candidate"
        guidance = "The repeated entries appear identical. Keep one copy in the source and scan it again."
    elif suffix.endswith(_CONDITIONAL_REPAIR_SUFFIXES):
        repairability = "conditional"
        guidance = "Review the source data before changing it; an automated repair may be possible."
    else:
        repairability = "manual"
        guidance = "Review this part of the song package and correct it in an authoring tool."
    if category == "feedback_compatibility":
        guidance = (
            "The Feedpak value is allowed, but the current FeedBack display or "
            "playback path may not represent it correctly."
        )
    if severity == "info":
        guidance = "Review this if the song behaves or looks wrong in FeedBack."
    area = _RULE_AREAS.get(prefix, category.replace("-", " ").title())
    player_impact, fix_benefit = _rule_experience(code, severity, category, area)
    return {
        "title": title,
        "area": area,
        "confidence": "high" if prefix != "review" else "medium",
        "repairability": repairability,
        "guidance": guidance,
        "player_impact": player_impact,
        "fix_benefit": fix_benefit,
    }

_JSONC_STRIP_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"|'  # JSON string literal: preserve it.
    r"//.*|"                  # Line comment: remove it.
    r"/\*[\s\S]*?\*/",       # Block comment: replace with whitespace.
)

SIDE_FILE_SCHEMAS = {
    "lyrics": "lyrics.schema.json",
    "vocal_pitch": "vocal-pitch.schema.json",
    "song_timeline": "song-timeline.schema.json",
    "drum_tab": "drum-tab.schema.json",
    "vocal_pitch_contour": "vocal-pitch-contour.schema.json",
    "keys": "keys.schema.json",
    "harmony": "harmony.schema.json",
    "rigs": "rigs.schema.json",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    category: str = "validation"
    location: str = ""
    arrangement_id: str | None = None
    time: float | None = None
    string: int | None = None
    affected_count: int = 1

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["rule"] = rule_metadata(self.code, self.severity, self.category)
        payload["evidence"] = {
            key: value
            for key, value in {
                "location": self.location or None,
                "arrangement_id": self.arrangement_id,
                "time": self.time,
                "string": self.string,
            }.items()
            if value is not None
        }
        return payload


@dataclass(frozen=True)
class _OggFacts:
    payload_digest: bytes
    duration_seconds: float | None
    codec: str | None


@dataclass(frozen=True)
class _ImageFacts:
    format: str
    width: int | None
    height: int | None


class _Findings:
    def __init__(self) -> None:
        self.items: list[Finding] = []
        self.counts = {"error": 0, "warning": 0, "info": 0}
        self._dropped = 0

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        category: str = "validation",
        location: str = "",
        arrangement_id: str | None = None,
        time: float | None = None,
        string: int | None = None,
        affected_count: int = 1,
    ) -> None:
        self.counts[severity] += 1
        finding = Finding(
            severity=severity,
            code=code,
            message=_bounded_text(message),
            category=_bounded_text(category, 100),
            location=_bounded_text(location, 500),
            arrangement_id=_bounded_text(arrangement_id, 200) if arrangement_id else None,
            time=round(time, 4) if time is not None and math.isfinite(time) else None,
            string=string,
            affected_count=max(1, int(affected_count)),
        )
        if len(self.items) < MAX_FINDINGS - 1:
            self.items.append(finding)
        else:
            self._dropped += 1

    def finish(self) -> None:
        if not self._dropped:
            return
        self.counts["warning"] += 1
        self.items.append(Finding(
            severity="warning",
            code="scan.findings-truncated",
            message=(
                f"{self._dropped} additional findings were omitted from this report. "
                "Fix the displayed errors and scan the package again."
            ),
        ))


class _PackageReadError(Exception):
    pass


class _PackageBudgetError(_PackageReadError):
    pass


class _BoundedSafeLoader(yaml.SafeLoader):
    """Safe YAML loader with an explicit alias-expansion budget."""

    def __init__(self, stream) -> None:
        super().__init__(stream)
        self._alias_count = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            self._alias_count += 1
            if self._alias_count > MAX_YAML_ALIASES:
                raise _PackageBudgetError(
                    f"manifest.yaml exceeds the {MAX_YAML_ALIASES}-alias safety limit"
                )
        return super().compose_node(parent, index)


def _bounded_text(value, limit: int = 1_000) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_jsonc(text: str):
    """Parse spec-compliant JSONC without depending on FeedBack internals."""
    def strip(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.startswith('"'):
            return value
        if value.startswith("/*"):
            return "\n" * value.count("\n") if "\n" in value else " "
        return ""

    return json.loads(_JSONC_STRIP_RE.sub(strip, text))


def _safe_relpath(value) -> bool:
    """The normative Feedpak POSIX-relative pointer rule (spec section 2.2)."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    return ".." not in parts and "" not in parts[:-1]


def _archive_member_key(name: str) -> str | None:
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        return None
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or None


class _PackageReader:
    """Read individual package members without unpacking audio to disk."""

    def __init__(self, package: Path, findings: _Findings) -> None:
        self.package = package
        self.findings = findings
        self._root: Path | None = None
        self._zip: zipfile.ZipFile | None = None
        self._members: dict[str, zipfile.ZipInfo] = {}
        self._reserved_read_bytes = 0
        self._structure_items = 0

    def __enter__(self) -> "_PackageReader":
        if self.package.is_dir():
            self._root = self.package.resolve()
            member_count = 0
            total_size = 0
            for dirpath, _dirnames, filenames in os.walk(
                self._root, followlinks=False
            ):
                for name in filenames:
                    member_count += 1
                    if member_count > MAX_PACKAGE_MEMBERS:
                        raise _PackageBudgetError(
                            f"Package contains more than {MAX_PACKAGE_MEMBERS:,} files."
                        )
                    try:
                        total_size += (Path(dirpath) / name).stat().st_size
                    except OSError:
                        continue
                    if total_size > MAX_PACKAGE_UNCOMPRESSED_BYTES:
                        raise _PackageBudgetError(
                            "Package contents exceed the validation safety limit."
                        )
            return self
        if not self.package.is_file():
            raise _PackageReadError("Package does not exist or is not a regular file.")
        try:
            self._zip = zipfile.ZipFile(self.package, "r")
            infos = self._zip.infolist()
            if len(infos) > MAX_PACKAGE_MEMBERS:
                raise _PackageBudgetError(
                    f"Package contains more than {MAX_PACKAGE_MEMBERS:,} archive members."
                )
            if sum(info.file_size for info in infos) > MAX_PACKAGE_UNCOMPRESSED_BYTES:
                raise _PackageBudgetError(
                    "Archive contents exceed the validation safety limit."
                )
            casefolded_members: dict[str, str] = {}
            for info in infos:
                key = _archive_member_key(info.filename)
                if key is None:
                    self.findings.add(
                        "error",
                        "package.unsafe-archive-path",
                        f"Archive member uses an unsafe path: {_bounded_text(info.filename, 240)}",
                    )
                    continue
                if info.is_dir():
                    continue
                folded = key.casefold()
                previous_case = casefolded_members.get(folded)
                if previous_case is not None and previous_case != key:
                    self.findings.add(
                        "warning",
                        "package.case-colliding-archive-member",
                        (
                            "Archive members differ only by letter case and can overwrite "
                            f"one another on case-insensitive systems: {previous_case}, {key}"
                        ),
                        location=key,
                    )
                casefolded_members.setdefault(folded, key)
                if key in self._members:
                    self.findings.add(
                        "error",
                        "package.duplicate-archive-member",
                        f"Multiple archive members resolve to the same path: {key}",
                        location=key,
                    )
                # FeedBack's extractor is last-entry-wins, so inspection must be too.
                self._members[key] = info
        except _PackageBudgetError:
            self.close()
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            self.close()
            raise _PackageReadError(f"Package is not a readable ZIP archive ({exc}).") from exc
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def _directory_target(self, relpath: str) -> Path | None:
        if self._root is None or not _safe_relpath(relpath):
            return None
        try:
            target = (self._root / relpath).resolve()
            target.relative_to(self._root)
        except (OSError, ValueError):
            return None
        return target

    def exists(self, relpath: str) -> bool:
        if self._root is not None:
            target = self._directory_target(relpath)
            return bool(target and target.is_file())
        return relpath in self._members

    def size(self, relpath: str) -> int | None:
        if self._root is not None:
            target = self._directory_target(relpath)
            try:
                return target.stat().st_size if target and target.is_file() else None
            except OSError:
                return None
        info = self._members.get(relpath)
        return info.file_size if info is not None else None

    @contextmanager
    def open_binary(self, relpath: str) -> Iterator[BinaryIO]:
        size = self.size(relpath)
        if size is not None:
            self._reserved_read_bytes += size
            if self._reserved_read_bytes > MAX_PACKAGE_READ_BYTES:
                raise _PackageBudgetError(
                    "Package inspection exceeded the total read safety limit."
                )
        if self._root is not None:
            target = self._directory_target(relpath)
            if target is None or not target.is_file():
                raise _PackageReadError(f"Missing package member: {relpath}")
            try:
                with target.open("rb") as stream:
                    yield stream
            except OSError as exc:
                raise _PackageReadError(f"Unable to read {relpath} ({exc}).") from exc
            return

        info = self._members.get(relpath)
        if self._zip is None or info is None:
            raise _PackageReadError(f"Missing package member: {relpath}")
        try:
            with self._zip.open(info, "r") as stream:
                yield stream
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise _PackageReadError(f"Unable to read {relpath} ({exc}).") from exc

    def read_text(self, relpath: str) -> str:
        size = self.size(relpath)
        if size is None:
            raise _PackageReadError(f"Missing package member: {relpath}")
        if size > MAX_TEXT_BYTES:
            raise _PackageReadError(
                f"{relpath} is too large to validate safely "
                f"({size / (1024 * 1024):.1f} MiB; limit {MAX_TEXT_BYTES // (1024 * 1024)} MiB)."
            )
        with self.open_binary(relpath) as stream:
            raw = stream.read(MAX_TEXT_BYTES + 1)
        if len(raw) > MAX_TEXT_BYTES:
            raise _PackageReadError(f"{relpath} exceeds the validation size limit.")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _PackageReadError(f"{relpath} is not valid UTF-8 ({exc}).") from exc

    def inspect_structure(self, data, label: str) -> None:
        """Bound aggregate JSON/YAML traversal work for one package."""
        stack = [data]
        seen: set[int] = set()
        while stack:
            value = stack.pop()
            self._structure_items += 1
            if self._structure_items > MAX_PACKAGE_STRUCTURE_ITEMS:
                raise _PackageBudgetError(
                    f"{label} pushes this package beyond the "
                    f"{MAX_PACKAGE_STRUCTURE_ITEMS:,}-value validation safety limit."
                )
            if isinstance(value, dict):
                if id(value) in seen:
                    continue
                seen.add(id(value))
                stack.extend(value.values())
            elif isinstance(value, list):
                if id(value) in seen:
                    continue
                seen.add(id(value))
                stack.extend(value)


_SCHEMA_CACHE: dict[str, Draft202012Validator] = {}


def _schema(name: str) -> Draft202012Validator:
    validator = _SCHEMA_CACHE.get(name)
    if validator is not None:
        return validator
    with (SCHEMA_DIR / name).open("r", encoding="utf-8") as stream:
        raw = json.load(stream)
    Draft202012Validator.check_schema(raw)
    validator = Draft202012Validator(raw)
    _SCHEMA_CACHE[name] = validator
    return validator


def _json_path(parts) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "<root>"


def _validate_schema(
    data,
    schema_name: str,
    label: str,
    findings: _Findings,
) -> None:
    validator = _schema(schema_name)
    for index, error in enumerate(validator.iter_errors(data)):
        if index >= MAX_SCHEMA_ERRORS_PER_FILE:
            findings.add(
                "warning",
                "spec.schema-errors-truncated",
                (
                    f"{label}: more than {MAX_SCHEMA_ERRORS_PER_FILE} schema errors "
                    "were found; additional schema errors were not evaluated."
                ),
                location=label,
            )
            break
        location = f"{label}:{_json_path(error.path)}"
        findings.add(
            "error",
            "spec.schema",
            f"{location}: {error.message}",
            location=location,
        )


def _report_nonfinite(data, label: str, findings: _Findings) -> None:
    stack = [(data, [])]
    seen_containers: set[int] = set()
    while stack:
        value, path = stack.pop()
        if isinstance(value, float) and not math.isfinite(value):
            location = f"{label}:{_json_path(path)}"
            findings.add(
                "error",
                "spec.nonfinite-number",
                f"{location}: NaN and Infinity are not valid Feedpak numbers.",
                location=location,
            )
        elif isinstance(value, dict):
            if id(value) in seen_containers:
                continue
            seen_containers.add(id(value))
            stack.extend((child, path + [key]) for key, child in value.items())
        elif isinstance(value, list):
            if id(value) in seen_containers:
                continue
            seen_containers.add(id(value))
            stack.extend((child, path + [index]) for index, child in enumerate(value))


def _load_json(
    reader: _PackageReader,
    relpath: str,
    schema_name: str,
    findings: _Findings,
    cache: dict[tuple[str, str], object | None],
):
    key = (relpath, schema_name)
    if key in cache:
        return cache[key]
    try:
        raw = reader.read_text(relpath)
        data = _parse_jsonc(raw) if relpath.lower().endswith(".jsonc") else json.loads(raw)
        reader.inspect_structure(data, relpath)
    except _PackageBudgetError as exc:
        findings.add(
            "error",
            "package.validation-budget-exceeded",
            str(exc),
            location=relpath,
        )
        cache[key] = None
        return None
    except (json.JSONDecodeError, RecursionError, _PackageReadError) as exc:
        findings.add(
            "error",
            "package.invalid-json",
            f"{relpath}: {exc}",
            location=relpath,
        )
        cache[key] = None
        return None
    _validate_schema(data, schema_name, relpath, findings)
    _report_nonfinite(data, relpath, findings)
    cache[key] = data
    return data


def _pointer(
    reader: _PackageReader,
    value,
    key: str,
    findings: _Findings,
) -> str | None:
    # Type/path shape is already reported by the official manifest schema.
    if not _safe_relpath(value):
        return None
    if not reader.exists(value):
        findings.add(
            "error",
            "package.missing-file",
            f"Manifest pointer '{key}' references a missing file: {value}",
            location=key,
        )
        return None
    return value


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _semver_at_least(value, minimum: tuple[int, int, int]) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+].*)?", value)
    if not match:
        return False
    version = tuple(int(part) for part in match.groups())
    return version > minimum or (
        version == minimum and "-" not in value.split("+", 1)[0]
    )


def _duration_mismatch(
    audio_duration: float | None,
    manifest_duration: float | None,
) -> bool:
    if audio_duration is None or manifest_duration is None:
        return False
    if audio_duration < manifest_duration:
        tolerance = max(
            AUDIO_SHORTFALL_TOLERANCE_SECONDS,
            manifest_duration * AUDIO_SHORTFALL_TOLERANCE_RATIO,
        )
        return manifest_duration - audio_duration > tolerance

    # Legacy/custom audio commonly retains a short silent tail even though the
    # manifest describes the audible song length.  This is harmless padding,
    # unlike a stem that ends before the declared song.  Keep a bounded larger
    # allowance in this direction while still catching substantially wrong
    # source files.
    tolerance = max(
        AUDIO_PADDING_TOLERANCE_SECONDS,
        manifest_duration * AUDIO_PADDING_TOLERANCE_RATIO,
    )
    return audio_duration - manifest_duration > tolerance


def _time_key(value: float) -> int:
    return math.floor(value * TIME_PRECISION + 0.5)


def _exact_json_identity(value) -> str | None:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _LaneEvent:
    kind: str
    fret: int
    location: str
    chord_index: int | None
    time: float
    sustain: float
    link_next: bool
    slide_to: int | None
    attributes: tuple[tuple[str, str], ...]
    explicit_chord_note: bool = False
    repair_identity: str | None = None


@dataclass(frozen=True)
class _ChartIssue:
    location: str
    time: float | None = None
    string: int | None = None
    occurrence: tuple | None = None


@dataclass(frozen=True)
class _LaneIssue:
    location: str
    time: float
    string: int | None = None
    detail: str = ""


def _is_keyboard_arrangement(entry, data, notation) -> bool:
    """Return whether same-string fret rules would be invalid for this chart.

    Feedpak keyboard charts encode MIDI pitch through the compact ``s``/``f``
    arrangement shape. Multiple simultaneous pitches can therefore share a
    synthetic string legitimately. Prefer explicit instrument metadata, then
    use the same conventional names FeedBack's import and visualization paths
    recognize for older packages.
    """
    def is_keyboard(value) -> bool:
        return isinstance(value, str) and bool(_KEYBOARD_HINT_RE.search(value.strip()))

    if isinstance(entry, dict):
        instrument_type = entry.get("type")
        if isinstance(instrument_type, str) and instrument_type.strip():
            return is_keyboard(instrument_type)
    if isinstance(notation, dict):
        notation_instrument = notation.get("instrument")
        if isinstance(notation_instrument, str) and notation_instrument.strip():
            return is_keyboard(notation_instrument)
    hints = []
    if isinstance(entry, dict):
        hints.extend((entry.get("id"), entry.get("name")))
    if isinstance(data, dict):
        hints.append(data.get("name"))
    return any(is_keyboard(hint) for hint in hints)


def _is_fretted_arrangement(entry, data, notation) -> bool:
    """Return whether guitar/bass-only semantics apply to this arrangement."""
    if _is_keyboard_arrangement(entry, data, notation):
        return False

    instrument_type = entry.get("type") if isinstance(entry, dict) else None
    if isinstance(instrument_type, str) and instrument_type.strip():
        normalized = instrument_type.strip().lower()
        if normalized in {"guitar", "bass"}:
            return True
        if normalized in {
            "vocals", "vocal", "drums", "drum", "keys", "piano", "keyboard",
        }:
            return False

    if not isinstance(data, dict):
        return False
    for note in data.get("notes", []) if isinstance(data.get("notes"), list) else []:
        if (
            isinstance(note, dict)
            and _integer(note.get("s")) is not None
            and _integer(note.get("f")) is not None
        ):
            return True
    for chord in data.get("chords", []) if isinstance(data.get("chords"), list) else []:
        if not isinstance(chord, dict):
            continue
        chord_notes = chord.get("notes", []) if isinstance(chord.get("notes"), list) else []
        for note in chord_notes:
            if (
                isinstance(note, dict)
                and _integer(note.get("s")) is not None
                and _integer(note.get("f")) is not None
            ):
                return True
    for phrase in data.get("phrases", []) if isinstance(data.get("phrases"), list) else []:
        if not isinstance(phrase, dict):
            continue
        levels = phrase.get("levels", []) if isinstance(phrase.get("levels"), list) else []
        for level in levels:
            if not isinstance(level, dict):
                continue
            for note in level.get("notes", []) if isinstance(level.get("notes"), list) else []:
                if (
                    isinstance(note, dict)
                    and _integer(note.get("s")) is not None
                    and _integer(note.get("f")) is not None
                ):
                    return True
            for chord in level.get("chords", []) if isinstance(level.get("chords"), list) else []:
                if not isinstance(chord, dict):
                    continue
                chord_notes = chord.get("notes", []) if isinstance(chord.get("notes"), list) else []
                for note in chord_notes:
                    if (
                        isinstance(note, dict)
                        and _integer(note.get("s")) is not None
                        and _integer(note.get("f")) is not None
                    ):
                        return True
    return False


def _is_explicitly_fretted(entry) -> bool:
    instrument_type = entry.get("type") if isinstance(entry, dict) else None
    return (
        isinstance(instrument_type, str)
        and instrument_type.strip().lower() in {"guitar", "bass"}
    )


class _TabValidator:
    """Deterministic arrangement checks tied to FeedBack's playback behavior.

    Findings are aggregated by rule and arrangement. A malformed song can
    contain thousands of repeated events; one concise finding per rule keeps a
    whole-library report useful and preserves the global finding bound.
    """

    def __init__(
        self,
        *,
        data: dict,
        relpath: str,
        arrangement_id: str,
        duration: float | None,
        findings: _Findings,
        entry,
        check_fretted: bool,
    ) -> None:
        self.data = data
        self.relpath = relpath
        self.arrangement_id = arrangement_id
        self.duration = duration
        self.findings = findings
        self.entry = entry if isinstance(entry, dict) else {}
        self.check_fretted = check_fretted
        self.notes = data.get("notes", []) if isinstance(data.get("notes"), list) else []
        self.chords = data.get("chords", []) if isinstance(data.get("chords"), list) else []
        self.anchors = data.get("anchors", []) if isinstance(data.get("anchors"), list) else []
        self.handshapes = data.get("handshapes", []) if isinstance(data.get("handshapes"), list) else []
        self.templates = data.get("templates", []) if isinstance(data.get("templates"), list) else []
        self.phrases = data.get("phrases", []) if isinstance(data.get("phrases"), list) else []
        self.issues: dict[str, list[_ChartIssue]] = {}
        self.phrase_playable_events = 0

        tuning = data.get("tuning")
        if not isinstance(tuning, list) or not tuning:
            tuning = self.entry.get("tuning")
        self.tuning = tuning if isinstance(tuning, list) else None
        self.tuning_length = len(tuning) if isinstance(tuning, list) else None

    def _record(
        self,
        code: str,
        location: str,
        *,
        time=None,
        string=None,
        occurrence: tuple | None = None,
    ) -> None:
        self.issues.setdefault(code, []).append(_ChartIssue(
            location=location,
            time=_number(time),
            string=_integer(string),
            occurrence=occurrence,
        ))

    def _emit(
        self,
        code: str,
        severity: str,
        message,
        *,
        category: str = "validation",
    ) -> None:
        issues = self.issues.get(code)
        if not issues:
            return
        if any(issue.occurrence is not None for issue in issues):
            unique: dict[tuple, _ChartIssue] = {}
            for issue in issues:
                key = (
                    ("event", issue.occurrence)
                    if issue.occurrence is not None
                    else ("location", issue.location)
                )
                unique.setdefault(key, issue)
            issues = list(unique.values())
        first = issues[0]
        self.findings.add(
            severity,
            code,
            message(len(issues)),
            category=category,
            location=first.location,
            arrangement_id=self.arrangement_id,
            time=first.time,
            string=first.string,
            affected_count=len(issues),
        )

    def _check_order(self, items, key: str, code: str, path: str) -> None:
        previous: float | None = None
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            value = _number(raw.get(key))
            if value is None:
                continue
            if previous is not None and value < previous:
                self._record(code, f"{path}[{index}]", time=value)
                return
            previous = value

    def _inspect_exact_array_duplicates(
        self,
        items: list,
        *,
        code: str,
        path: str,
        time_key: str,
        occurrence_kind: str,
    ) -> None:
        groups: dict[str, list[int]] = {}
        for index, raw in enumerate(items):
            if not isinstance(raw, dict) or _number(raw.get(time_key)) is None:
                continue
            identity = _exact_json_identity(raw)
            if identity is not None:
                groups.setdefault(identity, []).append(index)
        for identity, indices in groups.items():
            if len(indices) < 2:
                continue
            first_index = indices[0]
            raw = items[first_index]
            event_time = _number(raw.get(time_key))
            self._record(
                code,
                f"{path}[{first_index}]",
                time=event_time,
                occurrence=(
                    occurrence_kind,
                    hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                ),
            )

    def _valid_template_ref(self, raw_id) -> tuple[int | None, dict | None]:
        template_id = _integer(raw_id)
        if template_id is None or not (0 <= template_id < len(self.templates)):
            return template_id, None
        template = self.templates[template_id]
        return template_id, template if isinstance(template, dict) else None

    @staticmethod
    def _template_has_playable_note(template: dict | None) -> bool:
        frets = template.get("frets") if isinstance(template, dict) else None
        if not isinstance(frets, list):
            return False
        return any(
            _integer(fret) is not None
            and fret >= 0
            and string < HIGHWAY_MAX_STRINGS
            for string, fret in enumerate(frets)
        )

    def _inspect_note(self, raw, location: str, *, event_time=None) -> None:
        if not isinstance(raw, dict):
            return
        note_time = _number(raw.get("t") if event_time is None else event_time)
        string = _integer(raw.get("s"))
        fret = _integer(raw.get("f"))
        sustain = _number(raw.get("sus", 0))
        note_occurrence = (
            "note",
            _time_key(note_time) if note_time is not None else None,
            string,
            fret,
        )

        if self.check_fretted and fret is not None:
            # FeedBack deliberately supports ``f: -1`` as the positionless
            # fret-hand-mute sentinel when the event carries either of its
            # mute flags. The loader preserves it and the 3D highway draws the
            # muted X at the open/string lane. Other negative frets remain
            # invalid; requiring an actual boolean avoids accepting malformed
            # values such as ``"false"`` that the loader would coerce truthy.
            supported_fret_hand_mute = (
                fret == -1
                and (raw.get("mt") is True or raw.get("fhm") is True)
            )
            if fret < 0 and not supported_fret_hand_mute:
                self._record(
                    "chart.negative-fret", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
            elif fret > HIGHWAY_MAX_FRET:
                self._record(
                    "chart.fret-beyond-highway", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
        if self.check_fretted and string is not None:
            if string >= HIGHWAY_MAX_STRINGS:
                self._record(
                    "chart.string-beyond-highway", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
            if self.tuning_length is not None and string >= self.tuning_length:
                self._record(
                    "chart.string-without-tuning", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )

        if sustain is not None:
            if sustain < 0:
                self._record(
                    "chart.negative-sustain", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
            if (
                self.duration is not None
                and self.duration > 0
                and note_time is not None
                and note_time + max(0, sustain)
                > self.duration + CHART_END_TOLERANCE_SECONDS
            ):
                self._record(
                    "chart.sustain-after-duration", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )

        if not self.check_fretted:
            return

        slide_to = _integer(raw.get("sl"))
        slide_unpitched_to = _integer(raw.get("slu"))
        if slide_to is not None and slide_to < -1:
            self._record(
                "chart.invalid-slide-target", location, time=note_time, string=string,
                occurrence=note_occurrence + ("sl",),
            )
        if slide_unpitched_to is not None and slide_unpitched_to < -1:
            self._record(
                "chart.invalid-slide-target", location, time=note_time, string=string,
                occurrence=note_occurrence + ("slu",),
            )
        has_pitched_slide = slide_to is not None and slide_to >= 0
        has_unpitched_slide = slide_unpitched_to is not None and slide_unpitched_to >= 0
        if has_pitched_slide and has_unpitched_slide:
            self._record(
                "chart.ambiguous-slide", location, time=note_time, string=string,
                occurrence=note_occurrence,
            )
        slide_target = (
            slide_to if has_pitched_slide
            else slide_unpitched_to if has_unpitched_slide
            else None
        )
        if slide_target is not None:
            if slide_target > HIGHWAY_MAX_FRET:
                self._record(
                    "chart.slide-beyond-highway", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
            if sustain is None or sustain <= 0:
                self._record(
                    "chart.slide-without-sustain", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
            if fret == 0:
                self._record(
                    "chart.open-string-slide", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )
            if fret is not None and fret == slide_target:
                self._record(
                    "chart.no-op-slide", location, time=note_time, string=string,
                    occurrence=note_occurrence,
                )

        for field in NOTE_BOOLEAN_FIELDS:
            if field in raw and not isinstance(raw[field], bool):
                self._record(
                    "chart.technique-not-boolean",
                    f"{location}.{field}",
                    time=note_time,
                    string=string,
                    occurrence=note_occurrence + (field,),
                )
        if raw.get("ho") is True and raw.get("po") is True:
            self._record(
                "chart.conflicting-techniques", location, time=note_time, string=string,
                occurrence=note_occurrence,
            )

        bend_peak = _number(raw.get("bn"))
        if bend_peak is not None and bend_peak < 0:
            self._record(
                "chart.negative-bend", location, time=note_time, string=string,
                occurrence=note_occurrence + ("peak",),
            )

        bend_values = raw.get("bnv")
        if not isinstance(bend_values, list):
            return
        valid_points = []
        for point in bend_values:
            if not isinstance(point, dict):
                continue
            point_time = _number(point.get("t"))
            point_value = _number(point.get("v"))
            if point_time is not None and point_value is not None:
                valid_points.append((point_time, point_value))
        if any(point_value < 0 for _point_time, point_value in valid_points):
            self._record(
                "chart.negative-bend", location, time=note_time, string=string,
                occurrence=note_occurrence + ("curve",),
            )
        if any(
            current[0] < previous[0]
            for previous, current in zip(valid_points, valid_points[1:])
        ):
            self._record(
                "chart.bend-points-out-of-order", location, time=note_time, string=string,
                occurrence=note_occurrence,
            )
        if sustain is not None and any(
            point_time < 0 or point_time > max(0, sustain) + BEND_CURVE_TOLERANCE_SECONDS
            for point_time, _point_value in valid_points
        ):
            self._record(
                "chart.bend-point-outside-sustain", location, time=note_time, string=string,
                occurrence=note_occurrence,
            )
        if bend_peak is not None and any(
            point_value > bend_peak + 0.01
            for _point_time, point_value in valid_points
        ):
            self._record(
                "chart.bend-exceeds-peak", location, time=note_time, string=string,
                occurrence=note_occurrence,
            )


    def _inspect_chord(self, raw, location: str) -> None:
        if not isinstance(raw, dict):
            return
        chord_time = _number(raw.get("t"))
        chord_notes = raw.get("notes", []) if isinstance(raw.get("notes"), list) else []
        template_id, template = self._valid_template_ref(raw.get("id", 0))
        chord_occurrence = (
            "chord",
            _time_key(chord_time) if chord_time is not None else None,
            template_id,
        )
        chord_note_groups: dict[str, list[int]] = {}
        for note_index, note in enumerate(chord_notes):
            if chord_time is None or not isinstance(note, dict):
                continue
            string = _integer(note.get("s"))
            fret = _integer(note.get("f"))
            if string is None or string < 0 or fret is None:
                continue
            identity = _exact_json_identity(note)
            if identity is not None:
                chord_note_groups.setdefault(identity, []).append(note_index)
        for identity, indices in chord_note_groups.items():
            if len(indices) < 2:
                continue
            first_note = chord_notes[indices[0]]
            self._record(
                "chart.duplicate-chord-note",
                f"{location}.notes[{indices[0]}]",
                time=chord_time,
                string=first_note.get("s"),
                occurrence=(
                    "duplicate-chord-note",
                    _time_key(chord_time) if chord_time is not None else None,
                    _integer(first_note.get("s")),
                    _integer(first_note.get("f")),
                    hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                ),
            )
        if self.check_fretted and template is None and ("id" in raw or not chord_notes):
            self._record(
                "chart.missing-chord-template", f"{location}.id", time=chord_time,
                occurrence=chord_occurrence,
            )
        if (
            self.check_fretted
            and not chord_notes
            and not self._template_has_playable_note(template)
        ):
            self._record(
                "chart.invisible-chord", location, time=chord_time,
                occurrence=chord_occurrence,
            )

        template_frets = template.get("frets") if isinstance(template, dict) else None
        effective_frets = []
        if chord_notes:
            effective_frets = [
                fret
                for note in chord_notes
                if isinstance(note, dict)
                and (fret := _integer(note.get("f"))) is not None
                and fret > 0
            ]
        elif isinstance(template_frets, list):
            effective_frets = [
                fret
                for raw_fret in template_frets
                if (fret := _integer(raw_fret)) is not None and fret > 0
            ]
        if (
            self.check_fretted
            and effective_frets
            and max(effective_frets) - min(effective_frets) >= EXTREME_CHORD_SPAN_FRETS
        ):
            self._record(
                "review.extreme-chord-span", location, time=chord_time,
                occurrence=chord_occurrence,
            )

        for note_index, note in enumerate(chord_notes):
            note_location = f"{location}.notes[{note_index}]"
            self._inspect_note(note, note_location, event_time=chord_time)
            if (
                not self.check_fretted
                or not isinstance(note, dict)
                or not isinstance(template_frets, list)
            ):
                continue
            string = _integer(note.get("s"))
            fret = _integer(note.get("f"))
            if string is None or fret is None or not (0 <= string < len(template_frets)):
                continue
            template_fret = _integer(template_frets[string])
            if template_fret is not None and template_fret != fret:
                self._record(
                    "chart.chord-template-mismatch",
                    note_location,
                    time=chord_time,
                    string=string,
                    occurrence=chord_occurrence + (string, fret),
                )

    def _inspect_anchor(self, raw, location: str) -> None:
        if not isinstance(raw, dict):
            return
        anchor_time = _number(raw.get("time"))
        fret = _integer(raw.get("fret"))
        width = _integer(raw.get("width", 4))
        anchor_occurrence = (
            "anchor",
            _time_key(anchor_time) if anchor_time is not None else None,
            fret,
            width,
        )
        if anchor_time is not None and anchor_time < 0:
            self._record(
                "chart.negative-anchor-time", location, time=anchor_time,
                occurrence=anchor_occurrence,
            )
        if (
            self.duration is not None
            and self.duration > 0
            and anchor_time is not None
            and anchor_time > self.duration + CHART_END_TOLERANCE_SECONDS
        ):
            self._record(
                "chart.anchor-after-duration", location, time=anchor_time,
                occurrence=anchor_occurrence,
            )
        if not self.check_fretted:
            return
        if fret is not None and fret < 0:
            self._record(
                "chart.invalid-anchor", location, time=anchor_time,
                occurrence=anchor_occurrence,
            )
        if width is not None and width <= 0:
            self._record(
                "chart.invalid-anchor", location, time=anchor_time,
                occurrence=anchor_occurrence,
            )
        if fret is not None and width is not None and width > 0:
            effective_start = max(1, fret)
            if (
                fret > HIGHWAY_MAX_FRET
                or effective_start + width - 1 > HIGHWAY_MAX_FRET
            ):
                self._record(
                    "chart.anchor-beyond-highway", location, time=anchor_time,
                    occurrence=anchor_occurrence,
                )

    def _inspect_handshape(self, raw, location: str) -> None:
        if not isinstance(raw, dict):
            return
        start = _number(raw.get("start_time"))
        end = _number(raw.get("end_time"))
        template_id, template = self._valid_template_ref(raw.get("chord_id"))
        handshape_occurrence = (
            "handshape",
            _time_key(start) if start is not None else None,
            _time_key(end) if end is not None else None,
            template_id,
        )
        if start is None or end is None or start < 0 or end < start:
            self._record(
                "chart.invalid-handshape-span", location, time=start,
                occurrence=handshape_occurrence,
            )
        elif end == start:
            self._record(
                "chart.zero-length-handshape", location, time=start,
                occurrence=handshape_occurrence,
            )
        if self.check_fretted and template is None:
            self._record(
                "chart.missing-handshape-template",
                f"{location}.chord_id",
                time=start,
                occurrence=handshape_occurrence,
            )
        if (
            self.duration is not None
            and self.duration > 0
            and end is not None
            and end > self.duration + CHART_END_TOLERANCE_SECONDS
        ):
            self._record(
                "chart.handshape-after-duration", location, time=start,
                occurrence=handshape_occurrence,
            )

    def _inspect_templates(self) -> None:
        if not self.check_fretted:
            return
        for template_index, template in enumerate(self.templates):
            if not isinstance(template, dict):
                continue
            location = f"{self.relpath}:templates[{template_index}]"
            frets = template.get("frets")
            fingers = template.get("fingers")
            if (
                (isinstance(frets, list) and len(frets) > HIGHWAY_MAX_STRINGS)
                or (isinstance(fingers, list) and len(fingers) > HIGHWAY_MAX_STRINGS)
            ):
                self._record("chart.template-beyond-highway", location)
            if isinstance(frets, list):
                for string, raw_fret in enumerate(frets):
                    fret = _integer(raw_fret)
                    if fret is not None and fret < -1:
                        self._record(
                            "chart.invalid-template-fret",
                            f"{location}.frets[{string}]",
                            string=string,
                        )
                    elif fret is not None and fret > HIGHWAY_MAX_FRET:
                        self._record(
                            "chart.template-fret-beyond-highway",
                            f"{location}.frets[{string}]",
                            string=string,
                        )
            if isinstance(fingers, list):
                for string, raw_finger in enumerate(fingers):
                    finger = _integer(raw_finger)
                    if finger is not None and not (-1 <= finger <= 4):
                        self._record(
                            "chart.invalid-template-finger",
                            f"{location}.fingers[{string}]",
                            string=string,
                        )
            if (
                isinstance(frets, list)
                and isinstance(fingers, list)
                and len(frets) != len(fingers)
            ):
                self._record("chart.template-array-mismatch", location)
            if isinstance(frets, list) and isinstance(fingers, list):
                finger_frets: dict[int, set[int]] = {}
                for raw_fret, raw_finger in zip(frets, fingers):
                    fret = _integer(raw_fret)
                    finger = _integer(raw_finger)
                    # FeedBack uses 0 as open/unassigned. Only fretting fingers
                    # 1-4 can be physically assigned to conflicting frets.
                    if fret is None or fret <= 0 or finger is None or finger <= 0:
                        continue
                    finger_frets.setdefault(finger, set()).add(fret)
                if any(len(used_frets) > 1 for used_frets in finger_frets.values()):
                    self._record("review.impossible-chord-fingering", location)

    def _inspect_conflicting_anchors(self) -> None:
        groups: dict[int, set[tuple[int, int]]] = {}
        locations: dict[int, str] = {}
        for index, anchor in enumerate(self.anchors):
            if not isinstance(anchor, dict):
                continue
            anchor_time = _number(anchor.get("time"))
            fret = _integer(anchor.get("fret"))
            width = _integer(anchor.get("width", 4))
            if anchor_time is None or fret is None or width is None:
                continue
            key = _time_key(anchor_time)
            groups.setdefault(key, set()).add((fret, width))
            locations.setdefault(key, f"{self.relpath}:anchors[{index}]")
        for key, windows in groups.items():
            if len(windows) > 1:
                self._record(
                    "chart.conflicting-anchors",
                    locations[key],
                    time=key / TIME_PRECISION,
                )

    def _inspect_exact_stream_duplicates(
        self,
        *,
        chords: list,
        anchors: list,
        handshapes: list,
        path_prefix: str,
    ) -> None:
        self._inspect_exact_array_duplicates(
            chords,
            code="chart.duplicate-chord",
            path=f"{path_prefix}chords",
            time_key="t",
            occurrence_kind="duplicate-chord",
        )
        self._inspect_exact_array_duplicates(
            anchors,
            code="chart.duplicate-anchor",
            path=f"{path_prefix}anchors",
            time_key="time",
            occurrence_kind="duplicate-anchor",
        )
        self._inspect_exact_array_duplicates(
            handshapes,
            code="chart.duplicate-handshape",
            path=f"{path_prefix}handshapes",
            time_key="start_time",
            occurrence_kind="duplicate-handshape",
        )

    def _inspect_phrases(self) -> None:
        previous_phrase_end: float | None = None
        for phrase_index, phrase in enumerate(self.phrases):
            phrase_location = f"{self.relpath}:phrases[{phrase_index}]"
            if not isinstance(phrase, dict):
                self._record("chart.invalid-phrase-data", phrase_location)
                continue
            start = _number(phrase.get("start_time"))
            end = _number(phrase.get("end_time"))
            if start is None or end is None or start < 0 or end <= start:
                self._record("chart.invalid-phrase-span", phrase_location, time=start)
            if (
                self.duration is not None
                and self.duration > 0
                and end is not None
                and end > self.duration + TIMELINE_SIGNIFICANT_OVERRUN_SECONDS
            ):
                self._record("chart.phrase-after-duration", phrase_location, time=start)
            if (
                previous_phrase_end is not None
                and start is not None
                and start < previous_phrase_end - 0.001
            ):
                self._record("chart.overlapping-phrases", phrase_location, time=start)
            if end is not None:
                previous_phrase_end = end

            levels = phrase.get("levels")
            if not isinstance(levels, list):
                self._record("chart.invalid-phrase-data", f"{phrase_location}.levels", time=start)
                continue
            if not levels:
                self._record("chart.empty-phrase-levels", f"{phrase_location}.levels", time=start)
                continue

            max_difficulty = _integer(phrase.get("max_difficulty"))
            seen_difficulties: set[int] = set()
            previous_difficulty: int | None = None
            for level_index, level in enumerate(levels):
                level_location = f"{phrase_location}.levels[{level_index}]"
                if not isinstance(level, dict):
                    self._record("chart.invalid-phrase-data", level_location, time=start)
                    continue
                difficulty = _integer(level.get("difficulty"))
                if difficulty is None or difficulty < 0 or (
                    max_difficulty is not None and difficulty > max_difficulty
                ):
                    self._record(
                        "chart.invalid-difficulty-level",
                        f"{level_location}.difficulty",
                        time=start,
                    )
                if difficulty is not None:
                    if difficulty in seen_difficulties:
                        self._record(
                            "chart.duplicate-difficulty-level",
                            f"{level_location}.difficulty",
                            time=start,
                        )
                    if previous_difficulty is not None and difficulty < previous_difficulty:
                        self._record(
                            "chart.phrase-levels-out-of-order",
                            f"{level_location}.difficulty",
                            time=start,
                        )
                    seen_difficulties.add(difficulty)
                    previous_difficulty = difficulty

                arrays = self._phrase_level_arrays(level, level_location, start, end)
                self._inspect_exact_stream_duplicates(
                    chords=arrays["chords"],
                    anchors=arrays["anchors"],
                    handshapes=arrays["handshapes"],
                    path_prefix=f"{level_location}.",
                )
                self.phrase_playable_events += len(arrays["notes"]) + len(arrays["chords"])
                for note_index, note in enumerate(arrays["notes"]):
                    self._inspect_note(note, f"{level_location}.notes[{note_index}]")
                for chord_index, chord in enumerate(arrays["chords"]):
                    self._inspect_chord(chord, f"{level_location}.chords[{chord_index}]")
                for anchor_index, anchor in enumerate(arrays["anchors"]):
                    self._inspect_anchor(anchor, f"{level_location}.anchors[{anchor_index}]")
                for handshape_index, handshape in enumerate(arrays["handshapes"]):
                    self._inspect_handshape(
                        handshape,
                        f"{level_location}.handshapes[{handshape_index}]",
                    )

    def _phrase_level_arrays(
        self,
        level: dict,
        level_location: str,
        phrase_start: float | None,
        phrase_end: float | None,
    ) -> dict[str, list]:
        arrays: dict[str, list] = {}
        fields = (
            ("notes", "t"),
            ("chords", "t"),
            ("anchors", "time"),
            ("handshapes", "start_time"),
        )
        for field, time_key in fields:
            value = level.get(field, [])
            if not isinstance(value, list):
                self._record(
                    "chart.invalid-phrase-data",
                    f"{level_location}.{field}",
                    time=phrase_start,
                )
                value = []
            arrays[field] = value
            # FeedBack explicitly sorts handshapes after mastery filtering. The
            # other timelines are concatenated as-authored and must be ordered.
            if field != "handshapes":
                self._check_order(
                    value,
                    time_key,
                    "chart.phrase-events-out-of-order",
                    f"{level_location}.{field}",
                )
            for event_index, event in enumerate(value):
                if not isinstance(event, dict):
                    self._record(
                        "chart.invalid-phrase-data",
                        f"{level_location}.{field}[{event_index}]",
                        time=phrase_start,
                    )
                    continue
                event_time = _number(event.get(time_key))
                if (
                    phrase_start is not None
                    and phrase_end is not None
                    and event_time is not None
                    and (
                        event_time < phrase_start - 0.001
                        or event_time > phrase_end + 0.001
                    )
                ):
                    self._record(
                        "chart.phrase-event-outside-window",
                        f"{level_location}.{field}[{event_index}]",
                        time=event_time,
                    )
        return arrays

    def _inspect_mastery_stream_order(self) -> None:
        """Check the timelines FeedBack can actually build from phrase levels.

        A phrase marker can be structurally out of order without affecting the
        highway when all of its selectable levels are empty. FeedBack selects
        one level per phrase for a mastery fraction, then concatenates notes,
        chords, and anchors in authored phrase order. Check every distinct
        selection profile up to a deterministic bound and report an error only
        when one of those playable streams is itself nonchronological.
        """
        phrase_levels: list[
            tuple[int, list[dict[str, tuple[int, float, float]]]]
        ] = []
        profiles = {Fraction(0, 1), Fraction(1, 1)}
        for phrase_index, phrase in enumerate(self.phrases):
            if not isinstance(phrase, dict):
                continue
            levels = phrase.get("levels")
            if not isinstance(levels, list) or not levels:
                continue
            level_count = len(levels)
            level_edges: list[dict[str, tuple[int, float, float]]] = []
            for level in levels:
                edges: dict[str, tuple[int, float, float]] = {}
                if isinstance(level, dict):
                    for field, time_key in (
                        ("notes", "t"), ("chords", "t"), ("anchors", "time")
                    ):
                        events = level.get(field)
                        if not isinstance(events, list):
                            continue
                        first: tuple[int, float] | None = None
                        last_time: float | None = None
                        for event_index, event in enumerate(events):
                            if not isinstance(event, dict):
                                continue
                            event_time = _number(event.get(time_key))
                            if event_time is None:
                                continue
                            if first is None:
                                first = (event_index, event_time)
                            last_time = event_time
                        if first is not None and last_time is not None:
                            edges[field] = (first[0], first[1], last_time)
                level_edges.append(edges)
            phrase_levels.append((phrase_index, level_edges))
            profiles.update(
                Fraction(level_index, level_count)
                for level_index in range(1, level_count)
            )

        ordered_profiles = sorted(profiles)
        if len(ordered_profiles) > MAX_MASTERY_PROFILES:
            last_index = len(ordered_profiles) - 1
            ordered_profiles = [
                ordered_profiles[(sample * last_index) // (MAX_MASTERY_PROFILES - 1)]
                for sample in range(MAX_MASTERY_PROFILES)
            ]

        for profile in ordered_profiles:
            previous_times: dict[str, float] = {}
            for phrase_index, level_edges in phrase_levels:
                level_index = min(
                    len(level_edges) - 1,
                    (profile.numerator * len(level_edges)) // profile.denominator,
                )
                level_location = (
                    f"{self.relpath}:phrases[{phrase_index}].levels[{level_index}]"
                )
                for field, edge in level_edges[level_index].items():
                    first_index, first_time, last_time = edge
                    previous_time = previous_times.get(field)
                    if previous_time is not None and first_time < previous_time:
                        self._record(
                            "chart.mastery-events-out-of-order",
                            f"{level_location}.{field}[{first_index}]",
                            time=first_time,
                        )
                        return
                    previous_times[field] = last_time

    def validate(self) -> None:
        self._check_order(
            self.notes, "t", "chart.notes-out-of-order", f"{self.relpath}:notes",
        )
        self._check_order(
            self.chords, "t", "chart.chords-out-of-order", f"{self.relpath}:chords",
        )
        self._check_order(
            self.anchors, "time", "chart.anchors-out-of-order", f"{self.relpath}:anchors",
        )
        self._check_order(
            self.phrases,
            "start_time",
            "chart.phrases-out-of-order",
            f"{self.relpath}:phrases",
        )

        capo = self.data.get("capo")
        if capo is None:
            capo = self.entry.get("capo")
        if self.check_fretted and _integer(capo) is not None and capo < 0:
            self._record("chart.negative-capo", f"{self.relpath}:capo")
        if self.check_fretted and _integer(capo) is not None and capo > HIGHWAY_MAX_FRET:
            self._record("chart.capo-beyond-highway", f"{self.relpath}:capo")
        if self.check_fretted and self.tuning and len(self.tuning) > HIGHWAY_MAX_STRINGS:
            self._record("chart.tuning-beyond-highway", f"{self.relpath}:tuning")

        for index, note in enumerate(self.notes):
            self._inspect_note(note, f"{self.relpath}:notes[{index}]")
        for index, chord in enumerate(self.chords):
            self._inspect_chord(chord, f"{self.relpath}:chords[{index}]")
        for index, anchor in enumerate(self.anchors):
            self._inspect_anchor(anchor, f"{self.relpath}:anchors[{index}]")
        for index, handshape in enumerate(self.handshapes):
            self._inspect_handshape(handshape, f"{self.relpath}:handshapes[{index}]")

        self._inspect_exact_stream_duplicates(
            chords=self.chords,
            anchors=self.anchors,
            handshapes=self.handshapes,
            path_prefix=f"{self.relpath}:",
        )
        self._inspect_templates()
        self._inspect_conflicting_anchors()
        self._inspect_phrases()
        self._inspect_mastery_stream_order()
        if (
            _is_explicitly_fretted(self.entry)
            and not self.notes
            and not self.chords
            and self.phrase_playable_events == 0
        ):
            self._record("chart.empty-fretted-arrangement", self.relpath)
        self._emit_findings()

    def _emit_findings(self) -> None:
        rules = (
            (
                "chart.duplicate-chord-note", "warning",
                lambda count: f"{count} chord position(s) contain an identical repeated member; keep one copy on that string.",
            ),
            (
                "chart.duplicate-chord", "warning",
                lambda count: f"{count} position(s) contain an identical repeated chord event; keep one complete chord.",
            ),
            (
                "chart.duplicate-anchor", "warning",
                lambda count: f"{count} position(s) contain an identical repeated anchor; keep one fret-window instruction.",
            ),
            (
                "chart.duplicate-handshape", "warning",
                lambda count: f"{count} position(s) contain an identical repeated handshape; keep one chord-shape guide.",
            ),
            (
                "chart.notes-out-of-order", "error",
                lambda count: f"{count} note timeline(s) are not ordered by start time; FeedBack's highway can skip or delay these notes.",
            ),
            (
                "chart.chords-out-of-order", "error",
                lambda count: f"{count} chord timeline(s) are not ordered by start time; FeedBack's highway can skip or delay these chords.",
            ),
            (
                "chart.anchors-out-of-order", "error",
                lambda count: f"{count} anchor timeline(s) are not ordered by time, so the highway can select the wrong fret window.",
            ),
            (
                "chart.phrases-out-of-order", "warning",
                lambda count: f"{count} phrase timeline(s) move backward in time; review overlaps and repeated windows before changing their order.",
            ),
            (
                "chart.mastery-events-out-of-order", "error",
                lambda _count: "At least one mastery setting produces a nonchronological note, chord, or anchor stream; the highway can skip or delay those events.",
            ),
            (
                "chart.phrase-events-out-of-order", "error",
                lambda count: f"{count} difficulty-level timeline(s) are not chronological; the mastery view can omit or mis-time events.",
            ),
            ("chart.negative-capo", "error", lambda count: f"{count} arrangement capo value(s) are negative."),
            (
                "chart.capo-beyond-highway", "warning",
                lambda count: f"{count} arrangement capo value(s) are above fret {HIGHWAY_MAX_FRET}, beyond the current 3D highway.",
            ),
            (
                "chart.tuning-beyond-highway", "warning",
                lambda count: f"{count} arrangement tuning(s) define more than {HIGHWAY_MAX_STRINGS} strings; the current 3D highway cannot show the extras.",
            ),
            (
                "chart.negative-fret", "error",
                lambda count: (
                    f"{count} note(s) use a negative fret without the supported "
                    "-1 fret-hand-mute marker."
                ),
            ),
            (
                "chart.fret-beyond-highway", "warning",
                lambda count: f"{count} note(s) use a fret above {HIGHWAY_MAX_FRET}, beyond the current 3D highway.",
            ),
            (
                "chart.string-beyond-highway", "warning",
                lambda count: f"{count} note(s) use string index {HIGHWAY_MAX_STRINGS} or higher; the current 3D highway drops them.",
            ),
            (
                "chart.string-without-tuning", "warning",
                lambda count: f"{count} note(s) use a string with no corresponding tuning entry; pitch detection can be unreliable.",
            ),
            ("chart.negative-sustain", "error", lambda count: f"{count} note(s) have a negative sustain duration."),
            (
                "chart.sustain-after-duration", "warning",
                lambda count: f"{count} note sustain(s) extend beyond the manifest duration.",
            ),
            (
                "chart.ambiguous-slide", "warning",
                lambda count: f"{count} note(s) declare both pitched and unpitched slide targets; FeedBack displays the pitched slide.",
            ),
            (
                "chart.invalid-slide-target", "error",
                lambda count: f"{count} slide target value(s) are below the -1 no-slide sentinel.",
            ),
            (
                "chart.slide-beyond-highway", "warning",
                lambda count: f"{count} slide target(s) are above fret {HIGHWAY_MAX_FRET}, beyond the current 3D highway.",
            ),
            (
                "chart.slide-without-sustain", "warning",
                lambda count: f"{count} slide note(s) have no positive sustain, so the 3D highway cannot animate their movement.",
            ),
            (
                "chart.open-string-slide", "warning",
                lambda count: f"{count} slide(s) start at fret 0; the current 3D highway does not animate lateral movement from an open string.",
            ),
            ("chart.no-op-slide", "warning", lambda count: f"{count} slide(s) target their starting fret and show no movement."),
            (
                "chart.technique-not-boolean", "error",
                lambda count: f"{count} technique value(s) are not true/false; FeedBack can interpret strings such as 'false' as enabled.",
            ),
            (
                "chart.conflicting-techniques", "warning",
                lambda count: f"{count} note(s) combine mutually exclusive techniques whose 3D symbols have renderer precedence.",
            ),
            (
                "chart.bend-points-out-of-order", "warning",
                lambda count: f"{count} bend curve(s) have out-of-order points; FeedBack repairs their order while loading.",
            ),
            (
                "chart.bend-point-outside-sustain", "warning",
                lambda count: f"{count} bend curve(s) contain points before the note or after its sustain.",
            ),
            (
                "chart.bend-exceeds-peak", "warning",
                lambda count: f"{count} bend curve(s) exceed their declared peak bend amount.",
            ),
            (
                "chart.negative-bend", "error",
                lambda count: f"{count} bend amount(s) are negative and cannot represent an authored upward bend.",
            ),
            (
                "chart.missing-chord-template", "warning",
                lambda count: f"{count} chord event(s) reference a missing template; labels or fingering visuals can be absent.",
            ),
            (
                "chart.invisible-chord", "error",
                lambda count: f"{count} chord event(s) contain no notes and no usable template, so the highway cannot display them.",
            ),
            (
                "chart.chord-template-mismatch", "warning",
                lambda count: f"{count} chord member(s) disagree with their template fret; gems and chord guidance can show different shapes.",
            ),
            (
                "chart.invalid-template-fret", "error",
                lambda count: f"{count} chord-template fret value(s) are below the -1 unused-string sentinel.",
            ),
            (
                "chart.template-fret-beyond-highway", "warning",
                lambda count: f"{count} chord-template fret value(s) are above {HIGHWAY_MAX_FRET}, beyond the current 3D highway.",
            ),
            (
                "chart.invalid-template-finger", "error",
                lambda count: f"{count} chord-template finger value(s) are outside -1 through 4.",
            ),
            (
                "chart.template-array-mismatch", "warning",
                lambda count: f"{count} chord template(s) have different fret and finger array lengths.",
            ),
            (
                "chart.template-beyond-highway", "warning",
                lambda count: f"{count} chord template(s) define more than {HIGHWAY_MAX_STRINGS} strings; the current 3D highway cannot show the extras.",
            ),
            ("chart.negative-anchor-time", "error", lambda count: f"{count} anchor(s) occur before the song starts."),
            ("chart.invalid-anchor", "error", lambda count: f"{count} anchor(s) have a negative fret or non-positive width."),
            (
                "chart.anchor-beyond-highway", "warning",
                lambda count: f"{count} anchor window(s) extend beyond fret {HIGHWAY_MAX_FRET} and are clamped by the 3D highway.",
            ),
            ("chart.anchor-after-duration", "warning", lambda count: f"{count} anchor(s) occur after the manifest duration."),
            (
                "chart.conflicting-anchors", "warning",
                lambda count: f"{count} timestamp(s) contain different anchor windows, making the intended camera position ambiguous.",
            ),
            (
                "chart.invalid-handshape-span", "error",
                lambda count: f"{count} handshape(s) have a missing, negative, or reversed time span.",
            ),
            (
                "chart.zero-length-handshape", "warning",
                lambda count: f"{count} handshape(s) have zero duration, so their sustained shape cannot be displayed.",
            ),
            (
                "chart.missing-handshape-template", "error",
                lambda count: f"{count} handshape(s) reference a missing chord template and cannot produce the intended shape.",
            ),
            ("chart.handshape-after-duration", "warning", lambda count: f"{count} handshape(s) extend beyond the manifest duration."),
            (
                "chart.invalid-phrase-data", "error",
                lambda count: f"{count} phrase difficulty value(s) have a structure FeedBack cannot load safely.",
            ),
            (
                "chart.invalid-phrase-span", "error",
                lambda count: f"{count} phrase(s) have a missing, negative, or non-positive time span.",
            ),
            ("chart.phrase-after-duration", "warning", lambda count: f"{count} phrase(s) extend beyond the manifest duration."),
            (
                "chart.overlapping-phrases", "warning",
                lambda count: f"{count} phrase(s) overlap the preceding phrase and can duplicate mastery-filtered events.",
            ),
            (
                "chart.empty-phrase-levels", "warning",
                lambda count: f"{count} phrase(s) contain no difficulty levels and contribute no playable events.",
            ),
            (
                "chart.invalid-difficulty-level", "error",
                lambda count: f"{count} phrase level(s) have a missing, negative, or above-maximum difficulty value.",
            ),
            (
                "chart.duplicate-difficulty-level", "error",
                lambda count: f"{count} phrase level(s) repeat a difficulty value within the same phrase.",
            ),
            (
                "chart.phrase-levels-out-of-order", "error",
                lambda count: f"{count} phrase level(s) are not ordered by difficulty; FeedBack's mastery slider selects levels by array position.",
            ),
            (
                "chart.phrase-event-outside-window", "warning",
                lambda count: f"{count} difficulty-level event(s) fall outside their owning phrase window.",
            ),
            (
                "chart.empty-fretted-arrangement", "warning",
                lambda _count: "This guitar or bass arrangement contains no notes or chords at any difficulty level.",
            ),
        )
        for code, severity, message in rules:
            self._emit(
                code,
                severity,
                message,
                category=(
                    "feedback_compatibility"
                    if code in _HIGHWAY_COMPATIBILITY_RULES
                    else "validation"
                ),
            )
        review_rules = (
            (
                "review.impossible-chord-fingering",
                lambda count: f"{count} chord template(s) assign one finger to different positive frets at the same time; review the fingering.",
            ),
            (
                "review.extreme-chord-span",
                lambda count: f"{count} chord event(s) span at least {EXTREME_CHORD_SPAN_FRETS} frets, excluding open strings; review whether the shape is playable.",
            ),
        )
        for code, message in review_rules:
            self._emit(code, "info", message, category="authoring_review")

def _validate_lane_semantics(
    *,
    notes: list,
    chords: list,
    templates: list,
    location: str,
    duration: float | None,
    issues: dict[str, list[_LaneIssue]],
    check_lane_collisions: bool,
    check_fretted: bool,
) -> None:
    """Validate one playable stream without mixing alternative difficulties."""
    lanes: dict[tuple[int, int], list[_LaneEvent]] = {}
    negative: list[tuple[str, float]] = []
    after_duration: list[tuple[str, float]] = []

    def inspect_time(raw, event_location: str) -> float | None:
        if not isinstance(raw, dict):
            return None
        event_time = _number(raw.get("t"))
        if event_time is None:
            return None
        if event_time < 0:
            negative.append((event_location, event_time))
        if (
            duration is not None
            and duration > 0
            and event_time > duration + CHART_END_TOLERANCE_SECONDS
        ):
            after_duration.append((event_location, event_time))
        return event_time

    def add_lane_event(
        raw,
        kind: str,
        event_location: str,
        event_time: float | None,
        chord_index: int | None = None,
        *,
        explicit_chord_note: bool = False,
    ) -> None:
        if not isinstance(raw, dict) or event_time is None:
            return
        string = _integer(raw.get("s"))
        fret = _integer(raw.get("f"))
        if string is None or fret is None:
            return
        sustain = _number(raw.get("sus", 0))
        slide_to = _integer(raw.get("sl"))
        repair_value = None
        if kind == "note":
            repair_value = raw
        elif explicit_chord_note:
            repair_value = {"t": event_time, **raw}
        try:
            repair_identity = (
                json.dumps(
                    repair_value,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if repair_value is not None
                else None
            )
        except (TypeError, ValueError):
            repair_identity = None
        lanes.setdefault((_time_key(event_time), string), []).append(_LaneEvent(
            kind=kind,
            fret=fret,
            location=event_location,
            chord_index=chord_index,
            time=event_time,
            sustain=max(0.0, sustain or 0.0),
            link_next=raw.get("ln") is True,
            slide_to=slide_to if slide_to is not None and slide_to >= 0 else None,
            attributes=tuple(sorted(
                (
                    str(key),
                    json.dumps(value, sort_keys=True, ensure_ascii=False),
                )
                for key, value in raw.items()
                if key not in {"t", "s", "f"}
            )),
            explicit_chord_note=explicit_chord_note,
            repair_identity=repair_identity,
        ))

    for index, note in enumerate(notes):
        note_location = f"{location}.notes[{index}]"
        note_time = inspect_time(note, note_location)
        add_lane_event(note, "note", note_location, note_time)

    for chord_index, chord in enumerate(chords):
        chord_location = f"{location}.chords[{chord_index}]"
        chord_time = inspect_time(chord, chord_location)
        if not isinstance(chord, dict):
            continue
        chord_notes = chord.get("notes", []) if isinstance(chord.get("notes"), list) else []
        if chord_notes:
            for note_index, note in enumerate(chord_notes):
                add_lane_event(
                    note,
                    "chord",
                    f"{chord_location}.notes[{note_index}]",
                    chord_time,
                    chord_index,
                    explicit_chord_note=(
                        "t" not in note if isinstance(note, dict) else False
                    ),
                )
            continue

        template_id = _integer(chord.get("id", 0))
        template = (
            templates[template_id]
            if template_id is not None and 0 <= template_id < len(templates)
            else None
        )
        frets = template.get("frets") if isinstance(template, dict) else None
        if not isinstance(frets, list):
            continue
        for string, raw_fret in enumerate(frets):
            fret = _integer(raw_fret)
            if fret is None or fret < 0:
                continue
            add_lane_event(
                {"s": string, "f": fret},
                "chord",
                chord_location,
                chord_time,
                chord_index,
            )

    issues.setdefault("chart.negative-time", []).extend(
        _LaneIssue(event_location, event_time)
        for event_location, event_time in negative
    )
    issues.setdefault("chart.event-after-duration", []).extend(
        _LaneIssue(event_location, event_time)
        for event_location, event_time in after_duration
    )

    if not check_lane_collisions:
        return

    conflicts: list[_LaneIssue] = []
    duplicates: list[_LaneIssue] = []
    conflicting_duplicates: list[_LaneIssue] = []
    note_chord_duplicates: list[_LaneIssue] = []
    chord_duplicates: list[_LaneIssue] = []
    coincident_chords: list[_LaneIssue] = []
    coincident_times: set[int] = set()
    for (time_tick, string), events in sorted(lanes.items()):
        if len(events) < 2:
            continue
        event_time = time_tick / TIME_PRECISION
        frets = sorted({event.fret for event in events})
        if len(frets) > 1:
            conflicts.append(_LaneIssue(
                events[0].location,
                event_time,
                string,
                ", ".join(map(str, frets)),
            ))
            continue

        standalone = [event for event in events if event.kind == "note"]
        chord_notes = [event for event in events if event.kind == "chord"]
        if len(standalone) > 1:
            signatures = {event.attributes for event in standalone}
            if len(signatures) == 1:
                duplicates.append(_LaneIssue(
                    standalone[0].location,
                    event_time,
                    string,
                    str(len(standalone)),
                ))
            else:
                attribute_maps = [dict(event.attributes) for event in standalone]
                keys = sorted(set().union(*(mapping.keys() for mapping in attribute_maps)))
                differing = [
                    key for key in keys
                    if len({mapping.get(key, "<missing>") for mapping in attribute_maps}) > 1
                ]
                detail = ", ".join(differing[:8]) or "other note properties"
                conflicting_duplicates.append(_LaneIssue(
                    standalone[0].location,
                    event_time,
                    string,
                    detail,
                ))

        if standalone and chord_notes:
            chord_identities = [
                event.repair_identity
                for event in chord_notes
                if event.explicit_chord_note and event.repair_identity is not None
            ]
            matching_note = next(
                (
                    event for event in standalone
                    if event.repair_identity is not None
                    and chord_identities.count(event.repair_identity) == 1
                ),
                None,
            )
            if matching_note is not None:
                note_chord_duplicates.append(_LaneIssue(
                    matching_note.location,
                    event_time,
                    string,
                ))

        chord_groups: dict[int | None, list[_LaneEvent]] = {}
        for event in chord_notes:
            chord_groups.setdefault(event.chord_index, []).append(event)
        conflicting_chord_members = any(
            len(group) > 1
            and (
                group[0].repair_identity is None
                or any(
                    event.repair_identity != group[0].repair_identity
                    for event in group[1:]
                )
            )
            for group in chord_groups.values()
        )
        if conflicting_chord_members:
            chord_duplicates.append(_LaneIssue(chord_notes[0].location, event_time, string))
        elif len(chord_groups) > 1 and time_tick not in coincident_times:
            chord_event_identities = []
            for chord_index in chord_groups:
                identity = (
                    _exact_json_identity(chords[chord_index])
                    if isinstance(chord_index, int)
                    and 0 <= chord_index < len(chords)
                    else None
                )
                chord_event_identities.append(identity)
            exact_duplicate_chords = (
                chord_event_identities
                and chord_event_identities[0] is not None
                and len(set(chord_event_identities)) == 1
            )
            if not exact_duplicate_chords:
                coincident_times.add(time_tick)
                coincident_chords.append(_LaneIssue(
                    chord_notes[0].location,
                    event_time,
                    string,
                ))

    issues.setdefault("chart.string-conflict", []).extend(conflicts)
    issues.setdefault("chart.duplicate-note", []).extend(duplicates)
    issues.setdefault("chart.conflicting-duplicate-note", []).extend(
        conflicting_duplicates
    )
    issues.setdefault("chart.note-duplicates-chord", []).extend(
        note_chord_duplicates
    )
    issues.setdefault("chart.chord-string-duplicate", []).extend(chord_duplicates)
    issues.setdefault("chart.coincident-chords", []).extend(coincident_chords)

    if not check_fretted:
        return

    by_string: dict[int, list[_LaneEvent]] = {}
    for (time_tick, string), events in sorted(lanes.items()):
        frets = {event.fret for event in events}
        if len(frets) != 1:
            continue
        representative = _LaneEvent(
            kind=events[0].kind,
            fret=events[0].fret,
            location=events[0].location,
            chord_index=events[0].chord_index,
            time=time_tick / TIME_PRECISION,
            sustain=max(event.sustain for event in events),
            link_next=any(event.link_next for event in events),
            slide_to=next((event.slide_to for event in events if event.slide_to is not None), None),
            attributes=events[0].attributes,
        )
        by_string.setdefault(string, []).append(representative)

    near_onsets: list[_LaneIssue] = []
    sustain_overlaps: list[_LaneIssue] = []
    for string, events in sorted(by_string.items()):
        for previous, current in zip(events, events[1:]):
            gap = current.time - previous.time
            if 0 < gap < NEAR_SIMULTANEOUS_NOTE_SECONDS:
                near_onsets.append(_LaneIssue(previous.location, current.time, string))
            if (
                previous.sustain > 0
                and previous.time + previous.sustain
                > current.time + SUSTAIN_OVERLAP_TOLERANCE_SECONDS
                and not previous.link_next
                and previous.slide_to != current.fret
            ):
                sustain_overlaps.append(_LaneIssue(previous.location, current.time, string))

    issues.setdefault("review.near-simultaneous-string-notes", []).extend(near_onsets)
    issues.setdefault("review.same-string-sustain-overlap", []).extend(sustain_overlaps)


def _emit_lane_findings(
    issues: dict[str, list[_LaneIssue]],
    findings: _Findings,
    arrangement_id: str,
) -> None:
    rules = {
        "chart.negative-time": (
            "error",
            "validation",
            lambda count, first: (
                f"{count} chart event(s) occur before the song starts; "
                f"first at {first.time:.4f}s."
            ),
        ),
        "chart.event-after-duration": (
            "warning",
            "validation",
            lambda count, first: (
                f"{count} chart event(s) occur after the manifest duration; "
                f"first at {first.time:.4f}s."
            ),
        ),
        "chart.string-conflict": (
            "error",
            "validation",
            lambda count, first: (
                f"{count} timestamp(s) put different frets on one string; first is "
                f"string index {first.string} (0 = lowest), frets {first.detail} "
                f"at {first.time:.4f}s."
            ),
        ),
        "chart.duplicate-note": (
            "warning",
            "validation",
            lambda count, first: (
                f"{count} timestamp(s) contain duplicate standalone notes; first has "
                f"{first.detail} copies on string index {first.string} at {first.time:.4f}s."
            ),
        ),
        "chart.conflicting-duplicate-note": (
            "warning",
            "validation",
            lambda count, first: (
                f"{count} timestamp(s) contain notes on the same string and fret that "
                f"disagree about {first.detail}; first is string index {first.string} "
                f"at {first.time:.4f}s. These notes cannot be safely deduplicated "
                "without deciding which authored properties to keep."
            ),
        ),
        "chart.note-duplicates-chord": (
            "warning",
            "validation",
            lambda count, first: (
                f"{count} timestamp(s) contain a standalone note that exactly "
                f"duplicates a member of a chord; first is string index "
                f"{first.string} at {first.time:.4f}s."
            ),
        ),
        "chart.chord-string-duplicate": (
            "error",
            "validation",
            lambda count, first: (
                f"{count} chord event(s) contain more than one note on a string; first "
                f"is string index {first.string} at {first.time:.4f}s."
            ),
        ),
        "chart.coincident-chords": (
            "warning",
            "validation",
            lambda count, first: (
                f"{count} timestamp(s) contain multiple chord events; first is at "
                f"{first.time:.4f}s."
            ),
        ),
        "review.near-simultaneous-string-notes": (
            "info",
            "authoring_review",
            lambda count, first: (
                f"{count} same-string note pair(s) start less than "
                f"{NEAR_SIMULTANEOUS_NOTE_SECONDS * 1000:.0f} ms apart; first pair is "
                f"on string index {first.string} near {first.time:.4f}s. Review whether "
                "both can be played."
            ),
        ),
        "review.same-string-sustain-overlap": (
            "info",
            "authoring_review",
            lambda count, first: (
                f"{count} same-string sustain(s) overlap a following onset without "
                f"link-next or a matching pitched slide; first is on string index "
                f"{first.string} near {first.time:.4f}s. Review the sustain or transition."
            ),
        ),
    }
    for code, (severity, category, message) in rules.items():
        code_issues = issues.get(code)
        if not code_issues:
            continue
        # Difficulty levels are alternative render streams. The same physical
        # source event is commonly repeated in every level; count it once in
        # the user-facing total while still validating each level independently.
        if code in {
            "chart.string-conflict",
            "chart.duplicate-note",
            "chart.conflicting-duplicate-note",
            "chart.note-duplicates-chord",
            "chart.chord-string-duplicate",
            "chart.coincident-chords",
            "review.near-simultaneous-string-notes",
            "review.same-string-sustain-overlap",
        }:
            unique: dict[tuple[int, int | None, str], _LaneIssue] = {}
            for issue in code_issues:
                detail = issue.detail if code in {
                    "chart.string-conflict", "chart.conflicting-duplicate-note"
                } else ""
                unique.setdefault((_time_key(issue.time), issue.string, detail), issue)
            code_issues = list(unique.values())
        first = code_issues[0]
        findings.add(
            severity,
            code,
            message(len(code_issues), first),
            category=category,
            location=first.location,
            arrangement_id=arrangement_id,
            time=first.time,
            string=first.string,
            affected_count=len(code_issues),
        )


def _validate_arrangement_semantics(
    data,
    relpath: str,
    arrangement_id: str,
    duration: float | None,
    findings: _Findings,
    *,
    entry=None,
    check_lane_collisions: bool = True,
    check_fretted: bool = True,
) -> None:
    if not isinstance(data, dict):
        return

    _TabValidator(
        data=data,
        relpath=relpath,
        arrangement_id=arrangement_id,
        duration=duration,
        findings=findings,
        entry=entry,
        check_fretted=check_fretted,
    ).validate()
    if isinstance(data.get("tempos"), list):
        _validate_song_timeline_semantics(
            {"tempos": data["tempos"]},
            relpath,
            duration,
            findings,
            arrangement_id=arrangement_id,
        )

    templates = data.get("templates", []) if isinstance(data.get("templates"), list) else []
    lane_issues: dict[str, list[_LaneIssue]] = {}
    _validate_lane_semantics(
        notes=data.get("notes", []) if isinstance(data.get("notes"), list) else [],
        chords=data.get("chords", []) if isinstance(data.get("chords"), list) else [],
        templates=templates,
        location=relpath,
        duration=duration,
        issues=lane_issues,
        check_lane_collisions=check_lane_collisions,
        check_fretted=check_fretted,
    )

    phrases = data.get("phrases", []) if isinstance(data.get("phrases"), list) else []
    for phrase_index, phrase in enumerate(phrases):
        if not isinstance(phrase, dict):
            continue
        levels = phrase.get("levels", []) if isinstance(phrase.get("levels"), list) else []
        for level_index, level in enumerate(levels):
            if not isinstance(level, dict):
                continue
            level_location = f"{relpath}:phrases[{phrase_index}].levels[{level_index}]"
            _validate_lane_semantics(
                notes=level.get("notes", []) if isinstance(level.get("notes"), list) else [],
                chords=level.get("chords", []) if isinstance(level.get("chords"), list) else [],
                templates=templates,
                location=level_location,
                duration=duration,
                issues=lane_issues,
                check_lane_collisions=check_lane_collisions,
                check_fretted=check_fretted,
            )
    _emit_lane_findings(lane_issues, findings, arrangement_id)


def _validate_timed_sidecar_semantics(
    kind: str,
    data,
    relpath: str,
    duration: float | None,
    findings: _Findings,
    *,
    arrangement_id: str | None = None,
) -> None:
    """Check ordered sidecar event streams that FeedBack otherwise repairs."""
    rules = {
        "drum_tab": ("hits", "t", "drums", "drum hit", False, "k"),
        "vocal_pitch": ("notes", "t", "vocal-pitch", "vocal-pitch note", False, "d"),
        "vocal_pitch_contour": (
            "samples", "t", "vocal-pitch-contour", "pitch-contour sample", False, None,
        ),
        "keys": ("events", "t", "keys", "key event", True, None),
        "harmony": ("events", "t", "harmony", "harmony event", True, None),
    }
    rule = rules.get(kind)
    if not isinstance(data, dict) or rule is None:
        return
    field, time_key, prefix, label, allow_preroll, length_key = rule
    events = data.get(field)
    if not isinstance(events, list):
        return

    previous: float | None = None
    first_inversion: tuple[int, float] | None = None
    negative: list[tuple[int, float]] = []
    after_duration: list[tuple[int, float]] = []
    invalid_lengths: list[tuple[int, float]] = []
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            continue
        event_time = _number(raw.get(time_key))
        if event_time is None:
            continue
        if first_inversion is None and previous is not None and event_time < previous:
            first_inversion = (index, event_time)
        previous = event_time
        if event_time < 0 and not allow_preroll:
            negative.append((index, event_time))

        event_length = _number(raw.get(length_key)) if length_key else None
        if event_length is not None and event_length < 0:
            invalid_lengths.append((index, event_time))
        event_end = event_time + max(0.0, event_length or 0.0)
        if (
            duration is not None
            and duration > 0
            and event_end > duration + (
                TIMELINE_SIGNIFICANT_OVERRUN_SECONDS
                if allow_preroll else CHART_END_TOLERANCE_SECONDS
            )
        ):
            after_duration.append((index, event_time))

    if first_inversion is not None:
        index, event_time = first_inversion
        findings.add(
            "warning",
            f"{prefix}.events-out-of-order",
            f"The {label} timeline is not chronological; FeedBack may sort or skip entries while loading it.",
            location=f"{relpath}:{field}[{index}]",
            arrangement_id=arrangement_id,
            time=event_time,
        )
    if negative:
        index, event_time = negative[0]
        findings.add(
            "error",
            f"{prefix}.negative-time",
            f"{len(negative)} {label}(s) occur before the song starts.",
            location=f"{relpath}:{field}[{index}]",
            arrangement_id=arrangement_id,
            time=event_time,
            affected_count=len(negative),
        )
    if invalid_lengths:
        index, event_time = invalid_lengths[0]
        findings.add(
            "error",
            f"{prefix}.negative-duration",
            f"{len(invalid_lengths)} {label} duration value(s) are negative.",
            location=f"{relpath}:{field}[{index}].{length_key}",
            arrangement_id=arrangement_id,
            time=event_time,
            affected_count=len(invalid_lengths),
        )
    if after_duration:
        index, event_time = after_duration[0]
        findings.add(
            "warning",
            f"{prefix}.event-after-duration",
            f"{len(after_duration)} {label}(s) extend beyond the manifest duration.",
            location=f"{relpath}:{field}[{index}]",
            arrangement_id=arrangement_id,
            time=event_time,
            affected_count=len(after_duration),
        )

    if kind == "keys":
        empty_keys = [
            index for index, raw in enumerate(events)
            if isinstance(raw, dict)
            and isinstance(raw.get("key"), str)
            and not raw["key"].strip()
        ]
        if empty_keys:
            index = empty_keys[0]
            findings.add(
                "warning",
                "keys.empty-key",
                f"{len(empty_keys)} key event(s) have an empty key name and are dropped by FeedBack.",
                location=f"{relpath}:{field}[{index}].key",
                arrangement_id=arrangement_id,
                affected_count=len(empty_keys),
            )

    if kind == "drum_tab":
        kit = data.get("kit")
        seen: set[str] = set()
        duplicates: list[tuple[int, str]] = []
        if isinstance(kit, list):
            for index, raw in enumerate(kit):
                piece_id = raw.get("id") if isinstance(raw, dict) else None
                if not isinstance(piece_id, str):
                    continue
                if piece_id in seen:
                    duplicates.append((index, piece_id))
                seen.add(piece_id)
        if duplicates:
            index, piece_id = duplicates[0]
            findings.add(
                "warning",
                "drums.duplicate-kit-id",
                f"{len(duplicates)} drum-kit id(s) are duplicated; FeedBack keeps only the first '{piece_id}' entry.",
                location=f"{relpath}:kit[{index}].id",
                arrangement_id=arrangement_id,
                affected_count=len(duplicates),
            )

        hit_groups: dict[tuple[int, str], list[tuple[int, dict]]] = {}
        for index, raw in enumerate(events):
            if not isinstance(raw, dict):
                continue
            event_time = _number(raw.get("t"))
            piece = raw.get("p")
            if event_time is None or not isinstance(piece, str) or not piece:
                continue
            hit_groups.setdefault((_time_key(event_time), piece), []).append((index, raw))
        exact_duplicates = []
        conflicting_duplicates = []
        for (tick, piece), grouped in hit_groups.items():
            if len(grouped) < 2:
                continue
            first_properties = {
                key: value for key, value in grouped[0][1].items() if key != "t"
            }
            target = exact_duplicates if all(
                {key: value for key, value in raw.items() if key != "t"}
                == first_properties
                for _index, raw in grouped[1:]
            ) else conflicting_duplicates
            target.append((grouped[0][0], tick / TIME_PRECISION, piece))
        if exact_duplicates:
            index, event_time, piece = exact_duplicates[0]
            findings.add(
                "warning",
                "drums.duplicate-hit",
                f"{len(exact_duplicates)} timestamp(s) repeat the same '{piece}' drum hit.",
                location=f"{relpath}:hits[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
                affected_count=len(exact_duplicates),
            )
        if conflicting_duplicates:
            index, event_time, piece = conflicting_duplicates[0]
            findings.add(
                "warning",
                "drums.conflicting-hit",
                (
                    f"{len(conflicting_duplicates)} timestamp(s) put multiple '{piece}' "
                    "hits at the same time with different authored properties."
                ),
                location=f"{relpath}:hits[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
                affected_count=len(conflicting_duplicates),
            )


def _validate_notation_semantics(
    data,
    relpath: str,
    duration: float | None,
    findings: _Findings,
    *,
    arrangement_id: str | None = None,
) -> None:
    if not isinstance(data, dict):
        return
    staves = data.get("staves") if isinstance(data.get("staves"), list) else []
    staff_ids: set[str] = set()
    duplicate_staff = None
    for index, staff in enumerate(staves):
        staff_id = staff.get("id") if isinstance(staff, dict) else None
        if not isinstance(staff_id, str):
            continue
        if staff_id in staff_ids and duplicate_staff is None:
            duplicate_staff = (index, staff_id)
        staff_ids.add(staff_id)
    if duplicate_staff is not None:
        index, staff_id = duplicate_staff
        findings.add(
            "error",
            "notation.duplicate-staff-id",
            f"Notation staff id '{staff_id}' is declared more than once.",
            location=f"{relpath}:staves[{index}].id",
            arrangement_id=arrangement_id,
        )

    measures = data.get("measures") if isinstance(data.get("measures"), list) else []
    seen_indices: set[int] = set()
    duplicate_index = None
    index_inversion = None
    time_inversion = None
    previous_index: int | None = None
    previous_time: float | None = None
    unknown_staff = None
    beat_inversion = None
    beat_outside = None
    duplicate_voice = None
    for measure_pos, measure in enumerate(measures):
        if not isinstance(measure, dict):
            continue
        measure_index = _integer(measure.get("idx"))
        measure_time = _number(measure.get("t"))
        if measure_index is not None:
            if measure_index in seen_indices and duplicate_index is None:
                duplicate_index = (measure_pos, measure_index)
            if (
                previous_index is not None
                and measure_index < previous_index
                and index_inversion is None
            ):
                index_inversion = (measure_pos, measure_index)
            seen_indices.add(measure_index)
            previous_index = measure_index
        if measure_time is not None:
            if (
                previous_time is not None
                and measure_time < previous_time
                and time_inversion is None
            ):
                time_inversion = (measure_pos, measure_time)
            previous_time = measure_time

        measure_staves = measure.get("staves")
        if not isinstance(measure_staves, dict):
            continue
        for staff_id, staff_data in measure_staves.items():
            if staff_ids and staff_id not in staff_ids and unknown_staff is None:
                unknown_staff = (measure_pos, str(staff_id))
            voices = (
                staff_data.get("voices")
                if isinstance(staff_data, dict)
                and isinstance(staff_data.get("voices"), list)
                else []
            )
            voice_ids: set[int] = set()
            for voice_pos, voice in enumerate(voices):
                if not isinstance(voice, dict):
                    continue
                voice_id = _integer(voice.get("v"))
                if (
                    voice_id is not None
                    and voice_id in voice_ids
                    and duplicate_voice is None
                ):
                    duplicate_voice = (measure_pos, str(staff_id), voice_pos, voice_id)
                if voice_id is not None:
                    voice_ids.add(voice_id)
                beats = voice.get("beats") if isinstance(voice.get("beats"), list) else []
                previous_beat: float | None = None
                for beat_pos, beat in enumerate(beats):
                    beat_time = _number(beat.get("t")) if isinstance(beat, dict) else None
                    if beat_time is None:
                        continue
                    if (
                        previous_beat is not None
                        and beat_time < previous_beat
                        and beat_inversion is None
                    ):
                        beat_inversion = (
                            measure_pos, str(staff_id), voice_pos, beat_pos, beat_time
                        )
                    previous_beat = beat_time
                    if (
                        duration is not None
                        and duration > 0
                        and (beat_time < 0 or beat_time > duration + CHART_END_TOLERANCE_SECONDS)
                        and beat_outside is None
                    ):
                        beat_outside = (
                            measure_pos, str(staff_id), voice_pos, beat_pos, beat_time
                        )

    simple_rules = (
        (duplicate_index, "error", "notation.duplicate-measure-index", "A measure index is declared more than once."),
        (index_inversion, "warning", "notation.measure-indices-out-of-order", "Notation measure indices are not chronological."),
        (time_inversion, "warning", "notation.measures-out-of-order", "Notation measure start times are not chronological."),
    )
    for issue, severity, code, message in simple_rules:
        if issue is not None:
            findings.add(
                severity, code, message,
                location=f"{relpath}:measures[{issue[0]}]",
                arrangement_id=arrangement_id,
            )
    if unknown_staff is not None:
        measure_pos, staff_id = unknown_staff
        findings.add(
            "error",
            "notation.unknown-staff-reference",
            f"A measure contains staff '{staff_id}', which is not declared in staves[].",
            location=f"{relpath}:measures[{measure_pos}].staves.{staff_id}",
            arrangement_id=arrangement_id,
        )
    if duplicate_voice is not None:
        measure_pos, staff_id, voice_pos, voice_id = duplicate_voice
        findings.add(
            "warning",
            "notation.duplicate-voice-id",
            f"Voice id {voice_id} is repeated within one notation staff and measure.",
            location=(
                f"{relpath}:measures[{measure_pos}].staves.{staff_id}"
                f".voices[{voice_pos}].v"
            ),
            arrangement_id=arrangement_id,
        )
    if beat_inversion is not None:
        measure_pos, staff_id, voice_pos, beat_pos, beat_time = beat_inversion
        findings.add(
            "warning",
            "notation.beats-out-of-order",
            "Beat times within a notation voice are not chronological.",
            location=(
                f"{relpath}:measures[{measure_pos}].staves.{staff_id}"
                f".voices[{voice_pos}].beats[{beat_pos}]"
            ),
            arrangement_id=arrangement_id,
            time=beat_time,
        )
    if beat_outside is not None:
        measure_pos, staff_id, voice_pos, beat_pos, beat_time = beat_outside
        findings.add(
            "warning",
            "notation.beat-outside-song",
            "A notation beat occurs before the song starts or after its declared duration.",
            location=(
                f"{relpath}:measures[{measure_pos}].staves.{staff_id}"
                f".voices[{voice_pos}].beats[{beat_pos}]"
            ),
            arrangement_id=arrangement_id,
            time=beat_time,
        )


def _member_sha256(
    reader: _PackageReader,
    relpath: str,
    *,
    scan_checkpoint=None,
) -> str | None:
    size = reader.size(relpath)
    if size is None or size > MAX_REALIZATION_HASH_BYTES:
        return None
    digest = hashlib.sha256()
    with reader.open_binary(relpath) as stream:
        while True:
            if scan_checkpoint is not None:
                scan_checkpoint()
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _validate_tones(
    tones,
    *,
    location: str,
    arrangement_id: str,
    rig_ids: set[str],
    duration: float | None,
    findings: _Findings,
) -> None:
    if not isinstance(tones, dict):
        return
    base_rig = tones.get("base_rig")
    if isinstance(base_rig, str) and base_rig and base_rig not in rig_ids:
        findings.add(
            "error",
            "tones.missing-rig",
            f"Base tone references rig '{base_rig}', which is not declared in rigs.json.",
            location=f"{location}.base_rig",
            arrangement_id=arrangement_id,
        )

    changes = tones.get("changes") if isinstance(tones.get("changes"), list) else []
    previous: float | None = None
    inversion = None
    negative = None
    after = None
    missing = None
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            continue
        event_time = _number(change.get("t"))
        if event_time is None:
            event_time = _number(change.get("time"))
        if event_time is not None:
            if previous is not None and event_time < previous and inversion is None:
                inversion = (index, event_time)
            previous = event_time
            if event_time < 0 and negative is None:
                negative = (index, event_time)
            if (
                duration is not None
                and duration > 0
                and event_time > duration + CHART_END_TOLERANCE_SECONDS
                and after is None
            ):
                after = (index, event_time)
        rig = change.get("rig")
        if isinstance(rig, str) and rig and rig not in rig_ids and missing is None:
            missing = (index, rig, event_time)

    for issue, severity, code, message in (
        (inversion, "warning", "tones.changes-out-of-order", "Tone changes are not chronological."),
        (negative, "error", "tones.negative-time", "A tone change occurs before the song starts."),
        (after, "warning", "tones.change-after-duration", "A tone change occurs after the manifest duration."),
    ):
        if issue is not None:
            findings.add(
                severity,
                code,
                message,
                location=f"{location}.changes[{issue[0]}]",
                arrangement_id=arrangement_id,
                time=issue[1],
            )
    if missing is not None:
        index, rig, event_time = missing
        findings.add(
            "error",
            "tones.missing-rig",
            f"A tone change references rig '{rig}', which is not declared in rigs.json.",
            location=f"{location}.changes[{index}].rig",
            arrangement_id=arrangement_id,
            time=event_time,
        )


def _validate_rigs_semantics(
    data,
    relpath: str,
    duration: float | None,
    reader: _PackageReader,
    findings: _Findings,
    tone_bindings: list[tuple[str, str, dict]],
    *,
    scan_checkpoint=None,
) -> None:
    rigs = data.get("rigs") if isinstance(data, dict) and isinstance(data.get("rigs"), list) else []
    rig_ids: set[str] = set()
    duplicate_rig = None
    for rig_index, rig in enumerate(rigs):
        rig_id = rig.get("id") if isinstance(rig, dict) else None
        if not isinstance(rig_id, str):
            continue
        if rig_id in rig_ids and duplicate_rig is None:
            duplicate_rig = (rig_index, rig_id)
        rig_ids.add(rig_id)
    if duplicate_rig is not None:
        index, rig_id = duplicate_rig
        findings.add(
            "error",
            "rigs.duplicate-rig-id",
            f"Rig id '{rig_id}' is declared more than once.",
            location=f"{relpath}:rigs[{index}].id",
        )

    for rig_index, rig in enumerate(rigs):
        if not isinstance(rig, dict):
            continue
        blocks = rig.get("blocks") if isinstance(rig.get("blocks"), list) else []
        block_ids: set[str] = set()
        duplicate_block = None
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            block_id = block.get("id")
            if isinstance(block_id, str) and block_id:
                if block_id in block_ids and duplicate_block is None:
                    duplicate_block = (block_index, block_id)
                block_ids.add(block_id)

            realizations = (
                block.get("realizations")
                if isinstance(block.get("realizations"), list)
                else []
            )
            for realization_index, realization in enumerate(realizations):
                if not isinstance(realization, dict):
                    continue
                reference = realization.get("ref")
                if not isinstance(reference, str) or not reference:
                    continue
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", reference):
                    continue
                location = (
                    f"{relpath}:rigs[{rig_index}].blocks[{block_index}]"
                    f".realizations[{realization_index}]"
                )
                if not _safe_relpath(reference) or not reader.exists(reference):
                    findings.add(
                        "error",
                        "rigs.missing-realization-file",
                        f"A rig realization references a missing package file: {reference}",
                        location=f"{location}.ref",
                    )
                    continue
                expected_hash = realization.get("sha256")
                if isinstance(expected_hash, str) and re.fullmatch(
                    r"[0-9a-fA-F]{64}", expected_hash
                ):
                    actual_hash = _member_sha256(
                        reader, reference, scan_checkpoint=scan_checkpoint
                    )
                    if actual_hash is None:
                        findings.add(
                            "warning",
                            "rigs.realization-hash-not-checked",
                            (
                                "A rig realization is too large for its declared SHA-256 "
                                "to be checked within the normal safety limit."
                            ),
                            location=f"{location}.sha256",
                        )
                    elif actual_hash.casefold() != expected_hash.casefold():
                        findings.add(
                            "error",
                            "rigs.realization-hash-mismatch",
                            "A rig realization does not match its declared SHA-256 hash.",
                            location=f"{location}.sha256",
                        )

            automation = block.get("automation") if isinstance(block.get("automation"), list) else []
            for lane_index, lane in enumerate(automation):
                points = (
                    lane.get("points")
                    if isinstance(lane, dict) and isinstance(lane.get("points"), list)
                    else []
                )
                previous: float | None = None
                inversion = negative = after = None
                for point_index, point in enumerate(points):
                    event_time = _number(point.get("t")) if isinstance(point, dict) else None
                    if event_time is None:
                        continue
                    if previous is not None and event_time < previous and inversion is None:
                        inversion = (point_index, event_time)
                    previous = event_time
                    if event_time < 0 and negative is None:
                        negative = (point_index, event_time)
                    if (
                        duration is not None
                        and duration > 0
                        and event_time > duration + CHART_END_TOLERANCE_SECONDS
                        and after is None
                    ):
                        after = (point_index, event_time)
                for issue, severity, code, message in (
                    (inversion, "warning", "rigs.automation-out-of-order", "Rig automation points are not chronological."),
                    (negative, "error", "rigs.automation-negative-time", "A rig automation point occurs before the song starts."),
                    (after, "warning", "rigs.automation-after-duration", "A rig automation point occurs after the manifest duration."),
                ):
                    if issue is not None:
                        findings.add(
                            severity,
                            code,
                            message,
                            location=(
                                f"{relpath}:rigs[{rig_index}].blocks[{block_index}]"
                                f".automation[{lane_index}].points[{issue[0]}]"
                            ),
                            time=issue[1],
                        )

        if duplicate_block is not None:
            block_index, block_id = duplicate_block
            findings.add(
                "error",
                "rigs.duplicate-block-id",
                f"Block id '{block_id}' is repeated within one rig.",
                location=f"{relpath}:rigs[{rig_index}].blocks[{block_index}].id",
            )

        graph = rig.get("graph")
        if isinstance(graph, dict):
            nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
            node_values = [node for node in nodes if isinstance(node, str)]
            duplicate_nodes = len(node_values) - len(set(node_values))
            valid_nodes = block_ids | {"input", "output"}
            unknown_nodes = [node for node in node_values if node not in valid_nodes]
            invalid_edges = []
            for edge_index, edge in enumerate(
                graph.get("edges") if isinstance(graph.get("edges"), list) else []
            ):
                if (
                    isinstance(edge, list)
                    and len(edge) == 2
                    and any(node not in valid_nodes for node in edge)
                ):
                    invalid_edges.append(edge_index)
            if duplicate_nodes:
                findings.add(
                    "warning",
                    "rigs.duplicate-graph-node",
                    f"The rig graph repeats {duplicate_nodes} node id(s).",
                    location=f"{relpath}:rigs[{rig_index}].graph.nodes",
                )
            if unknown_nodes or invalid_edges:
                findings.add(
                    "error",
                    "rigs.invalid-graph-reference",
                    (
                        "The rig graph references a block that is not declared in this "
                        "rig (only block ids, 'input', and 'output' are valid nodes)."
                    ),
                    location=f"{relpath}:rigs[{rig_index}].graph",
                )

    for arrangement_id, location, tones in tone_bindings:
        _validate_tones(
            tones,
            location=location,
            arrangement_id=arrangement_id,
            rig_ids=rig_ids,
            duration=duration,
            findings=findings,
        )


def _validate_song_timeline_semantics(
    data,
    relpath: str,
    duration: float | None,
    findings: _Findings,
    *,
    arrangement_id: str | None = None,
) -> None:
    """Validate authored beat/section order without rewriting the timeline.

    FeedBack consumes both arrays in authored order. The highway uses ordered
    searches for beats and a forward-only scan for sections, so an inversion
    can select the wrong tempo, measure, or section. Negative entries are not
    rejected here because pre-roll grids can be intentional.
    """
    if not isinstance(data, dict):
        return

    rules = (
        (
            "beats",
            "error",
            "timeline.beats-out-of-order",
            "timeline.beat-after-duration",
            "Beat markers move backward in time. Repeated or conflicting timestamps may be the cause; review the grid before reordering it.",
            "beat marker(s) occur significantly after the manifest duration.",
        ),
        (
            "sections",
            "error",
            "timeline.sections-out-of-order",
            "timeline.section-after-duration",
            "Section markers move backward in time. Repeated or conflicting timestamps may be the cause; review the structure before reordering it.",
            "section marker(s) occur significantly after the manifest duration.",
        ),
        (
            "tempos",
            "warning",
            "timeline.tempos-out-of-order",
            "timeline.tempo-after-duration",
            "Tempo changes are not chronological; FeedBack sorts them while loading and may not preserve the authored order.",
            "tempo change(s) occur significantly after the manifest duration.",
        ),
        (
            "time_signatures",
            "warning",
            "timeline.time-signatures-out-of-order",
            "timeline.time-signature-after-duration",
            "Time-signature changes are not chronological; FeedBack sorts them while loading and may not preserve the authored order.",
            "time-signature change(s) occur significantly after the manifest duration.",
        ),
    )
    for field, order_severity, order_code, duration_code, order_message, duration_message in rules:
        items = data.get(field)
        if not isinstance(items, list):
            continue

        previous: float | None = None
        first_inversion: tuple[int, float] | None = None
        after_duration: list[tuple[int, float]] = []
        exact_entries: dict[str, int] = {}
        entries_by_time: dict[int, dict[str, int]] = {}
        previous_tick: int | None = None
        exact_duplicates: list[tuple[int, int, float]] = []
        repeated_time_conflicts: list[tuple[int, int, float]] = []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            event_time = _number(raw.get("time"))
            if event_time is None:
                continue
            tick = _time_key(event_time)
            identity = _exact_json_identity(raw)
            first_exact = exact_entries.get(identity) if identity is not None else None
            earlier_at_time = entries_by_time.get(tick)
            if field in {"beats", "sections"} and identity is not None:
                if first_exact is not None:
                    exact_duplicates.append((index, first_exact, event_time))
                elif earlier_at_time and previous_tick != tick:
                    first_conflict = next(iter(earlier_at_time.values()))
                    repeated_time_conflicts.append(
                        (index, first_conflict, event_time)
                    )
            if identity is not None:
                exact_entries.setdefault(identity, index)
                entries_by_time.setdefault(tick, {}).setdefault(identity, index)
            if (
                first_inversion is None
                and previous is not None
                and event_time < previous
            ):
                first_inversion = (index, event_time)
            previous = event_time
            previous_tick = tick
            if (
                duration is not None
                and duration > 0
                and event_time > duration + TIMELINE_SIGNIFICANT_OVERRUN_SECONDS
            ):
                after_duration.append((index, event_time))

        if first_inversion is not None:
            index, event_time = first_inversion
            findings.add(
                order_severity,
                order_code,
                order_message,
                location=f"{relpath}:{field}[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
            )
        if exact_duplicates:
            index, original_index, event_time = exact_duplicates[0]
            label = "beat" if field == "beats" else "section"
            marker_label = "marker" if len(exact_duplicates) == 1 else "markers"
            duplicate_verb = "duplicates" if len(exact_duplicates) == 1 else "duplicate"
            findings.add(
                "warning",
                f"timeline.duplicate-{label}",
                (
                    f"{len(exact_duplicates)} {label} {marker_label} exactly {duplicate_verb} "
                    f"an earlier entry; first duplicate repeats {field}[{original_index}]."
                ),
                location=f"{relpath}:{field}[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
                affected_count=len(exact_duplicates),
            )
        if repeated_time_conflicts:
            index, original_index, event_time = repeated_time_conflicts[0]
            label = "beat" if field == "beats" else "section"
            marker_label = (
                "marker" if len(repeated_time_conflicts) == 1 else "markers"
            )
            repeat_verb = "repeats" if len(repeated_time_conflicts) == 1 else "repeat"
            findings.add(
                "error",
                f"timeline.repeated-{label}-time",
                (
                    f"{len(repeated_time_conflicts)} {label} {marker_label} {repeat_verb} an "
                    f"earlier timestamp with different data after the timeline has "
                    f"advanced; first conflicts with {field}[{original_index}]."
                ),
                location=f"{relpath}:{field}[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
                affected_count=len(repeated_time_conflicts),
            )
        if after_duration:
            index, event_time = after_duration[0]
            findings.add(
                "warning",
                duration_code,
                f"{len(after_duration)} {duration_message}",
                location=f"{relpath}:{field}[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
                affected_count=len(after_duration),
            )

    conflict_rules = (
        (
            "tempos", "bpm", "timeline.conflicting-tempos",
            "Different tempo values are declared at the same time; the effective tempo is ambiguous.",
        ),
        (
            "time_signatures", "ts", "timeline.conflicting-time-signatures",
            "Different time signatures are declared at the same time; the effective meter is ambiguous.",
        ),
    )
    for field, value_key, code, message in conflict_rules:
        items = data.get(field)
        if not isinstance(items, list):
            continue
        groups: dict[int, set[str]] = {}
        locations: dict[int, tuple[int, float]] = {}
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            event_time = _number(raw.get("time"))
            value = raw.get(value_key)
            if event_time is None or value is None:
                continue
            tick = _time_key(event_time)
            def semantic_value(item):
                number = _number(item)
                if number is not None:
                    return ("number", number)
                if isinstance(item, list):
                    return ("list", tuple(semantic_value(child) for child in item))
                if isinstance(item, dict):
                    return (
                        "object",
                        tuple(sorted((str(key), semantic_value(child)) for key, child in item.items())),
                    )
                return (type(item).__name__, str(item))

            groups.setdefault(tick, set()).add(semantic_value(value))
            locations.setdefault(tick, (index, event_time))
        conflicts = [tick for tick, values in groups.items() if len(values) > 1]
        if conflicts:
            first_tick = conflicts[0]
            index, event_time = locations[first_tick]
            findings.add(
                "warning",
                code,
                f"{len(conflicts)} timestamp(s) conflict. {message}",
                location=f"{relpath}:{field}[{index}]",
                arrangement_id=arrangement_id,
                time=event_time,
                affected_count=len(conflicts),
            )


def _validate_lyrics_semantics(
    data,
    relpath: str,
    duration: float | None,
    findings: _Findings,
) -> int:
    if not isinstance(data, list):
        return 0
    if not data:
        findings.add(
            "warning",
            "lyrics.empty",
            "The manifest declares a lyrics file, but it contains no syllables.",
            location=relpath,
        )
        return 0

    valid: list[tuple[int, float, float]] = []
    invalid_timing = []
    after_duration = []
    empty_text = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        start = _number(entry.get("t"))
        length = _number(entry.get("d"))
        if start is None or length is None:
            continue
        valid.append((index, start, length))
        word = entry.get("w")
        if isinstance(word, str) and not word.rstrip("+").strip():
            empty_text.append(index)
        if start < 0 or length < 0:
            invalid_timing.append((index, start, length))
        if duration is not None and duration > 0 and start + max(length, 0) > duration + 0.05:
            after_duration.append((index, start))

    if invalid_timing:
        index, start, length = invalid_timing[0]
        findings.add(
            "error",
            "lyrics.negative-time",
            f"{len(invalid_timing)} lyric syllable(s) have a negative start or duration; first has t={start:g}, d={length:g}.",
            location=f"{relpath}:[{index}]",
            time=start,
            affected_count=len(invalid_timing),
        )
    inversions = [
        current
        for previous, current in zip(valid, valid[1:])
        if current[1] < previous[1]
    ]
    if inversions:
        index, start, _length = inversions[0]
        transition_label = "transition" if len(inversions) == 1 else "transitions"
        move_verb = "moves" if len(inversions) == 1 else "move"
        findings.add(
            "warning",
            "lyrics.out-of-order",
            (
                f"{len(inversions)} lyric cue {transition_label} {move_verb} backward in "
                f"time; first out-of-order cue starts at {start:.4f}s."
            ),
            location=f"{relpath}:[{index}]",
            time=start,
            affected_count=len(inversions),
        )
    if after_duration:
        index, start = after_duration[0]
        findings.add(
            "warning",
            "lyrics.after-duration",
            f"{len(after_duration)} lyric syllable(s) extend beyond the manifest duration; first starts at {start:.4f}s.",
            location=f"{relpath}:[{index}]",
            time=start,
            affected_count=len(after_duration),
        )
    if empty_text:
        index = empty_text[0]
        findings.add(
            "warning",
            "lyrics.empty-text",
            f"{len(empty_text)} timed lyric syllable(s) contain no visible text.",
            location=f"{relpath}:[{index}].w",
            affected_count=len(empty_text),
        )

    authored_breaks = 0
    longest_line = 0
    current_line = 0
    current_characters = 0
    current_line_start: float | None = None
    longest_characters = 0
    longest_duration = 0.0
    timed_syllables = 0
    previous_end: float | None = None
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("w"), str):
            continue
        start = _number(entry.get("t"))
        length = _number(entry.get("d"))
        if start is None or length is None or start < 0 or length < 0:
            continue

        if (
            current_line
            and previous_end is not None
            and start - previous_end > LYRIC_FALLBACK_BREAK_GAP_SECONDS
        ):
            longest_line = max(longest_line, current_line)
            longest_characters = max(longest_characters, current_characters)
            if current_line_start is not None and previous_end is not None:
                longest_duration = max(longest_duration, previous_end - current_line_start)
            current_line = 0
            current_characters = 0
            current_line_start = None

        timed_syllables += 1
        if current_line_start is None:
            current_line_start = start
        current_line += 1
        current_characters += len(entry["w"].rstrip("+").strip())
        if entry["w"].endswith("+"):
            authored_breaks += 1
            longest_line = max(longest_line, current_line)
            longest_characters = max(longest_characters, current_characters)
            if current_line_start is not None:
                longest_duration = max(longest_duration, start + length - current_line_start)
            current_line = 0
            current_characters = 0
            current_line_start = None
        previous_end = start + length

    longest_line = max(longest_line, current_line)
    longest_characters = max(longest_characters, current_characters)
    if current_line_start is not None and previous_end is not None:
        longest_duration = max(longest_duration, previous_end - current_line_start)
    if (
        longest_line > MAX_LYRIC_SYLLABLES_PER_LINE
        or longest_characters > MAX_LYRIC_CHARACTERS_PER_LINE
        or longest_duration > MAX_LYRIC_LINE_SECONDS
    ):
        findings.add(
            "warning",
            "lyrics.too-few-line-breaks",
            (
                f"One lyric line reaches {longest_line} timed syllables, "
                f"{longest_characters} visible characters, or {longest_duration:.1f}s; "
                "this is unusually large. The track has "
                f"{authored_breaks} authored '+' line break marker(s) across "
                f"{timed_syllables} timed syllables. Add '+' to the final syllable of "
                "each intended lyric line."
            ),
            location=relpath,
        )
    return len(valid)


def _same_content(
    reader: _PackageReader,
    left: str,
    right: str,
    *,
    scan_checkpoint=None,
) -> bool:
    left_size = reader.size(left)
    right_size = reader.size(right)
    if left_size is None or right_size is None or left_size != right_size or left_size <= 0:
        return False
    if left == right:
        return True
    if left_size > MAX_MEDIA_INSPECTION_BYTES:
        return False

    def digest(relpath: str) -> bytes:
        hasher = hashlib.sha256()
        with reader.open_binary(relpath) as stream:
            while True:
                if scan_checkpoint is not None:
                    scan_checkpoint()
                block = stream.read(1024 * 1024)
                if not block:
                    break
                hasher.update(block)
        return hasher.digest()

    try:
        return digest(left) == digest(right)
    except _PackageBudgetError:
        raise
    except _PackageReadError:
        return False


def _inspect_cover_image(reader: _PackageReader, relpath: str) -> _ImageFacts | None:
    """Recognize common browser-safe image headers without decoding pixels."""
    try:
        with reader.open_binary(relpath) as stream:
            data = stream.read(MAX_IMAGE_HEADER_BYTES)
    except _PackageBudgetError:
        raise
    except _PackageReadError:
        return None

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        if data[12:16] != b"IHDR":
            return None
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return _ImageFacts("png", width, height) if width and height else None

    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return _ImageFacts("gif", width, height) if width and height else None

    if data.startswith(b"RIFF") and len(data) >= 16 and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return _ImageFacts("webp", width, height)
        # VP8/VP8L dimensions require bit-level parsing, but the RIFF/WEBP
        # signature is still sufficient to reject a renamed text/audio file.
        return _ImageFacts("webp", None, None)

    if data.startswith(b"\xff\xd8"):
        offset = 2
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                return None
            if marker in sof_markers and segment_length >= 7:
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                return _ImageFacts("jpeg", width, height) if width and height else None
            offset += segment_length
        return None

    return None


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        block = stream.read(size - len(data))
        if not block:
            raise ValueError("unexpected end of Ogg stream")
        data.extend(block)
    return bytes(data)


def _inspect_ogg(
    reader: _PackageReader,
    relpath: str,
    *,
    max_bytes: int = MAX_MEDIA_INSPECTION_BYTES,
    scan_checkpoint=None,
) -> _OggFacts | None:
    """Read bounded Ogg container facts without decoding audio samples."""
    size = reader.size(relpath)
    if (
        not relpath.lower().endswith(".ogg")
        or size is None
        or size <= 0
        or size > max_bytes
    ):
        return None

    payload_hasher = hashlib.sha256()
    prefix = bytearray()
    primary_serial: int | None = None
    last_granule: int | None = None
    saw_page = False
    try:
        with reader.open_binary(relpath) as stream:
            while True:
                if scan_checkpoint is not None:
                    scan_checkpoint()
                header = stream.read(27)
                if not header:
                    break
                if len(header) != 27 or header[:4] != b"OggS" or header[4] != 0:
                    return None
                lacing = _read_exact(stream, header[26])
                body = _read_exact(stream, sum(lacing))
                payload_hasher.update(body)
                if len(prefix) < 64:
                    prefix.extend(body[: 64 - len(prefix)])

                serial = int.from_bytes(header[14:18], "little")
                granule = int.from_bytes(header[6:14], "little")
                if primary_serial is None:
                    primary_serial = serial
                if serial == primary_serial and granule != (1 << 64) - 1:
                    last_granule = granule
                saw_page = True
    except _PackageBudgetError:
        raise
    except (_PackageReadError, OSError, ValueError):
        return None

    if not saw_page:
        return None

    duration: float | None = None
    codec: str | None = None
    ident = bytes(prefix)
    if last_granule is not None and ident.startswith(b"\x01vorbis") and len(ident) >= 16:
        codec = "vorbis"
        sample_rate = int.from_bytes(ident[12:16], "little")
        if sample_rate > 0:
            duration = last_granule / sample_rate
    elif last_granule is not None and ident.startswith(b"OpusHead") and len(ident) >= 12:
        codec = "opus"
        pre_skip = int.from_bytes(ident[10:12], "little")
        duration = max(0, last_granule - pre_skip) / 48_000

    return _OggFacts(
        payload_digest=payload_hasher.digest(),
        duration_seconds=duration,
        codec=codec,
    )


def _inspect_suspicious_preview_pair(
    reader: _PackageReader,
    preview_rel: str,
    full_mix_rel: str,
    facts_cache: dict[str, _OggFacts | None] | None = None,
    *,
    scan_checkpoint=None,
    require_size_ratio: bool = True,
) -> tuple[_OggFacts, _OggFacts] | None:
    preview_size = reader.size(preview_rel)
    full_size = reader.size(full_mix_rel)
    if (
        preview_size is None
        or full_size is None
        or preview_size <= 0
        or full_size <= 0
        or (
            require_size_ratio
            and preview_size / full_size < PREVIEW_INSPECTION_SIZE_RATIO
        )
    ):
        return None
    def facts(relpath: str) -> _OggFacts | None:
        if facts_cache is not None and relpath in facts_cache:
            return facts_cache[relpath]
        value = _inspect_ogg(
            reader,
            relpath,
            scan_checkpoint=scan_checkpoint,
        )
        if facts_cache is not None:
            facts_cache[relpath] = value
        return value

    preview_facts = facts(preview_rel)
    full_facts = facts(full_mix_rel)
    if preview_facts is None or full_facts is None:
        return None
    return preview_facts, full_facts


def _result(
    package_name: str,
    title: str,
    artist: str,
    features: dict,
    findings: _Findings,
) -> dict:
    findings.finish()
    status = (
        "error" if findings.counts["error"]
        else "warning" if findings.counts["warning"]
        else "review" if findings.counts["info"]
        else "healthy"
    )
    return {
        "schema": "library_doctor.package.v1",
        "validator_version": VALIDATOR_VERSION,
        "spec_revision": SPEC_REVISION,
        "package": package_name,
        "title": title,
        "artist": artist,
        "status": status,
        "counts": dict(findings.counts),
        "features": features,
        "findings": [finding.to_dict() for finding in findings.items],
    }


def validate_feedpak(
    package: Path,
    package_name: str | None = None,
    *,
    deep_audio: bool = False,
    scan_checkpoint=None,
) -> dict:
    """Validate one Feedpak/Sloppak directory or ZIP without modifying it."""
    package = Path(package)
    display_name = package_name or package.name
    findings = _Findings()
    title = ""
    artist = ""
    features = {
        "lyrics_declared": False,
        "lyrics_entries": 0,
        "preview_declared": False,
        "preview_available": False,
        "deep_audio_checked": bool(deep_audio),
        "deep_audio_files": 0,
        "deep_audio_skipped": 0,
        "deep_audio_unsupported": 0,
    }
    loaded_json: dict[tuple[str, str], object | None] = {}
    validated_sidecars: set[tuple[str, str]] = set()
    ogg_facts: dict[str, _OggFacts | None] = {}
    tone_bindings: list[tuple[str, str, dict]] = []

    try:
        with _PackageReader(package, findings) as reader:
            if scan_checkpoint is not None:
                scan_checkpoint()
            manifest_rel = "manifest.yaml"
            if not reader.exists(manifest_rel):
                findings.add(
                    "error",
                    "package.missing-manifest",
                    "No manifest.yaml exists at the package root.",
                    location=manifest_rel,
                )
                return _result(display_name, title, artist, features, findings)

            try:
                manifest = yaml.load(
                    reader.read_text(manifest_rel), Loader=_BoundedSafeLoader
                )
                reader.inspect_structure(manifest, manifest_rel)
            except _PackageBudgetError as exc:
                findings.add(
                    "error",
                    "package.validation-budget-exceeded",
                    str(exc),
                    location=manifest_rel,
                )
                return _result(display_name, title, artist, features, findings)
            except (yaml.YAMLError, RecursionError, _PackageReadError) as exc:
                findings.add(
                    "error",
                    "package.invalid-manifest",
                    f"manifest.yaml could not be read as YAML ({exc}).",
                    location=manifest_rel,
                )
                return _result(display_name, title, artist, features, findings)
            if not isinstance(manifest, dict):
                findings.add(
                    "error",
                    "package.invalid-manifest",
                    "manifest.yaml must contain a mapping at the top level.",
                    location=manifest_rel,
                )
                return _result(display_name, title, artist, features, findings)

            title = manifest.get("title") if isinstance(manifest.get("title"), str) else ""
            artist = manifest.get("artist") if isinstance(manifest.get("artist"), str) else ""
            duration = _number(manifest.get("duration"))
            _validate_schema(manifest, "manifest.schema.json", manifest_rel, findings)
            _report_nonfinite(manifest, manifest_rel, findings)

            if isinstance(manifest.get("title"), str) and not title.strip():
                findings.add(
                    "warning", "manifest.empty-title",
                    "The song title is empty, so it cannot be identified reliably in the library.",
                    location="manifest.yaml:title",
                )
            if isinstance(manifest.get("artist"), str) and not artist.strip():
                findings.add(
                    "warning", "manifest.empty-artist",
                    "The artist is empty, so the song cannot be identified reliably in the library.",
                    location="manifest.yaml:artist",
                )
            if duration == 0:
                findings.add(
                    "warning", "manifest.zero-duration",
                    "The manifest duration is zero; timeline bounds and playback progress cannot be represented reliably.",
                    location="manifest.yaml:duration",
                )

            feedpak_version = manifest.get("feedpak_version", "1.0.0")
            if isinstance(feedpak_version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}.*", feedpak_version):
                if int(feedpak_version.split(".", 1)[0]) > SUPPORTED_MAJOR:
                    findings.add(
                        "warning",
                        "spec.newer-major-version",
                        f"This package declares Feedpak {feedpak_version}; this validator supports major version {SUPPORTED_MAJOR}.",
                        location="manifest.yaml:feedpak_version",
                    )

            arrangement_ids: set[str] = set()
            legacy_timeline_sources: dict[str, tuple[str, str, list]] = {}
            arrangements = manifest.get("arrangements") if isinstance(manifest.get("arrangements"), list) else []
            for index, entry in enumerate(arrangements):
                if scan_checkpoint is not None:
                    scan_checkpoint()
                if not isinstance(entry, dict):
                    continue
                arrangement_id = entry.get("id") if isinstance(entry.get("id"), str) else f"#{index + 1}"
                if arrangement_id in arrangement_ids:
                    findings.add(
                        "error",
                        "manifest.duplicate-arrangement-id",
                        f"Arrangement id '{arrangement_id}' is declared more than once.",
                        location=f"manifest.yaml:arrangements[{index}].id",
                        arrangement_id=arrangement_id,
                    )
                arrangement_ids.add(arrangement_id)

                notation_rel = _pointer(
                    reader, entry.get("notation"), f"arrangements[{index}].notation", findings
                )
                notation_data = None
                if notation_rel:
                    notation_data = _load_json(
                        reader, notation_rel, "notation.schema.json", findings, loaded_json
                    )
                    _validate_notation_semantics(
                        notation_data,
                        notation_rel,
                        duration,
                        findings,
                        arrangement_id=arrangement_id,
                    )

                arrangement_rel = _pointer(
                    reader, entry.get("file"), f"arrangements[{index}].file", findings
                )
                data = None
                if arrangement_rel:
                    data = _load_json(
                        reader, arrangement_rel, "arrangement.schema.json", findings, loaded_json
                    )
                    is_keyboard = _is_keyboard_arrangement(entry, data, notation_data)
                    _validate_arrangement_semantics(
                        data,
                        arrangement_rel,
                        arrangement_id,
                        duration,
                        findings,
                        entry=entry,
                        check_lane_collisions=not is_keyboard,
                        check_fretted=_is_fretted_arrangement(entry, data, notation_data),
                    )
                    if isinstance(data, dict):
                        for field in ("beats", "sections"):
                            events = data.get(field)
                            if (
                                field not in legacy_timeline_sources
                                and isinstance(events, list)
                                and events
                            ):
                                legacy_timeline_sources[field] = (
                                    arrangement_rel,
                                    arrangement_id,
                                    events,
                                )

                effective_tones = entry.get("tones")
                tones_location = f"manifest.yaml:arrangements[{index}].tones"
                if not isinstance(effective_tones, dict) and isinstance(data, dict):
                    effective_tones = data.get("tones")
                    tones_location = f"{arrangement_rel}:tones"
                if isinstance(effective_tones, dict):
                    tone_bindings.append(
                        (arrangement_id, tones_location, effective_tones)
                    )

                drum_rel = _pointer(
                    reader, entry.get("drum_tab"), f"arrangements[{index}].drum_tab", findings
                )
                if drum_rel:
                    drum_data = _load_json(
                        reader, drum_rel, "drum-tab.schema.json", findings, loaded_json
                    )
                    semantic_key = ("drum_tab", drum_rel)
                    if semantic_key not in validated_sidecars:
                        validated_sidecars.add(semantic_key)
                        _validate_timed_sidecar_semantics(
                            "drum_tab",
                            drum_data,
                            drum_rel,
                            duration,
                            findings,
                            arrangement_id=arrangement_id,
                        )

            stem_ids: set[str] = set()
            full_mix_rel: str | None = None
            full_mix_entry: dict | None = None
            stem_files: list[tuple[str, str]] = []
            stems = manifest.get("stems") if isinstance(manifest.get("stems"), list) else []
            for index, stem in enumerate(stems):
                if scan_checkpoint is not None:
                    scan_checkpoint()
                if not isinstance(stem, dict):
                    continue
                stem_id = stem.get("id")
                if isinstance(stem_id, str):
                    if stem_id in stem_ids:
                        findings.add(
                            "error",
                            "manifest.duplicate-stem-id",
                            f"Stem id '{stem_id}' is declared more than once.",
                            location=f"manifest.yaml:stems[{index}].id",
                        )
                    stem_ids.add(stem_id)
                relpath = _pointer(reader, stem.get("file"), f"stems[{index}].file", findings)
                if relpath:
                    stem_files.append((str(stem_id or f"#{index + 1}"), relpath))
                    if reader.size(relpath) == 0:
                        findings.add(
                            "error",
                            "media.empty-file",
                            f"Stem '{stem_id or index}' points to an empty file.",
                            location=relpath,
                        )
                    if stem_id == "full" and full_mix_rel is None:
                        full_mix_rel = relpath
                        full_mix_entry = stem

            valid_stems = [stem for stem in stems if isinstance(stem, dict)]
            if len(valid_stems) > 1:
                if full_mix_entry is None and _semver_at_least(feedpak_version, (1, 16, 0)):
                    findings.add(
                        "error",
                        "manifest.missing-full-mix",
                        "This separated Feedpak declares version 1.16 or newer but does not retain the required 'full' mix stem.",
                        location="manifest.yaml:stems",
                    )
                elif full_mix_entry is not None:
                    default = full_mix_entry.get("default")
                    disabled = default is False or (
                        isinstance(default, str)
                        and default.strip().lower() in {"off", "false", "no", "0"}
                    )
                    if not disabled:
                        findings.add(
                            "error",
                            "manifest.full-mix-default-not-off",
                            "The retained 'full' mix is beside separated stems but is not disabled by default; readers that sum enabled stems can double the mix.",
                            location="manifest.yaml:stems",
                        )

            if deep_audio:
                for _stem_id, relpath in stem_files:
                    if not relpath.lower().endswith(".ogg"):
                        size = reader.size(relpath)
                        if size is not None and size > 0:
                            features["deep_audio_unsupported"] += 1
                        continue
                    size = reader.size(relpath)
                    if size is None or size <= 0:
                        continue
                    if size > MAX_DEEP_AUDIO_INSPECTION_BYTES:
                        features["deep_audio_skipped"] += 1
                        continue
                    facts = _inspect_ogg(
                        reader,
                        relpath,
                        max_bytes=MAX_DEEP_AUDIO_INSPECTION_BYTES,
                        scan_checkpoint=scan_checkpoint,
                    )
                    ogg_facts[relpath] = facts
                    features["deep_audio_files"] += 1
                    if facts is None:
                        findings.add(
                            "error",
                            "media.invalid-ogg-container",
                            "A declared Ogg stem has malformed or unreadable container pages.",
                            location=relpath,
                        )
                    elif facts.codec is None:
                        features["deep_audio_unsupported"] += 1

                primary_rel = full_mix_rel or (stem_files[0][1] if stem_files else None)
                primary_facts = ogg_facts.get(primary_rel) if primary_rel else None
                if (
                    primary_rel
                    and primary_facts is not None
                    and _duration_mismatch(primary_facts.duration_seconds, duration)
                ):
                    audio_duration = primary_facts.duration_seconds
                    if audio_duration < duration:
                        code = "media.audio-shorter-than-manifest"
                        difference = duration - audio_duration
                        message = (
                            f"The primary audio ends at {audio_duration:.1f}s, "
                            f"{difference:.1f}s before the manifest duration of {duration:.1f}s."
                        )
                    else:
                        code = "media.audio-longer-than-manifest"
                        difference = audio_duration - duration
                        message = (
                            f"The primary audio is {audio_duration:.1f}s long, "
                            f"{difference:.1f}s beyond the manifest duration of {duration:.1f}s."
                        )
                    findings.add(
                        "warning",
                        code,
                        message,
                        location=primary_rel,
                    )

                digest_stems: dict[bytes, tuple[str, str]] = {}
                duplicate_pairs: list[tuple[str, str, str]] = []
                for stem_id, relpath in stem_files:
                    facts = ogg_facts.get(relpath)
                    if facts is None or stem_id == "full":
                        continue
                    previous = digest_stems.setdefault(facts.payload_digest, (stem_id, relpath))
                    if previous != (stem_id, relpath):
                        duplicate_pairs.append((previous[0], stem_id, relpath))
                if duplicate_pairs:
                    first_id, second_id, relpath = duplicate_pairs[0]
                    findings.add(
                        "warning",
                        "media.duplicate-stem-audio",
                        (
                            f"{len(duplicate_pairs)} separated stem pair(s) contain the same "
                            f"encoded audio; first duplicate is '{first_id}' and '{second_id}'."
                        ),
                        location=relpath,
                        affected_count=len(duplicate_pairs),
                    )

            # Validate the primary lyrics and every additional lyric track.
            lyric_files: list[str] = []
            if manifest.get("lyrics") is not None:
                features["lyrics_declared"] = True
                relpath = _pointer(reader, manifest.get("lyrics"), "lyrics", findings)
                if relpath:
                    lyric_files.append(relpath)
            lyric_tracks = manifest.get("lyric_tracks") if isinstance(manifest.get("lyric_tracks"), list) else []
            lyric_track_ids: set[str] = set()
            for index, track in enumerate(lyric_tracks):
                if not isinstance(track, dict):
                    continue
                features["lyrics_declared"] = True
                track_id = track.get("id")
                if isinstance(track_id, str):
                    if track_id in lyric_track_ids:
                        findings.add(
                            "error",
                            "manifest.duplicate-lyric-track-id",
                            f"Lyric track id '{track_id}' is declared more than once.",
                            location=f"manifest.yaml:lyric_tracks[{index}].id",
                        )
                    lyric_track_ids.add(track_id)
                lyric_stem = track.get("stem")
                if isinstance(lyric_stem, str) and lyric_stem not in stem_ids:
                    findings.add(
                        "warning",
                        "manifest.lyric-track-missing-stem",
                        f"Lyric track '{track_id or index}' references missing stem '{lyric_stem}'.",
                        location=f"manifest.yaml:lyric_tracks[{index}].stem",
                    )
                relpath = _pointer(
                    reader, track.get("file"), f"lyric_tracks[{index}].file", findings
                )
                if relpath:
                    lyric_files.append(relpath)
            for relpath in dict.fromkeys(lyric_files):
                if scan_checkpoint is not None:
                    scan_checkpoint()
                data = _load_json(
                    reader, relpath, "lyrics.schema.json", findings, loaded_json
                )
                features["lyrics_entries"] += _validate_lyrics_semantics(
                    data, relpath, duration, findings
                )

            song_timeline_overrides_legacy = False
            rigs_data = None
            for key, schema_name in SIDE_FILE_SCHEMAS.items():
                if scan_checkpoint is not None:
                    scan_checkpoint()
                if key == "lyrics" or manifest.get(key) is None:
                    continue
                relpath = _pointer(reader, manifest.get(key), key, findings)
                if relpath:
                    data = _load_json(
                        reader, relpath, schema_name, findings, loaded_json
                    )
                    semantic_key = (key, relpath)
                    if key in {
                        "drum_tab", "vocal_pitch", "vocal_pitch_contour",
                        "keys", "harmony",
                    } and semantic_key not in validated_sidecars:
                        validated_sidecars.add(semantic_key)
                        _validate_timed_sidecar_semantics(
                            key, data, relpath, duration, findings
                        )
                    if key == "song_timeline":
                        # Match FeedBack's loader: a readable sidecar overrides
                        # legacy embedded grids only when both arrays exist.
                        song_timeline_overrides_legacy = (
                            isinstance(data, dict)
                            and isinstance(data.get("beats"), list)
                            and isinstance(data.get("sections"), list)
                        )
                        if song_timeline_overrides_legacy:
                            _validate_song_timeline_semantics(
                                data, relpath, duration, findings
                            )
                    elif key == "rigs":
                        rigs_data = data

            drum_tones = manifest.get("drum_tones")
            if isinstance(drum_tones, dict):
                tone_bindings.append(
                    ("drums", "manifest.yaml:drum_tones", drum_tones)
                )
            if rigs_data is not None or tone_bindings:
                _validate_rigs_semantics(
                    rigs_data,
                    str(manifest.get("rigs") or "rigs.json"),
                    duration,
                    reader,
                    findings,
                    tone_bindings,
                    scan_checkpoint=scan_checkpoint,
                )

            if not song_timeline_overrides_legacy:
                for field, source in legacy_timeline_sources.items():
                    relpath, arrangement_id, events = source
                    _validate_song_timeline_semantics(
                        {field: events},
                        relpath,
                        duration,
                        findings,
                        arrangement_id=arrangement_id,
                    )

            cover = manifest.get("cover")
            if cover is not None:
                relpath = _pointer(reader, cover, "cover", findings)
                if relpath:
                    if reader.size(relpath) == 0:
                        findings.add(
                            "error", "media.empty-file", "The cover image file is empty.", location=relpath
                        )
                    else:
                        image = _inspect_cover_image(reader, relpath)
                        if image is None:
                            findings.add(
                                "error",
                                "media.invalid-cover-image",
                                "The declared cover is not a readable JPEG, PNG, or WebP image.",
                                location=relpath,
                            )
                        elif image.format not in {"jpeg", "png", "webp"}:
                            findings.add(
                                "warning",
                                "media.unsupported-cover-image",
                                (
                                    f"The cover uses {image.format.upper()}, but FeedBack "
                                    "serves package covers as JPEG, PNG, or WebP."
                                ),
                                location=relpath,
                            )
                        else:
                            suffix = Path(relpath).suffix.lower()
                            declared_format = {
                                ".png": "png",
                                ".webp": "webp",
                                ".jpg": "jpeg",
                                ".jpeg": "jpeg",
                            }.get(suffix, "jpeg")
                            if declared_format != image.format:
                                findings.add(
                                    "warning",
                                    "media.cover-extension-mismatch",
                                    (
                                        f"The cover filename makes FeedBack serve it as "
                                        f"{declared_format.upper()}, but its bytes are "
                                        f"{image.format.upper()}."
                                    ),
                                    location=relpath,
                                )

            preview = manifest.get("preview")
            if preview is not None:
                features["preview_declared"] = True
                preview_rel = _pointer(reader, preview, "preview", findings)
                if preview_rel:
                    features["preview_available"] = True
                    if reader.size(preview_rel) == 0:
                        findings.add(
                            "error", "media.empty-file", "The preview audio file is empty.", location=preview_rel
                        )
                    elif deep_audio:
                        if not preview_rel.lower().endswith(".ogg"):
                            features["deep_audio_unsupported"] += 1
                        else:
                            preview_size = reader.size(preview_rel)
                            if (
                                preview_size is not None
                                and preview_size > MAX_DEEP_AUDIO_INSPECTION_BYTES
                            ):
                                features["deep_audio_skipped"] += 1
                            else:
                                preview_facts = _inspect_ogg(
                                    reader,
                                    preview_rel,
                                    max_bytes=MAX_DEEP_AUDIO_INSPECTION_BYTES,
                                    scan_checkpoint=scan_checkpoint,
                                )
                                ogg_facts[preview_rel] = preview_facts
                                features["deep_audio_files"] += 1
                                if preview_facts is None:
                                    findings.add(
                                        "error",
                                        "media.invalid-ogg-container",
                                        "The declared Ogg preview has malformed or unreadable container pages.",
                                        location=preview_rel,
                                    )
                                elif preview_facts.codec is None:
                                    features["deep_audio_unsupported"] += 1
                                elif (
                                    preview_facts.duration_seconds is not None
                                    and preview_facts.duration_seconds > MAX_EXPECTED_PREVIEW_SECONDS
                                    and not full_mix_rel
                                ):
                                    findings.add(
                                        "warning",
                                        "media.preview-too-long",
                                        (
                                            f"The preview is {preview_facts.duration_seconds:.1f}s long; "
                                            f"a preview is normally at most {MAX_EXPECTED_PREVIEW_SECONDS:.0f}s."
                                        ),
                                        location=preview_rel,
                                    )
                    if reader.size(preview_rel) and full_mix_rel:
                        ogg_pair = _inspect_suspicious_preview_pair(
                            reader,
                            preview_rel,
                            full_mix_rel,
                            ogg_facts,
                            scan_checkpoint=scan_checkpoint,
                            require_size_ratio=not deep_audio,
                        )
                        same_full_mix = False
                        if ogg_pair is not None:
                            preview_facts, full_facts = ogg_pair
                            same_full_mix = (
                                preview_facts.payload_digest == full_facts.payload_digest
                            )
                            full_duration = full_facts.duration_seconds
                            preview_duration = preview_facts.duration_seconds
                            full_is_long = (
                                full_duration is None
                                or full_duration > MAX_EXPECTED_PREVIEW_SECONDS
                            )
                            if same_full_mix and full_is_long:
                                findings.add(
                                    "warning",
                                    "media.preview-is-full-mix",
                                    (
                                        "The preview contains the same encoded Ogg payload as "
                                        "the full-mix stem instead of a short clip."
                                    ),
                                    location=preview_rel,
                                )
                            elif (
                                preview_duration is not None
                                and full_duration is not None
                                and full_duration > MAX_EXPECTED_PREVIEW_SECONDS
                                and preview_duration / full_duration
                                >= PREVIEW_FULL_LENGTH_RATIO
                            ):
                                findings.add(
                                    "warning",
                                    "media.preview-full-length",
                                    (
                                        f"The preview is {preview_duration:.1f}s long, "
                                        f"{preview_duration / full_duration:.0%} of the "
                                        f"{full_duration:.1f}s full mix, instead of a short clip."
                                    ),
                                    location=preview_rel,
                                )
                        if ogg_pair is None and _same_content(
                            reader,
                            preview_rel,
                            full_mix_rel,
                            scan_checkpoint=scan_checkpoint,
                        ):
                            findings.add(
                                "warning",
                                "media.preview-is-full-mix",
                                (
                                    "The preview is byte-for-byte identical to the full-mix "
                                    "stem instead of being a short clip."
                                ),
                                location=preview_rel,
                            )
    except _PackageBudgetError as exc:
        findings.add("error", "package.validation-budget-exceeded", str(exc))
    except _PackageReadError as exc:
        findings.add("error", "package.unreadable", str(exc))
    except Exception as exc:  # A broken package must never abort a library batch.
        findings.add(
            "error",
            "scan.validation-failed",
            f"Validation could not finish for this package ({type(exc).__name__}: {exc}).",
        )

    return _result(display_name, title, artist, features, findings)
