# Library Doctor public-release implementation plan

Date: 2026-08-20

## Implementation status

Phases 0 through 9 are implemented and locally verified as of 2026-08-20. The
exact allowlisted 76-file `0.45.0` release ZIP (SHA-256
`9fb360316ec2a9f472d260b0eb79712c76c45dbbc74c4c0ee5ba0b89ac9a30f0`)
loaded in a disposable current FeedBack host and passed all nine Playwright
journeys. The complete local gates passed with 566 Python tests, 89 frontend
tests, 85.88% Python coverage, clean Python/Node dependency audits,
minimum/current host-contract checks, Python and JavaScript static checks, and
deterministic release-artifact verification.

Phase 9 is also implemented. Candidate workspace ownership, recovery policy,
strict repair YAML loading, and transaction-journal reconciliation have
dedicated backend modules. Player Review separates transport, playback events,
timeline, navigation, choices, overlay, layout, highlighting, and chart
transformation. Batch orchestration and batch-result rendering now have
separate frontend modules. Backend and focused frontend size limits keep those
boundaries executable in the test suite.

This plan prepares Library Doctor for a public hobby-project beta. The target
user is a normal FeedBack player, not a software developer. The priorities are:

1. Never turn a temporary or failed repair into a discoverable song package.
2. Never make another change while a package has unresolved recovery state.
3. Keep Undo and recovery visible and understandable.
4. Make scanning and reviewing findings simple for a first-time user.
5. Support both FeedBack Plugin Manager Git installation and the normal GitHub
   ZIP/manual plugin-folder installation workflow.

The implementation is split into independently testable phases. No phase may
weaken path containment, source verification, candidate validation, backup
durability, or the rule allowlist.

## Phase 0: lightweight release policy

Remove the versioned mandatory signoff ledger and its evidence tests. Manual
NVDA, display-mode, and multi-person usability sessions become optional
recommendations rather than release gates. Keep the automated safety suite,
host-contract checks, dependency audits, and clean release-artifact checks.

Deliverables:

- Remove `release-signoff.json`, its dedicated test, and version-specific
  signoff procedure documents.
- Make CI contract tests inspect the workflow directly.
- Replace active README signoff language with a short practical release guide.
- Clearly label dated implementation reports as historical rather than current
  release policy.

Exit criteria:

- No runtime, test, or release command depends on a signoff ledger.
- Human testing is useful guidance, not a blocking status field.

## Phase 1: undiscoverable candidate workspaces

Temporary repaired packages must never have `.feedpak` or `.sloppak` names
inside a scanned library. Use a same-volume plugin-owned workspace with a
non-package candidate name and a bounded ownership marker.

Deliverables:

- Centralize candidate workspace creation, validation, and cleanup.
- Keep every temporary candidate name invisible to FeedBack package discovery.
- Reject candidate paths that escape through links or reparse points.
- Exclude plugin-owned workspaces in Library Doctor discovery as defense in
  depth.
- Inventory and conservatively reconcile abandoned owned workspaces at startup.
- Report only privacy-safe orphan counts in diagnostics.

Tests cover process death before/during copy, after validation, before/after
journal durability, after commit, and before normal cleanup. Every checkpoint
must prove that neither FeedBack-style discovery nor Library Doctor discovery
can see a phantom package.

## Phase 2: durable recovery mutation lock

Treat an unresolved package transaction as quarantined. Scans and diagnostics
remain read-only, but no ordinary mutation may proceed until recovery is
resolved.

Deliverables:

- Add a central per-package unresolved-recovery query.
- Gate individual, combined, reviewed, preview-media, and batch mutations.
- Return a structured `recovery_required` error with permitted next actions.
- Mark recovery state in scan results and exclude affected packages from batch
  eligibility.
- Define safe resolution states: restore saved original, keep a fully validated
  current version, or preserve both versions for manual review when external
  edits make either automatic choice unsafe.
- Persist resolution across restart and never overwrite ambiguous external
  changes.

## Phase 3: persistent Activity, Undo, and recovery UI

Replace the single dismissible latest-result notification with durable
actionable activity.

Deliverables:

- List unresolved recoveries and every retained Undo/finalize choice.
- Replace Dismiss with Collapse; actionable state cannot be hidden permanently.
- Show a persistent recovery banner with plain-language state and only safe
  actions.
