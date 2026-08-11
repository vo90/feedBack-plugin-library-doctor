import logging

import pytest

from mutation_receipts import (
    MutationReceiptError,
    MutationReceiptStore,
    request_fingerprint,
)


def _store(tmp_path):
    return MutationReceiptStore(
        tmp_path / "config",
        logging.getLogger("library-doctor-mutation-receipts-tests"),
    )


def test_completed_receipt_replays_after_store_restart(tmp_path):
    request_id = "phase4-apply-0001"
    operation = "repair.apply"
    fingerprint = request_fingerprint(
        operation,
        {"package": "Artist/Song.feedpak", "plan_id": "a" * 64},
    )
    store = _store(tmp_path)

    assert store.begin(request_id, operation, fingerprint) is None
    assert store.lookup(request_id)["state"] == "pending"
    store.complete(
        request_id,
        operation,
        fingerprint,
        {"outcome": "success", "backup_id": "20260811-120000-abcdef123456"},
    )

    replay = _store(tmp_path).begin(request_id, operation, fingerprint)
    assert replay["outcome"] == "success"
    assert replay["request_id"] == request_id
    assert replay["idempotent_replay"] is True
    lookup = _store(tmp_path).lookup(request_id)
    assert lookup["schema"] == "library_doctor.mutation_receipt.v1"
    assert lookup["state"] == "complete"
    assert lookup["receipt"]["backup_id"] == "20260811-120000-abcdef123456"


def test_request_id_cannot_be_reused_for_different_inputs(tmp_path):
    store = _store(tmp_path)
    request_id = "phase4-apply-0002"
    first = request_fingerprint("repair.apply", {"plan_id": "a" * 64})
    second = request_fingerprint("repair.apply", {"plan_id": "b" * 64})
    store.begin(request_id, "repair.apply", first)

    with pytest.raises(MutationReceiptError) as raised:
        store.begin(request_id, "repair.apply", second)

    assert raised.value.code == "idempotency_key_reused"
    assert raised.value.file_state == "unchanged"


def test_pending_reservation_can_reconcile_service_history(tmp_path):
    store = _store(tmp_path)
    request_id = "phase4-restore-0001"
    operation = "repair.restore"
    fingerprint = request_fingerprint(operation, {"backup_id": "known"})
    store.begin(request_id, operation, fingerprint)

    replay = _store(tmp_path).begin(
        request_id,
        operation,
        fingerprint,
        recovered_receipt={"outcome": "restored", "receipt_saved": True},
    )

    assert replay["outcome"] == "restored"
    assert replay["idempotent_replay"] is True
    assert _store(tmp_path).lookup(request_id)["state"] == "complete"


def test_unchanged_failure_releases_reservation_for_intentional_retry(tmp_path):
    store = _store(tmp_path)
    request_id = "phase4-finalize-0001"
    operation = "repair.finalize"
    fingerprint = request_fingerprint(operation, {"backup_id": "known"})
    store.begin(request_id, operation, fingerprint)

    store.abandon(request_id, operation, fingerprint)

    assert store.begin(request_id, operation, fingerprint) is None


def test_corrupt_receipt_store_fails_closed_without_overwriting_it(tmp_path):
    store = _store(tmp_path)
    store._path.parent.mkdir(parents=True)
    original = b"not-json-private-receipts"
    store._path.write_bytes(original)
    fingerprint = request_fingerprint("repair.apply", {"plan_id": "a" * 64})

    with pytest.raises(MutationReceiptError) as raised:
        store.begin("phase4-apply-0003", "repair.apply", fingerprint)

    assert raised.value.code == "idempotency_store_unavailable"
    assert raised.value.retryable is True
    assert store._path.read_bytes() == original
