# Phase 2 implementation — characterization and contract tests

Date: 2026-08-11
Plugin version after this phase: `0.37.0`
Baseline: Phase 1 implementation on top of commit
`6435c216a7a1d32fc43c3d1602fa7a1df6d622f0`

## Repository boundary

Every source, fixture, test, workflow, dependency lock, documentation, and
version change in this phase is contained in the standalone Library Doctor
plugin repository. FeedBack Desktop and the nightly FeedBack checkout were used
only as the unmodified runtime host. The official `main` refs were verified at:

- FeedBack nightly: `eef58c88c315b59375926bbfc8740cd18cb958f9`
- FeedBack Desktop: `00653cf4cef8ef7b3753f9f257cb1d7e0638fa94`

## Implemented scope

### Executable frontend characterization

`tests/frontend/` now runs the real `screen.js` in JSDOM with deterministic
synthetic status, result, history, library, and network fixtures. The suite
asserts behavior and semantics rather than snapshots or pixel markup:

- first-run, cached-complete, partial, and stale dashboard transitions;
- the single safe first-run action and progressive result disclosure;
- result-filter request and `aria-pressed` semantics;
- stale response suppression when requests resolve out of order;
- Library Check/Song Tools isolation and semantic workspace state;
- collapsed historical repair activity.

### Latest-nightly Playwright harness

`tests/playwright/` adds three small, single-worker journeys against a configured
FeedBack nightly host:

1. first-run shell, safe action, and scoped live-region semantics;
2. result filtering plus repair review/cancel;
3. keyboard workspace activation, Song Tools, and reversible batch confirmation.

All Library Doctor data routes are fulfilled with synthetic payloads. Known GET
asset routes continue to the host, while every unexpected mutation route is
answered with `501`. The suite also asserts that repair and batch apply routes
were never requested. It cannot start a real scan or alter the configured song
library.

### Typed API and error contracts

`api_contracts.py` and `tests/fixtures/api_contracts.json` lock the frontend's
core additive response shapes and strict mutation-request shapes. Route-level
tests validate the real status, results, repair-catalog, and error payloads.
The repair-catalog response now includes the existing catalog version as an
additive field.

### Catalog-to-test ownership

`tests/fixtures/repair_catalog_coverage.json` maps every current repair rule to
at least one executable test owner. A meta-test fails when:

- a catalog rule has no entry;
- a removed rule remains in the map;
- a referenced pytest module or function does not exist.

The four media-preview catalog entries also share a direct characterization of
their reviewed full-mix replacement contract.

### Privacy-safe support logging

`privacy.py` installs one logger boundary before migration, scanner, preview,
repair, batch, or route services receive the host logger. It:

- replaces Feedpak/Sloppak identities with stable per-session opaque tokens;
- removes local paths and nested path values;
- retains exception type but removes private exception text;
- deliberately prevents traceback collection for support-facing exception logs.

Privacy tests assemble captured support-log text and prove that artist, song,
package suffix, local path, exception message, and traceback state do not leak.

## CI and local workflow

The existing Windows/Linux CI matrix now installs Node dependencies and runs:

- the nine executable frontend DOM tests;
- Playwright discovery/configuration validation.

The host-backed Playwright journeys remain an explicit nightly test because the
normal CI runners do not provide FeedBack nightly. Browser dependencies and
reports are locked or ignored inside this plugin repository only.

## Verification record

- Ruff: passed.
- Python compile gate: passed.
- `node --check screen.js`: passed.
- Python suite: `371 passed`.
- Coverage: `85.50%` (required minimum: `85%`).
- Frontend DOM suite: `9 passed`.
- Playwright nightly suite: `3 passed`.
- Playwright test discovery: `3 tests in 1 file`.
- Runtime plugin: `0.37.0`, enabled, scan idle, repair idle, batch idle.
- No real scan, repair, batch apply, or Undo was run during Phase 2 browser QA.

## Completion and rollback

The Phase 2 completion criteria are met: critical frontend tests are small and
deterministic, assertions target states and semantics, contracts are typed and
executable, every repair has a test owner, and support-facing logging has a
tested privacy boundary.

If a browser assertion becomes flaky, remove only the unstable host-navigation
or presentation assertion while retaining state transitions, request contracts,
and mutation-denial checks. Never mute the repair review/cancel, batch
confirmation/cancel, stale-response, or privacy safety journeys.

The next audit phase is Phase 3, native module extraction. Phase 2 intentionally
does not move frontend production code; it establishes the behavioral baseline
that Phase 3 must preserve.
