# Safe structural repairs implementation plan

Date: 2026-08-15

Repository boundary: standalone `feedBack-plugin-library-doctor` only

Starting baseline: `main` at
`5cd7763a3ddd5eaf6bc294f491cbfc958af62a51`

Research basis:
`docs/guitar-tablature-rocksmith-cdlc-repair-research-2026-08-15.md`

Plan status: ready to execute without further product or safety decisions

## Outcome

Implement eight narrowly bounded automatic repairs for structural Feedpak data:

1. omit explicitly empty optional arrangement `phrases` and `tempos` keys;
2. remove complete JSON-identical tempo, meter, and tone-change events; and
3. stable-sort valid tempo, meter, and tone-change streams by their existing
   stored times.

The implementation must reuse Library Doctor's current preview, complete
candidate validation, recovery, transaction, receipt, finalization, and Undo
path. It must remain deterministic, read-only during scanning, bounded by the
existing JSON limits, and fail closed whenever the stored data or effective
source is ambiguous.

This phase deliberately excludes musical rewrites and the lower-confidence
structural candidates from the research report.

## Locked scope

The following rule codes, action kinds, sources, severities, and mutation
classes are final for this phase.

| Rule code | Action kind | Catalog `source_kind` and resolved source | Severity | Exact mutation |
|---|---|---|---|---|
| `chart.empty-phrases-key` | `omit_empty_phrases_key` | `arrangement`; arrangement JSON root only | Warning | Remove the root `phrases` key only when its value is exactly `[]`. |
| `timeline.empty-arrangement-tempos-key` | `omit_empty_arrangement_tempos_key` | `arrangement`; arrangement JSON root only | Warning | Remove the root `tempos` key only when its value is exactly `[]`. |
| `timeline.duplicate-tempo` | `remove_exact_duplicate_tempo_events` | `timeline`; every per-arrangement tempo override and the declared song-timeline sidecar | Warning | Keep the first complete JSON object and remove later identical copies in the same list. |
| `timeline.tempos-out-of-order` | `reorder_tempo_events` | `timeline`; every per-arrangement tempo override and the declared song-timeline sidecar | Existing warning | Stable-sort the existing objects by finite numeric `time`. |
| `timeline.duplicate-time-signature` | `remove_exact_duplicate_time_signature_events` | `timeline`; declared song-timeline sidecar only | Warning | Keep the first complete JSON object and remove later identical copies in the same list. |
| `timeline.time-signatures-out-of-order` | `reorder_time_signature_events` | `timeline`; declared song-timeline sidecar only | Existing warning | Stable-sort the existing objects by finite numeric `time`. |
| `tones.duplicate-change` | `remove_exact_duplicate_tone_changes` | `arrangement`; effective inline arrangement JSON tone block only | Warning | Keep the first complete JSON object and remove later identical copies in the same list. |
| `tones.changes-out-of-order` | `reorder_tone_changes` | `arrangement`; effective inline arrangement JSON tone block only | Existing warning | Stable-sort the existing objects by finite numeric `t`. |

Use the same tempo rule codes for per-arrangement overrides and song-level
tempo data. They describe the same structural issue, the current validator
already uses the generic ordering code for both sources, and a selected rule
must disappear package-wide after repair. Locations and `arrangement_id`
continue to identify individual occurrences.

Add all eight definitions to the automatic-safe catalog. Set
`change_kind="omit_empty"` for the first two, `change_kind="remove_duplicates"`
for the three duplicate repairs, and `change_kind="reorder"` for the three
ordering repairs.

## Explicitly deferred

Do not implement any of the following in this phase:

- sorting top-level notes, chords, or anchors;
- sorting or deduplicating phrase levels, phrase windows, handshapes, or
  dynamic-difficulty streams;
- exact duplicate bend-point removal;
- slide, link-next, sustain, bend-value, fingering, FHP, anchor, chord-name,
  sync, tuning, or transcription decisions;
- tone-change time conversion from `time` to `t`;
- numeric-string, boolean, `null`, NaN, or Infinity coercion;
- manifest YAML mutation;
- song-timeline empty-array omission;
- new suppression, reviewed-repair, or audio-analysis behavior;
- changes in FeedBack core, the runtime launcher, or another repository; or
- dependency additions, schema revisions, commits, pushes, or releases.

