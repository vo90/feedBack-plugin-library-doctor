"""Conservative repair planning and transactional package updates.

Scanning remains read-only.  This module is used only by the explicit repair
preview and confirmation routes.  Every applied repair is rebuilt from current
source bytes, backed up, validated as a candidate package, and limited to the
small allowlist below.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

import yaml

try:
    import repair_eligibility as _eligibility
except ModuleNotFoundError:  # Tests and some plugin hosts load files by path.
    _eligibility_name = "_library_doctor_repair_eligibility"
    _eligibility = sys.modules.get(_eligibility_name)
    if _eligibility is None:
        _eligibility_spec = importlib.util.spec_from_file_location(
            _eligibility_name,
            Path(__file__).resolve().with_name("repair_eligibility.py"),
        )
        _eligibility = importlib.util.module_from_spec(_eligibility_spec)
        sys.modules[_eligibility_name] = _eligibility
        _eligibility_spec.loader.exec_module(_eligibility)
assess_redundant_handshapes = _eligibility.assess_redundant_handshapes
complete_json_identity = _eligibility.complete_json_identity
effective_tones_source = _eligibility.effective_tones_source
repairable_tempo_event = _eligibility.repairable_tempo_event
repairable_time_signature_event = _eligibility.repairable_time_signature_event
repairable_tone_change = _eligibility.repairable_tone_change
timed_event_stream_eligibility = _eligibility.timed_event_stream_eligibility
_shared_chord_matches_handshape = _eligibility.chord_matches_handshape
redundant_handshape_is_plain = _eligibility.redundant_handshape_is_plain
_shared_reported_invalid_handshape_span = (
    _eligibility.reported_invalid_handshape_span
)
_shared_reported_reversed_handshape = _eligibility.reported_reversed_handshape
_shared_reported_zero_length_handshape = (
    _eligibility.reported_zero_length_handshape
)
_shared_strict_reversed_handshape_context = (
    _eligibility.strict_reversed_handshape_context
)

try:
    import reviewed_repair as _reviewed
except ModuleNotFoundError:  # Tests and some plugin hosts load files by path.
    _reviewed_name = "_library_doctor_reviewed_repair"
    _reviewed = sys.modules.get(_reviewed_name)
    if _reviewed is None:
        _reviewed_spec = importlib.util.spec_from_file_location(
            _reviewed_name,
            Path(__file__).resolve().with_name("reviewed_repair.py"),
        )
        _reviewed = importlib.util.module_from_spec(_reviewed_spec)
        sys.modules[_reviewed_name] = _reviewed
        _reviewed_spec.loader.exec_module(_reviewed)

_actions_name = "_library_doctor_repair_actions"
_actions = sys.modules.get(_actions_name)
if _actions is None:
    _actions_spec = importlib.util.spec_from_file_location(
        _actions_name,
        Path(__file__).resolve().with_name("repair_actions.py"),
    )
    _actions = importlib.util.module_from_spec(_actions_spec)
    sys.modules[_actions_name] = _actions
    _actions_spec.loader.exec_module(_actions)
RepairDefinition = _actions.RepairDefinition
DuplicateGroup = _actions.DuplicateGroup
DeleteArrayItems = _actions.DeleteArrayItems
ChordMatchGroup = _actions.ChordMatchGroup
DeleteNotesMatchingChords = _actions.DeleteNotesMatchingChords
OmitEmptyRootArray = _actions.OmitEmptyRootArray
RedundantHandshapeMatch = _actions.RedundantHandshapeMatch
DeleteRedundantHandshapes = _actions.DeleteRedundantHandshapes
MutedFretChange = _actions.MutedFretChange
NormalizeMutedNegativeFrets = _actions.NormalizeMutedNegativeFrets
StableSortBendPoints = _actions.StableSortBendPoints
StableSortLyricCues = _actions.StableSortLyricCues
StableSortTimelineMarkers = _actions.StableSortTimelineMarkers
StableSortTimedEvents = _actions.StableSortTimedEvents

_catalog_name = "_library_doctor_repair_catalog"
_catalog = sys.modules.get(_catalog_name)
if _catalog is None:
    _catalog_spec = importlib.util.spec_from_file_location(
        _catalog_name,
        Path(__file__).resolve().with_name("repair_catalog.py"),
    )
    _catalog = importlib.util.module_from_spec(_catalog_spec)
    sys.modules[_catalog_name] = _catalog
    _catalog_spec.loader.exec_module(_catalog)
_REPAIR_DEFINITIONS = _catalog.SAFE_REPAIR_DEFINITIONS
_MEDIA_REPAIR_DEFINITIONS = _catalog.MEDIA_REPAIR_DEFINITIONS

_workspace_name = "_library_doctor_repair_workspace"
_workspace = sys.modules.get(_workspace_name)
if _workspace is None:
    _workspace_spec = importlib.util.spec_from_file_location(
        _workspace_name,
        Path(__file__).resolve().with_name("repair_workspace.py"),
    )
    _workspace = importlib.util.module_from_spec(_workspace_spec)
    sys.modules[_workspace_name] = _workspace
    _workspace_spec.loader.exec_module(_workspace)

_recovery_name = "_library_doctor_repair_recovery"
_recovery = sys.modules.get(_recovery_name)
if _recovery is None:
    _recovery_spec = importlib.util.spec_from_file_location(
        _recovery_name,
        Path(__file__).resolve().with_name("repair_recovery.py"),
    )
    _recovery = importlib.util.module_from_spec(_recovery_spec)
    sys.modules[_recovery_name] = _recovery
    _recovery_spec.loader.exec_module(_recovery)

_yaml_name = "_library_doctor_repair_yaml"
_yaml = sys.modules.get(_yaml_name)
if _yaml is None:
    _yaml_spec = importlib.util.spec_from_file_location(
        _yaml_name,
        Path(__file__).resolve().with_name("repair_yaml.py"),
    )
    _yaml = importlib.util.module_from_spec(_yaml_spec)
    sys.modules[_yaml_name] = _yaml
    _yaml_spec.loader.exec_module(_yaml)
_UniqueSafeLoader = _yaml.UniqueSafeLoader

_transaction_name = "_library_doctor_repair_transaction"
_transaction = sys.modules.get(_transaction_name)
if _transaction is None:
    _transaction_spec = importlib.util.spec_from_file_location(
        _transaction_name,
        Path(__file__).resolve().with_name("repair_transaction.py"),
    )
    _transaction = importlib.util.module_from_spec(_transaction_spec)
    sys.modules[_transaction_name] = _transaction
    _transaction_spec.loader.exec_module(_transaction)


REPAIR_CATALOG_VERSION = "repairs-20"
REPAIR_PLAN_SCHEMA = "library_doctor.repair_plan.v1"
REVIEWED_PACKAGE_PLAN_SCHEMA = "library_doctor.reviewed_repair_plan.v1"
REVIEWED_INSPECTION_SCHEMA = "library_doctor.reviewed_repair_inspection.v1"
PLAYER_REVIEW_CONTEXT_SCHEMA = "library_doctor.player_review_context.v1"
REVIEWED_OPTIONS_SCHEMA = "library_doctor.reviewed_repair_options.v1"
REVIEWED_REPAIR_REGISTRY_VERSION = _reviewed.REVIEWED_REPAIR_REGISTRY_VERSION
MAX_REPAIR_TEXT_BYTES = 64 * 1024 * 1024
MAX_REPAIR_MEMBER_BYTES = 128 * 1024 * 1024
MAX_REPAIR_STRUCTURE_ITEMS = 2_000_000
MAX_REPAIR_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_DECLARED_REPAIR_MEMBERS = 1_000
MAX_DIRECTORY_CANDIDATE_ENTRIES = 50_000
MAX_RECOVERY_BACKUP_BYTES = 512 * 1024 * 1024
PACKAGE_SUFFIXES = (".feedpak", ".sloppak")
PACKAGE_REPAIR_SCHEMA = "library_doctor.package_repair.v1"
BACKUP_SCHEMA = "library_doctor.repair_backup.v3"
HISTORY_SCHEMA = "library_doctor.repair_history.v1"
TRANSACTION_SCHEMA = "library_doctor.repair_transaction.v1"
MAX_REPAIR_HISTORY = 50
MAX_PENDING_TRANSACTIONS = 100
MAX_REVIEWED_DECISIONS = 2_000
MAX_REVIEWED_OPTIONS_CACHE = 256
_BACKUP_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{12}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_REQUEST_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
ALL_SAFE_RULE_CODE = "package.all-safe"


class RepairPlanningError(ValueError):
    """A stable, user-safe reason why a repair preview cannot be produced."""

    def __init__(self, code: str, message: str, *, file_state: str = "unchanged"):
        super().__init__(message)
        self.code = code
        self.file_state = file_state


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
    "chart.empty-phrases-key",
    "timeline.empty-arrangement-tempos-key",
    "timeline.duplicate-tempo",
    "timeline.tempos-out-of-order",
    "timeline.duplicate-time-signature",
    "timeline.time-signatures-out-of-order",
    "tones.duplicate-change",
    "tones.changes-out-of-order",
    "chart.negative-muted-fret",
    "chart.bend-points-out-of-order",
    "chart.duplicate-chord-note",
    "chart.duplicate-chord",
    "chart.duplicate-note",
    "chart.note-duplicates-chord",
    "chart.duplicate-anchor",
    "chart.duplicate-handshape",
    "chart.zero-length-handshape",
    "chart.invalid-handshape-span",
    "lyrics.out-of-order",
    "timeline.duplicate-beat",
    "timeline.beats-out-of-order",
    "timeline.duplicate-section",
    "timeline.sections-out-of-order",
    "drums.duplicate-hit",
)

_CONDITIONAL_STRUCTURAL_RULES = frozenset({
    "chart.empty-phrases-key",
    "timeline.empty-arrangement-tempos-key",
    "timeline.duplicate-tempo",
    "timeline.tempos-out-of-order",
    "timeline.duplicate-time-signature",
    "timeline.time-signatures-out-of-order",
    "tones.duplicate-change",
    "tones.changes-out-of-order",
})

_ALL_REPAIR_DEFINITIONS = _REPAIR_DEFINITIONS + _MEDIA_REPAIR_DEFINITIONS

_ALL_SAFE_DEFINITION = {
    "rule_code": ALL_SAFE_RULE_CODE,
    "safety": "safe_automatic",
    "title": "Fix all safe issues",
    "description": (
        "Apply every deterministic safe song-data repair currently available in "
        "this Feedpak as one validated transaction."
    ),
    "player_result": (
        "Eligible invalid values are normalized, redundant instructions are "
        "removed, and supported ordering problems are corrected without changing "
        "the intended musical data. "
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
    return [definition.to_dict() for definition in _ALL_REPAIR_DEFINITIONS]


def reviewed_repair_catalog() -> list[dict]:
    """Return author-decided adapters, kept outside every safe allowlist."""
    return _reviewed.reviewed_repair_catalog()


def all_safe_repair_definition() -> dict:
    """Return user-facing metadata for the combined per-package repair."""
    return dict(_ALL_SAFE_DEFINITION)


def repair_for_rule(rule_code: str) -> dict | None:
    """Return a repair definition only when the rule is explicitly supported."""
    for definition in _ALL_REPAIR_DEFINITIONS:
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

    _apply_plan_actions_to_document(document, plan)
    return _render_json(document, raw)


def _apply_plan_actions_to_document(document, plan: dict) -> None:
    """Apply already-validated closed actions to an in-memory JSON document."""
    removed: set[tuple[tuple[str | int, ...], int]] = set()
    reordered: set[tuple[str | int, ...]] = set()
    normalized: set[tuple[tuple[str | int, ...], int]] = set()
    omitted: set[tuple[str | int, ...]] = set()
    reviewed_paths: set[tuple[str | int, ...]] = set()
    repair_mode = plan.get("repair_mode", "automatic")
    expected_safety = (
        "review_required" if repair_mode == "reviewed" else "safe_automatic"
    )
    reviewed_candidates = None
    if repair_mode == "reviewed":
        try:
            reviewed_definition = _reviewed.reviewed_repair_definition(
                plan.get("adapter_id")
            )
        except (TypeError, ValueError):
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        operation_ids = {
            operation.get("candidate_id")
            for action in plan.get("actions", [])
            if isinstance(action, dict)
            for operation in action.get("operations", [])
            if isinstance(operation, dict)
        }
        source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
        selection = reviewed_definition.select_document(
            document,
            member_path=str(source.get("member_path") or ""),
            candidate_ids=operation_ids,
            difficulty_scope=_reviewed_difficulty_scope(
                plan.get("difficulty_scope", "full_only")
            ),
        )
        reviewed_candidates = {
            candidate.candidate_id: candidate
            for candidate in selection.candidates
        }
    for action in plan.get("actions", []):
        if not isinstance(action, dict) or action.get("safety") != expected_safety:
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        rule_code = None
        if repair_mode != "reviewed":
            rule_code = action.get("rule_code")
            definition = _REPAIR_BY_RULE.get(rule_code)
            if (
                definition is None
                or action.get("action_kind") != definition.action_kind
                or action.get("change_kind") != definition.change_kind
            ):
                raise RepairPlanningError(
                    "invalid_plan", "The repair preview is invalid."
                )
        for operation in action.get("operations", []):
            if repair_mode == "reviewed":
                candidate = reviewed_candidates.get(
                    operation.get("candidate_id")
                    if isinstance(operation, dict) else None
                )
                if candidate is None:
                    raise RepairPlanningError(
                        "source_changed",
                        "A reviewed note changed after this preview. Inspect it again before applying decisions.",
                    )
                expected = _reviewed_operation(
                    document,
                    reviewed_definition,
                    candidate,
                    operation.get("decision"),
                )
                if expected != operation:
                    raise RepairPlanningError(
                        "invalid_plan", "The reviewed repair preview is invalid."
                    )
            _apply_operation(
                document,
                operation,
                removed,
                reordered,
                normalized,
                reviewed_paths,
                omitted,
                rule_code=rule_code,
            )


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
        preview_repair=None,
        validate_reviewed_arrangement=None,
        transaction_barrier=None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._get_dlc_dir = get_dlc_dir
        self._validate_feedpak = validate_feedpak
        self._validator_version = validator_version
        self._log = log
        self._preview_repair = preview_repair
        self._validate_reviewed_arrangement = (
            validate_reviewed_arrangement
            if callable(validate_reviewed_arrangement)
            else None
        )
        self._reviewed_options_cache: dict[tuple, dict] = {}
        self._transaction_barrier = (
            transaction_barrier if callable(transaction_barrier) else None
        )
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
        self._recovery_policy = _recovery.RecoveryPolicy(
            backup_size=self._backup_size,
            error_type=RepairPlanningError,
            lock=self._lock,
            prepare_finalize=self._prepare_finalize_backup,
            prepare_restore=self._prepare_restore,
            read_history=self._read_history,
            read_transactions=self._read_transactions,
            recover_legacy_receipts=self._recover_legacy_receipts,
            resolve_package=self._resolve_package,
            valid_backup_id=lambda value: bool(_BACKUP_ID_RE.fullmatch(value)),
        )
        self._transaction_journal = _transaction.TransactionJournal(
            atomic_write=self._atomic_write,
            backup_id_matches=lambda value: bool(_BACKUP_ID_RE.fullmatch(value)),
            candidate=self._candidate,
            capture_package_token=self._capture_package_token,
            commit=self._commit,
            config_dir=self._config_dir,
            delete_backup=self._delete_backup,
            error_type=RepairPlanningError,
            file_handling=self._file_handling,
            finish_transaction=self._finish_transaction,
            log=self._log,
            max_manifest_bytes=MAX_REPAIR_MANIFEST_BYTES,
            max_member_bytes=MAX_REPAIR_MEMBER_BYTES,
            max_pending=MAX_PENDING_TRANSACTIONS,
            member_exists=self._member_exists,
            public_recovery=self._recovery_policy.public_required,
            read_backup=self._read_backup,
            read_history=self._read_history,
            read_member=self._read_member,
            resolve_package=self._resolve_package,
            schema=TRANSACTION_SCHEMA,
            sync_directory=self._sync_directory,
            update_transaction=self._update_transaction,
            validate_feedpak=self._validate_feedpak,
            write_history=self._write_history,
        )
        try:
            self._workspace_reconciliation = _workspace.reconcile_stale_workspaces(
                self._config_dir
            )
        except Exception as exc:
            self._workspace_reconciliation = {
                "pending": 0,
                "removed": 0,
                "unreadable": 1,
                "capped": False,
            }
            self._log.warning(
                "Library Doctor could not reconcile old repair workspaces: %s",
                type(exc).__name__,
            )
        self._reconcile_transactions()

    def preview(
        self,
        package: str,
        rule_code: str,
        *,
        start_seconds: float | None = None,
    ) -> dict:
        """Return a bounded, read-only summary bound to current package bytes."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            media_repair = self._is_preview_repair(rule_code)
            if media_repair:
                internal = self._preview_repair.preview(
                    package_path,
                    package_name,
                    rule_code,
                    lambda member_path, limit: self._read_member(
                        package_path, member_path, limit
                    ),
                    catalog_version=REPAIR_CATALOG_VERSION,
                    validator_version=self._validator_version,
                    start_seconds=start_seconds,
                )
                return self._public_plan(internal)
            internal = self._plan_package(package_path, package_name, rule_code)
            return self._public_plan(internal)

    def preview_all(self, package: str) -> dict:
        """Preview every currently available safe repair for one Feedpak."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_all_package(package_path, package_name)
            return self._public_plan(internal)

    def preview_selected(
        self,
        package: str,
        rule_codes: Iterable[str],
    ) -> dict:
        """Preview only scan-reported safe rules for batch preflight.

        The completed scan and its package signature define the eligible rule
        set. Replanning those rules from current source bytes preserves the
        authoritative safety check without traversing unrelated repair types.
        """
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_all_package(
                package_path,
                package_name,
                rule_codes=rule_codes,
            )
            return self._public_plan(internal)

    def inspect_reviewed(
        self,
        package: str,
        adapter_id: str,
        *,
        difficulty_scope: str = "full_only",
        offset: int = 0,
        limit: int | None = None,
    ) -> dict:
        """Return current source-bound candidates without creating a write plan."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._inspect_reviewed_package(
                package_path,
                package_name,
                adapter_id,
                difficulty_scope=difficulty_scope,
                offset=offset,
                limit=limit,
            )
            return self._public_plan(internal)

    def reviewed_options(
        self,
        package: str,
        adapter_id: str,
        candidate_id: str,
        *,
        difficulty_scope: str = "full_only",
    ) -> dict:
        """Return only source-bound decisions whose simulated outcomes are safe."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            return self._reviewed_options_package(
                package_path,
                package_name,
                adapter_id,
                candidate_id,
                difficulty_scope=difficulty_scope,
            )

    def inspect_reviewed_player(
        self,
        package: str,
        adapter_id: str,
        playback_filename: str,
        *,
        difficulty_scope: str = "full_only",
        offset: int = 0,
        limit: int | None = None,
    ) -> dict:
        """Return one source-bound review queue for FeedBack's normal Player."""
        relative_player = PurePosixPath(str(playback_filename))
        if (
            relative_player.is_absolute()
            or "\\" in str(playback_filename)
            or any(part in {"", ".", ".."} for part in relative_player.parts)
            or relative_player.suffix.lower() not in PACKAGE_SUFFIXES
        ):
            raise RepairPlanningError(
                "player_review_unavailable",
                "This package cannot be opened safely in FeedBack's normal Player.",
            )
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            inspection = self._inspect_reviewed_package(
                package_path,
                package_name,
                adapter_id,
                difficulty_scope=difficulty_scope,
                offset=offset,
                limit=limit,
            )
            manifest = self._read_repair_manifest(package_path)
            bindings = self._reviewed_arrangement_bindings(manifest)
            candidates = []
            for candidate in inspection.get("candidates", []):
                if not isinstance(candidate, dict):
                    continue
                options = copy.deepcopy(bindings.get(candidate.get("member_path"), []))
                enriched = copy.deepcopy(candidate)
                enriched["player"] = {
                    "available": bool(options),
                    "arrangements": options,
                    "default_arrangement_index": (
                        options[0]["index"] if options else None
                    ),
                    "mastery_fraction": (
                        candidate.get("stream_context", {}).get("mastery_fraction", 1.0)
                        if isinstance(candidate.get("stream_context"), dict)
                        else 1.0
                    ),
                }
                if not options:
                    blockers = list(enriched.get("blockers") or [])
                    if "player_arrangement_unavailable" not in blockers:
                        blockers.append("player_arrangement_unavailable")
                    enriched["blockers"] = blockers
                candidates.append(enriched)
            public_inspection = self._public_plan({
                **inspection,
                "candidates": candidates,
                "candidate_count": len(candidates),
                "available": bool(candidates),
            })
            pending = self._pending_recovery_for_package(package_name)
            return {
                "schema": PLAYER_REVIEW_CONTEXT_SCHEMA,
                "package": package_name,
                "adapter_id": adapter_id,
                "difficulty_scope": difficulty_scope,
                "playback_filename": relative_player.as_posix(),
                "inspection": public_inspection,
                "capabilities": {
                    "normal_player": True,
                    "live_highway_preview": True,
                    "full_tab_live_preview": False,
                    "partial_apply": True,
                    "single_undo_checkpoint": True,
                },
                "pending_recovery": pending,
            }

    def preview_reviewed(
        self,
        package: str,
        adapter_id: str,
        decisions: list[dict],
        *,
        difficulty_scope: str = "full_only",
    ) -> dict:
        """Preview explicit author decisions against current candidate IDs."""
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            internal = self._plan_reviewed_package(
                package_path,
                package_name,
                adapter_id,
                decisions,
                difficulty_scope=difficulty_scope,
            )
            self._validate_reviewed_preview_candidate(
                package_path,
                package_name,
                internal,
            )
            internal["candidate_validated"] = True
            return self._public_plan(internal)

    def apply_reviewed(
        self,
        package: str,
        adapter_id: str,
        decisions: list[dict],
        plan_id: str,
        *,
        difficulty_scope: str = "full_only",
        deep_audio: bool = False,
        verified_before_report: dict | None = None,
        source_guard=None,
        request_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Replan, validate, back up, and commit reviewed author decisions."""
        if not isinstance(plan_id, str) or len(plan_id) != 64:
            raise RepairPlanningError(
                "invalid_plan",
                "Review these decisions again before applying them.",
            )
        transaction_started = time.monotonic()
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            self._assert_package_mutation_allowed(package_name, operation="repair")
            pending = self._pending_recovery_for_package(package_name)
            if pending is not None:
                raise RepairPlanningError(
                    "reviewed_recovery_pending",
                    "Undo or finalize the current repair for this song before applying another reviewed group.",
                )
            internal = self._plan_reviewed_package(
                package_path,
                package_name,
                adapter_id,
                decisions,
                difficulty_scope=difficulty_scope,
            )
            if internal["plan_id"] != plan_id:
                raise RepairPlanningError(
                    "source_changed",
                    "The song or reviewed decisions changed after preview. Review them again before applying.",
                )
            if not internal["available"]:
                raise RepairPlanningError(
                    "nothing_to_repair",
                    "No selected reviewed decision changes this package.",
                )
            return self._apply_internal(
                package_path,
                package_name,
                internal,
                deep_audio=deep_audio,
                verified_before_report=verified_before_report,
                source_guard=source_guard,
                transaction_started=transaction_started,
                request_id=request_id,
                request_operation="reviewed-repair.apply",
                request_fingerprint=request_fingerprint,
            )

    @staticmethod
    def _reviewed_arrangement_bindings(manifest: dict) -> dict[str, list[dict]]:
        """Map arrangement members to the compacted indices used by Player."""
        result: dict[str, list[dict]] = {}
        compact_index = 0
        entries = manifest.get("arrangements")
        if not isinstance(entries, list):
            return result
        for manifest_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            arrangement_type = str(entry.get("type") or "").strip().lower()
            if arrangement_type in {"drum", "drums"}:
                continue
            raw_member = entry.get("file")
            has_notation = isinstance(entry.get("notation"), str) and bool(
                entry["notation"].strip()
            )
            if not isinstance(raw_member, str) or not raw_member.strip():
                if has_notation:
                    compact_index += 1
                continue
            try:
                member_path = _validate_member_path(raw_member)
            except RepairPlanningError:
                continue
            result.setdefault(member_path, []).append({
                "index": compact_index,
                "manifest_index": manifest_index,
                "id": str(entry.get("id") or "").strip(),
                "name": str(entry.get("name") or entry.get("id") or f"Arrangement {compact_index + 1}").strip(),
                "type": arrangement_type or "unknown",
            })
            compact_index += 1
        return result

    def _pending_recovery_for_package(self, package_name: str) -> dict | None:
        return self._recovery_policy.pending_repair(package_name)

    def recovery_state(self, package: str) -> dict:
        """Return public, path-free quarantine state for one package."""
        return self._recovery_policy.state(package)

    def recovery_states(self, packages: Iterable[str]) -> dict[str, dict]:
        """Return unresolved recovery states for a bounded set of public IDs."""
        return self._recovery_policy.states(packages)

    def _assert_package_mutation_allowed(
        self,
        package_name: str,
        *,
        operation: str,
        backup_id: str | None = None,
    ) -> dict | None:
        return self._recovery_policy.assert_mutation_allowed(
            package_name, operation=operation, backup_id=backup_id
        )

    def apply(
        self,
        package: str,
        rule_code: str,
        plan_id: str,
        *,
        deep_audio: bool = False,
        verified_before_report: dict | None = None,
        source_guard=None,
        request_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Rebuild, validate, back up, and atomically commit one package repair."""
        if not isinstance(plan_id, str) or len(plan_id) != 64:
            raise RepairPlanningError("invalid_plan", "Review the safe fix again before applying it.")
        transaction_started = time.monotonic()
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            media_repair = self._is_preview_repair(rule_code)
            if media_repair:
                self._assert_package_mutation_allowed(
                    package_name, operation="repair"
                )
                internal = self._preview_repair.claim(
                    package_path,
                    package_name,
                    rule_code,
                    plan_id,
                    lambda member_path, limit: self._read_member(
                        package_path, member_path, limit
                    ),
                )
            else:
                self._assert_package_mutation_allowed(
                    package_name, operation="repair"
                )
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
            result = self._apply_internal(
                package_path,
                package_name,
                internal,
                deep_audio=bool(deep_audio or media_repair),
                retain_recovery=not media_repair,
                verified_before_report=verified_before_report,
                source_guard=source_guard,
                transaction_started=transaction_started,
                request_id=request_id,
                request_operation="repair.apply",
                request_fingerprint=request_fingerprint,
            )
            if media_repair:
                self._preview_repair.discard(plan_id)
            return result

    def preview_audio(self, plan_id: str) -> bytes:
        """Return one in-memory proposed Ogg clip for the review player."""
        if not isinstance(plan_id, str) or len(plan_id) != 64 or self._preview_repair is None:
            raise RepairPlanningError(
                "invalid_plan", "This proposed preview is unavailable. Generate it again."
            )
        return self._preview_repair.audio(plan_id)

    def current_preview_audio(self, package: str) -> bytes:
        """Return the package's current, bounded Ogg preview for listening only."""
        with self._lock:
            _root, package_path, _package_name = self._resolve_package(package)
            manifest = self._read_repair_manifest(package_path)
            preview_value = manifest.get("preview")
            if not isinstance(preview_value, str) or not preview_value.strip():
                raise RepairPlanningError(
                    "preview_unavailable",
                    "This Feedpak does not declare a preview to play.",
                )
            preview_path = _validate_member_path(preview_value)
            if not preview_path.lower().endswith(".ogg"):
                raise RepairPlanningError(
                    "preview_unsupported",
                    "This Feedpak's current preview is not a supported Ogg audio file.",
                )
            content = self._read_member(
                package_path,
                preview_path,
                MAX_REPAIR_MEMBER_BYTES,
            )
            if not content.startswith(b"OggS"):
                raise RepairPlanningError(
                    "preview_unavailable",
                    "This Feedpak's current preview is not playable Ogg audio.",
                )
            return content

    def reviewed_passage(
        self,
        package: str,
        adapter_id: str,
        candidate_id: str,
    ) -> dict:
        """Generate optional bounded audio for one current reviewed candidate."""
        if self._preview_repair is None or not hasattr(
            self._preview_repair, "passage"
        ):
            raise RepairPlanningError(
                "review_audio_unavailable",
                "Optional passage listening is unavailable. Visual review is still available.",
            )
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            try:
                definition = _reviewed.reviewed_repair_definition(adapter_id)
            except ValueError as exc:
                raise RepairPlanningError(
                    "unsupported_reviewed_repair",
                    "This Reviewed repair adapter is not supported.",
                ) from exc
            if not definition.audio_support:
                raise RepairPlanningError(
                    "review_audio_unavailable",
                    "This Reviewed repair does not provide passage listening.",
                )
            manifest = self._read_repair_manifest(package_path)
            matches = []
            for member_path, _raw, document in self._reviewed_member_documents(
                package_path, manifest
            ):
                selection = definition.select_document(
                    document,
                    member_path=member_path,
                    candidate_ids={candidate_id},
                )
                matches.extend(selection.candidates)
            if len(matches) != 1:
                raise RepairPlanningError(
                    "candidate_changed",
                    "This reviewed note changed or is no longer available. Inspect the package again.",
                )
            candidate = matches[0]
            return self._preview_repair.passage(
                package_name,
                candidate.candidate_id,
                candidate.time,
                lambda member_path, limit: self._read_member(
                    package_path, member_path, limit
                ),
            )

    def reviewed_passage_audio(self, audio_token: str) -> bytes:
        if self._preview_repair is None or not hasattr(
            self._preview_repair, "passage_audio"
        ):
            raise RepairPlanningError(
                "review_audio_unavailable",
                "Optional passage listening is unavailable.",
            )
        return self._preview_repair.passage_audio(audio_token)

    def preview_tool_status(self, package: str) -> dict:
        """Return scan-independent Preview Creator eligibility for one package."""
        if self._preview_repair is None:
            raise RepairPlanningError(
                "preview_unavailable", "Preview Creator is unavailable."
            )
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            return self._preview_repair.tool_status(
                package_path,
                package_name,
                lambda member_path, limit: self._read_member(
                    package_path, member_path, limit
                ),
                lambda member_path: self._member_exists(
                    package_path, member_path
                ),
            )

    def apply_automatic_preview(
        self,
        package: str,
        rule_code: str,
        *,
        verified_before_report: dict | None = None,
        source_guard=None,
        request_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Select, validate, and apply one temporary-recovery-protected preview."""
        if not self._is_preview_repair(rule_code):
            raise RepairPlanningError(
                "unsupported_repair",
                "This finding does not have automatic preview generation.",
            )
        transaction_started = time.monotonic()
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            self._assert_package_mutation_allowed(package_name, operation="repair")
            use_verified_report = bool(
                package_path.is_file()
                and self._can_reuse_verified_before_report(
                    verified_before_report,
                    source_guard,
                )
            )
            if use_verified_report and not source_guard():
                raise RepairPlanningError(
                    "source_changed",
                    "This Feedpak changed after its completed Deep Audio scan. Scan it again before repairing it.",
                )
            internal = self._preview_repair.preview(
                package_path,
                package_name,
                rule_code,
                lambda member_path, limit: self._read_member(
                    package_path, member_path, limit
                ),
                catalog_version=REPAIR_CATALOG_VERSION,
                validator_version=self._validator_version,
                verified_before_report=(
                    verified_before_report if use_verified_report else None
                ),
            )
            plan_id = internal["plan_id"]
            try:
                claimed = self._preview_repair.claim(
                    package_path,
                    package_name,
                    rule_code,
                    plan_id,
                    lambda member_path, limit: self._read_member(
                        package_path, member_path, limit
                    ),
                )
                return self._apply_internal(
                    package_path,
                    package_name,
                    claimed,
                    deep_audio=True,
                    retain_recovery=False,
                    verified_before_report=(
                        verified_before_report if use_verified_report else None
                    ),
                    source_guard=source_guard if use_verified_report else None,
                    transaction_started=transaction_started,
                    request_id=request_id,
                    request_operation="repair.automatic",
                    request_fingerprint=request_fingerprint,
                )
            finally:
                self._preview_repair.discard(plan_id)

    def _is_preview_repair(self, rule_code: str) -> bool:
        return bool(
            self._preview_repair is not None
            and self._preview_repair.supports(rule_code)
        )

    def apply_all(
        self,
        package: str,
        plan_id: str,
        *,
        deep_audio: bool = False,
        rule_codes: Iterable[str] | None = None,
        verified_before_report: dict | None = None,
        source_guard=None,
        request_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Apply all available safe repairs as one package transaction."""
        if not isinstance(plan_id, str) or len(plan_id) != 64:
            raise RepairPlanningError(
                "invalid_plan",
                "Review all safe fixes again before applying them.",
            )
        transaction_started = time.monotonic()
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            self._assert_package_mutation_allowed(package_name, operation="repair")
            internal = self._plan_all_package(
                package_path,
                package_name,
                rule_codes=rule_codes,
            )
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
                verified_before_report=verified_before_report,
                source_guard=source_guard,
                transaction_started=transaction_started,
                request_id=request_id,
                request_operation="repair.all.apply",
                request_fingerprint=request_fingerprint,
            )

    def apply_selected(
        self,
        package: str,
        rule_codes: Iterable[str],
        *,
        deep_audio: bool = False,
        verified_before_report: dict | None = None,
        source_guard=None,
    ) -> dict:
        """Plan and immediately apply scan-selected safe rules.

        Batch review is bound to a completed scan signature and a fixed rule
        set. Rebuilding that same selected plan inside the mutation lock keeps
        the authoritative source-byte, blocker, candidate-validation, backup,
        and atomic-commit checks without doing the expensive work twice.
        """
        transaction_started = time.monotonic()
        with self._lock:
            _root, package_path, package_name = self._resolve_package(package)
            self._assert_package_mutation_allowed(package_name, operation="repair")
            internal = self._plan_all_package(
                package_path,
                package_name,
                rule_codes=rule_codes,
            )
            blockers = internal.get("blockers") or []
            if blockers:
                first = blockers[0]
                raise RepairPlanningError(
                    str(first.get("code") or "repair_blocked"),
                    str(first.get("message") or (
                        "A referenced song-data file cannot be changed safely."
                    )),
                )
            if not internal["available"]:
                raise RepairPlanningError(
                    "nothing_to_repair",
                    "No scan-selected safe repairs are currently available in this package.",
                )
            return self._apply_internal(
                package_path,
                package_name,
                internal,
                deep_audio=deep_audio,
                verified_before_report=verified_before_report,
                source_guard=source_guard,
                transaction_started=transaction_started,
            )

    def _validate_reviewed_preview_candidate(
        self,
        package_path: Path,
        package_name: str,
        internal: dict,
    ) -> None:
        """Build and fully validate the selected group without committing it."""
        originals = {
            item["member_path"]: item["raw"] for item in internal["_members"]
        }
        replacements = {
            item["member_path"]: apply_json_member(item["raw"], item["plan"])
            for item in internal["_members"]
        }
        if not originals or not replacements:
            raise RepairPlanningError(
                "nothing_to_repair",
                "No selected reviewed decision changes this package.",
            )
        before = self._validate_feedpak(
            package_path, package_name, deep_audio=False
        )
        candidate, cleanup = self._candidate(package_path, replacements)
        try:
            after = self._validate_feedpak(
                candidate, package_name, deep_audio=False
            )
            self._verify_reviewed_validation(before, after)
        finally:
            cleanup()

    def _apply_internal(
        self,
        package_path: Path,
        package_name: str,
        internal: dict,
        *,
        deep_audio: bool,
        retain_recovery: bool = True,
        verified_before_report: dict | None = None,
        source_guard=None,
        transaction_started: float | None = None,
        request_id: str | None = None,
        request_operation: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Validate and commit one already-recalculated package plan."""
        if (
            not isinstance(transaction_started, (int, float))
            or not math.isfinite(transaction_started)
        ):
            transaction_started = time.monotonic()
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
        source_token = self._capture_package_token(package_path)
        self._emit_transaction_barrier(
            "source_captured", package=package_name, operation="repair"
        )
        song_data_only = all(
            item.get("source_kind") in {
                "arrangement", "timeline", "lyrics", "drum_tab"
            }
            for item in internal["_members"]
        )
        reuse_verified_before = bool(
            package_path.is_file()
            and self._can_reuse_verified_before_report(
                verified_before_report,
                source_guard,
            )
        )
        reuse_deep_audio = bool(
            deep_audio
            and song_data_only
            # Archived Feedpaks receive a complete candidate CRC pass below.
            # Unpacked directory packages have no equivalent archive checksum,
            # so keep the conservative full Deep Audio validation for them.
            and package_path.is_file()
            and reuse_verified_before
        )
        if reuse_verified_before:
            if not source_guard():
                raise RepairPlanningError(
                    "source_changed",
                    "This Feedpak changed after its completed Deep Audio scan. Scan it again before repairing it.",
                )
            before = copy.deepcopy(verified_before_report)
        else:
            before = self._validate_feedpak(
                package_path, package_name, deep_audio=bool(deep_audio)
            )
        candidate, cleanup = self._candidate(package_path, replacements)
        try:
            after = self._validate_feedpak(
                candidate,
                package_name,
                deep_audio=bool(deep_audio and not reuse_deep_audio),
            )
            if reuse_deep_audio:
                after = self._reuse_unchanged_deep_audio(before, after)
            rule_codes = internal.get("rule_codes")
            if not isinstance(rule_codes, list) or not rule_codes:
                rule_codes = [internal["rule_code"]]
            verification = internal.get("_verification")
            if (
                isinstance(verification, dict)
                and verification.get("mode") == "reviewed"
            ):
                self._verify_reviewed_validation(
                    before,
                    after,
                    set(internal.get("rule_codes") or ()),
                )
            else:
                self._verify_validation(before, after, rule_codes)
            self._emit_transaction_barrier(
                "candidate_validated", package=package_name, operation="repair"
            )
            if reuse_verified_before and not source_guard():
                raise RepairPlanningError(
                    "source_changed",
                    "This Feedpak changed while its repaired candidate was being checked. Nothing was saved.",
                )
            backup_id = self._create_backup(
                package_name,
                package_path,
                originals,
                replacements,
                internal["plan_id"],
                internal["rule_code"],
                self._public_plan(internal),
            )
            transaction = None
            try:
                if package_path.is_dir():
                    transaction = self._begin_transaction(
                        package_name,
                        backup_id,
                        operation="repair",
                        target_state="repaired",
                    )
                self._emit_transaction_barrier(
                    "backup_durable",
                    package=package_name,
                    operation="repair",
                    backup_id=backup_id,
                )
                try:
                    self._verify_backup_durable(
                        backup_id,
                        package_name,
                        originals,
                    )
                except RepairPlanningError as verify_exc:
                    raise RepairPlanningError(
                        "backup_failed",
                        "The recovery backup could not be verified, so nothing was changed.",
                    ) from verify_exc
                self._assert_source_state(
                    package_name,
                    package_path,
                    originals,
                    source_token,
                )
                self._emit_transaction_barrier(
                    "source_guarded",
                    package=package_name,
                    operation="repair",
                    backup_id=backup_id,
                )
                self._commit(
                    package_name,
                    package_path,
                    candidate,
                    replacements,
                    originals,
                    source_token=source_token,
                    transaction=transaction,
                )
            except RepairPlanningError as exc:
                if exc.file_state == "unchanged":
                    if transaction is not None:
                        self._finish_transaction(transaction)
                    try:
                        self._delete_backup(backup_id)
                    except RepairPlanningError as cleanup_exc:
                        self._log.warning(
                            "Library Doctor could not remove an unused recovery backup %s: %s",
                            backup_id,
                            cleanup_exc,
                        )
                raise
        finally:
            cleanup()

        backup_removed = False
        recovery_bytes_freed = 0
        backup_cleanup_error = ""
        if not retain_recovery:
            try:
                recovery_bytes_freed = self._delete_backup(backup_id)
                backup_removed = True
            except RepairPlanningError as exc:
                # The validated repair is already committed. Report the rare
                # cleanup failure accurately without pretending the Feedpak
                # transaction failed or deleting anything else.
                backup_cleanup_error = str(exc)
                self._log.warning(
                    "Library Doctor completed preview repair for %s but could not remove temporary recovery backup %s: %s",
                    package_name,
                    backup_id,
                    exc,
                )

        file_handling = dict(internal.get("file_handling") or {})
        if file_handling:
            file_handling.update({
                "backup_created": True,
                "backup_id": backup_id,
                "undo_available": bool(retain_recovery or not backup_removed),
                "backup_retained": not backup_removed,
                "backup_removed": backup_removed,
                "backup_cleanup_required": bool(backup_cleanup_error),
                "backup_cleanup_error": backup_cleanup_error,
                "backup_size_bytes": self._backup_size(backup_id) or 0,
                "recovery_bytes_freed": recovery_bytes_freed,
            })
            if not retain_recovery:
                file_handling["summary"] = (
                    "Library Doctor checked the complete repaired song before replacing the Feedpak at the same location. "
                    "Its temporary recovery copy was then removed automatically, so no duplicate song or pending preview backup remains."
                    if backup_removed else
                    "The validated preview repair completed at the same Feedpak path, but Library Doctor could not remove its temporary recovery copy automatically. "
                    "The repaired Feedpak remains active and the recovery copy is available for explicit cleanup."
                )
        else:
            file_handling = self._file_handling(backup_id)
            file_handling["backup_size_bytes"] = self._backup_size(backup_id) or 0
        performance = {
            "elapsed_seconds": round(
                max(0.0, time.monotonic() - transaction_started), 6
            ),
            "deep_audio_requested": bool(deep_audio),
            "verified_scan_report_reused": reuse_verified_before,
            "deep_audio_reused": reuse_deep_audio,
        }
        result = {
            **self._public_plan(internal),
            "applied": True,
            "outcome": "success",
            "backup_id": backup_id,
            "undo_available": bool(retain_recovery or not backup_removed),
            "report": after,
            "deep_audio": bool(deep_audio),
            "deep_audio_reused": reuse_deep_audio,
            "verified_scan_report_reused": reuse_verified_before,
            "performance": performance,
            "file_handling": file_handling,
            **self._request_metadata(
                request_id,
                request_operation,
                request_fingerprint,
            ),
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
            **(
                {
                    key: internal.get(key)
                    for key in (
                        "selected_count",
                        "changing_count",
                        "skipped_count",
                        "blocked_count",
                        "unresolved_count",
                        "remaining_review_count",
                        "decision_counts",
                    )
                }
                if internal.get("change_kind") == "reviewed_decisions"
                else {}
            ),
            "backup_id": backup_id,
            "change_kind": internal.get("change_kind", "remove_duplicates"),
            "change_count": internal.get("change_count", internal["removed_count"]),
            "removed_count": internal["removed_count"],
            "musical_positions": internal["musical_positions"],
            "item_name": internal["item_name"],
            "player_result": internal["player_result"],
            "user_value": internal["user_value"],
            "media": internal.get("media"),
            "performance": performance,
            "file_handling": result["file_handling"],
            **self._request_metadata(
                request_id,
                request_operation,
                request_fingerprint,
            ),
        })
        return result

    def _can_reuse_verified_before_report(
        self,
        report: dict | None,
        source_guard,
    ) -> bool:
        """Accept only a current-version Deep Audio report with a live guard."""
        return bool(
            isinstance(report, dict)
            and report.get("validator_version") == self._validator_version
            and isinstance(report.get("features"), dict)
            and report["features"].get("deep_audio_checked") is True
            and callable(source_guard)
        )

    @staticmethod
    def _reuse_unchanged_deep_audio(before: dict, after: dict) -> dict:
        """Carry forward Deep Audio facts when every audio byte is unchanged.

        Candidate archive verification separately checks the CRC and size of
        every untouched member. The standard candidate validation still
        reparses all changed song data; only media findings and Deep Audio
        coverage counters come from the signature-bound completed scan. This
        path is deliberately limited to archives; unpacked directory packages
        receive a fresh Deep Audio pass.
        """
        merged = copy.deepcopy(after)
        before_findings = before.get("findings")
        after_findings = after.get("findings")
        if not isinstance(before_findings, list) or not isinstance(
            after_findings, list
        ):
            return merged
        media_before = [
            copy.deepcopy(item)
            for item in before_findings
            if isinstance(item, dict)
            and str(item.get("code") or "").startswith("media.")
        ]
        non_media_after = [
            copy.deepcopy(item)
            for item in after_findings
            if not (
                isinstance(item, dict)
                and str(item.get("code") or "").startswith("media.")
            )
        ]
        merged["findings"] = non_media_after + media_before
        counts = {"error": 0, "warning": 0, "info": 0}
        for finding in merged["findings"]:
            severity = finding.get("severity") if isinstance(finding, dict) else None
            if severity in counts:
                counts[severity] += 1
        merged["counts"] = counts
        merged["status"] = (
            "error" if counts["error"]
            else "warning" if counts["warning"]
            else "review" if counts["info"]
            else "healthy"
        )
        before_features = before.get("features")
        merged_features = merged.get("features")
        if isinstance(before_features, dict) and isinstance(merged_features, dict):
            merged_features["deep_audio_checked"] = True
            merged_features["deep_audio_reused"] = True
            for key in (
                "deep_audio_files",
                "deep_audio_skipped",
                "deep_audio_unsupported",
            ):
                merged_features[key] = int(before_features.get(key) or 0)
        return merged

    def history(self, limit: int = 5) -> dict:
        """Return a small, non-sensitive repair receipt history for the UI."""
        safe_limit = max(1, min(int(limit), 20))
        with self._lock:
            self._reconcile_transactions()
            items = self._read_history()
            if not items:
                items = self._recover_legacy_receipts()
                if items:
                    self._write_history(items)
            pending = self._pending_transaction_receipts()
            pending_ids = {item["id"] for item in pending}
            items = [item for item in items if item.get("id") not in pending_ids]
            items.extend(pending)
            public_items = []
            for stored in reversed(items[-safe_limit:]):
                item = dict(stored)
                backup_id = item.get("backup_id")
                if isinstance(backup_id, str) and _BACKUP_ID_RE.fullmatch(backup_id):
                    backup_size = self._backup_size(backup_id)
                    retained = backup_size is not None
                    item["undo_available"] = bool(
                        retained
                        and item.get("action") == "repair"
                        and item.get("outcome") == "success"
                    )
                    handling = item.get("file_handling")
                    if isinstance(handling, dict):
                        item["file_handling"] = {
                            **handling,
                            "backup_retained": retained,
                            "backup_size_bytes": backup_size or 0,
                        }
                public_items.append(item)
            return {
                "schema": HISTORY_SCHEMA,
                "items": public_items,
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
        request_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Restore the song-data members saved before a successful repair.

        Restoration refuses to overwrite a package whose repaired members have
        changed since the backup was made.  Unrelated current package members
        are preserved.
        """
        with self._lock:
            _root, _package_path, package_name = self._resolve_package(package)
            pending_transaction = self._assert_package_mutation_allowed(
                package_name,
                operation="restore",
                backup_id=backup_id,
            )
            prepared = self._prepare_restore(
                package,
                backup_id,
                deep_audio=deep_audio,
            )
            try:
                transaction = None
                if prepared["_package_path"].is_dir():
                    transaction = self._begin_transaction(
                        prepared["package"],
                        backup_id,
                        operation="restore",
                        target_state="original",
                    )
                self._commit(
                    prepared["package"],
                    prepared["_package_path"],
                    prepared["_candidate"],
                    prepared["_originals"],
                    prepared["_current"],
                    source_token=prepared["_source_token"],
                    transaction=transaction,
                    operation="restore",
                )
            finally:
                prepared["_cleanup"]()

            backup_removed = False
            recovery_bytes_freed = 0
            try:
                recovery_bytes_freed = self._delete_backup(backup_id)
                backup_removed = True
            except RepairPlanningError as exc:
                # The exact original has already been restored and validated. A
                # failed cleanup must not misreport that successful package
                # transaction or encourage the user to run Undo a second time.
                self._log.warning(
                    "Library Doctor restored %s but could not remove redundant recovery backup %s: %s",
                    prepared["package"],
                    backup_id,
                    exc,
                )
            if pending_transaction is not None:
                self._finish_transaction(pending_transaction)

            result = {
                **self._public_restore_plan(prepared),
                "outcome": "restored",
                "restored": True,
                "undo_available": False,
                "change_kind": prepared["change_kind"],
                "change_count": prepared["change_count"],
                "restored_count": prepared["removed_count"],
                "report": prepared["_after"],
                "file_handling": {
                    "package_replaced": True,
                    "duplicate_library_package_created": False,
                    "backup_retained": not backup_removed,
                    "backup_removed": backup_removed,
                    "backup_cleanup_failed": not backup_removed,
                    "recovery_bytes_freed": recovery_bytes_freed,
                    "summary": (
                        "The generated preview was removed and the exact original manifest was restored at the same Feedpak path. "
                        "Every other package file was preserved, the redundant recovery copy was removed, "
                        "and no second song package was added."
                        if prepared["change_kind"] == "replace_media"
                        and bool((prepared.get("media") or {}).get("creates_preview"))
                        and backup_removed else
                        "The generated preview was removed and the exact original manifest was restored at the same Feedpak path. "
                        "Every other package file was preserved. The redundant recovery copy could not be removed automatically, "
                        "but it is no longer needed for Undo."
                        if prepared["change_kind"] == "replace_media"
                        and bool((prepared.get("media") or {}).get("creates_preview")) else
                        "The exact original preview was restored at the same Feedpak path. "
                        "Every other package file was preserved, the redundant recovery copy was removed, "
                        "and no second song package was added."
                        if prepared["change_kind"] == "replace_media" and backup_removed else
                        "The exact original preview was restored at the same Feedpak path. "
                        "Every other package file was preserved. The redundant recovery copy could not be removed automatically, "
                        "but it is no longer needed for Undo."
                        if prepared["change_kind"] == "replace_media" else
                        "The saved original song data was restored at the same Feedpak path. "
                        "The redundant recovery copy was removed, and no second song package was added."
                        if backup_removed else
                        "The saved original song data was restored at the same Feedpak path. "
                        "The redundant recovery copy could not be removed automatically, but it is no longer needed for Undo."
                    ),
                },
                **self._request_metadata(
                    request_id,
                    "repair.restore",
                    request_fingerprint,
                ),
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
                "returning_finding_codes": prepared["returning_finding_codes"],
                "returning_finding_count": prepared["returning_finding_count"],
                "backup_id": backup_id,
                "change_kind": prepared["change_kind"],
                "change_count": prepared["change_count"],
                "restored_count": prepared["removed_count"],
                "item_name": prepared["item_name"],
                "player_result": prepared["player_result"],
                "user_value": prepared["user_value"],
                "media": prepared.get("media"),
                "file_handling": result["file_handling"],
                **self._request_metadata(
                    request_id,
                    "repair.restore",
                    request_fingerprint,
                ),
            })
            return result

    def finalize_backup(
        self,
        package: str,
        backup_id: str,
        *,
        request_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict:
        """Remove a verified recovery copy without changing the Feedpak.

        A recovery copy can be finalized only while every affected package
        member still matches either the repaired bytes or the exact restored
        bytes recorded by that backup. This prevents cleanup from silently
        discarding the only known original after an unrelated edit.
        """
        with self._lock:
            _root, _package_path, package_name = self._resolve_package(package)
            pending_transaction = self._assert_package_mutation_allowed(
                package_name,
                operation="finalize",
                backup_id=backup_id,
            )
            prepared = self._prepare_finalize_backup(package, backup_id)
            recovery_bytes_freed = self._delete_backup(backup_id)
            if pending_transaction is not None:
                self._finish_transaction(pending_transaction)
            summary = prepared["summary"]
            package_state = prepared["package_state"]
            result = {
                "id": uuid.uuid4().hex,
                "action": "finalize",
                "outcome": "finalized",
                "completed_at": time.time(),
                "package": prepared["package"],
                "title": summary.get("title") or prepared["package"],
                "artist": summary.get("artist") or "",
                "rule_code": prepared["rule_code"],
                "rule_codes": prepared["rule_codes"],
                "backup_id": backup_id,
                "undo_available": False,
                "package_state": package_state,
                "change_kind": summary.get("change_kind", "repair"),
                "change_count": int(summary.get("change_count", 0) or 0),
                "removed_count": int(summary.get("removed_count", 0) or 0),
                "item_name": summary.get("item_name", "item"),
                "player_result": summary.get("player_result", ""),
                "user_value": summary.get("user_value", ""),
                "media": summary.get("media"),
                "file_handling": {
                    "package_replaced": False,
                    "duplicate_library_package_created": False,
                    "backup_retained": False,
                    "backup_removed": True,
                    "recovery_bytes_freed": recovery_bytes_freed,
                    "summary": (
                        "The repaired Feedpak was not changed. Its recovery copy was removed, so this repair can no longer be undone."
                        if package_state == "repaired" else
                        "The Feedpak was not changed. Its redundant recovery copy was removed because the original data is already restored."
                    ),
                },
                **self._request_metadata(
                    request_id,
                    "repair.finalize",
                    request_fingerprint,
                ),
            }
            result["receipt_saved"] = self._record_history(result)
            return result

    def preview_finalize_backup(self, package: str, backup_id: str) -> dict:
        """Verify one recovery copy and report what finalization would remove."""
        with self._lock:
            _root, _package_path, package_name = self._resolve_package(package)
            self._assert_package_mutation_allowed(
                package_name,
                operation="finalize",
                backup_id=backup_id,
            )
            prepared = self._prepare_finalize_backup(package, backup_id)
            return {
                key: value
                for key, value in prepared.items()
                if key != "summary"
            }

    def _prepare_finalize_backup(self, package: str, backup_id: str) -> dict:
        """Verify current member bytes before any irreversible backup removal."""
        if not isinstance(backup_id, str) or not _BACKUP_ID_RE.fullmatch(backup_id):
            raise RepairPlanningError("invalid_backup", "The recovery backup is invalid.")
        _root, package_path, package_name = self._resolve_package(package)
        metadata, _originals = self._read_backup(backup_id, package_name)
        states = set()
        verified_members = []
        for entry in metadata["members"]:
            member_path = entry["member_path"]
            present = self._member_exists(package_path, member_path)
            current_hash = None
            if present:
                current = self._read_member(
                    package_path,
                    member_path,
                    MAX_REPAIR_MEMBER_BYTES,
                )
                current_hash = hashlib.sha256(current).hexdigest()
            if (
                present == entry["repaired_present"]
                and current_hash == entry["repaired_sha256"]
            ):
                states.add("repaired")
            elif (
                present == entry["original_present"]
                and current_hash == entry["original_sha256"]
            ):
                states.add("restored")
            else:
                raise RepairPlanningError(
                    "package_changed",
                    "This song changed after the repair. Its recovery copy was kept so no saved original data is lost.",
                )
            verified_members.append({
                "member_path": member_path,
                "present": present,
                "sha256": current_hash,
            })
        if len(states) > 1:
            raise RepairPlanningError(
                "package_changed",
                "This song contains a mixture of repaired and original files. Its recovery copy was kept for manual review.",
            )

        summary = metadata.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        rule_codes = metadata.get("rule_codes")
        if not isinstance(rule_codes, list):
            rule_codes = []
        unsigned = {
            "schema": "library_doctor.finalize_plan.v1",
            "package": package_name,
            "backup_id": backup_id,
            "package_state": next(iter(states), "restored"),
            "members": verified_members,
        }
        return {
            **unsigned,
            "plan_id": _digest_json(unsigned),
            "rule_code": metadata.get("rule_code"),
            "rule_codes": rule_codes,
            "member_count": len(verified_members),
            "recovery_bytes": int(self._backup_size(backup_id) or 0),
            "summary": summary,
        }

    def preview_restore(
        self,
        package: str,
        backup_id: str,
        *,
        deep_audio: bool = False,
    ) -> dict:
        """Verify that one retained recovery backup can be restored now."""
        with self._lock:
            _root, _package_path, package_name = self._resolve_package(package)
            self._assert_package_mutation_allowed(
                package_name,
                operation="restore",
                backup_id=backup_id,
            )
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
            present = self._member_exists(package_path, member_path)
            raw = (
                self._read_member(package_path, member_path, MAX_REPAIR_MEMBER_BYTES)
                if present else None
            )
            current_hash = hashlib.sha256(raw).hexdigest() if raw is not None else None
            repaired_match = (
                present == entry["repaired_present"]
                and current_hash == entry["repaired_sha256"]
            )
            original_match = (
                present == entry["original_present"]
                and current_hash == entry["original_sha256"]
            )
            if not (repaired_match or original_match):
                raise RepairPlanningError(
                    "package_changed",
                    "This song changed after the repair, so Library Doctor will not overwrite it. Scan it again and review it manually.",
                )
            current[member_path] = raw
            source_members.append({
                "member_path": member_path,
                "repaired_sha256": current_hash,
                "original_sha256": entry["original_sha256"],
                "repaired_present": entry["repaired_present"],
                "original_present": entry["original_present"],
            })

        before = self._validate_feedpak(
            package_path, package_name, deep_audio=bool(deep_audio)
        )
        candidate, cleanup = self._candidate(package_path, originals)
        source_token = self._capture_package_token(package_path)
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
            # Undo deliberately restores the exact pre-repair bytes, including
            # every finding those bytes originally produced. One safe repair can
            # remove secondary findings as a consequence (for example, deleting
            # an exact duplicate beat can also remove an out-of-order warning).
            # Those related findings must be allowed to return. Safety comes
            # from the verified backup hashes, exact current repaired-member
            # hashes, full candidate construction/integrity checks, validation,
            # and atomic commit. It does not come from requiring the original
            # to be as healthy as its repaired replacement.
            returning_finding_codes = sorted(
                _report_codes(after) - _report_codes(before)
            )

            backup_summary = metadata.get("summary")
            if not isinstance(backup_summary, dict):
                backup_summary = {}
            combined_repair = rule_code == ALL_SAFE_RULE_CODE
            media_repair = backup_summary.get("change_kind") == "replace_media"
            media_summary = backup_summary.get("media")
            created_preview = bool(
                isinstance(media_summary, dict)
                and media_summary.get("creates_preview")
            )
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
                "returning_finding_codes": returning_finding_codes,
                "returning_finding_count": len(returning_finding_codes),
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
                    "After Undo, the generated preview is removed and library browsing returns to having no embedded preview for this Feedpak."
                    if media_repair and created_preview else
                    "After Undo, library browsing uses the exact original preview again; the repaired preview recommendation is expected to return."
                    if media_repair else
                    "After Undo, the package contains all original song data again; "
                    "the repaired findings and related findings may return in the refreshed report."
                    if combined_repair else
                    "After Undo, the package contains the original song data again; "
                    "the repaired finding and related findings may return in the refreshed report."
                ),
                "user_value": (
                    "This removes the newly created preview and restores the exact manifest that existed before the repair."
                    if media_repair and created_preview else
                    "This restores the exact preview saved before audio conversion if the proposed excerpt did not represent the song well."
                    if media_repair else
                    "This returns the entire combined repair to its exact saved starting point if the song did not behave as expected."
                    if combined_repair else
                    "This returns the song to the exact data saved before the repair if the repaired song did not behave as expected."
                ),
                "media": media_summary,
                "file_handling": (
                    "The generated preview will be removed and the exact saved manifest will be restored at the same Feedpak path. "
                    "Other package members are preserved. After validation, the redundant recovery copy is removed."
                    if media_repair and created_preview else
                    "The saved original preview will replace only the repaired preview at the same Feedpak path. "
                    "Other package members are preserved. After the original is restored and validated, the redundant recovery copy is removed."
                    if media_repair else
                    "The saved original song-data files will replace only the repaired files at the same Feedpak path. "
                    "Other package members are preserved. After the original is restored and validated, the redundant recovery copy is removed."
                ),
                "_package_path": package_path,
                "_candidate": candidate,
                "_cleanup": cleanup,
                "_originals": originals,
                "_current": current,
                "_source_token": source_token,
                "_after": after,
            }
        except Exception:
            cleanup()
            raise

    @staticmethod
    def _public_restore_plan(prepared: dict) -> dict:
        return {key: value for key, value in prepared.items() if not key.startswith("_")}

    def _emit_transaction_barrier(self, name: str, **context) -> None:
        """Expose stable fault-injection points without affecting production."""
        if self._transaction_barrier is not None:
            self._transaction_barrier(name, dict(context))

    @staticmethod
    def _capture_package_token(package_path: Path) -> dict:
        """Bind a commit to the same package object and, for archives, bytes."""
        try:
            stat = package_path.stat()
            token = {
                "kind": "directory" if package_path.is_dir() else "archive",
                "device": int(stat.st_dev),
                "inode": int(stat.st_ino),
            }
            if package_path.is_file():
                digest = hashlib.sha256()
                with package_path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                token["sha256"] = digest.hexdigest()
            return token
        except OSError as exc:
            raise RepairPlanningError(
                "source_changed",
                "The selected package changed before it could be saved. Nothing was overwritten.",
            ) from exc

    def _assert_source_state(
        self,
        package_name: str,
        package_path: Path,
        originals: dict[str, bytes | None],
        source_token: dict | None,
    ) -> None:
        """Recheck containment, identity, and affected bytes at commit time."""
        self._assert_package_identity(package_name, package_path, source_token)
        for member_path, raw in originals.items():
            self._assert_member_state(package_path, member_path, raw)

    def _assert_package_identity(
        self,
        package_name: str,
        package_path: Path,
        source_token: dict | None,
    ) -> None:
        _root, current_path, current_name = self._resolve_package(package_name)
        if current_name != package_name or current_path != package_path:
            raise RepairPlanningError(
                "source_changed",
                "The selected package moved or changed before it could be saved. Nothing was overwritten.",
            )
        if source_token is not None:
            current_token = self._capture_package_token(package_path)
            expected_identity = (
                source_token.get("kind"),
                source_token.get("device"),
                source_token.get("inode"),
            )
            current_identity = (
                current_token.get("kind"),
                current_token.get("device"),
                current_token.get("inode"),
            )
            if expected_identity != current_identity or (
                source_token.get("kind") == "archive"
                and source_token.get("sha256") != current_token.get("sha256")
            ):
                raise RepairPlanningError(
                    "source_changed",
                    "The selected package changed while its candidate was being checked. Nothing was overwritten.",
                )

    def _assert_member_state(
        self,
        package_path: Path,
        member_path: str,
        expected: bytes | None,
    ) -> None:
        present = self._member_exists(package_path, member_path)
        if expected is None:
            matches = not present
        elif not present:
            matches = False
        else:
            current = self._read_member(
                package_path, member_path, MAX_REPAIR_MEMBER_BYTES
            )
            matches = hashlib.sha256(current).digest() == hashlib.sha256(expected).digest()
        if not matches:
            raise RepairPlanningError(
                "source_changed",
                "A song file changed while the repaired candidate was being checked. The newer file was not overwritten.",
            )

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
                "The selected package is unavailable or outside the current scan target.",
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
        rule_code: str | None = None,
        parsed_json_cache: dict[
            str, tuple[str, object | None, str | None]
        ] | None = None,
    ) -> list[str]:
        """Resolve the active files for one repair role.

        Tempo and meter selection mirrors FeedBack's independent 1.2 timeline
        streams, while beat/section selection retains the complete-sidecar and
        first-legacy-grid behavior. Tone rules select only effective inline
        arrangement JSON; effective manifest tone issues fail closed because
        this repair engine cannot rewrite YAML losslessly.
        """
        def load_json(
            member_path: str,
        ) -> tuple[str, object | None, str | None]:
            if parsed_json_cache is not None and member_path in parsed_json_cache:
                return parsed_json_cache[member_path]
            try:
                raw = self._read_member(
                    package_path, member_path, MAX_REPAIR_TEXT_BYTES
                )
            except RepairPlanningError:
                result = ("unavailable", None, None)
            else:
                source_sha256 = hashlib.sha256(raw).hexdigest()
                try:
                    data = _parse_json(raw)
                    _inspect_structure(data)
                except RepairPlanningError:
                    result = ("invalid", None, source_sha256)
                else:
                    result = ("valid", data, source_sha256)
            if parsed_json_cache is not None:
                parsed_json_cache[member_path] = result
            return result

        if rule_code in {"tones.duplicate-change", "tones.changes-out-of-order"}:
            top_level_tones = manifest.get("drum_tones")
            if _manifest_tone_rule_present(top_level_tones, rule_code):
                raise RepairPlanningError(
                    "manifest_tones_require_manual_edit",
                    "This tone issue is stored in manifest.yaml, which cannot be rewritten losslessly yet.",
                )
            member_paths = []
            seen = set()
            for entry in manifest["arrangements"]:
                if not isinstance(entry, dict):
                    continue
                tone_source, manifest_tones = effective_tones_source(
                    entry.get("tones"), None
                )
                if tone_source == "manifest":
                    if _manifest_tone_rule_present(manifest_tones, rule_code):
                        raise RepairPlanningError(
                            "manifest_tones_require_manual_edit",
                            "This tone issue is stored in manifest.yaml, which cannot be rewritten losslessly yet.",
                        )
                    continue
                member = entry.get("file")
                if not isinstance(member, str) or not member:
                    continue
                try:
                    member_path = _validate_member_path(member)
                except RepairPlanningError:
                    continue
                if member_path not in seen:
                    seen.add(member_path)
                    member_paths.append(member_path)
            return member_paths

        if source_kind != "timeline":
            return self._repair_member_paths(manifest, source_kind)

        if rule_code in {
            "timeline.duplicate-tempo",
            "timeline.tempos-out-of-order",
            "timeline.duplicate-time-signature",
            "timeline.time-signatures-out-of-order",
        }:
            field = (
                "tempos"
                if rule_code in {
                    "timeline.duplicate-tempo",
                    "timeline.tempos-out-of-order",
                }
                else "time_signatures"
            )
            member_paths = []
            seen = set()

            def include_if_relevant(member: str) -> None:
                try:
                    member_path = _validate_member_path(member)
                except RepairPlanningError:
                    return
                if member_path in seen:
                    return
                if member_path.lower().endswith(".jsonc"):
                    seen.add(member_path)
                    member_paths.append(member_path)
                    return
                status, data, _source_sha256 = load_json(member_path)
                if status == "unavailable":
                    return
                if status == "invalid":
                    seen.add(member_path)
                    member_paths.append(member_path)
                    return
                values = data.get(field) if isinstance(data, dict) else None
                if isinstance(values, list) and values:
                    seen.add(member_path)
                    member_paths.append(member_path)

            if field == "tempos":
                for entry in manifest["arrangements"]:
                    member = entry.get("file") if isinstance(entry, dict) else None
                    if isinstance(member, str) and member:
                        include_if_relevant(member)
            declared = manifest.get("song_timeline")
            if isinstance(declared, str) and declared:
                include_if_relevant(declared)
            return member_paths

        timeline_field = {
            "timeline.duplicate-beat": "beats",
            "timeline.beats-out-of-order": "beats",
            "timeline.duplicate-section": "sections",
            "timeline.sections-out-of-order": "sections",
        }.get(rule_code, "beats")

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
                status, data, _source_sha256 = load_json(member_path)
                if status == "unavailable":
                    # An unavailable or oversized sidecar cannot override the
                    # legacy grid in FeedBack, so continue to its fallback.
                    data = None
                elif status == "invalid":
                    # The validator may be more permissive about a value
                    # (for example duplicate keys or non-finite numbers).
                    # Refuse this declared sidecar rather than risk editing
                    # a legacy grid that FeedBack could consider inactive.
                    return [member_path]
                else:
                    if (
                        isinstance(data, dict)
                        and isinstance(data.get("beats"), list)
                        and isinstance(data.get("sections"), list)
                    ):
                        return [member_path]

        # FeedBack's legacy fallback takes the first non-empty array of each
        # timeline type from arrangement order. Once found, later embedded
        # grids of that type are inactive.
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
            status, data, _source_sha256 = load_json(member_path)
            if status == "unavailable":
                continue
            if status == "invalid":
                # Do not skip past a readable arrangement whose permissively
                # parsed contents could be FeedBack's active legacy grid.
                return [member_path]
            if (
                isinstance(data, dict)
                and isinstance(data.get(timeline_field), list)
                and data[timeline_field]
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
        planned = []
        blockers = []
        try:
            member_paths = self._resolved_repair_member_paths(
                package_path, manifest, definition["source_kind"], rule_code
            )
        except RepairPlanningError as exc:
            member_paths = []
            blockers.append({
                "member_path": "manifest.yaml",
                "code": exc.code,
                "message": str(exc),
            })
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
                planned.append({
                    "member_path": member_path,
                    "raw": raw,
                    "plan": plan,
                    "source_kind": definition["source_kind"],
                })

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

    def _reviewed_member_documents(
        self,
        package_path: Path,
        manifest: dict,
    ) -> list[tuple[str, bytes, dict]]:
        documents = []
        for member_path in self._resolved_repair_member_paths(
            package_path,
            manifest,
            "arrangement",
        ):
            raw = self._read_member(
                package_path, member_path, MAX_REPAIR_TEXT_BYTES
            )
            safe_member_path = _validate_member_path(member_path)
            if safe_member_path.lower().endswith(".jsonc"):
                raise RepairPlanningError(
                    "jsonc_requires_lossless_writer",
                    "Commented JSON can be inspected by the scanner but cannot be changed until comments can be preserved.",
                )
            if not safe_member_path.lower().endswith(".json"):
                raise RepairPlanningError(
                    "unsupported_text_format",
                    "Reviewed song-data repairs currently require ordinary JSON arrangement files.",
                )
            document = _parse_json(raw)
            _inspect_structure(document)
            if not isinstance(document, dict):
                raise RepairPlanningError(
                    "invalid_document_shape",
                    "An arrangement does not have the expected JSON structure for Reviewed repair.",
                )
            documents.append((safe_member_path, raw, document))
        return sorted(documents, key=lambda item: item[0])

    def _inspect_reviewed_package(
        self,
        package_path: Path,
        package_name: str,
        adapter_id: str,
        *,
        difficulty_scope: str,
        offset: int,
        limit: int | None,
    ) -> dict:
        try:
            definition = _reviewed.reviewed_repair_definition(adapter_id)
        except ValueError as exc:
            raise RepairPlanningError(
                "unsupported_reviewed_repair",
                "This Reviewed repair adapter is not supported.",
            ) from exc
        difficulty_scope = _reviewed_difficulty_scope(difficulty_scope)
        if not _integer(offset) or offset < 0:
            raise RepairPlanningError(
                "invalid_review_page", "The reviewed-repair page offset is invalid."
            )
        page_limit = definition.candidate_limit if limit is None else limit
        if (
            not _integer(page_limit)
            or page_limit < 1
            or page_limit > definition.candidate_limit
        ):
            raise RepairPlanningError(
                "invalid_review_page", "The reviewed-repair page size is invalid."
            )
        manifest = self._read_repair_manifest(package_path)
        candidates = []
        member_sources = []
        total_candidate_count = 0
        blocked_count = 0
        full_candidate_count = 0
        lower_candidate_count = 0
        remaining_offset = offset
        remaining_limit = page_limit
        for member_path, raw, document in self._reviewed_member_documents(
            package_path, manifest
        ):
            member_page = definition.inspect_page_document(
                document,
                member_path=member_path,
                offset=remaining_offset,
                limit=max(remaining_limit, 1),
                difficulty_scope=difficulty_scope,
            )
            if remaining_limit:
                page_items = member_page.candidates[:remaining_limit]
                candidates.extend(candidate.to_dict() for candidate in page_items)
                remaining_limit -= len(page_items)
            remaining_offset = max(0, remaining_offset - member_page.total_count)
            total_candidate_count += member_page.total_count
            blocked_count += member_page.blocked_count
            full_candidate_count += member_page.full_candidate_count
            lower_candidate_count += member_page.lower_candidate_count
            member_sources.append({
                "member_path": member_path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
                "candidate_count": member_page.total_count,
                "full_candidate_count": member_page.full_candidate_count,
                "lower_candidate_count": member_page.lower_candidate_count,
            })
        page_count = len(candidates)
        omitted_candidate_count = max(0, total_candidate_count - page_count)
        has_previous = offset > 0
        has_next = offset + page_count < total_candidate_count
        unsigned = {
            "schema": REVIEWED_INSPECTION_SCHEMA,
            "catalog_version": REPAIR_CATALOG_VERSION,
            "reviewed_registry_version": _reviewed.REVIEWED_REPAIR_REGISTRY_VERSION,
            "validator_version": self._validator_version,
            "package": package_name,
            "adapter_id": adapter_id,
            "difficulty_scope": difficulty_scope,
            "offset": offset,
            "limit": page_limit,
            "member_sources": member_sources,
            "candidates": candidates,
        }
        return {
            **unsigned,
            "inspection_id": _digest_json(unsigned),
            "title": definition.title,
            "description": definition.description,
            "safety": "review_required",
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidate_count,
            "full_candidate_count": full_candidate_count,
            "lower_candidate_count": lower_candidate_count,
            "hidden_lower_candidate_count": (
                lower_candidate_count if difficulty_scope == "full_only" else 0
            ),
            "omitted_candidate_count": omitted_candidate_count,
            "offset": offset,
            "limit": page_limit,
            "has_previous": has_previous,
            "has_next": has_next,
            "previous_offset": max(0, offset - page_limit) if has_previous else None,
            "next_offset": offset + page_count if has_next else None,
            "inspection_blocker": (
                f"Showing candidates {offset + 1}-{offset + page_count} of {total_candidate_count} in one bounded page. Use the page controls to review the rest."
                if has_previous or has_next else ""
            ),
            "blocked_count": blocked_count,
            "page_blocked_count": sum(
                bool(item["blockers"]) for item in candidates
            ),
            "decision_definitions": [
                decision.to_dict() for decision in definition.decisions
            ],
            "available": bool(candidates),
        }

    @staticmethod
    def _reviewed_validation_contexts(
        manifest: dict,
        member_path: str,
    ) -> list[dict]:
        """Return deterministic manifest context for one arrangement member."""
        raw_duration = manifest.get("duration") if isinstance(manifest, dict) else None
        duration = float(raw_duration) if _finite_number(raw_duration) else None
        contexts = []
        entries = manifest.get("arrangements") if isinstance(manifest, dict) else None
        if isinstance(entries, list):
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                raw_member = entry.get("file")
                if not isinstance(raw_member, str):
                    continue
                try:
                    declared = _validate_member_path(raw_member)
                except RepairPlanningError:
                    continue
                if declared != member_path:
                    continue
                entry_context = {
                    key: entry[key]
                    for key in ("id", "name", "type")
                    if isinstance(entry.get(key), str)
                }
                if isinstance(entry.get("tuning"), list):
                    entry_context["tuning"] = copy.deepcopy(entry["tuning"])
                if _integer(entry.get("capo")):
                    entry_context["capo"] = entry["capo"]
                contexts.append({
                    "arrangement_id": str(entry.get("id") or f"#{index + 1}"),
                    "duration": duration,
                    "entry": entry_context,
                })
        if not contexts:
            contexts.append({
                "arrangement_id": "",
                "duration": duration,
                "entry": None,
            })
        return contexts

    def _reviewed_validation_reports(
        self,
        document: dict,
        manifest: dict,
        member_path: str,
    ) -> list[dict]:
        if self._validate_reviewed_arrangement is None:
            return []
        reports = []
        try:
            for context in self._reviewed_validation_contexts(
                manifest, member_path
            ):
                report = self._validate_reviewed_arrangement(
                    document,
                    relpath=member_path,
                    arrangement_id=context["arrangement_id"],
                    duration=context["duration"],
                    entry=context["entry"],
                )
                if not isinstance(report, dict):
                    raise TypeError("invalid reviewed validation report")
                reports.append(report)
        except RepairPlanningError:
            raise
        except Exception as exc:
            raise RepairPlanningError(
                "reviewed_validation_failed",
                "Library Doctor could not validate this proposed reviewed choice, so it was not offered.",
            ) from exc
        return reports

    @staticmethod
    def _reviewed_finding_counts(reports: list[dict]) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for report in reports:
            findings = report.get("findings") if isinstance(report, dict) else None
            if not isinstance(findings, list):
                continue
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                severity = finding.get("severity")
                code = finding.get("code")
                if not isinstance(code, str):
                    continue
                if not isinstance(severity, str):
                    severity = ""
                affected = finding.get("affected_count", 1)
                if not _integer(affected) or affected < 1:
                    affected = 1
                key = (severity, code)
                counts[key] = counts.get(key, 0) + affected
        return counts

    @staticmethod
    def _reviewed_path_state(
        definition,
        document: dict,
        member_path: str,
        paths: set[tuple[str | int, ...]],
    ) -> dict[str, frozenset[str]]:
        selection = definition.select_paths_document(
            document,
            member_path=member_path,
            target_paths=frozenset(paths),
            difficulty_scope="all_authored",
        )
        state: dict[str, set[str]] = {}
        for item in selection.candidates:
            state.setdefault(item.review_item_id, set()).update(
                item.trigger_codes
            )
        return {
            review_item_id: frozenset(codes)
            for review_item_id, codes in state.items()
        }

    def _evaluate_reviewed_decision(
        self,
        document: dict,
        manifest: dict,
        member_path: str,
        definition,
        candidate,
        decision: str,
        *,
        before_reports: list[dict] | None = None,
    ) -> dict:
        """Simulate one decision and return a bounded, user-safe outcome."""
        try:
            operation = _reviewed_operation(
                document, definition, candidate, decision
            )
        except RepairPlanningError as exc:
            return {
                "allowed": False,
                "code": exc.code,
                "message": str(exc),
            }
        if operation is None or not operation.get("changes"):
            return {
                "allowed": False,
                "code": "no_effect",
                "message": "This choice would not change the stored note.",
            }

        touched_paths = {
            tuple(change["target_path"])
            for change in operation["changes"]
        }
        before_state = self._reviewed_path_state(
            definition, document, member_path, touched_paths
        )
        mutated = copy.deepcopy(document)
        try:
            _apply_reviewed_operation(
                mutated,
                operation,
                set(),
                allowed_fields=frozenset(definition.mutable_fields),
            )
        except RepairPlanningError as exc:
            return {
                "allowed": False,
                "code": exc.code,
                "message": str(exc),
            }
        if _canonical_json(mutated) == _canonical_json(document):
            return {
                "allowed": False,
                "code": "no_effect",
                "message": "This choice would not change the stored note.",
            }

        after_state = self._reviewed_path_state(
            definition, mutated, member_path, touched_paths
        )
        selected_item_id = candidate.review_item_id
        if selected_item_id in after_state:
            return {
                "allowed": False,
                "code": "issue_remains",
                "message": "This choice would leave or recreate the current review issue.",
            }
        introduced = sorted(set(after_state) - set(before_state))
        if introduced:
            return {
                "allowed": False,
                "code": "introduces_review",
                "message": "This choice would create another HO/PO issue for review.",
            }
        expanded = sorted(
            review_item_id
            for review_item_id in set(after_state).intersection(before_state)
            if not after_state[review_item_id].issubset(
                before_state[review_item_id]
            )
        )
        if expanded:
            return {
                "allowed": False,
                "code": "introduces_review",
                "message": "This choice would add another reason for manual review.",
            }

        if before_reports is None:
            before_reports = self._reviewed_validation_reports(
                document, manifest, member_path
            )
        after_reports = self._reviewed_validation_reports(
            mutated, manifest, member_path
        )
        before_counts = self._reviewed_finding_counts(before_reports)
        after_counts = self._reviewed_finding_counts(after_reports)
        increases = sorted(
            code
            for (severity, code), count in after_counts.items()
            if count > before_counts.get((severity, code), 0)
        )
        if increases:
            return {
                "allowed": False,
                "code": "introduces_validation",
                "message": "This choice would introduce another validation or review finding.",
            }
        return {
            "allowed": True,
            "code": "validated_outcome",
            "message": "This choice resolves the issue without introducing another finding.",
            "operation": operation,
            "touched_paths": touched_paths,
            "document": mutated,
            "before_state": before_state,
            "after_state": after_state,
        }

    def _reviewed_options_package(
        self,
        package_path: Path,
        package_name: str,
        adapter_id: str,
        candidate_id: str,
        *,
        difficulty_scope: str,
    ) -> dict:
        try:
            definition = _reviewed.reviewed_repair_definition(adapter_id)
        except ValueError as exc:
            raise RepairPlanningError(
                "unsupported_reviewed_repair",
                "This Reviewed repair adapter is not supported.",
            ) from exc
        difficulty_scope = _reviewed_difficulty_scope(difficulty_scope)
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.startswith(f"{definition.candidate_id_prefix}-")
            or len(candidate_id) != len(definition.candidate_id_prefix) + 25
        ):
            raise RepairPlanningError(
                "invalid_candidate", "The reviewed note identifier is invalid."
            )

        manifest = self._read_repair_manifest(package_path)
        selected = []
        for member_path, raw, document in self._reviewed_member_documents(
            package_path, manifest
        ):
            selection = definition.select_document(
                document,
                member_path=member_path,
                candidate_ids=frozenset({candidate_id}),
                difficulty_scope=difficulty_scope,
            )
            for candidate in selection.candidates:
                selected.append((member_path, raw, document, candidate))
        if len(selected) != 1:
            raise RepairPlanningError(
                "candidate_changed",
                "This reviewed note changed or is no longer questionable. Refresh the review queue.",
            )
        member_path, raw, document, candidate = selected[0]
        source_sha256 = hashlib.sha256(raw).hexdigest()
        contexts = self._reviewed_validation_contexts(manifest, member_path)
        context_sha256 = hashlib.sha256(
            _canonical_json(contexts)
        ).hexdigest()
        try:
            package_stat = package_path.stat()
            package_cache_token = (
                "directory" if package_path.is_dir() else "archive",
                int(package_stat.st_dev),
                int(package_stat.st_ino),
                int(package_stat.st_size),
                int(package_stat.st_mtime_ns),
            )
        except OSError as exc:
            raise RepairPlanningError(
                "source_changed",
                "This package changed while its reviewed choices were being checked.",
            ) from exc
        cache_key = (
            package_name,
            package_cache_token,
            adapter_id,
            difficulty_scope,
            candidate_id,
            member_path,
            source_sha256,
            context_sha256,
            self._validator_version,
            _reviewed.REVIEWED_REPAIR_REGISTRY_VERSION,
        )
        cached = self._reviewed_options_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

        definitions = {
            item.name: item for item in definition.decisions
        }
        offered = []
        omitted = []
        blocked = bool(candidate.blockers)
        before_reports = (
            []
            if blocked
            else self._reviewed_validation_reports(
                document, manifest, member_path
            )
        )
        for name in candidate.decision_names:
            decision_definition = definitions.get(name)
            if decision_definition is None:
                continue
            if blocked:
                outcome = {
                    "allowed": False,
                    "code": "candidate_blocked",
                    "message": "Conflicting or malformed source data prevents a safe Library Doctor change.",
                }
            else:
                outcome = self._evaluate_reviewed_decision(
                    document,
                    manifest,
                    member_path,
                    definition,
                    candidate,
                    name,
                    before_reports=before_reports,
                )
            if outcome["allowed"]:
                offered.append(decision_definition.to_dict())
            else:
                omitted.append({
                    "name": name,
                    "code": str(outcome.get("code") or "outcome_rejected"),
                    "message": str(outcome.get("message") or (
                        "This choice did not pass Library Doctor's outcome checks."
                    )),
                })

        if offered:
            message = (
                "Only choices that resolve this issue without creating another finding are shown."
            )
        elif blocked:
            message = (
                "This issue cannot be changed safely here because its source evidence is conflicting or malformed. Skip it for now."
            )
        else:
            message = (
                "None of Library Doctor's bounded choices resolves this issue safely. Skip it for now and edit the source manually if needed."
            )
        unsigned = {
            "schema": REVIEWED_OPTIONS_SCHEMA,
            "package": package_name,
            "adapter_id": adapter_id,
            "difficulty_scope": difficulty_scope,
            "candidate_id": candidate.candidate_id,
            "review_item_id": candidate.review_item_id,
            "source": {
                "member_path": member_path,
                "sha256": source_sha256,
                "context_sha256": context_sha256,
                "byte_count": len(raw),
            },
            "validator_version": self._validator_version,
            "reviewed_registry_version": _reviewed.REVIEWED_REPAIR_REGISTRY_VERSION,
            "decision_names": [item["name"] for item in offered],
            "decision_definitions": offered,
            "omitted_decisions": omitted,
            "available": bool(offered),
            "blocked": blocked,
            "message": message,
        }
        result = {**unsigned, "options_id": _digest_json(unsigned)}
        if len(self._reviewed_options_cache) >= MAX_REVIEWED_OPTIONS_CACHE:
            self._reviewed_options_cache.pop(
                next(iter(self._reviewed_options_cache)), None
            )
        self._reviewed_options_cache[cache_key] = copy.deepcopy(result)
        return result

    def _plan_reviewed_package(
        self,
        package_path: Path,
        package_name: str,
        adapter_id: str,
        decisions: list[dict],
        *,
        difficulty_scope: str,
    ) -> dict:
        try:
            definition = _reviewed.reviewed_repair_definition(adapter_id)
        except ValueError as exc:
            raise RepairPlanningError(
                "unsupported_reviewed_repair",
                "This Reviewed repair adapter is not supported.",
            ) from exc
        difficulty_scope = _reviewed_difficulty_scope(difficulty_scope)
        requested = _reviewed_decision_items(decisions, definition)
        requested_by_id = dict(requested)
        manifest = self._read_repair_manifest(package_path)
        planned = []
        seen_candidates = set()
        decision_summaries = []
        outcome_members = []
        total_candidate_count = 0
        blocked_candidate_count = 0
        remaining_review_count = 0
        for member_path, raw, document in self._reviewed_member_documents(
            package_path, manifest
        ):
            member_selection = definition.select_document(
                document,
                member_path=member_path,
                candidate_ids=frozenset(requested_by_id),
                difficulty_scope=difficulty_scope,
            )
            member_candidates = member_selection.candidates
            member_ids = {
                candidate.candidate_id for candidate in member_candidates
            }
            total_candidate_count += member_selection.total_count
            blocked_candidate_count += member_selection.blocked_count
            member_decisions = [
                {"candidate_id": candidate_id, "decision": decision}
                for candidate_id, decision in requested
                if candidate_id in member_ids
            ]
            seen_candidates.update(member_ids.intersection(requested_by_id))
            if not member_decisions:
                remaining_review_count += member_selection.total_count
                continue
            candidates_by_id = {
                candidate.candidate_id: candidate
                for candidate in member_candidates
            }
            before_reports = self._reviewed_validation_reports(
                document, manifest, member_path
            )
            for item in member_decisions:
                candidate = candidates_by_id[item["candidate_id"]]
                outcome = self._evaluate_reviewed_decision(
                    document,
                    manifest,
                    member_path,
                    definition,
                    candidate,
                    item["decision"],
                    before_reports=before_reports,
                )
                if not outcome["allowed"]:
                    raise RepairPlanningError(
                        "decision_outcome_rejected",
                        str(outcome.get("message") or (
                            "This reviewed choice no longer produces a safe outcome. Inspect the issue again."
                        )),
                    )
            plan = plan_reviewed_json_member(
                raw,
                member_path=member_path,
                adapter_id=adapter_id,
                validator_version=self._validator_version,
                decisions=member_decisions,
                difficulty_scope=difficulty_scope,
            )
            changed = bool(plan["actions"])
            decision_summaries.extend(member_decisions)
            if changed:
                replacement = apply_json_member(raw, plan)
                repaired_document = _parse_json(replacement)
                _inspect_structure(repaired_document)
                touched_paths = {
                    tuple(change["target_path"])
                    for action in plan["actions"]
                    for operation in action["operations"]
                    for change in operation["changes"]
                }
                before_state = self._reviewed_path_state(
                    definition, document, member_path, touched_paths
                )
                after_state = self._reviewed_path_state(
                    definition, repaired_document, member_path, touched_paths
                )
                selected_item_ids = {
                    candidates_by_id[item["candidate_id"]].review_item_id
                    for item in member_decisions
                }
                retained = sorted(selected_item_ids.intersection(after_state))
                introduced = sorted(set(after_state) - set(before_state))
                expanded = sorted(
                    review_item_id
                    for review_item_id in set(after_state).intersection(before_state)
                    if not after_state[review_item_id].issubset(
                        before_state[review_item_id]
                    )
                )
                if retained:
                    raise RepairPlanningError(
                        "reviewed_issue_remains",
                        "The complete selected group would leave or recreate a selected review issue.",
                    )
                if introduced or expanded:
                    raise RepairPlanningError(
                        "reviewed_group_introduces_issue",
                        "The complete selected group would introduce another issue for review.",
                    )
                after_reports = self._reviewed_validation_reports(
                    repaired_document, manifest, member_path
                )
                before_counts = self._reviewed_finding_counts(before_reports)
                after_counts = self._reviewed_finding_counts(after_reports)
                if any(
                    count > before_counts.get(key, 0)
                    for key, count in after_counts.items()
                ):
                    raise RepairPlanningError(
                        "reviewed_group_introduces_finding",
                        "The complete selected group would introduce another validation or review finding.",
                    )
                remaining_review_count += definition.inspect_page_document(
                    repaired_document,
                    member_path=member_path,
                    offset=0,
                    limit=1,
                    difficulty_scope=difficulty_scope,
                ).total_count
                planned.append({
                    "member_path": member_path,
                    "raw": raw,
                    "plan": plan,
                    "source_kind": "arrangement",
                })
                outcome_members.append({
                    "member_path": member_path,
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "selected_review_item_ids": sorted(selected_item_ids),
                    "before_touched_review_count": len(before_state),
                    "after_touched_review_count": len(after_state),
                })
            else:
                raise RepairPlanningError(
                    "decision_no_effect",
                    "No selected reviewed choice changes this arrangement.",
                )
        missing = [
            candidate_id
            for candidate_id, _decision in requested
            if candidate_id not in seen_candidates
        ]
        if missing:
            raise RepairPlanningError(
                "candidate_changed",
                "A reviewed note changed or is no longer questionable. Inspect the package again.",
            )
        change_count = sum(
            action["change_count"]
            for item in planned
            for action in item["plan"]["actions"]
        )
        decision_counts = {
            decision.name: sum(
                selected == decision.name
                for _candidate_id, selected in requested
            )
            for decision in definition.decisions
        }
        unsigned = {
            "schema": REVIEWED_PACKAGE_PLAN_SCHEMA,
            "catalog_version": REPAIR_CATALOG_VERSION,
            "reviewed_registry_version": _reviewed.REVIEWED_REPAIR_REGISTRY_VERSION,
            "validator_version": self._validator_version,
            "package": package_name,
            "rule_code": adapter_id,
            "rule_codes": list(definition.trigger_rule_codes),
            "adapter_id": adapter_id,
            "difficulty_scope": difficulty_scope,
            "decisions": decision_summaries,
            "member_plans": [
                {
                    "member_path": item["member_path"],
                    "plan_id": item["plan"]["plan_id"],
                }
                for item in planned
            ],
            "outcome_digest": _digest_json({
                "members": outcome_members,
                "remaining_review_count": remaining_review_count,
            }),
            "blockers": [],
        }
        return {
            **unsigned,
            "plan_id": _digest_json(unsigned),
            "available": bool(planned),
            "title": definition.title,
            "description": definition.description,
            "safety": "review_required",
            "player_result": (
                "Only the HO/PO/tap fields selected in Reviewed repair are changed; note timing, fret, string, and every other technique are preserved."
            ),
            "user_value": (
                "Ambiguous tab can be corrected inside Library Doctor with an explicit author choice, complete validation, recovery backup, and Undo."
            ),
            "file_handling": self._file_handling(None),
            "item_name": "reviewed HO/PO decision",
            "change_kind": "reviewed_decisions",
            "change_count": change_count,
            "candidate_count": total_candidate_count,
            "selected_count": len(requested),
            "changing_count": len(requested),
            "skipped_count": 0,
            "blocked_count": blocked_candidate_count,
            # Report the authoritative post-plan inspection result. A reviewed
            # choice can resolve another candidate at the same musical onset,
            # so subtracting the submitted decision count is only an estimate.
            "unresolved_count": remaining_review_count,
            "remaining_review_count": remaining_review_count,
            "decision_counts": decision_counts,
            "member_count": len(planned),
            "arrays_affected": sum(
                action["arrays_affected"]
                for item in planned
                for action in item["plan"]["actions"]
            ),
            "musical_positions": change_count,
            "removed_count": 0,
            "repair_summaries": [{
                "rule_code": adapter_id,
                "title": definition.title,
                "item_name": "reviewed HO/PO decision",
                "change_kind": "reviewed_decisions",
                "change_count": change_count,
                "removed_count": 0,
                "arrays_affected": len(planned),
                "musical_positions": change_count,
                "member_count": len(planned),
            }],
            "_verification": {
                "mode": "reviewed",
                "adapter_id": adapter_id,
            },
            "_members": planned,
        }

    @staticmethod
    def _ordered_safe_rule_codes(
        rule_codes: Iterable[str] | None,
    ) -> tuple[str, ...]:
        if rule_codes is None:
            return _ALL_SAFE_RULE_ORDER
        if isinstance(rule_codes, (str, bytes)):
            raise RepairPlanningError(
                "invalid_rule_selection",
                "Choose one or more supported safe repair rules.",
            )
        try:
            requested = set(rule_codes)
        except TypeError as exc:
            raise RepairPlanningError(
                "invalid_rule_selection",
                "Choose one or more supported safe repair rules.",
            ) from exc
        if not requested or not all(isinstance(code, str) for code in requested):
            raise RepairPlanningError(
                "invalid_rule_selection",
                "Choose one or more supported safe repair rules.",
            )
        unsupported = requested.difference(_ALL_SAFE_RULE_ORDER)
        if unsupported:
            raise RepairPlanningError(
                "unsupported_repair",
                "The selected batch contains a rule without a safe automatic repair.",
            )
        return tuple(code for code in _ALL_SAFE_RULE_ORDER if code in requested)

    def _plan_all_package(
        self,
        package_path: Path,
        package_name: str,
        *,
        rule_codes: Iterable[str] | None = None,
    ) -> dict:
        """Build one dependency-ordered plan for the requested safe repairs."""
        implicit_all = rule_codes is None
        requested_rule_codes = self._ordered_safe_rule_codes(rule_codes)
        manifest = self._read_repair_manifest(package_path)
        member_sources: dict[str, set[str]] = {}
        member_rules: dict[str, set[str]] = {}
        parsed_json_cache: dict[
            str, tuple[str, object | None, str | None]
        ] = {}
        source_blockers = []
        for rule_code in requested_rule_codes:
            definition = _REPAIR_BY_RULE[rule_code]
            try:
                resolved_paths = self._resolved_repair_member_paths(
                    package_path,
                    manifest,
                    definition.source_kind,
                    rule_code,
                    parsed_json_cache,
                )
            except RepairPlanningError as exc:
                if implicit_all and rule_code in _CONDITIONAL_STRUCTURAL_RULES:
                    continue
                source_blockers.append({
                    "member_path": "manifest.yaml",
                    "code": exc.code,
                    "message": str(exc),
                })
                continue
            if implicit_all and rule_code in _CONDITIONAL_STRUCTURAL_RULES:
                preflight_blocked = False
                for member_path in resolved_paths:
                    try:
                        raw = self._read_member(
                            package_path, member_path, MAX_REPAIR_TEXT_BYTES
                        )
                        safe_member_path = _validate_member_path(member_path)
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
                        source_sha256 = hashlib.sha256(raw).hexdigest()
                        cached_status, cached_document, cached_sha256 = (
                            parsed_json_cache.get(
                                member_path, ("unavailable", None, None)
                            )
                        )
                        if (
                            cached_status == "valid"
                            and cached_sha256 == source_sha256
                        ):
                            document = cached_document
                        else:
                            document = _parse_json(raw)
                            _inspect_structure(document)
                            parsed_json_cache[member_path] = (
                                "valid", document, source_sha256
                            )
                        expected_shape = (
                            list if definition.source_kind == "lyrics" else dict
                        )
                        if not isinstance(document, expected_shape):
                            raise RepairPlanningError(
                                "invalid_document_shape",
                                "The song file does not have the expected JSON structure for this repair.",
                            )
                        _plan_json_document(
                            copy.deepcopy(document),
                            raw=raw,
                            safe_member_path=safe_member_path,
                            source_kind=definition.source_kind,
                            validator_version=self._validator_version,
                            definition=definition,
                        )
                    except RepairPlanningError:
                        preflight_blocked = True
                        break
                if preflight_blocked:
                    continue
            for member_path in resolved_paths:
                member_sources.setdefault(member_path, set()).add(
                    definition.source_kind
                )
                member_rules.setdefault(member_path, set()).add(rule_code)

        planned = []
        blockers = list(source_blockers)
        totals = {
            rule_code: {
                "change_count": 0,
                "removed_count": 0,
                "arrays_affected": 0,
                "musical_positions": 0,
                "members": set(),
            }
            for rule_code in requested_rule_codes
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
                safe_member_path = _validate_member_path(member_path)
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
                cached_status, cached_document, cached_sha256 = parsed_json_cache.get(
                    member_path, ("unavailable", None, None)
                )
                if (
                    cached_status == "valid"
                    and cached_sha256 == hashlib.sha256(original_raw).hexdigest()
                ):
                    document = copy.deepcopy(cached_document)
                else:
                    document = _parse_json(original_raw)
                    _inspect_structure(document)
                expected_shape = list if source_kind == "lyrics" else dict
                if not isinstance(document, expected_shape):
                    raise RepairPlanningError(
                        "invalid_document_shape",
                        "The song file does not have the expected JSON structure for this repair.",
                    )
                steps = []
                for rule_code in requested_rule_codes:
                    definition = _REPAIR_BY_RULE[rule_code]
                    if rule_code not in member_rules[member_path]:
                        continue
                    plan = _plan_json_document(
                        document,
                        raw=original_raw,
                        safe_member_path=safe_member_path,
                        source_kind=definition.source_kind,
                        validator_version=self._validator_version,
                        definition=definition,
                    )
                    if not plan["actions"]:
                        continue
                    _apply_plan_actions_to_document(document, plan)
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
                candidate_raw = (
                    _render_json(document, original_raw) if steps else original_raw
                )
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
        for rule_code in requested_rule_codes:
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
            "requested_rule_codes": list(requested_rule_codes),
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
            "undo_available": True,
            "backup_retained": backup_id is not None,
            "backup_contents": "original_changed_song_data_files",
            "summary": (
                "Library Doctor checks the complete repaired song first. Only then does it replace "
                "the existing Feedpak at the same location. It does not add a second playable song to the library. "
                "The original changed song data is saved privately for Undo."
            ),
        }

    def _backup_size(self, backup_id: str) -> int | None:
        if not _BACKUP_ID_RE.fullmatch(backup_id):
            return None
        backup_dir = self._config_dir / "library_doctor" / "repair_backups"
        destination = backup_dir / f"{backup_id}.zip"
        try:
            destination.resolve(strict=True).relative_to(backup_dir.resolve(strict=True))
            return destination.stat().st_size
        except (OSError, ValueError):
            return None

    def _delete_backup(self, backup_id: str) -> int:
        if not isinstance(backup_id, str) or not _BACKUP_ID_RE.fullmatch(backup_id):
            raise RepairPlanningError("invalid_backup", "The recovery backup is invalid.")
        backup_dir = self._config_dir / "library_doctor" / "repair_backups"
        destination = backup_dir / f"{backup_id}.zip"
        try:
            destination.resolve(strict=True).relative_to(backup_dir.resolve(strict=True))
            size = destination.stat().st_size
            destination.unlink()
            return size
        except FileNotFoundError as exc:
            raise RepairPlanningError(
                "backup_unavailable", "The recovery backup no longer exists."
            ) from exc
        except (OSError, ValueError) as exc:
            raise RepairPlanningError(
                "backup_cleanup_failed",
                "The recovery backup could not be removed. No Feedpak content was removed or restored.",
            ) from exc

    @staticmethod
    def _member_exists(package_path: Path, member_path: str) -> bool:
        safe_path = _validate_member_path(member_path)
        if package_path.is_dir():
            target = package_path.joinpath(*PurePosixPath(safe_path).parts)
            try:
                resolved = target.resolve(strict=False)
                resolved.relative_to(package_path.resolve(strict=True))
                return resolved.is_file() and not target.is_symlink()
            except (OSError, ValueError):
                return False
        try:
            with zipfile.ZipFile(package_path, "r") as archive:
                info = archive.getinfo(safe_path)
                return not info.is_dir()
        except KeyError:
            return False
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise RepairPlanningError(
                "package_unreadable", "The package archive cannot be read safely for repair."
            ) from exc

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
            workspace = _workspace.create_candidate_workspace(
                config_dir=self._config_dir,
                package_path=package_path,
            )
        except _workspace.WorkspaceError as exc:
            raise RepairPlanningError(
                "candidate_failed", "A repaired package candidate could not be created."
            ) from exc
        candidate = workspace.candidate
        if package_path.is_dir():
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
                    if raw is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self._atomic_write(target, raw)
                self._verify_directory_candidate(
                    package_path, candidate, replacements
                )
            except RepairPlanningError:
                workspace.cleanup()
                raise
            except (OSError, shutil.Error) as exc:
                workspace.cleanup()
                raise RepairPlanningError(
                    "candidate_failed", "A repaired package candidate could not be created."
                ) from exc
        else:
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
                                replacement = replacements[info.filename]
                                if replacement is not None:
                                    target.writestr(info, replacement)
                            elif info.is_dir():
                                target.writestr(info, b"")
                            else:
                                with source.open(info, "r") as input_stream:
                                    with target.open(info, "w", force_zip64=True) as output_stream:
                                        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                        existing = set(names)
                        for member_path, raw in replacements.items():
                            if member_path in existing or raw is None:
                                continue
                            info = zipfile.ZipInfo(member_path)
                            info.compress_type = zipfile.ZIP_DEFLATED
                            target.writestr(info, raw)
                shutil.copystat(package_path, candidate)
                self._verify_archive_candidate(package_path, candidate, replacements)
            except RepairPlanningError:
                workspace.cleanup()
                raise
            except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                workspace.cleanup()
                raise RepairPlanningError(
                    "candidate_failed", "A repaired package candidate could not be created."
                ) from exc

        def cleanup():
            workspace.cleanup()

        return candidate, cleanup

    @staticmethod
    def _verify_directory_candidate(
        source_path: Path,
        candidate_path: Path,
        replacements: dict[str, bytes | None],
    ) -> None:
        """Verify the complete directory candidate without following links.

        Same-volume hard links are exact by construction. If candidate creation
        had to copy a file instead, both byte streams are hashed. This keeps the
        common repair path inexpensive while still proving that every unrelated
        member presented to the validator survived candidate creation.
        """

        def entries(root: Path) -> dict[str, tuple[str, Path]]:
            found: dict[str, tuple[str, Path]] = {}
            pending = [root]
            while pending:
                parent = pending.pop()
                with os.scandir(parent) as stream:
                    for item in stream:
                        path = Path(item.path)
                        relative = path.relative_to(root).as_posix()
                        is_junction = bool(
                            hasattr(os.path, "isjunction")
                            and os.path.isjunction(path)
                        )
                        if item.is_symlink() or is_junction:
                            kind = "link"
                        elif item.is_dir(follow_symlinks=False):
                            kind = "directory"
                            pending.append(path)
                        elif item.is_file(follow_symlinks=False):
                            kind = "file"
                        else:
                            kind = "unsupported"
                        found[relative] = (kind, path)
                        if len(found) > MAX_DIRECTORY_CANDIDATE_ENTRIES:
                            raise RepairPlanningError(
                                "candidate_integrity_failed",
                                "The repaired directory candidate contains too many members to verify safely.",
                            )
            return found

        def file_hash(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        def fail(message: str) -> None:
            raise RepairPlanningError("candidate_integrity_failed", message)

        try:
            source = entries(source_path)
            candidate = entries(candidate_path)
            expected_kinds = {name: value[0] for name, value in source.items()}
            for member_path, raw in replacements.items():
                safe_path = _validate_member_path(member_path)
                if raw is None:
                    expected_kinds.pop(safe_path, None)
                    continue
                parent = PurePosixPath(safe_path).parent
                while parent != PurePosixPath("."):
                    parent_name = parent.as_posix()
                    existing = expected_kinds.get(parent_name)
                    if existing not in {None, "directory"}:
                        fail(
                            "A repaired member conflicts with the directory candidate structure."
                        )
                    expected_kinds[parent_name] = "directory"
                    parent = parent.parent
                expected_kinds[safe_path] = "file"

            candidate_kinds = {
                name: value[0] for name, value in candidate.items()
            }
            if expected_kinds != candidate_kinds:
                fail(
                    "The repaired directory candidate did not preserve the expected package members."
                )

            for name, expected_kind in expected_kinds.items():
                if expected_kind == "unsupported":
                    fail(
                        "The directory package contains a member type that cannot be verified safely."
                    )
                candidate_kind, candidate_member = candidate[name]
                if candidate_kind != expected_kind or expected_kind == "directory":
                    continue
                replacement = replacements.get(name, ...)
                if replacement is not ...:
                    if replacement is None:
                        fail("A deleted repair member remained in the directory candidate.")
                    if (
                        candidate_member.stat().st_size != len(replacement)
                        or file_hash(candidate_member)
                        != hashlib.sha256(replacement).hexdigest()
                    ):
                        fail(
                            "A changed repair member did not match its planned candidate bytes."
                        )
                    continue

                source_kind, source_member = source[name]
                if source_kind == "link":
                    if os.readlink(source_member) != os.readlink(candidate_member):
                        fail(
                            "An unchanged linked member did not survive directory candidate creation."
                        )
                    continue
                source_stat = source_member.stat()
                candidate_stat = candidate_member.stat()
                if source_stat.st_size != candidate_stat.st_size:
                    fail(
                        "An unchanged package member changed size during directory candidate creation."
                    )
                same_file = (
                    source_stat.st_dev,
                    source_stat.st_ino,
                ) == (
                    candidate_stat.st_dev,
                    candidate_stat.st_ino,
                )
                if not same_file and file_hash(source_member) != file_hash(
                    candidate_member
                ):
                    fail(
                        "An unchanged package member did not survive directory candidate creation."
                    )
        except RepairPlanningError:
            raise
        except (OSError, ValueError) as exc:
            raise RepairPlanningError(
                "candidate_integrity_failed",
                "The repaired directory candidate could not be verified safely.",
            ) from exc

    @staticmethod
    def _verify_archive_candidate(
        source_path: Path,
        candidate_path: Path,
        replacements: dict[str, bytes | None],
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
                source_names = [item.filename for item in source_infos]
                expected_names = [
                    name for name in source_names
                    if name not in replacements or replacements[name] is not None
                ]
                expected_names.extend(
                    name for name, raw in replacements.items()
                    if name not in source_names and raw is not None
                )
                candidate_names = [item.filename for item in candidate_infos]
                if candidate_names != expected_names:
                    raise RepairPlanningError(
                        "candidate_integrity_failed",
                        "The repaired candidate did not preserve the expected package members, so it was not saved.",
                    )
                candidate_by_name = {item.filename: item for item in candidate_infos}
                for original in source_infos:
                    if original.filename in replacements:
                        continue
                    rebuilt = candidate_by_name[original.filename]
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
        feature_verified = set()
        if "media.preview-missing" in requested:
            before_features = before.get("features") if isinstance(before, dict) else {}
            after_features = after.get("features") if isinstance(after, dict) else {}
            if (
                isinstance(before_features, dict)
                and isinstance(after_features, dict)
                and not bool(before_features.get("preview_declared"))
                and bool(after_features.get("preview_declared"))
                and bool(after_features.get("preview_available"))
            ):
                feature_verified.add("media.preview-missing")
        if "media.preview-regenerate" in requested:
            before_features = before.get("features") if isinstance(before, dict) else {}
            after_features = after.get("features") if isinstance(after, dict) else {}
            if (
                isinstance(before_features, dict)
                and isinstance(after_features, dict)
                and bool(before_features.get("preview_declared"))
                and bool(before_features.get("preview_available"))
                and bool(after_features.get("preview_declared"))
                and bool(after_features.get("preview_available"))
            ):
                feature_verified.add("media.preview-regenerate")
        if not requested or not ((requested & before_codes) | feature_verified):
            raise RepairPlanningError(
                "nothing_to_repair",
                "The selected safe issues are no longer present in this package.",
            )
        if requested & after_codes:
            raise RepairPlanningError(
                "verification_failed",
                "The repaired candidate still contains a selected safe issue.",
            )
        if "media.preview-regenerate" in requested and after_codes & {
            "media.preview-too-short",
            "media.preview-too-long",
        }:
            raise RepairPlanningError(
                "verification_failed",
                "The replacement preview did not pass Library Doctor's preview checks.",
            )
        introduced = sorted(after_codes - before_codes)
        if introduced:
            raise RepairPlanningError(
                "verification_failed",
                "The repaired candidate introduced a new validation finding and was not saved.",
            )

    @staticmethod
    def _verify_reviewed_validation(
        before: dict,
        after: dict,
        reviewed_codes: set[str] | None = None,
    ) -> None:
        """Require a reviewed group to introduce no validation finding increase."""
        before_counts = RepairService._reviewed_finding_counts([before])
        after_counts = RepairService._reviewed_finding_counts([after])
        introduced = sorted(
            key
            for key, count in after_counts.items()
            if count > before_counts.get(key, 0)
        )
        if introduced:
            raise RepairPlanningError(
                "verification_failed",
                "The reviewed candidate introduced a new validation finding and was not saved.",
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
                    "selected_count", "changing_count", "skipped_count",
                    "blocked_count", "unresolved_count",
                    "remaining_review_count", "decision_counts",
                    "media", "artist",
                )
            },
            "members": [],
        }
        for index, (member_path, raw) in enumerate(originals.items()):
            replacement = replacements[member_path]
            original_present = raw is not None
            repaired_present = replacement is not None
            metadata["members"].append({
                "member_path": member_path,
                "backup_entry": f"original/{index}.bin" if original_present else None,
                "original_present": original_present,
                "repaired_present": repaired_present,
                "original_sha256": (
                    hashlib.sha256(raw).hexdigest() if original_present else None
                ),
                "repaired_sha256": (
                    hashlib.sha256(replacement).hexdigest() if repaired_present else None
                ),
            })
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "repair.json",
                    json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                for entry, raw in zip(metadata["members"], originals.values()):
                    if entry["original_present"]:
                        archive.writestr(entry["backup_entry"], raw)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            self._sync_directory(destination.parent)
        except (OSError, RuntimeError, zipfile.LargeZipFile) as exc:
            temporary.unlink(missing_ok=True)
            raise RepairPlanningError(
                "backup_failed", "A recovery backup could not be created, so nothing was changed."
            ) from exc
        try:
            self._verify_backup_durable(backup_id, package_name, originals)
        except RepairPlanningError as exc:
            destination.unlink(missing_ok=True)
            raise RepairPlanningError(
                "backup_failed",
                "The recovery backup could not be verified, so nothing was changed.",
            ) from exc
        return backup_id

    def _verify_backup_durable(
        self,
        backup_id: str,
        package_name: str,
        originals: dict[str, bytes | None],
    ) -> None:
        """Reopen a durable backup and prove every original byte is readable."""
        _metadata, recovered = self._read_backup(backup_id, package_name)
        if recovered.keys() != originals.keys() or any(
            recovered[member_path] != raw
            for member_path, raw in originals.items()
        ):
            raise RepairPlanningError(
                "backup_unreadable",
                "The recovery backup failed its byte-for-byte verification.",
            )

    def _read_backup(
        self, backup_id: str, package_name: str
    ) -> tuple[dict, dict[str, bytes | None]]:
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
                    "library_doctor.repair_backup.v2",
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
                    original_present = entry.get("original_present", True) is True
                    repaired_present = entry.get("repaired_present", True) is True
                    backup_entry = (
                        _validate_member_path(entry.get("backup_entry"))
                        if original_present else None
                    )
                    original_hash = entry.get("original_sha256")
                    repaired_hash = entry.get("repaired_sha256")
                    if (
                        (
                            original_present
                            and (
                                not isinstance(original_hash, str)
                                or not re.fullmatch(r"[0-9a-f]{64}", original_hash)
                            )
                        )
                        or (not original_present and original_hash is not None)
                        or (
                            repaired_present
                            and (
                                not isinstance(repaired_hash, str)
                                or not re.fullmatch(r"[0-9a-f]{64}", repaired_hash)
                            )
                        )
                        or (not repaired_present and repaired_hash is not None)
                        or member_path in originals
                        or (
                            original_present
                            and not backup_entry.startswith("original/")
                        )
                    ):
                        raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                    raw = None
                    if original_present:
                        member_info = archive.getinfo(backup_entry)
                        if member_info.is_dir() or member_info.file_size > MAX_REPAIR_MEMBER_BYTES:
                            raise RepairPlanningError("backup_unreadable", "The recovery backup is invalid.")
                        raw = archive.read(member_info)
                        if hashlib.sha256(raw).hexdigest() != original_hash:
                            raise RepairPlanningError(
                                "backup_unreadable", "The recovery backup failed its integrity check."
                            )
                    entry["original_present"] = original_present
                    entry["repaired_present"] = repaired_present
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

    @property
    def _transaction_dir(self) -> Path:
        return self._transaction_journal.directory

    def _begin_transaction(
        self,
        package: str,
        backup_id: str,
        *,
        operation: str,
        target_state: str,
    ) -> dict:
        transaction = {
            "schema": TRANSACTION_SCHEMA,
            "transaction_id": backup_id,
            "backup_id": backup_id,
            "package": package,
            "package_kind": "directory",
            "operation": operation,
            "target_state": target_state,
            "phase": "prepared",
            "committed_members": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        try:
            self._write_transaction(transaction)
        except OSError as exc:
            raise RepairPlanningError(
                "journal_failed",
                "The package transaction could not be recorded durably, so nothing was changed.",
            ) from exc
        self._emit_transaction_barrier(
            "journal_durable",
            package=package,
            operation=operation,
            backup_id=backup_id,
        )
        return transaction

    def _update_transaction(self, transaction: dict, **updates) -> None:
        transaction.update(updates)
        transaction["updated_at"] = time.time()
        self._write_transaction(transaction)

    def _write_transaction(self, transaction: dict) -> None:
        self._transaction_journal.write(transaction)

    def _finish_transaction(self, transaction: dict) -> None:
        self._transaction_journal.finish(transaction)

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_transactions(self) -> list[dict]:
        return self._transaction_journal.read()

    def _reconcile_transactions(self) -> None:
        self._transaction_journal.reconcile_all()

    def _reconcile_transaction(self, transaction: dict) -> None:
        self._transaction_journal.reconcile(transaction)

    def _record_recovered_transaction(
        self,
        transaction: dict,
        metadata: dict,
        *,
        committed: bool,
    ) -> bool:
        return self._transaction_journal.record_recovered(
            transaction,
            metadata,
            committed=committed,
        )

    def _pending_transaction_receipts(self) -> list[dict]:
        return self._transaction_journal.pending_receipts()

    @staticmethod
    def _request_metadata(
        request_id: str | None,
        operation: str | None,
        fingerprint: str | None,
    ) -> dict:
        # Service callers always provide their fixed operation name, even when
        # the HTTP client did not opt into idempotency. In that case there is
        # no request identity to persist.
        if request_id is None and fingerprint is None:
            return {}
        if (
            not isinstance(request_id, str)
            or not _REQUEST_ID_RE.fullmatch(request_id)
            or not isinstance(operation, str)
            or not operation
            or len(operation) > 100
            or not isinstance(fingerprint, str)
            or not _REQUEST_FINGERPRINT_RE.fullmatch(fingerprint)
        ):
            raise RepairPlanningError(
                "invalid_request_id",
                "The mutation retry identity is invalid.",
            )
        return {
            "request_id": request_id,
            "request_operation": operation,
            "request_fingerprint": fingerprint,
        }

    def receipt_for_request(
        self,
        request_id: str,
        operation: str,
        fingerprint: str,
    ) -> dict | None:
        """Recover a successful mutation receipt after an interrupted response."""
        expected = self._request_metadata(request_id, operation, fingerprint)
        with self._lock:
            for item in reversed(self._read_history()):
                if item.get("request_id") != request_id:
                    continue
                if any(item.get(key) != value for key, value in expected.items()):
                    raise RepairPlanningError(
                        "idempotency_key_reused",
                        "This mutation request ID was already used for different inputs.",
                    )
                receipt = copy.deepcopy(item)
                receipt["idempotent_replay"] = True
                return receipt
        return None

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
        package_name: str,
        package_path: Path,
        candidate: Path,
        replacements: dict[str, bytes],
        originals: dict[str, bytes],
        *,
        source_token: dict | None = None,
        transaction: dict | None = None,
        operation: str = "repair",
    ) -> None:
        try:
            self._assert_source_state(
                package_name,
                package_path,
                originals,
                source_token,
            )
        except RepairPlanningError:
            if transaction is not None:
                self._finish_transaction(transaction)
            raise

        if package_path.is_file():
            try:
                with candidate.open("r+b") as stream:
                    os.fsync(stream.fileno())
                self._assert_source_state(
                    package_name,
                    package_path,
                    originals,
                    source_token,
                )
                self._emit_transaction_barrier(
                    "before_archive_replace",
                    package=package_name,
                    operation=operation,
                )
                self._assert_source_state(
                    package_name,
                    package_path,
                    originals,
                    source_token,
                )
                os.replace(candidate, package_path)
            except OSError as exc:
                raise RepairPlanningError(
                    "save_failed", "The repaired package could not replace the original."
                ) from exc
            self._emit_transaction_barrier(
                "package_committed", package=package_name, operation=operation
            )
            return

        committed = []
        try:
            if transaction is not None:
                self._update_transaction(transaction, phase="committing")
            for member_path, raw in replacements.items():
                self._assert_package_identity(
                    package_name,
                    package_path,
                    source_token,
                )
                self._assert_member_state(
                    package_path,
                    member_path,
                    originals[member_path],
                )
                self._emit_transaction_barrier(
                    "before_member_replace",
                    package=package_name,
                    operation=operation,
                    member_path=member_path,
                    member_index=len(committed) + 1,
                )
                self._assert_package_identity(
                    package_name,
                    package_path,
                    source_token,
                )
                self._assert_member_state(
                    package_path,
                    member_path,
                    originals[member_path],
                )
                target = package_path.joinpath(*PurePosixPath(member_path).parts)
                if raw is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self._atomic_write(target, raw)
                self._emit_transaction_barrier(
                    "member_replaced",
                    package=package_name,
                    operation=operation,
                    member_path=member_path,
                    member_index=len(committed) + 1,
                )
                committed.append(member_path)
                if transaction is not None:
                    self._update_transaction(
                        transaction,
                        phase="committing",
                        committed_members=committed,
                    )
                self._emit_transaction_barrier(
                    "member_committed",
                    package=package_name,
                    operation=operation,
                    member_path=member_path,
                    member_index=len(committed),
                )
            self._assert_package_identity(
                package_name,
                package_path,
                source_token,
            )
            for member_path, raw in replacements.items():
                self._assert_member_state(package_path, member_path, raw)
            if transaction is not None:
                self._update_transaction(
                    transaction,
                    phase="package_committed",
                    committed_members=committed,
                )
            self._emit_transaction_barrier(
                "package_committed",
                package=package_name,
                operation=operation,
            )
            if transaction is not None:
                self._finish_transaction(transaction)
        except (OSError, RepairPlanningError) as exc:
            rollback_failed = False
            try:
                self._assert_package_identity(
                    package_name,
                    package_path,
                    source_token,
                )
            except RepairPlanningError:
                rollback_failed = bool(committed)
                rollback_members = []
            else:
                rollback_members = []
                for member_path in reversed(committed):
                    try:
                        self._assert_member_state(
                            package_path,
                            member_path,
                            replacements[member_path],
                        )
                    except RepairPlanningError:
                        rollback_failed = True
                        self._log.error(
                            "Library Doctor preserved an external edit to %s in %s during rollback",
                            member_path,
                            package_path.name,
                        )
                    else:
                        rollback_members.append(member_path)
            for member_path in rollback_members:
                try:
                    target = package_path.joinpath(*PurePosixPath(member_path).parts)
                    original = originals[member_path]
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self._atomic_write(target, original)
                except Exception:
                    rollback_failed = True
                    self._log.error(
                        "Library Doctor could not roll back %s in %s",
                        member_path,
                        package_path.name,
                    )
            if transaction is not None:
                if rollback_failed:
                    try:
                        self._update_transaction(
                            transaction,
                            phase="recovery_required",
                            committed_members=committed,
                        )
                    except OSError:
                        pass
                else:
                    self._finish_transaction(transaction)
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

    def _atomic_write(self, path: Path, raw: bytes) -> None:
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
            self._sync_directory(path.parent)
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

    return _plan_json_document(
        document,
        raw=raw,
        safe_member_path=safe_member_path,
        source_kind=source_kind,
        validator_version=validator_version,
        definition=definition,
    )


