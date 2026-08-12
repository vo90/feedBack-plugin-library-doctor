# Reviewed Repairs implementation plan

Date: 2026-08-12
Repository boundary: standalone `feedBack-plugin-library-doctor` only
Starting baseline: `main` at `3901366091ea03ab7497da8247016aad7d4b4fa7`

## Objective

Build a scalable **Reviewed Repairs** framework, with hammer-on/pull-off
repair as its first guided-repair adapter. Reviewed repairs let the user make
an explicit musical decision inside Library Doctor while retaining the same
source binding, complete candidate validation, recovery, transaction, receipt,
and Undo safeguards as existing song-data repairs.

## Non-negotiable boundary

- Do not add HO/PO repair to the automatic-safe allowlist.
- Exclude reviewed repairs from **Fix all safe issues** and multi-song safe
  batch repair.
- Keep scanning read-only.
- Do not modify FeedBack host or Desktop repositories.
- Do not modify packages in `DEV TEST, master untouched` during development.
- Do not create an unrestricted JSON or tablature editor. Every reviewed
  repair must expose only registered decisions and server-generated,
  field-limited operations.
- Reuse `RepairService` for complete validation, recovery backup, final source
  guards, journaling, commit, receipt, cache refresh, and Undo.

## Target architecture

Reviewed Repairs has two layers.

### Shared framework

- Closed adapter registry and catalog metadata.
- Bounded candidate discovery, stable candidate IDs, grouping, and pagination.
- Explicit decision collection with no preselected answer.
- Exact decision preview and source-state binding.
- Strict request/response/error contracts.
- A generic accessible review shell and confirmation flow.
- Existing transaction, receipt, and Undo integration.
- Optional bounded passage-audio generation.
- Explicit unresolved and blocked-candidate reporting.

### Repair-specific adapters

Each adapter declares:

1. adapter/review ID and triggering validation rules;
2. candidate classifier;
3. context presented to the user;
4. allowlisted decisions;
5. exact fields each decision may change;
6. structural blockers;
7. postconditions;
8. user-facing copy; and
9. executable test ownership.

HO/PO is the first adapter. Future same-string conflict, ambiguous-slide,
linked-slide, chord-fingering, and sustain-overlap adapters must reuse the
framework rather than duplicate its workflow or write path.

## User workflow

```text
Open finding
  -> inspect related candidates
  -> review previous/current/next context
  -> optionally listen to the passage
  -> choose one or more decisions
  -> preview exact combined changes
  -> confirm
  -> recalculate against current package bytes
  -> build and validate the complete candidate
  -> create and verify recovery
  -> commit through the existing transaction engine
  -> show result, unresolved items, and Undo
```

Opening the reviewer or listening never changes the package. No decision is
preselected. Unreviewed, skipped, and blocked occurrences remain findings.

## HO/PO diagnostic scope

The first adapter covers:

- both `ho` and `po` set on one note;
- a lone HO contradicting the incoming fret direction;
- a lone PO contradicting the incoming fret direction;
- same-fret HO/PO transitions;
- HO/PO without a usable preceding same-string event; and
- a suspicious flag that matches the outgoing transition and may have been
  attached one note early.

The current note is the destination of the incoming HO/PO transition. The next
note is contextual evidence only and must not cause an otherwise correct
incoming technique to be reported. Long pauses do not suppress a candidate;
the time gap remains visible in context.

## Shared transition classifier

One mutation-free classifier is shared by validation and authoritative repair
planning. It must:

- process standalone notes and explicit chord members;
- use chord-template notes as contextual predecessor/next events;
- keep top-level data and each phrase difficulty level as separate playable
  streams;
- never mix alternative difficulty streams;
- identify same-time string conflicts and ambiguous predecessors;
- retain exact server-side object paths and hashes for writable notes;
- identify writable next notes;
- expose chord membership and current/neighbor technique facts;
- recognize when the current flag matches the outgoing transition; and
- group only exact duplicated storage representations without conflating
  different musical contexts.

## HO/PO decisions

| Decision | Exact mutation |
|---|---|
| Correct to hammer-on | Set `ho: true`; remove `po`. |
| Correct to pull-off | Set `po: true`; remove `ho`. |
| Keep hammer-on only | Resolve a both-flags conflict by removing `po`. |
| Keep pull-off only | Resolve a both-flags conflict by removing `ho`. |
| Convert to tap | Remove `ho` and `po`; set Feedpak field `tp: true`. |
| Remove HO/PO | Remove `ho` and `po` only. |
| Move to next note | Remove HO/PO from current and add an eligible technique to the exact next note. |
| Leave unchanged | Make no mutation; retain the finding. |

Move-to-next is offered only when the target is unique, explicit/writable, on
the same string and playable stream, directionally compatible, free of
conflicting HO/PO/tap data, and neither endpoint is blocked by a same-time
string conflict.

The adapter may change only `ho`, `po`, and `tp`. It must never change frets,
strings, times, sustains, chord templates, other techniques, or unknown stored
properties.

## Blocked and unresolved cases

