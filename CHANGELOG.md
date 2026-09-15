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

- Report invalid or ambiguous measure-number progression without changing the
  chart, including non-1 starts, skips, regressions, unsupported markers, and
  repeated downbeats that do not meet the existing Safe Fix requirements.

- Prepare and validate independent Feedpak repairs concurrently, using a
  conservative CPU, memory, workload, drive-type, and free-space worker policy. Package
  commits, Undo backups, recovery journals, and result ordering remain
  coordinated one at a time.
- Add Automatic and Custom maximum repair-worker controls. Custom values remain
  safety ceilings and cannot override the runtime's hardware limits.
- Add a plugin-owned portable FFmpeg fallback for preview repair, verify the
  selected executable before use, and allow preview preparation for independent
  Feedpaks to overlap.
- Reserve bounded termination and kill-observation time when validation workers
  stop, avoiding false repair-backend quarantine on Linux.

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