def _reviewed_difficulty_scope(value: str) -> str:
    if value not in {"full_only", "all_authored"}:
        raise RepairPlanningError(
            "invalid_review_difficulty_scope",
            "Choose full difficulty only or all authored difficulties.",
        )
    return value


def _reviewed_decision_items(decisions, definition) -> list[tuple[str, str]]:
    if (
        not isinstance(decisions, list)
        or not decisions
        or len(decisions) > MAX_REVIEWED_DECISIONS
    ):
        raise RepairPlanningError(
            "invalid_decisions",
            "Choose at least one reviewed repair decision from the current inspection.",
        )
    result = []
    seen = set()
    candidate_prefix = f"{definition.candidate_id_prefix}-"
    decision_names = {item.name for item in definition.decisions}
    for item in decisions:
        if not isinstance(item, dict) or set(item) != {"candidate_id", "decision"}:
            raise RepairPlanningError(
                "invalid_decisions", "A reviewed repair decision is invalid."
            )
        candidate_id = item.get("candidate_id")
        decision = item.get("decision")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.startswith(candidate_prefix)
            or len(candidate_id) != len(candidate_prefix) + 24
            or not isinstance(decision, str)
            or decision not in decision_names
            or candidate_id in seen
        ):
            raise RepairPlanningError(
                "invalid_decisions", "A reviewed repair decision is invalid."
            )
        seen.add(candidate_id)
        result.append((candidate_id, decision))
    return result


