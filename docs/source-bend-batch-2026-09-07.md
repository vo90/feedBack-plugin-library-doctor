# Folder-based source bend recovery

Song Tools can now review and repair source-proven bend fields across a completed
Library Doctor scan. The user selects an original PSARC folder, reviews exact
source matches and changes, then applies the eligible batch. This is a Library
Doctor feature; it does not change FeedForge, core rendering or song audio.

## Scope and matching

The scope includes every currently scanned package, rather than only bend
warnings: discarded curves can leave no detectable warning. A cheap chart read
skips packages without stored bend values or curves. The source folder is indexed
once using a necessary string/fret/chord topology key; filenames never establish
a match. Every coarse candidate is then checked by the existing complete
arrangement/difficulty/technique matcher and candidate validator. Existing edited
curves and their related copies remain excluded.

Content-identical archives collapse into one source choice. Different matching
archives, nonempty malformed sources and incomplete folder discovery block
automatic selection. The preview names the approved source. Apply rechecks that
source's complete SHA-256 and the reviewed package plan, rather than repeating
the search for newly added unrelated sources.

Folder discovery is bounded to 10,000 archives, 250,000 entries and 128 levels;
links/junctions are not followed. Compilation indexing streams at most 1,024 SNGs
one at a time, with 32 MiB individual and 256 MiB aggregate expansion bounds.
Selected source reads retain at most 128 exact members; the batch generally
reads only the few arrangements matching a package. Two small source selections
may be cached, always behind current file hashes. Declared and decoded zero-byte
SNG placeholders are explicitly counted; malformed nonempty charts remain errors.

## Transactions and review

The scanner reserves each background operation, preventing overlapping scans or
repairs. Playback pauses work; cancellation is cooperative between bounded reads
and guarded package transactions. A new scan invalidates ready previews. Package
signatures and scan provenance are checked again after reservation and before
mutation. Existing repair recovery decisions must be resolved first; this job
never finalizes earlier backups automatically.

The batch reuses RepairService candidate validation, journals, source guards and
Undo. It persists results and active mutation identities before each write, and
can reconcile durable receipts after interruption without replaying a mutation.
Batch Undo separately previews each retained backup. Its inventory is independent
of the ordinary 50-entry repair-history display limit. Running status responses
omit large row arrays; terminal results are searchable and paginated.

## Verification

- Full Python suite: 784 passed, one Windows symlink-privilege skip. A real Windows
  junction traversal check passed. One dependency deprecation warning was emitted.
- Full frontend suite: 112 passed. Frontend syntax, ESLint and changed Python Ruff
  checks passed. Release allowlist and architecture-boundary tests passed.
- A 300-package test applies every repair, restarts the manager, then undoes all
  300 and compares exact original member bytes.
- Real copied-package API smoke recovered 327 entries in Caught Up in You, 327
  in its No Guitar version and 25 in Among the Living. Only lead arrangement JSON
  changed; all audio and other members remained identical. Batch Undo restored
  every original member. Original inputs remained unchanged.
- Both actual Rocksmith compatibility compilations were streamed: 167 and 448
  pitched charts, respectively. Selective Higher Ground and Godzilla member reads
  succeeded without retaining entire parsed compilations.
- Headless Chromium checks at 1280- and 390-pixel viewport widths verified the
  existing dark styling and no horizontal overflow. Frontend regressions cover
  canonical Windows folder paths, hundreds of rows, stale responses, navigation,
  cancellation, individual-source fallback and interrupted batch Undo.

Implementation branch: `feat/doctor-batch-source-bends`, based on the current
`integration/doctor-render-audit` collection, which contains the latest fetched
`origin/main` (`d47e5ff`). No dependencies, core files or FeedForge files changed.
No user-library repairs were performed during implementation or testing.
