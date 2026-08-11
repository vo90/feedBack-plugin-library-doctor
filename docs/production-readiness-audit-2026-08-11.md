# Library Doctor production-readiness audit

**Audit date:** 2026-08-11
**Audited repository:** `C:\Dev\feedback-workspace\feedBack-plugin-library-doctor`
**Audit mode:** Audit only; current working tree; no product-code changes

## 1. Audit scope and baseline

### Repository baseline

| Item | Observed value |
|---|---|
| Branch | `main`, tracking `origin/main` |
| Commit | `6435c216a7a1d32fc43c3d1602fa7a1df6d622f0` |
| Commit subject | `test: make parallel worker expectations deterministic` |
| Upstream | `https://github.com/vo90/feedBack-plugin-library-doctor` |
| Upstream parity | Local `HEAD` exactly matched `origin/main` when the audit began |
| Version | `0.34.0` (`plugin.json:1-14`) |
| Git status at start | Clean: `## main...origin/main` |
| Uncommitted changes included | Yes by audit policy; none existed at baseline |
| Product changes during audit | None. This report is the only intentional repository change |

The audit covered the manifest, README, development instructions, every production module, all nine test modules, schemas and dependency declarations, relevant FeedBack nightly plugin-host documentation and loader code, and a live read-only walkthrough in the latest FeedBack nightly. No `AGENTS.md` was present in this repository or its checked parent directories.

The live walkthrough used an isolated clean plugin worktree at the same commit. It navigated between Health Scan and Song Tools and opened a song/tool disclosure, but did **not** start a scan, generate a preview, or apply, undo, or finalize any repair. No real Feedpak was written.

### Operating environment

| Tool/runtime | Version or state |
|---|---|
| OS | Microsoft Windows NT `10.0.26200.0` |
| PowerShell | `5.1.26100.8875` |
| Python | `3.12.10` |
| pytest | `9.1.1` |
| Node.js | `v24.18.0` |
| npm | `11.16.0` |
| Git | `2.55.0.windows.3` |
| Ruff | available and passing repository checks |
| FFmpeg | `8.1.2` |
| FeedBack host | latest local nightly, plugin loaded and reported ready |
| Machine pressure during tests | 16.97 GB RAM total; 1.13 GB available, which materially affected a worker-policy test |

### Evidence limitations

- The live profile already contained scan and repair history. The first-run assessment is therefore based on rendered source plus the live populated state, not a destructive reset of the user's profile.
- Keyboard structure and runtime accessibility-tree output were inspected. NVDA/JAWS/VoiceOver, 200–400% zoom, forced colors, and light-theme contrast were not manually certified.
- Power loss, forced process termination, filesystem-junction swaps, SQLite lock storms, and hostile-package wall-clock exhaustion were analyzed from code and existing tests; they were not induced against real data.
- Dependency consistency was checked with `pip check`; a CVE scanner such as `pip-audit` was not installed and was not added during this audit.

## 2. Executive verdict

**Verdict: technically capable and unusually conservative, but not yet production-ready for the stated novice-user standard. Overall weighted score: 61/100.**

The plugin's core safety model is substantially stronger than its interface suggests. Automatic repair is allowlisted, ambiguous data is normally refused, plans are content-bound, candidates are validated before replacement, archive integrity is checked, and Undo is hash-guarded. The test suite exercises a large amount of real repair and route behavior using disposable packages. These are important strengths and should not be diluted during simplification.

The release should nevertheless be held for four P1 concerns:

1. Normal repairs do not prove that source bytes are still unchanged at the final commit boundary; a concurrent editor or sync process can be overwritten after planning and validation.
2. An unpacked package with several changed members is committed one file at a time. Exception rollback exists, but abrupt process or machine death can leave a mixed package with a recovery backup that is not automatically surfaced.
3. validation workers have cooperative cancellation but no hard per-package deadline or forced termination; shutdown waits indefinitely for a stuck worker.
4. The 3,539-line frontend and its safety-critical workflows have no executable JavaScript, browser, keyboard, or accessibility tests. Current “frontend” tests mostly assert that strings exist in source.

For novice users, the dominant problem is not visual polish. It is that configuration, prior recovery state, batch operations, eight summary actions, rule filters, nine package filters, exports, detailed safety prose, and Song Tools all compete for attention. In the live populated screen, 57 controls were visible, including 39 buttons. The interface often explains every safety mechanism at once instead of guiding the next safe action.

The recommended product direction is a **simplified dashboard with progressive disclosure**, not a rigid wizard. Preserve direct access for experienced users, but make the default sequence obvious: choose the recommended scope, scan, review needs-attention results, explicitly review a repair, then see a concise outcome and recovery choice.

## 3. Production-readiness scorecard by dimension

| Dimension | Weight | Score | Weighted | Rationale |
|---|---:|---:|---:|---|
| UX, IA, novice usability | 40% | 5.2/10 | 20.8 | Safe capabilities exist, but the populated screen has excessive simultaneous choices, overlapping result concepts, advanced language, and weak journey staging. |
| Safety and runtime reliability | 25% | 7.4/10 | 18.5 | Strong allowlist/candidate/backup/Undo model; final-source TOCTOU, directory crash consistency, and hard worker termination remain material gaps. |
| Maintainability and testability | 20% | 6.3/10 | 12.6 | Backend has meaningful domain separation and extensive tests; frontend concentration, implicit states, raw API dictionaries, and missing UI tests increase change risk. |
| Accessibility | 10% | 5.4/10 | Names and native controls are often good; nested main landmarks, invalid tab semantics, absent focus management, and excessive live regions are material. |
| Performance and developer experience | 5% | 6.6/10 | Incremental SQLite cache, process workers, bounds, and telemetry are strengths; stuck-worker behavior and an environment-sensitive gate reduce confidence. |
| **Total** | **100%** |  | **60.6/100 → 61/100** | **Robust technical beta; not yet a novice-ready production release.** |

## 4. Current architecture and workflow map

### Responsibilities and dependency direction

| Area | Current owner | Responsibility and dependencies |
|---|---|---|
| Host integration | `plugin.json`, `routes.py:60-117`, `screen.js:3488-3540` | Declares screen/assets/routes; obtains `load_sibling`, library path and plugin logger; subscribes to FeedBack lifecycle and playback events. |
| Browser shell | `screen.html:1-275`, `assets/library-doctor.css:1-634` | Static landmarks, all workspaces and result containers, responsive styling, native controls. |
| Browser behavior | `screen.js:1-3540` | Owns UI state, API client, polling, rendering, scan flow, result filtering, all repair flows, batch flows, playback priority, Song Tools, and lifecycle. |
| API composition | `routes.py:60-617` | Constructs services and maps HTTP requests to scanner, repair, batch, preview and export operations. |
| Scan/cache | `scanner.py:147-726`, `scanner.py:729-2417` | Owns SQLite report cache and scan state; resolves scopes; discovers packages; signs content; schedules workers; persists current scope and last scan. |
| Read-only worker | `library_doctor_scan_worker.py:31-139` | Spawn-safe validator loading, cooperative pause/cancel, read-only process pool. It has no cache or repair objects. |
| Validation | `validator.py:566-590`, `validator.py:657-915`, `validator.py:1315-2687`, `validator.py:4464-5114` | Bounded package reader, schemas/rules, finding aggregation, optional deep-audio inspection; returns reports only. |
| Eligibility | `repair_eligibility.py:1-259` | Shared conservative predicates used to keep scanner promises aligned with repair planning. |
| Repair planning/transaction | `repair.py:928-3249` plus pure planners after `repair.py:3252` | Explicit repair catalog, authoritative re-planning, candidate construction/validation, backup, commit, history, Undo and finalization. |
| Preview generation | `preview_repair.py:149-305`, `preview_repair.py:358-905` | Bounded FFmpeg invocation, in-memory content-bound preview plans; intentionally delegates writes to `RepairService`. |
| Batch orchestration | `batch_repair.py:81-2633` | Explicit state machines for preview/apply/undo/finalize; delegates every package mutation to `RepairService`; persists last result/checkpoint. |
| Migration | `migration.py:39-293` | One-time identity migration, atomic state writes, fail-closed collisions. Runs before services open state. |
| Persistence | SQLite plus JSON/ZIP under host config | Scan reports in WAL SQLite; recovery ZIPs; repair history; batch result and checkpoint. Playable packages remain in the configured library. |

### State ownership and state machines

- **Browser:** one mutable `state` object (`screen.js:9-44`) plus playback globals (`screen.js:45-54`). Scan, batch and repair states are partly authoritative server states and partly inferred client states.
- **Scanner:** lock-protected scan/repair flags, background thread, cancellation event, playback condition, worker pool and SQLite cache (`scanner.py:729-765`).
- **Repair:** one service lock serializes its own plans and mutations, but does not lock out external filesystem writers (`repair.py:1215-1239`, `repair.py:1259-1288`).
- **Batch:** explicit phases such as `idle`, `previewing`, `ready`, `applying`, undo and finalize phases; operations reserve the scanner's library-busy state and stop between packages (`batch_repair.py:131-164`, `batch_repair.py:344-380`).
- **Preview plans:** in-memory, maximum four, 30-minute TTL, content hashes (`preview_repair.py:54-59`, `preview_repair.py:597-708`, `preview_repair.py:728-783`).

### Primary data flow

```text
FeedBack host
  ├─ plugin loader → screen.html + CSS + screen.js
  └─ route setup → migration → scanner/cache
                         ├─ spawn workers → validator → read-only report
                         ├─ results/rules/export → browser
                         └─ repair reservation
                              ├─ RepairService → eligibility/planners
                              ├─ PreviewRepairEngine → FFmpeg → in-memory candidate
                              ├─ complete candidate validation
                              ├─ recovery ZIP
                              ├─ package commit
                              └─ history/cache refresh

BatchRepairManager → scan snapshot → per-package RepairService transactions
                  → checkpoint/result JSON → restart recovery UI
```

### Trust boundaries and side effects

1. Browser/API input crosses into local filesystem operations; routes currently accept raw dictionaries and domain services perform the real validation.
2. Feedpak/Sloppak contents are untrusted archive/YAML/JSON/audio input.
3. The configured library path and user-selected scope are trusted only after canonicalization and containment checks.
4. FFmpeg is an external process operating only on bounded temporary copies.
5. Worker processes are isolated from mutation services but not hard-limited by wall-clock time.
6. External editors, sync clients, antivirus and filesystem-junction changes are outside the plugin's locks and create TOCTOU risk.
7. Host logs can enter a user-created diagnostic bundle, so logged song identifiers cross a privacy boundary.

