# Library Doctor

Library Doctor is FeedBack's Feedpak library validator and conservative repair
assistant. Scans are always read-only. The plugin reports problems in `.feedpak`
and legacy `.sloppak` packages and offers an explicit preview only for repairs
that can be performed without choosing between different musical data.

The first scan validates every package. Later scans reuse cached reports only
when package identity, file metadata, and sampled content still match. This
helps catch changes even when a tool preserves a file's size and timestamp.
Validation runs in a background thread. Both scan modes automatically pause
while a song session is open and resume after the player is closed, so gameplay
always has priority.

## Install

Library Doctor is designed to remain an optional plugin. To install it in the
FeedBack desktop app:

1. Open **Plugins → Plugin Manager → Install Plugin**.
2. Paste `https://github.com/vo90/feedBack-plugin-library-doctor.git`.
3. Select **Install**, then restart FeedBack when prompted.
4. Enable the Library Doctor pedal in Plugin Manager if it is switched off.

The Plugin Manager uses Git to download and update plugins, so `git` must be
available on the computer's `PATH`. FeedBack installs Library Doctor's small
Python runtime dependency from `requirements.txt` during plugin startup.

For local development, put this repository directly under FeedBack Desktop's
user-plugins directory (or point `SLOPSMITH_PLUGINS_DIR` at its parent
directory), then restart FeedBack. Do not copy its files into FeedBack core.

## Use

1. Open **Library Doctor** from FeedBack's plugin navigation.
2. Choose **Whole library**, **Selected folder**, or **Single Feedpak**. Folder
   scans include all Feedpaks and Sloppaks in their subfolders. Selected paths
   must be inside FeedBack's configured song library.
3. Start the scan. You can leave the screen while it works or cancel the scan;
   completed reports are kept and the dashboard clearly marks the result as
   incomplete. Enable **Deep audio checks** after a normal scan when you also
   want supported Ogg container and duration validation; it reads substantially
   more data, can take a long time for a large library, and is therefore off by
   default. If you open a song, the scan pauses without losing progress and
   resumes automatically when you leave the player.
4. Review **Needs attention** first. The rule summary shows how widespread each
   issue is and can filter the package list. Every finding separates the actual
   data problem, what a player may notice in FeedBack, and why fixing it matters.
   A suggested next action follows, while the expandable technical details retain
   the stable rule code, file, arrangement, time, and stored string index.
   JSON and CSV exports use the current package, search, and rule filters.
5. When **Review safe fix** appears beside a finding, open the preview to see
   exactly how many stored copies and musical positions are affected. Applying
   the repair requires a separate confirmation. Library Doctor creates a
   recovery backup, validates a repaired candidate, and saves it only if it
   introduces no new finding. A persistent result card confirms success or
   failure, states exactly what changed and what to expect in game, and offers
   Undo after a successful repair. Repeated findings for the same repair rule
   are grouped into one package-wide action with an arrangement/source
   breakdown. When a Feedpak has more than one distinct safe repair type,
   **Fix all safe issues** previews and applies them as one validated package
   transaction with one recovery backup and one Undo. The individual repair
   controls remain available. Findings without a deterministic repair remain
   report-only.
6. Correct remaining source-package problems in an editor, then run the same
   target again. Only changed packages are revalidated. **Recheck without
   cache** is available when timestamps cannot be trusted or a fresh
   confirmation is wanted.

For larger cleanups, **Safe batch repair** uses the complete current scan scope
(whole library, selected folder, or single Feedpak). It first builds a read-only
preview showing eligible packages, repair totals, and every package excluded by
a safety blocker. A second confirmation is required before execution. Packages
are then repaired one at a time, so each receives its own candidate validation,
recovery backup, cache refresh, result, and Undo path. Gameplay pauses the batch
between packages. Stopping also takes effect between packages: completed repairs
remain valid and recoverable, while packages not yet started remain unchanged.
The completed result distinguishes repairs that are still active from originals
that have since been restored. **Review Undo all remaining repairs** first checks
every retained backup and current repaired song-data file without changing anything. A
second confirmation restores eligible Feedpaks one at a time. Packages changed
after repair or backed by an unreadable recovery file are excluded rather than
overwritten; already-restored packages are not attempted again.