Top-level note/chord/anchor sorting and bend-point deduplication remain
evidence-gated follow-ups described near the end of this plan.

## Non-negotiable safety invariants

Every implementation and test decision must preserve these invariants:

1. Scanning and preview planning do not write package files.
2. No repair invents, retimes, renames, coerces, or changes a musical event.
3. Exact duplicate identity includes the complete JSON object, including all
   unknown/additional properties.
4. Equal-time relative order is preserved by sorting.
5. Same-time events with different complete data are never deduplicated.
6. A list with a malformed member is not automatically sorted.
7. Conditional repair eligibility is shared by the validator and the
   authoritative planner; the scan must not promise a repair that Apply must
   reject for an already-known source condition.
8. A mixed safe/unsafe occurrence set for one tone rule blocks that rule for
   the complete package. Partial tone repair is not allowed.
9. Member paths are canonicalized, containment checked, and deduplicated
   before planning. A shared JSON member is never mutated twice.
10. Apply recomputes structural guards against the current source. It does not
    trust client-supplied paths, permutations, hashes, or counts by themselves.
11. Complete candidate validation runs before commit and must satisfy the
    existing finding-delta policy.
12. Every committed repair has the existing verified recovery, receipt,
    finalization, restart reconciliation, and exact-byte Undo behavior.
13. A failed package does not abort an independent batch.
14. Invalid, JSONC, duplicate-key, oversized, excessively nested, or stale
    sources fail closed without producing a package backup or mutation.
15. Tests use synthetic packages or disposable copies. They never mutate the
    user's real song library or the development test library.

## Effective data-source rules

### Arrangement phrases

Only a root arrangement JSON property whose key is `phrases` and whose parsed
value is exactly an empty list is eligible. Absence already means that no
difficulty ladder is present. `null`, an object, a string, a nonempty list,
and any nested property named `phrases` are not eligible.

The validator may still emit the existing generic schema finding for the
non-conformant empty array. The new dedicated finding supplies the safe repair.
After repair, both the dedicated finding and a schema finding caused solely by
that empty key must disappear.

### Per-arrangement and song-level tempos

These are separate active sources and can coexist:

- A nonempty arrangement-root `tempos` list overrides the song tempo only for
  that chart.
- The declared `song_timeline` sidecar supplies the song-level tempo fallback.

Tempo duplicate and order rules therefore resolve all of the following in one
package-wide action:

1. every active arrangement JSON member carrying a nonempty root `tempos`
   list; and
2. the manifest-declared song-timeline JSON member when it carries a root
   `tempos` list with a repairable issue.

Do not pick one source over the other. Do not reinterpret arrangement tempo
overrides as legacy song beats or sections. Canonically deduplicate shared
arrangement member paths while retaining all affected arrangement IDs for
reporting.

An arrangement-root `tempos: []` is a separate omission rule because the
Feedpak schema says absence means "follow song tempo." A song-timeline
`tempos: []` is schema-valid and must remain untouched.

### Song-level time signatures

Time-signature rules target only the manifest-declared `song_timeline` member.
They never target arrangement JSON.

FeedBack loads sidecar `tempos` and `time_signatures` whenever the sidecar
parses to an object. Their activity does not depend on the same file also
having valid `beats` and `sections` arrays. Update validation and repair source
resolution to reflect that runtime behavior. Preserve the current stricter
beats/sections activation rule for beat and section repairs.

Concretely, split or parameterize the current song-timeline semantic pass so:

- `tempos` and `time_signatures` are checked in any parsed declared sidecar
  object; and
- `beats` and `sections` are checked as sidecar overrides only when their
  existing completeness condition is met.

### Tone changes

Match FeedBack's current source precedence exactly for each arrangement entry:

1. a nonempty manifest-entry `tones` object overrides the arrangement JSON
   tone block wholesale;
2. an empty manifest-entry `tones: {}` is treated as absent; and
3. otherwise, an inline arrangement JSON `tones` object is effective.

Align the validator's effective-tone selection with this behavior before
enabling the repair metadata. Do not merge manifest and inline tone fields.

The current repair engine writes JSON members but has no lossless manifest
YAML writer. Consequently:

- an issue in an effective inline arrangement JSON tone block may be automatic;
- an issue in a nonempty manifest-entry tone block is unavailable for automatic
  repair with reason `manifest_tones_require_manual_edit`;
