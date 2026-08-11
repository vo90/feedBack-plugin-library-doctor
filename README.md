# Library Doctor

Library Doctor is FeedBack's Feedpak library validator and conservative repair
assistant. Scans are always read-only. The plugin reports problems in `.feedpak`
and legacy `.sloppak` packages. Deterministic song-data repairs show an exact
change preview; audio-preview recommendations offer both a listen-first workflow
and a confirmed automatic workflow. A separate **Song Tools** workspace provides
optional, user-requested changes for any indexed local song without requiring a
scan finding.

The first scan validates every package. Later scans reuse cached reports only
when package identity, file metadata, and sampled content still match. This
helps catch changes even when a tool preserves a file's size and timestamp.
Validation runs in a background coordinator and, when enough uncached packages
need checking, a bounded pool of read-only worker processes. Automatic worker
selection considers physical CPU cores, available memory, FeedBack's global
scan-worker ceiling, the pending package count, and the operating-system limit.
Small and fully cached scans stay single-worker. An advanced custom maximum is
available, but remains a ceiling rather than forcing unsafe parallelism. Both
scan modes automatically pause all workers while a song session is open and
resume after the player is closed, so gameplay always has priority.
Every uncached package is validated in an isolated worker with a bounded
active-time deadline. A worker that stops responding is terminated and
replaced; Library Doctor records a `package.validation-timeout` finding for the
affected package and continues the scan without changing the package.
Workers also have a measured per-process RSS ceiling: 768 MiB for a normal scan
and 1.5 GiB for Deep Audio. A multi-worker overage is retried with one isolated
worker so the package can be identified safely. A repeat overage records
`package.validation-memory-limit`, terminates that worker, and continues with
the remaining packages.

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
During the Phase 1 transition, append `?libraryDoctorLayout=legacy` to the local
FeedBack URL to reopen the temporary high-density layout for rollback testing.

## Use

1. Open **Library Doctor** from FeedBack's plugin navigation. **Library check** is
   the diagnostic and repair workspace; **Song tools** is for optional changes
   to a song you choose.
2. Select **Scan my library** for the recommended read-only check. Open **Scan
   options** when you need **All songs**, **A folder**, or **One package**. Folder
   scans include all Feedpaks and Sloppaks in their subfolders. Selected paths
   must be inside FeedBack's configured song library.
3. Start the scan. You can leave the screen while it works or cancel the scan;
   completed reports are kept and the dashboard clearly marks the result as
   incomplete. Enable **Deep audio checks** after a normal scan when you also
   want supported Ogg container and duration validation; it reads substantially
   more data, can take a long time for a large library, and is therefore off by
   default. If you open a song, the scan pauses without losing progress and
   resumes automatically when you leave the player. **Scan performance** uses
   an automatic worker count by default and shows the chosen count in progress
   and scan provenance. Its custom maximum is for troubleshooting or deliberate
   tuning; it cannot override CPU, memory, task, or platform safety limits.
4. Review **Needs fixing** first, followed by **May affect FeedBack** and
   **Optional improvements**. Additional coverage filters, rule aggregation,
   provenance, and exports remain under expandable details. Every finding separates the actual
   data problem, what a player may notice in FeedBack, and why fixing it matters.
   A suggested next action follows, while the expandable technical details retain
   the stable rule code, file, arrangement, time, and stored string index.
   JSON and CSV exports use the current package, search, and rule filters.
5. When **Review safe fix** appears beside a finding, open the preview to see
   exactly how many stored copies and musical positions are affected. Applying
   the repair requires a separate confirmation. Library Doctor creates
   recovery data, validates a repaired candidate, and saves it only if it
   introduces no new finding. A persistent result card confirms success or
   failure, states exactly what changed and what to expect in game, and offers
   **Undo repair** after a successful chart repair. The latest receipt stays in
   the Library check workspace under **Activity and recovery** instead of
   appearing above unrelated Song tools. Repeated findings for the same repair rule
   are grouped into one package-wide action with an arrangement/source
   breakdown. When a Feedpak has more than one distinct safe repair type,
   **Fix all safe issues** previews and applies them as one validated package
   transaction with one recovery backup and one Undo. The individual repair
   controls remain available. Findings without a deterministic repair remain
   report-only. Every newly written recovery ZIP is reopened and its original
   members are verified byte-for-byte before commit. Directory-package writes
   also keep a durable private transaction journal. If FeedBack or the computer
   stops between member writes, the next Library Doctor startup verifies the
   journal and recovery backup, then either accepts the fully committed package
   or restores the exact original members.
   Unknown external edits are never overwritten and are surfaced for manual
   recovery. Preview recommendations make **Listen and choose a preview** the
   primary action, with **Create automatically and finish** as the secondary path.
   Both generate from the full song mix using the same selection standard; listen-first review lets you hear and choose
   another starting point. A validated preview repair removes its temporary
   recovery copy automatically, so no later finalization step is required.
