Library Doctor checks a FeedBack song library, explains problems in plain
language, and offers limited repairs when a change can be made without musical
guesswork. Version 0.46.0 adds new repairs and batch improvements since the
v0.45.0 Public Beta released on August 20, 2026.

> **Public beta:** Keep the original download or another backup of important
> songs. Scans never change a song; repairs require a separate preview and
> confirmation.

## Download and install

1. Close FeedBack.
2. Under **Assets** below, download
   `feedBack-plugin-library-doctor-0.46.0.zip`.
3. Extract the ZIP into `%APPDATA%\feedback-desktop\plugins`.
4. Confirm the resulting layout contains
   `...\plugins\feedBack-plugin-library-doctor\plugin.json`.
5. Keep only one installed Library Doctor folder, then start FeedBack.

Use the named release asset for this version. The repository's **Code → Download
ZIP** archive can contain newer work. Library Doctor needs a compatible current
FeedBack Desktop build; update FeedBack if it reports an incompatible plugin.

**After updating, rescan your songs.** New findings and current repair
eligibility must replace older saved results before the new actions appear.
Updating the plugin does not remove scan history, retained backups, or Undo.

## New repairs and clearer findings

- **Readable lyric phrases:** scan results can offer a repair for excessive
  lyric blocks. It adds only `+` phrase endings at safe whole-word boundaries,
  preserving every cue's visible text, order, start time, duration, and other
  data. It handles primary and additional lyric tracks, keeps normal authored lines and
  active overlaps, and explains when malformed or indivisible data needs manual
  review. Preview shows the added endings, and normal backup, full candidate
  validation, and exact Undo apply. It does not retime words or fix source sync.
- **Terminal beat tails:** detect redundant ending data across all declared
  arrangement and song grids. Repair is offered only with current package-wide
  approval, either from an existing matching clean grid or exact redundant
  suffix evidence. Unknown references, conflicting data, and ambiguous cases
  remain manual. Retained timing data and musical events are preserved.
- **Repeated measure markers:** repair a strict repeated-positive-marker
  pattern across all declared beat copies together, preserving beat timestamps.
  Invalid or ambiguous measure-number progression is also reported separately.
- **Imported mute and bend data:** normalize corroborated fret-127 string-mute
  sentinels and unambiguous absolute bend timestamps across difficulty copies.
  Ambiguous cases remain unchanged.

The original-PSARC bend recovery tools, their folder batches, and saved-review
rechecks have been removed. For missing or ambiguous bend curves, use an updated
converter or review the original chart. Normal scanning, safe repairs, Song
Tools, preview creation, and existing repair history and Undo remain available.

## Batch repairs and audio previews

- Independent packages can be prepared and validated concurrently under a
  conservative CPU, memory, workload, drive, and free-space policy. Package
  replacement, backup and recovery records remain coordinated one at a time.
- **Automatic** and **Custom maximum** worker controls keep hardware safety
  limits in force. Cancellation preserves completed repairs and leaves
  uncommitted source packages unchanged.
- Preview repair can use a plugin-owned portable FFmpeg fallback after checking
  the selected executable. Independent preview preparations can overlap.
- Batch outcome filters no longer reset scan-result filters. Completion and
  worker shutdown handling are more reliable; expected safety refusals are
  reported separately from failures.
- Development dependencies have been updated to address reported advisories.

Normal scans and repairs do not upload songs, titles, paths, or results. Every
repair still uses a separate preview, source checks, and complete candidate
validation before replacing a song.

## Verification

<!-- Before publication, replace this comment with the checks completed for
the tagged revision and exact attached ZIP, including the tested FeedBack build
and ZIP SHA-256. Keep the checksum outside documents included in the ZIP. -->

## Need help?

Read the player guide in the
[README](https://github.com/vo90/feedBack-plugin-library-doctor#readme), or
[open an issue](https://github.com/vo90/feedBack-plugin-library-doctor/issues).
Include the Library Doctor version, FeedBack version, what you clicked, and the
exact message shown. Please do not upload copyrighted songs or a private song
library.