- an issue in `drum_tones` or a drum arrangement's manifest tone block is
  unavailable for the same reason; and
- if one package has both repairable inline occurrences and manifest-blocked
  occurrences for the selected tone rule, block the entire rule transaction.

An inactive inline tone block hidden by a nonempty manifest override is neither
reported as the effective runtime problem nor repaired.

FeedBack's canonical tone-change time key is `t`. The validator may diagnose
legacy or malformed `time` data separately, but `time` must never make a tone
duplicate/order repair eligible and must never be converted automatically.

## Shared validity and identity contract

Put pure, mutation-free predicates in `repair_eligibility.py` and use them from
both `validator.py` and `repair.py`. Keep source-file discovery in the existing
validator/repair readers, but share the meaning of a valid repairable event and
effective tone precedence.

The shared contract has three event kinds.

### Tempo event

A repairable tempo event is a dictionary with:

- `time`: an `int` or `float`, excluding `bool`, whose float value is finite;
- `bpm`: an `int` or `float`, excluding `bool`, finite and strictly greater
  than zero; and
- any number of additional JSON properties, all preserved.

### Time-signature event

A repairable time-signature event is a dictionary with:

- `time`: an `int` or `float`, excluding `bool`, whose float value is finite;
- `ts`: a list of exactly two positive integers, both excluding `bool`; and
- any number of additional JSON properties, all preserved.

### Tone-change event

A repairable tone change is a dictionary with:

- canonical `t`: an `int` or `float`, excluding `bool`, whose float value is
  finite;
- `name`: a nonempty string;
- optional `rig`: when present, a nonempty string after whitespace trimming;
  and
- any number of additional JSON properties, all preserved.

Do not accept a `time` alias, numeric string, missing name, invalid rig, or
non-finite value for automatic tone repair.

### Complete JSON identity

After an object passes its event-kind predicate, exact duplicate identity is
the existing typed/canonical JSON identity of the complete object:

- object key order is irrelevant;
- array order is significant;
- unknown properties participate in identity;
- strings are not trimmed or normalized for identity;
- `120` and `120.0` remain distinct stored JSON values;
- `true`, `1`, and `1.0` remain distinct; and
- non-finite or non-JSON values have no repair identity.

Keep the first occurrence and remove every later occurrence of the same
identity, including non-adjacent copies. A same-time object with a different
value, spelling, rig, or unknown property is a conflict, not a duplicate.

For this first phase, fail closed for a targeted list if any member fails the
event-kind predicate. This is intentionally stricter than proving one duplicate
pair in isolation and prevents a successful action from implying that a
partially malformed stream is clean.

## Detection and reporting contract

### Empty optional keys

Emit one dedicated finding per affected arrangement member and include:

- the exact member-root location (`<member>:phrases` or `<member>:tempos`);
- `arrangement_id` when available;
- `affected_count: 1`;
- automatic repair metadata only when shared eligibility succeeds; and
- no musical time or string.

Absent keys, empty song-timeline arrays, and wrong-shaped values do not receive
these dedicated safe findings.

### Exact duplicate timed events

For each affected active list, scan left to right, retain the first index for
each complete identity, and collect all later indexes. Emit a finding at the
first duplicate with:

- the source member and list index;
- `arrangement_id` for per-chart tempo/tone occurrences;
- the event time;
- `affected_count` equal to the number of later objects removable from that
  list; and
- copy stating that only complete identical copies are removed.

Do not count the kept object. Do not group duplicates across files, across two
arrangements with distinct members, or across different arrays.

### Out-of-order timed events

Retain the current first-inversion reporting style. A list is out of order
when a later valid stored time is strictly less than the preceding valid stored
time. Equal times alone are not out of order.

The finding can remain visible as a diagnostic if a list contains malformed
members, but its repair metadata must be unavailable with a stable reason. A
valid list is automatically repairable only when the source rules above also
permit mutation.

### Conditional metadata

Extend the existing shared eligibility/feature mechanism rather than adding a
second repair catalog. Scanner results must distinguish:

- catalog `safety="safe_automatic"` plus eligibility feature
  `status="automatic"`, which emits finding `repairability="safe_candidate"`;
- cataloged safe action plus eligibility feature `status="unavailable"`, which
  emits finding `repairability="manual"` with a stable blocker code and concise
  message; and
