# Reviewed Repairs implementation report

Date: 2026-08-12
Baseline: `docs/reviewed-repairs-implementation-plan-2026-08-12.md`
Repository: Library Doctor plugin only

## Outcome

The approved scalable Reviewed Repairs plan is implemented with HO/PO as the
first registered adapter. No FeedBack host or Desktop source file was changed,
no real Feedpak was modified, and none of the new HO/PO decisions entered the
automatic-safe catalog, Fix all safe issues, or multi-song safe batch repair.

The first adapter now finds and presents:

- both hammer-on and pull-off enabled on one note;
- a lone hammer-on whose incoming movement descends;
- a lone pull-off whose incoming movement ascends;
- same-fret HO/PO, including chord members;
- HO/PO without one usable predecessor; and
- evidence that a marker may have been attached one note early.

Long gaps remain candidates. Top-level arrangements and every phrase
difficulty are independent streams. Chord-template frets may provide context,
but only an explicit stored note can be a mutation target.

## Plan reconciliation

| Approved requirement | Implemented result |
|---|---|
| Saved implementation baseline | The approved plan remains in `docs/reviewed-repairs-implementation-plan-2026-08-12.md`. |
| Scalable registry | `reviewed_repair.py` defines registry metadata, context schema, candidate ID prefix, paged/selective classifiers, decisions, blockers, mutable fields, mutation derivation, postconditions, audio support, candidate limit, and test owner. |
| Shared mutation-free classifier | `repair_eligibility.py` supplies the same bounded HO/PO facts to validation and authoritative planning. |
| New diagnostics | Added direction-mismatch, same-fret, and no-usable-source findings; the existing conflicting-techniques finding is now explicitly review-required. Validator version is `rules-28`. |
| No automatic guessing | The automatic allowlists and `_ALL_SAFE_RULE_ORDER` are unchanged. Reviewed rules are served from a separate catalog. |
| Context-specific explicit choices | Hammer-on, pull-off, tap, remove HO/PO, conditional move-to-next, and leave unchanged are registered. Nothing is preselected. |
| Closed mutation surface | Operations can modify only `ho`, `po`, and `tp`; clients cannot submit paths, fields, arbitrary values, or mutation objects. |
| Visible blockers | Same-time string conflicts, ambiguous predecessors, malformed techniques, JSONC, stale source, and ambiguous move targets cannot mutate. |
| Exact preview and source binding | Candidate IDs include server-derived context; preview and Apply replan from current bytes and verify exact object hashes and postconditions. |
| Existing transaction path | Reviewed Apply uses complete candidate construction, validation, durable backup, journal/reconciliation, atomic commit, receipt storage, cache refresh, and hash-guarded Undo. |
| Partial decisions | Preview reports candidate, selected, changing, skipped, blocked, unresolved, remaining-review, and per-choice counts. Unselected JSON values are preserved. |
| Strict versioned API | Separate catalog, inspect, preview, apply, passage-generation, and passage-audio routes use strict request contracts and uniform error envelopes. |
| Optional passage listening | A 12-second bounded full-mix excerpt is generated on demand, cached temporarily with TTL/size limits, supports browser byte ranges, and is never written into the Feedpak. |
| Generic accessible UI | Related findings open one focused workflow with previous/current/next evidence, one-based and stored string numbers, no default choice, Leave unchanged, focus restoration, live announcements, confirmation, and Undo handoff. |
| Accurate pagination | Candidate inspection scans exact totals across arrangement members, returns bounded disjoint pages, retains decisions across pages, and can authoritatively plan a candidate from any page. |
| Extensibility gate | `tests/fixtures/reviewed_repair_coverage.json` fails when a future adapter lacks candidate, decision, blocker, preservation, tamper, validation, Undo, API, or frontend test ownership. |

## Important behavior boundaries

- This is reviewed repair, not safe repair. The implementation does not
  automatically infer HO/PO or apply it through batch repair.
- An author can deliberately retain an unusual reviewed technique. Such a
  choice may remain visible on later scans; leaving or choosing it does not
  permanently suppress the finding.
- Same-fret input can be converted to a tap, stripped of HO/PO, assigned one
  explicit flag, or left unchanged. Library Doctor does not alter the fret,
  timing, string, chord, sustain, or other techniques.
- Move-to-next appears only for one explicit, unambiguous, different-fret next
  note that does not already contain HO/PO/tap or malformed technique data.
- JSONC remains read-only until a comment-preserving writer exists.
- The mixed recording can help locate the passage but cannot prove the exact
  fretting gesture; the UI says this directly and remains usable without audio
  or FFmpeg.
- A reviewed page displays at most 2,000 candidates. Previous/next page
  controls preserve the current session's explicit choices, while the adapter
  limit keeps any one preview/apply request bounded.
- No persistent Ignore/Suppress system was added, matching the approved plan.

## Verification evidence

The completed implementation passed:

- full Python suite: 481 tests against the final implementation state;
- coverage policy: 85.65%, above the required 85%;
- reviewed/backend focused suites, including transaction and Undo;
- Python Ruff and compile gates;
- `pip check` and `pip-audit`: no broken requirements or known runtime
  vulnerabilities;
- npm audit: zero high-severity vulnerabilities;
- frontend syntax, ESLint, and 24 interaction/module tests;
- latest-nightly Playwright: all 8 journeys, including reviewed cancel,
  reviewed apply/Undo, axe, forced colors, keyboard focus, and narrow reflow;
- current local Nightly host capability contract: compatible; and
- `git diff --check`.

All write-path tests used synthetic disposable packages. The configured real
library and FeedBack host repositories remained outside the mutation scope.

## Residual release scope

This implementation does not constitute a release or waive the existing human
release-signoff ledger. Plugin version `0.44.0` is intentionally unchanged.
NVDA/JAWS and other human release checks remain separate from this code phase,
as previously agreed.
