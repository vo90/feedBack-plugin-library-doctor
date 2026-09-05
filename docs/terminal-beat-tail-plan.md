# Terminal duplicate beat tail

Implementation baseline: origin/main d47e5ff (verified 2026-09-05).

## Scope and evidence

Some converted packages retain one or three redundant terminal beat records.
The source SNG records can have different flags which are absent from Feedpak;
this repair makes no claim about those source flags or the author's intent.
The defect is a non-increasing stored Feedpak grid, including inactive copies
that the existing active-timeline validator misses.

## Implementation

1. Diagnose every declared beat grid without changing runtime source precedence.
2. Recognize only a strictly increasing prefix followed by one to three exact
   copies in alternating order of its last two records, starting with the
   penultimate record. Require ordinary finite numeric times and integer measures.
3. Offer a separate package repair only when an existing clean declared grid
   exactly corroborates every repaired prefix. Require known data shapes, no
   unresolved beat references, finite event/sustain ends before the tail, and
   retained coverage of the declared duration. Block unknown extensions.
4. Preview every affected copy together. Bind all corroborating inputs to the
   package plan. Never fall back to generic sorting/deduplication for this tail.
5. Reuse verified recovery backups, candidate validation, source guards, atomic
   replacement and Undo. Preserve retained positions and every non-beat value.
6. Bump validator/catalog identities to invalidate cached reports and old plans.

## Verification

Synthetic tests: one/three-entry tails, inactive and sidecar grids, arrangement
order, strict output, unchanged other data, no-op second run, unknown fields,
references, malformed/nonfinite times, interior defects, conflicting records,
unsupported/long tails, missing corroboration, events/sustains after tail,
stale clean-grid evidence, tampered operations, and transaction failure/Undo.
Run relevant existing transaction, batch, catalog, validator and release tests,
then the full Python suite and lint. Exercise preview/apply/rescan/Undo on an
isolated copy of the investigated package; compare every member and verify the
source package hash before and after. No real library mutations, host changes,
plugin deployment, push or publication are part of this implementation.

## Completed verification

- 660 Python tests passed, including 40 focused terminal-tail cases; total
  coverage 86.09% exceeds the existing 85% gate.
- Ruff, frontend syntax checks, release packaging and archive CRC verification
  passed. The new module is present in the development ZIP.
- An isolated copy of the investigated song produced three findings and one
  package preview removing five entries: one each from lead/rhythm and three
  from vocals. All four resulting grids matched the existing clean bass grid.
- Every non-beat JSON value and every other archive member remained identical.
  The candidate rescanned healthy; a second preview offered no changes. Undo
  restored every original member's bytes. The original library file hash was
  unchanged before and after both copy-based trials.
- Scanner identity is `rules-34`; repair catalog identity is `repairs-22`.
  Plugin version remains 0.45.0 because this is an unreleased development change.
- No live playback or full Hybrid creation was performed. Verification proves
  strict stored-grid ordering and data preservation, not source SNG flag intent.

The standard Library Doctor checkout and launcher remain on the verified main
baseline. Development is isolated in `fix/terminal-beat-tail`; deployment is a
separate operation.