All candidates remain visible, but mutation is blocked when safe targeting or
interpretation is impossible, including different frets on the same string at
the current timestamp, multiple incompatible predecessor frets, malformed
technique values, changed source objects, ambiguous move targets, unsupported
JSONC, or malformed source structure.

The first implementation deliberately has no persistent suppression system.
Leaving an unusual technique unchanged does not hide it from later scans.

## Registry and API

The reviewed-repair registry is distinct from the automatic-safe allowlist.
Each registration includes its review type, trigger rules, adapter, context
schema, decision types, candidate limit, audio support, copy, and test owner.

Strict versioned operations are required for:

1. candidate inspection;
2. on-demand passage-audio generation and retrieval;
3. exact decision preview; and
4. idempotent reviewed-decision apply.

Clients submit only server-issued candidate IDs and registered decision names.
They never submit source paths, JSON field names, arbitrary values, fret
replacements, or mutation objects. Decisions are normalized into the signed
plan and recalculated against current bytes at Apply.

## Mutation and verification model

A reviewed technique operation binds the exact object path, original object
hash, expected technique state, chosen decision, transition context, optional
target object hash, and exact postcondition. Apply independently validates the
operation and rejects unknown fields, decisions, IDs, duplicates, or stale
source.

Users may repair a bounded subset of candidates. Preview reports selected,
changing, skipped, blocked, unresolved, and per-choice counts. Unselected
objects remain byte-identical at the JSON-value level.

Reviewed repairs use the existing transaction path, preserving exact-source
and containment guards, complete archive/directory integrity, full candidate
validation, durable recovery verification, directory journals, restart
reconciliation, idempotent receipts, cache refresh, and hash-guarded Undo.

## In-plugin passage listening

When one unambiguous manifest-declared Ogg full mix is available, Library
Doctor may generate a short passage clip on demand in temporary storage. The
path uses fixed FFmpeg arguments, `-nostdin`, bounded input/output/duration,
bounded cache and TTL, a process timeout, and automatic cleanup. It never adds
audio to the package. Visual review remains available when audio or FFmpeg is
unavailable, with copy explaining that a mixed recording may not prove a
specific fretting technique.

## UI requirements

The generic reviewed-repair shell displays one focused candidate with
arrangement, time, one-based string plus stored index in technical evidence,
standalone/chord-member context, previous/current/next frets, existing flags,
gaps, outgoing-match evidence, blockers, optional audio, and context-specific
choices.

It provides no default choice, an explicit Leave/Skip option, one dominant
**Preview selected changes** action, technical paths under disclosure,
accessible groups, deliberate focus entry/restoration, and concise live
announcements.

The exact preview states what will change, what will remain unchanged,
unresolved counts, complete-package validation behavior, failure behavior,
and Undo availability.

## Test strategy

Required coverage includes:

- correct single HO/PO ignored;
- wrong-direction single flags;
- both flags for ascending, descending, equal, and missing-predecessor cases;
- same-fret single flags and long gaps;
- outgoing-match evidence;
- standalone, chord-member, template, top-level, and phrase-level contexts;
- mastery grouping without stream mixing;
- string conflicts, ambiguous predecessors, malformed flags, and JSON;
- every registered decision and conditional move availability;
- tampered/duplicate/unknown decisions and candidate IDs;
- property preservation and exact postconditions;
- partial decisions and stale current/target notes;
- archive/directory candidate validation, recovery, source changes,
  idempotency, receipts, fault barriers, and Undo;
- strict API contracts and error envelopes;
- frontend no-default-choice, blocking, navigation, exact preview, focus,
  stale response, and safe-batch exclusion; and
- synthetic Playwright review/cancel and review/apply/Undo journeys.

Registry coverage must fail when any future adapter lacks candidate, decision,
blocker, preservation, stale/tamper, validation, Undo, API, or frontend test
ownership.

## Implementation order

1. Save this plan as the implementation baseline.
2. Add registry contracts and coverage tests.
3. Add shared reviewed-repair types and limits.
4. Implement the shared HO/PO classifier.
5. Add validator findings and metadata; bump the validator rule version.
6. Implement the HO/PO decision adapter and guarded operations.
7. Integrate reviewed preview/apply with `RepairService`.
8. Add strict routes and API contracts.
9. Build the generic reviewed-repair UI shell.
10. Add HO/PO context rendering and decisions.
11. Add optional bounded passage audio.
12. Add receipts, remaining-review reporting, and Undo integration.
13. Run all Python, frontend, browser, dependency, host-contract, and hygiene
    gates.
14. Verify against synthetic fixtures and disposable copies only.
15. Reconcile implementation against this plan and document residual limits.

## Completion criteria

The work is complete when:

- every agreed HO/PO category appears in Reviewed Repairs;
- none enters automatic safe repair or multi-song safe batch;
- no mutation occurs without explicit decision and confirmation;
- only `ho`, `po`, and `tp` can change;
- transition context is accurate and stream-local;
- stale and structurally ambiguous mutations fail closed;
- existing validation, recovery, transaction, receipt, and Undo protections are
  used without a second write path;
- the registry can accept another adapter without duplicating API, workflow,
  transaction, audio, or UI infrastructure; and
- all repository quality gates pass.
