# Library Doctor

> **Status: Public beta** — Library Doctor is ready for normal use, but this is
> its first wider public release. Keep the original download or another backup
> of any song you would be unhappy to lose.

Library Doctor is a free, open-source plugin that checks your FeedBack song
library. It tells you what looks wrong, what you may notice in the game, and
what you can do next—in ordinary language.

A scan never changes a song. When Library Doctor knows a repair is mechanical
and unambiguous, it lets you review the planned change before confirming it.
The plugin creates recovery data first, checks the repaired song completely,
and keeps **Undo** available when that repair supports it. If the plugin cannot
make a trustworthy choice, it explains the problem and leaves the song alone.

[Download Library Doctor](https://github.com/vo90/feedBack-plugin-library-doctor/releases)
· [Installation](#installation)
· [Your first scan](#your-first-scan)
· [Help](#getting-help)

## Is Library Doctor for me?

Library Doctor is useful if you play custom songs in FeedBack and want help
finding packages that may be broken, incomplete, or behaving strangely.

It can:

- scan your whole library, one folder, or one Feedpak;
- explain problems with charts, song metadata, lyrics, timelines, previews,
  audio declarations, and other supported Feedpak data;
- safely repair a limited set of exact duplicates, ordering mistakes, empty
  optional data, and other changes that do not require musical guesswork;
- create or replace supported song previews;
- help review certain HO/PO technique questions in **Player Review**;
- repair several eligible songs through one reviewed batch; and
- keep repair history, recovery information, and available Undo actions visible.

It deliberately does not:

- guess how a chart was supposed to be authored;
- silently change a song during a scan;
- promise an automatic fix for every finding;
- upload your songs or scan results; or
- treat “no issues found” as proof that every possible musical problem is absent.

## Before you install

- Use a current FeedBack Desktop build. The technical minimum is
  **0.3.0-alpha.1**, but older builds with the same version text may not contain
  the plugin features Library Doctor needs. If FeedBack reports an incompatible
  plugin, update FeedBack to the newest available build.
- Keep the original song downloads or your own backup. Library Doctor is
  designed to fail safely and maintains private recovery copies for supported
  repairs, but recovery data should not be the only copy of valuable songs.
- Library Doctor currently targets the normal Windows FeedBack Desktop setup.

## Installation

### Download the release ZIP — recommended for most players

1. Close FeedBack.
2. Open the
   [Library Doctor Releases page](https://github.com/vo90/feedBack-plugin-library-doctor/releases).
3. Choose the newest release labelled **Public Beta**. Under **Assets**, download
   the file named like
   <code>feedBack-plugin-library-doctor-0.45.0.zip</code>.
4. Extract the downloaded ZIP. Do not leave the plugin inside the ZIP file.
5. Press **Win+R**, enter
   <code>%APPDATA%\feedback-desktop\plugins</code>, and press Enter. Create the
   <code>plugins</code> folder if Windows says it does not exist.
6. Move the extracted <code>feedBack-plugin-library-doctor</code> folder into
   that <code>plugins</code> folder.
7. Check that <code>plugin.json</code> is directly inside the folder you moved.
   The finished layout should look like:

   <code>%APPDATA%\feedback-desktop\plugins\feedBack-plugin-library-doctor\plugin.json</code>

8. Start FeedBack. Open **Plugins → Plugin Manager** and confirm that
   **Library Doctor** appears. Restart FeedBack once if it asks you to.

GitHub's **Code → Download ZIP** option is also installable, but it downloads
the current repository rather than the exact tested release. Prefer the ZIP
listed under a release's **Assets**. If you use **Code → Download ZIP**, the
folder may end in <code>-main</code>; that is fine as long as
<code>plugin.json</code> is directly inside it.

### Install through Plugin Manager with Git

If Git is already installed on your computer:

1. Open **Plugins → Plugin Manager → Install Plugin**.
2. Paste
   <code>https://github.com/vo90/feedBack-plugin-library-doctor.git</code>.
3. Select **Install**, then restart FeedBack when prompted.

If FeedBack says Git is missing, use the release ZIP instructions above.
FeedBack installs Library Doctor's small Python dependency automatically when
the plugin starts.

## Your first scan

1. Open **Library Doctor** from FeedBack's plugin navigation.
2. Select **Scan my library**. Leave **Scan options** closed for the recommended
   first scan.
3. Let the scan finish. You may leave the Library Doctor screen while it works.
   Opening a song pauses scanning so gameplay keeps priority; scanning resumes
   after you leave the player.
4. Start with **Needs fixing**, then review **May affect FeedBack** and
   **Optional improvements**.
5. Open a song result to read:

   - what Library Doctor found;
   - what you might notice in FeedBack;
   - why it may be worth fixing; and
   - the recommended next step.

6. If **Review safe fix** is available, open it and read the preview. Nothing is
   changed until you confirm the separate repair step.
7. Run the same scan again after making repairs or editing songs elsewhere.
   Unchanged songs reuse their previous result, making later scans faster.

Start with the normal scan. **Deep audio checks** read much more audio data and
can take considerably longer, so enable them only when you want the additional
audio-container and duration checks.

Use **Scan options** when you want to check one folder or one package, or when
you deliberately want to recheck without using saved scan results.

## Understanding the results

| Result | What it means | What to do |
|---|---|---|
| **Needs fixing** | Library Doctor found invalid or contradictory data. | Review these songs first. Use a safe fix when offered, or edit/replace the source package. |
| **May affect FeedBack** | The data may be accepted but can display, play, or behave incorrectly in FeedBack. | Read the explanation and decide whether the symptom matters to you. |
| **Optional improvements** | The song may work, but Library Doctor found something worth reviewing. | Treat this as advice, not proof that the author made a mistake. |
| **No issues found** | None of Library Doctor's current checks found a problem. | No action is needed unless you still notice something wrong in game. |

One song can appear in more than one group. Technical details such as the rule
code, file, arrangement, and time remain available inside each result for
authors and bug reports.

## Repairs and review

### How a safe repair works

1. Select **Review safe fix**.
2. Read the exact planned change.
3. Cancel if anything is unclear, or confirm when it matches your intention.
4. Library Doctor creates recovery data and builds the repaired song privately.
5. The complete candidate must pass validation before it can replace the
   current song.
6. The result explains what changed and shows **Undo repair** when available.

A finding without a repair button is not a failure of the interface. It means
Library Doctor cannot make that decision without guessing. Follow the displayed
next step and use a Feedpak editor or replace the song when necessary.

### Fix several songs

After a complete scan, **Fix several songs** can collect the same narrowly safe
repairs across eligible songs. It always shows a read-only preview and asks for
confirmation before changing anything. Songs that changed, have uncertain
recovery state, or no longer match the scan are skipped rather than overwritten.

Batch repair works one song at a time. Completed repairs stay valid if you stop
the batch, while songs that were not started remain unchanged.

### Player Review

**Player Review** currently supports a focused set of HO/PO technique decisions.
It lets you listen, inspect the relevant chart position, and make the choice
yourself. Library Doctor does not decide the musical answer for you. More
review-assisted repair types may be added in future versions.

### Song Tools and previews

Open **Song tools** to search FeedBack's indexed library and select a song
directly. **Preview Creator** can add a missing preview or replace an existing
one. You can listen and choose a starting point, or explicitly select the
automatic option. Preview work uses the same private candidate validation and
recovery safeguards as suggested repairs.

## Undo and recovery

| Message or action | Meaning |
|---|---|
| **Undo repair** | Restore the exact song data saved before the repair. |
| **Finalize repair** or **Remove recovery copy** | Keep the repaired song and permanently remove Library Doctor's private Undo copy. |
| **Recovery needed** | A repair was interrupted or its result is uncertain. Library Doctor blocks further changes to that song until the state is resolved. |
| **Manual review needed** | The song was also changed outside Library Doctor, so the plugin will not choose which version to overwrite. Preserve both versions and compare them manually. |

Do not manually delete recovery files just to clear a warning. Use the actions
shown under **Activity and recovery**, or keep both versions until you can
review them safely.

## Privacy

Library Doctor reads song packages stored on your computer. Normal scans and
repairs do not upload songs, titles, paths, or results. JSON and CSV exports are
created only when you request them.

Support diagnostics contain bounded technical counts and state information.
They remove song titles, local package paths, exception text that may reveal
those paths, and song file contents.

## Troubleshooting

- **Library Doctor does not appear:** close FeedBack and check that exactly one
  folder directly under
  <code>%APPDATA%\feedback-desktop\plugins</code> contains
  <code>plugin.json</code>. Remove an accidental extra folder level, then
  restart FeedBack.
- **FeedBack says the plugin needs a newer version:** update FeedBack Desktop to
  the newest available build.
- **A Python dependency failed to install:** restart FeedBack once while
  connected to the internet. If it still fails, open FeedBack's plugin
  diagnostics and keep the displayed error for a bug report.
- **The scan seems slow:** finish a normal scan before enabling Deep audio.
  Scanning also pauses while a song is open.
- **A repair button is missing:** the finding is intentionally report-only, the
  package changed after scanning, or another recovery decision must be resolved
  first. Read the next-action text shown with the finding.
- **The displayed result seems out of date:** use **Recheck without cache** for
  that scope.

## Updating or removing Library Doctor

Close FeedBack before replacing a manually installed folder. Keep only one
Library Doctor folder in the plugins directory; two copies use the same plugin
ID and make it unclear which version FeedBack loaded.

Replacing or deleting the plugin folder does not automatically delete scan
history or retained recovery data. Resolve any visible Undo or recovery choices
before removing the plugin if you may need them later.

## Getting help

Open a
[GitHub issue](https://github.com/vo90/feedBack-plugin-library-doctor/issues)
and include:

- your Library Doctor version;
- your FeedBack version/build;
- what you were trying to do;
- the exact wording shown on screen; and
- whether retrying or rescanning changed the result.

Do not upload copyrighted songs or a private song library. A small song you
created specifically to reproduce the problem is useful only when you are
comfortable sharing it.

The remaining sections are technical reference for Feedpak authors,
contributors, and anyone who wants to inspect the safety boundaries.

<details>
<summary><strong>Technical repair catalog and validation coverage</strong></summary>

## Safe repairs

The automatic-safe song-data catalog contains 24 rule-specific repair actions.
The repair allowlist removes exact duplicate standalone notes, members inside
one chord, complete chord events, anchors, handshapes, beat markers, section
markers, and drum hits. It can also remove a standalone note that exactly
repeats one explicit member of a chord, remove strictly redundant zero-length
or reversed-duration handshapes, and put out-of-order bend points and lyric
cues, beat markers, and section markers into chronological order without
removing any stored entry. It also omits explicitly empty optional arrangement
`phrases` and `tempos` properties, removes exact duplicate tempo,
time-signature, and tone-change events, and puts otherwise-valid instances of
those event streams into chronological order.
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

An empty root arrangement `phrases: []` property is omitted because absence is
the format's representation for no phrase ladder. An empty root arrangement
`tempos: []` override is omitted so that chart continues following the song
tempo. Song-timeline empty arrays, nested properties, nonempty arrays, and
wrong-shaped values are never selected by either omission repair. These actions
remove only empty optional properties; they do not remove musical events or
positions.

Tempo repairs cover every active per-arrangement override and the declared song
timeline in the same package-wide action. Time-signature repairs cover only the
declared song timeline. Tone-change repairs cover only effective inline
arrangement JSON tone data using the canonical `t` time key. Nonempty
manifest-stored tone data requires a manual edit because Library Doctor does not
rewrite manifest YAML. Each duplicate repair keeps the first complete JSON
object and removes only later identical copies, including unknown properties.
Each ordering repair requires a fully valid list and stable-sorts existing raw
numeric times; equal-time entries keep their original order. A malformed list,
same-time differing event, or ambiguous effective source is left unchanged.

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
JSONC arrangement and song-timeline files are left unchanged until a
comment-preserving writer is available. Repairs cannot run during a library
scan or while the song player has priority. Player Review therefore stops and
releases the active transport immediately before Apply or Undo, completes the
validated transaction, and reloads the same song and review position.

### Reviewed tab repairs

Hammer-on and pull-off findings that require musical intent are deliberately
separate from every automatic-safe allowlist. **Reviewed repair** covers notes
with both flags, a lone flag opposite to the incoming same-string fret
movement, same-fret HO/PO, and HO/PO without one usable predecessor. It keeps
each runtime-selectable phrase difficulty independent, uses the arrangement
root only when there is no usable phrase ladder, and treats the next same-string
note only as evidence; long gaps do not hide a candidate. A strict authored
`linkNext` from the immediately preceding same-fret event identifies a held
continuation rather than a new HO/PO attack, so that continuation is not sent
to manual review. Independent contradictions, such as both HO and PO being
stored, remain reviewable.

**Default Manual Player Review view** defaults to **Full difficulty only** in
Scan options. The validator still records every authored phrase level in one
pass. After the scan, a separate **Manual Player Review** filter above the
affected-song list can switch between max difficulty and every authored
difficulty without rescanning or changing that saved default. The list, rule
totals, dashboard counters, text review, and Player Review follow the current
list filter. Only difficulty-sensitive manual-review findings are hidden;
automatic-safe and other findings always remain visible. For arrangements with
a usable phrase ladder, its last authored level is the full-difficulty stream;
the arrangement-root arrays are only the Player's fallback when no usable
ladder exists.

For packages inside FeedBack's configured song library, **Player Review** opens
the normal Player paused at the exact issue. **Jump to issue** returns there at
any time, while **Play preview** plays from two seconds before through two
seconds after in chart time and then returns paused to the issue. A whole-song
slider with an aligned issue marker and ±1/±0.1-second controls allow precise
navigation without replacing the normal Player controls. Dragging or holding a
control is coalesced locally: Library Doctor pauses once, performs one
authoritative seek on release, and resumes only when it owned that pause. The
timeline lives in its own overlay so the decision window stays compact. Both overlays
can be dragged independently, moved with the keyboard, remember their
positions, remain clamped to the visible screen, and provide **Reset layout**.
The author keeps their normal visualization and camera settings, can play,
pause, seek, or scrub the song, and uses the review overlay to move directly
between issues. The exact current note or chord member receives a temporary
pulsing highlight through the normal Highway note-state hook in the bundled 2D
and 3D views. A string/fret/time description remains visible for renderers that
do not support that hook. The 2D and 3D Highways (and compatible Highway
renderers) also receive a temporary chart-transform preview when a choice is
selected; the Feedpak is still unchanged. Full Tab View uses a separate GP5
rendering path, so it reflects a choice after Apply reloads the song rather
than during the temporary preview. The text-only evidence workflow remains
available as a fallback.

Player Review never weakens FeedBack's library boundary. Folder and package
scans outside the configured song library still receive the complete scan and
all automatic/standard repairs, but reviewed findings are reported as manual
and show a clear explanation instead of an actionable reviewed-repair control.

The workflow presents one bounded candidate at a time. It never preselects a
choice. Before showing an option, Library Doctor applies it to a temporary
copy, reruns the HO/PO inspection and targeted validator, and omits any no-op,
any option that leaves or recreates the issue, and any option that introduces
another finding. The complete selected group is simulated and validated again
before Apply. Depending on those outcome checks, the author can explicitly
store hammer-on, store pull-off, convert the current note to a tap, remove its
HO/PO fields, or conditionally move the marker to one unambiguous next explicit
note that has no HO, PO, or tap flag. **Skip for now** is navigation rather
than a repair: it writes nothing and leaves the issue unresolved. Conflicting
same-time frets, ambiguous predecessors, malformed technique values, stale
objects, JSONC, and ambiguous
move targets remain visible but cannot be mutated. Only `ho`, `po`, and `tp`
may change; timing, string, fret, sustain, chord data, and other techniques are
preserved.

Candidate inspection uses bounded server pages with exact package totals.
Previous/next page controls retain explicit decisions already made in the
current session, while a per-adapter decision limit prevents an unbounded
request. Candidates from later pages remain source-bound and can be previewed,
applied, and undone normally.

Reviewed choices use the same complete candidate validation, durable recovery
backup, receipt history, cache refresh, and hash-guarded Undo as conservative
chart repairs. They never enter **Fix all safe issues** or multi-song safe
batch repair. An optional on-demand 12-second full-mix excerpt can help with
timing, but it is held only in bounded temporary memory and is never added to
the Feedpak; the UI explains that a mixed recording cannot prove a particular
fretting technique. The scalable adapter registry declares candidate IDs,
pagination, decisions, blockers, mutable fields, mutation derivation, and exact
postconditions. Its executable ownership fixture requires candidate, decision,
blocker, preservation, tamper, validation, Undo, API, and frontend coverage
before another reviewed repair can be added.

**Accept & Next** records an outcome-checked choice in memory without writing
the song. **Skip for now** keeps an issue out of the current pass until the
author chooses **Review skipped issues**; skipped issues are never sent to
Preview or Apply. The author may preview and apply any partial group without finishing the entire
queue. One retained Undo checkpoint is allowed per song: reviewing and staging
may continue after Apply, but another group cannot be applied until the current
checkpoint is explicitly undone or finalized. Undo restores the exact original
changed bytes; Finalize keeps the repaired song and removes only that recovery
copy. The overlay keeps those controls visible and can return to Library Doctor
at any time.

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
repair because it creates audio and follows different recovery rules. It can be
included explicitly in **Fix several songs** after reviewing the multi-song
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
`library_doctor/library_doctor.db`. It stores package-relative paths, scan
results, and the private filesystem root needed to keep later repairs bound to
the exact selected scope. That root is never included in status responses,
exports, logs, diagnostics, or support bundles. A targeted scan changes the
visible dashboard scope without discarding
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

</details>

<details>
<summary><strong>Developer architecture and verification commands</strong></summary>

## Architecture

- `validator.py` contains the package reader and validation rules. It has no
  server or UI state and can be tested directly.
- `repair_eligibility.py` contains the pure conditional handshape, preview,
  and reviewed HO/PO classifiers shared by scanning and transactional repair
  planning.
- `reviewed_repair.py` owns the closed reviewed-repair adapter registry and
  registered decision vocabulary without performing filesystem writes.
- `scanner.py` owns the playback-aware background scan, scope resolution,
  cancellation, worker lifecycle, report enrichment, and exports.
- `library_doctor_report_cache.py` owns incremental SQLite persistence,
  corruption quarantine, bounded lock handling, scope state, pagination, and
  cached rule/summary queries.
- `library_doctor_scan_policy.py` owns the independently testable CPU, memory,
  platform, and user ceilings used to select validation worker counts.
- `library_doctor_scan_worker.py` is the spawn-safe, side-effect-free process
  worker. It can only read and validate a package; SQLite and every file change
  remain in the parent process.
- `repair.py` owns source-bound previews, candidate construction, full
  archive-integrity and validation gates, recovery backups, bounded repair
  receipts, undo, and transactional package writes.
- `repair_actions.py` owns the immutable action values shared by repair
  planning and application. It has no filesystem, validation, or service state.
- `repair_catalog.py` owns the closed declarative metadata allowlists for safe
  structural repairs and reviewed preview-media repairs.
- `repair_transaction.py` owns durable transaction-journal persistence and
  reconciles interrupted directory-package repairs before new changes begin.
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
- `route_support.py` owns FastAPI validation normalization, public error
  mapping, and browser byte-range responses without importing domain services.
- `architecture-contract.json` records backend module ownership, dependency
  direction, and regression size ceilings enforced by the test suite.
- `src/batch-controller.js` owns batch requests, confirmation, cancellation,
  and polling; `src/batch-results-view.js` renders searchable repair, Undo, and
  finalization outcomes without owning network state.
- `screen.html`, the thin native-module `screen.js` entry, `src/`, and
  `assets/library-doctor.css` provide the in-game interface. The plugin needs
  FeedBack's capability-capable `0.3.0-alpha.1` nightly (commit `05be9eb` or newer),
  not the older builds with the same version text. `host-contract.json` and
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
python -m ruff check validator.py scanner.py library_doctor_report_cache.py library_doctor_scan_policy.py library_doctor_scan_worker.py repair.py repair_actions.py repair_catalog.py repair_eligibility.py repair_recovery.py repair_transaction.py repair_workspace.py repair_yaml.py reviewed_repair.py preview_repair.py batch_repair.py migration.py privacy.py diagnostics.py api_contracts.py mutation_receipts.py routes.py route_support.py tools tests
python -m pytest --cov --cov-report=term
python -m py_compile validator.py scanner.py library_doctor_report_cache.py library_doctor_scan_policy.py library_doctor_scan_worker.py repair.py repair_actions.py repair_catalog.py repair_eligibility.py repair_recovery.py repair_transaction.py repair_workspace.py repair_yaml.py reviewed_repair.py preview_repair.py batch_repair.py migration.py privacy.py diagnostics.py api_contracts.py mutation_receipts.py routes.py route_support.py tools/verify_host_contract.py
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

Optional accessibility and manual assistive-technology checks are collected in
`docs/accessibility-certification-2026-08-11.md`. They are useful release
guidance, not formal certification or mandatory evidence. Scanner and
adversarial-corpus limits are defined in
`docs/performance-and-fuzz-budgets.md`. Pytest keeps temporary files and its
cache in ignored repository-local `.test-artifacts/` and `.test-cache/`
directories, so a clean checkout does not depend on the system temp directory.
The practical public-release workflow is documented in
`docs/public-release-implementation-plan-2026-08-20.md`.

`library_doctor` is the plugin ID and API namespace from version 0.15 onward.
Upgrading from an earlier release moves the previous local cache and recovery
data automatically before Library Doctor opens it. If both old and new data
folders exist, startup stops safely so neither copy is overwritten.

</details>
