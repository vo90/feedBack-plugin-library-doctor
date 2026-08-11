# Phase 4 implementation: deeper reliability and backend contracts

Date: 2026-08-11
Plugin version: `0.40.0`
Repository boundary: standalone `feedBack-plugin-library-doctor` only

## Outcome

Phase 4 is complete. Library Doctor now has strict typed mutation requests, one
structured error contract, durable mutation request receipts, deterministic
standalone Finalize/Undo exclusion, and explicit SQLite corruption and lock
recovery behavior.

The new inputs are additive: callers may continue to omit a mutation request
ID. The frontend still understands the legacy string error shape during the
transition, while every current plugin route emits the new envelope. No
FeedBack host or Desktop source file was changed.

## Typed API boundary

`api_contracts.py` owns strict request models and additive response models.
Unknown mutation fields, coercible booleans, missing fields, and unsafe shapes
now fail before a service runs. OpenAPI tests assert the request-model mapping
for playback, Apply, automatic preview, Fix all, Undo, and Finalize.

Every plugin route failure now has this detail shape:

```json
{
  "code": "stable_machine_code",
  "message": "Safe user-facing explanation.",
  "file_state": "unchanged",
  "retryable": false,
  "next_action": "correct_request"
}
```

The route boundary normalizes Pydantic failures and otherwise-unhandled
backend faults without exposing parser, database, song, or local-path details.
The API client exposes `code`, `fileState`, `retryable`, and `nextAction` on the
resulting JavaScript error.

## Idempotent mutation receipts

Apply, automatic preview creation, Fix all, Undo, and recovery Finalize accept
an optional `request_id` in the strict JSON body or an `Idempotency-Key` header.
If both are present, they must match. Each ID is bound to a SHA-256 fingerprint
of its operation and safety-relevant inputs; reuse with different inputs fails
closed before any package operation starts.

`mutation_receipts.py` stores a private, bounded ledger under the plugin config
directory. It uses bounded reads, schema and size validation, an in-process
lock, fsync, and atomic replacement. States are `pending` or `complete`:

- a completed retry returns the original stable receipt with
  `idempotent_replay: true`;
- a pending request can reconcile the matching durable RepairService history
  after a response/ledger interruption;
- an unchanged domain rejection releases its reservation for an intentional
  corrected retry;
- an ambiguous unexpected mutation failure remains pending and directs the
  caller to receipt recovery instead of risking a duplicate write;
- corrupt or unknown receipt storage fails closed and is not overwritten.

Receipts can be inspected with
`GET /api/plugins/library_doctor/repair/receipt/{request_id}`. The ledger is
limited to 256 receipts, 30 days, 2 MiB total, and 512 KiB per receipt.

## Finalization reservation

Standalone recovery Finalize now acquires the same scanner mutation lane used
by Apply and Undo. A concurrent Undo deterministically receives
`operation_busy` with `retryable: true`; Finalize then completes alone. A retry
with the same request ID replays the completed receipt even though the recovery
ZIP has already been removed.

This closes the audit's specific standalone Finalize/Undo race without adding
locks to FeedBack or Desktop.

## Worker, cache, and database faults

Phase 0 already introduced worker deadlines, forced process termination,
replacement, bounded shutdown, and fault tests. Phase 4 retains that supervisor
and adds cache/database behavior:

- a corrupt SQLite report cache is closed, atomically quarantined with a
  `.corrupt-*` suffix, and replaced with a clean cache;
- WAL/SHM companions are quarantined with the same incident suffix;
- SQLite busy/locked startup uses the configured busy timeout plus bounded
  retry/backoff;
- a short external database lock recovers without losing the cache;
- an unexpected database fault at a route boundary returns the uniform,
  privacy-safe error envelope.

The report cache is derived scan data, so rebuilding it does not modify song
packages or destroy recovery receipts.

## Structured presentation facts

The audit listed further extraction of presentation copy as optional. Existing
receipts already carry stable structured facts such as `change_kind`, counts,
rule codes, package state, Undo availability, media facts, and file-handling
facts. Phase 4 preserves those facts and does not perform a broad copy rewrite,
avoiding an unrelated UX behavior change after Phase 3.

## Regression coverage

- Canonical typed request, response, and error fixtures.
- OpenAPI request-model and shared-error schema mapping.
- Strict malformed-body matrix and unknown-field rejection.
- Durable complete replay across a receipt-store restart.
- Request-ID input mismatch and different-input reuse rejection.
- Pending receipt reconciliation and unchanged-failure release.
- Corrupt receipt ledger fail-closed behavior.
- Apply and Undo route replay after their source state has changed.
- Concurrent Finalize/Undo exclusion and Finalize replay after backup deletion.
- SQLite corruption quarantine/rebuild and short-lock recovery.
- Privacy-safe unexpected database-fault response.
- Frontend normalization of retry and next-action facts.

## Verification

- Python: `385 passed`; total coverage `85.45%` (required `85%`).
- Ruff and Python production-module compilation: passed.
- Native JavaScript parse and ESLint dependency/size gates: passed.
- Node/JSDOM frontend: `18 passed`.
- Latest-nightly Playwright host suite: `3 passed`.
- Runtime: Library Doctor `0.40.0`, enabled and ready; module manifest loaded;
  scan, repair, and batch states idle.
- Git hygiene: `git diff --check` passed. FeedBack nightly and Desktop remain
  unmodified and exactly match their official `upstream/main` commits. The
  plugin base commit still exactly matches its GitHub `main` commit.

## Completion criteria

- All plugin endpoints share one error contract: passed.
- Repeated mutation requests return a safe stable outcome: passed.
- Concurrent standalone Finalize/Undo is deterministic: passed.
- SQLite lock recovery is explicitly tested: passed.
- Legacy callers can omit request IDs and the frontend tolerates old/new error
  responses during the transition: passed.
