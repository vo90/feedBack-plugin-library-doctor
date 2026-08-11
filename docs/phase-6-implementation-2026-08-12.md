# Phase 6 implementation: residual transaction safety and executable proof

Date: 2026-08-12
Plugin version: `0.42.0`
Repository boundary: standalone `feedBack-plugin-library-doctor` only

## Outcome

Phase 6 closes the highest-priority implementation and evidence gaps found by
the full production-readiness audit review. Recovery backups are now reopened
and byte-verified before any package commit, directory Apply and Undo recovery
are exercised with real child-process termination, a batch crash is tested
inside a package transaction, archive IO faults fail closed, commit-time parent
link swaps are rejected, validation workers enforce a Python-process write
guard, and the nightly browser suite covers a complete synthetic scan, repair,
and Undo journey.

No FeedBack nightly, Desktop, or other repository source file was changed.

## Recovery-backup verification

`RepairService` now reopens every newly renamed recovery ZIP and verifies its
schema, archive integrity, member set, stored hashes, and original member bytes.
It performs the verification once immediately after creation and again after
the named `backup_durable` fault boundary. Corruption at either point removes
the unusable backup and leaves the package unchanged.

Archive candidate tests now inject both a truncated copy and an `OSError`
representing disk exhaustion. Candidate member/CRC verification rejects the
short read; candidate construction rejects the disk error. Neither path creates
a recovery backup or replaces the source archive.

## Real process-death matrix

`tests/fixtures/transaction_process.py` runs the real `RepairService` and
`BatchRepairManager` in a separate Python process. The barrier callback uses
`os._exit(86)`, so Python exception cleanup and `finally` blocks cannot run.

The restart suite covers directory Apply termination after:

- durable journal creation;
- durable backup rename;
- first member replacement before journal progress;
- persisted first-member progress;
- the package-committed marker before normal cleanup.

It covers Undo at the journal, first-member replacement/progress, and package
commit boundaries. Every restart resolves to a uniform repaired or original
package; no mixed package or pending journal remains. A three-package batch is
also terminated during the second package. The first package receipt/checkpoint
survives, the current package is reconciled, and the third package is untouched.

This matrix exposed and fixed a real defect: interrupted Undo reconciliation
previously treated the original repair receipt as if it were already an Undo
receipt because both share a backup ID. Recovery now matches backup ID, action,
and outcome before suppressing a receipt.

## Commit containment and read-only workers

Named `before_archive_replace` and `before_member_replace` barriers now sit at
the final commit boundary. Source identity, containment, and affected bytes are
checked again after each barrier and immediately before replacement. A real
temporary Windows directory junction (or POSIX directory symlink) is swapped
into the package parent at that boundary; the external target remains unchanged
and the recovery backup/journal are cleaned safely.

Spawned validation workers install a Python audit hook before accepting work.
While a package is active, Python-level open/create/truncate/rename/delete and
metadata mutation attempts under that package are denied with
`PermissionError`. Writes outside the package remain available for runtime
diagnostics. This is an in-process capability boundary, not an operating-system
ACL and not a claim about independently spawned native programs.

No portable Python filesystem API offers an atomic compare-and-swap of arbitrary
file contents on every supported Windows/Linux filesystem. A theoretical
external write after the final recheck but before the replacement instruction
therefore remains a platform-level residual. The repeated checks, guarded
member protocol, durable recovery journal, external-edit-preserving rollback,
and exact-boundary tests are the chosen fail-closed mitigation.

## Browser end-to-end evidence

The latest-nightly Playwright fixture now supports a stateful synthetic plugin
route. One journey starts in the first-run state, starts and completes a scan,
opens a finding, reviews and applies its repair, enters the Undo confirmation,
restores the original, and verifies the complete request sequence and resulting
UI states. All data and mutations remain intercepted; the configured user
library is never read or changed.

The existing FastAPI integration suite continues to exercise the corresponding
real scanner, repair, backup, cache refresh, history, and Undo services against
disposable packages.

## Verification

The complete local gate run passed:

- `python -m pytest --cov --cov-report=term`: 428 passed, 85.70% total
  coverage (the configured minimum is 85%);
- `python -m ruff check .` and `python -m compileall -q .`: passed;
- `npm run check:frontend` and `npm run lint:frontend`: passed;
- `npm run test:frontend`: 20 passed;
- `npm run test:browser`: 6 passed against the latest-nightly host, including
  the new stateful scan, repair, and Undo journey;
- `python -m pip check`: no broken requirements;
- `python -m pip_audit -r requirements.txt`: no known vulnerabilities;
- `npm audit --audit-level=high`: zero vulnerabilities; and
- `git diff --check`: passed (Git reports only the repository's existing
  Windows line-ending conversion notices).

## Remaining release signoff

Phase 6 does not replace the manual NVDA, Windows zoom/Contrast Themes, minimum
host, remote Windows/Linux CI, or human novice-usability signoffs documented by
the audit. Those remain release validation rather than unimplemented Phase 6
transaction behavior.
