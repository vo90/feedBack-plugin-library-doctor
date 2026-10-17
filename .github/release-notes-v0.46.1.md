Library Doctor v0.46.1 adds a conservative repair for duplicated chord sustains.

When a plain standalone note and a chord member have exactly the same time,
string, and fret, the repair moves the standalone note's existing sustain onto
the chord member if its sustain field is absent, then removes the redundant
standalone note. It preserves that note's duration and leaves the other chord
members alone.

Explicit zero durations, conflicting values, techniques, unknown data, or
ambiguous matches remain unchanged and require review. All authored difficulty
copies are checked together. This cleanup preserves the chart's existing data;
it does not guess the author's musical intent or add sustain to other notes.

The repair supports reviewed Safe Fix and batches, complete candidate
validation, retained backups, and exact Undo.

## Download and install

1. Close FeedBack.
2. Download `feedBack-plugin-library-doctor-0.46.1.zip` under **Assets** below.
3. Extract it into `%APPDATA%\feedback-desktop\plugins`.
4. Check that the layout is
   `...\plugins\feedBack-plugin-library-doctor\plugin.json`.
5. Keep only one installed Library Doctor folder, then start FeedBack.

Use a compatible current FeedBack Desktop build. The README also describes
installation through Plugin Manager with Git.

**Rescan after updating** so the new findings and repair choices appear.
Updating the plugin keeps scan history, retained backups, and Undo data.

This is a public beta. Keep the original download or another backup of important
songs. Scanning never changes a song; repairs require a separate preview and
confirmation.

Read the [player guide](https://github.com/vo90/feedBack-plugin-library-doctor#readme)
or [report a problem](https://github.com/vo90/feedBack-plugin-library-doctor/issues).
