"""Background orchestration for safe repairs across the current scan scope.

This module deliberately delegates every package mutation to RepairService.
It coordinates preview, progress, playback priority, cancellation, and a final
receipt without weakening the per-Feedpak plan, validation, backup, or recovery
contracts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path


BATCH_SCHEMA = "library_doctor.batch_repair.v1"
BATCH_PREVIEW_SCHEMA = "library_doctor.batch_preview.v1"
BATCH_RESULT_SCHEMA = "library_doctor.batch_result.v1"
BATCH_UNDO_PREVIEW_SCHEMA = "library_doctor.batch_undo_preview.v1"
BATCH_UNDO_RESULT_SCHEMA = "library_doctor.batch_undo_result.v1"
BATCH_FINALIZE_PREVIEW_SCHEMA = "library_doctor.batch_finalize_preview.v1"
BATCH_FINALIZE_RESULT_SCHEMA = "library_doctor.batch_finalize_result.v1"
BATCH_CHECKPOINT_SCHEMA = "library_doctor.batch_checkpoint.v1"
MAX_BATCH_PACKAGES = 10_000
CHECKPOINT_INTERVAL_SECONDS = 60.0
CHECKPOINT_PACKAGE_INTERVAL = 100
_SKIPPED_REPAIR_CODES = {
    "source_changed",
    "nothing_to_repair",
    "package_changed",
    "package_unavailable",
    "member_unavailable",
    "zero_length_handshape_requires_review",
    "reversed_handshape_requires_review",
    "full_mix_unavailable",
    "song_too_short",
}
_SKIPPED_RESTORE_CODES = {
    "backup_mismatch",
    "backup_unavailable",
    "backup_unreadable",
    "member_unavailable",
    "package_changed",
    "package_unavailable",
}
_SKIPPED_FINALIZE_CODES = {
    "backup_mismatch",
    "backup_unavailable",
    "backup_unreadable",
    "member_unavailable",
    "package_changed",
    "package_unavailable",
}


class BatchRepairError(ValueError):
    """Stable user-facing error for an invalid batch state transition."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(value: dict) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BatchRepairManager:
    """Run read-only batch planning and confirmed package repairs in a worker."""

    def __init__(
        self,
        *,
        config_dir: Path,
        scanner,
        repair_service,
        repair_error_type,
        log,
        legacy_schemas: dict | None = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._scanner = scanner
        self._repair_service = repair_service
        self._repair_error_type = repair_error_type
        self._log = log
        compatibility = legacy_schemas if isinstance(legacy_schemas, dict) else {}
        self._legacy_batch_result_schemas = frozenset(
            item
            for item in compatibility.get("batch_result", ())
            if isinstance(item, str)
        )
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._plans: list[dict] = []
        self._snapshot: dict | None = None
        self._undo_plans: list[dict] = []
        self._finalize_plans: list[dict] = []
        self._state = self._initial_state()
        latest = self._read_last_result()
        checkpoint = self._read_checkpoint()
        recovered_checkpoint = False
        if (
            isinstance(checkpoint, dict)
            and float(checkpoint.get("checkpointed_at") or 0)
            > float((latest or {}).get("completed_at") or 0)
        ):
            latest = self._checkpoint_result(checkpoint)
            recovered_checkpoint = True
        if latest is not None:
            normalized = self._refresh_result_counts(latest)
            if normalized or recovered_checkpoint:
                self._write_last_result(latest)
        if recovered_checkpoint:
            self._delete_checkpoint()
        self._state["last_result"] = latest

    @staticmethod
    def _initial_state() -> dict:
        return {
            "schema": BATCH_SCHEMA,
            "phase": "idle",
            "running": False,
            "mode": None,
            "message": "",
            "target": None,
            "deep_audio": False,
            "include_preview_repairs": False,
            "total": 0,
            "done": 0,
            "current": "",
            "started_at": None,
            "completed_at": None,
            "packages_per_second": 0.0,
            "eta_seconds": None,
            "live_outcomes": {
                "completed": 0,
                "repaired": 0,
                "partial": 0,
                "skipped": 0,
                "failed": 0,
                "previews_repaired": 0,
            },
            "preview": None,
            "undo_preview": None,
            "undo_result": None,
            "finalize_preview": None,
            "finalize_result": None,
            "result": None,
            "last_result": None,
        }

    def status(self) -> dict:
        with self._lock:
            status = copy.deepcopy(self._state)
        if status.get("running"):
            # The main status endpoint is polled several times per second. Keep
            # progress responsive for large libraries by omitting package-level
            # rows that the UI does not render until the worker has stopped.
            for key in (
                "preview", "undo_preview", "undo_result", "finalize_preview",
                "finalize_result", "result", "last_result"
            ):
                payload = status.get(key)
                if not isinstance(payload, dict):
                    continue
                payload.pop("packages", None)
                payload.pop("blocked", None)
                payload.pop("outcomes", None)
        return status

    @staticmethod
    def _refresh_result_counts(result: dict) -> bool:
        outcomes = result.get("outcomes")
        if not isinstance(outcomes, list):
            return False
        normalized = False
        for item in outcomes:
            if (
                isinstance(item, dict)
                and item.get("outcome") == "restored"
                and item.get("file_state") != "restored"
            ):
                # Older receipts changed the outcome after Undo but retained
                # the pre-Undo file state. The restored outcome is authoritative.
                item["file_state"] = "restored"
                normalized = True
        currently_repaired = [
            item for item in outcomes
            if isinstance(item, dict) and (
                item.get("outcome") in {"success", "finalized", "partial"}
                or bool(item.get("preview_repaired"))
            )
        ]
        restored = [
            item for item in outcomes
            if isinstance(item, dict) and item.get("outcome") == "restored"
        ]
        result["currently_repaired_count"] = len(currently_repaired)
        result["restored_count"] = len(restored)
        result["finalized_count"] = sum(
            1 for item in outcomes
            if isinstance(item, dict) and item.get("outcome") == "finalized"
        )
        result["current_removed_count"] = sum(
            int(item.get("removed_count") or 0)
            for item in currently_repaired
            if item.get("outcome") != "restored"
        )
        result["current_change_count"] = sum(
            (
                1 if item.get("outcome") == "restored"
                and item.get("preview_repaired") else
                int(item.get("change_count", item.get("removed_count")) or 0)
            )
            for item in currently_repaired
        )
        undoable_count = sum(
            1 for item in outcomes
            if (
                isinstance(item, dict)
                and item.get("outcome") in {"success", "partial"}
                and isinstance(item.get("backup_id"), str)
                and item.get("undo_available", True)
            )
        )
        result["undoable_count"] = undoable_count
        preview_cleanup_required_count = sum(
            1 for item in outcomes
            if isinstance(item, dict) and item.get("preview_cleanup_required")
        )
        result["preview_cleanup_required_count"] = (
            preview_cleanup_required_count
        )
        finalized_count = result["finalized_count"]
        preview_count = int(
            result.get("preview_successful_count")
            or sum(1 for item in outcomes if item.get("preview_repaired"))
        )
        if result.get("include_preview_repairs") and (
            preview_count or preview_cleanup_required_count
        ):
            finalized_preview_count = max(
                0, preview_count - preview_cleanup_required_count
            )
            preview_summary = (
                f"{finalized_preview_count} automatic preview repair"
                f"{' is' if finalized_preview_count == 1 else 's are'} finalized without a retained preview recovery copy."
            )
            if preview_cleanup_required_count:
                preview_summary += (
                    f" {preview_cleanup_required_count} temporary preview recovery "
                    f"cop{'y still needs' if preview_cleanup_required_count == 1 else 'ies still need'} explicit cleanup."
                )
            result["recovery_summary"] = (
                f"{undoable_count} Feedpak"
                f"{' retains' if undoable_count == 1 else 's retain'} a song-data Undo backup. "
                f"{preview_summary} Undo restores only saved song data; repaired previews remain in place."
            )
            return normalized
        if undoable_count and finalized_count:
            undoable_label = "Feedpak" if undoable_count == 1 else "Feedpaks"
            finalized_label = "Feedpak" if finalized_count == 1 else "Feedpaks"
            undoable_verb = "retains" if undoable_count == 1 else "retain"
            finalized_verb = "has" if finalized_count == 1 else "have"
            result["recovery_summary"] = (
                f"{undoable_count} repaired {undoable_label} still {undoable_verb} a recovery copy and can be undone; "
                f"{finalized_count} repaired {finalized_label} {finalized_verb} been finalized and no longer use recovery storage."
            )
        elif undoable_count:
            result["recovery_summary"] = (
                "Every currently undoable Feedpak retains its own recovery copy. Finalize a repair only after you are satisfied with it."
            )
        elif finalized_count:
            result["recovery_summary"] = (
                "All currently repaired Feedpaks in this batch have been finalized. Their recovery copies were removed and Undo is no longer available."
            )
        return normalized

    def start_preview(self, snapshot: dict) -> dict:
        candidates = snapshot.get("candidates") if isinstance(snapshot, dict) else None
        if not isinstance(candidates, list):
            raise BatchRepairError("invalid_scope", "The current scan scope is unavailable.")
        if len(candidates) > MAX_BATCH_PACKAGES:
            raise BatchRepairError(
                "scope_too_large",
                "This scan scope is too large for one batch. Scan and repair smaller folders.",
            )
        with self._lock:
            if self._state["running"]:
                raise BatchRepairError(
                    "batch_busy", "A Library Doctor batch operation is already running."
                )
            reserved, reason = self._scanner.begin_batch_operation()
            if not reserved:
                raise BatchRepairError("library_busy", reason)
            self._cancel.clear()
            self._plans = []
            self._snapshot = copy.deepcopy(snapshot)
            started_at = time.time()
            self._state = {
                **self._initial_state(),
                "phase": "previewing",
                "running": True,
                "mode": "preview",
                "message": "Checking completed-scan repair candidates without changing any Feedpaks.",
                "target": copy.deepcopy(snapshot.get("target")),
                "deep_audio": bool(snapshot.get("deep_audio")),
                "include_preview_repairs": bool(
                    snapshot.get("include_preview_repairs")
                ),
                "total": len(candidates),
                "started_at": started_at,
                "last_result": self._state.get("last_result"),
            }
            self._thread = threading.Thread(
                target=self._run_preview,
                args=(copy.deepcopy(snapshot),),
                name="library-doctor-batch-preview",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._scanner.finish_repair()
                self._state["running"] = False
                self._state["phase"] = "error"
                raise
            return copy.deepcopy(self._state)

    def start_apply(self, batch_plan_id: str) -> dict:
        if not isinstance(batch_plan_id, str) or len(batch_plan_id) != 64:
            raise BatchRepairError(
                "invalid_batch_plan", "Review the batch again before applying it."
            )
        with self._lock:
            preview = self._state.get("preview")
            if self._state["running"]:
                raise BatchRepairError(
                    "batch_busy", "A Library Doctor batch operation is already running."
                )
            if (
                self._state["phase"] != "ready"
                or not isinstance(preview, dict)
                or preview.get("batch_plan_id") != batch_plan_id
                or not self._plans
            ):
                raise BatchRepairError(
                    "stale_batch_plan", "This batch preview is no longer current. Review it again."
                )
            reserved, reason = self._scanner.begin_batch_operation()
            if not reserved:
                raise BatchRepairError("library_busy", reason)
            self._cancel.clear()
            started_at = time.time()
            self._state.update({
                "phase": "applying",
                "running": True,
                "mode": "apply",
                "message": "Applying one validated Feedpak transaction at a time.",
                "total": len(self._plans),
                "done": 0,
                "current": "",
                "started_at": started_at,
                "completed_at": None,
                "packages_per_second": 0.0,
                "eta_seconds": None,
                "live_outcomes": self._initial_state()["live_outcomes"],
                "result": None,
            })
            self._delete_checkpoint()
            plans = copy.deepcopy(self._plans)
            snapshot = copy.deepcopy(self._snapshot) if self._snapshot else {}
            self._thread = threading.Thread(
                target=self._run_apply,
                args=(batch_plan_id, plans, snapshot),
                name="library-doctor-batch-apply",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._scanner.finish_repair()
                self._state["running"] = False
                self._state["phase"] = "error"
                raise
            return copy.deepcopy(self._state)

    def start_undo_preview(self) -> dict:
        """Review every still-repaired package in the latest batch receipt."""
        with self._lock:
            if self._state["running"]:
                raise BatchRepairError(
                    "batch_busy", "A Library Doctor batch operation is already running."
                )
            source = self._state.get("result") or self._state.get("last_result")
            if not isinstance(source, dict):
                raise BatchRepairError(
                    "batch_result_unavailable",
                    "No completed batch repair is available to undo.",
                )
            self._refresh_result_counts(source)
            outcomes = source.get("outcomes")
            if not isinstance(outcomes, list):
                raise BatchRepairError(
                    "batch_result_unavailable",
                    "The latest batch result cannot be reviewed safely.",
                )
            candidates = [
                copy.deepcopy(item) for item in outcomes
                if (
                    isinstance(item, dict)
                    and item.get("outcome") in {"success", "partial"}
                    and isinstance(item.get("package"), str)
                    and isinstance(item.get("backup_id"), str)
                )
            ]
            if not candidates:
                raise BatchRepairError(
                    "nothing_to_restore",
                    "Every successful repair in this batch has already been undone.",
                )
            if len(candidates) > MAX_BATCH_PACKAGES:
                raise BatchRepairError(
                    "scope_too_large",
                    "This batch result is too large to undo in one operation.",
                )
            reserved, reason = self._scanner.begin_batch_operation()
            if not reserved:
                raise BatchRepairError("library_busy", reason)
            self._cancel.clear()
            self._undo_plans = []
            started_at = time.time()
            self._state.update({
                "phase": "undo_previewing",
                "running": True,
                "mode": "undo-preview",
                "message": "Checking retained backups without changing any Feedpaks.",
                "target": copy.deepcopy(source.get("target")),
                "deep_audio": bool(source.get("deep_audio")),
                "include_preview_repairs": bool(
                    source.get("include_preview_repairs")
                ),
                "total": len(candidates),
                "done": 0,
                "current": "",
                "started_at": started_at,
                "completed_at": None,
                "packages_per_second": 0.0,
                "eta_seconds": None,
                "undo_preview": None,
                "undo_result": None,
            })
            self._thread = threading.Thread(
                target=self._run_undo_preview,
                args=(copy.deepcopy(source), candidates),
                name="library-doctor-batch-undo-preview",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._scanner.finish_repair()
                self._state["running"] = False
                self._state["phase"] = "error"
                raise
            return self.status()

    def start_undo_apply(self, undo_plan_id: str) -> dict:
        if not isinstance(undo_plan_id, str) or len(undo_plan_id) != 64:
            raise BatchRepairError(
                "invalid_undo_plan", "Review Undo all again before applying it."
            )
        with self._lock:
            preview = self._state.get("undo_preview")
            if self._state["running"]:
                raise BatchRepairError(
                    "batch_busy", "A Library Doctor batch operation is already running."
                )
            if (
                self._state["phase"] != "undo_ready"
                or not isinstance(preview, dict)
                or preview.get("undo_plan_id") != undo_plan_id
                or not self._undo_plans
            ):
                raise BatchRepairError(
                    "stale_undo_plan",
                    "This Undo preview is no longer current. Review Undo all again.",
                )
            reserved, reason = self._scanner.begin_batch_operation()
            if not reserved:
                raise BatchRepairError("library_busy", reason)
            self._cancel.clear()
            started_at = time.time()
            self._state.update({
                "phase": "undoing",
                "running": True,
                "mode": "undo-apply",
                "message": "Restoring one validated Feedpak at a time.",
                "total": len(self._undo_plans),
                "done": 0,
                "current": "",
                "started_at": started_at,
                "completed_at": None,
                "packages_per_second": 0.0,
                "eta_seconds": None,
                "undo_result": None,
            })
            plans = copy.deepcopy(self._undo_plans)
            self._thread = threading.Thread(
                target=self._run_undo_apply,
                args=(undo_plan_id, plans),
                name="library-doctor-batch-undo-apply",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._scanner.finish_repair()
                self._state["running"] = False
                self._state["phase"] = "error"
                raise
            return self.status()

    def start_finalize_preview(self) -> dict:
        """Review recovery copies that can be removed from the latest batch."""
        with self._lock:
            if self._state["running"]:
                raise BatchRepairError(
                    "batch_busy", "A Library Doctor batch operation is already running."
                )
            source = self._state.get("result") or self._state.get("last_result")
            if not isinstance(source, dict):
                raise BatchRepairError(
                    "batch_result_unavailable",
                    "No completed batch repair is available to finalize.",
                )
            self._refresh_result_counts(source)
            outcomes = source.get("outcomes")
            if not isinstance(outcomes, list):
                raise BatchRepairError(
                    "batch_result_unavailable",
                    "The latest batch result cannot be reviewed safely.",
                )
            candidates = []
            for item in outcomes:
                if not isinstance(item, dict) or not isinstance(
                    item.get("package"), str
                ):
                    continue
                if (
                    item.get("outcome") in {"success", "partial"}
                    and item.get("undo_available", True)
                    and isinstance(item.get("backup_id"), str)
                ):
                    candidate = copy.deepcopy(item)
                    candidate["recovery_kind"] = "song_data"
                    candidates.append(candidate)
                if (
                    item.get("preview_cleanup_required")
                    and isinstance(item.get("preview_cleanup_backup_id"), str)
                ):
                    candidate = copy.deepcopy(item)
                    candidate["backup_id"] = item["preview_cleanup_backup_id"]
                    candidate["recovery_kind"] = "preview"
                    candidates.append(candidate)
            if not candidates:
                raise BatchRepairError(
                    "nothing_to_finalize",
                    "This batch has no remaining recovery copies to finalize.",
                )
            if len(candidates) > MAX_BATCH_PACKAGES:
                raise BatchRepairError(
                    "scope_too_large",
                    "This batch result is too large to finalize in one operation.",
                )
            reserved, reason = self._scanner.begin_batch_operation()
            if not reserved:
                raise BatchRepairError("library_busy", reason)
            self._cancel.clear()
            self._undo_plans = []
            self._finalize_plans = []
            started_at = time.time()
            self._state.update({
                "phase": "finalize_previewing",
                "running": True,
                "mode": "finalize-preview",
                "message": (
                    "Verifying recovery copies without changing any Feedpaks."
                ),
                "target": copy.deepcopy(source.get("target")),
                "deep_audio": False,
                "total": len(candidates),
                "done": 0,
                "current": "",
                "started_at": started_at,
                "completed_at": None,
                "packages_per_second": 0.0,
                "eta_seconds": None,
                "undo_preview": None,
                "finalize_preview": None,
                "finalize_result": None,
            })
            self._thread = threading.Thread(
                target=self._run_finalize_preview,
                args=(copy.deepcopy(source), candidates),
                name="library-doctor-batch-finalize-preview",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._scanner.finish_repair()
                self._state["running"] = False
                self._state["phase"] = "error"
                raise
            return self.status()

    def start_finalize_apply(self, finalize_plan_id: str) -> dict:
        if not isinstance(finalize_plan_id, str) or len(finalize_plan_id) != 64:
            raise BatchRepairError(
                "invalid_finalize_plan",
                "Review Finalize all again before applying it.",
            )
        with self._lock:
            preview = self._state.get("finalize_preview")
            if self._state["running"]:
                raise BatchRepairError(
                    "batch_busy", "A Library Doctor batch operation is already running."
                )
            if (
                self._state["phase"] != "finalize_ready"
                or not isinstance(preview, dict)
                or preview.get("finalize_plan_id") != finalize_plan_id
                or not self._finalize_plans
            ):
                raise BatchRepairError(
                    "stale_finalize_plan",
                    "This finalization review is no longer current. Review Finalize all again.",
                )
            reserved, reason = self._scanner.begin_batch_operation()
            if not reserved:
                raise BatchRepairError("library_busy", reason)
            self._cancel.clear()
            started_at = time.time()
            self._state.update({
                "phase": "finalizing",
                "running": True,
                "mode": "finalize-apply",
                "message": "Verifying and removing one recovery copy at a time.",
                "total": len(self._finalize_plans),
                "done": 0,
                "current": "",
                "started_at": started_at,
                "completed_at": None,
                "packages_per_second": 0.0,
                "eta_seconds": None,
                "finalize_result": None,
            })
            plans = copy.deepcopy(self._finalize_plans)
            self._thread = threading.Thread(
                target=self._run_finalize_apply,
                args=(finalize_plan_id, plans),
                name="library-doctor-batch-finalize-apply",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._scanner.finish_repair()
                self._state["running"] = False
                self._state["phase"] = "error"
                raise
            return self.status()

    def cancel(self) -> bool:
        with self._lock:
            if not self._state["running"]:
                return False
            self._cancel.set()
            self._state["phase"] = "cancelling"
            mode = self._state.get("mode")
            if mode in {"apply", "undo-apply", "finalize-apply"}:
                self._state["message"] = (
                    "Finishing the current Feedpak operation before stopping."
                )
            else:
                self._state["message"] = "Stopping the read-only preview."
            return True

    def invalidate_ready(self, reason: str) -> bool:
        """Expire a completed preview when scan scope or package data changes."""
        with self._lock:
            if self._state["running"] or self._state["phase"] not in {
                "ready", "undo_ready", "finalize_ready"
            }:
                return False
            self._plans = []
            self._snapshot = None
            self._undo_plans = []
            self._finalize_plans = []
            self._state.update({
                "phase": "stale",
                "message": str(reason),
                "preview": None,
                "undo_preview": None,
                "finalize_preview": None,
                "total": 0,
                "done": 0,
                "current": "",
            })
            return True

    def mark_restored(
        self,
        package: str,
        backup_id: str,
        *,
        cache_updated: bool | None = None,
    ) -> bool:
        """Keep the latest batch outcome accurate after any successful Undo."""
        updated = False
        latest = None
        with self._lock:
            for key in ("result", "last_result"):
                payload = self._state.get(key)
                outcomes = payload.get("outcomes") if isinstance(payload, dict) else None
                if not isinstance(outcomes, list):
                    continue
                payload_updated = False
                for outcome in outcomes:
                    if (
                        isinstance(outcome, dict)
                        and outcome.get("package") == package
                        and outcome.get("backup_id") == backup_id
                        and outcome.get("outcome") in {"success", "partial"}
                    ):
                        outcome["outcome"] = "restored"
                        outcome["message"] = (
                            "The original song data saved before this batch repair was restored. The finalized generated preview remains in the Feedpak."
                            if outcome.get("preview_repaired") else
                            "The original song data saved before this batch repair was restored."
                        )
                        outcome["file_state"] = "restored"
                        if cache_updated is not None:
                            outcome["cache_updated"] = bool(cache_updated)
                        outcome["restored_at"] = time.time()
                        payload_updated = True
                        updated = True
                if payload_updated:
                    self._refresh_result_counts(payload)
            if updated and isinstance(self._state.get("last_result"), dict):
                latest = copy.deepcopy(self._state["last_result"])
        if latest is not None:
            self._write_last_result(latest)
        return updated

    @staticmethod
    def _mark_recovery_finalized(
        outcome: dict,
        backup_id: str,
        *,
        package_state: str | None = None,
        finalized_at: float | None = None,
    ) -> bool:
        """Apply one verified cleanup receipt to one stored batch outcome."""
        when = finalized_at if finalized_at is not None else time.time()
        if outcome.get("preview_cleanup_backup_id") == backup_id:
            outcome["preview_cleanup_required"] = False
            outcome["preview_cleanup_backup_id"] = None
            outcome["preview_cleanup_error"] = ""
            outcome["preview_cleanup_size_bytes"] = 0
            outcome["preview_finalized"] = True
            if outcome.get("undo_available"):
                outcome["message"] = (
                    "The automatic preview repair and its recovery cleanup completed. "
                    "The separate song-data backup remains available for Undo."
                )
            elif outcome.get("outcome") != "restored":
                outcome["outcome"] = "finalized"
                outcome["message"] = (
                    "The repaired Feedpak was kept and its temporary preview recovery copy was removed."
                )
            outcome["preview_cleanup_finalized_at"] = when
            return True
        if (
            outcome.get("backup_id") != backup_id
            or outcome.get("outcome") not in {"success", "partial"}
        ):
            return False
        outcome["undo_available"] = False
        outcome["song_data_finalized"] = True
        if package_state == "restored":
            outcome["outcome"] = "restored"
            outcome["file_state"] = "restored"
            outcome["message"] = (
                "The original song data was already restored. Its redundant recovery copy was removed."
            )
        elif outcome.get("preview_cleanup_required"):
            outcome["outcome"] = "success"
            outcome["message"] = (
                "The song-data recovery copy was removed. The repaired preview remains active, but its separate temporary recovery copy still needs cleanup."
            )
        else:
            outcome["outcome"] = "finalized"
            outcome["message"] = (
                "The repaired Feedpak was kept and its recovery copy was removed. Undo is no longer available."
            )
        outcome["finalized_at"] = when
        return True

    def mark_finalized(
        self,
        package: str,
        backup_id: str,
        *,
        package_state: str | None = None,
    ) -> bool:
        """Keep the latest batch outcome accurate after recovery finalization."""
        updated = False
        latest = None
        with self._lock:
            for key in ("result", "last_result"):
                payload = self._state.get(key)
                outcomes = payload.get("outcomes") if isinstance(payload, dict) else None
                if not isinstance(outcomes, list):
                    continue
                payload_updated = False
                for outcome in outcomes:
                    if not isinstance(outcome, dict) or outcome.get("package") != package:
                        continue
                    if self._mark_recovery_finalized(
                        outcome,
                        backup_id,
                        package_state=package_state,
                    ):
                        payload_updated = True
                        updated = True
                if payload_updated:
                    self._refresh_result_counts(payload)
            if updated and isinstance(self._state.get("last_result"), dict):
                latest = copy.deepcopy(self._state["last_result"])
        if latest is not None:
            self._write_last_result(latest)
        return updated

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _wait_for_playback(self, resume_phase: str) -> bool:
        if self._scanner.playback_active():
            with self._lock:
                self._state["phase"] = "paused"
                self._state["message"] = (
                    "Batch Undo paused while a song is open. It will resume automatically."
                    if resume_phase.startswith("undo") else
                    "Batch finalization paused while a song is open. It will resume automatically."
                    if resume_phase.startswith("finaliz") else
                    "Batch repair paused while a song is open. It will resume automatically."
                )
        ready = self._scanner.wait_for_playback(self._cancel)
        if ready:
            with self._lock:
                self._state["phase"] = resume_phase
                messages = {
                    "previewing": "Checking completed-scan repair candidates without changing any Feedpaks.",
                    "applying": "Applying one validated Feedpak transaction at a time.",
                    "undo_previewing": "Checking retained backups without changing any Feedpaks.",
                    "undoing": "Restoring one validated Feedpak at a time.",
                    "finalize_previewing": "Verifying recovery copies without changing any Feedpaks.",
                    "finalizing": "Verifying and removing one recovery copy at a time.",
                }
                self._state["message"] = messages.get(
                    resume_phase, "Continuing the batch operation."
                )
        return ready

    def _progress(self, *, done: int, total: int, started: float) -> None:
        elapsed = max(0.001, time.monotonic() - started)
        rate = done / elapsed if done else 0.0
        remaining = max(0, total - done)
        with self._lock:
            self._state["done"] = done
            self._state["packages_per_second"] = round(rate, 3)
            self._state["eta_seconds"] = remaining / rate if rate > 0 else None

    def _update_live_outcomes(self, outcomes: list[dict]) -> None:
        live = {
            "completed": len(outcomes),
            "repaired": sum(
                item.get("outcome") in {"success", "finalized"}
                for item in outcomes
                if isinstance(item, dict)
            ),
            "partial": sum(
                item.get("outcome") == "partial"
                for item in outcomes
                if isinstance(item, dict)
            ),
            "skipped": sum(
                item.get("outcome") == "skipped"
                for item in outcomes
                if isinstance(item, dict)
            ),
            "failed": sum(
                item.get("outcome") == "failed"
                for item in outcomes
                if isinstance(item, dict)
            ),
            "previews_repaired": sum(
                bool(item.get("preview_repaired"))
                for item in outcomes
                if isinstance(item, dict)
            ),
        }
        with self._lock:
            self._state["live_outcomes"] = live

    def _run_preview(self, snapshot: dict) -> None:
        candidates = snapshot["candidates"]
        total = len(candidates)
        started = time.monotonic()
        eligible = []
        plans = []
        blocked = []
        no_longer_needed = 0
        rule_totals: dict[str, dict] = {}
        try:
            for candidate in candidates:
                if self._cancel.is_set() or not self._wait_for_playback("previewing"):
                    break
                package = candidate["package"]
                with self._lock:
                    self._state["current"] = package
                package_blockers = []
                expected_signature = candidate.get("scan_signature")
                signature_checker = getattr(
                    self._scanner, "package_matches_signature", None
                )
                if (
                    isinstance(expected_signature, str)
                    and callable(signature_checker)
                    and not signature_checker(package, expected_signature)
                ):
                    package_blockers.append({
                        "code": "package_changed",
                        "message": (
                            "This Feedpak changed after the completed scan. Scan it again before including it in a batch repair."
                        ),
                    })

                requested_safe = bool(candidate.get("rule_codes"))
                safe_ready = requested_safe and not package_blockers

                preview_rule_code = candidate.get("preview_rule_code")
                preview_ready = (
                    isinstance(preview_rule_code, str) and not package_blockers
                )

                repair_summaries = []
                if safe_ready:
                    reported_by_code = {
                        summary.get("rule_code"): summary
                        for summary in (candidate.get("safe_findings") or [])
                        if isinstance(summary, dict)
                        and isinstance(summary.get("rule_code"), str)
                    }
                    for code in candidate.get("rule_codes") or []:
                        summary = copy.deepcopy(reported_by_code.get(code) or {})
                        summary.setdefault("rule_code", code)
                        summary.setdefault("title", code)
                        summary.setdefault("finding_count", 1)
                        summary.setdefault("reported_affected_count", 1)
                        repair_summaries.append(summary)
                if preview_ready:
                    repair_summaries.append({
                        "rule_code": preview_rule_code,
                        "title": "Create a standard audio preview",
                        "finding_count": 1,
                        "reported_affected_count": 1,
                    })

                if safe_ready or preview_ready:
                    safe_rule_codes = (
                        list(candidate.get("rule_codes") or [])
                        if safe_ready else []
                    )
                    rule_codes = list(safe_rule_codes)
                    if preview_ready:
                        rule_codes.append(preview_rule_code)
                    reported_affected_count = sum(
                        int(summary.get("reported_affected_count") or 0)
                        for summary in repair_summaries
                    )
                    public_item = {
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "rule_codes": rule_codes,
                        "rule_count": len(rule_codes),
                        "safe_rule_count": len(safe_rule_codes),
                        "safe_rule_codes": safe_rule_codes,
                        "preview_repair": preview_ready,
                        "preview_rule_code": (
                            preview_rule_code if preview_ready else None
                        ),
                        "reported_affected_count": reported_affected_count,
                        "repair_summaries": repair_summaries,
                        "excluded_operation_count": len(package_blockers),
                        "operation_blockers": copy.deepcopy(package_blockers),
                    }
                    eligible.append(public_item)
                    plans.append({
                        **public_item,
                        "scan_signature": expected_signature,
                    })
                    for summary in repair_summaries:
                        code = summary.get("rule_code")
                        if not isinstance(code, str):
                            continue
                        total_for_rule = rule_totals.setdefault(code, {
                            "rule_code": code,
                            "title": summary.get("title") or code,
                            "package_count": 0,
                            "finding_count": 0,
                            "reported_affected_count": 0,
                        })
                        total_for_rule["package_count"] += 1
                        total_for_rule["finding_count"] += int(
                            summary.get("finding_count") or 0
                        )
                        total_for_rule["reported_affected_count"] += int(
                            summary.get("reported_affected_count") or 0
                        )
                elif package_blockers:
                    first = package_blockers[0]
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "code": first.get("code") or "blocked",
                        "blocker_count": len(package_blockers),
                        "message": first.get("message") or (
                            "This Feedpak cannot be changed safely in the current batch."
                        ),
                    })
                else:
                    no_longer_needed += 1
                self._progress(
                    done=len(eligible) + len(blocked) + no_longer_needed,
                    total=total,
                    started=started,
                )

            cancelled = self._cancel.is_set()
            if cancelled:
                with self._lock:
                    self._plans = []
                    self._snapshot = None
                    self._state.update({
                        "phase": "cancelled",
                        "running": False,
                        "message": "Batch preview cancelled. No Feedpaks were changed.",
                        "current": "",
                        "completed_at": time.time(),
                        "preview": None,
                    })
                return

            unsigned = {
                "schema": BATCH_PREVIEW_SCHEMA,
                "target": snapshot.get("target"),
                "validator_version": snapshot.get("validator_version"),
                "deep_audio": bool(snapshot.get("deep_audio")),
                "include_preview_repairs": bool(
                    snapshot.get("include_preview_repairs")
                ),
                "packages": [
                    {
                        "package": item["package"],
                        "safe_rule_codes": item.get("safe_rule_codes") or [],
                        "preview_rule_code": item.get("preview_rule_code"),
                        "scan_signature": item.get("scan_signature"),
                    }
                    for item in plans
                ],
                "blocked": [
                    {"package": item["package"], "code": item["code"]}
                    for item in blocked
                ],
            }
            preview = {
                **unsigned,
                "batch_plan_id": _digest(unsigned),
                "created_at": time.time(),
                "scope_package_count": int(snapshot.get("scope_package_count") or 0),
                "candidate_count": total,
                "eligible_count": len(eligible),
                "blocked_count": len(blocked),
                "no_longer_needed_count": no_longer_needed,
                "reported_affected_count": sum(
                    item["reported_affected_count"] for item in eligible
                ),
                "safe_repair_package_count": sum(
                    1 for item in eligible if item["safe_rule_count"] > 0
                ),
                "preview_repair_count": sum(
                    1 for item in eligible if item["preview_repair"]
                ),
                "mixed_repair_package_count": sum(
                    1 for item in eligible
                    if item["safe_rule_count"] > 0 and item["preview_repair"]
                ),
                "excluded_operation_count": sum(
                    item["excluded_operation_count"] for item in eligible
                ),
                "rule_summaries": list(rule_totals.values()),
                "packages": eligible,
                "blocked": blocked,
                "performance": {
                    "elapsed_seconds": round(
                        max(0.0, time.monotonic() - started), 6
                    ),
                },
                "file_handling": (
                    "This review uses the completed scan and verifies that each eligible Feedpak still matches it. During repair, each package's selected fixes are recalculated and validated immediately before it is replaced; no duplicate song package is created. "
                    "Safe song-data repairs retain their normal Undo backup. Automatic preview repairs use temporary recovery data that is removed after successful validation."
                ),
            }
            with self._lock:
                self._plans = plans
                self._snapshot = copy.deepcopy(snapshot)
                self._state.update({
                    "phase": "ready",
                    "running": False,
                    "message": (
                        "Batch preview ready. No Feedpaks have been changed."
                        if eligible else
                        "No Feedpaks in this scan scope currently have an eligible safe repair."
                    ),
                    "done": total,
                    "current": "",
                    "completed_at": time.time(),
                    "eta_seconds": 0.0,
                    "preview": preview,
                })
        except Exception as exc:
            self._log.exception("Library Doctor batch preview failed: %s", exc)
            with self._lock:
                self._plans = []
                self._snapshot = None
                self._state.update({
                    "phase": "error",
                    "running": False,
                    "message": (
                        "The batch preview could not finish. No Feedpaks were changed."
                    ),
                    "current": "",
                    "completed_at": time.time(),
                })
        finally:
            self._scanner.finish_repair()

    def _build_apply_result(
        self,
        *,
        result_id: str,
        batch_plan_id: str,
        snapshot: dict,
        total: int,
        outcomes: list[dict],
        succeeded: int,
        partial: int,
        skipped: int,
        failed: int,
        change_count: int,
        removed_count: int,
        preview_succeeded: int,
        preview_failed: int,
        preview_bytes_saved: int,
        cache_refresh_failed: int,
        outcome: str,
        completed_at: float,
        performance: dict,
    ) -> dict:
        result = {
            "schema": BATCH_RESULT_SCHEMA,
            "id": result_id,
            "batch_plan_id": batch_plan_id,
            "outcome": outcome,
            "target": copy.deepcopy(snapshot.get("target")),
            "deep_audio": bool(snapshot.get("deep_audio")),
            "started_at": self._state.get("started_at"),
            "completed_at": completed_at,
            "planned_count": total,
            "completed_count": len(outcomes),
            "remaining_count": max(0, total - len(outcomes)),
            "successful_count": succeeded,
            "partial_count": partial,
            "skipped_count": skipped,
            "failed_count": failed,
            "change_count": change_count,
            "removed_count": removed_count,
            "backup_count": sum(
                1 for item in outcomes
                if isinstance(item.get("backup_id"), str)
            ),
            "preview_successful_count": preview_succeeded,
            "preview_failed_count": preview_failed,
            "preview_bytes_saved": preview_bytes_saved,
            "cache_refresh_failed_count": cache_refresh_failed,
            "performance": copy.deepcopy(performance),
            "outcomes": copy.deepcopy(outcomes),
            "include_preview_repairs": bool(
                snapshot.get("include_preview_repairs")
            ),
            "recovery_summary": (
                "Safe song-data repairs retain individual Undo backups. Successful automatic preview repairs are finalized after validation and do not leave recovery copies."
                if snapshot.get("include_preview_repairs") else
                "Every successful Feedpak has its own retained recovery backup and can be undone individually."
            ),
        }
        self._refresh_result_counts(result)
        return result

    def _run_apply(self, batch_plan_id: str, plans: list[dict], snapshot: dict) -> None:
        total = len(plans)
        started = time.monotonic()
        result_id = uuid.uuid4().hex
        last_checkpoint_at = started
        last_checkpoint_count = 0
        outcomes = []
        succeeded = 0
        partial = 0
        skipped = 0
        failed = 0
        change_count = 0
        removed_count = 0
        preview_succeeded = 0
        preview_failed = 0
        preview_bytes_saved = 0
        cache_refresh_failed = 0
        performance = {
            "signature_seconds": 0.0,
            "scan_report_lookup_seconds": 0.0,
            "song_data_repair_seconds": 0.0,
            "preview_repair_seconds": 0.0,
            "cache_refresh_seconds": 0.0,
            "checkpoint_seconds": 0.0,
            "verified_scan_report_reused_packages": 0,
            "deep_audio_reused_packages": 0,
        }

        def performance_payload() -> dict:
            elapsed = max(0.0, time.monotonic() - started)
            measured = sum(
                float(value)
                for key, value in performance.items()
                if key.endswith("_seconds")
            )
            return {
                **{
                    key: round(value, 6) if key.endswith("_seconds") else value
                    for key, value in performance.items()
                },
                "elapsed_seconds": round(elapsed, 6),
                "other_seconds": round(max(0.0, elapsed - measured), 6),
            }

        def checkpoint_if_due(*, force: bool = False) -> None:
            nonlocal last_checkpoint_at, last_checkpoint_count
            now = time.monotonic()
            due = (
                len(outcomes) > last_checkpoint_count
                and (
                    len(outcomes) - last_checkpoint_count
                    >= CHECKPOINT_PACKAGE_INTERVAL
                    or now - last_checkpoint_at >= CHECKPOINT_INTERVAL_SECONDS
                )
            )
            if not force and not due:
                return
            partial_result = self._build_apply_result(
                result_id=result_id,
                batch_plan_id=batch_plan_id,
                snapshot=snapshot,
                total=total,
                outcomes=outcomes,
                succeeded=succeeded,
                partial=partial,
                skipped=skipped,
                failed=failed,
                change_count=change_count,
                removed_count=removed_count,
                preview_succeeded=preview_succeeded,
                preview_failed=preview_failed,
                preview_bytes_saved=preview_bytes_saved,
                cache_refresh_failed=cache_refresh_failed,
                outcome="interrupted",
                completed_at=time.time(),
                performance=performance_payload(),
            )
            checkpoint_started = time.monotonic()
            self._write_checkpoint(partial_result)
            performance["checkpoint_seconds"] += max(
                0.0, time.monotonic() - checkpoint_started
            )
            last_checkpoint_at = now
            last_checkpoint_count = len(outcomes)
        try:
            for item in plans:
                if self._cancel.is_set() or not self._wait_for_playback("applying"):
                    break
                package = item["package"]
                with self._lock:
                    self._state["current"] = package
                    self._state["message"] = (
                        "Applying and validating the current Feedpak."
                    )
                safe_result = None
                preview_result = None
                preview_attempted = False
                safe_rule_codes = list(item.get("safe_rule_codes") or [])
                preview_rule_code = item.get("preview_rule_code")
                try:
                    expected_signature = item.get("scan_signature")
                    signature_verified = False
                    signature_checker = getattr(
                        self._scanner, "package_matches_signature", None
                    )
                    if isinstance(expected_signature, str) and callable(signature_checker):
                        operation_started = time.monotonic()
                        try:
                            signature_verified = bool(
                                signature_checker(package, expected_signature)
                            )
                        finally:
                            performance["signature_seconds"] += max(
                                0.0, time.monotonic() - operation_started
                            )
                        if not signature_verified:
                            raise self._repair_error_type(
                                "source_changed",
                                "This Feedpak changed after the batch preview. Scan it again before repairing it.",
                            )

                    verified_report = None
                    if snapshot.get("deep_audio"):
                        verified_reader = None
                        if signature_verified:
                            verified_reader = getattr(
                                self._scanner,
                                "deep_audio_report_for_signature",
                                None,
                            )
                        if not callable(verified_reader):
                            verified_reader = getattr(
                                self._scanner,
                                "verified_deep_audio_report",
                                None,
                            )
                        if callable(verified_reader):
                            operation_started = time.monotonic()
                            try:
                                candidate_report = verified_reader(
                                    package, expected_signature
                                )
                            finally:
                                performance["scan_report_lookup_seconds"] += max(
                                    0.0, time.monotonic() - operation_started
                                )
                            if isinstance(candidate_report, dict):
                                verified_report = candidate_report

                    if safe_rule_codes:
                        apply_options = {
                            "deep_audio": bool(snapshot.get("deep_audio")),
                            "rule_codes": safe_rule_codes,
                        }
                        if isinstance(verified_report, dict):
                            apply_options.update({
                                "verified_before_report": verified_report,
                                "source_guard": (
                                    lambda p=package, s=expected_signature:
                                    self._scanner.package_matches_signature(p, s)
                                ),
                            })
                        operation_started = time.monotonic()
                        try:
                            safe_result = self._repair_service.apply_selected(
                                package, **apply_options
                            )
                        finally:
                            performance["song_data_repair_seconds"] += max(
                                0.0, time.monotonic() - operation_started
                            )
                        if safe_result.get("verified_scan_report_reused"):
                            performance["verified_scan_report_reused_packages"] += 1
                        if safe_result.get("deep_audio_reused"):
                            performance["deep_audio_reused_packages"] += 1
                    if isinstance(preview_rule_code, str):
                        preview_attempted = True
                        with self._lock:
                            self._state["message"] = (
                                "Creating and validating the current audio preview."
                            )
                        preview_options = {}
                        if safe_result is None and isinstance(verified_report, dict):
                            preview_options.update({
                                "verified_before_report": verified_report,
                                "source_guard": (
                                    lambda p=package, s=expected_signature:
                                    self._scanner.package_matches_signature(p, s)
                                ),
                            })
                        operation_started = time.monotonic()
                        try:
                            preview_result = self._repair_service.apply_automatic_preview(
                                package,
                                preview_rule_code,
                                **preview_options,
                            )
                        finally:
                            performance["preview_repair_seconds"] += max(
                                0.0, time.monotonic() - operation_started
                            )
                        if preview_result.get("verified_scan_report_reused"):
                            performance["verified_scan_report_reused_packages"] += 1
                    result = preview_result or safe_result
                    if not isinstance(result, dict):
                        raise self._repair_error_type(
                            "nothing_to_repair",
                            "No planned batch repair remains for this Feedpak.",
                        )
                    try:
                        operation_started = time.monotonic()
                        try:
                            self._scanner.record_repair_result(
                                package,
                                result["report"],
                                deep_audio=bool(
                                    preview_result or snapshot.get("deep_audio")
                                ),
                            )
                        finally:
                            performance["cache_refresh_seconds"] += max(
                                0.0, time.monotonic() - operation_started
                            )
                        cache_updated = True
                    except Exception as exc:
                        cache_updated = False
                        cache_refresh_failed += 1
                        self._log.warning(
                            "Library Doctor batch repaired %s but could not refresh its report: %s",
                            package,
                            exc,
                        )
                    succeeded += 1
                    safe_changes = int(
                        safe_result.get(
                            "change_count", safe_result.get("removed_count")
                        ) or 0
                    ) if safe_result else 0
                    safe_removed = int(
                        safe_result.get("removed_count") or 0
                    ) if safe_result else 0
                    media_changes = 1 if preview_result else 0
                    package_changes = safe_changes + media_changes
                    change_count += package_changes
                    removed_count += safe_removed
                    if preview_result:
                        preview_succeeded += 1
                        preview_bytes_saved += max(
                            0,
                            int(
                                preview_result.get("media", {}).get(
                                    "estimated_package_savings_bytes"
                                ) or 0
                            ),
                        )
                    backup_id = safe_result.get("backup_id") if safe_result else None
                    preview_handling = (
                        preview_result.get("file_handling") or {}
                        if preview_result else {}
                    )
                    preview_cleanup_required = bool(
                        preview_handling.get("backup_cleanup_required")
                    )
                    preview_cleanup_backup_id = (
                        preview_result.get("backup_id")
                        if preview_cleanup_required and preview_result else None
                    )
                    rule_codes = list(
                        safe_result.get("rule_codes") or []
                    ) if safe_result else []
                    if preview_result:
                        rule_codes.append(preview_rule_code)
                    repair_summaries = copy.deepcopy(
                        safe_result.get("repair_summaries") or []
                    ) if safe_result else []
                    if preview_result:
                        repair_summaries.append({
                            "rule_code": preview_rule_code,
                            "title": "Create a standard audio preview",
                            "item_name": "audio preview",
                            "change_kind": "replace_media",
                            "change_count": 1,
                            "removed_count": 0,
                            "member_count": (
                                2 if preview_rule_code == "media.preview-missing"
                                else 1
                            ),
                        })
                    outcomes.append({
                        "package": package,
                        "title": result.get("report", {}).get("title") or item["title"],
                        "artist": result.get("report", {}).get("artist") or item["artist"],
                        "outcome": (
                            "success"
                            if backup_id or preview_cleanup_required else "finalized"
                        ),
                        "message": (
                            "The repair completed, but Library Doctor could not remove the temporary preview recovery copy. The repaired preview is active; remove the extra recovery copy from this result."
                            if preview_cleanup_required else
                            "Safe song-data repairs and the automatic preview repair completed. The song-data backup remains available for Undo; the preview repair is finalized."
                            if safe_result and preview_result else
                            "The automatic preview repair completed and its temporary recovery data was removed."
                            if preview_result else
                            "Repair completed and the validated Feedpak replaced the package at the same path."
                        ),
                        "backup_id": backup_id,
                        "undo_available": bool(backup_id),
                        "preview_repaired": bool(preview_result),
                        "preview_finalized": bool(
                            preview_result and not preview_cleanup_required
                        ),
                        "preview_cleanup_required": preview_cleanup_required,
                        "preview_cleanup_backup_id": preview_cleanup_backup_id,
                        "preview_cleanup_error": (
                            str(preview_handling.get("backup_cleanup_error") or "")
                            if preview_cleanup_required else ""
                        ),
                        "preview_cleanup_size_bytes": int(
                            preview_handling.get("backup_size_bytes") or 0
                        ) if preview_cleanup_required else 0,
                        "preview_rule_code": (
                            preview_rule_code if preview_result else None
                        ),
                        "media": copy.deepcopy(
                            preview_result.get("media")
                        ) if preview_result else None,
                        "change_kind": (
                            "combined" if safe_result and preview_result
                            else "replace_media" if preview_result
                            else safe_result.get("change_kind", "combined")
                        ),
                        "change_count": package_changes,
                        "removed_count": safe_removed,
                        "rule_codes": rule_codes,
                        "repair_summaries": repair_summaries,
                        "cache_updated": cache_updated,
                        "file_state": "repaired",
                    })
                except self._repair_error_type as exc:
                    if preview_attempted:
                        preview_failed += 1
                    if safe_result is not None:
                        partial += 1
                        failed += 1
                        safe_changes = int(
                            safe_result.get(
                                "change_count", safe_result.get("removed_count")
                            ) or 0
                        )
                        safe_removed = int(safe_result.get("removed_count") or 0)
                        change_count += safe_changes
                        removed_count += safe_removed
                        try:
                            self._scanner.record_repair_result(
                                package,
                                safe_result["report"],
                                deep_audio=bool(snapshot.get("deep_audio")),
                            )
                            cache_updated = True
                        except Exception as cache_exc:
                            cache_updated = False
                            cache_refresh_failed += 1
                            self._log.warning(
                                "Library Doctor partially repaired %s but could not refresh its report: %s",
                                package,
                                cache_exc,
                            )
                        outcomes.append({
                            "package": package,
                            "title": safe_result.get("report", {}).get("title") or item["title"],
                            "artist": safe_result.get("report", {}).get("artist") or item["artist"],
                            "outcome": "partial",
                            "code": exc.code,
                            "message": (
                                "The safe song-data repairs completed, but the audio preview remained unchanged: "
                                f"{exc}"
                            ),
                            "backup_id": safe_result.get("backup_id"),
                            "undo_available": bool(safe_result.get("backup_id")),
                            "preview_repaired": False,
                            "preview_rule_code": preview_rule_code,
                            "change_kind": safe_result.get("change_kind", "combined"),
                            "change_count": safe_changes,
                            "removed_count": safe_removed,
                            "rule_codes": list(safe_result.get("rule_codes") or []),
                            "repair_summaries": copy.deepcopy(
                                safe_result.get("repair_summaries") or []
                            ),
                            "cache_updated": cache_updated,
                            "file_state": "partially_repaired",
                        })
                        self._update_live_outcomes(outcomes)
                        checkpoint_if_due()
                        self._progress(
                            done=len(outcomes), total=total, started=started
                        )
                        continue
                    outcome = "skipped" if exc.code in _SKIPPED_REPAIR_CODES else "failed"
                    if outcome == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                    outcomes.append({
                        "package": package,
                        "title": item["title"],
                        "artist": item["artist"],
                        "outcome": outcome,
                        "code": exc.code,
                        "message": str(exc),
                        "backup_id": None,
                        "change_count": 0,
                        "removed_count": 0,
                        "rule_codes": list(item.get("rule_codes") or []),
                        "cache_updated": False,
                        "file_state": getattr(exc, "file_state", "unchanged"),
                    })
                except Exception as exc:
                    if preview_attempted:
                        preview_failed += 1
                    if safe_result is not None:
                        partial += 1
                        failed += 1
                        safe_changes = int(
                            safe_result.get(
                                "change_count", safe_result.get("removed_count")
                            ) or 0
                        )
                        safe_removed = int(safe_result.get("removed_count") or 0)
                        change_count += safe_changes
                        removed_count += safe_removed
                        cache_refresh_failed += 1
                        self._log.exception(
                            "Library Doctor preview repair failed after safe song-data repair for %s: %s",
                            package,
                            exc,
                        )
                        outcomes.append({
                            "package": package,
                            "title": safe_result.get("report", {}).get("title") or item["title"],
                            "artist": safe_result.get("report", {}).get("artist") or item["artist"],
                            "outcome": "partial",
                            "code": "unexpected_preview_repair_failure",
                            "message": (
                                "The safe song-data repairs completed, but the preview result requires verification. Scan this Feedpak again before retrying the preview."
                            ),
                            "backup_id": safe_result.get("backup_id"),
                            "undo_available": bool(safe_result.get("backup_id")),
                            "preview_repaired": False,
                            "preview_rule_code": preview_rule_code,
                            "change_count": safe_changes,
                            "removed_count": safe_removed,
                            "rule_codes": list(safe_result.get("rule_codes") or []),
                            "cache_updated": False,
                            "file_state": "verify_required",
                        })
                        self._update_live_outcomes(outcomes)
                        checkpoint_if_due()
                        self._progress(
                            done=len(outcomes), total=total, started=started
                        )
                        continue
                    failed += 1
                    self._log.exception(
                        "Library Doctor batch repair failed safely for %s: %s",
                        package,
                        exc,
                    )
                    outcomes.append({
                        "package": package,
                        "title": item["title"],
                        "artist": item["artist"],
                        "outcome": "failed",
                        "code": "unexpected_repair_failure",
                        "message": (
                            "The repair could not be confirmed. Scan this Feedpak before trying again."
                        ),
                        "backup_id": None,
                        "change_count": 0,
                        "removed_count": 0,
                        "rule_codes": list(item.get("rule_codes") or []),
                        "cache_updated": False,
                        "file_state": "verify_required",
                    })
                self._update_live_outcomes(outcomes)
                checkpoint_if_due()
                self._progress(done=len(outcomes), total=total, started=started)

            cancelled = self._cancel.is_set() and len(outcomes) < total
            result = self._build_apply_result(
                result_id=result_id,
                batch_plan_id=batch_plan_id,
                snapshot=snapshot,
                total=total,
                outcomes=outcomes,
                succeeded=succeeded,
                partial=partial,
                skipped=skipped,
                failed=failed,
                change_count=change_count,
                removed_count=removed_count,
                preview_succeeded=preview_succeeded,
                preview_failed=preview_failed,
                preview_bytes_saved=preview_bytes_saved,
                cache_refresh_failed=cache_refresh_failed,
                outcome="cancelled" if cancelled else "complete",
                completed_at=time.time(),
                performance=performance_payload(),
            )
            self._write_last_result(result)
            self._delete_checkpoint()
            with self._lock:
                self._state.update({
                    "phase": "cancelled" if cancelled else "completed",
                    "running": False,
                    "message": (
                        "Batch repair stopped between Feedpaks. Completed repairs were kept."
                        if cancelled else
                        "Batch repair finished. Review the package outcomes below."
                    ),
                    "done": len(outcomes),
                    "current": "",
                    "completed_at": result["completed_at"],
                    "eta_seconds": 0.0,
                    "result": result,
                    "last_result": result,
                })
        except Exception as exc:
            if outcomes:
                checkpoint_if_due(force=True)
            self._log.exception("Library Doctor batch execution failed: %s", exc)
            with self._lock:
                self._state.update({
                    "phase": "error",
                    "running": False,
                    "message": (
                        "Batch processing stopped unexpectedly. Completed package receipts and backups were kept."
                    ),
                    "current": "",
                    "completed_at": time.time(),
                })
        finally:
            self._scanner.finish_repair()

    def _run_undo_preview(self, source: dict, candidates: list[dict]) -> None:
        total = len(candidates)
        started = time.monotonic()
        eligible = []
        plans = []
        blocked = []
        deep_audio = bool(source.get("deep_audio"))
        try:
            for candidate in candidates:
                if (
                    self._cancel.is_set()
                    or not self._wait_for_playback("undo_previewing")
                ):
                    break
                package = candidate["package"]
                with self._lock:
                    self._state["current"] = package
                try:
                    plan = self._repair_service.preview_restore(
                        package,
                        candidate["backup_id"],
                        deep_audio=deep_audio,
                    )
                except self._repair_error_type as exc:
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "backup_id": candidate.get("backup_id"),
                        "code": exc.code,
                        "message": str(exc),
                    })
                except Exception as exc:
                    self._log.exception(
                        "Library Doctor batch Undo preview failed safely for %s: %s",
                        package,
                        exc,
                    )
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "backup_id": candidate.get("backup_id"),
                        "code": "unexpected_restore_preview_failure",
                        "message": (
                            "This recovery backup could not be reviewed safely and was excluded."
                        ),
                    })
                else:
                    public_item = {
                        "package": package,
                        "title": plan.get("title") or candidate.get("title") or package,
                        "artist": plan.get("artist") or candidate.get("artist") or "",
                        "backup_id": candidate["backup_id"],
                        "change_kind": plan.get("change_kind", "combined"),
                        "change_count": int(
                            plan.get(
                                "change_count", candidate.get("removed_count")
                            ) or 0
                        ),
                        "removed_count": int(candidate.get("removed_count") or 0),
                        "member_count": int(plan.get("member_count") or 0),
                        "rule_codes": list(candidate.get("rule_codes") or []),
                        "preview_repaired": bool(
                            candidate.get("preview_repaired")
                        ),
                        "repair_summaries": copy.deepcopy(
                            plan.get("repair_summaries") or []
                        ),
                    }
                    eligible.append(public_item)
                    plans.append({
                        **public_item,
                        "plan_id": plan["plan_id"],
                    })
                self._progress(
                    done=len(eligible) + len(blocked),
                    total=total,
                    started=started,
                )

            if self._cancel.is_set():
                with self._lock:
                    self._undo_plans = []
                    self._state.update({
                        "phase": "undo_cancelled",
                        "running": False,
                        "message": "Undo preview cancelled. No Feedpaks were changed.",
                        "current": "",
                        "completed_at": time.time(),
                        "undo_preview": None,
                    })
                return

            unsigned = {
                "schema": BATCH_UNDO_PREVIEW_SCHEMA,
                "batch_result_id": source.get("id"),
                "packages": [
                    {
                        "package": item["package"],
                        "backup_id": item["backup_id"],
                        "plan_id": item["plan_id"],
                    }
                    for item in plans
                ],
                "blocked": [
                    {
                        "package": item["package"],
                        "backup_id": item["backup_id"],
                        "code": item["code"],
                    }
                    for item in blocked
                ],
            }
            preview = {
                **unsigned,
                "undo_plan_id": _digest(unsigned),
                "created_at": time.time(),
                "candidate_count": total,
                "eligible_count": len(eligible),
                "blocked_count": len(blocked),
                "already_restored_count": int(source.get("restored_count") or 0),
                "entries_to_restore": sum(
                    item["removed_count"] for item in eligible
                ),
                "changes_to_restore": sum(
                    item["change_count"] for item in eligible
                ),
                "changed_member_count": sum(
                    item["member_count"] for item in eligible
                ),
                "packages": eligible,
                "blocked": blocked,
                "file_handling": (
                    "Each eligible Feedpak is checked and restored separately from its retained backup. "
                    "Only song-data files saved by its repair are restored; unrelated package files and the backup are preserved."
                ),
            }
            with self._lock:
                self._undo_plans = plans
                self._state.update({
                    "phase": "undo_ready",
                    "running": False,
                    "message": (
                        "Undo preview ready. No Feedpaks have been changed."
                        if eligible else
                        "No remaining batch repairs can currently be undone automatically."
                    ),
                    "done": total,
                    "current": "",
                    "completed_at": time.time(),
                    "eta_seconds": 0.0,
                    "undo_preview": preview,
                })
        except Exception as exc:
            self._log.exception("Library Doctor batch Undo preview failed: %s", exc)
            with self._lock:
                self._undo_plans = []
                self._state.update({
                    "phase": "error",
                    "running": False,
                    "message": (
                        "The Undo preview could not finish. No Feedpaks were changed."
                    ),
                    "current": "",
                    "completed_at": time.time(),
                })
        finally:
            self._scanner.finish_repair()

    def _run_undo_apply(self, undo_plan_id: str, plans: list[dict]) -> None:
        total = len(plans)
        started = time.monotonic()
        outcomes = []
        restored = 0
        skipped = 0
        failed = 0
        restored_changes = 0
        restored_entries = 0
        cache_refresh_failed = 0
        deep_audio = bool(self._state.get("deep_audio"))
        try:
            for item in plans:
                if self._cancel.is_set() or not self._wait_for_playback("undoing"):
                    break
                package = item["package"]
                with self._lock:
                    self._state["current"] = package
                try:
                    result = self._repair_service.restore(
                        package,
                        item["backup_id"],
                        deep_audio=deep_audio,
                    )
                    try:
                        self._scanner.record_repair_result(
                            package,
                            result["report"],
                            deep_audio=deep_audio,
                        )
                        cache_updated = True
                    except Exception as exc:
                        cache_updated = False
                        cache_refresh_failed += 1
                        self._log.warning(
                            "Library Doctor restored %s but could not refresh its report: %s",
                            package,
                            exc,
                        )
                    self.mark_restored(
                        package,
                        item["backup_id"],
                        cache_updated=cache_updated,
                    )
                    restored += 1
                    restored_change_count = int(
                        result.get("change_count", item.get("change_count")) or 0
                    )
                    restored_changes += restored_change_count
                    restored_entries += int(item.get("removed_count") or 0)
                    outcomes.append({
                        "package": package,
                        "title": result.get("title") or item["title"],
                        "artist": result.get("artist") or item["artist"],
                        "outcome": "restored",
                        "message": (
                            "Original song data restored and validated; the finalized generated preview remains in the Feedpak. The redundant song-data recovery copy was removed."
                            if item.get("preview_repaired") and result.get("file_handling", {}).get("backup_removed") else
                            "Original song data restored and validated; the finalized generated preview remains in the Feedpak. The redundant song-data recovery copy could not be removed automatically."
                            if item.get("preview_repaired") else
                            "Original song data restored and validated; the redundant recovery copy was removed."
                            if result.get("file_handling", {}).get("backup_removed") else
                            "Original song data restored and validated. The redundant recovery copy could not be removed automatically."
                        ),
                        "backup_id": item["backup_id"],
                        "change_kind": result.get(
                            "change_kind", item.get("change_kind", "combined")
                        ),
                        "change_count": restored_change_count,
                        "restored_count": int(item.get("removed_count") or 0),
                        "repair_summaries": copy.deepcopy(
                            result.get("repair_summaries")
                            or item.get("repair_summaries")
                            or []
                        ),
                        "cache_updated": cache_updated,
                        "file_state": "restored",
                        "backup_retained": bool(
                            result.get("file_handling", {}).get("backup_retained")
                        ),
                        "preview_repaired": bool(item.get("preview_repaired")),
                    })
                except self._repair_error_type as exc:
                    outcome = (
                        "skipped" if exc.code in _SKIPPED_RESTORE_CODES else "failed"
                    )
                    if outcome == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                    outcomes.append({
                        "package": package,
                        "title": item["title"],
                        "artist": item["artist"],
                        "outcome": outcome,
                        "code": exc.code,
                        "message": str(exc),
                        "backup_id": item["backup_id"],
                        "change_count": 0,
                        "restored_count": 0,
                        "cache_updated": False,
                        "file_state": getattr(exc, "file_state", "unchanged"),
                    })
                except Exception as exc:
                    failed += 1
                    self._log.exception(
                        "Library Doctor batch Undo failed safely for %s: %s",
                        package,
                        exc,
                    )
                    outcomes.append({
                        "package": package,
                        "title": item["title"],
                        "artist": item["artist"],
                        "outcome": "failed",
                        "code": "unexpected_restore_failure",
                        "message": (
                            "Undo could not be confirmed. Scan this Feedpak before trying again."
                        ),
                        "backup_id": item["backup_id"],
                        "change_count": 0,
                        "restored_count": 0,
                        "cache_updated": False,
                        "file_state": "verify_required",
                    })
                self._progress(done=len(outcomes), total=total, started=started)

            cancelled = self._cancel.is_set() and len(outcomes) < total
            undo_result = {
                "schema": BATCH_UNDO_RESULT_SCHEMA,
                "id": uuid.uuid4().hex,
                "undo_plan_id": undo_plan_id,
                "outcome": "cancelled" if cancelled else "complete",
                "started_at": self._state.get("started_at"),
                "completed_at": time.time(),
                "planned_count": total,
                "completed_count": len(outcomes),
                "remaining_count": max(0, total - len(outcomes)),
                "restored_count": restored,
                "skipped_count": skipped,
                "failed_count": failed,
                "restored_change_count": restored_changes,
                "restored_entry_count": restored_entries,
                "cache_refresh_failed_count": cache_refresh_failed,
                "outcomes": outcomes,
            }
            latest = None
            with self._lock:
                source = self._state.get("result") or self._state.get("last_result")
                if isinstance(source, dict):
                    self._refresh_result_counts(source)
                    source["latest_undo_result"] = copy.deepcopy(undo_result)
                    latest = copy.deepcopy(source)
                self._state.update({
                    "phase": "undo_cancelled" if cancelled else "undo_completed",
                    "running": False,
                    "message": (
                        "Batch Undo stopped between Feedpaks. Completed restores were kept."
                        if cancelled else
                        "Batch Undo finished. Review the package outcomes below."
                    ),
                    "done": len(outcomes),
                    "current": "",
                    "completed_at": undo_result["completed_at"],
                    "eta_seconds": 0.0,
                    "undo_result": undo_result,
                })
            if latest is not None:
                self._write_last_result(latest)
        except Exception as exc:
            self._log.exception("Library Doctor batch Undo execution failed: %s", exc)
            with self._lock:
                self._state.update({
                    "phase": "error",
                    "running": False,
                    "message": (
                        "Batch Undo stopped unexpectedly. Completed restores were kept."
                    ),
                    "current": "",
                    "completed_at": time.time(),
                })
        finally:
            self._scanner.finish_repair()

    def _run_finalize_preview(
        self,
        source: dict,
        candidates: list[dict],
    ) -> None:
        """Verify all remaining recovery copies without removing them."""
        total = len(candidates)
        started = time.monotonic()
        eligible = []
        plans = []
        blocked = []
        try:
            for candidate in candidates:
                if self._cancel.is_set() or not self._wait_for_playback(
                    "finalize_previewing"
                ):
                    break
                package = candidate["package"]
                backup_id = candidate["backup_id"]
                with self._lock:
                    self._state["current"] = package
                try:
                    checked = self._repair_service.preview_finalize_backup(
                        package,
                        backup_id,
                    )
                    plan = {
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "backup_id": backup_id,
                        "recovery_kind": candidate.get(
                            "recovery_kind", "song_data"
                        ),
                        "song_data_undo_remains": bool(
                            candidate.get("recovery_kind") == "preview"
                            and candidate.get("undo_available")
                            and isinstance(candidate.get("backup_id"), str)
                        ),
                        "package_state": checked.get("package_state", "repaired"),
                        "member_count": int(checked.get("member_count") or 0),
                        "recovery_bytes": int(checked.get("recovery_bytes") or 0),
                        "change_count": int(
                            candidate.get("change_count", candidate.get("removed_count"))
                            or 0
                        ),
                        "removed_count": int(candidate.get("removed_count") or 0),
                        "rule_codes": copy.deepcopy(
                            checked.get("rule_codes")
                            or candidate.get("rule_codes")
                            or []
                        ),
                        "item_plan_id": checked.get("plan_id"),
                    }
                    plans.append(plan)
                    eligible.append(copy.deepcopy(plan))
                except self._repair_error_type as exc:
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "backup_id": backup_id,
                        "recovery_kind": candidate.get(
                            "recovery_kind", "song_data"
                        ),
                        "code": exc.code,
                        "message": str(exc),
                        "file_state": getattr(exc, "file_state", "unchanged"),
                    })
                except Exception as exc:
                    self._log.exception(
                        "Library Doctor could not verify recovery cleanup for %s: %s",
                        package,
                        exc,
                    )
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "backup_id": backup_id,
                        "code": "unexpected_finalize_preview_failure",
                        "message": (
                            "This recovery copy could not be verified and was kept. The Feedpak was not changed."
                        ),
                        "file_state": "unchanged",
                    })
                self._progress(
                    done=len(eligible) + len(blocked),
                    total=total,
                    started=started,
                )

            cancelled = self._cancel.is_set() and (
                len(eligible) + len(blocked) < total
            )
            if cancelled:
                with self._lock:
                    self._finalize_plans = []
                    self._state.update({
                        "phase": "finalize_cancelled",
                        "running": False,
                        "message": (
                            "Finalization review stopped. No recovery copies were removed."
                        ),
                        "done": len(eligible) + len(blocked),
                        "current": "",
                        "completed_at": time.time(),
                        "eta_seconds": 0.0,
                        "finalize_preview": None,
                    })
                return

            unsigned = {
                "schema": BATCH_FINALIZE_PREVIEW_SCHEMA,
                "batch_result_id": source.get("id"),
                "packages": [
                    {
                        "package": item["package"],
                        "backup_id": item["backup_id"],
                        "recovery_kind": item.get("recovery_kind", "song_data"),
                        "item_plan_id": item.get("item_plan_id"),
                    }
                    for item in plans
                ],
                "blocked": [
                    {
                        "package": item["package"],
                        "backup_id": item["backup_id"],
                        "code": item["code"],
                    }
                    for item in blocked
                ],
            }
            preview = {
                **unsigned,
                "finalize_plan_id": _digest(unsigned),
                "candidate_count": total,
                "eligible_count": len(eligible),
                "blocked_count": len(blocked),
                "already_finalized_count": int(source.get("finalized_count") or 0),
                "recovery_bytes_to_free": sum(
                    item["recovery_bytes"] for item in eligible
                ),
                "changed_member_count": sum(
                    item["member_count"] for item in eligible
                ),
                "packages": eligible,
                "blocked": blocked,
                "file_handling": (
                    "Finalization keeps every current Feedpak exactly as it is and removes only its verified private Library Doctor recovery copy. "
                    "Each copy is verified again immediately before removal. Finalized repairs cannot be undone through Library Doctor."
                ),
            }
            with self._lock:
                self._finalize_plans = plans
                self._state.update({
                    "phase": "finalize_ready",
                    "running": False,
                    "message": (
                        "Finalization review ready. No recovery copies were removed."
                        if eligible else
                        "No remaining recovery copies can currently be finalized automatically."
                    ),
                    "done": total,
                    "current": "",
                    "completed_at": time.time(),
                    "eta_seconds": 0.0,
                    "finalize_preview": preview,
                })
        except Exception as exc:
            self._log.exception(
                "Library Doctor batch finalization review failed: %s", exc
            )
            with self._lock:
                self._finalize_plans = []
                self._state.update({
                    "phase": "error",
                    "running": False,
                    "message": (
                        "The finalization review could not finish. No recovery copies were removed."
                    ),
                    "current": "",
                    "completed_at": time.time(),
                })
        finally:
            self._scanner.finish_repair()

    def _run_finalize_apply(
        self,
        finalize_plan_id: str,
        plans: list[dict],
    ) -> None:
        """Remove verified backups one at a time and preserve every Feedpak."""
        total = len(plans)
        started = time.monotonic()
        outcomes = []
        finalized = 0
        skipped = 0
        failed = 0
        recovery_bytes_freed = 0
        successful_cleanups: list[tuple[str, str, str, float]] = []
        try:
            for item in plans:
                if self._cancel.is_set() or not self._wait_for_playback("finalizing"):
                    break
                package = item["package"]
                backup_id = item["backup_id"]
                with self._lock:
                    self._state["current"] = package
                try:
                    result = self._repair_service.finalize_backup(package, backup_id)
                    package_state = result.get(
                        "package_state", item.get("package_state", "repaired")
                    )
                    freed = int(
                        result.get("file_handling", {}).get(
                            "recovery_bytes_freed", item.get("recovery_bytes")
                        )
                        or 0
                    )
                    completed_at = float(result.get("completed_at") or time.time())
                    finalized += 1
                    recovery_bytes_freed += freed
                    successful_cleanups.append((
                        package,
                        backup_id,
                        package_state,
                        completed_at,
                    ))
                    outcomes.append({
                        "package": package,
                        "title": result.get("title") or item["title"],
                        "artist": result.get("artist") or item["artist"],
                        "outcome": "finalized",
                        "message": (
                            "The repaired preview was kept and its temporary recovery copy was removed. The separate song-data Undo copy remains available."
                            if item.get("recovery_kind") == "preview"
                            and item.get("song_data_undo_remains") else
                            "The repaired preview was kept and its temporary recovery copy was removed."
                            if item.get("recovery_kind") == "preview" else
                            "The repaired Feedpak was kept and its verified recovery copy was removed. Undo is no longer available."
                            if package_state == "repaired" else
                            "The original song data was already present. Its redundant recovery copy was removed."
                        ),
                        "backup_id": backup_id,
                        "recovery_kind": item.get(
                            "recovery_kind", "song_data"
                        ),
                        "package_state": package_state,
                        "member_count": int(item.get("member_count") or 0),
                        "recovery_bytes_freed": freed,
                        "file_state": (
                            "repaired" if package_state == "repaired" else "restored"
                        ),
                    })
                except self._repair_error_type as exc:
                    outcome = (
                        "skipped" if exc.code in _SKIPPED_FINALIZE_CODES else "failed"
                    )
                    if outcome == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                    outcomes.append({
                        "package": package,
                        "title": item["title"],
                        "artist": item["artist"],
                        "outcome": outcome,
                        "code": exc.code,
                        "message": str(exc),
                        "backup_id": backup_id,
                        "recovery_bytes_freed": 0,
                        "file_state": getattr(exc, "file_state", "unchanged"),
                    })
                except Exception as exc:
                    failed += 1
                    self._log.exception(
                        "Library Doctor batch finalization failed safely for %s: %s",
                        package,
                        exc,
                    )
                    outcomes.append({
                        "package": package,
                        "title": item["title"],
                        "artist": item["artist"],
                        "outcome": "failed",
                        "code": "unexpected_finalize_failure",
                        "message": (
                            "The recovery copy could not be confirmed as removed. The Feedpak was not changed."
                        ),
                        "backup_id": backup_id,
                        "recovery_bytes_freed": 0,
                        "file_state": "unchanged",
                    })
                self._progress(done=len(outcomes), total=total, started=started)

            cancelled = self._cancel.is_set() and len(outcomes) < total
            finalize_result = {
                "schema": BATCH_FINALIZE_RESULT_SCHEMA,
                "id": uuid.uuid4().hex,
                "finalize_plan_id": finalize_plan_id,
                "outcome": "cancelled" if cancelled else "complete",
                "started_at": self._state.get("started_at"),
                "completed_at": time.time(),
                "planned_count": total,
                "completed_count": len(outcomes),
                "remaining_count": max(0, total - len(outcomes)),
                "finalized_count": finalized,
                "skipped_count": skipped,
                "failed_count": failed,
                "recovery_bytes_freed": recovery_bytes_freed,
                "outcomes": outcomes,
            }
            latest = None
            with self._lock:
                for key in ("result", "last_result"):
                    payload = self._state.get(key)
                    package_outcomes = (
                        payload.get("outcomes") if isinstance(payload, dict) else None
                    )
                    if not isinstance(package_outcomes, list):
                        continue
                    payload_updated = False
                    for package, backup_id, package_state, completed_at in successful_cleanups:
                        for outcome in package_outcomes:
                            if (
                                isinstance(outcome, dict)
                                and outcome.get("package") == package
                                and self._mark_recovery_finalized(
                                    outcome,
                                    backup_id,
                                    package_state=package_state,
                                    finalized_at=completed_at,
                                )
                            ):
                                payload_updated = True
                                break
                    payload["latest_finalize_result"] = copy.deepcopy(finalize_result)
                    if payload_updated:
                        self._refresh_result_counts(payload)
                source = self._state.get("result") or self._state.get("last_result")
                if isinstance(source, dict):
                    latest = copy.deepcopy(source)
                self._state.update({
                    "phase": (
                        "finalize_cancelled" if cancelled else "finalize_completed"
                    ),
                    "running": False,
                    "message": (
                        "Batch finalization stopped between Feedpaks. Completed finalizations were kept."
                        if cancelled else
                        "Batch finalization finished. Review the recovery-copy outcomes below."
                    ),
                    "done": len(outcomes),
                    "current": "",
                    "completed_at": finalize_result["completed_at"],
                    "eta_seconds": 0.0,
                    "finalize_result": finalize_result,
                })
            if latest is not None:
                self._write_last_result(latest)
        except Exception as exc:
            self._log.exception(
                "Library Doctor batch finalization execution failed: %s", exc
            )
            with self._lock:
                self._state.update({
                    "phase": "error",
                    "running": False,
                    "message": (
                        "Batch finalization stopped unexpectedly. Completed recovery-copy removals were kept."
                    ),
                    "current": "",
                    "completed_at": time.time(),
                })
        finally:
            self._scanner.finish_repair()

    @property
    def _last_result_path(self) -> Path:
        return self._config_dir / "library_doctor" / "batch_result.json"

    def _read_last_result(self) -> dict | None:
        try:
            raw = self._last_result_path.read_bytes()
            if len(raw) > 16 * 1024 * 1024:
                return None
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict) or result.get("schema") not in {
                BATCH_RESULT_SCHEMA,
                *self._legacy_batch_result_schemas,
            }:
                return None
            # Keep the migrated receipt usable in memory. The next batch write
            # persists it with the current schema.
            result["schema"] = BATCH_RESULT_SCHEMA
            return result
        except (OSError, AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _write_last_result(self, result: dict) -> None:
        path = self._last_result_path
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(
                prefix=".batch-result-", suffix=".tmp", dir=path.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(handle, "wb") as stream:
                stream.write(json.dumps(
                    result,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                ).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self._log.warning("Library Doctor could not save the batch result: %s", exc)

    @property
    def _checkpoint_path(self) -> Path:
        return self._config_dir / "library_doctor" / "batch_checkpoint.json"

    @staticmethod
    def _checkpoint_result(checkpoint: dict) -> dict | None:
        result = checkpoint.get("result")
        if not isinstance(result, dict) or result.get("schema") != BATCH_RESULT_SCHEMA:
            return None
        recovered = copy.deepcopy(result)
        recovered["outcome"] = "interrupted"
        recovered["completed_at"] = float(
            checkpoint.get("checkpointed_at") or recovered.get("completed_at") or 0
        )
        recovered["recovered_from_checkpoint"] = True
        return recovered

    def _read_checkpoint(self) -> dict | None:
        try:
            raw = self._checkpoint_path.read_bytes()
            if len(raw) > 16 * 1024 * 1024:
                return None
            checkpoint = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(checkpoint, dict)
                or checkpoint.get("schema") != BATCH_CHECKPOINT_SCHEMA
                or self._checkpoint_result(checkpoint) is None
            ):
                return None
            return checkpoint
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _write_checkpoint(self, result: dict) -> None:
        path = self._checkpoint_path
        temporary = None
        payload = {
            "schema": BATCH_CHECKPOINT_SCHEMA,
            "checkpointed_at": time.time(),
            "result": result,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(
                prefix=".batch-checkpoint-", suffix=".tmp", dir=path.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(handle, "wb") as stream:
                stream.write(json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self._log.warning(
                "Library Doctor could not save a batch checkpoint: %s", exc
            )

    def _delete_checkpoint(self) -> None:
        try:
            self._checkpoint_path.unlink(missing_ok=True)
        except OSError as exc:
            self._log.warning(
                "Library Doctor could not remove a completed batch checkpoint: %s",
                exc,
            )
