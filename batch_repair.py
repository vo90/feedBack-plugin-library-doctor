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
MAX_BATCH_PACKAGES = 10_000
_SKIPPED_REPAIR_CODES = {
    "source_changed",
    "nothing_to_repair",
    "package_changed",
    "package_unavailable",
    "member_unavailable",
}
_SKIPPED_RESTORE_CODES = {
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
        self._state = self._initial_state()
        latest = self._read_last_result()
        if latest is not None:
            normalized = self._refresh_result_counts(latest)
            if normalized:
                self._write_last_result(latest)
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
            "total": 0,
            "done": 0,
            "current": "",
            "started_at": None,
            "completed_at": None,
            "packages_per_second": 0.0,
            "eta_seconds": None,
            "preview": None,
            "undo_preview": None,
            "undo_result": None,
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
                "preview", "undo_preview", "undo_result", "result", "last_result"
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
            if isinstance(item, dict) and item.get("outcome") == "success"
        ]
        restored = [
            item for item in outcomes
            if isinstance(item, dict) and item.get("outcome") == "restored"
        ]
        result["currently_repaired_count"] = len(currently_repaired)
        result["restored_count"] = len(restored)
        result["current_removed_count"] = sum(
            int(item.get("removed_count") or 0) for item in currently_repaired
        )
        result["current_change_count"] = sum(
            int(item.get("change_count", item.get("removed_count")) or 0)
            for item in currently_repaired
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
                "message": "Reviewing safe repairs without changing any Feedpaks.",
                "target": copy.deepcopy(snapshot.get("target")),
                "deep_audio": bool(snapshot.get("deep_audio")),
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
                "result": None,
            })
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
                    and item.get("outcome") == "success"
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

    def cancel(self) -> bool:
        with self._lock:
            if not self._state["running"]:
                return False
            self._cancel.set()
            self._state["phase"] = "cancelling"
            mode = self._state.get("mode")
            if mode in {"apply", "undo-apply"}:
                self._state["message"] = (
                    "Finishing the current Feedpak before stopping."
                )
            else:
                self._state["message"] = "Stopping the read-only preview."
            return True

    def invalidate_ready(self, reason: str) -> bool:
        """Expire a completed preview when scan scope or package data changes."""
        with self._lock:
            if self._state["running"] or self._state["phase"] not in {
                "ready", "undo_ready"
            }:
                return False
            self._plans = []
            self._snapshot = None
            self._undo_plans = []
            self._state.update({
                "phase": "stale",
                "message": str(reason),
                "preview": None,
                "undo_preview": None,
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
                        and outcome.get("outcome") == "success"
                    ):
                        outcome["outcome"] = "restored"
                        outcome["message"] = (
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
                    "Batch repair paused while a song is open. It will resume automatically."
                )
        ready = self._scanner.wait_for_playback(self._cancel)
        if ready:
            with self._lock:
                self._state["phase"] = resume_phase
                messages = {
                    "previewing": "Reviewing safe repairs without changing any Feedpaks.",
                    "applying": "Applying one validated Feedpak transaction at a time.",
                    "undo_previewing": "Checking retained backups without changing any Feedpaks.",
                    "undoing": "Restoring one validated Feedpak at a time.",
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
                try:
                    plan = self._repair_service.preview_all(package)
                except self._repair_error_type as exc:
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "code": exc.code,
                        "message": str(exc),
                    })
                except Exception as exc:
                    self._log.exception(
                        "Library Doctor batch preview failed safely for %s: %s",
                        package,
                        exc,
                    )
                    blocked.append({
                        "package": package,
                        "title": candidate.get("title") or package,
                        "artist": candidate.get("artist") or "",
                        "code": "unexpected_preview_failure",
                        "message": "This Feedpak could not be reviewed safely and was excluded.",
                    })
                else:
                    if plan.get("available") and int(plan.get("rule_count") or 0) > 0:
                        public_item = {
                            "package": package,
                            "title": candidate.get("title") or package,
                            "artist": candidate.get("artist") or "",
                            "rule_codes": list(plan.get("rule_codes") or []),
                            "rule_count": int(plan.get("rule_count") or 0),
                            "change_count": int(
                                plan.get("change_count", plan.get("removed_count")) or 0
                            ),
                            "removed_count": int(plan.get("removed_count") or 0),
                            "member_count": int(plan.get("member_count") or 0),
                            "repair_summaries": copy.deepcopy(
                                plan.get("repair_summaries") or []
                            ),
                        }
                        eligible.append(public_item)
                        plans.append({
                            **public_item,
                            "plan_id": plan["plan_id"],
                        })
                        for summary in public_item["repair_summaries"]:
                            code = summary.get("rule_code")
                            if not isinstance(code, str):
                                continue
                            total_for_rule = rule_totals.setdefault(code, {
                                "rule_code": code,
                                "title": summary.get("title") or code,
                                "item_name": summary.get("item_name") or "item",
                                "change_kind": summary.get(
                                    "change_kind", "remove_duplicates"
                                ),
                                "package_count": 0,
                                "change_count": 0,
                                "removed_count": 0,
                            })
                            total_for_rule["package_count"] += 1
                            total_for_rule["change_count"] += int(
                                summary.get(
                                    "change_count", summary.get("removed_count")
                                ) or 0
                            )
                            total_for_rule["removed_count"] += int(
                                summary.get("removed_count") or 0
                            )
                    elif plan.get("blockers"):
                        first = plan["blockers"][0]
                        blocked.append({
                            "package": package,
                            "title": candidate.get("title") or package,
                            "artist": candidate.get("artist") or "",
                            "code": first.get("code") or "blocked",
                            "blocker_count": len(plan["blockers"]),
                            "message": first.get("message") or (
                                "A referenced song-data file cannot be changed safely."
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
                "packages": [
                    {"package": item["package"], "plan_id": item["plan_id"]}
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
                "change_count": sum(item["change_count"] for item in eligible),
                "removed_count": sum(item["removed_count"] for item in eligible),
                "changed_member_count": sum(item["member_count"] for item in eligible),
                "rule_summaries": list(rule_totals.values()),
                "packages": eligible,
                "blocked": blocked,
                "file_handling": (
                    "Each eligible Feedpak is validated and backed up separately before it is replaced. "
                    "No duplicate song packages are created."
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

    def _run_apply(self, batch_plan_id: str, plans: list[dict], snapshot: dict) -> None:
        total = len(plans)
        started = time.monotonic()
        outcomes = []
        succeeded = 0
        skipped = 0
        failed = 0
        change_count = 0
        removed_count = 0
        cache_refresh_failed = 0
        try:
            for item in plans:
                if self._cancel.is_set() or not self._wait_for_playback("applying"):
                    break
                package = item["package"]
                with self._lock:
                    self._state["current"] = package
                try:
                    result = self._repair_service.apply_all(
                        package,
                        item["plan_id"],
                        deep_audio=bool(snapshot.get("deep_audio")),
                    )
                    try:
                        self._scanner.record_repair_result(
                            package,
                            result["report"],
                            deep_audio=bool(snapshot.get("deep_audio")),
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
                    change_count += int(
                        result.get("change_count", result.get("removed_count")) or 0
                    )
                    removed_count += int(result.get("removed_count") or 0)
                    outcomes.append({
                        "package": package,
                        "title": result.get("report", {}).get("title") or item["title"],
                        "artist": result.get("report", {}).get("artist") or item["artist"],
                        "outcome": "success",
                        "message": "Repair completed and the validated Feedpak replaced the package at the same path.",
                        "backup_id": result.get("backup_id"),
                        "change_kind": result.get("change_kind", "combined"),
                        "change_count": int(
                            result.get("change_count", result.get("removed_count")) or 0
                        ),
                        "removed_count": int(result.get("removed_count") or 0),
                        "rule_codes": list(result.get("rule_codes") or []),
                        "repair_summaries": copy.deepcopy(
                            result.get("repair_summaries") or []
                        ),
                        "cache_updated": cache_updated,
                        "file_state": "repaired",
                    })
                except self._repair_error_type as exc:
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
                self._progress(done=len(outcomes), total=total, started=started)

            cancelled = self._cancel.is_set() and len(outcomes) < total
            result = {
                "schema": BATCH_RESULT_SCHEMA,
                "id": uuid.uuid4().hex,
                "batch_plan_id": batch_plan_id,
                "outcome": "cancelled" if cancelled else "complete",
                "target": copy.deepcopy(snapshot.get("target")),
                "deep_audio": bool(snapshot.get("deep_audio")),
                "started_at": self._state.get("started_at"),
                "completed_at": time.time(),
                "planned_count": total,
                "completed_count": len(outcomes),
                "remaining_count": max(0, total - len(outcomes)),
                "successful_count": succeeded,
                "skipped_count": skipped,
                "failed_count": failed,
                "change_count": change_count,
                "removed_count": removed_count,
                "backup_count": succeeded,
                "cache_refresh_failed_count": cache_refresh_failed,
                "outcomes": outcomes,
                "recovery_summary": (
                    "Every successful Feedpak has its own retained recovery backup and can be undone individually."
                ),
            }
            self._refresh_result_counts(result)
            self._write_last_result(result)
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
                        "message": "Original song data restored and validated.",
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
