# Accessibility certification matrix

Date: 2026-08-11
Plugin version: `0.41.0`
Target: WCAG 2.1 AA-oriented Library Doctor plugin UI

## Automated and live-browser result

The latest-nightly Playwright suite passes axe-core against both the first-run
and populated result surfaces. The populated surface is checked with the live
dark theme and a synthetic light-theme token set. The same suite verifies
keyboard activation and focus restoration, Windows forced-colors focus, reduced
motion, and a 320 CSS-pixel viewport representing 400% reflow from a 1280-pixel
desktop layout.

The first accessibility run identified three real contrast failures: eyebrow
text, the primary action, and the worker-summary value. The colors were fixed;
no axe rule was disabled or waived.

The Node/JSDOM suite additionally verifies that:

- the scan live region changes at state/progress milestones, not for every
  polled package name;
- Song Tools keeps the detail panel outside its `role=list` and focuses that
  panel after selection;
- closing the Song Tools panel restores focus to the re-rendered song trigger;
- confirmation entry and cancel paths place and restore focus deterministically.

## Semantics implemented

- The plugin root is a labelled `section`, avoiding a nested host `main`.
- Visible scan progress is separate from one atomic, polite, visually hidden
  live region.
- Batch, activity, Song Tools count, and playback status regions are atomic.
- Audio controls for proposed, current, and finished previews have explicit
  accessible names.
- Buttons, inputs, selects, summaries, audio controls, and programmatic focus
  targets share a visible focus treatment.
- Narrow layout rules allow content and action labels to wrap without horizontal
  plugin overflow.
- Forced-colors mode uses system colors and does not depend on translucent
  theme blends to communicate selection or focus.

## Manual release checklist

Run this checklist on a Windows machine with current NVDA before declaring an
external release candidate fully assistive-technology certified:

1. Start NVDA, open Plugins, and enter Library Doctor using only the keyboard.
2. Confirm one `Library Doctor` region/heading is announced and no duplicate
   `main` landmark is introduced.
3. Tab through Library check, both scan-option disclosure levels, filters,
   result disclosures, repair review/cancel, Activity, and exports. Confirm the
   focus indicator is always visible and focus order matches visual order.
4. Start a synthetic scan. Confirm one start announcement, milestone updates,
   paused/resumed state, and one terminal announcement. Package filenames must
   not be spoken on each polling refresh.
5. Enter Song Tools, search, select a song, open Preview Creator, and close the
   selection. Confirm the selected heading is announced, the detail content is
   not presented as part of the song list item, every audio player has a useful
   name, and focus returns to the song trigger on close.
6. Open and cancel every confirmation surface. Confirm focus enters at the
   confirm action and returns to the initiating control on cancel.
7. Repeat the principal journeys at Windows 200% and 400% text/display zoom and
   with Windows Contrast Themes enabled.

NVDA was not installed on the implementation workstation, so the checklist
above is deliberately recorded as an outstanding human release-signoff step.
It is not represented as having been run. Axe, accessibility semantics,
keyboard journeys, forced colors, dark/light contrast, and 400%-equivalent
reflow were executed successfully against the current nightly.