### Host constraints verified

FeedBack nightly supports native ES modules and directly serves plugin source modules. `docs/plugin-modules.md` in the adjacent nightly documents `"scriptType": "module"`, a thin `screen.js` entry, and `src/` modules without a bundler. `static/js/plugin-loader.js:5`, `static/js/plugin-loader.js:660` implements module loading. A framework or build pipeline is therefore unnecessary for useful modularization.

## 5. Safety-invariant verification matrix

Legend: **Verified** means directly supported by code and relevant tests; **Partial** means the normal path is protected but a stated edge remains; **Unverified** means the claim needs a new fault/adversarial test.

| Invariant | Status | Implementation evidence | Test evidence | Gap, consequence, and recommended verification |
|---|---|---|---|---|
| Scans remain read-only | Verified | Workers import only `validator.py` and never cache/repair state (`library_doctor_scan_worker.py:1-8`, `72-98`); validator only opens members for read (`validator.py:847-892`). | `tests/test_scan_worker.py` process/pause tests; scanner tests use disposable packages and compare cache behavior. | Static guarantee depends on future validator changes. Add a sandbox test that denies all writes under the package and runs every scan profile. |
| Paths stay in library/temp/config roots | Partial | Scope and package paths are canonicalized and checked with `relative_to`; symlinked packages/members are refused (`scanner.py:1610-1671`, `repair.py:2066-2102`, `repair.py:2708-2740`). | `test_repair_preview_rejects_ambiguous_manifest_and_package_traversal` (`tests/test_routes.py:2892`); unsafe member parameterization in `tests/test_repair.py:1270+`. | Checks precede final writes; an external parent-directory/junction swap is not rechecked in `_commit` (`repair.py:3194-3203`). Add concurrent junction/symlink swap tests and commit-time handle/containment guards. |
| Repair plan is bound to exact source and stale plans are rejected | Partial | Plan IDs digest source-bound plans; apply recalculates under lock and compares plan ID (`repair.py:1215-1226`); preview hashes manifest/full mix/current preview (`preview_repair.py:597-619`, `748-769`). | `tests/test_repair.py:1977`, `tests/test_routes.py:1988`, `tests/test_routes.py:2748`, `tests/test_preview_repair.py:301`. | A source may change after authoritative planning/validation but before commit. Add mutation hooks at every transaction boundary and a final compare-before-swap protocol. |
| Every repair is explicitly reviewed or confirmed | Verified for current UI/API flow | UI always performs preview/review then apply; batch has preview plus second confirmation (`screen.js:748-918`, `screen.js:2280-2785`). Plan IDs prevent direct application without a valid review artifact. | Contract tests around review/confirmation in `tests/test_plugin_contract.py:127+`; route apply rejects missing/invalid plans. | API is local and callable outside the UI, but still requires a valid plan. Add browser tests proving focus and confirmation state, including double-click/retry. |
| Only allowlisted deterministic transformations are automatic | Verified | Explicit catalog and dataclass action types (`repair.py:121-379`, catalog near `repair.py:868`); shared eligibility is conservative (`repair_eligibility.py:197-259`). | `test_catalog_is_an_explicit_allowlist` (`tests/test_repair.py:54`) and per-rule tests. | Preserve the catalog as a closed set. Add a meta-test mapping every catalog entry to planner, apply, ambiguity and route tests. |
| Ambiguous data is skipped, not guessed | Verified | Duplicate keys, JSONC/nonstandard JSON, ambiguous paths and handshape context are rejected; preview source requires an unambiguous declared full mix (`repair.py:2104-2127`, `repair_eligibility.py:53-68`, `preview_repair.py:494-551`). | Extensive blocker cases in `tests/test_repair.py` and `tests/test_routes.py`, including handshape/template ambiguity and duplicate manifest keys. | Good. Add generative near-miss cases around every automatic rule to guard future broadening. |
| Complete candidate is validated before commit | Verified | `_candidate` then `validate_feedpak` then `_verify_validation`, before `_create_backup` and `_commit` (`repair.py:1352-1379`). | Candidate-validation failure tests in repair/preview/routes suites. | “Complete” is strongest for archives; directory candidates preserve symlinks and rely on validator coverage. Add unchanged-member manifest checks for directory candidates too. |
| Archive integrity is preserved | Verified | Archive rebuild preserves order/metadata, compares unchanged size/CRC and runs `testzip()` (`repair.py:2797-2835`, `repair.py:2842-2897`). | Duplicate/colliding archive tests and candidate failure tests in validator/repair suites. | Good. Add fault injection for short reads and disk-full during archive candidate creation. |
| Backup/recovery is valid before replacement | Verified for normal exceptions | Backup metadata contains original/repaired hashes; ZIP is fsynced and atomically renamed before commit (`repair.py:2955-3033`). Reads enforce schema, count, size and hash integrity (`repair.py:3035-3130`). | Backup/Undo/integrity route tests and legacy backup tests. | Backup file itself is not reopened/tested after rename before commit. Add a post-write reopen/testzip/hash test plus corruption injection immediately before commit. |
| Writes are atomic or safely recoverable | Partial | Archive replacement and individual file writes use fsync + `os.replace` (`repair.py:3176-3249`); exception rollback restores committed directory members. | Normal save/restore behavior is covered; the actual exception branch `repair.py:3199-3224` is uncovered in the coverage report. | Multiple directory members are not one atomic transaction; abrupt death bypasses rollback. Add a durable journal and startup recovery plus process-kill tests after every member boundary. |
| Crash/process termination cannot silently leave ambiguous state | Partial | Batch checkpoints recover completed package receipts (`batch_repair.py:2579-2618`); backups exist before package commit. | `test_interrupted_batch_checkpoint_becomes_a_recoverable_result` and package-boundary checkpoint test (`tests/test_batch_repair.py:453`, `508`). | No forced-death test covers package commit or history-after-commit. A partially committed directory backup fails legacy receipt recovery's “all repaired” predicate (`repair.py:1578-1642`). Add subprocess kill/restart recovery tests and surface orphan/mixed backups. |
| Undo cannot overwrite unrelated later changes | Verified for tracked members | Undo re-reads current repaired members and requires exact repaired hashes; unrelated members are carried through the candidate (`repair.py:1904-2057`). | `test_restore_refuses_to_overwrite_chart_changes_made_after_repair` (`tests/test_routes.py:2698`) and batch equivalent (`tests/test_routes.py:2426`). | Same commit-time external race remains after the last hash check. Reuse the final-source guard/journal protocol for Undo. |
| Finalization cannot modify playable data | Verified | Finalization only verifies member state then deletes the recovery ZIP (`repair.py:1758-1883`, `repair.py:2666-2684`). | Single and batch finalization tests, including changed-package refusal (`tests/test_routes.py:2293`, `2732`). | Standalone finalization does not reserve scanner/batch busy state (`routes.py:560-593`). Add concurrency tests with simultaneous Undo/finalize. |
| Batch preserves package transaction boundaries | Verified for normal execution | Batch delegates each mutation to `RepairService` and checkpoints only completed outcomes (`batch_repair.py:1-6`, apply loop around `1390-1411`). | Partial success, stale skip, cancellation and checkpoint tests in batch/routes suites. | Abrupt death inside one unpacked package inherits the directory transaction gap. Test kill at each package/member boundary. |
| Cancellation stops at safe boundaries | Partial | Scanner uses checkpoints; batch cancellation is checked between packages; UI says “stop after current Feedpak” (`library_doctor_scan_worker.py:63-98`, batch state machines). | Scanner pause/cancel and batch cancellation tests. | A validator stuck between checkpoints cannot be hard-stopped; pool shutdown waits (`library_doctor_scan_worker.py:132-139`). Add hard deadlines and non-cooperative worker tests. |
| Retries/repeated requests are idempotent where needed | Partial | Scan start and busy reservations reject overlap; plan IDs become stale after mutation; restore/finalize validate current hashes. | Route conflict, stale plan and restart tests. | HTTP mutation endpoints do not accept idempotency keys; a lost successful response followed by retry returns a conflict rather than the original receipt. Add request IDs/receipt lookup for repair/apply/restore/finalize. |
| Changes during scan are detected | Verified | Signature is rechecked and validation retries up to two times (`scanner.py:1921-2417`, `MAX_SOURCE_CHANGE_RETRIES`). | `test_scan_retries_when_a_feedpak_changes_during_validation` (`tests/test_scanner.py:294`). | Signatures sample large files, so adversarial same-metadata/sample changes may evade cache signatures, though repair content checks are stronger. Add a documented threat model and randomized large-file mutation tests. |
| Changes during preview/repair/Undo/finalize are detected | Partial | Preview and stale pre-apply changes are hash-bound; Undo/finalize verify known member hashes. | Stale preview/apply/Undo/finalize tests cited above. | Final commit window is not guarded against concurrent external mutation. See LD-AUD-002. |
| Worker processes cannot write packages | Verified by construction | Worker imports only the read-only validator and receives path/package/profile tuples (`library_doctor_scan_worker.py:31-48`, `72-98`, `101-124`). | Spawn isolation/version tests in `tests/test_scan_worker.py`. | Add an OS-level write-denial test so future validator imports cannot silently broaden capability. |
| Hostile packages have bounded resource impact | Partial | Member, uncompressed, read, structure, YAML alias, text and audio limits exist (`validator.py:51-66`, `validator.py:657-915`); worker count is CPU/RAM bounded (`scanner.py:56-144`). | Member/YAML/structure/deep-audio limit tests in `tests/test_validator.py:2037+`. | No per-package CPU/wall-clock deadline or worker RSS enforcement; some long pure-Python loops have no checkpoint. Add adversarial time-budget tests and a kill/replace worker watchdog. |
| External process/temp handling is safe | Verified with residual pressure risk | FFmpeg is argument-list invoked with `-nostdin`, fixed options and 30/120/180-second timeouts inside `TemporaryDirectory` (`preview_repair.py:178-305`). Output candidate size is bounded. | FFmpeg command/timeout/failure tests (`tests/test_preview_repair.py:149+`). | `capture_output` buffers decoded loudness PCM until exit. Bound output duration/bytes or stream the analysis, and test timeout cleanup. |
| Migration fails closed | Verified | Both old/new directories or destination collisions raise before merging; atomic moves/writes are used (`migration.py:240-293`). | Conflict, interrupted rename, idempotence and byte-preservation tests in `tests/test_migration.py`. | Good. Preserve as a separately testable startup step. |
| Song information is not unintentionally included in support data | Not met | Scanner and batch warning/exception logs include relative package names (`scanner.py:2141-2165`, `scanner.py:2259-2263`, `batch_repair.py:1468-1471`, `batch_repair.py:1703-1706`). FeedBack diagnostic bundles can include raw server logs (adjacent host `tests/test_diagnostics_bundle.py:62-71`). | No plugin test asserts log redaction; `CLAUDE.md:24` explicitly says never include library paths or song identities in support bundles. | Error-only song identities can enter a support bundle. Log opaque hashes/counts by default or add host-aware redaction; test the end-to-end bundle. |