6. Open **Song tools** to search FeedBack's indexed local library and select any
   song, even one that has not been scanned or has no warning. **Preview
   Creator** can add a missing preview or replace an existing valid preview.
   It reuses the same source-bound generation, complete-package validation, and
   temporary-recovery transaction as preview repair; it is not a separate audio
   implementation.
7. Correct remaining source-package problems in an editor, then run the same
   target again. Only changed packages are revalidated. **Recheck without
   cache** is available when timestamps cannot be trusted or a fresh
   confirmation is wanted.

Saved reports from an older Library Doctor rule version remain readable after
an update, but are clearly marked as needing a new scan and do not expose repair
controls. This prevents historical classifications from being treated as a
current automatic-repair decision.

For larger cleanups, **Review safe repairs** sits below the affected-song results
and uses the complete current scan scope
(whole library, selected folder, or single Feedpak). It first builds a read-only
preview showing eligible packages, repair totals, and every package excluded by
a safety blocker. The scan records whether conditional handshape findings are
actually unambiguous enough for automatic repair and whether a missing or invalid
preview has a usable full-song source; findings that require author review or
lack source audio are never advertised as batch-repairable. Flagged missing,
short, and long previews can be included with
an explicit opt-in; acceptable previews are never replaced by batch repair. A
second confirmation is required before execution. Packages are then repaired
one at a time, with their own candidate validation, cache refresh, and result.
Safe song-data changes retain an individual Undo backup. Automatic previews use
temporary recovery while the candidate is validated, then remove that copy and
finish without Preview Undo. Gameplay pauses the batch between packages.
Stopping also takes effect between packages: completed repairs remain valid,
while packages not yet started remain unchanged.
During a running repair, live counters show repaired, partial, safely skipped,
failed, and generated-preview outcomes. A compact durable checkpoint is written
only between complete Feedpak transactions, at most once per minute or every
100 packages, so completed receipts survive an unexpected app or system stop
without materially slowing the batch. The completed result can be searched,
filtered by outcome, sorted by attention/change count/song/artist/path, and
browsed in a progressively rendered scrollable list instead of page-by-page.
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
repeats one explicit member of a chord, remove strictly redundant zero-length
or reversed-duration handshapes, and put out-of-order bend points and lyric
cues, beat markers, and section markers into chronological order without
removing any stored entry.
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
active arrangement-embedded beat or section grid. Exact-duplicate repairs keep
the first marker and remove only later copies whose complete stored properties
match. Ordering repairs stable-sort every otherwise-valid marker by its existing
time without changing any value; equal-time markers retain their authored
relative order. For beats, validity includes time and measure; for sections it
includes name, time, optional number, and any future properties. Markers at the
same time with different data remain present and are never chosen between or
deleted by the ordering repair.

Zero-length handshapes use a narrower safety rule because FeedBack can synthesize
a chord from an unmatched handshape onset. Library Doctor removes one only when
it is non-arpeggiated, contains no unknown properties, and exactly one chord with
the same template ID already exists at the identical time in the same event
list. The chord and all playable notes remain unchanged. An unmatched handshape,
an ambiguous chord match, an arpeggio marker, or future authoring data blocks the
repair for that arrangement file instead of being guessed at.

