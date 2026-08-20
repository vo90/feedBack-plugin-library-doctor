Library Doctor checks a FeedBack song library, explains problems in plain
language, and offers carefully limited repairs when it can make a change
without guessing.

> **Public beta:** This release has passed the automated and installed-plugin
> checks described below, but it is the first release intended for wider use.
> Keep the original download or another backup of important songs.

## Download and install

1. Close FeedBack.
2. Under **Assets** below, download
   `feedBack-plugin-library-doctor-0.45.0.zip`.
3. Extract the ZIP into `%APPDATA%\feedback-desktop\plugins`.
4. Confirm the resulting layout contains
   `...\plugins\feedBack-plugin-library-doctor\plugin.json`.
5. Start FeedBack and confirm **Library Doctor** appears in Plugin Manager.

The attached ZIP is the exact tested build and is recommended for most
players. The repository's **Code → Download ZIP** archive is also installable,
but can contain newer work that is not part of this release.

Library Doctor needs a current FeedBack Desktop build. If FeedBack reports that
the plugin is incompatible, update FeedBack before using it.

## What you can do

- Scan your whole library, a folder, or one Feedpak without changing songs.
- Read what was found, what you may notice in FeedBack, and what to do next.
- Preview and confirm narrowly safe fixes for supported data problems.
- Undo supported repairs while their recovery copy is retained.
- Review supported HO/PO questions yourself with focused Player Review tools.
- Add or replace song previews through Song Tools.
- Review and apply eligible repairs across several songs in a controlled batch.

Library Doctor does not guess at musical intent. Findings that need an author
or player decision remain report-only unless a dedicated review workflow is
available.

## Safety and privacy

- Scanning is read-only.
- A repair is separately previewed and confirmed.
- Recovery data is created and checked before a supported song-data change.
- The complete repaired song must pass validation before it replaces the
  current version.
- Changed or uncertain packages are skipped instead of overwritten.
- Normal scans and repairs do not upload songs, titles, paths, or results.

## Highlights since v0.34.0

- A simpler first-run dashboard and result workflow.
- Clearer player-facing explanations and next steps.
- Durable, visible Undo and recovery handling.
- Safer directory-package transactions and interrupted-repair reconciliation.
- Focused Player Review for supported HO/PO decisions.
- Reviewed multi-song repair, cancellation, Undo, and finalization.
- Deep Audio checks, external scan targets, Song Tools, and preview creation.
- A deterministic Git-free release ZIP and installed-ZIP browser verification.

## Verification

- 565 Python tests passed with 85.89% coverage.
- 89 frontend tests passed.
- 9 installed-plugin browser journeys passed against the current FeedBack
  nightly host, including repair/Undo and accessibility journeys.
- Python and Node dependency audits reported no known vulnerabilities.
- The minimum and current FeedBack capability contracts passed.

ZIP SHA-256: `19fce239113fd7599a53e8185a8025ec9f1a6f560fce804514fbe093af5365a7`

## Need help?

Read the player guide in the
[README](https://github.com/vo90/feedBack-plugin-library-doctor#readme), or
[open an issue](https://github.com/vo90/feedBack-plugin-library-doctor/issues).
Include the Library Doctor version, FeedBack version, what you clicked, and the
exact message shown on screen. Please do not upload copyrighted songs or a
private song library.
