# Releasing Library Doctor

Library Doctor is a hobby open-source FeedBack plugin. Releases rely on
repeatable technical checks rather than a versioned human signoff ledger.

The current candidate is **v0.46.0**. Before publishing a tagged beta:

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
5. Confirm the README describes both Git and GitHub ZIP installation. Record the
   tested FeedBack build and completed checks in the release notes; do not copy
   results from an earlier candidate or publish pending verification text.
6. Create tag **v0.46.0** at the verified committed revision and attach the exact
   `dist/feedBack-plugin-library-doctor-0.46.0.zip`. Use the
   title **Library Doctor v0.46.0 — Public Beta**, select GitHub's
   **Set as a pre-release** option, and use
   `.github/release-notes-v0.46.0.md` as the player-facing release notes.
   Append the final asset's SHA-256 to the GitHub release body after building,
   and confirm it matches the attached file. Keep that checksum outside the
   committed ZIP payload: adding it to an included document changes the ZIP
   and invalidates its own checksum. GitHub's
   automatic **Source code (zip)** download also remains installable because
   this repository keeps `plugin.json` at its root, but the tested release
   asset should be presented as the recommended download.

Use explicit `releases/tag/v0.46.0` and
`releases/download/v0.46.0/feedBack-plugin-library-doctor-0.46.0.zip` links in
the README. GitHub's `/releases/latest` excludes prereleases and can direct
players to an older release. Check both explicit links after publication.

Version 0.45.0 was published as a Public Beta on 2026-08-20. Describe the changes
since that release, including adaptive repair workers, portable FFmpeg,
terminal beat repairs, and lyric phrase repair; do not present features already
shipped in 0.45.0 as new. Tell updating users to rescan for current findings and
repair eligibility. Do not imply that lyric phrase repair retimes words or
corrects source synchronization.

For a manual Windows install, the extracted directory is placed directly under
`%APPDATA%\feedback-desktop\plugins`. Confirm that the installed layout is
`...\plugins\<one folder>\plugin.json`, not a nested duplicate folder. Test an
upgrade with only one copy of the `library_doctor` plugin ID present.

Manual assistive-technology checks and volunteer usability feedback are useful
but optional. They do not require evidence files or status entries in the
repository.
