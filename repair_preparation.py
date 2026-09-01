"""Validated repair candidates and the single-writer commit boundary."""

from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class PreparedRepair:
    """One fully validated candidate whose package transaction has not begun."""

    owner: object
    package_path: Path
    package_name: str
    internal: dict
    deep_audio: bool
    retain_recovery: bool
    source_guard: object
    transaction_started: float
    request_id: str | None
    request_operation: str | None
    request_fingerprint: str | None
    originals: dict[str, bytes | None]
    replacements: dict[str, bytes | None]
    source_token: dict
    candidate: Path
    cleanup: Callable[[], None]
    after: dict
    rule_codes: list[str]
    reuse_verified_before: bool
    reuse_deep_audio: bool
    _discard_lock: threading.Lock = field(default_factory=threading.Lock)
    _discarded: bool = False

    def discard(self) -> bool:
        """Release the registered candidate workspace exactly once."""
        with self._discard_lock:
            if self._discarded:
                return False
            self._discarded = True
        self.cleanup()
        return True


def _assert_source_guard(guard, error_type) -> None:
    if not callable(guard):
        return
    try:
        unchanged = bool(guard())
    except Exception:
        unchanged = False
    if not unchanged:
        raise error_type(
            "source_changed",
            "This Feedpak changed after its completed scan. Scan it again before repairing it.",
        )


def prepare(
    service,
    package_path: Path,
    package_name: str,
    internal: dict,
    *,
    apply_json_member,
    error_type,
    validate_feedpak=None,
    deep_audio: bool,
    retain_recovery: bool = True,
    verified_before_report: dict | None = None,
    source_guard=None,
    transaction_started: float | None = None,
    request_id: str | None = None,
    request_operation: str | None = None,
    request_fingerprint: str | None = None,
) -> PreparedRepair:
    """Build and fully validate a candidate without recovery or package writes."""
    validator = validate_feedpak or service._validate_feedpak
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
    source_token = service._capture_package_token(package_path, replacements)
    terminal_evidence = internal.get("terminal_evidence")
    if terminal_evidence:
        source_token["terminal_evidence"] = {
            path: digest
            for path, digest in terminal_evidence.items()
            if path not in originals
        }
        source_token["terminal_inventory"] = internal["terminal_inventory"]
    service._emit_transaction_barrier(
        "source_captured", package=package_name, operation="repair"
    )
    if terminal_evidence:
        service._assert_package_identity(package_name, package_path, source_token)
    song_data_only = all(
        item.get("source_kind") in {
            "arrangement", "timeline", "lyrics", "drum_tab"
        }
        for item in internal["_members"]
    )
    reuse_verified_before = bool(
        package_path.is_file()
        and service._can_reuse_verified_before_report(
            verified_before_report,
            source_guard,
        )
    )
    reuse_deep_audio = bool(
        deep_audio
        and song_data_only
        and package_path.is_file()
        and reuse_verified_before
    )
    source_guarded = callable(source_guard)
    if source_guarded:
        _assert_source_guard(source_guard, error_type)
    if reuse_verified_before:
        before = copy.deepcopy(verified_before_report)
    else:
        before = validator(
            package_path, package_name, deep_audio=bool(deep_audio)
        )
    candidate, cleanup = service._candidate(package_path, replacements)
    try:
        after = validator(
            candidate,
            package_name,
            deep_audio=bool(deep_audio and not reuse_deep_audio),
        )
        if reuse_deep_audio:
            after = service._reuse_unchanged_deep_audio(before, after)
        rule_codes = internal.get("rule_codes")
        if not isinstance(rule_codes, list) or not rule_codes:
            rule_codes = [internal["rule_code"]]
        verification = internal.get("_verification")
        if isinstance(verification, dict) and verification.get("mode") == "reviewed":
            service._verify_reviewed_validation(
                before,
                after,
                set(internal.get("rule_codes") or ()),
            )
        else:
            service._verify_validation(before, after, rule_codes)
        service._emit_transaction_barrier(
            "candidate_validated", package=package_name, operation="repair"
        )
        if source_guarded:
            _assert_source_guard(source_guard, error_type)
    except BaseException as exc:
        if not getattr(exc, "preserve_candidate_workspace", False):
            cleanup()
        raise

    return PreparedRepair(
        owner=service._prepared_owner,
        package_path=package_path,
        package_name=package_name,
        internal=internal,
        deep_audio=bool(deep_audio),
        retain_recovery=bool(retain_recovery),
        source_guard=source_guard,
        transaction_started=float(transaction_started),
        request_id=request_id,
        request_operation=request_operation,
        request_fingerprint=request_fingerprint,
        originals=originals,
        replacements=replacements,
        source_token=source_token,
        candidate=candidate,
        cleanup=cleanup,
        after=after,
        rule_codes=list(rule_codes),
        reuse_verified_before=reuse_verified_before,
        reuse_deep_audio=reuse_deep_audio,
    )