## 6. Test and quality-gate results

| Command | Result | Meaning |
|---|---|---|
| `python -m ruff check .` | Pass: `All checks passed!` | Only `E4`, `E7`, `E9`, and `F` are enabled (`pyproject.toml`), so this is syntax/import hygiene, not broad style or complexity analysis. |
| `python -m py_compile validator.py scanner.py library_doctor_scan_worker.py repair_eligibility.py repair.py preview_repair.py batch_repair.py migration.py routes.py` | Pass | All production Python modules compile. |
| `node --check screen.js` | Pass | Delivery JavaScript parses; it does not exercise behavior. |
| `python -m pip check` | Pass: no broken requirements | Installed distributions are mutually consistent. It is not a vulnerability audit. |
| `python -m pytest --cov --cov-report=term-missing` | Environment-invalid first run | Pytest could not traverse `C:\Users\vikto\AppData\Local\Temp\pytest-of-vikto` (`WinError 5`), causing setup errors. No assertion conclusion was drawn from this run. |
| `python -m pytest --basetemp C:\Users\vikto\Documents\Codex\2026-08-11\can\work\pytest-library-doctor-20260811 --cov --cov-report=term-missing` | **Fail: 344 passed, 1 failed; 84.99% coverage; 106.19s** | The single failure was `test_parallel_pool_start_failure_falls_back_to_sequential_validation`: expected `parallel_fallbacks == 1`, observed `0`. The machine had only 1.13 GB available RAM, so policy correctly selected one worker and never attempted the intentionally failing pool. The latest commit made one neighboring parallel test deterministic but not this one. Coverage then missed the configured 85% threshold by 0.01 percentage point. |
| isolated rerun of the failing scanner test with `-vv --tb=long` | Reproduced | Confirms an environment-sensitive test assumption, not a scan correctness failure: stage was complete and all three packages scanned, but the intended fallback branch was not entered. |

Additional warnings: pytest could not write parts of the repository's ignored `.pytest_cache` due `WinError 5`. The worktree remained Git-clean apart from this report; `.pytest_cache` and `.coverage` are ignored.

Coverage concentrations and gaps:

- Total: 84.99% (gate failure).
- `batch_repair.py`: 77%; several error, restart, cleanup and finalization branches are uncovered.
- `routes.py`: 80%; many error-contract branches are uncovered.
- `migration.py`: 80%.
- `repair.py`: rounded 85%, but notably the directory commit failure/rollback lines `3199-3224` are uncovered.
- `validator.py`: 88%; `preview_repair.py`: 90%; worker: 99%.
- There are no executable frontend unit tests, DOM tests, browser tests, keyboard tests, visual regressions, or automated accessibility checks. `tests/test_plugin_contract.py` verifies source strings and element IDs, which is valuable smoke coverage but cannot detect broken state transitions, races, focus, or announcements.

Passing backend tests meaningfully exercise the allowlist, ambiguity blockers, stale plan handling, archive safety, candidate validation, Undo, finalization, batch partial success, migration and several resource bounds. High line coverage should not be interpreted as proof of crash consistency or UX correctness.

## 7. Novice-user cognitive walkthrough

| Journey | Goal and present cognitive load | Primary action, predictability and recovery | Minimum-complexity novice path | Advanced disclosure to retain |
|---|---|---|---|---|
| 1. First opening | Learn “what this does” and “is it safe.” The lead immediately introduces Feedpak, Sloppak, deterministic issues, Song Tools and confirmation (`screen.html:4-10`), followed by scope, deep audio, empty summaries, filters and long policy prose. | “Scan whole library” exists, but is not the only visually active concept. Scan safety is stated, yet the mechanism-heavy prose asks for trust through reading. | One sentence: “Check your song library for problems. Scanning never changes songs.” One dominant **Scan my library** button. | “Choose a different scope,” Deep Audio and performance under **Scan options**. Link to “How safety works.” |
| 2. Safest whole-library scan | Start the recommended scan. Whole library and automatic workers are sensible defaults. | The primary scan button is clear; “Recheck without cache” appears only after results, which is good. Deep Audio competes too early. | Accept default scope and click **Scan my library** within 30 seconds. | Folder/file scope, Deep Audio, cache bypass and worker ceiling. |
| 3. Folder or package | Limit scope without knowing package internals. “Single Feedpak” assumes vocabulary and a desktop picker. | Selecting a radio reveals a picker and can be backed out safely. The user must understand recursive folders and extensions. | Secondary **Scan options** → “Whole library / A folder / One song package.” | Show `.feedpak`/`.sloppak` extensions and resolved path after selection. |
| 4. Progress/pause/cancel/incomplete | Know whether scanning is working and whether playing is safe. Current copy distinguishes discovering, paused, cancelling, incomplete and ETA (`screen.js:513-600`). | Cancel says the current package finishes and completed reports remain; this is predictable. “Paused while a song is open” is good. | One status card with phase, count, ETA, and **Stop scan**. After stop: “Partial results; scan again to complete.” | Workers, cache reuse, deep-audio profile, raw provenance. |
| 5. Completed dashboard | Answer “is my library okay and what should I do?” Current UI shows batch action before eight summary cards, a long overlap explanation, rule aggregation and packages. | No single next action dominates. “Warnings only,” “Needs attention,” optional coverage and authoring review overlap. | Outcome headline: “1 song needs attention,” severity breakdown, then the ordered package list. | Coverage categories, rule aggregation, export and scan provenance in expandable details. |
| 6. Distinguish issue types | Understand urgency without package expertise. Current terms “FeedBack compatibility,” “authoring review,” optional lyrics/previews and partial deep audio need a paragraph to decode. | Color is supplemented by text, but labels do not consistently communicate action/urgency. | Three top-level groups: **Needs fixing**, **May affect FeedBack**, **Optional improvements**. | Exact severity, rule code, authoring category and coverage mechanics. |
| 7. Find most important package | Open the package most likely to affect play. Packages default to “Needs attention,” which is good, but rule filters, summary filters and nine chips create three ways to filter. | Importance ordering is not explicit; warnings and errors can be mixed. | One default list sorted errors → compatibility → warnings → review, with search. | Alternate sorts and rule filters in **Filter & export**. |
| 8. Understand one finding | Know what happens in game and whether it can be fixed. Finding cards provide title, explanation, technical location and grouped evidence. | This is one of the stronger flows. Technical rule/location still appears early and repeated repair buttons can crowd a package. | Show **What this means**, **Recommended action**, and repair availability. | Rule ID, exact path/location, affected count, raw evidence. |
| 9. Apply one safe repair | Preview exact change and decide. Current review cards explain player result, value, file handling and include an explicit apply/cancel. | Generally predictable and reversible for chart repairs. Dynamic confirmation is inline with no focus movement, so keyboard/screen-reader users may miss it. | Review “Changes / Stays the same / Undo / If validation fails,” then one **Apply safe repair**. | Detailed counts, validation evidence, performance and technical plan metadata. |
| 10. Understand result | Answer “what happened?” and “what next?” Result cards explicitly contain What happened, in-game expectation, value and file handling. | Content is strong but verbose and persists above unrelated workspaces. “Show this package” is useful. | Concise success headline, exact changed count, validation result, **View song**, **Undo** if available, **Done**. | Four detailed explanation panels and performance timing. |
| 11. Undo/finalize | Recover or discard recovery data. Current receipts surface Undo and irreversible finalization with checks. | Safety semantics are strong, but “finalize recovery copy” is technical and can be mistaken for finishing the repair. | **Undo repair** and secondary **Delete Undo backup…**, with plain irreversible explanation. | Backup size, hashes/state and batch cleanup controls. |
| 12. Batch repair | Safely act on many reviewed packages. Batch currently appears before the result summary and offers optional automatic preview repair in the same area. | Preview and second confirmation are strong. A novice may enter mass changes before understanding individual findings. Earlier packages remain changed if a later one fails; this is disclosed in dense copy. | Show batch only after results, beneath “Review affected songs.” Default off; **Review safe repairs for N songs**. | Package/rule lists, preview opt-in, sort/filter outcomes, pause/cancel/checkpoint details. |
| 13. Gameplay pause/resume | Play without scan/repair contention. Playback priority is automatic and explained. | Good: scan/batch waits at safe boundaries and notice communicates pause. Repair may be blocked while playback has priority. | No decision required; status says “Paused while you play; resumes automatically.” | Worker/playback telemetry and explicit retry details. |
| 14. Preview Creator | Find a song and optionally replace its preview. Runtime showed 24 songs per page across 4,399, an inline selected-song card and two creation choices. | Search/pagination are understandable. “Create automatically and finish” is visually primary even though it skips listen-first review and has no retained Undo after success. A prior repair receipt remained above this workspace. | Search → choose song → **Listen and choose a preview** as recommended; automatic creation secondary. | Start time, selection reason, current/candidate sizes and one-click automatic path. |
| 15. Error/stale/interrupted recovery | Know whether anything changed and the next safe step. Backend errors often include `file_state`, and copy distinguishes unchanged/verify required/recovery required. | UI renders errors but contracts vary and there is no unified recovery center. Some rare recovery backups can become orphaned from normal history after a crash. | Standard status pattern: “Nothing changed / Change completed / State uncertain,” then one recommended next action. | Error code, technical cause, backup ID, rescan and manual recovery instructions. |

Acceptance summary:

- First-open purpose and read-only scanning are present but require too much reading.
- The recommended scan can be started quickly, but the default screen does not consistently maintain one dominant next action after history/results exist.
- Advanced performance settings are already disclosed well; Deep Audio, batch, exports, rule aggregation and recovery internals need similar staging.
- Single-repair preflight and result copy are substantively good and should be shortened, not removed.

## 8. UX and information-architecture findings

### Highest-impact issues

