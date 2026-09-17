# Exact redundant terminal markers

This extends the earlier clean-reference repair; it does not interpret or edit
Rocksmith SNG flags and does not reconstruct musical timing. It operates only
on the Feedpak's represented records.

## Eligibility and evidence

`terminal-tail-2` has two evidence modes. `clean_reference_v1` keeps the original
unaffected-grid corroboration. `shared_prefix_exact_redundancy_v1` additionally
allows at least two distinct nonempty grid files when every nonempty grid has
the recognized one-to-three-record alternating repetition of its last retained
pair. Retained grids must agree exactly in time and measure. An explicitly empty
grid stays unchanged and does not count as evidence; a missing input blocks.

Agreement alone is not independent evidence of author intent. The new proof
requires exact duplicate records, strictly increasing finite nonnegative
retained times, a finite positive duration bracketed by the final retained pair
(inclusive endpoints), and no accepted dependency on the removed positions.
Every event onset and end must be strictly before the repeated-region boundary.
Silent phrase windows may extend through retained coverage. All difficulties,
sustains, bends, lyrics, timeline events and time-based rig automation are checked.

Only closed, typed known metadata is accepted, including legacy FeedForge tone
definitions and rig graphs. Unknown extensions, opaque plugin state, extra
sidecars, malformed values, ambiguous paths/keys, links or exhausted read/
structure/inventory budgets cannot approve a repair. Unknown cases remain
visible as review findings, not Safe Fix candidates.

Scan and transaction planning use the same assessor. Old scans require a rescan
(`rules-40`) and old plans are invalidated (`repairs-26`). A scan approves batch
selection, not a later write: preview/preparation recalculates the proof, binds
all input hashes and inventory, and verifies the full candidate. Commit rechecks
sources, preserves backup/recovery protections, and permits exact member Undo.

## Verification

Synthetic tests cover ZIP/directory transactions, single/combined/batch parity,
all-affected 3/1/1/3 tails, unsupported lookalikes and metadata, changed evidence,
candidate failure, partial-write rollback, tampered operations, and Undo.
Inventory limits count directory entries too; linked directories are not walked.

`tools/verify_terminal_copy.py` accepts a source archive and a NEW output folder.
It writes only an isolated copy/profile, checks scan/preview/apply/rescan and
Undo, compares every member, and records the original source hash before/after.
Licensed song data and machine paths are never checked into this repository.

The investigated four-arrangement real-song copy passed with eight removals and
470 retained markers per grid, zero rescan findings, unchanged non-beat values
and other member bytes, repeat repair unavailable, and every original member
restored by Undo. The source archive was unchanged. Restored ZIP-container bytes
may differ due to archive metadata/compression; original member bytes match.

This verifies data integrity, not perceived playback quality. End-of-song
playback remains a Dev Integration acceptance step after the active MinusMix
batch ends and the new plugin is deliberately deployed. No live-library repair,
runtime restart, main release or GitHub push is performed by these tests.