Errors indicate invalid data or a contradiction that cannot work as authored.
Warnings identify data that FeedBack may repair, omit, or display incorrectly.
**FeedBack compatibility** identifies format-valid data that exceeds a current
game or 3D-highway capability.
**Authoring review** is a separate, lower-confidence tier for technically valid
tab data that may be difficult or impossible to play; unusual intentional
authoring is not labelled as broken. “No lyrics”, “No preview”, and “Partial
deep audio” are coverage filters only; they do not make a package unhealthy.

Summary cards can overlap because a package may, for example, both have an error
and omit optional lyrics. "No issues found by current checks" means exactly
that; it is not a guarantee that every possible musical or authoring problem is
absent.

## Safe repairs

The repair allowlist removes exact duplicate standalone notes, members inside
one chord, complete chord events, anchors, handshapes, beat markers, section
markers, and drum hits. It can also remove a standalone note that exactly
repeats one explicit member of a chord and put out-of-order bend points and
lyric cues into chronological order without removing any bend point or lyric cue.
Ordinary duplicates keep the first entry and delete only later copies whose
complete JSON properties and values match within the same event array or chord.
For a note that repeats a chord member, the complete chord is preserved and the
standalone copy is removed only when its time and every stored note property
match one unambiguous chord member. Root events and each mastery-level event
list remain separate. Events with different sustain, fret, velocity, technique,
time span, or other data are never selected automatically.
For bend curves, the existing point timestamps are stable-sorted; every point,
unknown future property, and the authored order between equal-time points is
preserved. A curve that also contains an invalid point is blocked rather than
guessed at.
For lyrics, the complete cue list is stable-sorted by its existing start times;
every word, duration, unknown future property, and the authored order between
equal-time cues is preserved. A timeline that also contains an invalid cue is
blocked rather than guessed at. The primary lyrics and additional lyric tracks
declared by the manifest use the same safeguards.
For beats and sections, the complete song-wide timeline sidecar is repaired when
it is the active FeedBack source. Otherwise, Library Doctor follows FeedBack's
legacy fallback independently for each timeline type and repairs only the first
active arrangement-embedded beat or section grid. It keeps the first marker and
removes only later copies whose complete stored properties match. For beats this
includes time and measure; for sections it includes name, time, optional number,
and any future properties. Markers at the same time with different data remain
unchanged and visible for manual review.

Zero-length handshapes use a narrower safety rule because FeedBack can synthesize
a chord from an unmatched handshape onset. Library Doctor removes one only when
it is non-arpeggiated, contains no unknown properties, and exactly one chord with
the same template ID already exists at the identical time in the same event
list. The chord and all playable notes remain unchanged. An unmatched handshape,
an ambiguous chord match, an arpeggio marker, or future authoring data blocks the
repair for that arrangement file instead of being guessed at.

A preview is bound to the exact current source bytes and validator version. If
the package changes before confirmation, Library Doctor refuses the stale plan.
JSONC arrangement files are left unchanged until a comment-preserving writer is
available. Repairs cannot run during a library scan or while the song player
has priority.

**Fix all safe issues** recalculates each eligible repair against the result of
the previous step, using a fixed dependency order. The plugin then builds and
validates one complete candidate, creates one backup, and saves once. If any
referenced song-data file cannot be prepared safely, the combined repair is blocked
and no partial set of fixes is applied. This button covers only the explicit
deterministic allowlist above; warnings that require musical judgment remain
unchanged.

Batch repair orchestrates this same per-Feedpak transaction; it does not use a
less strict mass-editing path. A package that changed after the preview is
skipped safely, a failure in one package does not prevent later eligible
packages from being attempted, and blocked packages are clearly excluded before
confirmation. The latest batch outcome is retained across restarts. Successful
package rows expose their own reviewed Undo action. The same result also offers
a controlled Undo-all workflow with preview, confirmation, gameplay pausing,
safe stopping, per-package outcomes, and current-state totals.

Library Doctor does not add a second playable Feedpak to the song library. It
builds a complete candidate beside the package, verifies every archive member,
runs the current package validation, and creates a private recovery backup
before replacing the archive at the same path (or atomically writing each changed
file in an unpacked directory package). The backup
contains the original bytes of only the song-data files changed by the repair, not
another full copy of large audio and artwork assets. It is stored under
`library_doctor/repair_backups` in FeedBack's config directory and is retained
after repair. **Undo this repair** restores those exact original song-data bytes only
when the repaired files have not subsequently changed; unrelated current package
members are preserved. Recovery is validated before it is saved. Findings that
were present in the exact original are allowed to return, because restoring that
previous state is the purpose of Undo; they are shown again in the refreshed
package report. The backup remains available afterward.