- unsupported/manual-only behavior without automatic catalog eligibility.

All eight repairs require ordinary JSON. A `.jsonc` source may retain its
diagnostic finding, but its eligibility feature must be `status="unavailable"`
and its finding `repairability="manual"`, with stable blocker
`jsonc_requires_lossless_writer`. It is excluded from individual Apply, Fix all
safe, and batch-safe selection. Preview refusal must not create a backup. Cover
both arrangement JSONC and song-timeline JSONC sources.

The authoritative planner must recalculate the same predicates. A stale source
uses the existing `source_changed` response; a stable eligibility blocker uses
its specific reason and creates no backup.

## Mutation operations

### Guarded empty-key omission

Add one closed operation representation for root optional-array omission. It
must carry:

- operation kind;
- the exact one-element root path;
- expected source-document hash before omission;
- expected result-document hash after omission; and
- no caller-supplied replacement value.

Planning may emit only the two catalog-authorized combinations:

- `chart.empty-phrases-key` with `['phrases']`; and
- `timeline.empty-arrangement-tempos-key` with `['tempos']` on an arrangement
  source.

Apply must require an exact operation key set, an allowed rule/path pair, the
current field to be present with value exactly `[]`, matching before/after
hashes, and no prior omission of that path in the same plan. It then removes
only the key and verifies the declared result hash. Any mismatch is
`invalid_plan` or `source_changed` according to the existing distinction.

An omission reports one changed stored object, zero removed musical events,
zero reordered events, and `musical_positions: 0`.

### Exact duplicate deletion

Reuse the existing duplicate-group/delete-array mechanism, but add the new
event-kind identity factories and authoritative rule/path validation. The
planner must bind:

- the allowed rule and exact array path;
- expected array length;
- kept and removed indexes;
- the complete entry hash for every group; and
- a descending, unique aggregate removal-index list.

Allowed new paths are exactly:

- `['tempos']` for tempo rules;
- `['time_signatures']` for meter rules; and
- `['tones', 'changes']` for tone rules.

Apply must re-read the current list, rerun the shared event-kind predicate,
recompute complete identity/hash equality, validate every declared index and
group, keep the first occurrence, and reject overlap with an earlier operation.
Unknown properties on the kept entry and all non-target entries remain intact.

### Stable timed-event sort

Add a strict reusable timed-event sort operation for the three new stream
families. Keep the existing bend, lyric, beat, and section operations unchanged
to reduce regression risk.

The new operation carries:

- exact array path;
- fixed time key (`time` or `t`);
- expected length;
- original array hash;
- sorted array hash;
- the complete source-index permutation; and
- moved-object count.

The only allowed path/time-key pairs are:

- `['tempos']` with `time`;
- `['time_signatures']` with `time`; and
- `['tones', 'changes']` with `t`.

The planner and Apply both require at least two entries and require every entry
to pass its shared event-kind predicate. Compute the permutation using Python's
stable sort over the raw numeric value; do not round, quantize, coerce, or use
the validator's tolerance tick. Equal-time entries retain source order.

Apply independently recomputes the expected permutation, moved count, and both
hashes. It rejects duplicate sorts of one path and any permutation that is not
an exact rearrangement of all original indexes.

## Catalog and version changes

Perform these version changes in the same implementation commit/change set:

- `validator.VALIDATOR_VERSION`: `rules-28` to `rules-29`;
- `repair.REPAIR_CATALOG_VERSION`: `repairs-18` to `repairs-19`; and
- automatic safe catalog count: 16 to 24.

Do not change the Feedpak schema revision. These are validator/catalog behavior
changes, not a new file-format contract.

Update:

- `_SAFE_REPAIR_CANDIDATES` and conditional feature metadata;
- `_REPAIR_DEFINITIONS`, `_REPAIR_BY_RULE`, dispatch, planner, Apply, and
  position/count formatting;
- `_ALL_SAFE_RULE_ORDER`;
- catalog coverage fixtures;
- README repair inventory/count; and
- frontend formatter/finding copy for `change_kind="omit_empty"`.

No route or API schema should change. If implementation reveals that generic
catalog propagation is insufficient, make the smallest backward-compatible
addition and cover it with contract tests; do not invent a second endpoint.

## Deterministic Fix-all-safe order

Preserve the current relative order of existing rules except where a dependency
requires the following local order:

