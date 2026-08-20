"""Recovery-state policy for Library Doctor package mutations."""

from __future__ import annotations

from collections.abc import Iterable


class RecoveryPolicy:
    """Keep recovery selection, public state, and mutation gating in one seam."""

    def __init__(
        self,
        *,
        backup_size,
        error_type,
        lock,
        prepare_finalize,
        prepare_restore,
        read_history,
        read_transactions,
        recover_legacy_receipts,
        resolve_package,
        valid_backup_id,
    ) -> None:
        self._backup_size = backup_size
        self._error_type = error_type
        self._lock = lock
        self._prepare_finalize = prepare_finalize
        self._prepare_restore = prepare_restore
        self._read_history = read_history
        self._read_transactions = read_transactions
        self._recover_legacy_receipts = recover_legacy_receipts
        self._resolve_package = resolve_package
        self._valid_backup_id = valid_backup_id

    def pending_repair(self, package_name: str) -> dict | None:
        history = self._read_history()
        known = {
            item.get("backup_id")
            for item in history
            if isinstance(item, dict) and isinstance(item.get("backup_id"), str)
        }
        history.extend(
            item
            for item in self._recover_legacy_receipts()
            if item.get("backup_id") not in known
        )
        history.sort(key=lambda item: float(item.get("completed_at") or 0))
        for item in reversed(history):
            if item.get("package") != package_name:
                continue
            if item.get("action") != "repair" or item.get("outcome") != "success":
                continue
            backup_id = item.get("backup_id")
            if (
                not isinstance(backup_id, str)
                or not self._valid_backup_id(backup_id)
                or self._backup_size(backup_id) is None
            ):
                continue
            return {
                "backup_id": backup_id,
                "undo_available": True,
                "change_count": int(item.get("change_count") or 0),
                "change_kind": str(item.get("change_kind") or "repair"),
                "title": str(item.get("title") or package_name),
                "artist": str(item.get("artist") or ""),
            }
        return None

    def required_transaction(self, package_name: str) -> dict | None:
        pending = [
            transaction
            for transaction in self._read_transactions()
            if transaction.get("package") == package_name
            and transaction.get("phase") == "recovery_required"
        ]
        if not pending:
            return None
        return max(
            pending,
            key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0),
        )

    def resolution_actions(self, transaction: dict) -> list[str]:
        package = transaction.get("package")
        backup_id = transaction.get("backup_id")
        if not isinstance(package, str) or not isinstance(backup_id, str):
            return []
        actions = []
        prepared = None
        try:
            prepared = self._prepare_restore(package, backup_id, deep_audio=False)
            actions.append("restore")
        except self._error_type:
            pass
        finally:
            if prepared is not None:
                prepared["_cleanup"]()
        try:
            self._prepare_finalize(package, backup_id)
            actions.append("finalize")
        except self._error_type:
            pass
        return actions

    def public_required(self, transaction: dict | None) -> dict:
        if transaction is None:
            return {"required": False}
        backup_id = transaction["backup_id"]
        actions = self.resolution_actions(transaction)
        return {
            "required": True,
            "file_state": "recovery_required",
            "package": transaction["package"],
            "backup_id": backup_id,
            "backup_available": self._backup_size(backup_id) is not None,
            "restore_available": "restore" in actions,
            "keep_available": "finalize" in actions,
            "resolution_actions": actions,
            "manual_review_required": not actions,
            "message": (
                "Library Doctor could not confirm a complete package after an interrupted repair. "
                "Scanning is still safe, but this song is locked against further changes until recovery is resolved."
            ),
            "next_action": "resolve_recovery",
        }

    def state(self, package: str) -> dict:
        with self._lock:
            _root, _path, package_name = self._resolve_package(package)
            return self.public_required(self.required_transaction(package_name))

    def states(self, packages: Iterable[str]) -> dict[str, dict]:
        requested = {str(item) for item in packages if isinstance(item, str) and item}
        if not requested:
            return {}
        with self._lock:
            newest: dict[str, dict] = {}
            for transaction in self._read_transactions():
                package = transaction.get("package")
                if package not in requested or transaction.get("phase") != "recovery_required":
                    continue
                current = newest.get(package)
                current_time = float(
                    current.get("updated_at") or current.get("created_at") or 0
                ) if current else -1
                transaction_time = float(
                    transaction.get("updated_at") or transaction.get("created_at") or 0
                )
                if transaction_time >= current_time:
                    newest[package] = transaction
            return {
                package: self.public_required(transaction)
                for package, transaction in newest.items()
            }

    def assert_mutation_allowed(
        self,
        package_name: str,
        *,
        operation: str,
        backup_id: str | None = None,
    ) -> dict | None:
        pending = self.required_transaction(package_name)
        if pending is None:
            return None
        if operation in {"restore", "finalize"} and backup_id == pending.get("backup_id"):
            return pending
        raise self._error_type(
            "recovery_required",
            "This song has an interrupted repair that must be resolved before Library Doctor can change it again.",
            file_state="recovery_required",
        )