1. **The screen reflects system capabilities, not the user's current step.** Static order is repair receipt → scan configuration → status → batch → eight-card summary → long safety/coverage paragraph → rules/exports → packages/filters (`screen.html:19-240`). The live populated state exposed 57 controls and 39 visible buttons.
2. **Scan configuration and results compete permanently.** After a complete scan, the full three-scope panel and Deep Audio choice remain above results. It should collapse to a compact “Last scan / Change scope / Scan again” header.
3. **Batch is promoted before comprehension.** “Safe batch repair” appears above the summary and package list (`screen.html:102-136`). Batch belongs after the user understands what needs attention, or behind an explicit “Repair multiple songs” action.
4. **Filtering is duplicated.** Eight summary buttons (`screen.html:138-170`), a rule-button grid (`191-205`) and nine package filter chips (`219-229`) all alter the same result set. Users must learn overlapping categories and three filter mechanisms.
5. **The explanation for the summaries is evidence that the summaries are too complex.** The 150+ word paragraph at `screen.html:173-188` explains overlaps, compatibility, review, coverage, repairs, backups, previews and Song Tools in one block.
6. **Global history intrudes on unrelated tasks.** `lh-repair-result` is outside both workspaces (`screen.html:19-21`, `241-243`). Runtime confirmed the “Karate” Undo receipt appeared above an unrelated Preview Creator flow.
7. **Technical terms appear before user intent.** Feedpak, Sloppak, cached reports, Deep Audio, worker, deterministic, authoring review, transaction, recovery copy and finalization are accurate but not novice-first.

### Terminology recommendations

| Current label | Proposed label | Rationale |
|---|---|---|
| Health Scan | Library check | Familiar outcome rather than technical mechanism. Keep “scan” in helper text. |
| Whole library | All songs (recommended) | A user thinks in songs, not the storage root. |
| Single Feedpak | One song package | Avoid requiring the extension vocabulary before selection. |
| Deep audio checks | Thorough audio check | State the user-visible tradeoff: slower, reads audio, use when audio problems are suspected. |
| Recheck without cache | Scan everything again | Explains effect; put under More actions. |
| Checked | Songs checked | Removes ambiguity. |
| With errors | Must fix | Use only for conditions that block or credibly break behavior. |
| Warnings only | May cause problems | Conveys consequence instead of implementation severity. |
| Authoring review | Check if intentional | Directly communicates musical judgment. |
| Without lyrics / previews | Optional: no lyrics / no preview | Prevents optional coverage from reading as damage. |
| Partial deep audio | Some audio not fully checked | Self-explanatory coverage statement. |
| Safe batch repair | Repair multiple songs | “Safe” should be evidenced in the review, not used as the noun. |
| Finalize recovery copy | Delete Undo backup… | Makes irreversibility and actual effect explicit. |
| Cached package reports | Previous scan results | User language; keep cache details in diagnostics. |
| Song Tools | Preview tools | Today this workspace exposes only Preview Creator; rename again if more tools are added. |

### Guided flow versus dashboard

A mandatory wizard would hide useful comparison and make returning users repeat steps. The better fit is a **simplified dashboard with guided states**:

- the shell changes emphasis based on `first-run`, `scanning`, `complete`, `repair-review`, and `outcome`;
- one dominant action per state;
- results remain directly searchable;
- scope, coverage, rule IDs, performance, exports and recovery internals stay accessible through disclosures;
- URL or in-memory state can open a specific package/repair without forcing linear navigation.

## 9. Proposed target UI structure and text wireframes

### Default hierarchy

1. State-specific headline and primary action.
2. Compact safety statement: scanning is read-only; repairs require review.
3. Current task content: scan progress **or** results **or** repair review—not all simultaneously.
4. Secondary actions relevant to that state.
5. Advanced evidence and tooling under disclosures.
6. Preview Tools as a distinct top-level workspace without global repair history above it.

### First-run state

```text
Library Doctor
Check your song library for problems. Scanning never changes your songs.

[ Scan all songs ]  (recommended)
[ Scan options ▾ ]  A folder · One song package · Thorough audio check

What happens?  Library Doctor reads each song package and reports what needs attention.
No repairs are made during a scan.

Secondary: Preview tools
Help: How Library Doctor keeps repairs safe
```

Visible by default: purpose, read-only assurance, recommended scope, one primary button. Hidden: worker controls, cache behavior, extensions, exports, batch, empty summary cards and empty filters.

### Active-scan state

```text
Checking all songs…                         328 of 4,399
[██████████████████----------------]        About 4 min left
Paused automatically while a song is open / Currently checking: …

[ Stop after this song package ]

Details ▾  Thorough audio: off · 4 workers · 219 unchanged results reused
```

### Completed-results state

```text
4 songs need attention
2 must fix · 2 may cause problems · 18 optional improvements

[ Review the most important song ]
Secondary: Repair multiple songs…   Scan again…

Search songs  [________________]
Filter: [Needs attention] [Must fix] [May cause problems] [Optional] [All]

1. Song / artist        Must fix       Short consequence
2. Song / artist        May cause…     Short consequence

Scan details ▾   Rule summary & export ▾   Coverage details ▾
```

The full scan configuration collapses to “All songs · completed at … · Change.” Batch is not a large permanent section.

### Single-repair review state

```text
Review repair — Song / Artist

Problem
20 identical notes can play twice.

Will change
Remove 20 exact duplicate entries in arrangements/lead.json.

Will not change
Timing, techniques, audio, lyrics, artwork, or any non-duplicate note.

Safety and recovery
The complete package will be checked before saving. If validation fails, nothing is replaced.
Undo available: Yes (original changed files are retained).

[ Apply safe repair ]  [ Cancel ]
Technical evidence ▾
```

### Batch-repair review state

```text
Review repairs for 12 songs — no changes yet
10 ready · 2 skipped because they need judgment

Repair types ▾     Songs included ▾     Skipped songs ▾
[ ] Also create missing/invalid previews (4) — no Undo after successful preview creation

Each song is saved separately. If a later song fails, earlier completed repairs remain.

[ Continue ]  [ Cancel ]

Confirmation step:
Apply reviewed repairs to 10 songs?
[ Apply to 10 songs ]  [ Go back ]
```

### Outcome/recovery state

```text
Repair complete
20 duplicate notes removed. The complete song package passed validation.

[ View song ]  [ Undo repair ]  [ Done ]

Details ▾  Files changed · Backup size · Validation timing · Rule ID
More actions ▾  Delete Undo backup…
```

The receipt belongs in a dismissible activity/recovery area, not above Preview Tools forever.

### Preview Tools flow

```text
Preview tools
Search all local songs [________________]

Selected: Song / Artist
Current preview [audio]

Recommended: [ Listen and choose a replacement ]
Secondary:   [ Create a 30-second preview automatically… ]

Before apply: exact excerpt, full mix unchanged, candidate duration,
validation behavior, and “Undo is not retained after successful completion.”
```

### Copy placement

- Concise helper text: scanning read-only, scope recursion, gameplay pause, Undo availability.
- Tooltip or inline “?”: Feedpak/Sloppak, cached result, rule code.
- Expandable help: worker selection, Deep Audio internals, category definitions, archive/candidate mechanics.
- Documentation: complete safety model, schema coverage, limits and transaction design.
- Never hide an irreversible consequence only in a tooltip; keep it adjacent to the confirmation.

## 10. Frontend architecture findings

The concern about `screen.js` is verified. One IIFE owns global state (`1-54`), fetch/error normalization (`126-152`), playback coordination (`164-360`), scan rendering (`470-605`), batch flows (`618-1692`), repair/history workflows (`1692-2817`), Song Tools (`2821-3112`), result rendering/filtering (`3114-3327`), polling (`3328-3353`), scan commands (`3355-3400`), bindings and host lifecycle (`3402-3540`). Size alone is not the problem; coupled workflow state and lack of executable tests are.

Verified positives:

- dynamic user/package strings are created with `textContent`/node construction (`screen.js:607-611`); no `innerHTML`/`insertAdjacentHTML` use was found;
- result and Song Tools fetches use request IDs to reject stale responses (`screen.js:2896-2928`, `3307-3325`);
- binding has a DOM dataset guard (`screen.js:3402-3405`);
- timers are cleared on leave (`screen.js:3504-3509`);
- native elements are used for buttons, inputs, details and progress.

Risks:

- `refreshStatus`, `loadRules`, repair catalog and repair history lack activation-generation/request guards. An old request can leave a screen, re-enter, then be accepted because `state.active` is true again (`screen.js:3237-3273`, `3334-3352`, `3488-3501`).
- no `AbortController` is used, so obsolete requests still consume resources and can extend race windows;
- dynamic confirmations are implicit inline state machines duplicated across single repair, preview, batch, Undo and finalization;
- route strings, phase names and explanatory copy are duplicated in a delivery file and backend responses;
- `wire()` keeps host subscriptions without retaining unsubscribe handles (`screen.js:3525-3539`), a likely hot-reload/re-enable leak depending on host lifecycle;
- DOM and state are tightly coupled, making even basic state transition tests require the full host.

### Recommended delivery approach

Use **native ES modules served directly by FeedBack**, with a thin `screen.js` entry and no framework, TypeScript, bundler or runtime dependency. This is the least complex supported option and aligns with the adjacent host's documented module contract.

Options considered:

| Option | Assessment |
|---|---|
| Direct native modules | **Recommended.** Supported by current nightly, no build artifact drift, easy unit imports. Requires manifest `scriptType: module` and careful lifecycle extraction. |
| Source modules bundled to one file | Useful only if supporting older hosts that cannot load modules. Adds build/release complexity and artifact parity tests. |
| Namespaces in one classic script | Lower migration risk but only partly solves testing and hidden coupling. Reasonable temporary extraction step. |
| Keep one handwritten file | Lowest immediate cost, highest ongoing workflow regression risk; not recommended after characterization tests exist. |

## 11. Backend/API architecture findings

### What is working well

- Domain responsibilities are more separated than file sizes imply: validator is read-only; shared eligibility is mutation-free; preview generation returns plans; `RepairService` owns package writes; batch delegates per-package mutation; migration runs before current state opens.
- Scanner cache ownership remains in the parent process, while worker processes receive only validation tasks (`scanner.py:2066-2069`, `library_doctor_scan_worker.py:72-124`).
- SQLite uses a lock, WAL, a busy timeout and explicit transactions (`scanner.py:147-164`). Query/result pagination and indexed finding summaries are appropriate for large libraries.
- The archive transaction path is cohesive and strongly checked. Splitting `validator.py` or the pure repair planners solely by line count would add navigation cost without necessarily reducing risk.

### Material issues

