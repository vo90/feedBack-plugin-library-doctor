# Changelog

## Unreleased

- Report invalid or ambiguous measure-number progression without changing the
  chart, including non-1 starts, skips, regressions, unsupported markers, and
  repeated downbeats that do not meet the existing Safe Fix requirements.
- Add the origin-agnostic `timeline.repeated-measure-markers` Safe Fix for the
  strict repeated-positive-marker pattern produced by older FeedForge versions
  and equivalent sources. It repairs all declared beat copies atomically while
  preserving beat timing, array shape, and all unrelated data.

## 0.45.0 — Public beta

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