1. `chart.empty-phrases-key`;
2. `timeline.empty-arrangement-tempos-key`;
3. `timeline.duplicate-tempo`;
4. `timeline.tempos-out-of-order`;
5. `timeline.duplicate-time-signature`;
6. `timeline.time-signatures-out-of-order`;
7. `tones.duplicate-change`;
8. `tones.changes-out-of-order`.

Place the arrangement omissions before other operations on the same document.
For every nonempty timed list, exact deduplication precedes stable sorting.
Continue recalculating each rule against the output of every earlier rule, as
the current all-safe planner does.

The exact insertion point among unrelated existing repair families is not
musically significant. Use this deterministic family order while preserving
the existing behavior of negative-fret, chart duplicate, handshape, bend,
lyrics, beat, section, and drum repairs.

## Implementation sequence

### Phase 0: preserve and characterize the baseline

1. Record branch, HEAD, and `git status --short`.
2. Preserve all pre-existing tracked and untracked user changes. Do not reset,
   checkout, clean, stash, or rewrite them.
3. Run targeted baseline tests for validator, repair, scanner, routes, batch,
   catalog coverage, and frontend state rendering. Record pre-existing failures
   separately from implementation regressions.
4. Build only synthetic in-test Feedpaks and temporary disposable directories.

Exit gate: baseline state is recorded and no user package was written.

### Phase 1: shared pure eligibility helpers

1. Add the three strict event-kind predicates and complete identity helper to
   `repair_eligibility.py`.
2. Add the nonempty-manifest-tone precedence helper matching FeedBack.
3. Add unit tests for every accepted and rejected primitive before connecting
   the helpers to validation or mutation.

Exit gate: validator and repair can import one tested definition of eligibility
without circular imports or filesystem writes.

### Phase 2: validator findings and source alignment

1. Add the two exact-empty-key findings during arrangement semantic validation.
2. Add exact duplicate collection for tempos, time signatures, and effective
   tone changes.
3. Keep generic tempo codes across arrangement and sidecar sources.
4. Validate sidecar tempo/meter independently of beat/section completeness.
5. Align empty manifest tone override behavior with FeedBack.
6. Attach conditional automatic/unavailable metadata from the shared helpers.
7. Bump `VALIDATOR_VERSION` to `rules-29`.

Exit gate: read-only scan tests prove exact findings, counts, locations, source
precedence, blockers, and no filesystem mutations.

### Phase 3: repair catalog and strict operations

1. Register all eight repair definitions and actions.
2. Add the guarded empty-root-key operation.
3. Add tempo/meter/tone identities to duplicate planning and strict Apply.
4. Add the strict stable timed-event sort operation.
5. Add rule-aware source resolution for both tempo source classes, meter
   sidecars, and effective inline tones.
6. Add package-wide atomic tone eligibility.
7. Add count and `musical_positions` behavior.
8. Insert the deterministic all-safe ordering.
9. Bump `REPAIR_CATALOG_VERSION` to `repairs-19`.

Exit gate: pure/member repair tests prove plans are deterministic, tamper
resistant, no-op cleanly, and preserve every non-target JSON value.

### Phase 4: package transaction integration

1. Exercise each action against directory Feedpaks and archive Feedpaks.
2. Exercise combined duplicate-then-sort and omission-plus-other-action plans.
3. Verify complete candidate validation and finding deltas.
4. Verify stale-source rejection before commit.
5. Verify backup creation only after planning/validation succeeds.
6. Verify Apply, receipt, Undo, finalize, restart reconciliation, and
   idempotency with the existing transaction machinery.
7. Verify one failed song does not abort batch work on other songs.

Exit gate: original package bytes are recoverable exactly, failed plans leave
no source or recovery debris, and all-safe is atomic per package.

### Phase 5: frontend and documentation

1. Add concise `omit_empty` preview/result wording that says the empty optional
   key is omitted and does not claim a musical event was deleted.
2. Confirm existing generic duplicate/reorder views render the new catalog
   definitions without custom panels.
3. Show stable unavailable reason copy for manifest-stored tone changes and
   malformed lists.
4. Confirm new rules participate in the existing severity ordering, per-song
   safe action, Fix all safe, and batch safe selection only when eligible.
5. Update README repair count and inventory.