Reversed-duration handshapes use an even narrower form of the same safeguard.
Library Doctor removes one only when its start is valid and non-negative, its
end is strictly earlier, it contains no additional authoring data, its template
does not declare arpeggio intent, and exactly one chord with at least one
playable note has the same template ID and exact onset in the same event list.
The matching chord remains unchanged. Missing or negative timing, an unmatched
or ambiguous chord, a missing or arpeggiated template, an unplayable chord, or
future handshape properties block the repair for that arrangement file. The
plugin never invents a replacement end time or swaps the two times because the
author's intended duration cannot be proved from a reversed span.

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
less strict mass-editing path. Its read-only review reuses the completed scan's
findings, including scan-time automatic-repair eligibility, and verifies each
candidate's scan signature, instead of reopening and
fully planning every song only to repeat that work during application. After
confirmation, Library Doctor recalculates the selected repair rules from the
current bytes exactly once, immediately before candidate validation, backup,
and commit. A package that changed, no longer needs a repair, or fails this
authoritative safety planning is skipped without being changed. A failure in
one package does not prevent later packages from being attempted, and the final
receipt reports exact changes and every skipped or failed package. The latest
batch outcome is retained across restarts. Successful package rows expose their
own reviewed Undo action. The same result also offers a controlled Undo-all
workflow with preview, confirmation, gameplay pausing, safe stopping,
per-package outcomes, and current-state totals. **Finalize all remaining
repairs** provides the complementary cleanup workflow. Its read-only review
verifies every retained recovery copy and shows the storage that can be freed.
After explicit confirmation, each Feedpak and backup are verified again before
only that private recovery copy is removed. Changed or uncertain packages are
skipped, playable Feedpaks are never rewritten by finalization, and the result
reports every removed, retained, or failed copy. Finalization permanently
removes Library Doctor Undo for the related repair.

When the completed scan used Deep Audio and a repair changes only song-data
members in an archived Feedpak, Library Doctor reuses the signature-bound media
findings and coverage counters for unchanged audio. It still reparses and
validates the complete changed song data, verifies every untouched archive
member by size and CRC, and checks the source signature again before commit.
Unpacked directory packages, and repairs that create or replace audio, continue
to run fresh Deep Audio validation on the new candidate.

Library Doctor does not add a second playable Feedpak to the song library. It
builds a complete candidate beside the package, verifies every archive member,
runs the current package validation, and creates a private recovery backup
before replacing the archive at the same path (or atomically writing each changed
file in an unpacked directory package). The backup contains the original bytes
of only the package members changed by the repair, never another full copy of
the Feedpak. For chart repairs these are the affected song-data files. The
backup is stored under `library_doctor/repair_backups` in FeedBack's config
directory and is retained until the repair is undone or explicitly finalized.
**Undo repair** restores
those exact original member bytes only
when the repaired files have not subsequently changed; unrelated current package
members are preserved. Recovery is validated before it is saved. Findings that
were present in the exact original are allowed to return, because restoring that
previous state is the purpose of Undo; they are shown again in the refreshed
package report. A successful Undo removes the now-redundant backup. **Delete
Undo backup…** first verifies that the relevant package members
still exactly match the repaired state, then removes only the private recovery
copy; the playable Feedpak is not changed, but that repair can no longer be
undone from Library Doctor.

Declared Ogg previews from 20 through 35 seconds are accepted without a finding.
A shorter preview receives a recommendation unless the song itself is shorter
than 20 seconds and the preview reasonably covers it. A preview longer than 35
seconds receives the single **Preview needs replacement** recommendation. Its
technical detail reports only the measured preview duration and accepted limit.
The decision is made from duration alone. This duration check is part of the
normal scan when the preview is within the scanner's media safety bound. A
missing preview remains optional coverage information rather than making the
Feedpak unhealthy, but the **Without previews** view offers the same creation
controls.

Every newly generated preview targets 30 seconds, or the available full-song
length when the song is shorter. Library Doctor does not reuse the authored
preview's starting point. Its automatic selection tries usable musical cues and
then a bounded audio-energy choice, with a deterministic 25-percent fallback.
Manual review uses the same proposed excerpt but lets the user listen and choose
another start. Both paths render an Ogg excerpt with short fades and validate the
complete candidate before saving it. The manual path changes nothing until the
user chooses **Keep this preview** and then **Confirm replacement and finish**.
The automatic path selects, creates, validates, and finishes the repair in one
confirmed action.

An existing dedicated preview is replaced in place. If no preview is declared,
Library Doctor adds a new preview member and manifest pointer. If a malformed
Feedpak points `preview` directly at the full song mix, Library Doctor creates a
separate preview member and redirects only the manifest pointer; it never
overwrites the gameplay audio. Charts, lyrics, artwork, and every unrelated
package member are preserved.

Preview repair remains separate from the per-song **Fix all safe issues** chart
transaction because it creates audio and follows different recovery semantics.
It can be included explicitly in **Review safe repairs** after reviewing the batch
scope and confirming that flagged previews should be generated automatically.
The read-only batch review performs no encoding; generation happens for one
Feedpak at a time during the confirmed run. During replacement, temporary
recovery contains the original preview and any manifest state required to
protect the transaction. During creation, it records the exact original manifest
and absence of the new member. After the candidate passes complete validation
and is committed, this temporary recovery is removed automatically. If cleanup
exceptionally fails, the result clearly reports that the preview is repaired but
cleanup remains and offers an explicit recovery-copy removal action.

