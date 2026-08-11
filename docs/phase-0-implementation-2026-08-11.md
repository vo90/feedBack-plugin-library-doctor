# Phase 0 implementation record

Date: 2026-08-11
Baseline audit: `docs/production-readiness-audit-2026-08-11.md`
Starting upstream commit: `6435c216a7a1d32fc43c3d1602fa7a1df6d622f0`
Plugin version after this phase: `0.35.0`

The production-readiness audit remains the immutable baseline. This record maps
the first implementation pass to the Phase 0 findings and provides the gate
evidence used to move forward.

## Completed scope

### LD-AUD-002 — final-source and containment binding

- Commits are bound to the resolved package identity and exact affected member
  bytes; archive commits also bind the complete archive SHA-256.
- Source state is checked after backup creation, immediately before archive
  replacement, and before every directory-member write.
- Directory identity is rechecked after the final member write.
- Rollback restores only members that still match Library Doctor's committed
  replacement. A newer external edit or a replaced package path is preserved
  and moves the transaction to `recovery_required`.
- Named transaction barriers provide deterministic race and fault injection.

### LD-AUD-003 — durable directory transaction recovery

- A private, atomically written and fsynced transaction journal is required
  before the first directory-member write.
- Journal state is persisted at `prepared`, `committing`, per-member progress,
  `package_committed`, and `recovery_required` boundaries.
- Startup reconciliation verifies the journal against the existing v3 recovery
  backup and current member hashes before taking action.
- Fully committed packages are accepted and receive a recovered receipt.
- Known partial repairs restore the exact original members. Interrupted Undo
  operations complete restoration to the exact originals.
- Unknown external states are not overwritten; current package bytes, journal,
  and backup are retained and surfaced as `recovery_required`.
- Existing v1/v2/v3 backup and legacy history reads remain compatible.

### LD-AUD-004 — bounded validation workers

- Every production scan package, including single-worker scopes, runs through
  the isolated worker backend.
- Standard and Deep Audio package validation have bounded active-time
  deadlines; playback-paused time does not consume the deadline.
- A non-cooperative worker pool is cancelled, terminated, killed if necessary,
  and closed within a bounded interval.
- Remaining packages resume in a replacement pool.
- The timed-out package receives a stable `package.validation-timeout` finding;
  scanning continues and no package write capability exists in the worker.

### LD-AUD-012 — deterministic gate

- The pool-start fallback test now supplies deterministic CPU and memory policy
  inputs instead of depending on live host RAM.
- Route tests that customize the validator use an explicit thread-backed test
  worker instead of relying on the selected worker count.
- Timeout, forced termination, replacement, and performance telemetry have
  deterministic tests.

## Fault matrix covered

- external directory-member edit after durable backup;
- complete archive edit after durable backup;
- process death after a member replacement but before journal progress;
- process death after persisted progress for the first and final members;
- path replacement during a directory commit;
- external edit to an already committed member during final verification;
- journal-storage failure before the first write;
- restart with a known partial state, completed state, and unknown external
  state;
- non-cooperative validation future and forced process termination.

## Verification evidence

- `pytest --cov=. --cov-report=term --cov-fail-under=85`: **356 passed**,
  **85.37%** total coverage on Windows/Python 3.12.
- Ruff: passed for all production modules and tests.
- Python compile gate: passed for all production modules.
- `node --check screen.js`: passed.
- `pip check`: no broken requirements.
- `git diff --check`: passed.
- CI remains configured for both `ubuntu-latest` and `windows-latest`; the Linux
  job will provide the second operating-system result when these changes are
  pushed.

## Baseline decision

Phase 0 is complete for local development and becomes the implementation
baseline for subsequent phases. Phase 1 should start from this transaction and
worker behavior rather than reintroducing the previous unjournaled commit or
unbounded worker lifecycle.