- Provide accurate restore, keep, manual-review, Undo, and finalize journeys.
- Preserve keyboard focus and expose useful live-region announcements.
- Remove generic retry copy from recovery-required failures.

## Phase 4: first-run simplification

The initial screen presents one recommended action: scan the configured song
library. Scan options are collapsed and advanced controls remain available on
demand.

Deliverables:

- Collapse scan options by default.
- Keep target selection, Deep Audio, and performance tuning progressively
  disclosed.
- Show external-folder Player Review limitations only when an external target
  is selected.
- Remove the Manual Player Review difficulty selector from first-run setup.
- Preserve clear partial, stale, cancelled, and playback-paused states.

## Phase 5: results hierarchy and language

Lead from outcome to the most important affected songs, then expose optional
multi-song operations.

Deliverables:

- Make summary cards actionable and use consistent labels everywhere.
- Put raw package paths and storage terminology under Technical details.
- Preserve the three-part finding explanation: what was found, what may be
  noticed in game, and why fixing it matters.
- Audit primary copy for unnecessary words such as candidate, deterministic,
  manifest, transaction, and validation scope.
- Move and rename batch repair as the secondary `Fix several songs` workflow
  after individual results.
- Keep batch preview, confirmation, cancellation, Undo, and finalization
  behavior unchanged in safety terms.

## Phase 6: focused Player Review workflow

Do not expand repair coverage. Simplify the existing workflow around three
steps: inspect/listen, choose, and apply after review.

Deliverables:

- Keep a persistent `Preview only - song files have not changed` state.
- Present one primary action for the current step and visually subordinate
  navigation/layout controls.
- Clarify Accept & Next, Skip, partial Apply, Return, Undo, and Finalize.
- Keep difficulty filtering inside the review context.
- Retain the text-only fallback and ensure leaving cannot apply a choice.
- Keep responsive, forced-color, keyboard, and delayed-player tests.

Volunteer feedback is welcome but is not a formal release gate or evidence
requirement.

## Phase 7: public installation and release artifact

Support the installation methods expected by FeedBack users:

1. FeedBack Plugin Manager installation from the GitHub repository URL when Git
   is available.
2. Downloading the tagged GitHub release ZIP, extracting it, and placing the
   plugin directory in FeedBack's user plugin folder.

The ZIP route must not require Git, Python knowledge, or running an arbitrary
installer script. Confirm the exact folder naming and discovery rules from the
current FeedBack host source before documenting them.

Deliverables:

- Add an allowlisted deterministic release ZIP builder.
- Put `plugin.json` and every runtime module at the directory level expected by
  FeedBack after extraction.
- Exclude tests, caches, recovery data, development artifacts, and local state.
- Verify the ZIP contents and runtime imports automatically.
- Document Windows ZIP extraction and the exact user plugin folder.
- Put the tested minimum FeedBack capability/build beside the install steps.
- Add a friendly unsupported-host message, a short first-scan guide, recovery
  guidance, troubleshooting, privacy notes, and a changelog.
- Test fresh ZIP install and upgrade in disposable host/config/library roots.

## Phase 8: release-candidate verification

Run the full Python and frontend suites, dependency audits, crash/recovery
tests, minimum/latest host contracts, isolated Playwright journeys, fresh ZIP
installation, upgrade/migration checks, and diagnostic privacy checks against
the exact artifact intended for release.

A short maintainer smoke journey is practical but does not create a signoff
ledger: scan synthetic songs, preview/cancel, apply/Undo, enter/leave Player
Review, and confirm that a recovery-required package is locked.

## Phase 9: bounded post-beta maintainability work

Implemented: candidate workspace handling, transaction reconciliation, and
recovery state are extracted from `repair.py`; Player Review is separated by
transport and view responsibility; batch orchestration and result rendering
are split; and architecture limits are tighter. The extractions remain covered
by backend boundary tests, frontend import-graph and size tests, and workflow
regressions.

## Public beta threshold

Phases 0 through 8 define the public-beta threshold. The genuine hard gates are
undiscoverable candidates, a recovery mutation lock, durable recovery/Undo UI,
and a verified distributable ZIP. Formal human signoffs are not required.
