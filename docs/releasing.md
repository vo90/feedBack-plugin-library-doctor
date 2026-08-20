# Releasing Library Doctor

Library Doctor is a hobby open-source FeedBack plugin. Releases rely on
repeatable technical checks rather than a versioned human signoff ledger.

Before publishing a tagged beta:

1. Build the allowlisted release ZIP from a clean committed revision:

   ```bash
   python tools/build_release.py
   ```

   The command verifies the archive and prints its SHA-256 hash. The ZIP has one
   top-level `feedBack-plugin-library-doctor` folder, with `plugin.json` directly
   inside it.
2. Run the Python, frontend, dependency, host-contract, and isolated browser
   checks documented in the repository.
3. Install that exact ZIP into a disposable FeedBack configuration and scan
   only synthetic song packages.
4. Preview and cancel a repair, apply and Undo a repair, enter and leave Player
   Review, and confirm a recovery-required package refuses further changes.
5. Confirm the README names the tested FeedBack build and describes both Git
   and GitHub ZIP installation.
6. Create the GitHub tag/release and attach the exact ZIP from `dist/`. Use the
   title **Library Doctor v0.45.0 — Public Beta**, select GitHub's
   **Set as a pre-release** option, and use
   `.github/release-notes-v0.45.0.md` as the player-facing release notes.
   Confirm that the SHA-256 in those notes matches the attached file. GitHub's
   automatic **Source code (zip)** download also remains installable because
   this repository keeps `plugin.json` at its root, but the tested release
   asset should be presented as the recommended download.

For a manual Windows install, the extracted directory is placed directly under
`%APPDATA%\feedback-desktop\plugins`. Confirm that the installed layout is
`...\plugins\<one folder>\plugin.json`, not a nested duplicate folder. Test an
upgrade with only one copy of the `library_doctor` plugin ID present.

Manual assistive-technology checks and volunteer usability feedback are useful
but optional. They do not require evidence files or status entries in the
repository.
