# Changelog

## Unreleased

- Prepare and validate independent Feedpak repairs concurrently, using a
  conservative CPU, memory, workload, drive-type, and free-space worker policy. Package
  commits, Undo backups, recovery journals, and result ordering remain
  coordinated one at a time.
- Add Automatic and Custom maximum repair-worker controls. Custom values remain
  safety ceilings and cannot override the runtime's hardware limits.
- Add a plugin-owned portable FFmpeg fallback for preview repair, verify the
  selected executable before use, and allow preview preparation for independent
  Feedpaks to overlap.
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
