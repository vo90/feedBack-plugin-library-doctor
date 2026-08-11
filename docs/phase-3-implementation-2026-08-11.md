# Phase 3 implementation: frontend responsibility extraction

Date: 2026-08-11
Plugin version: `0.39.0`
Repository boundary: standalone `feedBack-plugin-library-doctor` only

## Outcome

Phase 3 is complete. Library Doctor now uses FeedBack's source-served native
ES-module contract with a thin `screen.js` entry and a small composition root.
The scan, results, finding, repair, preview, batch, Song Tools, playback, and
status responsibilities are isolated behind explicit factory dependencies.

The extraction is behavior-preserving: the Phase 2 semantic browser fixtures,
request-race tests, native-module host journeys, and the full backend safety
suite remain the baseline. No FeedBack host or Desktop source file was changed.

## Module structure

- `src/app.js`: DOM lookup, dependency composition, event binding, enter/leave,
  host subscription wiring, and destruction.
- `src/api.js`: activation-aware requests and structured error normalization.
- `src/store.js`: explicit state plus activation generation and abort control.
- `src/dom.js`: safe DOM construction, confirmation focus, and trigger restore.
- `src/formatters.js`: shared size, duration, receipt, and count presentation.
- `src/status-view.js`: dashboard state, scan progress, outcome, and status UI.
- `src/scan-controller.js`: scope controls, worker settings, scan commands,
  cancellation, and polling.
- `src/results-controller.js`: summaries, filters, rules, pagination, export,
  and result rendering.
- `src/finding-view.js`: finding explanations and technical evidence.
- `src/repair-controller.js`: single/all-safe repair review, receipts, Undo,
  recovery finalization, and history.
- `src/preview-controller.js`: listen-first and automatic preview workflows.
- `src/batch-controller.js`: batch preview/apply/Undo/finalize orchestration.
- `src/song-tools-controller.js`: local song search, selection, and Preview
  Creator shell.
- `src/playback-controller.js`: host playback priority and global notice.

`src/app.js` is below the enforced 500-line composition-root limit. Every
source module is below 1,500 lines.

## Dependency and host contract

- `plugin.json` declares `"scriptType": "module"` and
  `"minHost": "0.3.0-alpha.1"`.
- `screen.js` statically imports and boots `src/app.js`.
- Module files perform no browser I/O at import time; the browser environment
  and feature collaborators are injected by `bootLibraryDoctor(window)`.
- Controllers do not import one another. Runtime collaboration uses one
  explicitly injected action registry, avoiding circular dependencies.
- The import contract discovers every `src/*.js` file, checks relative import
  resolution, rejects cycles and reverse dependencies on `app.js`, and
  enforces module-size limits.
- ESLint enforces unresolved-import, cycle, undefined-name, unused-name, and
  1,500-line rules in local and Windows/Linux CI.
- The unsupported-host/loading explanation remains visible until the native
  module graph evaluates and boot hides it.

## Lifecycle and stale-work protection

The store issues a new activation generation and `AbortController` for each
visit. Leaving Library Doctor invalidates the generation, aborts scoped
requests, and clears scan, result-search, and Song Tools timers. Older responses
cannot repaint a later visit.

Host subscriptions are wired once. Cleanup functions returned by
`feedBack.on` are stored, and `destroy()` unsubscribes them, removes the
capability-ready listener, deactivates the screen, and stops playback timers.
Playback-priority synchronization remains global to FeedBack navigation and
uses the API client's non-activation-bound request path.

## Accessible shared primitives

`src/dom.js` supplies safe element/text construction, confirmation groups,
entry focus, and trigger-focus restoration. Batch apply/Undo/finalization and
single repair/recovery confirmations use these primitives. Dynamic content
continues to use native nodes and `textContent`, with no HTML-string injection.

## Regression coverage

- Native-module manifest and thin-entry contract.
- Import-time purity for every source module.
- Complete acyclic import graph and module-size boundaries.
- Lifecycle subscription count and destruction cleanup.
- Structured API error normalization.
- Activation invalidation and AbortSignal behavior.
- Leave/re-enter stale-status and concurrent stale-result response races.
- Shared confirmation focus entry and restoration.
- Phase 1 dashboard states, filter semantics, Song Tools isolation, and repair
  history disclosure.
- Latest-nightly module loader, first-run scan, results, and Preview Creator
  journeys.

## Completion criteria

- No intended behavior delta: passed.
- Dependency rules and source-size boundaries: passed.
- Thin `screen.js` and small composition root: passed.
- Lifecycle cleanup and stale-response protection: passed.
- All characterized frontend workflows: passed.
- Latest supported nightly native-module smoke: passed.

## Verification

- Python: `371 passed`; total coverage `85.50%` (required `85%`).
- Ruff and Python production-module compilation: passed.
- Native JavaScript parse and ESLint dependency/size gates: passed.
- Node/JSDOM frontend: `18 passed`.
- Latest-nightly Playwright host suite: `3 passed`.
- Runtime: Library Doctor `0.39.0`, enabled and ready; module manifest loaded;
  scan, repair, and batch states idle.
- Git hygiene: `git diff --check` passed. The FeedBack nightly and Desktop
  working trees are clean and exactly match their official `upstream/main`
  commits. The plugin base commit exactly matches its GitHub `main` commit.
