# Library Health

Library Health is FeedBack's read-only Feedpak library validator. It scans
`.feedpak` files and legacy `.sloppak` files in the configured library, reports
problems per package, and never edits song files.

The first scan validates every package. Later scans reuse cached reports for
packages whose size and modification time have not changed. Validation runs in
a background thread so the rest of FeedBack remains usable.

## Install

Library Health is designed to remain an optional plugin. Once this repository
is published at an HTTPS Git URL, desktop users can paste that URL into
**Plugins → Plugin Manager → Install Plugin**, restart FeedBack, and enable the
Library Health pedal if necessary. FeedBack installs the small Python runtime
dependency from `requirements.txt` during plugin startup.

For local development, put this repository directly under FeedBack Desktop's
user-plugins directory (or point `SLOPSMITH_PLUGINS_DIR` at its parent
directory), then restart FeedBack. Do not copy its files into FeedBack core.

## Use

1. Open **Library Health** from FeedBack's plugin navigation.
2. Choose **Whole library**, **Selected folder**, or **Single Feedpak**. Folder
   scans include all Feedpaks and Sloppaks in their subfolders. Selected paths
   must be inside FeedBack's configured song library.
3. Start the scan. You can leave the screen while it works or cancel the scan;
   completed reports are kept.
4. Review **Needs attention** first. Expand a package to see the rule, file,
   arrangement, time, and string index when those details are available.
5. Correct the source package in an editor, then run the same target again.
   Only changed packages are revalidated. **Recheck without cache** is available
   when timestamps cannot be trusted or a fresh confirmation is wanted.

Errors indicate invalid data or a contradiction that cannot work as authored.
Warnings identify suspicious data that can still be intentional. “No lyrics”
and “No preview” are coverage filters only, because both features are optional.

## Checks

- Official Feedpak JSON Schema validation for manifests and referenced JSON or
  JSONC files.
- Safe, existing manifest pointers and readable package archives.
- Invalid, empty, or out-of-range lyric timelines, plus lyric tracks with an
  uninterrupted line longer than 80 timed syllables. Authored line endings use
  a trailing `+`; the check also honors FeedBack's automatic break after gaps
  longer than four seconds.
- Contradictory events on the same string at the same time, duplicate
  standalone notes, duplicate strings inside one chord, and coincident chord
  events. A standalone note matching a chord member is accepted because that
  dual representation is normal in many converted arrangements.
- Chart timelines that are not chronological. FeedBack's highway requires
  notes, chords, anchors, phrases, and difficulty-level event arrays in the
  order they will be played.
- Guitar/bass notes outside the current 3D highway's 24-fret and eight-string
  limits, strings without tuning entries, negative sustains, and sustains that
  run beyond the song duration. Compatibility limits are warnings when the
  Feedpak format itself permits the value.
- Slides that are ambiguous, cannot animate, target the starting fret, start
  open, or leave the current highway; malformed or inconsistent bend curves;
  non-boolean technique flags; and mutually exclusive technique pairs.
- Missing chord-template references, invisible chords, template/chord fret
  disagreements, and invalid template fret/finger data.
- Invalid or conflicting anchors, broken handshape spans/references, and
  anchor/handshape data outside the song duration.
- Phrase and mastery-ladder integrity: valid chronological phrase windows,
  ordered and unique difficulty levels, loadable level arrays, and events that
  belong to their phrase window.
- Explicitly declared guitar/bass arrangements that have no playable events at
  any difficulty level. Empty vocals or other non-fretted arrangements are not
  flagged.
- A preview that is byte-for-byte identical to the full-mix stem.
- Suspiciously large Ogg previews are inspected without decoding audio: the
  validator detects the same encoded payload even when container headers differ,
  and warns when a different preview still runs for at least 90% of a full mix
  longer than 35 seconds.

Keyboard arrangements are not subjected to fretted-string collision rules:
their compact chart encoding can legitimately place simultaneous MIDI pitches
on the same synthetic string.

Missing lyrics and missing previews are coverage information, not faults:
both are optional in the Feedpak specification and may be intentional.

Audio decoding, subjective lyric-to-vocal alignment, and manifest-to-audio
offset judgments are not part of the scan. Container inspection is limited to
previews that are at least 80% of the full mix's file size so normal
whole-library scans do not read every audio stem.

The report cache is local to FeedBack's config directory at
`library_health/library_health.db`. It stores package-relative paths and scan
results. A targeted scan changes the visible dashboard scope without discarding
cached reports for the rest of the library. Library Health does not contribute
the database or song identities to FeedBack support bundles.

## Architecture

- `validator.py` contains the package reader and validation rules. It has no
  server or UI state and can be tested directly.
- `scanner.py` owns the background scan, incremental SQLite cache, pagination,
  and cancellation.
- `routes.py` exposes the scanner through plugin-scoped FastAPI routes and uses
  `context["load_sibling"]` for both backend modules.
- `screen.html`, `screen.js`, and `assets/library-health.css` provide the
  report-only in-game interface.

The vendored schemas in `schemas/` come from the authoritative
[`got-feedback/feedpak-spec`](https://github.com/got-feedback/feedpak-spec)
repository. Their pinned revision and license are documented in
`schemas/UPSTREAM.md`.

## Develop

The plugin targets Python 3.12 and the documented FeedBack plugin API. Install
the test dependencies and run:

```bash
python -m pip install -r requirements-test.txt
python -m pytest
python -m py_compile validator.py scanner.py routes.py
node --check screen.js
```

`library_health` is the stable plugin ID and API namespace. The user-facing
name “Library Health” can change later without changing that identifier or the
cache location.
