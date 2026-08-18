# Library Doctor 0.45.0 release signoff

Date opened: 2026-08-16
Scope: standalone Library Doctor plugin only
Ledger: `release-signoff.json`

This checklist covers the human and remote-CI evidence required after adding
Player Review. Keep an item `pending` until evidence exists; do not copy the
0.44.0 evidence because the host capability floor and principal workflow have
changed. Evidence must not contain song names, library paths, user names, or
private screenshots.

## NVDA and keyboard

Using a disposable profile and synthetic song, complete the scan and reviewed
repair workflow with NVDA and keyboard only. Verify that Player Review controls,
outcome-checked choices, Skip for now/Review skipped issues, the textual current
issue locator, accepted-change count, recovery checkpoint, Undo,
Finalize, Return to Library Doctor, and unavailable-outside-library notice are
announced in a useful order. Switch the affected-song Manual Player Review
filter without rescanning and confirm automatic repairs remain present. Move
both Player overlays with keyboard arrows, use Reset layout, and confirm focus
remains visible and returns to the initiating control after dialogs or
navigation. Operate the single whole-song slider and every ±1/±0.1-second
control by keyboard, including press-and-hold, and confirm one adjustment is
announced rather than every local repeat.

Record NVDA/Windows versions, FeedBack/plugin commits, and a PII-free result.

## Windows display modes

Repeat the Player Review overlay at 200% and 400% display/text scaling and with
a Windows Contrast Theme. Verify that choices, transport-adjacent controls,
recovery controls, and Return remain readable and operable without horizontal
overflow or color-only state. Drag both overlays to every screen edge, resize
the window, and verify both stay reachable and independently movable. Confirm
the issue marker stays centered on the range thumb at the start, an off-center
position, the midpoint, and the end of the song; the pulsing note must also
have a usable string/fret/time text fallback.

## Minimum-host runtime

Use the exact minimum compatible FeedBack commit declared in
`host-contract.json`, in a separate disposable checkout/profile.

1. Run `python tools/verify_host_contract.py <feedback-checkout>`.
2. Confirm Library Doctor loads as a module and reports ready.
3. Run a synthetic in-library Player Review: open the normal Player, preview
   more than one HO/PO choice on the Highway, verify that no-op or still-faulty
   choices are absent, accept a subset, skip and revisit another item, Apply,
   Undo, and Return to Library Doctor.
4. Confirm the prior active chart transform is restored and no absolute source
   path reaches browser state or logs.

The authoritative capability floor is commit
`05be9ebdbe5f77310178772089655dab8f415246`, not the semantic version alone.

## Novice usability

With at least three people unfamiliar with Library Doctor, use synthetic songs
to test whether they can distinguish a live preview from an applied repair,
understand Accept & Next, apply a partial group, find Undo/Finalize, return to
Library Doctor, and understand why an external song cannot enter Player Review.
Record only timings and paraphrased observations.

## Remote CI and release hygiene

After committing and pushing, require green Windows/Linux tests, constrained
scan tests, dependency audits, minimum/latest host-contract jobs, and the
latest-nightly browser journey. Before tagging, verify version agreement across
the manifests, lockfile, diagnostics, and this ledger; confirm the worktree is
clean and ignored test artifacts are not packaged.

## Release decision

This implementation may be called engineering-complete while signoffs remain
pending. Do not describe 0.45.0 as fully accessibility- or usability-certified
until every required ledger item is passed or explicitly waived with rationale.
