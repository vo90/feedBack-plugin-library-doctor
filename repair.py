"""Conservative repair planning and transactional package updates.

Scanning remains read-only.  This module is used only by the explicit repair
preview and confirmation routes.  Every applied repair is rebuilt from current
source bytes, backed up, validated as a candidate package, and limited to the
small allowlist below.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

import yaml


REPAIR_CATALOG_VERSION = "repairs-7"
REPAIR_PLAN_SCHEMA = "library_doctor.repair_plan.v1"
MAX_REPAIR_TEXT_BYTES = 64 * 1024 * 1024
MAX_REPAIR_STRUCTURE_ITEMS = 2_000_000
MAX_REPAIR_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_DECLARED_REPAIR_MEMBERS = 1_000
MAX_RECOVERY_BACKUP_BYTES = 512 * 1024 * 1024
PACKAGE_SUFFIXES = (".feedpak", ".sloppak")
PACKAGE_REPAIR_SCHEMA = "library_doctor.package_repair.v1"
BACKUP_SCHEMA = "library_doctor.repair_backup.v2"
HISTORY_SCHEMA = "library_doctor.repair_history.v1"
MAX_REPAIR_HISTORY = 50
_BACKUP_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{12}$")
ALL_SAFE_RULE_CODE = "package.all-safe"


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses ambiguous repeated mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class RepairPlanningError(ValueError):
    """A stable, user-safe reason why a repair preview cannot be produced."""

    def __init__(self, code: str, message: str, *, file_state: str = "unchanged"):
        super().__init__(message)
        self.code = code
        self.file_state = file_state


@dataclass(frozen=True)
class RepairDefinition:
    rule_code: str
    action_kind: str
    source_kind: str
    item_name: str
    safety: str
    title: str
    description: str
    player_result: str
    user_value: str
    change_kind: str = "remove_duplicates"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DuplicateGroup:
    keep_index: int
    remove_indices: tuple[int, ...]
    entry_sha256: str

    def to_dict(self) -> dict:
        return {
            "keep_index": self.keep_index,
            "remove_indices": list(self.remove_indices),
            "entry_sha256": self.entry_sha256,
        }


@dataclass(frozen=True)
class DeleteArrayItems:
    array_path: tuple[str | int, ...]
    expected_length: int
    duplicate_groups: tuple[DuplicateGroup, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return tuple(sorted(
            (
                index
                for group in self.duplicate_groups
                for index in group.remove_indices
            ),
            reverse=True,
        ))

    def to_dict(self) -> dict:
        return {
            "operation": "delete_array_items",
            "array_path": list(self.array_path),
            "expected_length": self.expected_length,
            "remove_indices": list(self.remove_indices),
            "duplicate_groups": [
                group.to_dict() for group in self.duplicate_groups
            ],
        }


@dataclass(frozen=True)
class ChordMatchGroup:
    chord_index: int
    chord_note_index: int
    chord_sha256: str
    remove_indices: tuple[int, ...]
    entry_sha256: str

    def to_dict(self) -> dict:
        return {
            "chord_index": self.chord_index,
            "chord_note_index": self.chord_note_index,
            "chord_sha256": self.chord_sha256,
            "remove_indices": list(self.remove_indices),
            "entry_sha256": self.entry_sha256,
        }


@dataclass(frozen=True)
class DeleteNotesMatchingChords:
    note_array_path: tuple[str | int, ...]
    chord_array_path: tuple[str | int, ...]
    expected_note_length: int
    expected_chord_length: int
    match_groups: tuple[ChordMatchGroup, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return tuple(sorted(
            (
                index
                for group in self.match_groups
                for index in group.remove_indices
            ),
            reverse=True,
        ))

    def to_dict(self) -> dict:
        return {
            "operation": "delete_notes_matching_chords",
            "note_array_path": list(self.note_array_path),
            "chord_array_path": list(self.chord_array_path),
            "expected_note_length": self.expected_note_length,
            "expected_chord_length": self.expected_chord_length,
            "remove_indices": list(self.remove_indices),
            "match_groups": [group.to_dict() for group in self.match_groups],
        }


@dataclass(frozen=True)
class StableSortBendPoints:
    array_path: tuple[str | int, ...]
    expected_length: int
    original_sha256: str
    sorted_sha256: str
    sorted_indices: tuple[int, ...]
    moved_count: int
    note_time: float | None
    string: int | None

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "stable_sort_bend_points",
            "array_path": list(self.array_path),
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "sorted_sha256": self.sorted_sha256,
            "sorted_indices": list(self.sorted_indices),
            "moved_count": self.moved_count,
            "note_time": self.note_time,
            "string": self.string,
        }


@dataclass(frozen=True)
class StableSortLyricCues:
    expected_length: int
    original_sha256: str
    sorted_sha256: str
    sorted_indices: tuple[int, ...]
    moved_count: int

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "stable_sort_lyric_cues",
            "array_path": [],
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "sorted_sha256": self.sorted_sha256,
            "sorted_indices": list(self.sorted_indices),
            "moved_count": self.moved_count,
        }


_REPAIR_DEFINITIONS = (
    RepairDefinition(
        rule_code="chart.duplicate-note",
        action_kind="remove_exact_duplicate_notes",
        source_kind="arrangement",
        item_name="note",
        safety="safe_automatic",
        title="Remove exact duplicate notes",
        description=(
            "Keep the first note and remove only copies with identical stored "
            "values and properties from the same note list."
        ),
        player_result=(
            "The song keeps one note at every repaired position. Its timing, "
            "fret, sustain, and techniques remain unchanged."
        ),
        user_value=(
            "The highway has one unambiguous gem to display and process instead "
            "of redundant copies of the same authored note."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-chord-note",
        action_kind="remove_exact_duplicate_chord_notes",
        source_kind="arrangement",
        item_name="chord note",
        safety="safe_automatic",
        title="Remove exact duplicate chord notes",
        description=(
            "Keep the first chord member and remove only identical copies from "
            "inside that same chord."
        ),
        player_result=(
            "The chord keeps the same strings, frets, timing, and techniques, "
            "with one stored instruction per intended chord member."
        ),
        user_value=(
            "The editor and highway no longer have redundant gems stacked on "
            "one string inside the chord."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-chord",
        action_kind="remove_exact_duplicate_chords",
        source_kind="arrangement",
        item_name="chord",
        safety="safe_automatic",
        title="Remove exact duplicate chords",
        description=(
            "Keep the first complete chord and remove only copies with identical "
            "timing, shape, notes, techniques, and stored properties from the same list."
        ),
        player_result=(
            "One complete chord remains at each repaired position with all of "
            "its authored notes and techniques unchanged."
        ),
        user_value=(
            "The editor and highway have one unambiguous chord event instead of "
            "processing identical copies at the same moment."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-anchor",
        action_kind="remove_exact_duplicate_anchors",
        source_kind="arrangement",
        item_name="anchor",
        safety="safe_automatic",
        title="Remove exact duplicate anchors",
        description=(
            "Keep the first anchor and remove only copies with identical timing, "
            "fret window, width, and stored properties from the same list."
        ),
        player_result=(
            "The same fret-window instruction remains at each repaired position."
        ),
        user_value=(
            "The highway receives one clear hand-position instruction without "
            "redundant anchor data."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-handshape",
        action_kind="remove_exact_duplicate_handshapes",
        source_kind="arrangement",
        item_name="handshape",
        safety="safe_automatic",
        title="Remove exact duplicate handshapes",
        description=(
            "Keep the first handshape and remove only copies with identical chord, "
            "time span, and stored properties from the same list."
        ),
        player_result=(
            "The same chord-shape guide remains over the same time span."
        ),
        user_value=(
            "The highway has one clear shape guide instead of redundant overlay data."
        ),
    ),
    RepairDefinition(
        rule_code="chart.note-duplicates-chord",
        action_kind="remove_notes_duplicating_chords",
        source_kind="arrangement",
        item_name="standalone note",
        safety="safe_automatic",
        title="Remove notes already contained in chords",
        description=(
            "Keep the complete chord and remove only standalone notes at the "
            "same time whose string, fret, and every stored playable property "
            "exactly match one explicit chord member."
        ),
        player_result=(
            "The complete chord remains at every repaired position, including "
            "all of its strings and techniques. Only the redundant standalone "
            "copy is removed."
        ),
        user_value=(
            "The editor and highway show one clear chord instruction instead "
            "of stacking an extra gem on one of the chord strings."
        ),
    ),
    RepairDefinition(
        rule_code="chart.bend-points-out-of-order",
        action_kind="reorder_bend_points",
        source_kind="arrangement",
        item_name="bend curve",
        safety="safe_automatic",
        title="Put bend points in chronological order",
        description=(
            "Stable-sort each affected bend curve by its existing relative "
            "timestamps. Every bend point and stored property is preserved, "
            "and points with equal timestamps keep their authored order."
        ),
        player_result=(
            "FeedBack receives each bend curve in playback order directly from "
            "the Feedpak instead of repairing its order temporarily while loading."
        ),
        user_value=(
            "Bend animation becomes portable and predictable in FeedBack, the "
            "editor, and other Feedpak tools without changing the authored curve."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="lyrics.out-of-order",
        action_kind="reorder_lyric_cues",
        source_kind="lyrics",
        item_name="lyric timeline",
        safety="safe_automatic",
        title="Put lyric cues in chronological order",
        description=(
            "Stable-sort the existing lyric cues by their start times. Every "
            "cue, word, duration, and stored property is preserved, and cues "
            "with equal start times keep their authored order."
        ),
        player_result=(
            "FeedBack receives the same lyric cues in playback order, so the "
            "lyric display no longer has to process a cue after a later one."
        ),
        user_value=(
            "Lyrics advance predictably with the song without deleting, "
            "rewriting, or retiming any authored text."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="timeline.duplicate-beat",
        action_kind="remove_exact_duplicate_beat_markers",
        source_kind="timeline",
        item_name="beat marker",
        safety="safe_automatic",
        title="Remove exact duplicate beat markers",
        description=(
            "Keep the first beat marker and remove only later copies with "
            "identical time, measure, and every other stored property from the "
            "active song timeline."
        ),
        player_result=(
            "The same beat and measure grid remains, with one stored instruction "
            "at each repaired position. Conflicting beat markers, if present, "
            "remain visible for manual review."
        ),
        user_value=(
            "FeedBack receives a clean rhythm grid without changing any authored "
            "beat time or measure and without guessing between conflicting data."
        ),
    ),
    RepairDefinition(
        rule_code="drums.duplicate-hit",
        action_kind="remove_exact_duplicate_drum_hits",
        source_kind="drum_tab",
        item_name="drum hit",
        safety="safe_automatic",
        title="Remove exact duplicate drum hits",
        description=(
            "Keep the first hit and remove only copies with identical stored "
            "values and properties from the same drum-hit list."
        ),
        player_result=(
            "The song keeps one drum hit at every repaired position, with the "
            "same timing and authored properties."
        ),
        user_value=(
            "The drum highway has one unambiguous hit to display and process "
            "instead of redundant copies."
        ),
    ),
)

_REPAIR_BY_RULE = {
    definition.rule_code: definition for definition in _REPAIR_DEFINITIONS
}

_DEFAULT_REPAIR_BY_SOURCE = {
    "arrangement": _REPAIR_BY_RULE["chart.duplicate-note"],
    "lyrics": _REPAIR_BY_RULE["lyrics.out-of-order"],
    "timeline": _REPAIR_BY_RULE["timeline.duplicate-beat"],
    "drum_tab": _REPAIR_BY_RULE["drums.duplicate-hit"],
}

# Repairs are recalculated against the output of every earlier step.  This is
# important for relationships that become unambiguous only after exact chord
# redundancies have been removed.
_ALL_SAFE_RULE_ORDER = (
    "chart.bend-points-out-of-order",
    "chart.duplicate-chord-note",
    "chart.duplicate-chord",
    "chart.duplicate-note",
    "chart.note-duplicates-chord",
    "chart.duplicate-anchor",
    "chart.duplicate-handshape",
    "lyrics.out-of-order",
    "timeline.duplicate-beat",
    "drums.duplicate-hit",
)

_ALL_SAFE_DEFINITION = {
    "rule_code": ALL_SAFE_RULE_CODE,
    "safety": "safe_automatic",
    "title": "Fix all safe issues",
    "description": (
        "Apply every deterministic safe song-data repair currently available in "
        "this Feedpak as one validated transaction."
    ),
    "player_result": (
        "Eligible redundant instructions are removed and supported ordering "
        "problems are normalized without changing the intended musical data. "
        "Findings that require judgment, including conflicting entries, remain "
        "unchanged in the refreshed package report."
    ),
    "user_value": (
        "The song is cleaned in one step without requiring a separate confirmation "
        "for every safe repair type or guessing how ambiguous data should be authored."
    ),
}


def repair_catalog() -> list[dict]:
    """Return the explicit repair allowlist in stable display order."""
    return [definition.to_dict() for definition in _REPAIR_DEFINITIONS]


def all_safe_repair_definition() -> dict:
    """Return user-facing metadata for the combined per-package repair."""
    return dict(_ALL_SAFE_DEFINITION)


def repair_for_rule(rule_code: str) -> dict | None:
    """Return a repair definition only when the rule is explicitly supported."""
    for definition in _REPAIR_DEFINITIONS:
        if definition.rule_code == rule_code:
            return definition.to_dict()
    return None


def apply_json_member(raw: bytes, plan: dict) -> bytes:
    """Apply a trusted plan only when it still matches the exact source bytes."""
    if not isinstance(plan, dict) or plan.get("schema") != REPAIR_PLAN_SCHEMA:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    unsigned = {key: value for key, value in plan.items() if key != "plan_id"}
    if plan.get("plan_id") != _digest_json(unsigned):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    if hashlib.sha256(raw).hexdigest() != source.get("sha256"):
        raise RepairPlanningError(
            "source_changed",
            "The song changed after this preview. Review the safe fix again before applying it.",
        )

    document = _parse_json(raw)
    _inspect_structure(document)
    source_kind = source.get("source_kind")
    expected_shape = list if source_kind == "lyrics" else dict
    if source_kind not in _DEFAULT_REPAIR_BY_SOURCE or not isinstance(
        document, expected_shape
    ):
        raise RepairPlanningError(
            "invalid_document_shape",
            "The song file does not have the expected JSON structure for this repair.",
        )

    removed: set[tuple[tuple[str | int, ...], int]] = set()
    reordered: set[tuple[str | int, ...]] = set()
    for action in plan.get("actions", []):
        if not isinstance(action, dict) or action.get("safety") != "safe_automatic":
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        for operation in action.get("operations", []):
            _apply_operation(document, operation, removed, reordered)
    return _render_json(document, raw)


class RepairService:
    """Resolve library packages and apply explicitly confirmed safe repairs."""

    def __init__(
        self,
        *,
        config_dir: Path,
        get_dlc_dir,
        validate_feedpak,
        validator_version: str,
        log,
        legacy_schemas: dict | None = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._get_dlc_dir = get_dlc_dir
        self._validate_feedpak = validate_feedpak
        self._validator_version = validator_version
        self._log = log
        compatibility = legacy_schemas if isinstance(legacy_schemas, dict) else {}
        self._legacy_backup_schemas = frozenset(
            item
            for item in compatibility.get("repair_backup", ())
            if isinstance(item, str)
        )
        self._legacy_history_schemas = frozenset(
            item
            for item in compatibility.get("repair_history", ())
            if isinstance(item, str)
        )
        self._lock = threading.Lock()

    def preview(self, package: str, rule_code: str) -> dict:
        """Return a bounded, read-only summary bound to current package bytes."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_package(package_path, package_name, rule_code)
            return self._public_plan(internal)

    def preview_all(self, package: str) -> dict:
        """Preview every currently available safe repair for one Feedpak."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_all_package(package_path, package_name)
            return self._public_plan(internal)

    def apply(
        self,
        package: str,
        rule_code: str,
        plan_id: str,
        *,
        deep_audio: bool = False,
    ) -> dict:
        """Rebuild, validate, back up, and atomically commit one package repair."""
        if not isinstance(plan_id, str) or len(plan_id) != 64:
            raise RepairPlanningError("invalid_plan", "Review the safe fix again before applying it.")
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_package(package_path, package_name, rule_code)
            if internal["plan_id"] != plan_id:
                raise RepairPlanningError(
                    "source_changed",
                    "The song changed after this preview. Review the safe fix again before applying it.",
                )
            if not internal["available"]:
                raise RepairPlanningError(
                    "nothing_to_repair",
                    "The selected safe issue is no longer present in this package.",
                )
            return self._apply_internal(
                package_path,
                package_name,
                internal,
                deep_audio=deep_audio,
            )

    def apply_all(
        self,
        package: str,
        plan_id: str,
        *,
        deep_audio: bool = False,
    ) -> dict:
        """Apply all available safe repairs as one package transaction."""
        if not isinstance(plan_id, str) or len(plan_id) != 64:
            raise RepairPlanningError(
                "invalid_plan",
                "Review all safe fixes again before applying them.",
            )
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_all_package(package_path, package_name)
            if internal["plan_id"] != plan_id:
                raise RepairPlanningError(
                    "source_changed",
                    "The song changed after this preview. Review all safe fixes again before applying them.",
                )
            if not internal["available"]:
                raise RepairPlanningError(
                    "nothing_to_repair",
                    "No supported safe repairs are currently available in this package.",
                )
            return self._apply_internal(
                package_path,
                package_name,
                internal,
                deep_audio=deep_audio,
            )

    def _apply_internal(
        self,
        package_path: Path,
        package_name: str,
        internal: dict,
        *,
        deep_audio: bool,
    ) -> dict:
        """Validate and commit one already-recalculated package plan."""
        originals = {
            item["member_path"]: item["raw"] for item in internal["_members"]
        }
        replacements = {
            item["member_path"]: (
                item["replacement"]
                if "replacement" in item
                else apply_json_member(item["raw"], item["plan"])
            )
            for item in internal["_members"]
        }
        before = self._validate_feedpak(
            package_path, package_name, deep_audio=bool(deep_audio)
        )
        candidate, cleanup = self._candidate(package_path, replacements)
        try:
            after = self._validate_feedpak(
                candidate, package_name, deep_audio=bool(deep_audio)
            )
            rule_codes = internal.get("rule_codes")
            if not isinstance(rule_codes, list) or not rule_codes:
                rule_codes = [internal["rule_code"]]
            self._verify_validation(before, after, rule_codes)
            backup_id = self._create_backup(
                package_name,
                package_path,
                originals,
                replacements,
                internal["plan_id"],
                internal["rule_code"],
                self._public_plan(internal),
            )
            self._commit(package_path, candidate, replacements, originals)
        finally:
            cleanup()

        result = {
            **self._public_plan(internal),
            "applied": True,
            "outcome": "success",
            "backup_id": backup_id,
            "report": after,
            "file_handling": self._file_handling(backup_id),
        }
        result["receipt_saved"] = self._record_history({
            "id": uuid.uuid4().hex,
            "action": "repair",
            "outcome": "success",
            "completed_at": time.time(),
            "package": package_name,
            "title": after.get("title") or package_name,
            "artist": after.get("artist") or "",
            "rule_code": internal["rule_code"],
            "rule_codes": internal.get("rule_codes", [internal["rule_code"]]),
            "repair_summaries": internal.get("repair_summaries", []),
            "backup_id": backup_id,
            "change_kind": internal.get("change_kind", "remove_duplicates"),
            "change_count": internal.get("change_count", internal["removed_count"]),
            "removed_count": internal["removed_count"],
            "musical_positions": internal["musical_positions"],
            "item_name": internal["item_name"],
            "player_result": internal["player_result"],
            "user_value": internal["user_value"],
            "file_handling": result["file_handling"],
        })
        return result

    def history(self, limit: int = 5) -> dict:
        """Return a small, non-sensitive repair receipt history for the UI."""
        safe_limit = max(1, min(int(limit), 20))
        with self._lock:
            items = self._read_history()
            if not items:
                items = self._recover_legacy_receipts()
                if items:
                    self._write_history(items)
            return {
                "schema": HISTORY_SCHEMA,
                "items": list(reversed(items[-safe_limit:])),
            }

    def _recover_legacy_receipts(self) -> list[dict]:
        """Surface verified backups created before persistent receipts existed."""
        backup_dir = self._config_dir / "library_doctor" / "repair_backups"
        try:
            candidates = sorted(backup_dir.glob("*.zip"))[-20:]
        except OSError:
            return []
        receipts = []
        for path in candidates:
            backup_id = path.stem
            if not _BACKUP_ID_RE.fullmatch(backup_id):
                continue
            try:
                with zipfile.ZipFile(path, "r") as archive:
                    info = archive.getinfo("repair.json")
                    if info.file_size > MAX_REPAIR_MANIFEST_BYTES:
                        continue
                    metadata = json.loads(archive.read(info).decode("utf-8"))
                package = metadata.get("package") if isinstance(metadata, dict) else None
                if not isinstance(package, str) or metadata.get("backup_id") != backup_id:
                    continue
                _root, package_path, package_name = self._resolve_package(package)
                members = metadata.get("members")
                if not isinstance(members, list) or not members:
                    continue
                if not all(
                    isinstance(entry, dict)
                    and isinstance(entry.get("repaired_sha256"), str)
                    and hashlib.sha256(self._read_member(
                        package_path,
                        _validate_member_path(entry.get("member_path")),
                        MAX_REPAIR_TEXT_BYTES,
                    )).hexdigest() == entry["repaired_sha256"]
                    for entry in members
                ):
                    continue
                receipts.append({
                    "id": f"legacy-{backup_id}",
                    "action": "repair",
                    "outcome": "success",
                    "completed_at": float(metadata.get("created_at") or path.stat().st_mtime),
                    "package": package_name,
                    "title": package_name,
                    "artist": "",
                    "rule_code": metadata.get("rule_code"),
                    "backup_id": backup_id,
                    "change_kind": "legacy",
                    "change_count": 0,
                    "removed_count": 0,
                    "musical_positions": 0,
                    "item_name": "item",
                    "player_result": (
                        "The repaired song data is still present. This repair predates detailed result receipts, so its exact item count is unavailable."
                    ),
                    "user_value": (
                        "The package passed validation at repair time, and its saved original song data can still be restored with Undo."
                    ),
                    "file_handling": self._file_handling(backup_id),
                    "legacy_receipt": True,
                })
            except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError,
                    RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile,
                    RepairPlanningError):
                continue
        return receipts

    def restore(
        self,
        package: str,
        backup_id: str,
        *,
        deep_audio: bool = False,
    ) -> dict:
        """Restore the song-data members saved before a successful repair.

        Restoration refuses to overwrite a package whose repaired members have
        changed since the backup was made.  Unrelated current package members
        are preserved.
        """
        with self._lock:
            prepared = self._prepare_restore(
                package,
                backup_id,
                deep_audio=deep_audio,
            )
            try:
                self._commit(
                    prepared["_package_path"],
                    prepared["_candidate"],
                    prepared["_originals"],
                    prepared["_current"],
                )
            finally:
                prepared["_cleanup"]()

            result = {
                **self._public_restore_plan(prepared),
                "outcome": "restored",
                "restored": True,
                "change_kind": prepared["change_kind"],
                "change_count": prepared["change_count"],
                "restored_count": prepared["removed_count"],
                "report": prepared["_after"],
                "file_handling": {
                    "package_replaced": True,
                    "duplicate_library_package_created": False,
                    "backup_retained": True,
                    "summary": (
                        "The saved original song data was restored at the same Feedpak path. "
                        "The recovery backup was kept, and no second song package was added."
                    ),
                },
            }
            result["receipt_saved"] = self._record_history({
                "id": uuid.uuid4().hex,
                "action": "restore",
                "outcome": "restored",
                "completed_at": time.time(),
                "package": prepared["package"],
                "title": prepared["title"],
                "artist": prepared["artist"],
                "rule_code": prepared["rule_code"],
                "rule_codes": prepared["rule_codes"],
                "repair_summaries": prepared["repair_summaries"],
                "backup_id": backup_id,
                "change_kind": prepared["change_kind"],
                "change_count": prepared["change_count"],
                "restored_count": prepared["removed_count"],
                "item_name": prepared["item_name"],
                "player_result": prepared["player_result"],
                "user_value": prepared["user_value"],
                "file_handling": result["file_handling"],
            })
            return result

    def preview_restore(
        self,
        package: str,
        backup_id: str,
        *,
        deep_audio: bool = False,
    ) -> dict:
        """Verify that one retained recovery backup can be restored now."""
        with self._lock:
            prepared = self._prepare_restore(
                package,
                backup_id,
                deep_audio=deep_audio,
            )
            try:
                return self._public_restore_plan(prepared)
            finally:
                prepared["_cleanup"]()

    def _prepare_restore(
        self,
        package: str,
        backup_id: str,
        *,
        deep_audio: bool,
    ) -> dict:
        """Build and validate a restorable candidate without committing it."""
        if not isinstance(backup_id, str) or not _BACKUP_ID_RE.fullmatch(backup_id):
            raise RepairPlanningError("invalid_backup", "The recovery backup is invalid.")
        _root, package_path, package_name = self._resolve_package(package)
        metadata, originals = self._read_backup(backup_id, package_name)
        current = {}
        source_members = []
        for entry in metadata["members"]:
            member_path = entry["member_path"]
            raw = self._read_member(package_path, member_path, MAX_REPAIR_TEXT_BYTES)
            current_hash = hashlib.sha256(raw).hexdigest()
            if current_hash != entry["repaired_sha256"]:
                raise RepairPlanningError(
                    "package_changed",
                    "This song changed after the repair, so Library Doctor will not overwrite it. Scan it again and review it manually.",
                )
            current[member_path] = raw
            source_members.append({
                "member_path": member_path,
                "repaired_sha256": current_hash,
                "original_sha256": entry["original_sha256"],
            })

        before = self._validate_feedpak(
            package_path, package_name, deep_audio=bool(deep_audio)
        )
        candidate, cleanup = self._candidate(package_path, originals)
        try:
            after = self._validate_feedpak(
                candidate, package_name, deep_audio=bool(deep_audio)
            )
            rule_code = metadata.get("rule_code")
            metadata_rule_codes = metadata.get("rule_codes")
            if isinstance(metadata_rule_codes, list) and all(
                isinstance(item, str) for item in metadata_rule_codes
            ):
                rule_codes = metadata_rule_codes
            elif isinstance(rule_code, str):
                rule_codes = [rule_code]
            else:
                rule_codes = [
                    definition.rule_code for definition in _REPAIR_DEFINITIONS
                ]
            introduced = _report_codes(after) - _report_codes(before)
            if introduced - set(rule_codes):
                raise RepairPlanningError(
                    "restore_verification_failed",
                    "The original song data did not pass recovery validation, so the repaired Feedpak was left unchanged.",
                )

            backup_summary = metadata.get("summary")
            if not isinstance(backup_summary, dict):
                backup_summary = {}
            combined_repair = rule_code == ALL_SAFE_RULE_CODE
            unsigned = {
                "schema": "library_doctor.restore_plan.v1",
                "package": package_name,
                "backup_id": backup_id,
                "validator_version": self._validator_version,
                "deep_audio": bool(deep_audio),
                "members": source_members,
            }
            return {
                **unsigned,
                "plan_id": _digest_json(unsigned),
                "available": True,
                "title": after.get("title") or package_name,
                "artist": after.get("artist") or "",
                "rule_code": rule_code,
                "rule_codes": rule_codes,
                "repair_summaries": backup_summary.get("repair_summaries", []),
                "change_kind": backup_summary.get(
                    "change_kind", "remove_duplicates"
                ),
                "change_count": int(
                    backup_summary.get(
                        "change_count", backup_summary.get("removed_count", 0)
                    ) or 0
                ),
                "removed_count": int(backup_summary.get("removed_count", 0) or 0),
                "member_count": len(source_members),
                "item_name": backup_summary.get("item_name", "item"),
                "player_result": (
                    "After Undo, the package contains all original song data again; the safe findings repaired together may return."
                    if combined_repair else
                    "After Undo, the package contains the original song data again; the finding that was repaired may return."
                ),
                "user_value": (
                    "This returns the entire combined repair to its exact saved starting point if the song did not behave as expected."
                    if combined_repair else
                    "This returns the song to the exact data saved before the repair if the repaired song did not behave as expected."
                ),
                "file_handling": (
                    "The saved original song-data files will replace only the repaired files at the same Feedpak path. "
                    "Other package members are preserved, the recovery backup is retained, and no duplicate song is created."
                ),
                "_package_path": package_path,
                "_candidate": candidate,
                "_cleanup": cleanup,
                "_originals": originals,
                "_current": current,
                "_after": after,
            }
        except Exception:
            cleanup()
            raise

    @staticmethod
    def _public_restore_plan(prepared: dict) -> dict:
        return {key: value for key, value in prepared.items() if not key.startswith("_")}

    def _resolve_package(self, package: str) -> tuple[Path, Path, str]:
        if not isinstance(package, str) or not package or len(package) > 4_096:
            raise RepairPlanningError("invalid_package", "The selected package is invalid.")
        if "\\" in package or "\0" in package:
            raise RepairPlanningError("invalid_package", "The selected package is invalid.")
        relative = PurePosixPath(package)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise RepairPlanningError("invalid_package", "The selected package is invalid.")
        if relative.suffix.lower() not in PACKAGE_SUFFIXES:
            raise RepairPlanningError("invalid_package", "Choose a Feedpak or Sloppak package.")

        root_value = self._get_dlc_dir()
        if not root_value:
            raise RepairPlanningError(
                "library_unavailable", "No song library folder is configured in FeedBack Settings."
            )
        try:
            root = Path(root_value).resolve(strict=True)
            lexical_candidate = root.joinpath(*relative.parts)
            current = root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise RepairPlanningError(
                        "package_unavailable",
                        "Linked packages are not changed automatically.",
                    )
            candidate = lexical_candidate.resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, ValueError) as exc:
            raise RepairPlanningError(
                "package_unavailable",
                "The selected package is unavailable or outside the configured song library.",
            ) from exc
        if not root.is_dir() or not (candidate.is_dir() or candidate.is_file()):
            raise RepairPlanningError("package_unavailable", "The selected package is unavailable.")
        return root, candidate, relative.as_posix()

    def _read_repair_manifest(self, package_path: Path) -> dict:
        manifest_raw = self._read_member(
            package_path, "manifest.yaml", MAX_REPAIR_MANIFEST_BYTES
        )
        try:
            manifest = yaml.load(
                manifest_raw.decode("utf-8"), Loader=_UniqueSafeLoader
            )
        except (UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:
            raise RepairPlanningError(
                "invalid_manifest",
                "The package manifest cannot be read safely for repair.",
            ) from exc
        if not isinstance(manifest, dict):
            raise RepairPlanningError(
                "invalid_manifest",
                "The package manifest cannot be read safely for repair.",
            )
        arrangements = manifest.get("arrangements")
        if not isinstance(arrangements, list) or len(arrangements) > MAX_DECLARED_REPAIR_MEMBERS:
            raise RepairPlanningError(
                "invalid_manifest", "The package has an invalid arrangement list."
            )
        return manifest

    @staticmethod
    def _repair_member_paths(manifest: dict, source_kind: str) -> list[str]:
        pointer_key = (
            "file" if source_kind == "arrangement"
            else "drum_tab" if source_kind == "drum_tab"
            else "lyrics" if source_kind == "lyrics"
            else None
        )
        if pointer_key is None:
            raise RepairPlanningError(
                "unsupported_repair",
                "This finding does not have a safe automatic repair.",
            )

        member_paths = []
        seen = set()
        if pointer_key == "lyrics":
            candidates = []
            if isinstance(manifest.get("lyrics"), str):
                candidates.append(manifest["lyrics"])
            lyric_tracks = manifest.get("lyric_tracks")
            if isinstance(lyric_tracks, list):
                candidates.extend(
                    track.get("file")
                    for track in lyric_tracks
                    if isinstance(track, dict) and isinstance(track.get("file"), str)
                )
            for member in candidates:
                try:
                    safe_member = _validate_member_path(member)
                except RepairPlanningError:
                    continue
                if safe_member in seen:
                    continue
                seen.add(safe_member)
                member_paths.append(safe_member)
            return member_paths
        if pointer_key == "drum_tab" and isinstance(manifest.get("drum_tab"), str):
            try:
                safe_member = _validate_member_path(manifest["drum_tab"])
            except RepairPlanningError:
                pass
            else:
                seen.add(safe_member)
                member_paths.append(safe_member)
        for entry in manifest["arrangements"]:
            member = entry.get(pointer_key) if isinstance(entry, dict) else None
            if not isinstance(member, str) or not member:
                continue
            try:
                safe_member = _validate_member_path(member)
            except RepairPlanningError:
                continue
            if safe_member in seen:
                continue
            seen.add(safe_member)
            member_paths.append(safe_member)
        return member_paths

    def _resolved_repair_member_paths(
        self,
        package_path: Path,
        manifest: dict,
        source_kind: str,
    ) -> list[str]:
        """Resolve the active files for one repair role.

        Timeline selection mirrors FeedBack and the validator: a complete
        song-wide sidecar overrides legacy arrangement-embedded data.  Without
        one, only the first usable legacy beat grid is active.  This prevents a
        repair from changing dormant copies that the game does not consume.
        """
        if source_kind != "timeline":
            return self._repair_member_paths(manifest, source_kind)

        declared = manifest.get("song_timeline")
        if isinstance(declared, str) and declared:
            try:
                member_path = _validate_member_path(declared)
            except RepairPlanningError:
                member_path = None
            if member_path is not None:
                # A commented sidecar may be active, but Library Doctor cannot
                # inspect or rewrite it losslessly. Return it so preview reports
                # the normal JSONC blocker instead of editing legacy data.
                if member_path.lower().endswith(".jsonc"):
                    return [member_path]
                try:
                    raw = self._read_member(
                        package_path, member_path, MAX_REPAIR_TEXT_BYTES
                    )
                except RepairPlanningError:
                    # An unavailable or oversized sidecar cannot override the
                    # legacy grid in FeedBack, so continue to its fallback.
                    raw = None
                if raw is not None:
                    try:
                        data = _parse_json(raw)
                        _inspect_structure(data)
                    except RepairPlanningError:
                        # The validator may be more permissive about a value
                        # (for example duplicate keys or non-finite numbers).
                        # Refuse this declared sidecar rather than risk editing
                        # a legacy grid that FeedBack could consider inactive.
                        return [member_path]
                    if (
                        isinstance(data, dict)
                        and isinstance(data.get("beats"), list)
                        and isinstance(data.get("sections"), list)
                    ):
                        return [member_path]

        # FeedBack's legacy fallback takes the first non-empty beat array from
        # arrangement order. Once found, later embedded grids are inactive.
        seen = set()
        for entry in manifest["arrangements"]:
            member = entry.get("file") if isinstance(entry, dict) else None
            if not isinstance(member, str) or not member:
                continue
            try:
                member_path = _validate_member_path(member)
            except RepairPlanningError:
                continue
            if member_path in seen:
                continue
            seen.add(member_path)
            if member_path.lower().endswith(".jsonc"):
                # We cannot safely determine whether this is the active legacy
                # grid without a comment-preserving parser/writer.
                return [member_path]
            try:
                raw = self._read_member(
                    package_path, member_path, MAX_REPAIR_TEXT_BYTES
                )
            except RepairPlanningError:
                continue
            try:
                data = _parse_json(raw)
                _inspect_structure(data)
            except RepairPlanningError:
                # Do not skip past a readable arrangement whose permissively
                # parsed contents could be FeedBack's active legacy grid.
                return [member_path]
            if (
                isinstance(data, dict)
                and isinstance(data.get("beats"), list)
                and data["beats"]
            ):
                return [member_path]
        return []

    def _plan_package(self, package_path: Path, package_name: str, rule_code: str) -> dict:
        definition = repair_for_rule(rule_code)
        if definition is None:
            raise RepairPlanningError(
                "unsupported_repair", "This finding does not have a safe automatic repair."
            )
        manifest = self._read_repair_manifest(package_path)
        member_paths = self._resolved_repair_member_paths(
            package_path, manifest, definition["source_kind"]
        )

        planned = []
        blockers = []
        for member_path in member_paths:
            try:
                raw = self._read_member(package_path, member_path, MAX_REPAIR_TEXT_BYTES)
                plan = plan_json_member(
                    raw,
                    member_path=member_path,
                    source_kind=definition["source_kind"],
                    validator_version=self._validator_version,
                    rule_code=rule_code,
                )
            except RepairPlanningError as exc:
                blockers.append({
                    "member_path": member_path,
                    "code": exc.code,
                    "message": str(exc),
                })
                continue
            if plan["actions"]:
                planned.append({"member_path": member_path, "raw": raw, "plan": plan})

        removed_count = sum(
            action["removed_count"]
            for item in planned
            for action in item["plan"]["actions"]
        )
        change_count = sum(
            action.get("change_count", action["removed_count"])
            for item in planned
            for action in item["plan"]["actions"]
        )
        arrays_affected = sum(
            action["arrays_affected"]
            for item in planned
            for action in item["plan"]["actions"]
        )
        musical_positions = sum(
            action["musical_positions"]
            for item in planned
            for action in item["plan"]["actions"]
        )
        unsigned = {
            "schema": PACKAGE_REPAIR_SCHEMA,
            "catalog_version": REPAIR_CATALOG_VERSION,
            "validator_version": self._validator_version,
            "package": package_name,
            "rule_code": rule_code,
            "member_plans": [
                {"member_path": item["member_path"], "plan_id": item["plan"]["plan_id"]}
                for item in planned
            ],
            "blockers": blockers,
        }
        return {
            **unsigned,
            "plan_id": _digest_json(unsigned),
            "available": bool(planned) and not blockers,
            "title": definition["title"],
            "description": definition["description"],
            "safety": definition["safety"],
            "player_result": definition["player_result"],
            "user_value": definition["user_value"],
            "file_handling": self._file_handling(None),
            "item_name": definition["item_name"],
            "change_kind": definition.get("change_kind", "remove_duplicates"),
            "change_count": change_count,
            "member_count": len(planned),
            "arrays_affected": arrays_affected,
            "musical_positions": musical_positions,
            "removed_count": removed_count,
            "_members": planned,
        }

    def _plan_all_package(self, package_path: Path, package_name: str) -> dict:
        """Build one ordered plan for every supported safe repair in a package."""
        manifest = self._read_repair_manifest(package_path)
        member_sources: dict[str, set[str]] = {}
        for source_kind in ("arrangement", "lyrics", "timeline", "drum_tab"):
            for member_path in self._resolved_repair_member_paths(
                package_path, manifest, source_kind
            ):
                member_sources.setdefault(member_path, set()).add(source_kind)

        planned = []
        blockers = []
        totals = {
            rule_code: {
                "change_count": 0,
                "removed_count": 0,
                "arrays_affected": 0,
                "musical_positions": 0,
                "members": set(),
            }
            for rule_code in _ALL_SAFE_RULE_ORDER
        }
        for member_path, source_kinds in member_sources.items():
            compatible_legacy_timeline = source_kinds == {"arrangement", "timeline"}
            if len(source_kinds) != 1 and not compatible_legacy_timeline:
                blockers.append({
                    "member_path": member_path,
                    "code": "ambiguous_source",
                    "message": (
                        "The same source file is declared for more than one song-data "
                        "role, so it cannot be changed automatically."
                    ),
                })
                continue
            ordered_source_kinds = [
                source_kind
                for source_kind in ("arrangement", "timeline", "lyrics", "drum_tab")
                if source_kind in source_kinds
            ]
            source_kind = ordered_source_kinds[0]
            try:
                original_raw = self._read_member(
                    package_path, member_path, MAX_REPAIR_TEXT_BYTES
                )
                candidate_raw = original_raw
                steps = []
                for rule_code in _ALL_SAFE_RULE_ORDER:
                    definition = _REPAIR_BY_RULE[rule_code]
                    if definition.source_kind not in source_kinds:
                        continue
                    plan = plan_json_member(
                        candidate_raw,
                        member_path=member_path,
                        source_kind=definition.source_kind,
                        validator_version=self._validator_version,
                        rule_code=rule_code,
                    )
                    if not plan["actions"]:
                        continue
                    candidate_raw = apply_json_member(candidate_raw, plan)
                    steps.append({"rule_code": rule_code, "plan": plan})
                    rule_total = totals[rule_code]
                    rule_total["members"].add(member_path)
                    for action in plan["actions"]:
                        rule_total["change_count"] += action.get(
                            "change_count", action["removed_count"]
                        )
                        rule_total["removed_count"] += action["removed_count"]
                        rule_total["arrays_affected"] += action["arrays_affected"]
                        rule_total["musical_positions"] += action["musical_positions"]
            except RepairPlanningError as exc:
                blockers.append({
                    "member_path": member_path,
                    "code": exc.code,
                    "message": str(exc),
                })
                continue
            if steps:
                planned.append({
                    "member_path": member_path,
                    "source_kind": source_kind,
                    "source_kinds": ordered_source_kinds,
                    "raw": original_raw,
                    "replacement": candidate_raw,
                    "steps": steps,
                })

        repair_summaries = []
        for rule_code in _ALL_SAFE_RULE_ORDER:
            rule_total = totals[rule_code]
            if not rule_total["change_count"]:
                continue
            definition = _REPAIR_BY_RULE[rule_code]
            repair_summaries.append({
                "rule_code": rule_code,
                "title": definition.title,
                "item_name": definition.item_name,
                "change_kind": definition.change_kind,
                "change_count": rule_total["change_count"],
                "removed_count": rule_total["removed_count"],
                "arrays_affected": rule_total["arrays_affected"],
                "musical_positions": rule_total["musical_positions"],
                "member_count": len(rule_total["members"]),
            })

        rule_codes = [summary["rule_code"] for summary in repair_summaries]
        unsigned = {
            "schema": PACKAGE_REPAIR_SCHEMA,
            "catalog_version": REPAIR_CATALOG_VERSION,
            "validator_version": self._validator_version,
            "package": package_name,
            "rule_code": ALL_SAFE_RULE_CODE,
            "rule_codes": rule_codes,
            "member_plans": [
                {
                    "member_path": item["member_path"],
                    "source_kind": item["source_kind"],
                    "source_kinds": item["source_kinds"],
                    "steps": [
                        {
                            "rule_code": step["rule_code"],
                            "plan_id": step["plan"]["plan_id"],
                        }
                        for step in item["steps"]
                    ],
                }
                for item in planned
            ],
            "blockers": blockers,
        }
        return {
            **unsigned,
            "plan_id": _digest_json(unsigned),
            "available": bool(planned) and not blockers,
            **_ALL_SAFE_DEFINITION,
            "item_name": "song-data item",
            "change_kind": "combined",
            "change_count": sum(
                summary["change_count"] for summary in repair_summaries
            ),
            "rule_count": len(rule_codes),
            "repair_summaries": repair_summaries,
            "member_count": len(planned),
            "arrays_affected": sum(
                summary["arrays_affected"] for summary in repair_summaries
            ),
            "musical_positions": sum(
                summary["musical_positions"] for summary in repair_summaries
            ),
            "removed_count": sum(
                summary["removed_count"] for summary in repair_summaries
            ),
            "file_handling": self._file_handling(None),
            "_members": planned,
        }

    @staticmethod
    def _public_plan(internal: dict) -> dict:
        return {key: value for key, value in internal.items() if not key.startswith("_")}

    @staticmethod
    def _file_handling(backup_id: str | None) -> dict:
        return {
            "package_replaced": True,
            "duplicate_library_package_created": False,
            "backup_created": backup_id is not None,
            "backup_id": backup_id,
            "backup_contents": "original_changed_song_data_files",
            "summary": (
                "Library Doctor builds and validates a complete candidate first. Only then does it replace "
                "the existing Feedpak at the same path. It does not add a second playable song to the library. "
                "The original changed song-data files are kept in private recovery storage."
            ),
        }

    @staticmethod
    def _read_member(package_path: Path, member_path: str, limit: int) -> bytes:
        if package_path.is_dir():
            target = package_path.joinpath(*PurePosixPath(member_path).parts)
            try:
                current = package_path
                for part in PurePosixPath(member_path).parts:
                    current = current / part
                    if current.is_symlink():
                        raise RepairPlanningError(
                            "member_unavailable",
                            "Linked song files are not changed automatically.",
                        )
                resolved = target.resolve(strict=True)
                resolved.relative_to(package_path.resolve(strict=True))
            except (OSError, ValueError) as exc:
                raise RepairPlanningError(
                    "member_unavailable", "A required song file is unavailable."
                ) from exc
            if not resolved.is_file():
                raise RepairPlanningError(
                    "member_unavailable", "A required song file is unavailable."
                )
            try:
                if resolved.stat().st_size > limit:
                    raise RepairPlanningError(
                        "source_too_large", "This song file is too large to repair safely."
                    )
                return resolved.read_bytes()
            except OSError as exc:
                raise RepairPlanningError(
                    "member_unavailable", "A required song file cannot be read."
                ) from exc

        try:
            with zipfile.ZipFile(package_path, "r") as archive:
                matches = [info for info in archive.infolist() if info.filename == member_path]
                if len(matches) != 1 or matches[0].is_dir() or matches[0].file_size > limit:
                    raise RepairPlanningError(
                        "member_unavailable", "A required song file is unavailable or too large."
                    )
                with archive.open(matches[0], "r") as stream:
                    raw = stream.read(limit + 1)
                if len(raw) > limit:
                    raise RepairPlanningError(
                        "source_too_large", "This song file is too large to repair safely."
                    )
                return raw
        except RepairPlanningError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise RepairPlanningError(
                "package_unreadable", "The package archive cannot be read safely for repair."
            ) from exc

    def _candidate(self, package_path: Path, replacements: dict[str, bytes]):
        try:
            temporary_root = Path(tempfile.mkdtemp(
                prefix=".library-doctor-repair-", dir=package_path.parent
            ))
        except OSError as exc:
            raise RepairPlanningError(
                "candidate_failed", "A repaired package candidate could not be created."
            ) from exc
        if package_path.is_dir():
            candidate = temporary_root / package_path.name

            def link_or_copy(source, destination):
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copy2(source, destination)
                return destination

            try:
                shutil.copytree(package_path, candidate, copy_function=link_or_copy, symlinks=True)
                for member_path, raw in replacements.items():
                    target = candidate.joinpath(*PurePosixPath(member_path).parts)
                    self._atomic_write(target, raw)
            except (OSError, shutil.Error) as exc:
                shutil.rmtree(temporary_root, ignore_errors=True)
                raise RepairPlanningError(
                    "candidate_failed", "A repaired package candidate could not be created."
                ) from exc
        else:
            candidate = temporary_root / package_path.name
            try:
                with zipfile.ZipFile(package_path, "r") as source:
                    infos = source.infolist()
                    names = [info.filename for info in infos]
                    if len(names) != len(set(names)):
                        raise RepairPlanningError(
                            "ambiguous_archive", "The package contains duplicate archive paths."
                        )
                    with zipfile.ZipFile(candidate, "w", allowZip64=True) as target:
                        target.comment = source.comment
                        for info in infos:
                            if info.filename in replacements:
                                target.writestr(info, replacements[info.filename])
                            elif info.is_dir():
                                target.writestr(info, b"")
                            else:
                                with source.open(info, "r") as input_stream:
                                    with target.open(info, "w", force_zip64=True) as output_stream:
                                        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                shutil.copystat(package_path, candidate)
                self._verify_archive_candidate(package_path, candidate, set(replacements))
            except RepairPlanningError:
                shutil.rmtree(temporary_root, ignore_errors=True)
                raise
            except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                shutil.rmtree(temporary_root, ignore_errors=True)
                raise RepairPlanningError(
                    "candidate_failed", "A repaired package candidate could not be created."
                ) from exc

        def cleanup():
            shutil.rmtree(temporary_root, ignore_errors=True)

        return candidate, cleanup

    @staticmethod
    def _verify_archive_candidate(
        source_path: Path,
        candidate_path: Path,
        changed_members: set[str],
    ) -> None:
        """Read every candidate member and verify unchanged archive payloads.

        A normal package validation does not necessarily open every audio and
        image asset. ZIP CRC verification plus unchanged size/CRC comparison
        ensures the archive rewrite did not silently damage unrelated assets.
        """
        try:
            with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(
                candidate_path, "r"
            ) as candidate:
                source_infos = source.infolist()
                candidate_infos = candidate.infolist()
                if [item.filename for item in source_infos] != [
                    item.filename for item in candidate_infos
                ]:
                    raise RepairPlanningError(
                        "candidate_integrity_failed",
                        "The repaired candidate did not preserve every package member, so it was not saved.",
                    )
                for original, rebuilt in zip(source_infos, candidate_infos):
                    if original.filename in changed_members:
                        continue
                    if (original.file_size, original.CRC) != (rebuilt.file_size, rebuilt.CRC):
                        raise RepairPlanningError(
                            "candidate_integrity_failed",
                            "An unchanged package asset did not survive the candidate rebuild, so it was not saved.",
                        )
                bad_member = candidate.testzip()
                if bad_member is not None:
                    raise RepairPlanningError(
                        "candidate_integrity_failed",
                        "The repaired candidate failed its full archive integrity check, so it was not saved.",
                    )
        except RepairPlanningError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise RepairPlanningError(
                "candidate_integrity_failed",
                "The repaired candidate could not pass its full archive integrity check, so it was not saved.",
            ) from exc

    @staticmethod
    def _verify_validation(
        before: dict, after: dict, rule_codes: str | list[str]
    ) -> None:
        before_codes = _report_codes(before)
        after_codes = _report_codes(after)
        requested = {rule_codes} if isinstance(rule_codes, str) else set(rule_codes)
        if not requested or not (requested & before_codes):
            raise RepairPlanningError(
                "nothing_to_repair",
                "The selected safe issues are no longer present in this package.",
            )
        if requested & after_codes:
            raise RepairPlanningError(
                "verification_failed",
                "The repaired candidate still contains a selected safe issue.",
            )
        introduced = sorted(after_codes - before_codes)
        if introduced:
            raise RepairPlanningError(
                "verification_failed",
                "The repaired candidate introduced a new validation finding and was not saved.",
            )

    def _create_backup(
        self,
        package_name: str,
        package_path: Path,
        originals: dict[str, bytes],
        replacements: dict[str, bytes],
        plan_id: str,
        rule_code: str,
        plan: dict,
    ) -> str:
        backup_dir = self._config_dir / "library_doctor" / "repair_backups"
        backup_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:12]}"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(
                prefix=f".{backup_id}-", suffix=".tmp", dir=backup_dir
            )
            os.close(handle)
        except OSError as exc:
            raise RepairPlanningError(
                "backup_failed", "A recovery backup could not be created, so nothing was changed."
            ) from exc
        temporary = Path(temporary_name)
        destination = backup_dir / f"{backup_id}.zip"
        metadata = {
            "schema": BACKUP_SCHEMA,
            "backup_id": backup_id,
            "created_at": time.time(),
            "package": package_name,
            "package_kind": "directory" if package_path.is_dir() else "archive",
            "plan_id": plan_id,
            "rule_code": rule_code,
            "rule_codes": plan.get("rule_codes", [rule_code]),
            "summary": {
                key: plan.get(key)
                for key in (
                    "title", "item_name", "change_kind", "change_count",
                    "removed_count", "musical_positions",
                    "arrays_affected", "member_count", "rule_count",
                    "repair_summaries", "player_result", "user_value",
                )
            },
            "members": [],
        }
        for index, (member_path, raw) in enumerate(originals.items()):
            metadata["members"].append({
                "member_path": member_path,
                "backup_entry": f"original/{index}.bin",
                "original_sha256": hashlib.sha256(raw).hexdigest(),
                "repaired_sha256": hashlib.sha256(replacements[member_path]).hexdigest(),
            })
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "repair.json",
                    json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                for entry, raw in zip(metadata["members"], originals.values()):
                    archive.writestr(entry["backup_entry"], raw)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except (OSError, RuntimeError, zipfile.LargeZipFile) as exc:
            temporary.unlink(missing_ok=True)
            raise RepairPlanningError(
                "backup_failed", "A recovery backup could not be created, so nothing was changed."
            ) from exc
        return backup_id

    def _read_backup(self, backup_id: str, package_name: str) -> tuple[dict, dict[str, bytes]]:
        backup_dir = self._config_dir / "library_doctor" / "repair_backups"
        destination = backup_dir / f"{backup_id}.zip"
        try:
            destination.resolve(strict=True).relative_to(backup_dir.resolve(strict=True))
            with zipfile.ZipFile(destination, "r") as archive:
                infos = archive.infolist()
                names = [item.filename for item in infos]
                if (
                    len(names) != len(set(names))
                    or len(infos) > MAX_DECLARED_REPAIR_MEMBERS + 1
                    or sum(item.file_size for item in infos) > MAX_RECOVERY_BACKUP_BYTES
                ):
                    raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                if archive.testzip() is not None:
                    raise RepairPlanningError(
                        "backup_unreadable", "The recovery backup failed its integrity check."
                    )
                info = archive.getinfo("repair.json")
                if info.file_size > MAX_REPAIR_MANIFEST_BYTES:
                    raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                metadata = json.loads(archive.read(info).decode("utf-8"))
                if not isinstance(metadata, dict) or metadata.get("schema") not in {
                    "library_doctor.repair_backup.v1",
                    BACKUP_SCHEMA,
                    *self._legacy_backup_schemas,
                }:
                    raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                if metadata.get("backup_id") != backup_id or metadata.get("package") != package_name:
                    raise RepairPlanningError(
                        "backup_mismatch", "This recovery backup belongs to a different package."
                    )
                members = metadata.get("members")
                if not isinstance(members, list) or not members or len(members) > MAX_DECLARED_REPAIR_MEMBERS:
                    raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                originals = {}
                for entry in members:
                    if not isinstance(entry, dict):
                        raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                    member_path = _validate_member_path(entry.get("member_path"))
                    backup_entry = _validate_member_path(entry.get("backup_entry"))
                    original_hash = entry.get("original_sha256")
                    repaired_hash = entry.get("repaired_sha256")
                    if (
                        not isinstance(original_hash, str)
                        or not isinstance(repaired_hash, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", original_hash)
                        or not re.fullmatch(r"[0-9a-f]{64}", repaired_hash)
                        or member_path in originals
                        or not backup_entry.startswith("original/")
                    ):
                        raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                    member_info = archive.getinfo(backup_entry)
                    if member_info.is_dir() or member_info.file_size > MAX_REPAIR_TEXT_BYTES:
                        raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                    raw = archive.read(member_info)
                    if hashlib.sha256(raw).hexdigest() != original_hash:
                        raise RepairPlanningError(
                            "backup_unreadable", "The recovery backup failed its integrity check."
                        )
                    originals[member_path] = raw
                return metadata, originals
        except RepairPlanningError:
            raise
        except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError,
                RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise RepairPlanningError(
                "backup_unavailable", "The recovery backup cannot be read. The Feedpak was left unchanged."
            ) from exc

    @property
    def _history_path(self) -> Path:
        return self._config_dir / "library_doctor" / "repair_history.json"

    def _read_history(self) -> list[dict]:
        try:
            raw = self._history_path.read_bytes()
            if len(raw) > MAX_REPAIR_MANIFEST_BYTES:
                return []
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("schema") not in {
                HISTORY_SCHEMA,
                *self._legacy_history_schemas,
            } or not isinstance(payload.get("items"), list):
                return []
            return [item for item in payload["items"] if isinstance(item, dict)][-MAX_REPAIR_HISTORY:]
        except (OSError, AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return []

    def _record_history(self, item: dict) -> bool:
        history = self._read_history()
        history.append(item)
        return self._write_history(history)

    def _write_history(self, history: list[dict]) -> bool:
        payload = {
            "schema": HISTORY_SCHEMA,
            "items": history[-MAX_REPAIR_HISTORY:],
        }
        path = self._history_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            return True
        except OSError as exc:
            # The Feedpak repair has already succeeded. A missing UI receipt
            # must never turn a successful package commit into a reported
            # failure or trigger a second repair attempt.
            self._log.warning("Library Doctor could not save repair history: %s", exc)
            return False

    def _commit(
        self,
        package_path: Path,
        candidate: Path,
        replacements: dict[str, bytes],
        originals: dict[str, bytes],
    ) -> None:
        if package_path.is_file():
            try:
                with candidate.open("r+b") as stream:
                    os.fsync(stream.fileno())
                os.replace(candidate, package_path)
            except OSError as exc:
                raise RepairPlanningError(
                    "save_failed", "The repaired package could not replace the original."
                ) from exc
            return

        committed = []
        try:
            for member_path, raw in replacements.items():
                target = package_path.joinpath(*PurePosixPath(member_path).parts)
                self._atomic_write(target, raw)
                committed.append(member_path)
        except (OSError, RepairPlanningError) as exc:
            rollback_failed = False
            for member_path in reversed(committed):
                try:
                    target = package_path.joinpath(*PurePosixPath(member_path).parts)
                    self._atomic_write(target, originals[member_path])
                except Exception:
                    rollback_failed = True
                    self._log.error(
                        "Library Doctor could not roll back %s in %s",
                        member_path,
                        package_path.name,
                    )
            if isinstance(exc, RepairPlanningError) and not rollback_failed:
                raise
            raise RepairPlanningError(
                "save_failed",
                (
                    "The repaired song files could not be saved and automatic rollback was incomplete. "
                    "Do not use this package until it has been restored from the recovery backup."
                    if rollback_failed else
                    "The repaired song files could not be saved. The original song-data files were restored."
                ),
                file_state="recovery_required" if rollback_failed else "unchanged",
            ) from exc

    @staticmethod
    def _atomic_write(path: Path, raw: bytes) -> None:
        handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                shutil.copystat(path, temporary)
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def plan_json_member(
    raw: bytes,
    *,
    member_path: str,
    source_kind: str,
    validator_version: str,
    rule_code: str | None = None,
) -> dict:
    """Create a deterministic, read-only repair preview for one JSON member.

    A plan is bound to the exact source bytes and validator version.  It never
    trusts finding locations or cached array indexes.  JSONC is refused until a
    comment-preserving edit path exists.
    """
    safe_member_path = _validate_member_path(member_path)
    default_definition = _DEFAULT_REPAIR_BY_SOURCE.get(source_kind)
    if default_definition is None:
        raise RepairPlanningError(
            "unsupported_source_kind",
            "This type of song file does not have an automatic repair planner.",
        )
    definition = (
        _REPAIR_BY_RULE.get(rule_code)
        if rule_code is not None
        else default_definition
    )
    if definition is None or definition.source_kind != source_kind:
        raise RepairPlanningError(
            "unsupported_repair",
            "This finding does not have a safe automatic repair.",
        )
    if safe_member_path.lower().endswith(".jsonc"):
        raise RepairPlanningError(
            "jsonc_requires_lossless_writer",
            "Commented JSON cannot be repaired until comments can be preserved.",
        )
    if not safe_member_path.lower().endswith(".json"):
        raise RepairPlanningError(
            "unsupported_text_format",
            "Automatic song-data repairs currently require an ordinary JSON file.",
        )
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_REPAIR_TEXT_BYTES:
        raise RepairPlanningError(
            "source_too_large",
            "This song file is too large to plan a repair safely.",
        )
    if not isinstance(validator_version, str) or not validator_version.strip():
        raise ValueError("validator_version must be a non-empty string")

    document = _parse_json(raw)
    _inspect_structure(document)
    expected_shape = list if source_kind == "lyrics" else dict
    if not isinstance(document, expected_shape):
        raise RepairPlanningError(
            "invalid_document_shape",
            "The song file does not have the expected JSON structure for this repair.",
        )

    if definition.rule_code == "chart.duplicate-note":
        operations = _plan_exact_note_duplicates(document)
    elif definition.rule_code == "chart.duplicate-chord-note":
        operations = _plan_exact_chord_note_duplicates(document)
    elif definition.rule_code == "chart.duplicate-chord":
        operations = _plan_exact_arrangement_array_duplicates(
            document, "chords", _valid_chord_identity
        )
    elif definition.rule_code == "chart.duplicate-anchor":
        operations = _plan_exact_arrangement_array_duplicates(
            document, "anchors", _valid_anchor_identity
        )
    elif definition.rule_code == "chart.duplicate-handshape":
        operations = _plan_exact_arrangement_array_duplicates(
            document, "handshapes", _valid_handshape_identity
        )
    elif definition.rule_code == "chart.note-duplicates-chord":
        operations = _plan_exact_note_chord_duplicates(document)
    elif definition.rule_code == "chart.bend-points-out-of-order":
        operations = _plan_bend_point_order(document)
    elif definition.rule_code == "lyrics.out-of-order":
        operations = _plan_lyric_cue_order(document)
    elif definition.rule_code == "timeline.duplicate-beat":
        operations = _plan_exact_beat_duplicates(document)
    elif definition.rule_code == "drums.duplicate-hit":
        operations = _plan_exact_drum_duplicates(document)
    else:  # The explicit catalog dispatch above should make this unreachable.
        raise RepairPlanningError(
            "unsupported_repair",
            "This finding does not have a safe automatic repair.",
        )

    source_sha256 = hashlib.sha256(raw).hexdigest()
    actions = []
    if operations:
        removed_count = sum(len(operation.remove_indices) for operation in operations)
        change_count = (
            len(operations)
            if definition.change_kind == "reorder"
            else removed_count
        )
        arrays_affected = len(operations)
        musical_positions = _musical_position_count(
            document, operations, definition.rule_code
        )
        action_payload = {
            "rule_code": definition.rule_code,
            "action_kind": definition.action_kind,
            "change_kind": definition.change_kind,
            "safety": definition.safety,
            "title": definition.title,
            "summary": _summary(
                change_count,
                arrays_affected,
                definition.item_name,
                definition.change_kind,
            ),
            "change_count": change_count,
            "removed_count": removed_count,
            "arrays_affected": arrays_affected,
            "musical_positions": musical_positions,
            "operations": [operation.to_dict() for operation in operations],
        }
        action_payload["action_id"] = _digest_json({
            "source_sha256": source_sha256,
            **action_payload,
        })
        actions.append(action_payload)

    unsigned_plan = {
        "schema": REPAIR_PLAN_SCHEMA,
        "catalog_version": REPAIR_CATALOG_VERSION,
        "validator_version": validator_version,
        "source": {
            "member_path": safe_member_path,
            "source_kind": source_kind,
            "sha256": source_sha256,
            "byte_count": len(raw),
        },
        "actions": actions,
    }
    return {
        **unsigned_plan,
        "plan_id": _digest_json(unsigned_plan),
    }


def _validate_member_path(member_path: str) -> str:
    if not isinstance(member_path, str) or not member_path:
        raise RepairPlanningError("invalid_member_path", "The song file path is invalid.")
    if "\\" in member_path:
        raise RepairPlanningError("invalid_member_path", "The song file path is invalid.")
    path = PurePosixPath(member_path)
    if path.is_absolute() or path.as_posix() != member_path or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise RepairPlanningError("invalid_member_path", "The song file path is invalid.")
    return path.as_posix()


def _parse_json(raw: bytes):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepairPlanningError(
            "invalid_utf8", "The song file is not valid UTF-8."
        ) from exc

    def reject_constant(value: str):
        raise ValueError(f"Non-finite JSON number: {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RepairPlanningError(
                    "duplicate_json_key",
                    "The song file repeats a JSON property and cannot be repaired safely.",
                )
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except RepairPlanningError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
        raise RepairPlanningError(
            "invalid_json", "The song file is not valid JSON."
        ) from exc


def _inspect_structure(document) -> None:
    count = 0
    stack = [document]
    seen_containers: set[int] = set()
    while stack:
        value = stack.pop()
        count += 1
        if count > MAX_REPAIR_STRUCTURE_ITEMS:
            raise RepairPlanningError(
                "source_too_complex",
                "This song file is too complex to plan a repair safely.",
            )
        if not isinstance(value, (dict, list)):
            continue
        identity = id(value)
        if identity in seen_containers:
            continue
        seen_containers.add(identity)
        stack.extend(value.values() if isinstance(value, dict) else value)


def _plan_exact_note_duplicates(document: dict) -> list[DeleteArrayItems]:
    operations = []
    for path, notes in _note_arrays(document):
        operation = _duplicate_operation(path, notes, _valid_note_identity)
        if operation is not None:
            operations.append(operation)
    return operations


def _plan_exact_chord_note_duplicates(document: dict) -> list[DeleteArrayItems]:
    operations = []
    for chord_path, chords in _arrangement_arrays(document, "chords"):
        for chord_index, chord in enumerate(chords):
            if not isinstance(chord, dict) or not _finite_number(chord.get("t")):
                continue
            chord_notes = chord.get("notes")
            if not isinstance(chord_notes, list):
                continue
            operation = _duplicate_operation(
                chord_path + (chord_index, "notes"),
                chord_notes,
                _valid_chord_note_identity,
            )
            if operation is not None:
                operations.append(operation)
    return operations


def _plan_exact_arrangement_array_duplicates(
    document: dict,
    field: str,
    identity_factory,
) -> list[DeleteArrayItems]:
    operations = []
    for path, values in _arrangement_arrays(document, field):
        operation = _duplicate_operation(path, values, identity_factory)
        if operation is not None:
            operations.append(operation)
    return operations


def _plan_exact_note_chord_duplicates(
    document: dict,
) -> list[DeleteNotesMatchingChords]:
    operations = []
    for note_path, notes, chord_path, chords in _note_chord_arrays(document):
        chord_matches: dict[bytes, list[tuple[int, int, str]]] = {}
        for chord_index, chord in enumerate(chords):
            if not isinstance(chord, dict):
                continue
            chord_notes = chord.get("notes")
            if not isinstance(chord_notes, list):
                continue
            chord_digest = hashlib.sha256(_canonical_json(chord)).hexdigest()
            for chord_note_index, chord_note in enumerate(chord_notes):
                identity = _explicit_chord_note_identity(chord, chord_note)
                if identity is None:
                    continue
                chord_matches.setdefault(identity, []).append((
                    chord_index,
                    chord_note_index,
                    chord_digest,
                ))

        standalone_matches: dict[bytes, list[int]] = {}
        for note_index, note in enumerate(notes):
            identity = _valid_note_identity(note)
            if identity is not None:
                standalone_matches.setdefault(identity, []).append(note_index)

        match_groups = []
        for identity, remove_indices in standalone_matches.items():
            matching_chords = chord_matches.get(identity, [])
            # More than one matching chord member is ambiguous: there is no
            # uniquely identified chord to preserve, so leave it for review.
            if len(matching_chords) != 1:
                continue
            chord_index, chord_note_index, chord_digest = matching_chords[0]
            match_groups.append(ChordMatchGroup(
                chord_index=chord_index,
                chord_note_index=chord_note_index,
                chord_sha256=chord_digest,
                remove_indices=tuple(reversed(remove_indices)),
                entry_sha256=hashlib.sha256(identity).hexdigest(),
            ))
        if match_groups:
            operations.append(DeleteNotesMatchingChords(
                note_array_path=note_path,
                chord_array_path=chord_path,
                expected_note_length=len(notes),
                expected_chord_length=len(chords),
                match_groups=tuple(match_groups),
            ))
    return operations


def _plan_bend_point_order(document: dict) -> list[StableSortBendPoints]:
    operations = []
    for path, bend_points, note_time, string in _bend_point_arrays(document):
        parsed = []
        all_points_valid = True
        for point in bend_points:
            valid = (
                isinstance(point, dict)
                and _finite_number(point.get("t"))
                and _finite_number(point.get("v"))
            )
            if not valid:
                all_points_valid = False
                continue
            parsed.append((point["t"], point["v"]))
        out_of_order = any(
            current[0] < previous[0]
            for previous, current in zip(parsed, parsed[1:])
        )
        if not out_of_order:
            continue
        if not all_points_valid:
            raise RepairPlanningError(
                "invalid_bend_curve",
                "An out-of-order bend curve also contains an invalid point, so "
                "Library Doctor will not guess how to reorder it.",
            )
        sorted_indices = tuple(sorted(
            range(len(bend_points)), key=lambda index: bend_points[index]["t"]
        ))
        moved_count = sum(
            index != original_index
            for index, original_index in enumerate(sorted_indices)
        )
        if not moved_count:
            continue
        sorted_points = [bend_points[index] for index in sorted_indices]
        operations.append(StableSortBendPoints(
            array_path=path,
            expected_length=len(bend_points),
            original_sha256=hashlib.sha256(
                _canonical_json(bend_points)
            ).hexdigest(),
            sorted_sha256=hashlib.sha256(
                _canonical_json(sorted_points)
            ).hexdigest(),
            sorted_indices=sorted_indices,
            moved_count=moved_count,
            note_time=note_time,
            string=string,
        ))
    return operations


def _plan_lyric_cue_order(document: list) -> list[StableSortLyricCues]:
    parsed_times = [
        cue.get("t")
        for cue in document
        if isinstance(cue, dict) and _finite_number(cue.get("t"))
    ]
    if not any(
        current < previous
        for previous, current in zip(parsed_times, parsed_times[1:])
    ):
        return []

    if not all(
        isinstance(cue, dict)
        and _finite_number(cue.get("t"))
        and _finite_number(cue.get("d"))
        and isinstance(cue.get("w"), str)
        for cue in document
    ):
        raise RepairPlanningError(
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
    return [StableSortLyricCues(
        expected_length=len(document),
        original_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
        sorted_sha256=hashlib.sha256(_canonical_json(sorted_cues)).hexdigest(),
        sorted_indices=sorted_indices,
        moved_count=moved_count,
    )]


def _note_arrays(document: dict) -> Iterator[tuple[tuple[str | int, ...], list]]:
    yield from _arrangement_arrays(document, "notes")


def _bend_point_arrays(
    document: dict,
) -> Iterator[tuple[tuple[str | int, ...], list, float | None, int | None]]:
    for note_path, notes in _arrangement_arrays(document, "notes"):
        for note_index, note in enumerate(notes):
            if not isinstance(note, dict) or not isinstance(note.get("bnv"), list):
                continue
            note_time = note.get("t") if _finite_number(note.get("t")) else None
            string = note.get("s") if _integer(note.get("s")) else None
            yield note_path + (note_index, "bnv"), note["bnv"], note_time, string

    for chord_path, chords in _arrangement_arrays(document, "chords"):
        for chord_index, chord in enumerate(chords):
            if not isinstance(chord, dict):
                continue
            chord_time = chord.get("t") if _finite_number(chord.get("t")) else None
            chord_notes = chord.get("notes")
            if not isinstance(chord_notes, list):
                continue
            for note_index, note in enumerate(chord_notes):
                if not isinstance(note, dict) or not isinstance(note.get("bnv"), list):
                    continue
                string = note.get("s") if _integer(note.get("s")) else None
                yield (
                    chord_path + (chord_index, "notes", note_index, "bnv"),
                    note["bnv"],
                    chord_time,
                    string,
                )


def _arrangement_arrays(
    document: dict,
    field: str,
) -> Iterator[tuple[tuple[str | int, ...], list]]:
    values = document.get(field)
    if isinstance(values, list):
        yield (field,), values

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
            if not isinstance(level, dict):
                continue
            level_values = level.get(field)
            if isinstance(level_values, list):
                yield (
                    "phrases", phrase_index, "levels", level_index, field
                ), level_values


def _note_chord_arrays(
    document: dict,
) -> Iterator[tuple[tuple[str | int, ...], list, tuple[str | int, ...], list]]:
    notes = document.get("notes")
    chords = document.get("chords")
    if isinstance(notes, list) and isinstance(chords, list):
        yield ("notes",), notes, ("chords",), chords

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
            if not isinstance(level, dict):
                continue
            level_notes = level.get("notes")
            level_chords = level.get("chords")
            if not isinstance(level_notes, list) or not isinstance(level_chords, list):
                continue
            parent = ("phrases", phrase_index, "levels", level_index)
            yield parent + ("notes",), level_notes, parent + ("chords",), level_chords


def _plan_exact_drum_duplicates(document: dict) -> list[DeleteArrayItems]:
    hits = document.get("hits")
    if not isinstance(hits, list):
        return []
    operation = _duplicate_operation(("hits",), hits, _valid_drum_identity)
    return [operation] if operation is not None else []


def _plan_exact_beat_duplicates(document: dict) -> list[DeleteArrayItems]:
    beats = document.get("beats")
    if not isinstance(beats, list):
        return []
    operation = _duplicate_operation(("beats",), beats, _valid_beat_identity)
    return [operation] if operation is not None else []


def _duplicate_operation(
    path: tuple[str | int, ...],
    values: list,
    identity_factory,
) -> DeleteArrayItems | None:
    groups: dict[bytes, list[int]] = {}
    for index, value in enumerate(values):
        identity = identity_factory(value)
        if identity is not None:
            groups.setdefault(identity, []).append(index)

    duplicate_groups = []
    for identity, indices in groups.items():
        if len(indices) < 2:
            continue
        duplicate_groups.append(DuplicateGroup(
            keep_index=indices[0],
            remove_indices=tuple(reversed(indices[1:])),
            entry_sha256=hashlib.sha256(identity).hexdigest(),
        ))
    if not duplicate_groups:
        return None
    return DeleteArrayItems(
        array_path=path,
        expected_length=len(values),
        duplicate_groups=tuple(duplicate_groups),
    )


def _valid_note_identity(value) -> bytes | None:
    if not isinstance(value, dict):
        return None
    if not _finite_number(value.get("t")):
        return None
    string = value.get("s")
    fret = value.get("f")
    if not _integer(string) or string < 0:
        return None
    if not _integer(fret):
        return None
    return _canonical_json(value)


def _valid_chord_note_identity(value) -> bytes | None:
    if not isinstance(value, dict):
        return None
    string = value.get("s")
    fret = value.get("f")
    if not _integer(string) or string < 0 or not _integer(fret):
        return None
    return _canonical_json(value)


def _valid_timed_identity(value, time_key: str) -> bytes | None:
    if not isinstance(value, dict) or not _finite_number(value.get(time_key)):
        return None
    return _canonical_json(value)


def _valid_chord_identity(value) -> bytes | None:
    return _valid_timed_identity(value, "t")


def _valid_anchor_identity(value) -> bytes | None:
    return _valid_timed_identity(value, "time")


def _valid_handshape_identity(value) -> bytes | None:
    return _valid_timed_identity(value, "start_time")


def _explicit_chord_note_identity(chord, chord_note) -> bytes | None:
    if (
        not isinstance(chord, dict)
        or not isinstance(chord_note, dict)
        or not _finite_number(chord.get("t"))
        or "t" in chord_note
    ):
        return None
    expanded = {"t": chord["t"], **chord_note}
    return _valid_note_identity(expanded)


def _valid_drum_identity(value) -> bytes | None:
    if not isinstance(value, dict) or not _finite_number(value.get("t")):
        return None
    piece = value.get("p")
    if not isinstance(piece, str) or not piece:
        return None
    return _canonical_json(value)


def _valid_beat_identity(value) -> bytes | None:
    if (
        not isinstance(value, dict)
        or not _finite_number(value.get("time"))
        or not _integer(value.get("measure"))
    ):
        return None
    return _canonical_json(value)


def _apply_operation(
    document,
    operation: dict,
    removed: set[tuple[tuple[str | int, ...], int]],
    reordered: set[tuple[str | int, ...]],
) -> None:
    if not isinstance(operation, dict):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    if operation.get("operation") == "stable_sort_lyric_cues":
        _apply_lyric_cue_sort_operation(document, operation, reordered)
        return
    if operation.get("operation") == "stable_sort_bend_points":
        _apply_bend_point_sort_operation(document, operation, reordered)
        return
    if operation.get("operation") == "delete_notes_matching_chords":
        _apply_note_chord_delete_operation(document, operation, removed)
        return
    if operation.get("operation") != "delete_array_items":
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    raw_path = operation.get("array_path")
    if not isinstance(raw_path, list) or not raw_path:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    path = tuple(raw_path)
    values = _value_at_path(document, path)
    expected_length = operation.get("expected_length")
    if not isinstance(values, list) or expected_length != len(values):
        raise RepairPlanningError(
            "source_changed",
            "The song changed after this preview. Review the safe fix again before applying it.",
        )

    indexes = []
    for group in operation.get("duplicate_groups", []):
        if not isinstance(group, dict):
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        keep_index = group.get("keep_index")
        remove_indices = group.get("remove_indices")
        digest = group.get("entry_sha256")
        if (
            not _integer(keep_index)
            or not isinstance(remove_indices, list)
            or not remove_indices
            or not isinstance(digest, str)
        ):
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        group_indexes = [keep_index, *remove_indices]
        if any(not _integer(index) or index < 0 or index >= len(values) for index in group_indexes):
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        for index in group_indexes:
            if hashlib.sha256(_canonical_json(values[index])).hexdigest() != digest:
                raise RepairPlanningError("source_changed", "The song changed after this preview.")
        for index in remove_indices:
            marker = (path, index)
            if marker in removed:
                raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
            removed.add(marker)
            indexes.append(index)

    declared_indexes = operation.get("remove_indices")
    if (
        not isinstance(declared_indexes, list)
        or declared_indexes != sorted(indexes, reverse=True)
        or len(declared_indexes) != len(set(declared_indexes))
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    for index in declared_indexes:
        del values[index]


def _apply_bend_point_sort_operation(
    document: dict,
    operation: dict,
    reordered: set[tuple[str | int, ...]],
) -> None:
    raw_path = operation.get("array_path")
    if not isinstance(raw_path, list) or len(raw_path) < 2 or raw_path[-1] != "bnv":
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    path = tuple(raw_path)
    if path in reordered:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    values = _value_at_path(document, path)
    if (
        not isinstance(values, list)
        or operation.get("expected_length") != len(values)
        or len(values) < 2
    ):
        raise RepairPlanningError(
            "source_changed",
            "The song changed after this preview. Review the safe fix again before applying it.",
        )
    if not all(
        isinstance(point, dict)
        and _finite_number(point.get("t"))
        and _finite_number(point.get("v"))
        for point in values
    ):
        raise RepairPlanningError(
            "source_changed",
            "The bend curve changed after this preview. Review the safe fix again before applying it.",
        )
    original_digest = hashlib.sha256(_canonical_json(values)).hexdigest()
    if operation.get("original_sha256") != original_digest:
        raise RepairPlanningError(
            "source_changed",
            "The bend curve changed after this preview. Review the safe fix again before applying it.",
        )
    sorted_indices = list(sorted(
        range(len(values)), key=lambda index: values[index]["t"]
    ))
    declared_indices = operation.get("sorted_indices")
    moved_count = sum(
        index != original_index
        for index, original_index in enumerate(sorted_indices)
    )
    if (
        not moved_count
        or not isinstance(declared_indices, list)
        or any(not _integer(index) for index in declared_indices)
        or declared_indices != sorted_indices
        or not _integer(operation.get("moved_count"))
        or operation["moved_count"] != moved_count
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    sorted_values = [values[index] for index in sorted_indices]
    sorted_digest = hashlib.sha256(_canonical_json(sorted_values)).hexdigest()
    if operation.get("sorted_sha256") != sorted_digest:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    values[:] = sorted_values
    reordered.add(path)


def _apply_lyric_cue_sort_operation(
    document,
    operation: dict,
    reordered: set[tuple[str | int, ...]],
) -> None:
    if operation.get("array_path") != [] or () in reordered:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    if (
        not isinstance(document, list)
        or operation.get("expected_length") != len(document)
        or len(document) < 2
    ):
        raise RepairPlanningError(
            "source_changed",
            "The lyrics changed after this preview. Review the safe fix again before applying it.",
        )
    if not all(
        isinstance(cue, dict)
        and _finite_number(cue.get("t"))
        and _finite_number(cue.get("d"))
        and isinstance(cue.get("w"), str)
        for cue in document
    ):
        raise RepairPlanningError(
            "source_changed",
            "The lyrics changed after this preview. Review the safe fix again before applying it.",
        )
    original_digest = hashlib.sha256(_canonical_json(document)).hexdigest()
    if operation.get("original_sha256") != original_digest:
        raise RepairPlanningError(
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
        or any(not _integer(index) for index in declared_indices)
        or declared_indices != sorted_indices
        or not _integer(operation.get("moved_count"))
        or operation["moved_count"] != moved_count
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    sorted_cues = [document[index] for index in sorted_indices]
    sorted_digest = hashlib.sha256(_canonical_json(sorted_cues)).hexdigest()
    if operation.get("sorted_sha256") != sorted_digest:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    document[:] = sorted_cues
    reordered.add(())


def _apply_note_chord_delete_operation(
    document: dict,
    operation: dict,
    removed: set[tuple[tuple[str | int, ...], int]],
) -> None:
    raw_note_path = operation.get("note_array_path")
    raw_chord_path = operation.get("chord_array_path")
    if (
        not isinstance(raw_note_path, list)
        or not raw_note_path
        or not isinstance(raw_chord_path, list)
        or not raw_chord_path
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    note_path = tuple(raw_note_path)
    chord_path = tuple(raw_chord_path)
    if (
        note_path[-1] != "notes"
        or chord_path != note_path[:-1] + ("chords",)
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    notes = _value_at_path(document, note_path)
    chords = _value_at_path(document, chord_path)
    if (
        not isinstance(notes, list)
        or not isinstance(chords, list)
        or operation.get("expected_note_length") != len(notes)
        or operation.get("expected_chord_length") != len(chords)
    ):
        raise RepairPlanningError(
            "source_changed",
            "The song changed after this preview. Review the safe fix again before applying it.",
        )

    indexes = []
    match_groups = operation.get("match_groups")
    if not isinstance(match_groups, list) or not match_groups:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    for group in match_groups:
        if not isinstance(group, dict):
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        chord_index = group.get("chord_index")
        chord_note_index = group.get("chord_note_index")
        chord_digest = group.get("chord_sha256")
        remove_indices = group.get("remove_indices")
        entry_digest = group.get("entry_sha256")
        if (
            not _integer(chord_index)
            or not _integer(chord_note_index)
            or not isinstance(chord_digest, str)
            or not isinstance(remove_indices, list)
            or not remove_indices
            or not isinstance(entry_digest, str)
            or chord_index < 0
            or chord_index >= len(chords)
        ):
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        chord = chords[chord_index]
        chord_notes = chord.get("notes") if isinstance(chord, dict) else None
        if (
            not isinstance(chord_notes, list)
            or chord_note_index < 0
            or chord_note_index >= len(chord_notes)
        ):
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        if hashlib.sha256(_canonical_json(chord)).hexdigest() != chord_digest:
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        chord_identity = _explicit_chord_note_identity(
            chord, chord_notes[chord_note_index]
        )
        if (
            chord_identity is None
            or hashlib.sha256(chord_identity).hexdigest() != entry_digest
        ):
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        for index in remove_indices:
            if not _integer(index) or index < 0 or index >= len(notes):
                raise RepairPlanningError("source_changed", "The song changed after this preview.")
            identity = _valid_note_identity(notes[index])
            if identity is None or hashlib.sha256(identity).hexdigest() != entry_digest:
                raise RepairPlanningError("source_changed", "The song changed after this preview.")
            marker = (note_path, index)
            if marker in removed:
                raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
            removed.add(marker)
            indexes.append(index)

    declared_indexes = operation.get("remove_indices")
    if (
        not isinstance(declared_indexes, list)
        or declared_indexes != sorted(indexes, reverse=True)
        or len(declared_indexes) != len(set(declared_indexes))
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    for index in declared_indexes:
        del notes[index]


def _musical_position_count(
    document,
    operations: list[
        DeleteArrayItems | DeleteNotesMatchingChords | StableSortBendPoints
        | StableSortLyricCues
    ],
    rule_code: str,
) -> int:
    positions: set[bytes] = set()
    for operation in operations:
        if isinstance(operation, StableSortLyricCues):
            positions.add(_canonical_json({"path": [], "timeline": "lyrics"}))
        elif isinstance(operation, StableSortBendPoints):
            positions.add(_canonical_json({
                "path": list(operation.array_path[:-1]),
                "t": operation.note_time,
                "s": operation.string,
            }))
        elif isinstance(operation, DeleteArrayItems):
            values = _value_at_path(document, operation.array_path)
            for group in operation.duplicate_groups:
                value = values[group.keep_index]
                if rule_code == "chart.duplicate-chord-note":
                    chord = _value_at_path(document, operation.array_path[:-1])
                    position = {"t": chord["t"], "s": value["s"]}
                elif rule_code == "chart.duplicate-chord":
                    position = {"t": value["t"]}
                elif rule_code == "chart.duplicate-anchor":
                    position = {"time": value["time"]}
                elif rule_code == "chart.duplicate-handshape":
                    position = {
                        "start_time": value["start_time"],
                        "end_time": value.get("end_time"),
                    }
                elif rule_code == "drums.duplicate-hit":
                    position = {"t": value["t"], "p": value["p"]}
                elif rule_code == "timeline.duplicate-beat":
                    position = {
                        "time": value["time"],
                        "measure": value["measure"],
                    }
                else:
                    position = {"t": value["t"], "s": value["s"]}
                positions.add(_canonical_json(position))
        else:
            values = _value_at_path(document, operation.note_array_path)
            for group in operation.match_groups:
                value = values[group.remove_indices[-1]]
                position = {"t": value["t"], "s": value["s"]}
                positions.add(_canonical_json(position))
    return len(positions)


def _value_at_path(document, path: tuple[str | int, ...]):
    value = document
    for part in path:
        if isinstance(part, str) and isinstance(value, dict) and part in value:
            value = value[part]
        elif _integer(part) and isinstance(value, list) and 0 <= part < len(value):
            value = value[part]
        else:
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
    return value


def _render_json(document, original: bytes) -> bytes:
    try:
        original_text = original.decode("utf-8")
        multiline = "\n" in original_text or "\r" in original_text
        trailing_newline = original_text.endswith(("\n", "\r"))
        newline = "\r\n" if "\r\n" in original_text else "\n"
        if multiline:
            match = re.search(r"(?:\r?\n)([ \t]+)[\"}]", original_text)
            indent = match.group(1) if match else 2
            rendered = json.dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                indent=indent,
            )
        else:
            rendered = json.dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        if newline == "\r\n":
            rendered = rendered.replace("\n", "\r\n")
        if trailing_newline:
            rendered += newline
        return rendered.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RepairPlanningError(
            "serialization_failed", "The repaired song file could not be serialized safely."
        ) from exc


def _report_codes(report: dict) -> set[str]:
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list):
        return set()
    return {
        item["code"]
        for item in findings
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


def _finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_json(value) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise RepairPlanningError(
            "unsupported_json_value",
            "The song file contains a value that cannot be repaired safely.",
        ) from exc


def _digest_json(value) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _summary(
    change_count: int,
    arrays_affected: int,
    noun: str,
    change_kind: str,
) -> str:
    item_label = noun if change_count == 1 else f"{noun}s"
    list_label = "list" if arrays_affected == 1 else "lists"
    if change_kind == "reorder":
        return (
            f"Put {change_count} {item_label} into chronological point order "
            f"across {arrays_affected} {list_label}; preserve every bend point."
        )
    return (
        f"Remove {change_count} exact duplicate {item_label} from "
        f"{arrays_affected} {list_label}; keep the first authored copy."
    )