1. **Routes are composition root, adapter and contract validator in one 558-line closure.** `setup()` constructs every service and defines every endpoint (`routes.py:60-617`). This makes isolated route contract testing harder and encourages ad hoc dictionaries.
2. **Request/response schemas are implicit.** Mutation routes accept `dict = Body(...)` and manually check selected keys (`routes.py:125-146`, `202-284`, `309-365`, `435-565`). Unknown fields are silently ignored and generated OpenAPI cannot describe the real contracts.
3. **Errors are structurally inconsistent.** Some endpoints return a string `detail`, repair errors return `{code,message,file_state}`, batch errors return `{code,message}`, and unexpected errors use separate shapes. The frontend's nested normalization (`screen.js:126-143`) hides this inconsistency rather than preventing drift.
4. **Transaction boundary does not include external source stability.** The service lock serializes Library Doctor, not other local writers. Backup and candidate safety are strong, but source comparison is not closed over the final swap.
5. **Standalone finalization does not acquire the scanner's repair/batch reservation** (`routes.py:560-593`), so backup deletion can race another operation even though package content is not directly changed.
6. **Presentation copy is embedded in domain receipts.** Fields such as `player_result`, `user_value` and long `file_handling.summary` are useful stable semantics, but complete UI sentences in planners and batch services couple wording changes to backend code. Preserve structured facts and move most prose to a shared copy/presentation layer.
7. **State recovery is uneven.** Batch has a restart checkpoint; single repair relies on history plus legacy backup discovery. That discovery intentionally surfaces only fully repaired states (`repair.py:1578-1642`), so a mixed interrupted directory commit is not a normal UI recovery case.

### Recommended backend direction

- Introduce small Pydantic request/response/error models at the route boundary without moving domain validation out of services.
- Standardize every error as `{code, message, file_state, retryable, next_action}`; retain HTTP status as transport meaning.
- Add a durable per-package transaction journal/recovery store that is written before commit, updated after each durable boundary, and reconciled at startup.
- Add commit-time source guards and containment checks immediately before each replacement; on platforms where compare-and-swap semantics are unavailable, use a guarded rename/journal protocol and explicit recovery state.
- Add a worker supervisor with deadline, forced termination, replacement and a bounded failure report.
- Keep validator rule families together until characterization or ownership needs justify extraction. If split later, separate package IO/budgets, schema validation and rule families behind a stable `validate_feedpak` facade.

## 12. Reliability, security, and recovery findings

### Final-source race

`_apply_internal` captures planned originals, validates the current package, builds and validates a candidate, creates a backup from the earlier captured bytes, then commits (`repair.py:1308-1379`). For normal non-Deep-Audio work there is no source guard after candidate validation. Even the Deep Audio guard is a check before `_create_backup`, leaving a smaller check-to-swap window (`repair.py:1365-1379`). A sync client or editor changing a member in that interval can have its newer bytes overwritten; Undo would restore the pre-race bytes from the recovery ZIP, not the intervening edit. This is a reasonable high-confidence inference from the ordering and is not covered by a mid-transaction mutation test.

### Unpacked multi-file crash consistency

Archives are replaced with one `os.replace`, but directories loop over replacements and atomically write or delete each member (`repair.py:3194-3203`). Python exceptions trigger reverse rollback (`3204-3233`); power loss or process termination cannot execute it. A complete backup exists, but history is recorded only after commit (`repair.py:1447-1469`) and legacy discovery requires every tracked member to match its repaired hash (`repair.py:1603-1613`). The likely restart state is therefore a mixed package plus a recovery ZIP not shown as a normal Undo receipt. The backup makes manual recovery possible, so this is P1 rather than P0.

### Non-cooperative workers

Workers check pause/cancel only where validator code calls a checkpoint. A stuck parser/loop/native read can ignore the event. `ValidationProcessPool.shutdown()` calls executor shutdown with `wait=True` (`library_doctor_scan_worker.py:132-139`), while scanner finalization calls it without a deadline (`scanner.py:2290-2308`). This can leave a scan permanently “running” and prevent subsequent scan/repair work. Process isolation limits damage to the host process but does not provide termination.

### Resource and archive posture

Resource limits are broad and thoughtfully layered: maximum 50,000 members, 64 GiB declared uncompressed content, 4 GiB total reserved reads, 2,000,000 parsed structure values, 100 YAML aliases, bounded text/audio members and finding counts (`validator.py:51-66`, `validator.py:723-915`). They prevent straightforward unbounded reads but are ceilings, not service-level objectives. CPU complexity and RSS are not measured or terminated. A pathological package should be tested against an explicit time and memory budget, not merely a structural ceiling.

### Logging/privacy

The plugin documentation says support bundles must not contain song identities (`CLAUDE.md:24`; `README.md:442`). Error logs nevertheless include relative package names. FeedBack can package the raw server log when logs are selected. Replace identifiers with an ephemeral scan ordinal or keyed digest in support-facing logs; keep full local identifiers only in an explicitly non-exported debug channel if the host can guarantee that separation.

### Overall security view

No remote-code-execution, command-injection, unsafe HTML injection, arbitrary archive extraction or obvious path-traversal flaw was found. FFmpeg uses an argument array, fixed flags, `-nostdin`, timeouts and temporary copies. YAML loaders are safe and duplicate/alias constrained. The highest security-adjacent risk is local filesystem TOCTOU/containment at commit, not untrusted content execution.

## 13. Accessibility findings

### Verified from source and the live accessibility tree

- The plugin places `<main class="lh-shell">` inside the host's existing main landmark (`screen.html:1`). Runtime exposed three `main` landmarks and a nested Library Doctor main. Use a labeled `section`/`div`, leaving one document main.
- Health Scan and Song Tools are navigation buttons with `aria-selected`, but there is no `tablist`, `tab`, `tabpanel`, `aria-controls`, arrow-key behavior or roving focus (`screen.html:14-17`, `screen.js:2821-2831`). Either implement the complete tabs pattern or use ordinary links/buttons with `aria-pressed`/current semantics.
- Runtime exposed 17 live/status/alert regions, including entire result, rule, batch and Song Tools containers. Three dynamically created repair regions were also live. Replacing a 50-package result list can generate long or nested announcements (`screen.html:126-135`, `202`, `231`, `262-273`; `screen.js:2308`, `2446`, `2991`, `3069`). Announce concise counts/status only.
- No call to `.focus()` exists in `screen.js`. Inline confirmation, cancellation, result insertion and disclosure changes do not move or restore focus (`screen.js:858-878`, `1023-1043`, `2717-2753`). A keyboard or screen-reader user can activate “Continue,” have it disabled, and remain unaware that new confirmation buttons appeared later in the DOM.
- Confirmation containers have no dialog, alertdialog, labeled group or heading semantics. A modal is not required, but an inline `role="group"`/region with a heading, deliberate focus entry and focus restoration is.
- Custom visible focus is defined only for buttons and inputs (`assets/library-doctor.css:612`), not selects, disclosure summaries or audio controls. Browser defaults may remain, but consistency/contrast was not guaranteed.
- The Song Tools result uses `role=list` and `role=listitem`, then inserts the selected tool region inside the selected item (`screen.js:2840-2871`). This is legal but yields a very large list item containing headings, audio and actions. Moving selection details adjacent to the list would simplify navigation.
- Button labels and text accompany color, native radios/checkboxes are labeled, progress has an accessible name/value, and dynamic strings use text nodes. These are strengths.

### Manual confirmation still required

- Contrast under every FeedBack theme/token override and Windows high-contrast mode.
- Zoom/reflow at 200%, 300% and 400%, especially badge clusters and rule counts.
- NVDA/JAWS announcement behavior during polling and large result replacement.
- Native audio-control naming; the current `<audio>` relies on nearby “Current preview” text rather than an explicit accessible name (`screen.js:2964-2972`).
- Keyboard behavior through long inline repair and batch result cards.

## 14. Test-strategy findings

### Existing risk coverage

| Layer | Strong coverage | Important gaps |
|---|---|---|
| Pure repair rules | Explicit catalog, exact duplicates, normalization/reordering, near-miss ambiguity, plan tampering, formatting preservation. | Meta-map proving every catalog rule has positive, negative, stale and property-preservation cases; property-based near misses. |
| Validator | Schemas, package/member/read/structure/YAML limits, traversal, duplicate/case-colliding archive members, deep audio and many rule families. | Wall-clock/RSS budgets, algorithmic worst cases, truncated streams during inspection, fuzz corpus regression. |
| Transaction | Candidate validation, archive integrity, backup hashes, Undo after changes, finalization safeguards. | Failure before/after every durable boundary; kill/power-loss simulation; mixed directory startup recovery; commit-time concurrent mutation and junction swap. |
| Scanner/worker | Cache/signature, retry on mutation, process isolation, pause/cancel, fallback, restart last-scan state. | Non-cooperative/hung worker kill; worker OOM/crash replacement; deadline; database lock storm; test determinism under low memory. |
| Batch | Partial success, safe stop, preview partial failure, checkpoints, restart recovery, batch Undo/finalize. | Forced process death between package completion and checkpoint; crash within a package; concurrent standalone finalization. |
| API | Extensive FastAPI integration with disposable packages and real services. | Typed schema snapshots, unknown fields, uniform error shape, request retry/idempotency, malformed-body matrix. |
| Frontend | Source/markup contract assertions. | Actual state transitions, stale requests, polling, DOM rendering, keyboard, focus, announcements, error/empty/stale/partial states, host lifecycle. |

### Recommended layered strategy

1. **Pure unit tests:** keep existing validator/planner tests; add reducer/state-machine tests for browser state and pure view-model/copy functions.
2. **Generative tests where valuable:** generate exact duplicate/near-duplicate events, safe relative paths, archive member collisions and JSON/YAML structures around budgets. Assert preservation of all non-target bytes/properties.
3. **Transaction/fault-injection tests:** inject failure after candidate validation, backup fsync/rename, each directory member write/delete, archive swap, history write and backup cleanup. Run the repair in a subprocess and terminate it at each named barrier, then construct services again and verify deterministic recovery.
4. **API integration:** keep disposable packages; add contract models/snapshots and consistent error assertions.
5. **Frontend unit tests:** use Node's built-in test runner plus a small DOM implementation only if needed. Test API normalization, state transitions and rendering modules without adopting a framework.
6. **Browser/host integration:** Playwright against FeedBack nightly for first-run scan shell, result filtering, one repair review/cancel, batch review/cancel, Song Tools, stale response, keyboard focus and live-region behavior. Use synthetic libraries only.
7. **Critical end to end:** a handful of synthetic journeys: scan → review → repair → Undo; scan → batch preview → partial success → restart; Preview Creator review/cancel; interrupted directory transaction recovery.

