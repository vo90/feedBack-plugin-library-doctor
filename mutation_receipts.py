"""Durable idempotency reservations and mutation receipt replay."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


SCHEMA = "library_doctor.mutation_receipts.v1"
PUBLIC_SCHEMA = "library_doctor.mutation_receipt.v1"
MAX_RECEIPTS = 256
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 512 * 1024
MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class MutationReceiptError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        next_action: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.file_state = "unchanged"
        self.retryable = retryable
        self.next_action = next_action


def request_fingerprint(operation: str, facts: dict[str, Any]) -> str:
    payload = json.dumps(
        {"operation": operation, "facts": facts},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class MutationReceiptStore:
    """Keep a bounded private receipt ledger outside the song library."""

    def __init__(self, config_dir: Path, log) -> None:
        self._path = Path(config_dir) / "library_doctor" / "mutation_receipts.json"
        self._log = log
        self._lock = threading.RLock()

    @staticmethod
    def validate_request_id(request_id: str) -> str:
        if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
            raise MutationReceiptError(
                "invalid_request_id",
                "The mutation request ID is invalid.",
                retryable=False,
                next_action="create_new_request",
            )
        return request_id

    @staticmethod
    def _validate_identity(operation: str, fingerprint: str) -> None:
        if (
            not isinstance(operation, str)
            or not operation
            or len(operation) > 100
            or not isinstance(fingerprint, str)
            or not _FINGERPRINT_RE.fullmatch(fingerprint)
        ):
            raise MutationReceiptError(
                "invalid_idempotency_identity",
                "The mutation retry identity is invalid.",
                retryable=False,
                next_action="create_new_request",
            )

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_bytes()
            if len(raw) > MAX_STORE_BYTES:
                raise ValueError("receipt store exceeds its size limit")
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("schema") != SCHEMA or not isinstance(payload.get("items"), list):
                raise ValueError("receipt store schema is invalid")
            return [
                item
                for item in payload["items"]
                if isinstance(item, dict)
                and item.get("state") in {"pending", "complete"}
                and isinstance(item.get("request_id"), str)
            ][-MAX_RECEIPTS:]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._log.warning(
                "Library Doctor mutation receipt storage is unavailable: %s",
                type(exc).__name__,
            )
            raise MutationReceiptError(
                "idempotency_store_unavailable",
                "Mutation retry records cannot be read safely. No new change was started.",
                retryable=True,
                next_action="retry_later",
            ) from exc

    def _write(self, items: list[dict[str, Any]]) -> None:
        payload = json.dumps(
            {"schema": SCHEMA, "items": items[-MAX_RECEIPTS:]},
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        ).encode("utf-8")
        if len(payload) > MAX_STORE_BYTES:
            raise MutationReceiptError(
                "idempotency_store_full",
                "Mutation retry storage is full. No new change was started.",
                retryable=True,
                next_action="retry_later",
            )
        temporary = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".mutation-receipts-",
                suffix=".tmp",
                dir=self._path.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except OSError as exc:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            self._log.warning(
                "Library Doctor could not persist mutation retry storage: %s",
                type(exc).__name__,
            )
            raise MutationReceiptError(
                "idempotency_store_unavailable",
                "Mutation retry records cannot be saved safely. No new change was started.",
                retryable=True,
                next_action="retry_later",
            ) from exc

    @staticmethod
    def _prune(items: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        cutoff = now - MAX_AGE_SECONDS
        current = [
            item for item in items
            if float(item.get("updated_at") or item.get("created_at") or 0) >= cutoff
        ]
        return current[-(MAX_RECEIPTS - 1):]

    @staticmethod
    def _matching(
        items: list[dict[str, Any]],
        request_id: str,
    ) -> dict[str, Any] | None:
        return next(
            (item for item in reversed(items) if item.get("request_id") == request_id),
            None,
        )

    @staticmethod
    def _assert_same(
        item: dict[str, Any],
        operation: str,
        fingerprint: str,
    ) -> None:
        if item.get("operation") != operation or item.get("fingerprint") != fingerprint:
            raise MutationReceiptError(
                "idempotency_key_reused",
                "This mutation request ID was already used for different inputs.",
                retryable=False,
                next_action="create_new_request",
            )

    @staticmethod
    def _replay(item: dict[str, Any]) -> dict[str, Any]:
        receipt = copy.deepcopy(item.get("receipt"))
        if not isinstance(receipt, dict):
            raise MutationReceiptError(
                "idempotency_receipt_unavailable",
                "The saved mutation receipt is incomplete.",
                retryable=True,
                next_action="check_receipt",
            )
        receipt["request_id"] = item["request_id"]
        receipt["idempotent_replay"] = True
        return receipt

    def begin(
        self,
        request_id: str,
        operation: str,
        fingerprint: str,
        *,
        recovered_receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Reserve a request ID or return its original completed receipt."""
        request_id = self.validate_request_id(request_id)
        self._validate_identity(operation, fingerprint)
        with self._lock:
            items = self._read()
            existing = self._matching(items, request_id)
            if existing is not None:
                self._assert_same(existing, operation, fingerprint)
                if existing.get("state") == "complete":
                    return self._replay(existing)
                if isinstance(recovered_receipt, dict):
                    existing["state"] = "complete"
                    existing["receipt"] = copy.deepcopy(recovered_receipt)
                    existing["updated_at"] = time.time()
                    self._write(items)
                    return self._replay(existing)
                raise MutationReceiptError(
                    "request_in_progress",
                    "A mutation with this request ID is still in progress or needs receipt recovery.",
                    retryable=True,
                    next_action="check_receipt",
                )

            now = time.time()
            items = self._prune(items, now)
            items.append({
                "request_id": request_id,
                "operation": operation,
                "fingerprint": fingerprint,
                "state": "pending",
                "created_at": now,
                "updated_at": now,
                "receipt": None,
            })
            self._write(items)
            return None

    def complete(
        self,
        request_id: str,
        operation: str,
        fingerprint: str,
        receipt: dict[str, Any],
    ) -> None:
        request_id = self.validate_request_id(request_id)
        self._validate_identity(operation, fingerprint)
        try:
            receipt_size = len(json.dumps(
                receipt,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise MutationReceiptError(
                "idempotency_receipt_invalid",
                "The mutation completed but its retry receipt could not be serialized.",
                retryable=True,
                next_action="check_receipt",
            ) from exc
        if receipt_size > MAX_RECEIPT_BYTES:
            raise MutationReceiptError(
                "idempotency_receipt_too_large",
                "The mutation completed but its retry receipt exceeds the storage limit.",
                retryable=True,
                next_action="check_receipt",
            )
        with self._lock:
            items = self._read()
            existing = self._matching(items, request_id)
            if existing is None:
                raise MutationReceiptError(
                    "idempotency_reservation_missing",
                    "The mutation completed but its retry reservation is missing.",
                    retryable=True,
                    next_action="check_receipt",
                )
            self._assert_same(existing, operation, fingerprint)
            stored = copy.deepcopy(receipt)
            stored["request_id"] = request_id
            stored["idempotent_replay"] = False
            existing["state"] = "complete"
            existing["receipt"] = stored
            existing["updated_at"] = time.time()
            self._write(items)

    def abandon(self, request_id: str, operation: str, fingerprint: str) -> None:
        """Release only an unchanged failed mutation so an intentional retry can run."""
        request_id = self.validate_request_id(request_id)
        self._validate_identity(operation, fingerprint)
        with self._lock:
            items = self._read()
            existing = self._matching(items, request_id)
            if existing is None:
                return
            self._assert_same(existing, operation, fingerprint)
            if existing.get("state") != "pending":
                return
            items.remove(existing)
            self._write(items)

    def lookup(self, request_id: str) -> dict[str, Any]:
        request_id = self.validate_request_id(request_id)
        with self._lock:
            item = self._matching(self._read(), request_id)
            if item is None:
                raise MutationReceiptError(
                    "receipt_not_found",
                    "No mutation receipt exists for this request ID.",
                    retryable=False,
                    next_action="create_new_request",
                )
            return {
                "schema": PUBLIC_SCHEMA,
                "request_id": request_id,
                "operation": item.get("operation", ""),
                "state": item.get("state", "pending"),
                "receipt": (
                    copy.deepcopy(item.get("receipt"))
                    if item.get("state") == "complete"
                    else None
                ),
            }