Song Tools reads the selectable song list from FeedBack's public local-library
endpoint and checks Preview Creator eligibility directly against the selected
Feedpak. Its availability is therefore independent of Library Doctor's cached
scan scope and result filters. A malformed preview reference or an ambiguous or
missing Ogg full mix is shown as a clear blocker rather than being guessed at.

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
- Invalid, empty, or out-of-range lyric timelines, genuinely blank lyric text,
  plus lyric tracks with implausibly long uninterrupted lines based on timed
  syllables, visible characters, or duration. Authored line endings use a
  trailing `+`; standalone `+` and `-` control markers are valid and are not
  reported as empty text. The check also honors FeedBack's automatic break
  after gaps longer than four seconds.
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
  limits, strings without tuning entries, unsupported negative frets, negative
  sustains, and sustains that run beyond the song duration. Every negative note
  fret is reported: notes carrying the exact pitchless string-mute marker
  `mt: true` receive a repairable warning and can be normalized to fret 0;
  every other negative note fret remains an error requiring author review.
  Compatibility limits are warnings when the Feedpak format itself permits the
  value.
- Guitar/bass capo and tuning declarations beyond the current highway's fret or
  string capacity.
- Slides that are ambiguous, invalid, cannot animate, start open, or leave the
  current highway; negative, malformed, or inconsistent bend data; non-boolean
  technique flags; and mutually exclusive technique pairs. An isolated slide
  marker that targets its starting fret is an authoring-review suggestion, not
  a definite fault. Same-fret markers participating in a linked slide passage
  or a partial chord slide are recognized and left unreported.
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
- Declared Ogg previews outside the accepted 20-to-35-second window, with a
  short-song exception when the available preview reasonably covers the song.
  Missing previews remain optional coverage information with an actionable
  recommendation in the **Without previews** view.
- Cover files whose image header is unreadable, whose image type is unsupported
  by FeedBack, or whose filename extension makes FeedBack serve the wrong media
  type.
- Ogg preview duration is inspected without decoding audio. Preview findings are
  based only on the accepted duration policy, not comparisons with the full mix.
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
offset judgments are not part of the scan. Normal scans inspect a declared Ogg
preview within the bounded media limit so its duration policy can be checked.
Deep audio mode reads every supported, bounded declared Ogg container but still
does not decode samples or judge whether the tab is synchronized to what is
being played. Audio samples are decoded only on demand while proposing a preview
repair, never as part of a library scan.

The report cache is local to FeedBack's config directory at
`library_doctor/library_doctor.db`. It stores package-relative paths and scan
results. A targeted scan changes the visible dashboard scope without discarding
cached reports for the rest of the library. Library Doctor does not contribute
the database or song identities to FeedBack support bundles. Its diagnostic
callable contributes only bounded aggregate state and recovery counts; the
support-log adapter removes package identities, local paths, exception text,
and tracebacks before the host can collect them.

Each scan also records its target, profile, expected and completed package
counts, outcome, and discovery errors. Interrupted, cancelled, or partially
unreadable scopes remain visibly incomplete after a restart. Findings include
structured evidence and a conservative repair classification. Scanning remains
strictly read-only; package writes are available only through the separately
confirmed safe-repair workflow.

Completed scans and batch-repair receipts also retain aggregate phase timings.
They separate discovery, signatures, validation, cache work, song-data repair,
preview repair, and checkpoint work without recording additional song identity
data. These diagnostics make performance changes measurable while keeping the
normal in-game workflow focused on package outcomes.

## Architecture

- `validator.py` contains the package reader and validation rules. It has no
  server or UI state and can be tested directly.
- `repair_eligibility.py` contains the pure conditional handshape and preview
  source predicates shared by scanning and transactional repair planning.
- `scanner.py` owns the playback-aware background scan, incremental SQLite
  cache, corruption quarantine, bounded lock handling, cancellation,
  pagination, rule summaries, and report exports.
- `library_doctor_scan_worker.py` is the spawn-safe, side-effect-free process
  worker. It can only read and validate a package; SQLite and every file change
  remain in the parent process.