def commit(service, prepared: PreparedRepair, *, error_type) -> dict:
    """Recheck and commit one candidate through the caller's mutation lock."""
    with prepared._discard_lock:
        if prepared._discarded:
            raise error_type(
                "invalid_prepared_repair",
                "The prepared repair is no longer available.",
            )

    package_path = prepared.package_path
    package_name = prepared.package_name
    internal = prepared.internal
    originals = prepared.originals
    replacements = prepared.replacements
    source_token = prepared.source_token
    _assert_source_guard(prepared.source_guard, error_type)
    service._assert_source_state(
        package_name,
        package_path,
        originals,
        source_token,
    )
    backup_id = service._create_backup(
        package_name,
        package_path,
        originals,
        replacements,
        internal["plan_id"],
        internal["rule_code"],
        service._public_plan(internal),
    )
    transaction = None
    try:
        if package_path.is_dir():
            transaction = service._begin_transaction(
                package_name,
                backup_id,
                operation="repair",
                target_state="repaired",
            )
        service._emit_transaction_barrier(
            "backup_durable",
            package=package_name,
            operation="repair",
            backup_id=backup_id,
        )
        try:
            service._verify_backup_durable(
                backup_id,
                package_name,
                originals,
            )
        except error_type as verify_exc:
            raise error_type(
                "backup_failed",
                "The recovery backup could not be verified, so nothing was changed.",
            ) from verify_exc
        service._assert_source_state(
            package_name,
            package_path,
            originals,
            source_token,
        )
        _assert_source_guard(prepared.source_guard, error_type)
        service._emit_transaction_barrier(
            "source_guarded",
            package=package_name,
            operation="repair",
            backup_id=backup_id,
        )
        service._commit(
            package_name,
            package_path,
            prepared.candidate,
            replacements,
            originals,
            source_token=source_token,
            transaction=transaction,
        )
    except error_type as exc:
        if exc.file_state == "unchanged":
            if transaction is not None:
                service._finish_transaction(transaction)
            try:
                service._delete_backup(backup_id)
            except error_type as cleanup_exc:
                service._log.warning(
                    "Library Doctor could not remove an unused recovery backup %s: %s",
                    backup_id,
                    cleanup_exc,
                )
        raise

    backup_removed = False
    recovery_bytes_freed = 0
    backup_cleanup_error = ""
    if not prepared.retain_recovery:
        try:
            recovery_bytes_freed = service._delete_backup(backup_id)
            backup_removed = True
        except error_type as exc:
            backup_cleanup_error = str(exc)
            service._log.warning(
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
            "undo_available": bool(prepared.retain_recovery or not backup_removed),
            "backup_retained": not backup_removed,
            "backup_removed": backup_removed,
            "backup_cleanup_required": bool(backup_cleanup_error),
            "backup_cleanup_error": backup_cleanup_error,
            "backup_size_bytes": service._backup_size(backup_id) or 0,
            "recovery_bytes_freed": recovery_bytes_freed,
        })
        if not prepared.retain_recovery:
            file_handling["summary"] = (
                "Library Doctor checked the complete repaired song before replacing the Feedpak at the same location. "
                "Its temporary recovery copy was then removed automatically, so no duplicate song or pending preview backup remains."
                if backup_removed else
                "The validated preview repair completed at the same Feedpak path, but Library Doctor could not remove its temporary recovery copy automatically. "
                "The repaired Feedpak remains active and the recovery copy is available for explicit cleanup."
            )
    else:
        file_handling = service._file_handling(backup_id)
        file_handling["backup_size_bytes"] = service._backup_size(backup_id) or 0
    performance = {
        "elapsed_seconds": round(
            max(0.0, time.monotonic() - prepared.transaction_started), 6
        ),
        "deep_audio_requested": bool(prepared.deep_audio),
        "verified_scan_report_reused": prepared.reuse_verified_before,
        "deep_audio_reused": prepared.reuse_deep_audio,
    }
    result = {
        **service._public_plan(internal),
        "applied": True,
        "outcome": "success",
        "backup_id": backup_id,
        "undo_available": bool(prepared.retain_recovery or not backup_removed),
        "report": prepared.after,
        "deep_audio": bool(prepared.deep_audio),
        "deep_audio_reused": prepared.reuse_deep_audio,
        "verified_scan_report_reused": prepared.reuse_verified_before,
        "performance": performance,
        "file_handling": file_handling,
        **service._request_metadata(
            prepared.request_id,
            prepared.request_operation,
            prepared.request_fingerprint,
        ),
    }
    result["receipt_saved"] = service._record_history({
        "id": uuid.uuid4().hex,
        "action": "repair",
        "outcome": "success",
        "completed_at": time.time(),
        "package": package_name,
        "title": prepared.after.get("title") or package_name,
        "artist": prepared.after.get("artist") or "",
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
        **service._request_metadata(
            prepared.request_id,
            prepared.request_operation,
            prepared.request_fingerprint,
        ),
    })
    return result