Exit gate: keyboard/accessibility behavior and existing UI state transitions
remain unchanged except for the new catalog entries and accurate copy.

### Phase 6: end-to-end verification

1. Run the focused unit, package, route, scanner, batch, frontend, and browser
   scenarios below.
2. Run the complete repository quality gates.
3. Run `git diff --check` and review the final diff only within the plugin repo.
4. Produce an implementation report listing changed files, tests, results,
   blocked cases, and any evidence-gated follow-up left deferred.

Exit gate: every completion criterion in this document passes. Do not commit or
push unless the user separately requests publication.

## Test matrix

### Shared predicate tests

Cover, for each event kind:

- smallest valid object;
- valid unknown properties preserved in identity;
- integer and float stored forms remain distinct identities;
- booleans rejected as numbers/integers;
- missing keys, wrong types, `null`, numeric strings, NaN, and Infinity blocked;
- tempo BPM zero/negative blocked;
- meter length other than two and nonpositive components blocked;
- tone `time` without `t`, empty/non-string name, and invalid optional rig
  blocked; and
- deep but in-bound JSON identity deterministic, with existing structure limits
  still enforced.

### Validator tests

Add focused cases in `tests/test_validator.py` for:

- `phrases: []` and arrangement `tempos: []` each emit their dedicated rule;
- absent, `null`, wrong-shaped, and nonempty values do not emit the dedicated
  empty-key rule;
- song-timeline `tempos: []` does not emit the arrangement omission rule;
- adjacent and non-adjacent exact duplicates keep the first index and report
  the correct removable count;
- different unknown properties prevent duplicate identity;
- same-time different BPM/meter/tone data is not labeled an exact duplicate;
- valid inversions emit existing ordering codes;
- equal times without inversion do not emit ordering codes;
- arrangement tempo findings retain `arrangement_id`;
- sidecar tempo/meter findings occur even when beats or sections are absent;
- incomplete sidecar beat/section arrays do not become active accidentally;
- per-arrangement tempo and sidecar tempo findings coexist;
- absent/empty manifest tones fall back to inline JSON as FeedBack does;
- nonempty manifest tones override inline JSON wholesale;
- manifest/drum tone issues report repair unavailable;
- canonical `t` is eligible and `time` alone is not;
- a mixed inline/manifest tone issue blocks the package-wide selected tone
  rule;
- malformed mixed lists never advertise automatic Apply;
- arrangement and song-timeline JSONC findings are manual with
  `jsonc_requires_lossless_writer`; and
- `rules-29` invalidates the previous cached result.

Also assert validation is read-only by hashing every fixture member before and
after scanning.

### Pure repair-planner and Apply tests

Add focused cases in `tests/test_repair.py` for:

- all eight catalog definitions, exact action kinds, sources, safety, titles,
  change kinds, and deterministic order;
- exact empty-key omission with the other root values unchanged;
- both empty keys in one arrangement, with sequential before/after guards;
- no action for absent, nonempty, wrong-shaped, or sidecar-empty data;
- rejected tampered field/path, extra operation key, wrong before/after hash,
  duplicate omission, and stale document;
- duplicate grouping keeps the first and removes later adjacent/non-adjacent
  copies;
- unknown properties survive and participate in identity;
- same-time differing objects remain in source order;
- invalid mixed lists block rather than partially repair;
- sort permutations are stable for equal times and preserve the complete
  multiset of JSON objects;
- tampered index, time key, path, length, moved count, or hash is rejected;
- a healthy/already-sorted list returns a no-op rather than a mutation;
- duplicate removal runs before sorting in all-safe mode;
- shared member paths are planned once;
- per-arrangement tempo and song-sidecar tempo are both included;
- meter never resolves into arrangement JSON;
- hidden inline tones are never touched;
- manifest tone blockers prevent every operation for that rule; and
- JSONC sources are refused before planning a writable candidate or backup; and
- empty-key omission reports zero musical positions.

For deterministic plan tests, run the same input more than once and compare
the complete unsigned plan and operation order.

### Package and transaction tests

Cover both directory packages and archive packages in `tests/test_repair.py`
and `tests/test_routes.py`:

- preview is read-only;
- Apply removes only expected duplicate objects or empty keys;
- Apply sorts only the expected list and preserves equal-time order;
- candidate validation sees all package members, not only the edited file;
- selected rule findings disappear package-wide after Apply;
- unrelated findings remain unchanged;
- tempo/meter conflict findings remain and conflicting entries remain stored;
- no new non-allowlisted finding appears;
- source modification after preview yields `source_changed`;
- invalid eligibility yields no mutation and no backup;
- transaction or validation failure restores the original;
- successful Apply creates a verified recovery and receipt;
- Undo restores exact original bytes and report state;
- finalize removes the recovery only after successful finalization;
- restart reconciliation preserves the pending finalize/Undo choice;
- repeated Apply/Undo/finalize requests preserve current idempotency guarantees;
  and
- no recovery files accumulate after completed finalization.

### Scanner and batch tests

In `tests/test_scanner.py` and `tests/test_batch_repair.py`, verify:

- the new catalog/eligibility metadata survives scanner serialization;
- eligible counts match `affected_count`, not finding-row count;
- unavailable tone rules are excluded from safe selection;
- folder scan, single-song scan, selected-folder repair, and single-package
  repair remain independent of configured library containment under the
  already-established external-scope behavior;
- Fix all safe applies the dependency order per package;
- a blocked/malformed package does not abort another package;
- batch progress/worker behavior is unchanged; and
- batch Undo and result refresh include the new action kinds.

### Frontend and contract tests

Update `tests/fixtures/repair_catalog_coverage.json`,
`tests/frontend/state-view.test.mjs`, and the relevant plugin/route contract
tests to verify:

- all eight rule/action pairs are represented exactly once;
- safe catalog count is 24;
- `omit_empty` preview, applying, success, failure, Undo, and restored copy is
  grammatically accurate;
- omission never says a note, chord, or musical position was removed;
- generic duplicate and reorder copy uses the correct item names;
- unavailable tone reason is visible and no repair button is offered;
- JSONC blocker copy is visible and excludes the finding from individual and
  batch safe actions;
- eligible actions appear in single-rule and Fix all safe flows;
- current severity sorting remains red, then yellow, then blue;
- current expandable safe-repair review placement/state remains intact; and
- no new route or response-shape drift is introduced.

### Browser/runtime smoke scenario

Use one synthetic disposable Feedpak containing:

- `phrases: []` and arrangement `tempos: []` in one chart;
- a second chart with valid duplicate and inverted tempo overrides;
- a sidecar with duplicate/inverted tempos and time signatures but incomplete
  or absent beat/section arrays;
- inline tones with an exact duplicate and inversion;
- same-time differing tempo and meter entries that must remain; and
- unknown properties on every event family.

Run:

```text
scan
  -> inspect dedicated findings and safe availability
  -> preview each action and Fix all safe
  -> apply Fix all safe
  -> refresh scan
  -> verify selected structural findings are gone
  -> verify conflicts and unrelated findings remain
  -> Undo
  -> verify exact original package bytes and findings return
  -> apply again
  -> finalize
  -> verify recovery cleanup and final repaired bytes
```

Use a second disposable fixture with one inline tone issue and one effective
nonempty manifest tone issue. Confirm the tone rule is unavailable, no partial
tone mutation occurs, and no backup is created.

Do not use the user's real development song folder for this smoke test.

## Performance and resource bounds

- Duplicate grouping must be O(n) expected time with bounded canonical
  identities and the existing maximum JSON structure size.
- Stable sorting must be O(n log n) and allocate at most one index permutation
  plus the existing bounded rendered/hash buffers.
- No repair introduces audio decoding, subprocesses, network access, or new
  worker pools.
- Scanner cancellation checkpoints and existing worker-count policy remain in
  force.
- No list is traversed recursively outside the existing structure inspection
  bound.
- No performance test may run against or rewrite a real user library.

Add a bounded synthetic large-list regression test if the current suite has no
equivalent coverage. It should assert completion and deterministic counts, not
wall-clock timing that would be flaky across machines.

## Required verification commands

Run targeted tests during each phase, then run the complete repository gates:

```powershell
python -m pytest
python -m pip check
python -m pip_audit -r requirements.txt
python -m py_compile validator.py scanner.py library_doctor_scan_worker.py repair.py repair_eligibility.py reviewed_repair.py preview_repair.py batch_repair.py migration.py privacy.py diagnostics.py api_contracts.py mutation_receipts.py routes.py tools/verify_host_contract.py
npm ci
npm run audit:dependencies
npm run check:frontend
npm run lint:frontend
npm run test:frontend
npm run test:browser:list
npm run test:browser
git diff --check
```