## Checks

- Official Feedpak JSON Schema validation for manifests and referenced JSON or
  JSONC files.
- Safe, existing manifest pointers and readable package archives, including
  duplicate or case-colliding archive members that unpack ambiguously.
- Explicit limits for package member count, declared unpacked size, cumulative
  reads, parsed structure size, and YAML aliases. A pathological or hostile
  package produces a bounded diagnostic instead of consuming unbounded memory.
- Manifest cross-reference checks for duplicate lyric-track IDs, missing lyric
  stems, empty identifying metadata, and invalid separated-stem/full-mix
  combinations required by current Feedpak versions.
- Invalid, empty, or out-of-range lyric timelines, entries with no visible text,
  plus lyric tracks with implausibly long uninterrupted lines based on timed
  syllables, visible characters, or duration. Authored line endings use a
  trailing `+`; the check also honors FeedBack's automatic break after gaps
  longer than four seconds.
- Contradictory events on the same string at the same time; exact duplicate
  standalone notes, chord members, complete chords, anchors, and handshapes;
  standalone notes that exactly repeat an explicit chord member; conflicting
  duplicate strings inside one chord; nonidentical coincident chord events; and
  bend-curve points stored outside chronological order.
  Root events and every difficulty level are checked independently so
  alternative mastery levels are never compared with each other. Events with
  different sustain, technique, time span, or other stored properties are left
  untouched by automatic repair.
- Chart timelines that are not chronological. FeedBack's highway requires
  notes, chords, anchors, and difficulty-level event arrays in the order they
  will be played. Out-of-order phrase windows are structural warnings when
  their selected levels are empty; they become errors when any selectable
  mastery setting produces a nonchronological playable stream.
- Song-level beat and section timelines that are not chronological, repeat an
  identical marker, repeat an earlier timestamp with conflicting data, or
  contain markers significantly beyond the declared song duration. These are
  reported separately so repeated data is not mistaken for a simple sorting
  problem. A five-second allowance avoids false warnings from normal trailing
  beat grids. This covers both the preferred `song_timeline` sidecar and the
  legacy arrangement-embedded timeline that FeedBack actually selects as its
  fallback.
- Per-chart and song-level tempo/time-signature timelines that are out of order,
  significantly beyond the song, or contain conflicting values at one time.
- Timed drum, vocal-pitch, pitch-contour, key, and harmony sidecars that are out
  of order or extend outside the song, plus invalid negative performance timing,
  duplicate drum-kit IDs, identical or conflicting simultaneous drum hits, and
  empty key events that FeedBack drops while loading.
- Standard-notation identity and relationship checks: duplicate staff or
  measure identifiers, missing staff references, duplicate voices, and measure
  or beat timelines that are out of order or outside the song.
- Rig and tone relationships, including duplicate rig/block IDs, broken graph
  references, missing realization assets, optional SHA-256 mismatches, invalid
  automation timing, and tone changes that reference undeclared rigs.
- Guitar/bass notes outside the current 3D highway's 24-fret and eight-string
  limits, strings without tuning entries, negative sustains, and sustains that
  run beyond the song duration. Compatibility limits are warnings when the
  Feedpak format itself permits the value.
- Guitar/bass capo and tuning declarations beyond the current highway's fret or
  string capacity.
- Slides that are ambiguous, invalid, cannot animate, target the starting fret,
  start open, or leave the current highway; negative, malformed, or inconsistent
  bend data; non-boolean technique flags; and mutually exclusive technique pairs.
- Missing chord-template references, invisible chords, template/chord fret
  disagreements, invalid template fret/finger data, and templates that exceed
  the current highway's eight-string display limit.
- Authoring-review suggestions for a finger assigned to different positive
  frets within one chord, chord shapes spanning at least eight frets (open
  strings excluded), same-string onsets less than 10 ms apart, and sustains
  that overlap a following note by more than 50 ms without link-next or a
  matching pitched slide.
- Invalid or conflicting anchors, broken handshape spans/references, and
  anchor/handshape data outside the song duration.
- Phrase and mastery-ladder integrity: valid chronological phrase windows,
  ordered and unique difficulty levels, loadable level arrays, and events that
  belong to their phrase window.
