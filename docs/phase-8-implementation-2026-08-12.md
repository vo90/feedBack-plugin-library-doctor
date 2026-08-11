# Phase 8 implementation: audit residual closure

Date: 2026-08-12
Plugin version: `0.44.0`
Repository boundary: standalone `feedBack-plugin-library-doctor` only
Baseline commit: `003bee7` (`feat: complete Library Doctor audit phases 0-7`)

## Outcome

Phase 8 closes the implementation gaps found by reconciling the complete audit
safety matrix, rollout guidance, and evidence limitations against the Phase 7
code. These items were recommendations outside the audit's numbered phase
roadmap, so they were not all represented by a registered `LD-AUD-*` finding.

No FeedBack nightly, Desktop, or other repository source file was changed.

## Bounded FFmpeg loudness analysis

Automatic preview selection no longer captures the complete decoded PCM stream
in process memory. FFmpeg writes to an isolated temporary PCM file with all of
the following limits:

- fixed mono 16-bit output at 400 Hz;
- the probed source duration supplied as an explicit `-t` boundary;
- an eight-hour analysis ceiling;
- a 23,040,000-byte FFmpeg `-fs` ceiling and matching post-run size check;
- a 120-second process timeout; and
- discarded stdout/stderr instead of `capture_output=True`.

Missing, failed, oversized, over-duration, and timed-out analysis returns the
existing safe fallback instead of generating an unbounded artifact. Tests prove
the cap arguments, loudest-window result, oversized rejection, timeout result,
and removal of partial temporary files.

## Complete directory-candidate integrity

Directory repair and Undo candidates now receive an archive-equivalent complete
member check before validation. The verifier:

- walks source and candidate without following links;
- compares the complete member/type manifest;
- requires planned additions, changes, and deletions to match exactly;
- compares unchanged link targets;
- treats same-volume hard-linked members as exact by construction; and
- SHA-256 compares unchanged files when candidate creation had to copy them.

Member enumeration is capped at 50,000. Injected same-size corruption in an
unrelated member fails with `candidate_integrity_failed`, leaves the source
unchanged, and removes the temporary candidate.

## Large-file cache invalidation

Large-file signatures now combine nine windows spread across the file with the
filesystem's native change record. On Windows, Library Doctor reads NTFS
`ChangeTime` through `GetFileInformationByHandleEx`, because Python exposes the
creation time as `st_ctime` there. Other systems use `st_ctime_ns`.

A deterministic randomized test mutates twelve positions distributed across
all nine content windows, flushes the writes, restores the original file size
and modification time, and proves that every sampled mutation invalidates the
directory package signature. A separate deterministic test proves that a
native change-record update is also bound into the signature.

The cache remains an invalidation optimization rather than a cryptographic
boundary. An edit outside the sampled windows can evade the cache if size and
modification time are preserved and the filesystem does not produce a distinct
change record; a privileged process capable of forging native records can do
the same. README states that threat model and points users to **Recheck without
cache**. Repair Apply and Undo continue to use independent exact hashes.

## Exact 0.34.0 compatibility

The versioned `library_doctor_0_34_state.json` fixture materializes an actual
`0.34.0` v3 recovery ZIP, v1 history receipt, repaired directory package, and no
transaction journal. Current code must expose the old Undo, validate the exact
original candidate, restore it through the current durable transaction path,
preserve an unrelated member, consume the backup, remove the journal, and retain
both the historical repair receipt and current restore receipt.

## Explicit residual-risk decisions

Three platform boundaries remain accepted rather than misrepresented as solved:

1. **Final filesystem instruction window.** Portable Python and supported
   Windows/Linux filesystems do not offer an atomic compare-and-swap of arbitrary
   file contents. Library Doctor performs repeated identity, containment, and
   affected-byte checks immediately before replacement, preserves external edits
   during rollback, and restart-reconciles durable journals. A theoretical write
   between the last check and the replacement instruction remains accepted.
2. **Validation worker capability boundary.** Workers install a Python audit
   hook that denies package mutations by current Python validation code. It is
   not an OS ACL or sandbox for independently spawned native programs. The
   validator currently launches no native child and imports no mutation service.
   Adding either capability reopens the OS-sandbox requirement and must not be
   treated as covered by the current guard.
3. **Bounded large-file cache signatures.** Reading and hashing every byte of
   every large audio member would remove the intended cache-performance
   benefit. Distributed samples plus a native change record catch sampled edits
   and ordinary filesystem-observable changes, but a same-metadata edit outside
   the samples can evade invalidation when the filesystem change record does not
   advance. A fresh scan is available explicitly; repair commits use exact
   hashes and do not inherit this cache limitation.

These decisions narrow claims to what the implementation and executable tests
actually prove. They are not waivers for any failed package, recovery, privacy,
or accessibility behavior.

## Release status

Phase 8 completes the audit-derived local engineering work. The version-bound
release ledger remains authoritative for NVDA, Windows display modes, minimum
host runtime, novice usability, remote CI, and clean release-worktree evidence.

## Verification

The complete local gate passed after implementation:

- `python -m pytest --cov --cov-report=term`: 444 passed with 85.57% total
  coverage (configured minimum: 85%);
- the constrained one-worker scanner, worker, and adversarial corpus: 77 passed;
- `python -m ruff check .` and the CI production/tool `py_compile` target: passed;
- `npm run check:frontend` and `npm run lint:frontend`: passed;
- `npm run test:frontend`: 20 passed;
- `npm run test:browser`: 6 passed against the latest-nightly host, including
  the synthetic scan/repair/Undo journey and both accessibility journeys;
- the host verifier passed every capability against current local nightly
  commit `eef58c88c315b59375926bbfc8740cd18cb958f9`;
- `python -m pip check`: no broken requirements;
- `python -m pip_audit -r requirements.txt`: no known vulnerabilities;
- `npm audit --audit-level=high`: zero vulnerabilities; and
- `git diff --check`: passed.
