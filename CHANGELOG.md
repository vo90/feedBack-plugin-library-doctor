# Changelog

## Unreleased

- Remove the original-PSARC bend recovery tools, folder batches, and saved-review
  rechecks. Keep normal scans, safe repairs, Preview Creator, and existing repair
  history and Undo. Ambiguous bend data now directs users to reconversion or
  original-chart review; refresh scan results to see the updated advice.

- Normalize retained bend timestamps only when absolute timing is unambiguous.
  Keep every point value and unknown property; block pre-onset, mixed, unordered
  and exceptional curves across all difficulties instead of guessing a repair.

- Offer source-bound normalization of imported fret-127 string-mute sentinels,
  including every difficulty copy and corroborated shared chord template. Mixed
  or unpitched-flag ambiguity blocks the whole repair. Preserve timing and mute
  flags, validate the complete candidate, and retain exact Undo.

- Detect redundant terminal beat tails in every declared arrangement and song
  timeline, including copies not selected for normal playback. Offer a separate
  suffix-only repair when a clean stored grid corroborates it and references,
  package contents and event ends pass conservative checks. Preserve every
  retained beat and musical event; keep verified recovery and Undo.

- Add the origin-agnostic `timeline.repeated-measure-markers` Safe Fix for the
  strict repeated-positive-marker pattern produced by older FeedForge versions
  and equivalent sources. It repairs all declared beat copies atomically while
  preserving beat timing, array shape, and all unrelated data.

## 0.45.0 â€” Public beta

- Prevent temporary repair candidates from looking like discoverable song packages.
- Lock further changes when a song has unresolved recovery state.
- Keep every available Undo and recovery action visible under Activity and recovery.
- Simplify first-run scanning, result filters, multi-song fixes, and Player Review.
- Add a verified, allowlisted release ZIP for Git-free installation.
- Separate repair-journal recovery and batch-result rendering behind tested,
  size-bounded modules.
- Added focused Player Review for supported HO/PO decisions.
- Added safe multi-song repair preview, cancellation, Undo, and finalization flows.
- Added Deep Audio checks, external target scans, Song tools, and preview creation.