def _reviewed_change(
    document: dict,
    path: tuple[str | int, ...],
    *,
    set_fields: dict,
    remove_fields: tuple[str, ...],
    allowed_fields: frozenset[str],
) -> dict | None:
    note = _value_at_path(document, path)
    if not isinstance(note, dict):
        raise RepairPlanningError(
            "source_changed",
            "A reviewed note changed after inspection. Inspect it again before applying decisions.",
        )
    if (
        not set(set_fields).issubset(allowed_fields)
        or not set(remove_fields).issubset(allowed_fields)
        or set(set_fields).intersection(remove_fields)
        or any(value is not True for value in set_fields.values())
    ):
        raise RepairPlanningError(
            "invalid_plan", "The reviewed repair preview is invalid."
        )
    updated = copy.deepcopy(note)
    for field in remove_fields:
        updated.pop(field, None)
    updated.update(set_fields)
    before_bytes = _canonical_json(note)
    after_bytes = _canonical_json(updated)
    if before_bytes == after_bytes:
        return None
    return {
        "target_path": list(path),
        "expected_before_sha256": hashlib.sha256(
            before_bytes
        ).hexdigest(),
        "expected_after_sha256": hashlib.sha256(
            after_bytes
        ).hexdigest(),
        "set_fields": dict(set_fields),
        "remove_fields": list(remove_fields),
    }


