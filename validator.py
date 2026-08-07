"""Read-only Feedpak validation used by the Library Health plugin.

The official schemas establish format conformance.  The named semantic rules
below are deliberately separate: they identify chart/media mistakes that are
schema-valid but suspicious in practice.  Nothing in this module modifies or
extracts a package.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

import yaml
from jsonschema import Draft202012Validator


SPEC_REVISION = "52548b742f64c2a35052a141976ea1b7889f4b1a"
VALIDATOR_VERSION = f"rules-3:feedpak-{SPEC_REVISION}"
SUPPORTED_MAJOR = 1
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_MEDIA_INSPECTION_BYTES = 128 * 1024 * 1024
MAX_FINDINGS = 250
MAX_SCHEMA_ERRORS_PER_FILE = 100
TIME_PRECISION = 10_000  # Match the highway safeguard's 0.0001-second key.
CHART_END_TOLERANCE_SECONDS = 0.05
HIGHWAY_MAX_FRET = 24
HIGHWAY_MAX_STRINGS = 8
NOTE_BOOLEAN_FIELDS = (
    "ho", "po", "hm", "hp", "pm", "mt", "vb", "tr", "ac", "tp",
    "ln", "fhm", "plk", "slp", "ig",
)
LYRIC_FALLBACK_BREAK_GAP_SECONDS = 4.0  # Match FeedBack's lyric renderer.
MAX_LYRIC_SYLLABLES_PER_LINE = 80
PREVIEW_INSPECTION_SIZE_RATIO = 0.8
PREVIEW_FULL_LENGTH_RATIO = 0.9
MAX_EXPECTED_PREVIEW_SECONDS = 35.0
_KEYBOARD_HINT_RE = re.compile(
    r"(?:^|\b)(?:keys?|piano|keyboard|organ|synth|rhodes|wurlitzer|clav|epiano)(?:\b|$)",
    re.IGNORECASE,
)

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
    location: str = ""
    arrangement_id: str | None = None
    time: float | None = None
    string: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _OggFacts:
    payload_digest: bytes
    duration_seconds: float | None


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
        location: str = "",
        arrangement_id: str | None = None,
        time: float | None = None,
        string: int | None = None,
    ) -> None:
        self.counts[severity] += 1
        finding = Finding(
            severity=severity,
            code=code,
            message=_bounded_text(message),
            location=_bounded_text(location, 500),
            arrangement_id=_bounded_text(arrangement_id, 200) if arrangement_id else None,
            time=round(time, 4) if time is not None and math.isfinite(time) else None,
            string=string,
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

    def __enter__(self) -> "_PackageReader":
        if self.package.is_dir():
            self._root = self.package.resolve()
            return self
        if not self.package.is_file():
            raise _PackageReadError("Package does not exist or is not a regular file.")
        try:
            self._zip = zipfile.ZipFile(self.package, "r")
            for info in self._zip.infolist():
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
                if key in self._members:
                    self.findings.add(
                        "error",
                        "package.duplicate-archive-member",
                        f"Multiple archive members resolve to the same path: {key}",
                        location=key,
                    )
                # FeedBack's extractor is last-entry-wins, so inspection must be too.
                self._members[key] = info
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
    except (json.JSONDecodeError, _PackageReadError) as exc:
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


def _time_key(value: float) -> int:
    return math.floor(value * TIME_PRECISION + 0.5)


@dataclass(frozen=True)
class _LaneEvent:
    kind: str
    fret: int
    location: str
    chord_index: int | None


@dataclass(frozen=True)
class _ChartIssue:
    location: str
    time: float | None = None
    string: int | None = None


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
        self.tuning_length = len(tuning) if isinstance(tuning, list) else None

    def _record(self, code: str, location: str, *, time=None, string=None) -> None:
        self.issues.setdefault(code, []).append(_ChartIssue(
            location=location,
            time=_number(time),
            string=_integer(string),
        ))

    def _emit(self, code: str, severity: str, message) -> None:
        issues = self.issues.get(code)
        if not issues:
            return
        first = issues[0]
        self.findings.add(
            severity,
            code,
            message(len(issues)),
            location=first.location,
            arrangement_id=self.arrangement_id,
            time=first.time,
            string=first.string,
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

        if self.check_fretted and fret is not None:
            if fret < 0:
                self._record("chart.negative-fret", location, time=note_time, string=string)
            elif fret > HIGHWAY_MAX_FRET:
                self._record("chart.fret-beyond-highway", location, time=note_time, string=string)
        if self.check_fretted and string is not None:
            if string >= HIGHWAY_MAX_STRINGS:
                self._record("chart.string-beyond-highway", location, time=note_time, string=string)
            if self.tuning_length is not None and string >= self.tuning_length:
                self._record("chart.string-without-tuning", location, time=note_time, string=string)

        if sustain is not None:
            if sustain < 0:
                self._record("chart.negative-sustain", location, time=note_time, string=string)
            if (
                self.duration is not None
                and self.duration > 0
                and note_time is not None
                and note_time + max(0, sustain)
                > self.duration + CHART_END_TOLERANCE_SECONDS
            ):
                self._record("chart.sustain-after-duration", location, time=note_time, string=string)

        if not self.check_fretted:
            return

        slide_to = _integer(raw.get("sl"))
        slide_unpitched_to = _integer(raw.get("slu"))
        has_pitched_slide = slide_to is not None and slide_to >= 0
        has_unpitched_slide = slide_unpitched_to is not None and slide_unpitched_to >= 0
        if has_pitched_slide and has_unpitched_slide:
            self._record("chart.ambiguous-slide", location, time=note_time, string=string)
        slide_target = (
            slide_to if has_pitched_slide
            else slide_unpitched_to if has_unpitched_slide
            else None
        )
        if slide_target is not None:
            if slide_target > HIGHWAY_MAX_FRET:
                self._record("chart.slide-beyond-highway", location, time=note_time, string=string)
            if sustain is None or sustain <= 0:
                self._record("chart.slide-without-sustain", location, time=note_time, string=string)
            if fret == 0:
                self._record("chart.open-string-slide", location, time=note_time, string=string)
            if fret is not None and fret == slide_target:
                self._record("chart.no-op-slide", location, time=note_time, string=string)

        for field in NOTE_BOOLEAN_FIELDS:
            if field in raw and not isinstance(raw[field], bool):
                self._record(
                    "chart.technique-not-boolean",
                    f"{location}.{field}",
                    time=note_time,
                    string=string,
                )
        if raw.get("ho") is True and raw.get("po") is True:
            self._record("chart.conflicting-techniques", location, time=note_time, string=string)
        if raw.get("hm") is True and raw.get("hp") is True:
            self._record("chart.conflicting-techniques", location, time=note_time, string=string)

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
        if any(
            current[0] < previous[0]
            for previous, current in zip(valid_points, valid_points[1:])
        ):
            self._record("chart.bend-points-out-of-order", location, time=note_time, string=string)
        if sustain is not None and any(
            point_time < 0 or point_time > max(0, sustain) + 0.001
            for point_time, _point_value in valid_points
        ):
            self._record("chart.bend-point-outside-sustain", location, time=note_time, string=string)
        bend_peak = _number(raw.get("bn"))
        if bend_peak is not None and any(
            point_value > bend_peak + 0.01
            for _point_time, point_value in valid_points
        ):
            self._record("chart.bend-exceeds-peak", location, time=note_time, string=string)


    def _inspect_chord(self, raw, location: str) -> None:
        if not isinstance(raw, dict):
            return
        chord_time = _number(raw.get("t"))
        chord_notes = raw.get("notes", []) if isinstance(raw.get("notes"), list) else []
        _template_id, template = self._valid_template_ref(raw.get("id", 0))
        if self.check_fretted and template is None and ("id" in raw or not chord_notes):
            self._record("chart.missing-chord-template", f"{location}.id", time=chord_time)
        if (
            self.check_fretted
            and not chord_notes
            and not self._template_has_playable_note(template)
        ):
            self._record("chart.invisible-chord", location, time=chord_time)

        template_frets = template.get("frets") if isinstance(template, dict) else None
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
            if template_fret is not None and template_fret >= 0 and template_fret != fret:
                self._record(
                    "chart.chord-template-mismatch",
                    note_location,
                    time=chord_time,
                    string=string,
                )

    def _inspect_anchor(self, raw, location: str) -> None:
        if not isinstance(raw, dict):
            return
        anchor_time = _number(raw.get("time"))
        fret = _integer(raw.get("fret"))
        width = _integer(raw.get("width", 4))
        if anchor_time is not None and anchor_time < 0:
            self._record("chart.negative-anchor-time", location, time=anchor_time)
        if (
            self.duration is not None
            and self.duration > 0
            and anchor_time is not None
            and anchor_time > self.duration + CHART_END_TOLERANCE_SECONDS
        ):
            self._record("chart.anchor-after-duration", location, time=anchor_time)
        if not self.check_fretted:
            return
        if fret is not None and fret < 0:
            self._record("chart.invalid-anchor", location, time=anchor_time)
        if width is not None and width <= 0:
            self._record("chart.invalid-anchor", location, time=anchor_time)
        if fret is not None and width is not None and width > 0:
            effective_start = max(1, fret)
            if (
                fret > HIGHWAY_MAX_FRET
                or effective_start + width - 1 > HIGHWAY_MAX_FRET
            ):
                self._record("chart.anchor-beyond-highway", location, time=anchor_time)

    def _inspect_handshape(self, raw, location: str) -> None:
        if not isinstance(raw, dict):
            return
        start = _number(raw.get("start_time"))
        end = _number(raw.get("end_time"))
        _template_id, template = self._valid_template_ref(raw.get("chord_id"))
        if start is None or end is None or start < 0 or end <= start:
            self._record("chart.invalid-handshape-span", location, time=start)
        if self.check_fretted and template is None:
            self._record(
                "chart.missing-handshape-template",
                f"{location}.chord_id",
                time=start,
            )
        if (
            self.duration is not None
            and self.duration > 0
            and end is not None
            and end > self.duration + CHART_END_TOLERANCE_SECONDS
        ):
            self._record("chart.handshape-after-duration", location, time=start)

    def _inspect_templates(self) -> None:
        if not self.check_fretted:
            return
        for template_index, template in enumerate(self.templates):
            if not isinstance(template, dict):
                continue
            location = f"{self.relpath}:templates[{template_index}]"
            frets = template.get("frets")
            fingers = template.get("fingers")
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
                and end > self.duration + CHART_END_TOLERANCE_SECONDS
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

        for index, note in enumerate(self.notes):
            self._inspect_note(note, f"{self.relpath}:notes[{index}]")
        for index, chord in enumerate(self.chords):
            self._inspect_chord(chord, f"{self.relpath}:chords[{index}]")
        for index, anchor in enumerate(self.anchors):
            self._inspect_anchor(anchor, f"{self.relpath}:anchors[{index}]")
        for index, handshape in enumerate(self.handshapes):
            self._inspect_handshape(handshape, f"{self.relpath}:handshapes[{index}]")

        self._inspect_templates()
        self._inspect_conflicting_anchors()
        self._inspect_phrases()
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
                "chart.phrases-out-of-order", "error",
                lambda count: f"{count} phrase timeline(s) are not chronological; mastery filtering concatenates them in authored order.",
            ),
            (
                "chart.phrase-events-out-of-order", "error",
                lambda count: f"{count} difficulty-level timeline(s) are not chronological; the mastery view can omit or mis-time events.",
            ),
            ("chart.negative-capo", "error", lambda count: f"{count} arrangement capo value(s) are negative."),
            ("chart.negative-fret", "error", lambda count: f"{count} fretted note(s) use a fret below 0."),
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
                lambda count: f"{count} handshape(s) have a missing, negative, or non-positive time span.",
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
            self._emit(code, severity, message)

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

    lanes: dict[tuple[int, int], list[_LaneEvent]] = {}
    negative: list[tuple[str, float]] = []
    after_duration: list[tuple[str, float]] = []

    def add_event(raw, kind: str, location: str, chord_index: int | None = None, time=None):
        if not isinstance(raw, dict):
            return
        event_time = _number(raw.get("t") if time is None else time)
        string = _integer(raw.get("s"))
        fret = _integer(raw.get("f"))
        if event_time is None or string is None or fret is None:
            return
        if event_time < 0:
            negative.append((location, event_time))
        if duration is not None and duration > 0 and event_time > duration + 0.01:
            after_duration.append((location, event_time))
        lanes.setdefault((_time_key(event_time), string), []).append(_LaneEvent(
            kind=kind,
            fret=fret,
            location=location,
            chord_index=chord_index,
        ))

    for index, note in enumerate(data.get("notes", []) if isinstance(data.get("notes"), list) else []):
        add_event(note, "note", f"{relpath}:notes[{index}]")

    chords = data.get("chords", []) if isinstance(data.get("chords"), list) else []
    for chord_index, chord in enumerate(chords):
        if not isinstance(chord, dict):
            continue
        chord_time = chord.get("t")
        chord_notes = chord.get("notes", []) if isinstance(chord.get("notes"), list) else []
        for note_index, note in enumerate(chord_notes):
            add_event(
                note,
                "chord",
                f"{relpath}:chords[{chord_index}].notes[{note_index}]",
                chord_index=chord_index,
                time=chord_time,
            )

    if negative:
        location, first_time = negative[0]
        findings.add(
            "error",
            "chart.negative-time",
            f"{len(negative)} chart event(s) occur before the song starts; first at {first_time:.4f}s.",
            location=location,
            arrangement_id=arrangement_id,
            time=first_time,
        )
    if after_duration:
        location, first_time = after_duration[0]
        findings.add(
            "warning",
            "chart.event-after-duration",
            f"{len(after_duration)} chart event(s) occur after the manifest duration; first at {first_time:.4f}s.",
            location=location,
            arrangement_id=arrangement_id,
            time=first_time,
        )

    if not check_lane_collisions:
        return

    for (time_tick, string), events in sorted(lanes.items()):
        if len(events) < 2:
            continue
        event_time = time_tick / TIME_PRECISION
        frets = sorted({event.fret for event in events})
        first = events[0]
        if len(frets) > 1:
            findings.add(
                "error",
                "chart.string-conflict",
                (
                    f"String index {string} (0 = lowest) has different frets "
                    f"({', '.join(map(str, frets))}) at {event_time:.4f}s."
                ),
                location=first.location,
                arrangement_id=arrangement_id,
                time=event_time,
                string=string,
            )
            continue

        notes = [event for event in events if event.kind == "note"]
        chord_notes = [event for event in events if event.kind == "chord"]
        if len(notes) > 1:
            findings.add(
                "warning",
                "chart.duplicate-note",
                (
                    f"{len(notes)} standalone notes use string index {string} "
                    f"(0 = lowest), fret {frets[0]} at {event_time:.4f}s."
                ),
                location=notes[0].location,
                arrangement_id=arrangement_id,
                time=event_time,
                string=string,
            )

        chord_groups: dict[int | None, int] = {}
        for event in chord_notes:
            chord_groups[event.chord_index] = chord_groups.get(event.chord_index, 0) + 1
        if any(count > 1 for count in chord_groups.values()):
            findings.add(
                "error",
                "chart.chord-string-duplicate",
                (
                    f"One chord contains more than one note on string index {string} "
                    f"(0 = lowest) at {event_time:.4f}s."
                ),
                location=chord_notes[0].location,
                arrangement_id=arrangement_id,
                time=event_time,
                string=string,
            )
        elif len(chord_groups) > 1:
            findings.add(
                "warning",
                "chart.coincident-chords",
                f"Multiple chord events use string {string}, fret {frets[0]} at {event_time:.4f}s.",
                location=chord_notes[0].location,
                arrangement_id=arrangement_id,
                time=event_time,
                string=string,
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
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        start = _number(entry.get("t"))
        length = _number(entry.get("d"))
        if start is None or length is None:
            continue
        valid.append((index, start, length))
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
        )
    if any(current[1] < previous[1] for previous, current in zip(valid, valid[1:])):
        findings.add(
            "warning",
            "lyrics.out-of-order",
            "Lyric syllables are not ordered by start time.",
            location=relpath,
        )
    if after_duration:
        index, start = after_duration[0]
        findings.add(
            "warning",
            "lyrics.after-duration",
            f"{len(after_duration)} lyric syllable(s) extend beyond the manifest duration; first starts at {start:.4f}s.",
            location=f"{relpath}:[{index}]",
            time=start,
        )

    authored_breaks = 0
    longest_line = 0
    current_line = 0
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
            current_line = 0

        timed_syllables += 1
        current_line += 1
        if entry["w"].endswith("+"):
            authored_breaks += 1
            longest_line = max(longest_line, current_line)
            current_line = 0
        previous_end = start + length

    longest_line = max(longest_line, current_line)
    if longest_line > MAX_LYRIC_SYLLABLES_PER_LINE:
        findings.add(
            "warning",
            "lyrics.too-few-line-breaks",
            (
                f"One lyric line contains {longest_line} timed syllables; the track has "
                f"{authored_breaks} authored '+' line break marker(s) across "
                f"{timed_syllables} timed syllables. Add '+' to the final syllable of "
                "each intended lyric line."
            ),
            location=relpath,
        )
    return len(valid)


def _same_content(reader: _PackageReader, left: str, right: str) -> bool:
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
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        return hasher.digest()

    try:
        return digest(left) == digest(right)
    except _PackageReadError:
        return False


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        block = stream.read(size - len(data))
        if not block:
            raise ValueError("unexpected end of Ogg stream")
        data.extend(block)
    return bytes(data)


def _inspect_ogg(reader: _PackageReader, relpath: str) -> _OggFacts | None:
    """Read bounded Ogg container facts without decoding audio samples."""
    size = reader.size(relpath)
    if (
        not relpath.lower().endswith(".ogg")
        or size is None
        or size <= 0
        or size > MAX_MEDIA_INSPECTION_BYTES
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
    except (_PackageReadError, OSError, ValueError):
        return None

    if not saw_page:
        return None

    duration: float | None = None
    ident = bytes(prefix)
    if last_granule is not None and ident.startswith(b"\x01vorbis") and len(ident) >= 16:
        sample_rate = int.from_bytes(ident[12:16], "little")
        if sample_rate > 0:
            duration = last_granule / sample_rate
    elif last_granule is not None and ident.startswith(b"OpusHead") and len(ident) >= 12:
        pre_skip = int.from_bytes(ident[10:12], "little")
        duration = max(0, last_granule - pre_skip) / 48_000

    return _OggFacts(
        payload_digest=payload_hasher.digest(),
        duration_seconds=duration,
    )


def _inspect_suspicious_preview_pair(
    reader: _PackageReader,
    preview_rel: str,
    full_mix_rel: str,
) -> tuple[_OggFacts, _OggFacts] | None:
    preview_size = reader.size(preview_rel)
    full_size = reader.size(full_mix_rel)
    if (
        preview_size is None
        or full_size is None
        or preview_size <= 0
        or full_size <= 0
        or preview_size / full_size < PREVIEW_INSPECTION_SIZE_RATIO
    ):
        return None
    preview_facts = _inspect_ogg(reader, preview_rel)
    full_facts = _inspect_ogg(reader, full_mix_rel)
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
        else "healthy"
    )
    return {
        "schema": "library_health.package.v1",
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


def validate_feedpak(package: Path, package_name: str | None = None) -> dict:
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
    }
    loaded_json: dict[tuple[str, str], object | None] = {}

    try:
        with _PackageReader(package, findings) as reader:
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
                manifest = yaml.safe_load(reader.read_text(manifest_rel))
            except (yaml.YAMLError, _PackageReadError) as exc:
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
            arrangements = manifest.get("arrangements") if isinstance(manifest.get("arrangements"), list) else []
            for index, entry in enumerate(arrangements):
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

                arrangement_rel = _pointer(
                    reader, entry.get("file"), f"arrangements[{index}].file", findings
                )
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

                drum_rel = _pointer(
                    reader, entry.get("drum_tab"), f"arrangements[{index}].drum_tab", findings
                )
                if drum_rel:
                    _load_json(
                        reader, drum_rel, "drum-tab.schema.json", findings, loaded_json
                    )

            stem_ids: set[str] = set()
            full_mix_rel: str | None = None
            stems = manifest.get("stems") if isinstance(manifest.get("stems"), list) else []
            for index, stem in enumerate(stems):
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
                    if reader.size(relpath) == 0:
                        findings.add(
                            "error",
                            "media.empty-file",
                            f"Stem '{stem_id or index}' points to an empty file.",
                            location=relpath,
                        )
                    if stem_id == "full" and full_mix_rel is None:
                        full_mix_rel = relpath

            # Validate the primary lyrics and every additional lyric track.
            lyric_files: list[str] = []
            if manifest.get("lyrics") is not None:
                features["lyrics_declared"] = True
                relpath = _pointer(reader, manifest.get("lyrics"), "lyrics", findings)
                if relpath:
                    lyric_files.append(relpath)
            lyric_tracks = manifest.get("lyric_tracks") if isinstance(manifest.get("lyric_tracks"), list) else []
            for index, track in enumerate(lyric_tracks):
                if not isinstance(track, dict):
                    continue
                features["lyrics_declared"] = True
                relpath = _pointer(
                    reader, track.get("file"), f"lyric_tracks[{index}].file", findings
                )
                if relpath:
                    lyric_files.append(relpath)
            for relpath in dict.fromkeys(lyric_files):
                data = _load_json(
                    reader, relpath, "lyrics.schema.json", findings, loaded_json
                )
                features["lyrics_entries"] += _validate_lyrics_semantics(
                    data, relpath, duration, findings
                )

            for key, schema_name in SIDE_FILE_SCHEMAS.items():
                if key == "lyrics" or manifest.get(key) is None:
                    continue
                relpath = _pointer(reader, manifest.get(key), key, findings)
                if relpath:
                    _load_json(reader, relpath, schema_name, findings, loaded_json)

            cover = manifest.get("cover")
            if cover is not None:
                relpath = _pointer(reader, cover, "cover", findings)
                if relpath and reader.size(relpath) == 0:
                    findings.add(
                        "error", "media.empty-file", "The cover image file is empty.", location=relpath
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
                    elif full_mix_rel:
                        ogg_pair = _inspect_suspicious_preview_pair(
                            reader, preview_rel, full_mix_rel
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
                            reader, preview_rel, full_mix_rel
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
    except _PackageReadError as exc:
        findings.add("error", "package.unreadable", str(exc))
    except Exception as exc:  # A broken package must never abort a library batch.
        findings.add(
            "error",
            "scan.validation-failed",
            f"Validation could not finish for this package ({type(exc).__name__}: {exc}).",
        )

    return _result(display_name, title, artist, features, findings)