Run `npm run test:browser` against the disposable Library Doctor development
runtime configured for the synthetic smoke fixtures. Listing the Playwright
tests is a discovery gate, not a substitute for executing them.

If a dependency/audit command requires network access that is unavailable,
record that exact environmental limitation and still complete every local gate.
Do not add, upgrade, or remove a dependency to make an unrelated audit green.

## Expected production and test files

Expected production edits:

- `validator.py`
- `repair_eligibility.py`
- `repair.py`
- `src/formatters.js`
- `src/finding-view.js`
- `README.md`

Expected test/fixture edits:

- `tests/test_validator.py`
- `tests/test_repair.py`
- `tests/test_routes.py`
- `tests/test_scanner.py`
- `tests/test_batch_repair.py`
- `tests/test_plugin_contract.py` when the catalog/count contract requires it
- `tests/fixtures/repair_catalog_coverage.json`
- `tests/frontend/state-view.test.mjs`

The current generic plumbing in `scanner.py`, `routes.py`, `batch_repair.py`,
and `api_contracts.py` should not require production edits. Modify one only if
a failing contract test demonstrates a real propagation gap, and document why
in the implementation report.

## Autonomous decision and fallback policy

The implementation may proceed later without asking the user for routine code,
test, naming, ordering, or UI-copy decisions. The rules and safety choices in
this document are authoritative for the phase.

When an unexpected condition appears:

1. Prefer the narrower interpretation that preserves source data.
2. Never broaden accepted types, coerce a value, infer musical intent, or add a
   YAML writer to keep the schedule moving.
3. If one rule contradicts a verified runtime behavior or cannot meet a hard
   safety invariant, leave that rule diagnostic/manual, add a stable unavailable
   reason and regression test, document it, and continue implementing the
   independent rules.
4. If a baseline test already fails, prove whether the failure predates this
   work; do not rewrite unrelated behavior.
5. If a pre-existing dirty change overlaps an implementation hunk, preserve it
   and make the smallest compatible edit. Do not reset or discard it.
6. Stop only for a genuinely external requirement such as unavailable user
   credentials, permission to mutate a real package, or publication to GitHub.
   None is required for local implementation and synthetic verification.

## Evidence-gated follow-ups

These candidates are not blockers for completing the eight-rule phase.

### Top-level note, chord, and anchor sorting

Consider only after tests prove all of the following against the checked-out
FeedBack loader, renderer, and grader:

- complete valid streams only;
- stable equal-time ordering;
- exact JSON multiset preservation;
- unchanged note/chord collision and predecessor/successor relationships;
- unchanged mastery/difficulty selection;
- clear separation of root streams from phrase-level streams; and
- representative capo, tuning, bass, chord-member, and dense same-time cases.

### Exact duplicate bend points

Consider `chart.duplicate-bend-point` only after a dense-curve fixture proves
renderer and grader equivalence when a complete JSON-identical point is removed.
Equal-time points with different bend values or unknown properties must remain.
If admitted later, exact deduplication must precede the existing bend-point sort.

## Completion criteria

The phase is complete only when all of the following are true:

- all eight catalog entries use the locked codes/actions/source rules;
- validator and planner share the strict eligibility contract;
- FeedBack's tempo/meter and tone-source precedence is matched;
- scanning remains read-only;
- preview and Apply are deterministic and stale-source protected;
- invalid or manifest-backed cases fail closed without partial mutation;
- exact duplicates preserve the first complete object and all unknown data;
- sorting preserves every object and stable equal-time order;
- empty omission removes only the exact empty root key;
- per-arrangement and song-level tempo sources are both covered;
- sidecar meter handling is independent of beat/section completeness;
- Fix all safe, batch repair, report refresh, receipt, Undo, finalize, and
  restart reconciliation pass for archive and directory packages;
- validator/catalog versions and README/catalog coverage are updated;
- targeted, frontend, full Python, dependency, compile, and browser-list gates
  pass or have a precisely recorded external-only limitation;
- `git diff --check` passes;
- no real user package, FeedBack core file, dependency set, commit, branch, or
  remote repository was changed; and
- the final implementation report lists test evidence and the deliberately
  deferred follow-ups.