def _reviewed_operation(
    document: dict, definition, candidate, decision: str
) -> dict | None:
    if decision not in candidate.decision_names:
        raise RepairPlanningError(
            "invalid_decision",
            "Choose one of the decisions currently offered for this reviewed note.",
        )
    if candidate.blockers:
        raise RepairPlanningError(
            "candidate_blocked",
            "This note has conflicting or malformed source data and cannot be changed from Reviewed repair.",
        )

    try:
        mutations = definition.build_mutations(candidate, decision)
    except ValueError as exc:
        raise RepairPlanningError(
            "invalid_decision",
            "Choose one of the decisions currently offered for this reviewed note.",
        ) from exc
    allowed_fields = frozenset(definition.mutable_fields)
    changes = []
    for mutation in mutations:
        if mutation.target_role == "current":
            target_path = candidate.target_path
        elif mutation.target_role == "next" and candidate.next is not None:
            target_path = candidate.next.path
        else:
            target_path = None
        if target_path is None:
            raise RepairPlanningError(
                "invalid_decision",
                "A reviewed repair target is no longer explicit and writable.",
            )
        change = _reviewed_change(
            document,
            target_path,
            set_fields=mutation.set_dict(),
            remove_fields=mutation.remove_fields,
            allowed_fields=allowed_fields,
        )
        if change is not None:
            changes.append(change)
    if not changes:
        return None
    return {
        "operation": definition.operation_name,
        "candidate_id": candidate.candidate_id,
        "decision": decision,
        "mutable_fields": sorted(definition.mutable_fields),
        "changes": changes,
    }


