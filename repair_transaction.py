"""Durable directory-repair transaction journal and crash reconciliation."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


class TransactionJournal:
    """Persist and reconcile interrupted directory-package mutations."""

    def __init__(
        self,
        *,
        atomic_write,
        backup_id_matches,
        candidate,
        capture_package_token,
        commit,
        config_dir: Path,
        delete_backup,
        error_type,
        file_handling,
        finish_transaction,
        log,
        max_manifest_bytes: int,
        max_member_bytes: int,
        max_pending: int,
        member_exists,
        public_recovery,
        read_backup,
        read_history,
        read_member,
        resolve_package,
        schema: str,
        sync_directory,
        update_transaction,
        validate_feedpak,
        write_history,
    ) -> None:
        self.directory = Path(config_dir) / "library_doctor" / "repair_transactions"
        self._atomic_write = atomic_write
        self._backup_id_matches = backup_id_matches
        self._candidate = candidate
        self._capture_package_token = capture_package_token
        self._commit = commit
        self._delete_backup = delete_backup
        self._error_type = error_type
        self._file_handling = file_handling
        self._finish_transaction = finish_transaction
        self._log = log
        self._max_manifest_bytes = max_manifest_bytes
        self._max_member_bytes = max_member_bytes
        self._max_pending = max_pending
        self._member_exists = member_exists
        self._public_recovery = public_recovery
        self._read_backup = read_backup
        self._read_history = read_history
        self._read_member = read_member
        self._resolve_package = resolve_package
        self._schema = schema
        self._sync_directory = sync_directory
        self._update_transaction = update_transaction
        self._validate_feedpak = validate_feedpak
        self._write_history = write_history

    def write(self, transaction: dict) -> None:
        backup_id = transaction.get("transaction_id")
        if not isinstance(backup_id, str) or not self._backup_id_matches(backup_id):
            raise OSError("invalid repair transaction id")
        path = self.directory / f"{backup_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            path,
            json.dumps(transaction, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        self._sync_directory(path.parent)

    def finish(self, transaction: dict) -> None:
        backup_id = transaction.get("transaction_id")
        if not isinstance(backup_id, str) or not self._backup_id_matches(backup_id):
            return
        path = self.directory / f"{backup_id}.json"
        try:
            path.unlink(missing_ok=True)
            self._sync_directory(path.parent)
        except OSError as exc:
            self._log.warning(
                "Library Doctor could not clear completed repair transaction %s: %s",
                backup_id,
                exc,
            )

    def read(self) -> list[dict]:
        try:
            paths = sorted(self.directory.glob("*.json"))[-self._max_pending :]
        except OSError:
            return []
        transactions = []
        for path in paths:
            if not self._backup_id_matches(path.stem):
                continue
            try:
                raw = path.read_bytes()
                if len(raw) > self._max_manifest_bytes:
                    continue
                item = json.loads(raw.decode("utf-8"))
                if (
                    not isinstance(item, dict)
                    or item.get("schema") != self._schema
                    or item.get("transaction_id") != path.stem
                    or item.get("backup_id") != path.stem
                    or item.get("operation") not in {"repair", "restore"}
                    or item.get("target_state") not in {"repaired", "original"}
                    or not isinstance(item.get("package"), str)
                ):
                    continue
                transactions.append(item)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        return transactions

    @staticmethod
    def _matches_backup_state(
        present: bool,
        current_hash: str | None,
        entry: dict,
        state: str,
    ) -> bool:
        prefix = "repaired" if state == "repaired" else "original"
        return (
            present == entry[f"{prefix}_present"]
            and current_hash == entry[f"{prefix}_sha256"]
        )

    def reconcile_all(self) -> None:
        """Resolve interrupted directory writes before accepting new repairs."""
        for transaction in self.read():
            try:
                self.reconcile(transaction)
            except Exception as exc:
                self._log.error(
                    "Library Doctor could not reconcile interrupted transaction %s: %s",
                    transaction.get("transaction_id"),
                    exc,
                )
                try:
                    self._update_transaction(transaction, phase="recovery_required")
                except OSError:
                    pass

    def reconcile(self, transaction: dict) -> None:
        package = transaction["package"]
        backup_id = transaction["backup_id"]
        for receipt in self._read_history():
            if receipt.get("backup_id") != backup_id:
                continue
            completed_repair = (
                transaction["operation"] == "repair"
                and receipt.get("action") == "repair"
                and receipt.get("outcome") == "success"
            )
            completed_restore = (
                transaction["operation"] == "restore"
                and receipt.get("action") == "restore"
                and receipt.get("outcome") == "restored"
            )
            if completed_repair or completed_restore:
                self._finish_transaction(transaction)
                return

        _root, package_path, package_name = self._resolve_package(package)
        if not package_path.is_dir():
            raise self._error_type(
                "recovery_required",
                "The interrupted package is no longer a directory.",
            )
        metadata, originals = self._read_backup(backup_id, package_name)
        member_states = []
        current = {}
        for entry in metadata["members"]:
            member_path = entry["member_path"]
            present = self._member_exists(package_path, member_path)
            raw = (
                self._read_member(package_path, member_path, self._max_member_bytes)
                if present
                else None
            )
            current_hash = hashlib.sha256(raw).hexdigest() if raw is not None else None
            states = {
                state
                for state in ("original", "repaired")
                if self._matches_backup_state(present, current_hash, entry, state)
            }
            if not states:
                self._update_transaction(transaction, phase="recovery_required")
                return
            member_states.append(states)
            current[member_path] = raw

        target_state = transaction["target_state"]
        source_state = "original" if target_state == "repaired" else "repaired"
        if all(target_state in states for states in member_states):
            if not self.record_recovered(transaction, metadata, committed=True):
                return
            if transaction["operation"] == "restore":
                self._delete_with_warning(
                    backup_id,
                    "Library Doctor completed interrupted Undo but could not remove backup %s: %s",
                )
            self._finish_transaction(transaction)
            return

        if all(source_state in states for states in member_states):
            if transaction["operation"] == "repair":
                self._delete_with_warning(
                    backup_id,
                    "Library Doctor found an unchanged interrupted repair but could not remove backup %s: %s",
                )
            self._finish_transaction(transaction)
            return

        candidate, cleanup = self._candidate(package_path, originals)
        try:
            self._validate_feedpak(candidate, package_name, deep_audio=False)
            source_token = self._capture_package_token(package_path)
            self._commit(
                package_name,
                package_path,
                candidate,
                originals,
                current,
                source_token=source_token,
                transaction=None,
                operation=transaction["operation"],
            )
        finally:
            cleanup()
        if not self.record_recovered(transaction, metadata, committed=False):
            return
        self._delete_with_warning(
            backup_id,
            "Library Doctor recovered interrupted transaction %s but could not remove backup: %s",
            transaction.get("transaction_id"),
        )
        self._finish_transaction(transaction)

    def _delete_with_warning(
        self,
        backup_id: str,
        message: str,
        transaction_id: str | None = None,
    ) -> None:
        try:
            self._delete_backup(backup_id)
        except self._error_type as exc:
            if transaction_id is None:
                self._log.warning(message, backup_id, exc)
            else:
                self._log.warning(message, transaction_id, exc)

    def record_recovered(
        self,
        transaction: dict,
        metadata: dict,
        *,
        committed: bool,
    ) -> bool:
        history = self._read_history()
        backup_id = transaction["backup_id"]
        summary = metadata.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        operation = transaction["operation"]
        repair_committed = operation == "repair" and committed
        expected_action = "repair" if repair_committed else "restore"
        expected_outcome = "success" if repair_committed else "restored"
        if any(
            item.get("backup_id") == backup_id
            and item.get("action") == expected_action
            and item.get("outcome") == expected_outcome
            for item in history
        ):
            return True
        item = {
            "id": f"recovered-{backup_id}",
            "action": expected_action,
            "outcome": expected_outcome,
            "completed_at": time.time(),
            "package": transaction["package"],
            "title": summary.get("title") or transaction["package"],
            "artist": summary.get("artist") or "",
            "rule_code": metadata.get("rule_code"),
            "rule_codes": metadata.get("rule_codes", []),
            "repair_summaries": summary.get("repair_summaries", []),
            **self._reviewed_summary(summary),
            "backup_id": backup_id,
            "change_kind": summary.get("change_kind", "repair"),
            "change_count": int(summary.get("change_count", 0) or 0),
            "removed_count": int(summary.get("removed_count", 0) or 0),
            "item_name": summary.get("item_name", "item"),
            "player_result": summary.get("player_result", ""),
            "user_value": summary.get("user_value", ""),
            "recovered_transaction": True,
            "recovery_summary": (
                "Library Doctor verified and completed a repair that had reached disk before the app stopped."
                if repair_committed
                else "Library Doctor restored the verified original files after an interrupted package transaction."
            ),
            "file_handling": self._file_handling(backup_id),
        }
        history.append(item)
        return self._write_history(history)

    @staticmethod
    def _reviewed_summary(summary: dict) -> dict:
        if summary.get("change_kind") != "reviewed_decisions":
            return {}
        return {
            key: summary.get(key)
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

    def pending_receipts(self) -> list[dict]:
        receipts = []
        for transaction in self.read():
            if transaction.get("phase") != "recovery_required":
                continue
            backup_id = transaction["backup_id"]
            recovery = self._public_recovery(transaction)
            summary = self._backup_summary(transaction)
            receipts.append({
                "id": f"recovery-required-{backup_id}",
                "action": "recovery",
                "outcome": "failure",
                "completed_at": float(
                    transaction.get("updated_at")
                    or transaction.get("created_at")
                    or time.time()
                ),
                "package": transaction["package"],
                "title": summary.get("title") or transaction["package"],
                "artist": summary.get("artist") or "",
                "backup_id": backup_id,
                "file_state": "recovery_required",
                "file_state_copy": (
                    "Library Doctor preserved the current song and its saved recovery copy, but could not confirm a complete final state. Further changes are locked until recovery is resolved."
                ),
                "recovery_required": True,
                "resolution_actions": recovery["resolution_actions"],
                "restore_available": recovery["restore_available"],
                "keep_available": recovery["keep_available"],
                "manual_review_required": recovery["manual_review_required"],
                "message": (
                    "Library Doctor found an external change while reconciling an interrupted directory transaction. "
                    "It preserved both the current package and the verified recovery backup for manual review."
                ),
                "undo_available": False,
                "recovered_transaction": False,
                "file_handling": self._file_handling(backup_id),
            })
        return receipts

    def _backup_summary(self, transaction: dict) -> dict:
        try:
            metadata, _originals = self._read_backup(
                transaction["backup_id"], transaction["package"]
            )
            summary = metadata.get("summary")
            return summary if isinstance(summary, dict) else {}
        except self._error_type:
            return {}
