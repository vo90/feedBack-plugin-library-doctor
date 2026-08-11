# Phase 1 implementation — low-risk UX simplification and terminology

Date: 2026-08-11
Plugin version after this phase: `0.36.0`
Baseline: Phase 0 production-readiness implementation on top of commit `6435c216a7a1d32fc43c3d1602fa7a1df6d622f0`

## Boundary

Every source, test, fixture, documentation, and version change in this phase is
inside the standalone `feedBack-plugin-library-doctor` repository. FeedBack
Desktop and the nightly source repository were used only as an existing local
host for read-only runtime verification; neither repository was modified.

Phase 1 changes presentation and interaction hierarchy only. Scan, repair,
recovery, and batch domain behavior and API contracts are unchanged.

## Implemented audit priorities

- Added a state-guided Library check shell for `first_run`, `scanning`,
  `complete`, `partial`, `stale`, and `outcome` states.
- Reduced the first-run message to: “Check your song library for problems.
  Scanning never changes songs.” The recommended action is `Scan my library`.
- Moved target scope, Deep Audio, and worker tuning under `Scan options`.
  Completed scans collapse the section to scope, completion time, and `Change`.
- Replaced clickable summary cards with four outcome metrics and one results
  filter surface: `All findings`, `Needs fixing`, `May affect FeedBack`,
  `Optional improvements`, and `All songs`.
- Moved coverage filters under `More filters`, and rule aggregation, scan
  provenance, and exports under `Scan details and exports`.
- Moved `Review safe repairs` below the affected-song list. Preview repair
  remains an explicit, unchecked opt-in.
- Scoped repair receipts to `Activity and recovery` inside Library check.
  Historical activity remains reachable but does not open or scroll on entry.
- Renamed novice-facing recovery actions to `Undo repair` and
  `Delete Undo backup…` while preserving their existing confirmation and
  verification behavior.
- Made `Listen and choose a preview` the primary Preview Creator action;
  automatic creation is secondary.
- Changed workspace switching from an incomplete tab pattern to ordinary
  buttons with `aria-pressed`, retained inline Song tools selection details,
  and limited live announcements to concise status/count regions.

## Browser-state fixtures

`tests/fixtures/phase1_browser_states.json` defines the audited first-run,
cached-complete, partial, stale, repair-receipt, batch-ready, and Song tools
states. `tests/test_plugin_contract.py` locks the fixture set, structural order,
progressive disclosure, terminology, rollback flag, receipt scope, primary
preview action, and accessibility semantics.

## One-release rollback

Add `?libraryDoctorLayout=legacy` to the local FeedBack URL to enable the
temporary high-density layout. The flag opens scan options, additional filters,
and scan details while leaving domain logic unchanged. A development harness
can alternatively set `window.__LIBRARY_DOCTOR_LEGACY_LAYOUT__ = true` before
the plugin screen enters.

This flag is intended for one release and can be deleted independently with
the Phase 1 presentation layer if a hidden expert state regresses.

## Verification

- `360 passed` in the full Python suite.
- Total coverage: `85.37%` (required floor: `85%`).
- Phase 1 plugin contract: `24 passed`.
- Ruff: passed.
- `node --check screen.js`: passed.
- Python compilation for production modules: passed.
- `git diff --check`: passed.
- Isolated nightly runtime loaded plugin `0.36.0` from the development junction.
- Read-only runtime smoke covered cached complete, collapsed/open scan options,
  cache-bypass reachability, Library check/Song tools switching, scoped
  activity, inline song selection, listen-first Preview Creator ordering, and
  the legacy-layout rollback flag.
- No Library Doctor console errors were observed. Browser-only audio/microphone
  warnings and an unrelated bundled metronome syntax error were outside plugin
  scope.

No scan, repair, batch, Undo, finalization, or preview-write action was run on
the user's real library during Phase 1 verification.

## Completion assessment

- The recommended first-run scan is visible without opening configuration.
- Scan safety is stated in the lead and explained in one expandable note.
- Completed results expose one outcome headline and one obvious review order.
- Every previous expert action remains reachable through progressive details.
- Cached complete, partial, stale, repair receipt, batch-ready, and Song tools
  states have explicit fixtures and structural contracts.
- The runtime accessibility tree reports state-appropriate pressed workspace
  buttons, an outcome region, a results region, and adjacent selected-song
  details.

The existing runtime data exercised cached-complete rather than a genuinely
empty first-run library. First-run behavior is therefore covered by the fixture
and contract suite; a timed novice study remains product validation rather than
a code-gate claim.