def plan_reviewed_json_member(
    raw: bytes,
    *,
    member_path: str,
    adapter_id: str,
    validator_version: str,
    decisions: list[dict],
    difficulty_scope: str = "full_only",
) -> dict:
    """Build a source-bound closed plan from explicit candidate decisions."""
    safe_member_path = _validate_member_path(member_path)
    try:
        definition = _reviewed.reviewed_repair_definition(adapter_id)
    except ValueError as exc:
        raise RepairPlanningError(
            "unsupported_reviewed_repair",
            "This Reviewed repair adapter is not supported by this version of Library Doctor.",
        ) from exc
    if safe_member_path.lower().endswith(".jsonc"):
        raise RepairPlanningError(
            "jsonc_requires_lossless_writer",
            "Commented JSON cannot be repaired until comments can be preserved.",
        )
    if not safe_member_path.lower().endswith(".json"):
        raise RepairPlanningError(
            "unsupported_text_format",
            "Reviewed song-data repairs currently require an ordinary JSON file.",
        )
    if not isinstance(raw, bytes) or len(raw) > MAX_REPAIR_TEXT_BYTES:
        raise RepairPlanningError(
            "source_too_large", "This song file is too large to review safely."
        )
    if not isinstance(validator_version, str) or not validator_version.strip():
        raise ValueError("validator_version must be a non-empty string")
    difficulty_scope = _reviewed_difficulty_scope(difficulty_scope)
    requested = _reviewed_decision_items(decisions, definition)
    document = _parse_json(raw)
    _inspect_structure(document)
    if not isinstance(document, dict):
        raise RepairPlanningError(
            "invalid_document_shape",
            "The arrangement does not have the expected JSON structure for Reviewed repair.",
        )
    selection = definition.select_document(
        document,
        member_path=safe_member_path,
        candidate_ids={candidate_id for candidate_id, _decision in requested},
        difficulty_scope=difficulty_scope,
    )
    candidates = {
        candidate.candidate_id: candidate
        for candidate in selection.candidates
    }
    operations = []
    accepted = []
    for candidate_id, decision in requested:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise RepairPlanningError(
                "candidate_changed",
                "A reviewed note changed or is no longer questionable. Inspect the package again.",
            )
        if decision not in candidate.decision_names:
            raise RepairPlanningError(
                "invalid_decision",
                "Choose one of the decisions currently offered for this reviewed note.",
            )
        accepted.append({"candidate_id": candidate_id, "decision": decision})
        operation = _reviewed_operation(
            document, definition, candidate, decision
        )
        if operation is None:
            raise RepairPlanningError(
                "decision_no_effect",
                "This reviewed choice would not change the stored note.",
            )
        operations.append(operation)

    source_sha256 = hashlib.sha256(raw).hexdigest()
    actions = []
    if operations:
        action = {
            "adapter_id": adapter_id,
            "action_kind": definition.operation_name,
            "change_kind": "reviewed_decisions",
            "safety": "review_required",
            "title": definition.title,
            "summary": f"Apply {len(operations)} explicit HO/PO decision(s).",
            "change_count": len(operations),
            "removed_count": 0,
            "arrays_affected": len({candidate.stream_path for candidate in candidates.values() if candidate.candidate_id in {item['candidate_id'] for item in accepted}}),
            "musical_positions": len(operations),
            "operations": operations,
        }
        action["action_id"] = _digest_json({
            "source_sha256": source_sha256,
            **action,
        })
        actions.append(action)
    unsigned = {
        "schema": REPAIR_PLAN_SCHEMA,
        "repair_mode": "reviewed",
        "adapter_id": adapter_id,
        "difficulty_scope": difficulty_scope,
        "catalog_version": REPAIR_CATALOG_VERSION,
        "reviewed_registry_version": _reviewed.REVIEWED_REPAIR_REGISTRY_VERSION,
        "validator_version": validator_version,
        "source": {
            "member_path": safe_member_path,
            "source_kind": "arrangement",
            "sha256": source_sha256,
            "byte_count": len(raw),
        },
        "decisions": accepted,
        "actions": actions,
    }
    return {**unsigned, "plan_id": _digest_json(unsigned)}