- `repair.py` owns the small repair allowlist, source-bound previews, candidate
  construction, full archive-integrity and validation gates, recovery backups,
  bounded repair receipts, undo, and transactional package writes.
- `preview_repair.py` generates bounded, listenable Ogg candidates in private
  temporary storage. It never writes to the song library; `repair.py` remains
  the only package transaction and recovery authority.
- `migration.py` performs the one-time, fail-closed move from the retired
  pre-0.15 identity while preserving scan history and recovery artifacts.
- `privacy.py` is the support-log boundary. It replaces package identities with
  per-session opaque tokens and removes local paths, exception text, and
  tracebacks before the host logger receives a record.
- `diagnostics.py` is the support-bundle callable. It reports only bounded,
  identity-free operational counts and state-file readability.
- `api_contracts.py` declares the additive response and strict mutation-request
  shapes plus the uniform structured error envelope locked by the
  characterization suite.
- `mutation_receipts.py` owns the bounded durable idempotency ledger. Apply,
  automatic preview, Fix all, Undo, and recovery finalization accept a
  `request_id` or matching `Idempotency-Key`; completed outcomes can be read at
  `GET /repair/receipt/{request_id}` and safely replayed after a lost response.
- `routes.py` exposes the scanner through plugin-scoped FastAPI routes and uses
  `context["load_sibling"]` for backend modules. Every route failure uses
  `{code, message, file_state, retryable, next_action}`. Standalone Apply,
  Undo, and Finalize share the same exclusive mutation reservation.
- `screen.html`, the thin native-module `screen.js` entry, `src/`, and
  `assets/library-doctor.css` provide the in-game interface. The plugin needs
  FeedBack's module-capable `0.3.0-alpha.1` nightly (commit `950e348` or newer),
  not the older tag with the same version text. `host-contract.json` and
  `tools/verify_host_contract.py` make that capability floor executable.

The vendored schemas in `schemas/` come from the authoritative
[`got-feedback/feedpak-spec`](https://github.com/got-feedback/feedpak-spec)
repository. Their pinned revision and license are documented in
`schemas/UPSTREAM.md`.

## Develop

The plugin targets Python 3.12 and the documented FeedBack plugin API. Install
the test dependencies and run:

```bash
python -m pip install -r requirements-test.txt
python -m pip check
python -m pip_audit -r requirements.txt
python -m ruff check validator.py scanner.py library_doctor_scan_worker.py repair.py repair_eligibility.py preview_repair.py batch_repair.py migration.py privacy.py diagnostics.py api_contracts.py mutation_receipts.py routes.py tools tests
python -m pytest --cov --cov-report=term
python -m py_compile validator.py scanner.py library_doctor_scan_worker.py repair.py repair_eligibility.py preview_repair.py batch_repair.py migration.py privacy.py diagnostics.py api_contracts.py mutation_receipts.py routes.py tools/verify_host_contract.py
npm ci
npm run audit:dependencies
npm run check:frontend
npm run lint:frontend
npm run test:frontend
npm run test:browser:list
```

The frontend suite imports the real `src/` graph in a small synthetic DOM. The
nightly browser suite requires FeedBack nightly to be running, but intercepts
every Library Doctor data and mutation route so it cannot scan or change the
configured song library. Its stateful synthetic journey exercises the complete
scan, repair, and Undo browser flow without touching a real Feedpak:

```bash
npx playwright install chromium
npm run test:browser
```

Set `FEEDBACK_NIGHTLY_URL` when the host is not available at
`http://127.0.0.1:18000`.

Verify a candidate minimum or latest FeedBack checkout without changing it:

```bash
python tools/verify_host_contract.py /path/to/feedBack
```

The full accessibility certification matrix and manual assistive-technology
journeys are in `docs/accessibility-certification-2026-08-11.md`. Scanner and
adversarial-corpus limits are defined in
`docs/performance-and-fuzz-budgets.md`. Pytest keeps temporary files and its
cache in ignored repository-local `.test-artifacts/` and `.test-cache/`
directories, so a clean checkout does not depend on the system temp directory.
The versioned release-candidate ledger and remaining human procedures are in
`release-signoff.json` and `docs/release-signoff-0.43.0.md`.

`library_doctor` is the plugin ID and API namespace from version 0.15 onward.
Upgrading from an earlier release moves the previous local cache and recovery
data automatically before Library Doctor opens it. If both old and new data
folders exist, startup stops safely so neither copy is overwritten.