- Explicitly declared guitar/bass arrangements that have no playable events at
  any difficulty level. Empty vocals or other non-fretted arrangements are not
  flagged.
- A preview that is byte-for-byte identical to the full-mix stem.
- Cover files whose image header is unreadable, whose image type is unsupported
  by FeedBack, or whose filename extension makes FeedBack serve the wrong media
  type.
- Suspiciously large Ogg previews are inspected without decoding audio: the
  validator detects the same encoded payload even when container headers differ,
  and warns when a different preview still runs for at least 90% of a full mix
  longer than 35 seconds.
- Optional deep audio checks validate the page structure of every declared Ogg
  stem and preview, compare the primary audio duration with the manifest, and
  identify separated stems containing the same encoded payload. Duration
  comparison is deliberately asymmetric: a stem that ends early receives the
  stricter five-second/2% allowance, while audio that runs long receives a
  ten-second/5% allowance for harmless trailing padding. Shorter and longer
  audio use separate finding codes so the required investigation is clear.

Deep Audio currently performs Ogg page/container inspection for Vorbis and Opus.
Declared audio in another format remains valid, but is counted as partial Deep
Audio coverage and can be filtered separately; oversized Ogg files skipped by
the safety bound are reported through the same coverage view.

Keyboard arrangements are not subjected to fretted-string collision rules:
their compact chart encoding can legitimately place simultaneous MIDI pitches
on the same synthetic string.

Missing lyrics and missing previews are coverage information, not faults:
both are optional in the Feedpak specification and may be intentional.

Playability thresholds are deliberately conservative and produce review
suggestions, not errors or warnings. Library Doctor does not attempt to judge
hand size, playing style, alternate tunings, musical correctness, or audio/tab
sync.

Audio decoding, subjective lyric-to-vocal alignment, and manifest-to-audio
offset judgments are not part of the scan. Normal scans limit container
inspection to previews that are at least 80% of the full mix's file size.
Deep audio mode reads declared Ogg containers but still does not decode samples
or judge whether the tab is synchronized to what is being played.

The report cache is local to FeedBack's config directory at
`library_doctor/library_doctor.db`. It stores package-relative paths and scan
results. A targeted scan changes the visible dashboard scope without discarding
cached reports for the rest of the library. Library Doctor does not contribute
the database or song identities to FeedBack support bundles.

Each scan also records its target, profile, expected and completed package
counts, outcome, and discovery errors. Interrupted, cancelled, or partially
unreadable scopes remain visibly incomplete after a restart. Findings include
structured evidence and a conservative repair classification. Scanning remains
strictly read-only; package writes are available only through the separately
confirmed safe-repair workflow.

## Architecture

- `validator.py` contains the package reader and validation rules. It has no
  server or UI state and can be tested directly.
- `scanner.py` owns the playback-aware background scan, incremental SQLite
  cache, cancellation, pagination, rule summaries, and report exports.
- `repair.py` owns the small repair allowlist, source-bound previews, candidate
  construction, full archive-integrity and validation gates, recovery backups,
  bounded repair receipts, undo, and transactional package writes.
- `migration.py` performs the one-time, fail-closed move from the retired
  pre-0.15 identity while preserving scan history and recovery artifacts.
- `routes.py` exposes the scanner through plugin-scoped FastAPI routes and uses
  `context["load_sibling"]` for backend modules.
- `screen.html`, `screen.js`, and `assets/library-doctor.css` provide the
  in-game interface.

The vendored schemas in `schemas/` come from the authoritative
[`got-feedback/feedpak-spec`](https://github.com/got-feedback/feedpak-spec)
repository. Their pinned revision and license are documented in
`schemas/UPSTREAM.md`.

## Develop

The plugin targets Python 3.12 and the documented FeedBack plugin API. Install
the test dependencies and run:

```bash
python -m pip install -r requirements-test.txt
python -m ruff check validator.py scanner.py repair.py batch_repair.py migration.py routes.py tests
python -m pytest --cov --cov-report=term
python -m py_compile validator.py scanner.py repair.py batch_repair.py migration.py routes.py
node --check screen.js
```

`library_doctor` is the plugin ID and API namespace from version 0.15 onward.
Upgrading from an earlier release moves the previous local cache and recovery
data automatically before Library Doctor opens it. If both old and new data
folders exist, startup stops safely so neither copy is overwritten.