Required CI profiles: normal resources; forced one-worker/low-memory; at least one Windows run for spawn and filesystem semantics; Linux for portable behavior. Tests that require parallelism must inject deterministic policy inputs rather than inspect live RAM.

## 15. Prioritized findings table

| ID | Category | Severity | Confidence | Classification | Summary |
|---|---|---|---|---|---|
| LD-AUD-001 | UX/IA | P1 | High | Verified issue | Populated Health Scan exposes too many simultaneous actions and no stable dominant next step. |
| LD-AUD-002 | Safety/reliability | P1 | High | Reasonable inference | Concurrent source changes after validation can be overwritten at final commit. |
| LD-AUD-003 | Recovery | P1 | High | Reasonable inference | Abrupt death during unpacked multi-file commit can leave mixed state not surfaced by normal recovery history. |
| LD-AUD-004 | Reliability/performance | P1 | High | Verified issue | Worker cancellation is cooperative and shutdown can wait forever; no hard package deadline/termination exists. |
| LD-AUD-005 | Testability/frontend | P1 | High | Verified issue | Safety-critical frontend workflows have no executable JS/browser/accessibility tests. |
| LD-AUD-006 | Frontend architecture | P2 | High | Verified issue | One IIFE concentrates state, transport, rendering, polling and all workflows; some async paths lack generation guards. |
| LD-AUD-007 | UX/IA | P2 | High | Verified issue | Scan configuration, prior receipt, batch and results compete; receipt leaks across workspaces. |
| LD-AUD-008 | UX/content | P2 | High | Verified issue | Three overlapping filter systems and expert terminology make issue priority hard to understand. |
| LD-AUD-009 | Accessibility | P2 | High | Verified issue | Nested main, incomplete tab semantics, no focus management and excessive live regions impair navigation/announcements. |
| LD-AUD-010 | API/maintainability | P2 | High | Verified issue | Raw request dictionaries and inconsistent error shapes make contracts implicit and drift-prone. |
| LD-AUD-011 | Privacy/observability | P2 | High | Verified issue | Error logs contain song/package identities that can enter FeedBack diagnostic bundles. |
| LD-AUD-012 | Tests/DevEx | P2 | High | Verified issue | Current commit's quality gate fails under low memory; coverage is 84.99%, below policy. |
| LD-AUD-013 | Safety tests | P2 | High | Verified issue | No systematic fault-injection/process-death/database-lock/mid-commit mutation suite protects safety claims. |
| LD-AUD-014 | Preview UX | P2 | Medium | Improvement opportunity | Preview Tools visually prioritizes automatic no-retained-Undo completion over listen-first review. |
| LD-AUD-015 | UI polish | P3 | High | Verified issue | Static batch progress contains mojibake (`Preparing batch previewâ€¦`). |

### Detailed finding dossiers

#### LD-AUD-001 — The default populated screen has no stable dominant task

- **Category / severity / confidence / type:** UX/IA; P1; high; verified issue.
- **Evidence:** `screen.html:19-240`; live FeedBack accessibility tree showed 57 visible controls and 39 visible buttons, with receipt, scan, batch, eight summary actions, rules/exports and nine filters.
- **Description:** The interface exposes nearly every capability at once and orders system mechanisms ahead of the user's next decision.
- **Impact and scenario:** A worried novice sees a prior restore, “Safe batch repair,” warnings, missing lyrics, Deep Audio, rule IDs and exports before understanding which song needs attention. They may avoid the plugin or enter batch repair prematurely.
- **Recommended change / benefit:** Implement the state-guided simplified dashboard in section 9. It reduces cognitive load while preserving advanced controls through disclosures.
- **Effort / regression risk:** Medium; medium risk because visibility/order changes can hide existing affordances.
- **Verification:** five-second comprehension test, first-scan task within 30 seconds, browser tests for every state and expert-action discoverability.
- **Dependencies:** characterization browser tests (LD-AUD-005); copy/IA work can precede module extraction.

#### LD-AUD-002 — Exact-source binding does not extend through final commit

- **Category / severity / confidence / type:** safety/reliability; P1; high; reasonable inference.
- **Evidence:** source captured at `repair.py:1308-1318`; candidate validation `1352-1364`; optional guard `1365-1369`; backup/commit `1370-1379`; actual swaps `3176-3249`.
- **Description:** Other local processes are not covered by the service lock and can change source after the last validation/check but before replacement.
- **Impact and scenario:** A sync client or editor saves a chart during repair; Library Doctor replaces it with the earlier planned candidate and Undo restores even older bytes, losing the concurrent edit.
- **Recommended change / benefit:** Introduce final per-member/package source guards, commit-time containment checks and a guarded rename/journal protocol. This closes the most important data-loss window.
- **Effort / regression risk:** High; high risk because transaction semantics and Windows filesystem behavior change.
- **Verification:** deterministic mutation injection after every boundary for archive/directory apply, preview, Undo and batch; assert no unrelated bytes are overwritten.
- **Dependencies:** transaction journal design from LD-AUD-003 and fault-injection harness LD-AUD-013.

#### LD-AUD-003 — Directory-package commit is not crash-atomic or restart-reconciled

- **Category / severity / confidence / type:** recovery; P1; high; reasonable inference.
- **Evidence:** sequential directory writes `repair.py:3194-3203`; exception-only rollback `3204-3233`; history after commit `1447-1469`; legacy recovery requires all repaired hashes `1578-1642`.
- **Description:** Abrupt death can leave a mixture of old and repaired files. The valid recovery ZIP may not appear as a normal receipt on restart.
- **Impact and scenario:** Power loss after the first of three member replacements leaves a package internally inconsistent; the user sees neither a completed repair nor an actionable Undo entry.
- **Recommended change / benefit:** Durable transaction journal with states and per-member progress; startup reconciliation that detects mixed state and offers verified restore. This makes recovery deterministic.
- **Effort / regression risk:** High; high.
- **Verification:** subprocess kill at each member/delete/history boundary, restart service, verify automatic or explicit recovery and byte-for-byte preservation.
- **Dependencies:** LD-AUD-002 and LD-AUD-013.

#### LD-AUD-004 — Hung validation cannot be forcibly stopped

- **Category / severity / confidence / type:** reliability/performance; P1; high; verified issue.
- **Evidence:** cooperative `_checkpoint` only (`library_doctor_scan_worker.py:63-98`); executor `shutdown(wait=True)` (`132-139`); scanner waits during cleanup (`scanner.py:2290-2308`).
- **Description:** No wall-clock deadline, RSS limit or kill/replace mechanism exists for a non-cooperative task.
- **Impact and scenario:** A pathological JSON structure or stuck native read never reaches a checkpoint; Cancel remains pending and all future scans/repairs stay blocked until FeedBack restarts.
- **Recommended change / benefit:** Supervisor deadline, forced process termination, replacement and bounded `package.validation-timeout` report. Cancellation becomes predictable under hostile input.
- **Effort / regression risk:** Medium-high; medium, especially on Windows spawn.
- **Verification:** intentionally non-cooperative worker, timeout, cancel, pool replacement and subsequent successful package validation on Windows/Linux.
- **Dependencies:** explicit service-level time/memory budgets; scanner status/error contract.

#### LD-AUD-005 — Frontend workflows are not executed by tests

- **Category / severity / confidence / type:** testability/frontend; P1; high; verified issue.
- **Evidence:** repository contains only Python tests; `tests/test_plugin_contract.py` asserts strings/IDs (for example `:114-321`); no Playwright, jsdom, Node test, axe or equivalent configuration was found.
- **Description:** Parsing and source-string tests cannot exercise confirmation, polling, stale responses, focus, event lifecycle or rendering.
- **Impact and scenario:** A “simplification” leaves an apply button disabled after Back, an older results response overwrites a newer filter, or focus disappears; CI remains green.
- **Recommended change / benefit:** Add state/view-model unit tests and a small Playwright host suite before major UI reorder/extraction. Enables safe UX improvement.
- **Effort / regression risk:** Medium; low product risk, moderate CI maintenance.
- **Verification:** tests fail on deliberately broken state transition/focus/request generation; stable synthetic host fixtures.
- **Dependencies:** define target UI states and module seams.

#### LD-AUD-006 — Frontend concentration and incomplete async invalidation raise change risk

- **Category / severity / confidence / type:** frontend architecture; P2; high; verified issue with one inferred race.
- **Evidence:** `screen.js:1-3540`; state `9-54`; unguarded loads `3237-3273`, `3334-3352`; activation lifecycle `3488-3509`; no `AbortController`.
- **Description:** Transport, state, rendering and workflows share mutable closure state. Some requests use IDs, others accept any response while `state.active` is true, including after leave/re-enter.
- **Impact and scenario:** A slow pre-navigation rules/status response returns after re-entry and repaints stale state; extraction or copy changes touch unrelated workflows.
- **Recommended change / benefit:** Native modules, activation generation/abort signal, explicit reducers/state transitions and shared confirmation/status primitives.
- **Effort / regression risk:** Medium-high; medium if done incrementally after tests.
- **Verification:** stale-response/leave-reenter tests, lifecycle subscription counts, timer cleanup, module dependency lint.
- **Dependencies:** LD-AUD-005; host module manifest update.

#### LD-AUD-007 — Workflow state and unrelated history compete across workspaces

- **Category / severity / confidence / type:** UX/IA; P2; high; verified issue.
- **Evidence:** global receipt `screen.html:19`; full configuration `22-89`; batch before summary `102-138`; Song Tools starts `243`; runtime showed the Karate receipt above an unrelated selected Preview Creator song.
- **Description:** Current task, previous outcome and alternate tool workspace are not spatially separated.
- **Impact and scenario:** A user entering Preview Tools believes the prior repair receipt relates to the selected song; completed-scan users scroll past configuration/batch to reach results.
- **Recommended change / benefit:** Scoped activity/recovery drawer, collapsible completed-scan header, batch entry within results, workspace-specific content only.
- **Effort / regression risk:** Medium; medium.
- **Verification:** state-by-state screenshots/accessibility trees and cross-workspace navigation tests.
- **Dependencies:** LD-AUD-001 and LD-AUD-005.

#### LD-AUD-008 — Result categories and terminology require expert interpretation