def _plan_json_document(
    document,
    *,
    raw: bytes,
    safe_member_path: str,
    source_kind: str,
    validator_version: str,
    definition: RepairDefinition,
) -> dict:
    """Plan one rule against an already parsed and structure-checked document."""

    if definition.rule_code == "chart.negative-muted-fret":
        operations = _plan_muted_negative_frets(document)
    elif definition.rule_code == "chart.empty-phrases-key":
        operations = _plan_empty_root_array(document, "phrases")
    elif definition.rule_code == "timeline.empty-arrangement-tempos-key":
        operations = _plan_empty_root_array(document, "tempos")
    elif definition.rule_code == "timeline.duplicate-tempo":
        operations = _plan_exact_timed_duplicates(
            document,
            ("tempos",),
            repairable_tempo_event,
            blocker_code="malformed_timed_events",
            blocker_message=(
                "A tempo timeline with exact duplicates also contains an invalid "
                "event, so Library Doctor will not partially repair it."
            ),
        )
    elif definition.rule_code == "timeline.tempos-out-of-order":
        operations = _plan_timed_event_order(
            document,
            ("tempos",),
            "time",
            repairable_tempo_event,
            blocker_code="malformed_timed_events",
            blocker_message=(
                "The out-of-order tempo timeline also contains an invalid event, "
                "so Library Doctor will not guess how to reorder it."
            ),
        )
    elif definition.rule_code == "timeline.duplicate-time-signature":
        operations = _plan_exact_timed_duplicates(
            document,
            ("time_signatures",),
            repairable_time_signature_event,
            blocker_code="malformed_timed_events",
            blocker_message=(
                "A time-signature timeline with exact duplicates also contains "
                "an invalid event, so Library Doctor will not partially repair it."
            ),
        )
    elif definition.rule_code == "timeline.time-signatures-out-of-order":
        operations = _plan_timed_event_order(
            document,
            ("time_signatures",),
            "time",
            repairable_time_signature_event,
            blocker_code="malformed_timed_events",
            blocker_message=(
                "The out-of-order time-signature timeline also contains an invalid "
                "event, so Library Doctor will not guess how to reorder it."
            ),
        )
    elif definition.rule_code == "tones.duplicate-change":
        operations = _plan_exact_timed_duplicates(
            document,
            ("tones", "changes"),
            repairable_tone_change,
            blocker_code="malformed_timed_events",
            blocker_message=(
                "A tone-change timeline with exact duplicates also contains an "
                "invalid event, so Library Doctor will not partially repair it."
            ),
        )
    elif definition.rule_code == "tones.changes-out-of-order":
        operations = _plan_timed_event_order(
            document,
            ("tones", "changes"),
            "t",
            repairable_tone_change,
            blocker_code="malformed_timed_events",
            blocker_message=(
                "The out-of-order tone-change timeline also contains an invalid "
                "event, so Library Doctor will not guess how to reorder it."
            ),
        )
    elif definition.rule_code == "chart.duplicate-note":
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
    elif definition.rule_code == "chart.zero-length-handshape":
        operations = _plan_redundant_zero_length_handshapes(document)
    elif definition.rule_code == "chart.invalid-handshape-span":
        operations = _plan_redundant_reversed_handshapes(document)
    elif definition.rule_code == "chart.note-duplicates-chord":
        operations = _plan_exact_note_chord_duplicates(document)
    elif definition.rule_code == "chart.bend-points-out-of-order":
        operations = _plan_bend_point_order(document)
    elif definition.rule_code == "lyrics.out-of-order":
        operations = _plan_lyric_cue_order(document)
    elif definition.rule_code == "timeline.duplicate-beat":
        operations = _plan_exact_beat_duplicates(document)
    elif definition.rule_code == "timeline.beats-out-of-order":
        operations = _plan_timeline_marker_order(document, "beats")
    elif definition.rule_code == "timeline.duplicate-section":
        operations = _plan_exact_section_duplicates(document)
    elif definition.rule_code == "timeline.sections-out-of-order":
        operations = _plan_timeline_marker_order(document, "sections")
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
        if definition.change_kind in {"reorder", "omit_empty"}:
            change_count = len(operations)
        elif definition.change_kind == "normalize":
            change_count = sum(
                operation.change_count for operation in operations
                if isinstance(operation, NormalizeMutedNegativeFrets)
            )
        else:
            change_count = removed_count
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


