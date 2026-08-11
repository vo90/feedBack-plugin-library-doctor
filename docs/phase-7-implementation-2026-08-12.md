# Phase 7 implementation: release qualification and host compatibility

Date: 2026-08-12
Plugin version: `0.43.0`
Repository boundary: standalone `feedBack-plugin-library-doctor` only

## Outcome

Phase 7 converts the audit's remaining release-validation gaps into executable
plugin-owned contracts where that is technically honest. The minimum FeedBack
capability floor is machine-verified, support bundles receive a small
identity-free plugin diagnostic, remote CI is configured to verify both the
first module-capable host commit and current host main, and required human
signoffs are tracked in a versioned ledger with evidence rules.

No FeedBack nightly, Desktop, or other repository source file was changed.

## Executable host floor

FeedBack's `VERSION` file remained `0.3.0-alpha.1` across the introduction of
native plugin modules, so a semantic `minHost` comparison cannot distinguish
the tagged pre-module host from a compatible nightly. `host-contract.json`
therefore records commit `950e3483573e458cc2aa7bc255d9590808947faa`
(2026-07-08) as the first compatible build and names the required sibling
loader, module source route/loader, and diagnostic callable capabilities.

`tools/verify_host_contract.py` checks those capabilities in an actual FeedBack
checkout without importing or modifying host code. Its alternative loader
evidence supports both the original `static/app.js` implementation and the
later extracted `static/js/plugin-loader.js`. Unit tests prove that the exact
version string without module rails is rejected and that evidence paths cannot
escape the supplied host checkout.

The CI host-contract matrix checks the exact minimum commit and current
`got-feedBack/feedBack` `main`. The in-product loading text now describes the
capability-bearing nightly rather than incorrectly implying that every build
labelled `0.3.0-alpha.1` can activate the module graph.

## Privacy-safe support diagnostics

The plugin manifest now contributes `diagnostics:collect` through FeedBack's
documented diagnostic callable. The bounded payload contains only plugin
version, state-file readability/presence, history record count, recovery backup
and pending-journal counts, and batch state-file readability. It never returns
package identifiers, titles, artists, paths, raw state, exception text, or
database content.

The diagnostic collector fails closed for missing, malformed, oversized, or
unreadable state. Bundle-shaped tests combine the callable output with the
existing privacy-safe log adapter and assert that synthetic artist, song,
package suffix, and local path data are absent from the resulting ZIP bytes.

## Release signoff ledger

`release-signoff.json` is version-bound to `0.43.0`. Required NVDA, real Windows
display-mode, minimum-host runtime, novice-usability, remote-CI, and clean
release-worktree approvals remain explicitly `pending` with null evidence.
Contract tests prevent a pending item from carrying invented evidence and
require passed/waived items to name their evidence.

The human procedure and evidence hygiene are defined in
`docs/release-signoff-0.43.0.md`. This does not pretend automation can certify a
screen reader or a novice's comprehension.

## Verification

The complete local gate run passed:

- `python -m pytest --cov --cov-report=term`: 437 passed, 85.53% total
  coverage (the configured minimum is 85%);
- `python -m ruff check .` and production/tool compilation: passed;
- `npm run check:frontend` and `npm run lint:frontend`: passed;
- `npm run test:frontend`: 20 passed;
- `npm run test:browser`: 6 passed against the latest-nightly host;
- the host verifier passed against the exact minimum commit and the current
  local latest-nightly checkout;
- a disposable FeedBack host loaded Library Doctor `0.43.0` as `ready`, invoked
  its diagnostic callable, exported a real diagnostics ZIP, and included the
  `library_doctor.diagnostics.v1` payload; the temporary host was then stopped;
- `python -m pip check`: no broken requirements;
- `python -m pip_audit -r requirements.txt`: no known vulnerabilities;
- `npm audit --audit-level=high`: zero vulnerabilities; and
- workflow/JSON parsing and `git diff --check`: passed (Git reports only the
  repository's existing Windows line-ending conversion notices).

## Remaining release decision

Phase 7 produces an engineering-complete release candidate, not an automatic
production declaration. Remote workflow results and the required human ledger
items can only be completed after the current work is committed/pushed and the
documented sessions are performed.
