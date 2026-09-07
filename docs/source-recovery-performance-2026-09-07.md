# Source recovery performance and reusable previews

## Objective

The first full preview spent over six hours processing roughly 5,400 packages. Each repairable candidate rebuilds and validates a complete temporary Feedpak, then discards it. Apply repeats that work. Fix the measured bottlenecks without interrupting the current preview or applying real-library repairs.

## Branch and scope

Library Doctor only: `perf/doctor-source-recovery`, based on the tested source-index isolation commit `c62d807`. Doctor main is still `d47e5ff` after fetch and is already in the ancestry. Keep the running projection unchanged until its completed report is captured. After verification, merge into the existing Doctor integration/runtime branches and use the supported launcher.

## Implementation

1. Profile representative copied real packages and time source matching, validation, archive building and identity checks. Record both before and after; distinguish measured gains from estimates.
2. Validate changed arrangement documents in memory during source preview. Do not build or reread complete temporary archives at this stage. Identify the validation scope honestly in the UI. Apply retains full package validation, integrity checks, durable backups and exact Undo.
3. Cache bounded serialized exact chart-matching and arrangement-validation results by input content and validator identity so identical full/No Guitar charts share work. Reuse completed current-version scan reports only with exact live package signatures. Retain full after-validation, ZIP CRC, backups and commit guards. Profiling found ZIP rewriting was a small fraction of runtime, so leave that implementation unchanged.
4. Capture the running old preview through its read-only status API when it completes. Its public report has source choices but lacks private hashes and actionable plan bindings. Treat imported choices as hints requiring fresh exact chart validation and fresh review; do not claim to restore the old plan or renewed folder-wide uniqueness.
5. Add reuse of completed source choices without another Library Health scan, folder-wide PSARC index or whole-archive preview. Recheck only prior eligible repairs; skip previously unchanged/blocked packages before reading their files and label them as not rechecked. A failed selected original blocks its own package. Issue fresh hash-bound plan IDs after validation.
6. Persist future completed source previews with their required scan/binding evidence so restart does not discard the review. Validate schema, scope, digest/version and fresh input guards; never resume Apply automatically.

## Verification and delivery

Cover changed inputs, invalid/ambiguous source alternatives, both audio variants, compilation choices, stale/imported reports, restart, corrupted saved state and refusal before mutation. Test Apply/Undo only on disposable copies. Run relevant full suites and benchmark the final implementation. Preserve the completed legacy report before restart, then rebuild only a fresh lightweight review from its source choices. No real-library repairs or source PSARC edits.

## Results

Measured on disposable copies of 38 Special / Caught Up in You, with ordinary and No Guitar packages:

| Operation | Committed c62d807 baseline | Updated code |
| --- | ---: | ---: |
| Ordinary preview, cold source | 10.91 s | 5.18 s |
| No Guitar preview, shared source already checked | 7.61 s | 0.58 s |
| Ordinary Apply | 9.00 s | 3.55 s |
| No Guitar Apply | 8.06 s | 3.74 s |

The updated Apply uses the exact completed standard-scan report. An isolated control using the same new preview/cache code but without original-report reuse took 6.61 s / 6.16 s. Reusing that report removed one complete original-package validation; one full candidate validation remains. ZIP rewrite plus CRC represented roughly 0.14–0.34 s on the measured packages, so it did not justify changing archive handling.

Both ordinary and No Guitar copies restored exactly after Undo; original source and library inputs were unchanged. These are a small representative pair, not a whole-library throughput prediction. New previews still need to decode and match changed or uncached charts, and Apply still performs complete candidate validation and backup work.

Final verification: 949 Python tests passed, one Windows symlink-privilege test was skipped, and one existing dependency deprecation warning remains. All 120 frontend tests passed; frontend syntax, focused ESLint, Ruff and whitespace checks passed. The verified runtime ZIP contains 97 files. An additional normal-report signature check at the commit boundary was added after the timings above and covered by regression tests. Delivery revision and live-preview preservation status are recorded in the task handoff.