- **Category / severity / confidence / type:** UX/content; P2; high; verified issue.
- **Evidence:** eight summary cards `screen.html:138-170`; explanatory paragraph `173-188`; rules `191-205`; nine chips `219-229`; long technical labels throughout.
- **Description:** Overlapping coverage and health concepts are represented as peer actions, then explained with jargon.
- **Impact and scenario:** “Without lyrics” beside “With errors” makes optional coverage appear broken; selecting a rule then a chip creates a filter state the user cannot easily describe.
- **Recommended change / benefit:** One urgency model, one visible filter group, novice labels from section 8, technical evidence under details.
- **Effort / regression risk:** Medium; low-medium.
- **Verification:** terminology comprehension interviews and deterministic filter-state tests.
- **Dependencies:** backend categories must remain stable; frontend view-model mapping.

#### LD-AUD-009 — Landmark, tab, focus and live-region behavior is incomplete

- **Category / severity / confidence / type:** accessibility; P2; high; verified issue.
- **Evidence:** nested main `screen.html:1`; pseudo-tabs `14-17`, `screen.js:2821-2831`; 17 runtime live/status regions; no `.focus()`; CSS focus selector `assets/library-doctor.css:612`.
- **Description:** The page exposes invalid/incomplete tab semantics, nested landmarks, no dynamic focus management and potentially verbose announcements.
- **Impact and scenario:** A screen-reader user activates Continue, stays on a disabled button and does not discover the new confirmation; result refresh announces dozens of package cards.
- **Recommended change / benefit:** One main landmark, complete tabs or plain navigation, focus-entry/restoration rules, concise status live regions, visible focus for all controls.
- **Effort / regression risk:** Medium; low-medium.
- **Verification:** keyboard scripts, axe/static checks, NVDA manual journeys, live-region announcement assertions.
- **Dependencies:** shared accessible primitives and LD-AUD-005.

#### LD-AUD-010 — API contracts and errors are implicit and inconsistent

- **Category / severity / confidence / type:** API/maintainability; P2; high; verified issue.
- **Evidence:** raw bodies across `routes.py:125-146`, `202-284`, `309-365`, `435-565`; error adapters `195-196`, `286-291`; frontend normalization `screen.js:126-143`.
- **Description:** Manual checks cover important keys but do not define full request/response schemas or a uniform error contract.
- **Impact and scenario:** A backend field changes type or error shape; one UI workflow displays “Request failed (409)” while another shows the intended recovery action.
- **Recommended change / benefit:** Versioned Pydantic boundary models and one structured error schema with `file_state`, `retryable` and `next_action`.
- **Effort / regression risk:** Medium; medium due compatibility.
- **Verification:** OpenAPI/schema snapshots, malformed-body matrix, frontend contract fixtures and backward-compatibility tests.
- **Dependencies:** preserve existing route URLs and response facts during migration.

#### LD-AUD-011 — Support-facing logs can expose song identities

- **Category / severity / confidence / type:** privacy/observability; P2; high; verified issue.
- **Evidence:** package names logged at `scanner.py:2141-2165`, `2259-2263`, `batch_repair.py:1468-1471`, `1703-1706`; contrary instruction `CLAUDE.md:24`; host raw-log bundle evidence in adjacent `tests/test_diagnostics_bundle.py:62-71`.
- **Description:** Failure paths log relative song/package identities, which generic host redaction does not necessarily recognize.
- **Impact and scenario:** A user includes logs in a support bundle and unintentionally shares song titles/artists/library organization.
- **Recommended change / benefit:** Opaque operation/package IDs in normal logs, explicit opt-in local detail, and bundle-time plugin redaction.
- **Effort / regression risk:** Low-medium; low, but diagnostics usability must remain adequate.
- **Verification:** synthetic named package failure → build redacted support bundle → assert no title, artist or relative path.
- **Dependencies:** coordinate with FeedBack diagnostics/redaction API.

#### LD-AUD-012 — Current quality gate is environment-sensitive and failing

- **Category / severity / confidence / type:** tests/DevEx; P2; high; verified issue.
- **Evidence:** isolated run: 344 passed/1 failed, 84.99%; `tests/test_scanner.py:266-290` assumes parallelism, while `scanner.py:56-144` intentionally selects one worker under low memory. Latest commit only injected deterministic policy into a neighboring test.
- **Description:** Live host RAM changes whether the test reaches the fallback branch. Coverage fails by 0.01 point and pytest cache/temp permissions add noise.
- **Impact and scenario:** Release CI or a developer workstation fails while runtime behavior is correct; genuine failures are obscured by setup errors.
- **Recommended change / benefit:** Inject deterministic worker-policy inputs in every branch-specific test, provide a repository-local/CI basetemp, and raise meaningful coverage around error paths rather than lowering the threshold.
- **Effort / regression risk:** Low; low.
- **Verification:** run under forced low/high memory policy on clean Windows/Linux; gate passes deterministically.
- **Dependencies:** none; suitable early work after audit.

#### LD-AUD-013 — Safety claims lack systematic fault-injection evidence

- **Category / severity / confidence / type:** safety tests; P2; high; verified issue.
- **Evidence:** coverage misses `repair.py:3199-3224`; searches found no process-death, per-commit-boundary or database-lock tests. Existing checkpoint tests (`tests/test_batch_repair.py:453-548`) simulate persisted data rather than killing a transaction.
- **Description:** Normal exception and high-level restart tests do not demonstrate behavior when the process disappears between durable operations.
- **Impact and scenario:** A future refactor moves history/backup order or weakens rollback and still passes the suite.
- **Recommended change / benefit:** Named transaction barriers plus subprocess kill/restart tests; lock and concurrent mutation suites. Converts documented safety claims into executable invariants.
- **Effort / regression risk:** Medium-high test effort; low product risk.
- **Verification:** each barrier produces exactly one allowed state: unchanged, fully committed with receipt, or explicitly recoverable.
- **Dependencies:** transaction journal design and synthetic package factory.

#### LD-AUD-014 — Preview Tools emphasizes the less reversible path

- **Category / severity / confidence / type:** preview UX; P2; medium; improvement opportunity.
- **Evidence:** listen-first button is secondary while `Create automatically and finish` uses primary styling (`screen.js:2975-3001`); success removes temporary recovery and retains no Undo (`repair.py:1383-1421`); runtime confirmed the ordering.
- **Description:** Automatic selection is safe in package-integrity terms but subjective in excerpt quality and less reversible after finalization.
- **Impact and scenario:** A novice clicks the dominant automatic action without listening, dislikes the excerpt, and must create another rather than Undo.
- **Recommended change / benefit:** Make listen-and-review recommended; place automatic creation behind an explicit confirmation that states no retained Undo and offers immediate post-success playback/replacement.
- **Effort / regression risk:** Low; low.
- **Verification:** browser flow asserts wording, hierarchy, confirmation and post-success playback/replacement.
- **Dependencies:** target Preview Tools IA; no backend change required.

#### LD-AUD-015 — Static progress copy contains an encoding artifact

- **Category / severity / confidence / type:** UI polish; P3; high; verified issue.
- **Evidence:** `screen.html:128` contains `Preparing batch previewâ€¦` when read as UTF-8.
- **Description:** The ellipsis is mojibake in the initial batch progress text.
- **Impact and scenario:** A user briefly sees corrupted text while batch review starts, lowering perceived quality.
- **Recommended change / benefit:** Replace with a plain ASCII ellipsis or correctly encoded U+2026 and add UTF-8/text smoke coverage.
- **Effort / regression risk:** Trivial; very low.
- **Verification:** source encoding assertion and live rendered text.
- **Dependencies:** none.

## 16. Recommended target module structure

### Frontend: native modules, no build step

```text
screen.js                         module entry; host lifecycle only
src/constants.js                  route paths, phases, UI copy keys
src/api.js                        fetch, error normalization, AbortSignal support
src/store.js                      explicit state, activation generation, transitions
src/dom.js                        safe element helpers and focus primitives
src/status-view.js                progress/status/outcome announcement primitives
src/scan-controller.js            scope, scan commands, polling orchestration
src/results-controller.js         summaries, query/filter/pagination, result view models
src/finding-view.js               finding and technical-evidence rendering
src/repair-controller.js          single/all-safe review, apply, receipt, Undo/finalize
src/preview-controller.js         listen-first and automatic preview workflows
src/batch-controller.js           preview/confirm/apply/undo/finalize state machine
src/song-tools-controller.js      library search, selection, Preview Tools shell
src/playback-controller.js        host playback priority and notice
src/app.js                        composition, enter/leave, dependency wiring
```

Permitted dependency direction:

```text
screen.js → app → controllers → api/store/view modules
controllers → constants + domain-specific view
views → dom + view models (never fetch)
api → constants (never DOM/store)
store → plain data only
playback → host adapter + status primitive
```

No circular dependencies. Controllers receive dependencies explicitly. `screen.js` should be a small import/boot file. Host subscriptions return/store cleanup handles; every async operation receives an activation `AbortSignal` and generation.

Migration order:

1. Characterize current UI states in browser tests.
2. Extract constants and API/error normalization without changing rendering.
3. Extract store/activation generation and stale-request tests.
4. Extract DOM/status/focus primitives.
5. Extract scan/results, then single repair, preview, batch, Song Tools, and finally playback/lifecycle.
6. Change `plugin.json` to `scriptType: module` only when the entry/import smoke test passes in the supported nightly; declare an appropriate `minHost` when host enforcement/compatibility policy is clear.

Protect every extraction with before/after DOM snapshots for semantic structure, route request fixtures, state-transition tests and the critical synthetic host journeys. Keep each PR behavior-preserving; do not combine module movement with IA redesign.

### Backend: targeted seams, not a file-count exercise

```text
routes.py                         thin composition and APIRouter registration
api_models.py                     versioned requests/responses/error envelope
services/scanner.py               existing scan orchestration/cache facade
services/worker_supervisor.py     deadlines, kill/replace, worker telemetry
services/repair_service.py        planning/apply/restore public facade
transactions/journal.py           durable state machine and startup reconciliation
transactions/package_io.py        guarded archive/directory commit + containment
transactions/recovery_store.py    backup create/read/delete/history
services/preview_repair.py        existing write-free preview planning
services/batch_repair.py          existing per-package orchestration
validation/package_reader.py      only if resource/IO tests justify extraction
validation/validator.py           stable facade; rule families may remain together
repair_eligibility.py             keep shared and mutation-free
migration.py                      keep isolated and fail-closed
```

The first backend extraction should be the transaction journal/package IO because it supports a safety fix and fault tests. Do not split validation rules or pure planners during Phase 0.

## 17. Incremental implementation roadmap

### Phase 0 — Verified P1 safety and correctness issues

