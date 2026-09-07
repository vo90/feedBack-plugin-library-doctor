# Source recovery index isolation

## Problem and scope

The first full-library source preview indexed 3,722 of 3,736 PSARCs, then blocked every one of 5,417 bend-bearing packages before exact matching. Eight archives contain duplicate artwork names but unique chart names. Six have readable chart structure with out-of-order bend points. Neither condition should make unrelated sources unavailable.

This fix belongs only to Library Doctor. It does not edit FeedForge, core rendering, source PSARCs, or real-library songs; it does not reorder questionable curves or relax exact chart matching.

## Branch strategy

Use `fix/doctor-source-index-isolation` from the completed batch feature at `e0cd377`. Its baseline contains the latest fetched Doctor main (`d47e5ff`). After validation, merge into `integration/doctor-render-audit` and `audit/rendering-doctor-runtime`. Keep Nightly/main and the original feature branch unchanged. No push or publication.

## Phase 1 — implementation

1. Preserve archive listing/table count checks and reject duplicate source-chart names across the complete chart inventory before any selected-member filtering. Permit duplicate unselected non-chart assets. Preserve read bounds and full-archive hash guards.
2. Share the same source-document topology extraction with an explicit mode that omits bend interpretation. Use that mode for folder indexing. Malformed bend curves remain discoverable candidates.
3. Establish exact note/chord, timing, technique, tuning and difficulty correspondence before curve normalization. A matching source with invalid curves must yield a specific source-curve blocker, not disappear as a missing match. An unrelated chart in a compilation must not block a valid matching chart. A matching unsafe alternative must still prevent an unsupported uniqueness claim.
4. Keep conservative incomplete-index handling for truly unreadable or structurally ambiguous archives. Preserve existing note exclusions, package-wide arrangement requirements, transaction checks and Undo.

## Phase 2 — verification

- Regression coverage for duplicate DDS versus duplicate SNG names (including unselected charts), bounds, malformed listing counts and changed-source hashes.
- A malformed bend chart remains indexed; unrelated songs remain eligible; matching malformed sources block with a precise reason even alongside a valid competing source. Structural mismatches do not become curve blockers.
- Exercise compilation selection, full-audio/MinusMix sharing, and the existing exact Apply/Undo tests using disposable packages.
- Read-only checks against the fourteen real archives that failed the completed preview. No source modifications or point reordering.
- Run the full Python suite and relevant frontend checks. Record limitations and results below.

## Phase 3 — integration and runtime verification

Review and commit the scoped diff, merge the tested commit into the existing Doctor integration/runtime targets, and use the supported workspace launcher for restart after identity and containment checks. Preserve the completed library scan. Rerun source indexing and preview, never Apply, to report actual eligible repairs and remaining per-song blockers.

## Validation results

- Full Python suite: **816 passed, 1 skipped**, 241.82 seconds. The skip is Windows symlink privilege; the existing junction protection tests passed. One existing dependency deprecation warning.
- Targeted recovery/batch suite: **57 passed**, including mixed valid/invalid songs, both source-order permutations, and full-audio/MinusMix Apply/Undo. The full suite includes the 300-package recovery-history test.
- Frontend syntax checks and **112 frontend tests passed** using the existing feature worktree's installed dependencies. Frontend source, test files and package manifests are byte-identical between that baseline and this fix; no dependency installation or frontend modification was needed.
- Ruff and whitespace checks passed; independent production-diff review found no blocking issues.
- The fourteen previously rejected real PSARCs all index successfully: **14 archives, 43 pitched charts, zero index errors**. All source hashes are unchanged. The eight invalid-bend charts remain discoverable candidates and still fail strict curve interpretation. This bounded check covers those fourteen sources, not the whole source folder.
- Isolated real-package smoke: **3 copied packages, 679 changes, 3 successful Applies and 3 exact Undos**. Only lead arrangement JSON changed; all audio and other package members were unchanged. Original inputs were verified unchanged. The standalone smoke scanner used its single-worker fallback because its subprocess lacked the scratch code import path.
- The 95-file local development ZIP verified successfully. No publication.

The existing conservative policy still validates all native levels of an exactly matching source chart, even when the Feedpak only stores the flattened chart. This change does not relax that policy or automatically repair unordered source curves. Integration and the fresh full-folder preview are recorded separately in runtime handoff evidence.