def _optional_array_at_path(
    document,
    path: tuple[str | int, ...],
) -> list | None:
    value = document
    for part in path:
        if isinstance(part, str) and isinstance(value, dict) and part in value:
            value = value[part]
        elif _integer(part) and isinstance(value, list) and 0 <= part < len(value):
            value = value[part]
        else:
            return None
    return value if isinstance(value, list) else None


def _plan_empty_root_array(
    document: dict,
    field: str,
) -> list[OmitEmptyRootArray]:
    if field not in {"phrases", "tempos"}:
        raise ValueError("unsupported optional arrangement field")
    if not isinstance(document, dict) or document.get(field) != []:
        return []
    updated = copy.deepcopy(document)
    del updated[field]
    return [OmitEmptyRootArray(
        field=field,
        original_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
        result_sha256=hashlib.sha256(_canonical_json(updated)).hexdigest(),
    )]


def _plan_exact_timed_duplicates(
    document: dict,
    path: tuple[str | int, ...],
    predicate,
    *,
    blocker_code: str,
    blocker_message: str,
) -> list[DeleteArrayItems]:
    values = _optional_array_at_path(document, path)
    if values is None:
        return []
    operation = _duplicate_operation(
        path,
        values,
        lambda value: (
            complete_json_identity(value) if predicate(value) else None
        ),
    )
    if operation is None:
        return []
    if not timed_event_stream_eligibility(values, predicate):
        raise RepairPlanningError(blocker_code, blocker_message)
    return [operation]


def _plan_timed_event_order(
    document: dict,
    path: tuple[str | int, ...],
    time_key: str,
    predicate,
    *,
    blocker_code: str,
    blocker_message: str,
) -> list[StableSortTimedEvents]:
    values = _optional_array_at_path(document, path)
    if values is None:
        return []
    parsed_times = []
    for value in values:
        if not isinstance(value, dict):
            continue
        event_time = value.get(time_key)
        if time_key == "t" and not _finite_number(event_time):
            event_time = value.get("time")
        if _finite_number(event_time):
            parsed_times.append(event_time)
    if not any(
        current < previous
        for previous, current in zip(parsed_times, parsed_times[1:])
    ):
        return []
    if not timed_event_stream_eligibility(values, predicate):
        raise RepairPlanningError(blocker_code, blocker_message)
    sorted_indices = tuple(sorted(
        range(len(values)), key=lambda index: values[index][time_key]
    ))
    moved_count = sum(
        index != original_index
        for index, original_index in enumerate(sorted_indices)
    )
    if not moved_count:
        return []
    sorted_values = [values[index] for index in sorted_indices]
    return [StableSortTimedEvents(
        array_path=path,
        time_key=time_key,
        expected_length=len(values),
        original_sha256=hashlib.sha256(_canonical_json(values)).hexdigest(),
        sorted_sha256=hashlib.sha256(
            _canonical_json(sorted_values)
        ).hexdigest(),
        sorted_indices=sorted_indices,
        moved_count=moved_count,
    )]


def _plan_muted_negative_frets(
    document: dict,
) -> list[NormalizeMutedNegativeFrets]:
    operations = []
    for path, notes in _arrangement_arrays(document, "notes"):
        operation = _muted_fret_normalization_operation(path, notes)
        if operation is not None:
            operations.append(operation)

    for chord_path, chords in _arrangement_arrays(document, "chords"):
        for chord_index, chord in enumerate(chords):
            if not isinstance(chord, dict):
                continue
            chord_notes = chord.get("notes")
            if not isinstance(chord_notes, list):
                continue
            operation = _muted_fret_normalization_operation(
                chord_path + (chord_index, "notes"), chord_notes
            )
            if operation is not None:
                operations.append(operation)
    return operations


def _muted_fret_normalization_operation(
    path: tuple[str | int, ...], notes: list
) -> NormalizeMutedNegativeFrets | None:
    changes = []
    for note_index, note in enumerate(notes):
        if (
            not isinstance(note, dict)
            or not _integer(note.get("f"))
            or note["f"] >= 0
            or note.get("mt") is not True
        ):
            continue
        changes.append(MutedFretChange(
            note_index=note_index,
            original_fret=note["f"],
            replacement_fret=0,
            note_sha256=hashlib.sha256(_canonical_json(note)).hexdigest(),
        ))
    if not changes:
        return None
    return NormalizeMutedNegativeFrets(
        note_array_path=path,
        expected_length=len(notes),
        changes=tuple(changes),
    )


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


def _plan_redundant_zero_length_handshapes(
    document: dict,
) -> list[DeleteRedundantHandshapes]:
    return _plan_redundant_handshapes(document, span_kind="zero_length")


def _plan_redundant_reversed_handshapes(
    document: dict,
) -> list[DeleteRedundantHandshapes]:
    return _plan_redundant_handshapes(document, span_kind="reversed")


