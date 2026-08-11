# Library Doctor 0.44.0 release signoff

Date opened: 2026-08-12
Scope: standalone Library Doctor plugin only
Ledger: `release-signoff.json`

This checklist is the human and remote-CI portion of the production-readiness
gate. Automated tests must stay green, but they do not stand in for an actual
screen reader, Windows display settings, a minimum-host launch, or a novice
usability session. Keep a signoff `pending` until its evidence exists. Record a
short evidence reference in the ledger when it passes; do not place song names,
library paths, user names, or private screenshots in the evidence.

## NVDA and keyboard

Use a disposable FeedBack profile, an empty or synthetic library, current NVDA,
and only the keyboard for the principal journey.

1. Enter Library Doctor and confirm one labelled region and one page heading.
2. Run a synthetic scan and confirm start, milestone, pause/resume, and terminal
   announcements without package names being spoken on every poll.
3. Traverse filters, finding disclosures, repair review, confirmation, receipt,
   Activity, Song Tools, and exports in visual order with a persistent focus
   indicator.
4. Cancel each confirmation and confirm focus returns to its trigger.
5. Select and close a Song Tools result; confirm its heading and named audio
   controls are announced and focus returns to the selected song trigger.

Record the NVDA version, Windows version, FeedBack commit, plugin commit, and a
PII-free pass/fail note. The expanded journey remains documented in
`docs/accessibility-certification-2026-08-11.md`.

## Windows display modes

Repeat first-run, populated results, repair review/cancel, Activity, and Song
Tools on Windows at 200% and 400% display/text scaling. Repeat with a Windows
Contrast Theme enabled. Confirm no horizontal plugin overflow, clipped action,
hidden focus indicator, color-only state, or unreadable disabled control.
Record the Windows build, theme/scaling combinations, FeedBack/plugin commits,
and a PII-free result.

## Minimum-host runtime

Use the exact minimum compatible FeedBack build from `host-contract.json` in a
separate disposable checkout/profile. Do not point it at a real song library.

1. Run `python tools/verify_host_contract.py <feedback-checkout>` and retain the
   PII-free result.
2. Install Library Doctor into the disposable profile and confirm the plugin
   reports ready, `screen.js` is loaded as a module, and `src/app.js` is served.
3. Open the first-run screen and run the intercepted/synthetic browser journey.
4. Confirm the loading/compatibility message disappears only after activation.

The semantic host version `0.3.0-alpha.1` predates and postdates several nightly
capability changes. The authoritative capability floor is therefore commit
`950e3483573e458cc2aa7bc255d9590808947faa`, not the version string alone.

### Recorded minimum-host evidence

- Date: 2026-08-12.
- FeedBack commit: `950e3483573e458cc2aa7bc255d9590808947faa`.
- Plugin commit tested: `e102b6237266ad1502c625a5246867bb316b9054`.
- Profile and library: separate disposable configuration with an empty synthetic
  library; no real song library was used.
- `python tools/verify_host_contract.py <checkout> --json`: compatible; all
  three declared capabilities passed.
- Runtime `/api/plugins` result: Library Doctor 0.44.0 reported `ready`, with
  `script_type` `module` and no load error.
- `FEEDBACK_NIGHTLY_URL=<minimum-host> npm run test:browser`: 6 of 6 journeys
  passed, including module and `src/app.js` loading, first-run activation,
  reversible keyboard/repair confirmation, repair/Undo, accessibility, forced
  colors, and 400%-equivalent reflow.

## Novice usability

Run the tasks with at least three people who have not worked on Library Doctor.
Use a synthetic profile and do not coach terminology or action choice.

1. Five-second view: ask what the plugin does and whether scanning changes songs.
2. First run: time the participant starting the recommended scan; target under
   30 seconds without assistance.
3. Results: ask which finding matters most and what the proposed repair changes.
4. Recovery: ask how to undo a completed repair and what happens if a song was
   edited afterward.
5. Expert access: ask the participant to find scan options, exports, Activity,
   and Song Tools without making a repair.

Record timings, task success, and paraphrased observations only. Do not record
participants' names, libraries, or song information in repository evidence.

## Remote CI and release hygiene

After the plugin changes are committed and pushed, require green Windows and
Linux normal jobs, the constrained Linux scan job, dependency audit, and both
minimum/latest FeedBack host-contract jobs. Reference the workflow run in the
`remote-ci` ledger item.

Immediately before a release tag, verify the intended plugin commit matches the
reviewed commit, `git status --short` is empty, the version agrees across
`plugin.json`, `package.json`, `package-lock.json`, and the signoff ledger, and
no ignored test artifacts are packaged. Record the commit in the
`clean-release-worktree` ledger item.

## Release decision

Library Doctor may be described as an engineering-complete release candidate
while signoffs are pending. It must not be described as fully assistive-
technology certified or novice-ready until every required ledger item is
`passed` (or is explicitly waived by the release owner with written rationale).