- **Scope:** add transaction fault harness; close final-source/containment race; add durable directory transaction journal and startup recovery; implement worker deadline/forced termination; make branch-specific scanner tests deterministic; restore the quality gate.
- **Why first:** these changes address credible overwrite/ambiguous-state and permanent-hang risks before UI work increases usage.
- **Prerequisite tests:** synthetic archive/directory factories; named commit barriers; process-kill/restart harness; non-cooperative worker; low/high deterministic worker policy.
- **User benefit:** repairs remain conservative even during sync/editor activity, abrupt shutdown and hostile input.
- **Risks:** Windows rename/locking semantics, false source-change refusals, recovery migration, terminating a worker during FeedBack shutdown.
- **Completion criteria:** every transaction barrier resolves to unchanged/committed/explicitly recoverable; no external edit is overwritten; Cancel has a bounded completion time; full gate passes on Windows/Linux.
- **Rollback:** feature-flag the new transaction protocol for synthetic validation, retain backward-compatible backup reads, and ship journal reconciliation before enabling guarded writes. Never roll back by deleting recovery data.

### Phase 1 — Low-risk UX simplification and terminology

- **Scope:** state-specific shell; collapse scan config after completion; move batch below results; consolidate filters; shorten lead/coverage copy; scope receipts; rename novice labels; make listen-first preview primary.
- **Why next:** highest weighted user concern, largely achievable without changing repair domain logic.
- **Prerequisite tests:** browser fixtures for first-run, cached complete, partial, stale, repair receipt, batch-ready and Song Tools; request snapshots.
- **User benefit:** one obvious next action, less jargon, clearer urgency and recovery.
- **Risks:** expert features become harder to find; hidden states regress.
- **Completion criteria:** novice starts recommended scan within 30 seconds, can explain scan safety and top result, every expert action remains reachable, accessibility tree is state-appropriate.
- **Rollback:** retain old layout behind a temporary development flag for one release; changes are presentation-only and can be reverted independently.

### Phase 2 — Characterization and contract tests

- **Scope:** executable frontend state/view tests, Playwright nightly harness, typed API/error contract fixtures, catalog-to-test coverage meta-map, support-bundle privacy test.
- **Why now:** locks the simplified behavior before code movement.
- **Prerequisite tests:** stable synthetic host/library seed and deterministic time/network helpers.
- **User benefit:** fewer regressions in confirmations, results and recovery.
- **Risks:** brittle snapshots and slow CI.
- **Completion criteria:** tests assert behavior/semantics rather than pixel-perfect markup; critical suite stays small and deterministic.
- **Rollback:** remove only flaky snapshots while retaining transition/contract assertions; never mute failing safety journeys.

### Phase 3 — Frontend responsibility extraction

- **Scope:** native modules in the order from section 16; activation generation/abort; shared accessible confirmation/status primitives.
- **Why now:** behavior is characterized and the host capability is verified.
- **Prerequisite tests:** Phase 2 green; module loader smoke test in minimum supported nightly.
- **User benefit:** indirect—faster, safer future improvements and fewer stale UI states.
- **Risks:** module load order, lifecycle cleanup, circular imports, old-host compatibility.
- **Completion criteria:** no behavior delta, dependency rules pass, `screen.js` is a thin entry, all workflows remain green.
- **Rollback:** one module extraction per PR; revert the most recent extraction without reverting prior tests or IA work.

### Phase 4 — Deeper reliability and backend improvements

- **Scope:** typed route models/error envelope, idempotent mutation receipt lookup, finalization reservation, worker/cache/database fault handling, optional structured presentation facts.
- **Why now:** Phase 0 closes urgent gaps; this phase improves contracts and rare failure behavior without blocking UX.
- **Prerequisite tests:** API schema snapshots, retry tests, concurrent finalize/Undo, SQLite lock recovery.
- **User benefit:** clearer recovery actions, safer retries, fewer “unknown” errors.
- **Risks:** client compatibility and receipt migrations.
- **Completion criteria:** all endpoints share one error contract; repeated requests return a safe stable outcome; lock/finalize races are deterministic.
- **Rollback:** version contracts and accept old/new responses during one transition release.

### Phase 5 — Accessibility, performance and developer-experience hardening

- **Scope:** NVDA/keyboard certification, contrast/zoom/forced-colors, live-region tuning, worker/RSS/time budgets, fuzz corpus, test temp/cache configuration, CVE automation and documentation refresh.
- **Why last:** builds on stable IA/modules/contracts while preserving a continuing accessibility gate from earlier phases.
- **Prerequisite tests:** automated semantics/focus checks, benchmark corpus and budget definitions.
- **User benefit:** dependable operation for assistive-technology users and pathological libraries; cleaner contributor workflow.
- **Risks:** platform-specific thresholds and false benchmark alarms.
- **Completion criteria:** WCAG-oriented manual checklist passes, budgets are measured in CI, clean checkout runs documented gates without environment workarounds.
- **Rollback:** thresholds can be tuned with evidence; do not remove semantic/focus assertions or hostile-input timeouts.

## 18. Regression and rollout strategy

1. **No big-bang rewrite.** Separate safety protocol, IA changes and module extraction into independently reviewable changes.
2. **Synthetic canary library.** Maintain small directory/archive packages for healthy, error, warning, ambiguity, optional coverage, Deep Audio partial, stale, repairable, preview and corrupt cases. Never run rollout tests on the user's library.
3. **Dual evidence for safety changes.** Require byte-level assertions plus user-facing `file_state`/receipt assertions.
4. **Startup compatibility.** New journals/receipts must tolerate old backups/history and fail closed on unknown versions. Test upgrades from current `0.34.0` state.
5. **Minimum-host matrix.** Once native modules are enabled, test the declared minimum FeedBack host and latest nightly. Refuse activation with a clear message on unsupported hosts.
6. **Telemetry without identity.** Record counts, durations, worker exits, phase and opaque package ID; never titles/relative paths in support-facing logs.
7. **Feature flags only for staged presentation/protocol activation.** Do not leave two permanent repair implementations. Recovery readers remain backward-compatible longer than writers.
8. **Release gates:** all static checks; deterministic 100% pass of 345+ tests; coverage at or above policy with error paths improved; critical Playwright suite; transaction kill suite; clean Git worktree.
9. **Rollback:** stop new writes before rollback, preserve all recovery/journal files, and provide a read-only reconciliation command/view. Never downgrade by deleting unknown state.

## 19. Important strengths and invariants to preserve

1. **Closed automatic repair catalog and conservative ambiguity handling.** Do not broaden eligibility to make more findings “fixable.”
2. **Candidate-first validation and archive integrity checks.** Full validation, unchanged-member CRC comparison and `testzip()` before replacement are excellent safeguards.
3. **Recovery before commit and hash-guarded Undo/finalization.** Keep exact original/repaired hashes and refuse to overwrite changed data.
4. **Read-only, process-isolated scanning with incremental cache.** Worker separation, SQLite scope/cache, source recheck and playback priority are strong foundations.
5. **Package-level batch delegation.** Batch should continue coordinating, never implementing a weaker write path.
6. **Shared eligibility predicates.** Scanner promises and authoritative repair planning use the same conservative logic.
7. **Safe DOM construction.** Continue using text nodes/native elements; do not introduce HTML string templates for untrusted package data.
8. **Bounded external tooling.** Keep fixed FFmpeg arguments, `-nostdin`, timeouts, candidate validation and temporary cleanup.
9. **Fail-closed migration.** Never merge ambiguous old/new state.
10. **Clear outcome facts.** “What happened,” “what stayed unchanged,” validation, Undo and recovery should remain available even when default copy becomes shorter.

## 20. Final recommendation

Do not ship `0.34.0` as a novice-ready production release yet. Treat it as a strong technical beta and complete Phase 0 plus the first UX slice before broader promotion. There is no need for a framework or backend rewrite. The appropriate path is targeted transaction hardening, deterministic safety tests, a state-guided dashboard, and native-module extraction protected by executable browser tests.

### Five most important changes

1. Close the final-source/containment race and add a durable, restart-reconciled transaction journal for unpacked packages.
2. Add hard worker deadlines/termination so hostile or stuck packages cannot block Library Doctor indefinitely.
3. Replace the capability-dense page with the state-guided simplified dashboard and one dominant next action.
4. Add executable frontend, keyboard/accessibility and critical host-journey tests before UI refactoring.
5. Standardize API errors/contracts and remove song identities from support-facing logs.

### Five most important existing strengths to preserve

1. Explicit deterministic repair allowlist with ambiguous cases blocked.
2. Complete candidate validation and archive integrity verification before commit.
3. Recovery data written first and exact-hash guarded Undo/finalization.
4. Read-only process workers, incremental SQLite cache and playback-aware cancellation boundaries.
5. Batch operations delegating to the same per-package transaction service rather than creating a weaker mass-write path.

### Recommended first implementation slice

Start with a **test-first Phase 0 transaction slice**: introduce named transaction barriers and synthetic archive/directory fixtures; reproduce a concurrent source change and a forced death after each directory member commit; then implement final-source guarding plus the durable journal/startup reconciliation needed to make those tests pass. Keep planner/validator/UI behavior unchanged in this slice.

Exact tests that must protect it:

1. archive apply: source changes after candidate validation → no replacement, backup retained/identified, `file_state=unchanged`;
2. directory apply: each member changes externally before its write → no unrelated edit overwritten;
3. directory process kill after journal write, backup rename, each member write/delete, commit-complete marker and before history write → restart yields unchanged, fully committed with receipt, or explicit recoverable state only;
4. Undo process kill/mutation at the same barriers → no unrelated change overwritten and recovery backup retained until completion;
5. junction/symlink parent swap immediately before commit → no write outside configured library;
6. archive/directory backup corruption immediately before commit → source unchanged;
7. batch kill inside one package → earlier package receipts intact, current package explicitly reconciled, later packages untouched;
8. old `0.34.0` backup/history with no journal → remains readable and behaves exactly as today.

### Conclusions requiring further runtime/specialized verification

FeedBack nightly **was launched** for this audit, so the core populated-screen and Song Tools observations are runtime-verified. The following still require a special clean/synthetic profile or assistive/fault environment: true first-run timing, NVDA/JAWS announcements, high-contrast/light-theme and 200–400% zoom, real forced process/power-loss recovery, filesystem-junction races, hostile-package timeout/RSS behavior, SQLite lock storms, and end-to-end support-bundle redaction. No repair behavior was exercised against the real library.

**No product code, dependencies, configuration, real song package, or real library data was changed during this audit. The only intentional repository change is this Markdown report.**
