# Safe structural repairs implementation report

Date: 2026-08-15  
Branch: `main`  
Publication status: local working tree only; no commit or push was performed.

## Outcome

Library Doctor now diagnoses, previews, applies, validates, backs up, restores,
and reports eight additional conservative structural repairs:

| Rule | Automatic action |
| --- | --- |
| `chart.empty-phrases-key` | `omit_empty_phrases_key` |
| `timeline.empty-arrangement-tempos-key` | `omit_empty_arrangement_tempos_key` |
| `timeline.duplicate-tempo` | `remove_exact_duplicate_tempo_events` |
| `timeline.tempos-out-of-order` | `reorder_tempo_events` |
| `timeline.duplicate-time-signature` | `remove_exact_duplicate_time_signature_events` |
| `timeline.time-signatures-out-of-order` | `reorder_time_signature_events` |
| `tones.duplicate-change` | `remove_exact_duplicate_tone_changes` |
| `tones.changes-out-of-order` | `reorder_tone_changes` |

The validator version is now `rules-29` and the repair catalog version is now
`repairs-19`. The automatic-safe song-data catalog contains 24 actions.

## Safety and source behavior

- Empty-key repairs delete only a present root `phrases: []` or arrangement
  `tempos: []`. They report zero removed musical events and zero musical
  positions.
- Duplicate repairs require a fully valid stream and compare complete canonical
  JSON, including unknown properties and stored scalar types. The first exact
  object is retained. Same-time objects with any different data remain.
- Ordering repairs require every list member to be valid, use a stable time
  sort, preserve equal-time order, and retain every complete object.
- Tempo repair considers all nonempty per-arrangement overrides and the declared
  song-timeline tempo stream concurrently.
- Time-signature repair uses only the declared song-timeline sidecar and does
  not require complete beat or section arrays.
- A nonempty manifest tone block overrides inline tones, matching FeedBack.
  Manifest-backed tone issues are diagnostic/manual until a lossless YAML
  writer exists. An empty manifest tone object falls back to inline JSON.
- JSONC findings remain diagnostic/manual until a lossless JSONC writer exists.
- Malformed timed streams, oversized numeric values, stale sources, and altered
  operation payloads fail closed without a partial write or backup.
- Fix all safe performs omission before exact deduplication and exact
  deduplication before stable sorting. An unavailable conditional rule is
  excluded without suppressing unrelated safe repairs.

## User interface

Preview, completion, failure, Undo, and restored-state copy now describes empty
optional-key omission accurately and never claims a musical event was deleted.
Manifest-tone and JSONC blockers are visible, and unavailable findings do not
offer individual or batch automatic actions.

## Implementation-specific files

Production and documentation:

- `validator.py`
- `repair_eligibility.py`
- `repair.py`
- `src/formatters.js`
- `src/finding-view.js`
- `src/preview-controller.js`
- `src/repair-controller.js`
- `README.md`

Tests and fixtures:

- `tests/test_validator.py`
- `tests/test_repair.py`
- `tests/test_routes.py`
- `tests/frontend/state-view.test.mjs`
- `tests/fixtures/repair_catalog_coverage.json`

The generic production plumbing in `scanner.py`, `routes.py`, and
`batch_repair.py` did not need implementation changes for these rules. Other
uncommitted workspace edits that existed before this implementation were left
untouched.

## Verification evidence

- `python -m pytest`: **512 passed**
- `python -m pip check`: no broken requirements
- `python -m pip_audit -r requirements.txt`: no known vulnerabilities
- required production-module `py_compile`: passed
- focused Ruff checks: passed
- `npm ci`: passed
- `npm run audit:dependencies`: 0 vulnerabilities
- `npm run check:frontend`: passed
- `npm run lint:frontend`: passed
- `npm run test:frontend`: **34 passed**
- `npm run test:browser:list`: **8 tests discovered**
- `npm run test:browser`: **8 passed** against the running Library Doctor dev
  host with intercepted synthetic data
- `git diff --check`: passed

The combined route transaction test uses a disposable directory Feedpak and
exercises all eight rules through scan, preview, Apply, fresh validation, one
backup, and exact-byte Undo. A separate manifest-tone route test verifies that
the rule is blocked before mutation and before backup creation. No real song
package or configured song library was changed. Generated test/cache artifacts
were removed after verification.

## Deliberately deferred

- Sorting top-level notes, chords, anchors, or other broad chart streams needs
  stronger renderer and gameplay-equivalence evidence.
- Exact bend-point deduplication remains evidence-gated behind dense-curve
  renderer and grader fixtures.
- Manifest-tone automation requires a lossless YAML writer.
- JSONC automation requires a lossless comment-preserving writer.