def _plan_redundant_handshapes(
    document: dict,
    *,
    span_kind: str,
) -> list[DeleteRedundantHandshapes]:
    if span_kind not in {"zero_length", "reversed"}:
        raise ValueError("unsupported redundant handshape span kind")
    assessment = assess_redundant_handshapes(document, span_kind=span_kind)
    if assessment["unsafe_count"]:
        raise RepairPlanningError(
            assessment["blocker_code"], assessment["message"]
        )
    grouped: dict[tuple[str | int, ...], list[dict]] = {}
    for match in assessment["matches"]:
        grouped.setdefault(match["parent_path"], []).append(match)
    operations = []
    for parent_path, matches in grouped.items():
        first = matches[0]
        operations.append(DeleteRedundantHandshapes(
            span_kind=span_kind,
            handshape_array_path=parent_path + ("handshapes",),
            chord_array_path=parent_path + ("chords",),
            expected_handshape_length=first["handshape_length"],
            expected_chord_length=first["chord_length"],
            match_groups=tuple(
                RedundantHandshapeMatch(
                    handshape_index=match["handshape_index"],
                    chord_index=match["chord_index"],
                    handshape_sha256=hashlib.sha256(
                        _canonical_json(match["handshape"])
                    ).hexdigest(),
                    chord_sha256=hashlib.sha256(
                        _canonical_json(match["chord"])
                    ).hexdigest(),
                )
                for match in matches
            ),
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


def _plan_timeline_marker_order(
    document: dict,
    field: str,
) -> list[StableSortTimelineMarkers]:
    if field not in {"beats", "sections"}:
        raise ValueError("field must be beats or sections")
    markers = document.get(field)
    if not isinstance(markers, list):
        return []

    parsed_times = [
        marker.get("time")
        for marker in markers
        if isinstance(marker, dict) and _finite_number(marker.get("time"))
    ]
    if not any(
        current < previous
        for previous, current in zip(parsed_times, parsed_times[1:])
    ):
        return []

    identity_factory = (
        _valid_beat_identity if field == "beats" else _valid_section_identity
    )
    if not all(identity_factory(marker) is not None for marker in markers):
        marker_name = "beat" if field == "beats" else "section"
        raise RepairPlanningError(
            f"invalid_{marker_name}_timeline",
            f"The out-of-order {marker_name} timeline also contains an invalid "
            "marker, so Library Doctor will not guess how to reorder it.",
        )

    sorted_indices = tuple(sorted(
        range(len(markers)), key=lambda index: markers[index]["time"]
    ))
    moved_count = sum(
        index != original_index
        for index, original_index in enumerate(sorted_indices)
    )
    if not moved_count:
        return []
    sorted_markers = [markers[index] for index in sorted_indices]
    return [StableSortTimelineMarkers(
        field=field,
        expected_length=len(markers),
        original_sha256=hashlib.sha256(_canonical_json(markers)).hexdigest(),
        sorted_sha256=hashlib.sha256(_canonical_json(sorted_markers)).hexdigest(),
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


def _plan_exact_section_duplicates(document: dict) -> list[DeleteArrayItems]:
    sections = document.get("sections")
    if not isinstance(sections, list):
        return []
    operation = _duplicate_operation(
        ("sections",), sections, _valid_section_identity
    )
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


def _reported_zero_length_handshape(value) -> bool:
    return _shared_reported_zero_length_handshape(value)


def _reported_invalid_handshape_span(value) -> bool:
    return _shared_reported_invalid_handshape_span(value)


def _reported_reversed_handshape(value) -> bool:
    return _shared_reported_reversed_handshape(value)


def _redundant_handshape_identity(value, *, span_kind: str) -> bytes | None:
    if span_kind == "zero_length":
        reported = _reported_zero_length_handshape(value)
    elif span_kind == "reversed":
        reported = _reported_reversed_handshape(value)
    else:
        return None
    if not reported or not redundant_handshape_is_plain(
        value, span_kind=span_kind
    ):
        return None
    return _canonical_json(value)


def _chord_matches_handshape(chord, handshape) -> bool:
    return _shared_chord_matches_handshape(chord, handshape)


def _strict_reversed_handshape_context(document, handshape, chord) -> bool:
    return _shared_strict_reversed_handshape_context(document, handshape, chord)


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


def _valid_section_identity(value) -> bytes | None:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("name"), str)
        or not _finite_number(value.get("time"))
        or ("number" in value and not _integer(value["number"]))
    ):
        return None
    return _canonical_json(value)


def _apply_operation(
    document,
    operation: dict,
    removed: set[tuple[tuple[str | int, ...], int]],
    reordered: set[tuple[str | int, ...]],
    normalized: set[tuple[tuple[str | int, ...], int]],
    reviewed_paths: set[tuple[str | int, ...]],
    omitted: set[tuple[str | int, ...]],
    *,
    rule_code: str | None,
) -> None:
    if not isinstance(operation, dict):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    reviewed_definition = _reviewed.reviewed_repair_for_operation(
        operation.get("operation")
    )
    if reviewed_definition is not None:
        _apply_reviewed_operation(
            document,
            operation,
            reviewed_paths,
            allowed_fields=frozenset(reviewed_definition.mutable_fields),
        )
        return
    if operation.get("operation") == "normalize_muted_negative_frets":
        _apply_muted_fret_normalization_operation(
            document, operation, normalized
        )
        return
    if operation.get("operation") == "omit_empty_root_array":
        _apply_empty_root_array_omission(
            document, operation, omitted, rule_code=rule_code
        )
        return
    if operation.get("operation") == "stable_sort_timed_events":
        _apply_timed_event_sort_operation(
            document, operation, reordered, rule_code=rule_code
        )
        return
    if operation.get("operation") == "stable_sort_lyric_cues":
        _apply_lyric_cue_sort_operation(document, operation, reordered)
        return
    if operation.get("operation") == "stable_sort_bend_points":
        _apply_bend_point_sort_operation(document, operation, reordered)
        return
    if operation.get("operation") == "stable_sort_timeline_markers":
        _apply_timeline_marker_sort_operation(document, operation, reordered)
        return
    if operation.get("operation") == "delete_notes_matching_chords":
        _apply_note_chord_delete_operation(document, operation, removed)
        return
    if operation.get("operation") == "delete_redundant_handshapes":
        _apply_redundant_handshape_delete_operation(document, operation, removed)
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

    timed_spec = _timed_duplicate_spec(rule_code)
    if timed_spec is not None:
        expected_path, predicate = timed_spec
        if path != expected_path or set(operation) != {
            "operation",
            "array_path",
            "expected_length",
            "remove_indices",
            "duplicate_groups",
        }:
            raise RepairPlanningError(
                "invalid_plan", "The repair preview is invalid."
            )
        if not timed_event_stream_eligibility(values, predicate):
            raise RepairPlanningError(
                "source_changed",
                "The timed event list changed after this preview. Review the safe fix again before applying it.",
            )
        expected_operation = _duplicate_operation(
            path,
            values,
            lambda value: (
                complete_json_identity(value) if predicate(value) else None
            ),
        )
        if expected_operation is None or expected_operation.to_dict() != operation:
            raise RepairPlanningError(
                "invalid_plan", "The repair preview is invalid."
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


def _manifest_tone_rule_present(tones, rule_code: str) -> bool:
    if not isinstance(tones, dict) or not isinstance(tones.get("changes"), list):
        return False
    changes = tones["changes"]
    if rule_code == "tones.duplicate-change":
        seen = set()
        for change in changes:
            if not repairable_tone_change(change):
                continue
            identity = complete_json_identity(change)
            if identity is None:
                continue
            if identity in seen:
                return True
            seen.add(identity)
        return False
    if rule_code == "tones.changes-out-of-order":
        times = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            event_time = change.get("t")
            if not _finite_number(event_time):
                event_time = change.get("time")
            if _finite_number(event_time):
                times.append(event_time)
        return any(
            current < previous
            for previous, current in zip(times, times[1:])
        )
    return False


def _timed_duplicate_spec(rule_code: str | None):
    return {
        "timeline.duplicate-tempo": (("tempos",), repairable_tempo_event),
        "timeline.duplicate-time-signature": (
            ("time_signatures",), repairable_time_signature_event
        ),
        "tones.duplicate-change": (
            ("tones", "changes"), repairable_tone_change
        ),
    }.get(rule_code)


def _apply_empty_root_array_omission(
    document: dict,
    operation: dict,
    omitted: set[tuple[str | int, ...]],
    *,
    rule_code: str | None,
) -> None:
    allowed = {
        "chart.empty-phrases-key": "phrases",
        "timeline.empty-arrangement-tempos-key": "tempos",
    }
    field = allowed.get(rule_code)
    if (
        field is None
        or set(operation) != {
            "operation",
            "array_path",
            "field",
            "original_sha256",
            "result_sha256",
        }
        or operation.get("field") != field
        or operation.get("array_path") != [field]
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    path = (field,)
    if path in omitted:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    if (
        not isinstance(document, dict)
        or field not in document
        or document[field] != []
        or hashlib.sha256(_canonical_json(document)).hexdigest()
        != operation.get("original_sha256")
    ):
        raise RepairPlanningError(
            "source_changed",
            "The arrangement changed after this preview. Review the safe fix again before applying it.",
        )
    del document[field]
    omitted.add(path)
    if (
        hashlib.sha256(_canonical_json(document)).hexdigest()
        != operation.get("result_sha256")
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")


def _apply_timed_event_sort_operation(
    document: dict,
    operation: dict,
    reordered: set[tuple[str | int, ...]],
    *,
    rule_code: str | None,
) -> None:
    allowed = {
        "timeline.tempos-out-of-order": (
            ("tempos",), "time", repairable_tempo_event
        ),
        "timeline.time-signatures-out-of-order": (
            ("time_signatures",), "time", repairable_time_signature_event
        ),
        "tones.changes-out-of-order": (
            ("tones", "changes"), "t", repairable_tone_change
        ),
    }
    spec = allowed.get(rule_code)
    if spec is None or set(operation) != {
        "operation",
        "array_path",
        "time_key",
        "expected_length",
        "original_sha256",
        "sorted_sha256",
        "sorted_indices",
        "moved_count",
    }:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    path, time_key, predicate = spec
    if (
        operation.get("array_path") != list(path)
        or operation.get("time_key") != time_key
        or path in reordered
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    values = _value_at_path(document, path)
    if (
        not isinstance(values, list)
        or len(values) < 2
        or operation.get("expected_length") != len(values)
        or not timed_event_stream_eligibility(values, predicate)
    ):
        raise RepairPlanningError(
            "source_changed",
            "The timed event list changed after this preview. Review the safe fix again before applying it.",
        )
    if (
        hashlib.sha256(_canonical_json(values)).hexdigest()
        != operation.get("original_sha256")
    ):
        raise RepairPlanningError(
            "source_changed",
            "The timed event list changed after this preview. Review the safe fix again before applying it.",
        )
    sorted_indices = list(sorted(
        range(len(values)), key=lambda index: values[index][time_key]
    ))
    declared_indices = operation.get("sorted_indices")
    moved_count = sum(
        index != original_index
        for index, original_index in enumerate(sorted_indices)
    )
    if (
        not moved_count
        or not isinstance(declared_indices, list)
        or len(declared_indices) != len(values)
        or any(not _integer(index) for index in declared_indices)
        or sorted(declared_indices) != list(range(len(values)))
        or declared_indices != sorted_indices
        or not _integer(operation.get("moved_count"))
        or operation["moved_count"] != moved_count
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    sorted_values = [values[index] for index in sorted_indices]
    if (
        hashlib.sha256(_canonical_json(sorted_values)).hexdigest()
        != operation.get("sorted_sha256")
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    values[:] = sorted_values
    reordered.add(path)


def _apply_reviewed_operation(
    document: dict,
    operation: dict,
    reviewed_paths: set[tuple[str | int, ...]],
    *,
    allowed_fields: frozenset[str],
) -> None:
    if set(operation) != {
        "operation", "candidate_id", "decision", "mutable_fields", "changes",
    } or operation.get("mutable_fields") != sorted(allowed_fields):
        raise RepairPlanningError(
            "invalid_plan", "The reviewed repair preview is invalid."
        )
    changes = operation.get("changes")
    if not isinstance(changes, list) or len(changes) not in {1, 2}:
        raise RepairPlanningError(
            "invalid_plan", "The reviewed repair preview is invalid."
        )
    for change in changes:
        if not isinstance(change, dict) or set(change) != {
            "target_path",
            "expected_before_sha256",
            "expected_after_sha256",
            "set_fields",
            "remove_fields",
        }:
            raise RepairPlanningError(
                "invalid_plan", "The reviewed repair preview is invalid."
            )
        raw_path = change.get("target_path")
        set_fields = change.get("set_fields")
        remove_fields = change.get("remove_fields")
        if (
            not isinstance(raw_path, list)
            or not raw_path
            or not isinstance(set_fields, dict)
            or not isinstance(remove_fields, list)
            or not set(set_fields).issubset(allowed_fields)
            or not set(remove_fields).issubset(allowed_fields)
            or set(set_fields).intersection(remove_fields)
            or len(remove_fields) != len(set(remove_fields))
            or any(value is not True for value in set_fields.values())
        ):
            raise RepairPlanningError(
                "invalid_plan", "The reviewed repair preview is invalid."
            )
        path = tuple(raw_path)
        if path in reviewed_paths:
            raise RepairPlanningError(
                "invalid_plan", "Two reviewed decisions target the same note."
            )
        note = _value_at_path(document, path)
        before_digest = change.get("expected_before_sha256")
        after_digest = change.get("expected_after_sha256")
        if (
            not isinstance(note, dict)
            or not isinstance(before_digest, str)
            or len(before_digest) != 64
            or not isinstance(after_digest, str)
            or len(after_digest) != 64
            or hashlib.sha256(_canonical_json(note)).hexdigest() != before_digest
        ):
            raise RepairPlanningError(
                "source_changed",
                "A reviewed note changed after this preview. Inspect it again before applying decisions.",
            )
        reviewed_paths.add(path)
        for field in remove_fields:
            note.pop(field, None)
        note.update(set_fields)
        if hashlib.sha256(_canonical_json(note)).hexdigest() != after_digest:
            raise RepairPlanningError(
                "invalid_plan", "The reviewed repair postcondition did not match."
            )


def _apply_muted_fret_normalization_operation(
    document: dict,
    operation: dict,
    normalized: set[tuple[tuple[str | int, ...], int]],
) -> None:
    raw_path = operation.get("note_array_path")
    if not isinstance(raw_path, list) or not raw_path:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    path = tuple(raw_path)
    notes = _value_at_path(document, path)
    if (
        not isinstance(notes, list)
        or operation.get("expected_length") != len(notes)
    ):
        raise RepairPlanningError(
            "source_changed",
            "The song changed after this preview. Review the safe fix again before applying it.",
        )

    changes = operation.get("changes")
    if not isinstance(changes, list) or not changes:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    indexes = [
        change.get("note_index") if isinstance(change, dict) else None
        for change in changes
    ]
    if (
        any(not _integer(index) for index in indexes)
        or indexes != sorted(indexes)
        or len(indexes) != len(set(indexes))
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")

    targets = []
    for change, note_index in zip(changes, indexes):
        if note_index < 0 or note_index >= len(notes):
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        note = notes[note_index]
        marker = (path, note_index)
        if (
            not isinstance(note, dict)
            or not _integer(change.get("original_fret"))
            or change["original_fret"] >= 0
            or change.get("replacement_fret") != 0
            or not isinstance(change.get("note_sha256"), str)
            or note.get("f") != change["original_fret"]
            or note.get("mt") is not True
            or hashlib.sha256(_canonical_json(note)).hexdigest()
            != change["note_sha256"]
        ):
            raise RepairPlanningError(
                "source_changed",
                "The muted note changed after this preview. Review the safe fix again before applying it.",
            )
        if marker in normalized:
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        targets.append((marker, note))

    for marker, note in targets:
        note["f"] = 0
        normalized.add(marker)


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


def _apply_timeline_marker_sort_operation(
    document: dict,
    operation: dict,
    reordered: set[tuple[str | int, ...]],
) -> None:
    field = operation.get("field")
    if (
        field not in {"beats", "sections"}
        or operation.get("array_path") != [field]
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    path = (field,)
    if path in reordered:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    markers = _value_at_path(document, path)
    if (
        not isinstance(markers, list)
        or operation.get("expected_length") != len(markers)
        or len(markers) < 2
    ):
        raise RepairPlanningError(
            "source_changed",
            "The song timeline changed after this preview. Review the safe fix "
            "again before applying it.",
        )
    identity_factory = (
        _valid_beat_identity if field == "beats" else _valid_section_identity
    )
    if not all(identity_factory(marker) is not None for marker in markers):
        raise RepairPlanningError(
            "source_changed",
            "The song timeline changed after this preview. Review the safe fix "
            "again before applying it.",
        )
    original_digest = hashlib.sha256(_canonical_json(markers)).hexdigest()
    if operation.get("original_sha256") != original_digest:
        raise RepairPlanningError(
            "source_changed",
            "The song timeline changed after this preview. Review the safe fix "
            "again before applying it.",
        )
    sorted_indices = list(sorted(
        range(len(markers)), key=lambda index: markers[index]["time"]
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
    sorted_markers = [markers[index] for index in sorted_indices]
    sorted_digest = hashlib.sha256(_canonical_json(sorted_markers)).hexdigest()
    if operation.get("sorted_sha256") != sorted_digest:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    markers[:] = sorted_markers
    reordered.add(path)


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


def _apply_redundant_handshape_delete_operation(
    document: dict,
    operation: dict,
    removed: set[tuple[tuple[str | int, ...], int]],
) -> None:
    span_kind = operation.get("span_kind")
    if span_kind not in {"zero_length", "reversed"}:
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    raw_handshape_path = operation.get("handshape_array_path")
    raw_chord_path = operation.get("chord_array_path")
    if (
        not isinstance(raw_handshape_path, list)
        or not raw_handshape_path
        or not isinstance(raw_chord_path, list)
        or not raw_chord_path
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    handshape_path = tuple(raw_handshape_path)
    chord_path = tuple(raw_chord_path)
    if (
        handshape_path[-1] != "handshapes"
        or chord_path != handshape_path[:-1] + ("chords",)
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    handshapes = _value_at_path(document, handshape_path)
    chords = _value_at_path(document, chord_path)
    if (
        not isinstance(handshapes, list)
        or not isinstance(chords, list)
        or operation.get("expected_handshape_length") != len(handshapes)
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
        handshape_index = group.get("handshape_index")
        chord_index = group.get("chord_index")
        handshape_digest = group.get("handshape_sha256")
        chord_digest = group.get("chord_sha256")
        if (
            not _integer(handshape_index)
            or not _integer(chord_index)
            or not isinstance(handshape_digest, str)
            or not isinstance(chord_digest, str)
        ):
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        if (
            handshape_index < 0
            or handshape_index >= len(handshapes)
            or chord_index < 0
            or chord_index >= len(chords)
        ):
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        handshape = handshapes[handshape_index]
        chord = chords[chord_index]
        handshape_identity = _redundant_handshape_identity(
            handshape, span_kind=span_kind
        )
        matching_chord_indexes = [
            index
            for index, candidate in enumerate(chords)
            if _chord_matches_handshape(candidate, handshape)
        ]
        if (
            handshape_identity is None
            or hashlib.sha256(handshape_identity).hexdigest() != handshape_digest
            or matching_chord_indexes != [chord_index]
            or hashlib.sha256(_canonical_json(chord)).hexdigest() != chord_digest
            or (
                span_kind == "reversed"
                and not _strict_reversed_handshape_context(
                    document, handshape, chord
                )
            )
        ):
            raise RepairPlanningError("source_changed", "The song changed after this preview.")
        marker = (handshape_path, handshape_index)
        if marker in removed:
            raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
        removed.add(marker)
        indexes.append(handshape_index)

    declared_indexes = operation.get("remove_indices")
    if (
        not isinstance(declared_indexes, list)
        or declared_indexes != sorted(indexes, reverse=True)
        or len(declared_indexes) != len(set(declared_indexes))
    ):
        raise RepairPlanningError("invalid_plan", "The repair preview is invalid.")
    for index in declared_indexes:
        del handshapes[index]


def _musical_position_count(
    document,
    operations: list[
        DeleteArrayItems | DeleteNotesMatchingChords | OmitEmptyRootArray
        | StableSortBendPoints | StableSortLyricCues
        | StableSortTimelineMarkers | StableSortTimedEvents
        | DeleteRedundantHandshapes | NormalizeMutedNegativeFrets
    ],
    rule_code: str,
) -> int:
    positions: set[bytes] = set()
    for operation in operations:
        if isinstance(operation, NormalizeMutedNegativeFrets):
            notes = _value_at_path(document, operation.note_array_path)
            chord_time = None
            if (
                len(operation.note_array_path) >= 3
                and operation.note_array_path[-1] == "notes"
                and _integer(operation.note_array_path[-2])
                and operation.note_array_path[-3] == "chords"
            ):
                chord = _value_at_path(document, operation.note_array_path[:-1])
                if isinstance(chord, dict) and _finite_number(chord.get("t")):
                    chord_time = chord["t"]
            for change in operation.changes:
                note = notes[change.note_index]
                note_time = (
                    note.get("t")
                    if isinstance(note, dict) and _finite_number(note.get("t"))
                    else chord_time
                )
                string = note.get("s") if isinstance(note, dict) else None
                positions.add(_canonical_json({"t": note_time, "s": string}))
        elif isinstance(operation, StableSortLyricCues):
            positions.add(_canonical_json({"path": [], "timeline": "lyrics"}))
        elif isinstance(operation, StableSortBendPoints):
            positions.add(_canonical_json({
                "path": list(operation.array_path[:-1]),
                "t": operation.note_time,
                "s": operation.string,
            }))
        elif isinstance(operation, StableSortTimelineMarkers):
            markers = _value_at_path(document, (operation.field,))
            for destination_index, source_index in enumerate(
                operation.sorted_indices
            ):
                if destination_index == source_index:
                    continue
                positions.add(_canonical_json({
                    "timeline": operation.field,
                    "time": markers[source_index]["time"],
                }))
        elif isinstance(operation, StableSortTimedEvents):
            values = _value_at_path(document, operation.array_path)
            for destination_index, source_index in enumerate(
                operation.sorted_indices
            ):
                if destination_index == source_index:
                    continue
                positions.add(_canonical_json({
                    "path": list(operation.array_path),
                    "time": values[source_index][operation.time_key],
                }))
        elif isinstance(operation, OmitEmptyRootArray):
            continue
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
                elif rule_code == "timeline.duplicate-section":
                    position = {
                        "time": value["time"],
                        "name": value["name"],
                        "number": value.get("number"),
                    }
                elif rule_code in {
                    "timeline.duplicate-tempo",
                    "timeline.duplicate-time-signature",
                }:
                    position = {"time": value["time"]}
                elif rule_code == "tones.duplicate-change":
                    position = {"t": value["t"], "name": value["name"]}
                else:
                    position = {"t": value["t"], "s": value["s"]}
                positions.add(_canonical_json(position))
        elif isinstance(operation, DeleteRedundantHandshapes):
            handshapes = _value_at_path(document, operation.handshape_array_path)
            for group in operation.match_groups:
                handshape = handshapes[group.handshape_index]
                positions.add(_canonical_json({
                    "time": handshape["start_time"],
                    "chord_id": handshape["chord_id"],
                }))
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


def _report_code_counts(report: dict) -> dict[str, int]:
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list):
        return {}
    counts: dict[str, int] = {}
    for item in findings:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            continue
        affected = item.get("affected_count", 1)
        if not _integer(affected) or affected < 1:
            affected = 1
        counts[item["code"]] = counts.get(item["code"], 0) + affected
    return counts


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


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
    if change_kind == "omit_empty":
        return (
            f"Omit {change_count} empty optional {item_label} across "
            f"{arrays_affected} {list_label}; preserve every musical event and "
            "other stored property."
        )
    if change_kind == "reorder":
        return (
            f"Put {change_count} {item_label} into chronological order across "
            f"{arrays_affected} {list_label}; preserve every stored entry and property."
        )
    if change_kind == "normalize":
        return (
            f"Normalize {change_count} negative {item_label} to fret 0 across "
            f"{arrays_affected} {list_label}; preserve every other stored property."
        )
    if change_kind == "remove_redundant":
        return (
            f"Remove {change_count} redundant {item_label} from "
            f"{arrays_affected} {list_label}; preserve every matching chord."
        )
    return (
        f"Remove {change_count} exact duplicate {item_label} from "
        f"{arrays_affected} {list_label}; keep the first authored copy."
    )
