# Guitar, tablature, and Rocksmith 2014 CDLC repair research

**Research date:** 2026-08-15  
**Scope:** Analysis and design recommendations for Library Doctor. No implementation is included.

## Executive summary

Library Doctor already has broad structural and highway-safety validation. It checks package and schema integrity, time ordering, exact duplicates, same-string conflicts, fret/string/capo limits, slides, bends, technique value types, chord templates and fingerings, handshapes, anchors, phrase ladders, tones, lyrics, media, and optional deep audio properties. It currently exposes 16 deterministic song-data repairs as safe automatic repairs and one reviewed repair workflow for hammer-ons and pull-offs.

The main conclusion of this research is that most remaining high-value CDLC problems are **not safe automatic fixes**. A fret number is not merely a pitch: tuning, capo, string choice, fingering, sustain, articulation, hand position, and the relationship to adjacent events all affect what the player sees and is expected to perform. Rocksmith-specific authoring choices such as pitched versus unpitched slides, link-next, fret-hand positions (FHPs), handshapes, tone-change placement, and dynamic-difficulty phrase boundaries encode author intent. They should be diagnosed and repaired only through a bounded review workflow.

The best additional safe fixes are deliberately unmusical and narrow:

1. Remove exactly empty optional arrangement keys where the pinned Feedpak schema explicitly says the key must instead be omitted (`phrases: []` and `tempos: []`).
2. Stable-sort simple event streams that have valid numeric times, preserving all events and the authored order of equal-time events.
3. Remove only complete JSON-identical redundant tempo, meter, tone-change, and bend-curve events, keeping the first copy.

The highest-value reviewed fixes are:

1. A slide/link-next relationship reviewer.
2. A sustain and technique-duration reviewer.
3. A bend-curve reviewer.
4. Separate chord-fingering and FHP/anchor/handshape reviewers.
5. Additional mutually exclusive technique reviewers, without outlawing legitimate composite techniques.
6. Tone-change placement review.
7. Phrase/section and sync quality assistants that remain advisory unless the user explicitly chooses every mutation.

The report recommends extending the existing generic reviewed-repair framework rather than building a second repair system.

## 1. Method and evidence limits

This report compares four kinds of evidence:

- General guitar and tablature semantics from Berklee, Guitar Pro, and the W3C MusicXML reference.
- Current CustomsForge CDLC standards and authoring guidance. CustomsForge is the active community authority for CDLC, but it is not an official Ubisoft technical specification.
- The current open-source `Rocksmith2014.NET` arrangement checker used by DLC Builder.
- The checked-out Library Doctor, FeedBack loader, Guitar Pro converter, and the pinned Feedpak schema revision.

The local schema is pinned to Feedpak specification revision `52548b742f64c2a35052a141976ea1b7889f4b1a`, retrieved 2026-08-07. Local conclusions are therefore about that revision and the checked-out FeedBack implementation, not every possible future Feedpak consumer.

Rocksmith authoring rules must not be copied blindly into Feedpak repair rules. For example, CustomsForge's ten seconds of leading silence, two empty opening measures, phrase-count limit, and tone-count conventions solve Rocksmith 2014 loading, scoring, and Riff Repeater behavior. A converted Feedpak may have a different time origin and different runtime requirements. Those items are useful provenance checks, but they are not automatically FeedBack defects.

Likewise, a checker finding is not proof that one mutation is correct. The current `Rocksmith2014.NET` checker deliberately emits issues for such relationships as link-next target mismatches, simultaneous tone changes and notes, natural-harmonic bends, overlapping bend values, fingering/anchor mismatches, and anchors inside handshapes. Its source is valuable evidence for diagnostics, while the correct repair still depends on musical context and author intent ([repository overview](https://github.com/iminashi/Rocksmith2014.NET), [instrumental checker](https://github.com/iminashi/Rocksmith2014.NET/blob/main/src/Rocksmith2014.XML.Processing/Checkers/InstrumentalChecker.fs)).

## 2. Relevant guitar theory and technique semantics

### 2.1 Pitch is determined by tuning, string, fret, and capo

On ordinary equal-tempered guitar, each fret raises an open string by one semitone. Fret `0` means the open string. The same sounding pitch can occur at multiple string/fret positions, so pitch equality does not mean two tab events are duplicates. String choice changes timbre, hand position, the available articulation, and how a passage connects to its neighbors. Alternate tuning and capo placement change every derived pitch and chord shape. Berklee's notation introduction also emphasizes that one chord can have multiple guitar voicings and that automatic fingering choices often do not make sense to a guitarist ([Berklee guitar notation basics](https://online.berklee.edu/takenote/guitar-notation-basics/)).

Consequences for Library Doctor:

- It is safe to calculate and display a pitch from declared tuning/capo/string/fret.
- It is not safe to move a note to a different string merely because the pitch is unchanged.
- It is not safe to “correct” an alternate tuning to standard tuning.
- Two simultaneous notes with the same pitch can be an intentional unison or doubled voicing.
- Chord-name and scale-degree checks must tolerate inversions, omitted tones, added tones, enharmonic names, open-string drones, and chromatic notes.

### 2.2 Rhythm and meter are independent of fret placement

Tablature directly describes where to play, but simple tab often omits precise rhythm. Both W3C MusicXML and Guitar Pro describe rhythm as additional information rather than something inherently encoded by the fret number ([MusicXML tablature tutorial](https://www.w3.org/2021/06/musicxml40/tutorial/tablature/), [Guitar Pro: how to read tabs](https://support.guitar-pro.com/hc/en-us/articles/208610945-GP-How-to-read-guitar-tabs)). A usable interactive chart therefore needs consistent event times, durations, tempo/meter information, and audio alignment in addition to valid frets.

Consequences:

- Stable chronological ordering can be a safe structural repair when no event value changes.
- Inferring a rhythm, tempo map, or sustain from visual spacing alone is not safe.
- Quantizing a live performance to a rigid grid can make a correctly synced chart wrong.
- Beat-map and audio-sync quality are high-value diagnostics but normally require listening.

### 2.3 Articulation is relational, not a flag in isolation

A hammer-on connects a lower fretted source to a higher destination on the same string; a pull-off starts with the higher note fretted and exposes a lower destination; a slide keeps pressure on the string while moving between positions. Berklee describes all three as ways of connecting consecutive notes, and MusicXML models hammer-ons and pull-offs as paired start/stop relationships rather than isolated labels ([Berklee technique lesson](https://online.berklee.edu/takenote/guitar-techniques-hammer-ons-pull-offs-and-slides/), [MusicXML tablature tutorial](https://www.w3.org/2021/06/musicxml40/tutorial/tablature/)).

However, fret direction is evidence, not complete proof of intent. Tapping, grace notes, re-articulation, intervening chords, slides that establish a new source pitch, and deliberately unusual notation can all change the interpretation. Library Doctor's existing decision to review questionable HO/PO rather than silently reverse or move it is therefore sound.

### 2.4 Duration is part of how a technique is communicated

Slides, vibrato, tremolo picking, and bends need time in which to occur. A zero-duration or extremely short note may make a valid-looking technique invisible or impossible to communicate on the highway. Conversely, an overlapping sustain may represent a deliberate tie/link, ringing note, or legato relationship. It cannot always be trimmed automatically.

CustomsForge's current charting standard asks for a visible gap before a following note and treats bends and similar techniques as exceptions to otherwise disposable short sustains. That is a Rocksmith visual convention, not a universal fact of guitar performance ([2025 charting standards](https://ignition4.customsforge.com/kb/article/Charting/charting-standards), [in-depth charting guide](https://ignition4.customsforge.com/kb/article/Charting/what-are-charting-standards)). The useful Library Doctor rule is therefore “this relationship needs review,” not “always shorten by a fixed number of milliseconds.”

### 2.5 Bends are curves, not only peak values

A bend can include a pre-bend, release, partial release, or rebend. MusicXML explicitly distinguishes bend amount from pre-bend/release state and allows multiple bend elements for a bend-and-release gesture ([MusicXML bend](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/bend/), [bend amount](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/bend-alter/)). In Feedpak, the scalar `bn`, intent `bt`, and point sequence `bnv` similarly describe related aspects of the gesture.

Consequences:

- Sorting existing curve points by time is safe and already supported.
- Removing an exactly repeated curve point can be safe.
- Choosing whether the scalar peak or the curve is authoritative is not safe.
- Extending the sustain, clamping a point, or deleting a release segment requires review.

### 2.6 Fingering and hand position are contextual

A four-fret “one finger per fret” position is a useful default, not a physical law. Tapping, high-fret solos, thumb-over playing, barres, partial barres, open strings, and player hand size all change viable fingerings. Current CustomsForge guidance explicitly warns against trusting auto-generated FHPs and chord fingerings, especially for tapping, solos, and drop-tuned power chords ([current standards](https://ignition4.customsforge.com/kb/article/Charting/charting-standards), [CDLC creation guide](https://customsforge.com/topic/35318-cdlc-creation-for-rocksmith-2014-remastered/)).

Consequences:

- Impossible array values and impossible same-finger assignments can be detected.
- A “best” fingering usually cannot be selected automatically.
- Wide spans and frequent FHP changes are review signals, not proof of an error.
- Copying a fingering from the same fret shape can still be wrong in a different musical context.

## 3. Common tablature mistakes

The sources and local conversion behavior point to the following recurring classes of mistakes.

| Mistake | Why it matters | Automatic-repair suitability |
|---|---|---|
| Wrong or missing tuning, capo, or string count | Every pitch and shape can be displaced | Diagnose; review only |
| Correct pitch on an implausible string/fret | Changes timbre, position, fingering, and transitions | Review only |
| Missing or inaccurate rhythm/meter | Notes can be correct but arrive at the wrong time | Diagnose/listen; review only |
| Events stored out of chronological order | Ordered consumers can skip, delay, or select the wrong event | Safe stable sort under strict guards |
| Exact duplicate events | Can produce redundant gems or ambiguous processing | Safe only when the complete stored objects are identical |
| Technique attached to the wrong endpoint | HO/PO, slide, bend, and link semantics become misleading | Review only |
| Technique without enough duration | Technique may be invisible or impossible to perform as displayed | Review only |
| Sustain touching/overlapping a following event | Obscures the next instruction unless a link/tie is intended | Review only |
| Pitched/unpitched slide confused | Rocksmith scores and displays the two differently | Review only |
| Chord notes, template, name, and fingering disagree | Player sees the wrong shape or an impossible fingering | Structural mismatches can be errors; repair needs review |
| Open/dead/palm/fret-hand mute confused | These are different physical actions and display semantics | Review only |
| Auto-generated FHPs jump excessively | Highway becomes visually noisy or suggests the wrong hand motion | Review only |
| Missing technique detail after format import | Guitar Pro, ASCII, MIDI, MusicXML, EOF, and Rocksmith do not share identical semantics | Diagnose provenance; review only |

Guitar Pro itself treats tuning, number of strings, capo, tempo, meter, note duration, ties, bends, harmonics, palm mutes, slides, tapping, and HO/PO as distinct authoring information ([tuning setup](https://support.guitar-pro.com/hc/en-us/articles/360000034505-GP8-Select-or-set-up-the-tuning-of-your-choice), [authoring shortcuts](https://support.guitar-pro.com/hc/en-us/articles/360001646978-GP8-List-of-keyboard-shortcuts)). It also states that audio-to-tab transcription is not automatic; audio can be attached, but notes still require manual transcription ([audio transcription limitation](https://support.guitar-pro.com/hc/en-us/articles/19213333798813-GP8-Can-I-convert-an-audio-file-into-a-Guitar-Pro-file)). This supports keeping audio-based “corrections” out of the safe repair catalog.

## 4. Rocksmith 2014 CDLC creation and common mistakes

### 4.1 Current authoring path

The current community workflow uses EOF or another chart editor, DLC Builder for packaging/quality checks/dynamic difficulty/tones, and Wwise for Rocksmith audio encoding. CustomsForge describes DLC Builder as the current packaging tool and the public DLC Builder page identifies version 3.5.0 (published 2025-07-24) as the latest listed build ([software guide](https://ignition4.customsforge.com/kb/article/Charting/which-software-to-use), [DLC Builder](https://ignition4.customsforge.com/tools/DLCBuilder)).

A normal quality loop is:

1. Obtain and verify the audio.
2. Establish leading time, beat map, tempo changes, and meter.
3. Import or transcribe notes, then verify them by ear and on the instrument.
4. Correct techniques, sustains, chord fingerings, FHPs, handshapes, phrases, and sections.
5. Add tones and lyrics where applicable.
6. Generate dynamic difficulty after the chart is stable.
7. Run DLC Builder's arrangement checks.
8. Test in Rocksmith, including Riff Repeater at reduced speed.

### 4.2 High-frequency CDLC failure modes

Current CustomsForge standards identify adequate transcription, sync, tuning, tones, manual phrases/sections, dynamic difficulty, FHPs, chord fingerings, sustains, slides, and handshapes as functional or playability requirements ([standards summary](https://ignition4.customsforge.com/kb/article/Charting/charting-standards)). The in-depth guide adds several important nuances:

- Real recordings may drift in tempo and should not be forced to a single BPM.
- Tone changes should occur slightly before the note rather than exactly on it.
- Phrase/section boundaries are learning and navigation choices, commonly based on musical form but not governed by one universal interval.
- Auto-generated FHPs are particularly unreliable in tapping passages.
- Drop-tuned barred power chords are often assigned several fingers when one finger is intended.
- Pitched slides connect to and hold a target note; unpitched slides communicate direction without the same target requirement.
- Sustains need visual separation unless a deliberate relationship requires otherwise.

The long-running CDLC guide is unusually explicit that no generally reliable rule can choose pitched versus unpitched slides; the author must decide which best conveys the performance, ideally by slow-speed testing ([CDLC creation guide](https://customsforge.com/topic/35318-cdlc-creation-for-rocksmith-2014-remastered/)). This is decisive evidence against an automatic slide-type converter.

The open-source DLC Builder checker confirms that real-world validation is relational. Among other checks, it looks for:

- link-next without a target, with a timing/fret/bend mismatch, or combined with an unpitched slide;
- HO/PO into the same pitch;
- natural and pinch harmonic both set;
- natural harmonic combined with bend;
- bends with no nonzero value or overlapping point times;
- techniques without sustain;
- finger changes during a slide and position shifts into pull-offs;
- tone changes exactly on notes/chords;
- suspicious chord fingerings, barres over open strings, and muted members inside a non-muted chord;
- fingering/anchor mismatch and anchors that interrupt handshapes;
- phrase and ending-structure problems.

These checks are strong candidates for Library Doctor diagnostics, but several are deliberately heuristic in the upstream source and some encode Rocksmith XML conventions not guaranteed by Feedpak. They should be ported selectively and tested against FeedBack's actual renderer and grader.

## 5. Current Library Doctor coverage

The checked-out validator identifies itself as `rules-28`. Its existing coverage is stronger than a conventional archive linter:

- Package, path, manifest, schema, reference, checksum, and validation-budget safety.
- Exact duplicate and conflicting notes/chords/chord members/anchors/handshapes.
- Note/chord/anchor/phrase ordering and composite mastery-stream ordering.
- Same-string collisions, near-simultaneous events, and sustain overlap.
- Fret, string, capo, tuning, and 24-fret/8-string highway limits.
- Negative frets, sustains, slides, bend curves, boolean technique values, and HO/PO conflict/direction/source review.
- Chord template references, explicit-note/template mismatches, finger arrays, impossible fingerings, and extreme spans.
- Handshape spans/references, anchors, phrases, difficulty levels, and empty playable arrangements.
- Beats, sections, tempo changes, time signatures, lyrics, drums, keys, harmony, vocal pitch, notation, rigs, tones, media, and optional deep audio inspection.

The safe repair catalog currently has 16 song-data rules:

- Exact duplicate note/chord/chord-note/anchor/handshape removal.
- Standalone-note versus chord redundancy removal.
- Exact negative muted-fret normalization.
- Bend-point sorting.
- Zero/reversed handshape span correction.
- Lyric ordering.
- Exact duplicate and chronological beat/section repair.
- Exact duplicate drum-hit removal.

Preview creation/replacement is correctly classified as review-required. The reviewed repair registry currently contains one adapter, `review.hopo-techniques`, with field-limited decisions over `ho`, `po`, and `tp`. That architecture already supplies candidate identity, blockers, bounded paging, field allowlists, before/after hashes, full-candidate validation, backup, commit, journal, finalization, and undo. New musical repairs should use it.

## 6. Safety model for future fixes

A future fix should be called **safe automatic** only when all of the following are true:

1. It preserves the same playable instruction and does not infer a new note, pitch, duration, articulation, fingering, name, or boundary.
2. The target is identified from the complete stored object, not merely from time/fret coincidence.
3. Conflicting alternatives are left untouched.
4. Unknown/additional properties are preserved unless the entire removed object is an exact duplicate.
5. Equal-time ordering is stable.
6. The behavior is supported by the pinned Feedpak contract and checked-out FeedBack consumer, not only a Rocksmith convention.
7. The complete candidate package passes schema and semantic validation after the mutation and does not gain new findings outside an explicitly documented replacement set.
8. Planning and application remain bounded, deterministic, transactional, undoable, and stale-source protected.

A fix is **review-required** if it chooses between plausible musical intentions, changes scoring/display semantics, derives a new time or duration, selects a fingering/hand position, moves an instruction to a different event, or relies on audio interpretation.

A feature should remain **diagnostic-only** when the plugin cannot express the correction through a small closed field set or cannot show enough context for an informed choice.

## 7. Proposed safe automatic fixes

These are research recommendations, not implemented rules.

### SA-1 — Omit explicitly empty optional arrangement streams

**Proposed detection:** An arrangement contains exactly `phrases: []` or `tempos: []`.

**Mutation:** Remove only that key.

**Why it is safe:** The pinned arrangement schema explicitly says an absent `phrases` key means no difficulty ladder and an absent `tempos` key means the arrangement follows song tempo. It also explicitly declares the empty arrays non-conformant. No event is deleted because the arrays contain none.

**Guards:** Do not touch `null`, malformed non-array values, non-empty arrays, song-level timeline arrays, or unknown similarly named properties. Revalidate the whole candidate.

**Suggested priority:** P1. This is the clearest new safe repair.

### SA-2 — Stable-sort valid flat event streams

**Initial proposed scope:**

- top-level arrangement `notes` by `t`;
- top-level arrangement `chords` by `t`;
- top-level arrangement `anchors` by `time`;
- song/arrangement `tempos` by `time`;
- song `time_signatures` by `time`;
- tone `changes` by `t` or the accepted canonical time field.

**Mutation:** Stable-sort existing objects by their stored finite numeric time. Preserve every object and every property. Preserve authored relative order for equal times.

**Why it can be safe:** FeedBack already requires ordered highway streams and already stable-sorts beats/sections as a safe repair. Its tone and tempo loading paths also normalize order. The mutation does not retime or select between events.

**Guards:** Every item in the touched list must be an object with a valid finite time. Conflicting equal-time events remain findings. Do not initially include phrase windows, phrase levels, handshapes, nested dynamic-difficulty streams, or cross-list merges; those need separate contract tests because list position can affect mastery selection and relationships.

**Suggested priority:** P1 for tempo/meter/tone streams; P2 for top-level notes/chords/anchors after consumer-equivalence tests.

### SA-3 — Remove exact duplicate tempo, meter, and tone-change events

**Proposed detection:** Within one active list, a later complete JSON object is identical to an earlier object. Identity must include time, value/rig, and all additional properties.

**Mutation:** Keep the first object; remove later exact copies.

**Why it is safe:** An identical repeated state change carries no additional authored information. The existing beat/section and note/chord duplicate rules establish the same safety principle.

**Guards:** Same time with different BPM, meter, rig, spelling, or extra properties is a conflict and must remain. Do not deduplicate merely because the effective rig or numeric value appears equal after coercion.

**Suggested priority:** P1.

### SA-4 — Remove exact duplicate bend-curve points

**Proposed detection:** Within one `bnv` list, a later point object is completely JSON-identical to an earlier point object.

**Mutation:** Keep the first exact point; remove later copies. Run the existing chronological sort separately if required.

**Why it can be safe:** A repeated point with the same relative time, same bend amount, and same extra data describes the same curve state twice. The current FeedBack loader preserves same-time points, while DLC Builder reports overlapping point times; exact-object deduplication is narrower than either behavior.

**Guards:** Equal time with different value or different extra properties is not safe. Do not collapse adjacent equal values at different times because they can encode a held plateau. Require bend-curve and full-package post-validation.

**Suggested priority:** P2, after a renderer/grader equivalence fixture proves that exact multiplicity has no meaning.

### SA-5 — Candidates that should not yet enter the safe catalog

Stable-sorting phrase levels by difficulty and removing exact duplicate difficulty levels look mechanical, but list position participates in dynamic-difficulty selection in the current FeedBack path. CustomsForge guidance also permits repeated musical content at multiple difficulty stages. These operations should remain unsupported until the Feedpak difficulty contract is more explicit and representative converted packages prove equivalence.

## 8. Proposed reviewed fixes

### RR-1 — Slide and link-next reviewer

**Triggers:** Existing ambiguous/same-fret/open-string/out-of-range/without-sustain slide findings plus new diagnostics for `ln` with unpitched slide, missing link target, target-time mismatch, target-fret mismatch, bend-state mismatch across a link, or chord/member link inconsistency.

**Context shown:** Previous/current/next same-string events, intervening chord members, fret/timing/sustain, active anchor/handshape, phrase boundary, detected target candidates, and a short audio audition.

**Decisions:**

- keep/set a pitched slide to an explicitly selected target;
- keep/set an unpitched slide and endpoint;
- add or remove link-next;
- choose a reviewed sustain endpoint;
- remove the slide;
- leave unchanged.

**Mutable fields:** `sl`, `slu`, `ln`, and, only when the user chooses a duration decision, `sus` on the selected note/chord member.

**Blockers:** Same-time string conflicts, malformed technique values, non-unique target, stale neighboring events, crossing incompatible phrase/mastery structures, or an unrepresentable chord relationship.

**Reason for review:** Pitched and unpitched slides change Rocksmith scoring expectations, and current community guidance explicitly says author judgment and slow-speed testing are required.

### RR-2 — Sustain and technique-duration reviewer

**Triggers:** Same-string sustain overlap, slide/vibrato/tremolo/bend with no usable duration, handshape ending on/before its last event, or an isolated suspiciously short/long sustain.

**Context shown:** Beat/meter-derived musical duration, next same-string onset, active technique flags, link state, chord/handshape membership, and audio.

**Decisions:**

- trim to a selected next onset with a previewed gap;
- extend to a selected beat/event;
- create/remove link-next where an explicit target exists;
- remove only the questioned technique;
- remove sustain;
- leave unchanged.

**Mutable fields:** `sus` plus only the individually selected relationship/technique field.

**Important constraint:** A Rocksmith-style 1/32 gap may be offered as a suggestion calculated from the active tempo/meter, but must not be a universal automatic mutation. A fixed millisecond gap is especially inappropriate across tempo changes.

### RR-3 — Bend-curve reviewer

**Triggers:** Existing negative/out-of-order/outside-sustain/exceeds-peak findings plus same-time different-value points, scalar peak/curve disagreement, a bend flag/peak with no nonzero curve, or a linked bend whose end state disagrees with its target.

**Context shown:** A small curve plot with note onset/end, scalar peak and intent, neighboring linked event, and audio.

**Decisions:**

- set scalar peak from the curve maximum;
- keep scalar peak and clamp/delete selected curve points;
- extend sustain to include a selected point;
- add/edit a selected point;
- remove the bend curve/peak;
- leave unchanged.

**Mutable fields:** `bn`, `bt`, `bnv`, and optionally `sus` only through an explicit duration decision.

**Reason for review:** A mismatch does not reveal whether the summary, the curve, the sustain, or the transcription is wrong. Bend/release/pre-bend gestures make naive clamping destructive.

### RR-4 — Chord-fingering reviewer

**Triggers:** Existing impossible fingering, extreme span, template mismatch, and repeated-string issues plus suspicious finger order, barre crossing an intended open string, all-finger drop-tuned power-chord patterns, or chord/member mute inconsistency.

**Context shown:** Fretboard diagram, tuning/capo, template and explicit chord notes, neighboring shapes, active anchor, handshape, and every occurrence that references the template.

**Decisions:**

- edit the fingering explicitly;
- apply a candidate fingering to this template after showing all affected occurrences;
- copy from an exact same fret/string shape as a suggestion;
- clear fingering guidance;
- reconcile the explicit chord to the template or the template to the explicit chord;
- leave unchanged.

**Mutable fields:** The selected template's `fingers` and, for a separately confirmed mismatch decision, the exact `frets`/chord-note fields involved.

**Reason for review:** Barres, thumb use, open strings, and passage context make fingering non-unique. A same-shape source is evidence, not authority.

### RR-5 — FHP, anchor, and handshape reviewer

This should be a separate adapter from chord fingering because its mutation surface and affected occurrences differ.

**Triggers:** Fingering/anchor mismatch, finger change during a pitched slide, position shift into a pull-off, anchor inside a handshape, anchor near an unpitched-slide endpoint, excessive short-lived FHP oscillation, or a handshape that omits an explicit slide destination chord.

**Context shown:** Time-window fretboard animation or compact highway preview, active notes/chords, anchor widths, handshape spans, tapping flags, and phrase boundaries.

**Decisions:** Move/delete/add an anchor, change width, split/extend/shorten a handshape, attach a selected chord template, accept a generated proposal, or leave unchanged.

**Reason for review:** FHP is pedagogical/display intent. Tapping and wide high-fret passages are legitimate exceptions to simple span heuristics.

### RR-6 — Additional technique-conflict reviewer

**High-confidence review triggers:**

- natural harmonic and pinch harmonic both true;
- natural harmonic plus bend where the current runtime representation is known to be unsupported;
- unpitched slide plus link-next;
- contradictory chord-level/member mute states that change inheritance;
- technique requiring duration when duration is absent.

**Decisions:** Keep one technique, convert to the related technique, remove the questioned techniques, edit duration/link relationship, or leave unchanged.

**Critical false-positive guard:** Do not build a generic “too many techniques” rule. Rocksmith pick scrapes are intentionally represented by a composite such as tap + tremolo + unpitched slide + ignore. Pinch harmonics can legitimately combine with bends or vibrato. The allowed/blocked combinations must be explicit, evidence-backed, and representation-specific.

### RR-7 — Tone-change placement reviewer

**Triggers:** Tone change exactly on a note/chord, changes too close to be useful, same-time conflicting rigs, or no usable base rig before the first played event.

**Context shown:** Previous beat/grid/event, first affected note, current/next rig, and a short preview if tone rendering is available.

**Decisions:** Move to a selected earlier beat/grid point, choose one same-time rig, set a base rig, remove a redundant change, or leave unchanged.

**Reason for review:** Moving a tone changes what the player hears. “Slightly before” is authoring guidance, not a universally correct timestamp.

### RR-8 — Phrase, section, and difficulty assistant

**Triggers:** Missing or extremely sparse sections for a long playable chart, invalid/overlapping phrase windows, boundaries inside linked sustains, empty difficulty stages, inconsistent progression, or navigation blocks that contain no playable event.

**Context shown:** Song structure timeline, beat/measure map, event density, lyrics, existing section names, and difficulty summaries.

**Decisions:** Add/move/split/merge a boundary at an explicitly selected beat; rename a section; remove an empty stage; choose a source stage; or leave unchanged.

**Constraint:** Do not automatically create a section every four measures, manufacture song-form labels, add a Rocksmith `COUNT`/`END` structure, or add ten seconds of silence to Feedpaks. Those are authoring conventions and package-specific behavior, not facts recoverable from tab data.

### RR-9 — Sync, tuning, transcription, and chord-name assistants

These are valuable but should begin as diagnostics, not one-click repairs.

- Display computed pitches from tuning/capo/string/fret and compare them with optional notation/harmony data.
- Show likely global audio/tab offset or local drift from bounded onset analysis, with confidence and audition controls.
- Display chord pitch classes and possible names without claiming one canonical name.
- Flag an arrangement declared as bass/guitar whose string count or pitch range is unusual, without changing its type.

Any mutation should require the user to choose the exact events and values. Key/scale membership must never be used to “correct” chromatic notes: borrowed chords, modulation, blues notes, altered dominants, and transcription choices make such a rule musically invalid.

## 9. Ideas explicitly rejected as automatic repairs

The following should not be added to “Fix all safe issues”:

- Reverse HO/PO solely from fret direction.
- Choose pitched versus unpitched slide from distance or the next fret.
- Move a note to another string because it produces the same pitch.
- Rewrite tuning/capo to make the notes fit a key.
- Correct notes because they fall outside a scale or chord.
- Generate chord names and overwrite authored names.
- Auto-finger chords or auto-place FHPs without review.
- Trim every sustain to a fixed gap.
- Extend every technique to the next note.
- Set scalar bend peak from curve maximum without asking.
- Remove all explicit boolean `false` technique fields. Presence and absence can be semantically distinct: the current FeedBack XML loader specifically preserves explicit false chord-member mute/accent values when a chord-level value would otherwise be inherited. Removing such values can change behavior, and Feedpak's additional-property contract also requires caution around future consumers.
- Treat complex technique combinations as invalid merely because several flags are true.
- Add Rocksmith leading silence, `COUNT`/`END` phrases, or section density to a converted Feedpak.
- Automatically sync or transcribe guitar from audio.
- Clone a rhythm arrangement into bass to fill missing content.

## 10. Suggested research roadmap

### Phase 1 — Safe structural additions

1. Specify tests for empty optional `phrases`/`tempos` omission.
2. Add diagnostic fixtures for duplicate and out-of-order tempo, meter, and tone changes.
3. Prove stable-sort equivalence against the current FeedBack loader.
4. Prove exact bend-point duplicate equivalence in renderer and grader.
5. Only after those proofs, write rule and repair implementation proposals.

### Phase 2 — Slide/link and sustain review

1. Extend diagnostics using the current same-string event index.
2. Design `review.slide-links` with a closed `sl`/`slu`/`ln`/optional `sus` mutation surface.
3. Design `review.sustain-techniques` separately.
4. Reuse the existing reviewed-repair transaction, backup, undo, paging, audio, and stale-source controls.

### Phase 3 — Bend and fingering/FHP review

1. Add a bend curve context visualization and explicit per-point decisions.
2. Split chord fingering from FHP/anchor/handshape review.
3. Build representative fixtures for alternate tunings, capo, bass, tapping, thumb use, partial barres, open strings, and extended-range instruments.

### Phase 4 — Advisory quality tools

1. Add read-only sync/tuning/pitch/chord-name evidence panels.
2. Measure false-positive rates on a consented corpus of ODLC-derived and community Feedpaks.
3. Promote a diagnostic to reviewed repair only after the necessary context and a closed mutation surface are demonstrated.

## 11. Acceptance criteria for any later implementation

Before any proposal becomes code, it should have:

- A precise rule code and player-facing explanation.
- A formal eligibility predicate and explicit blockers.
- A list of every mutable field and affected document type.
- Representative positive, negative, malformed, alternate-tuning, capo, bass, chord-member, phrase-level, and stale-source fixtures.
- A demonstrated bound on candidates, context size, and runtime.
- Exact preservation tests for unknown properties and unselected data.
- Full candidate schema/semantic validation before commit.
- Finding-delta postconditions, including which old code is expected to disappear and which replacement code may appear.
- Transaction, backup, undo, finalize, crash-recovery, and batch behavior tests.
- Manual in-app verification against the same FeedBack nightly behavior the plugin targets.

## Conclusion

Guitar theory helps Library Doctor most by defining what it **cannot safely infer**. Pitch can be calculated, but string choice, articulation, duration, fingering, hand position, phrasing, and sync are contextual. The Rocksmith/CDLC ecosystem supplies many useful relationship checks, yet its own current guidance repeatedly requires manual testing and judgment for the highest-impact playability details.

The right next step is a small expansion of exact structural repairs, followed by richer reviewed adapters. Library Doctor should remain conservative: automatically preserve and canonicalize known data; ask the author whenever a repair would choose how the music is played.

## Source index

- [Berklee: Guitar Notation Basics](https://online.berklee.edu/takenote/guitar-notation-basics/)
- [Berklee: Hammer-ons, Pull-offs, and Slides](https://online.berklee.edu/takenote/guitar-techniques-hammer-ons-pull-offs-and-slides/)
- [W3C MusicXML: Tablature](https://www.w3.org/2021/06/musicxml40/tutorial/tablature/)
- [W3C MusicXML: Bend](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/bend/)
- [W3C MusicXML: Bend Amount](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/bend-alter/)
- [W3C MusicXML: Staff Tuning](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/staff-tuning/)
- [Guitar Pro: How to Read Tabs](https://support.guitar-pro.com/hc/en-us/articles/208610945-GP-How-to-read-guitar-tabs)
- [Guitar Pro: Tuning Setup](https://support.guitar-pro.com/hc/en-us/articles/360000034505-GP8-Select-or-set-up-the-tuning-of-your-choice)
- [Guitar Pro: Authoring Shortcuts](https://support.guitar-pro.com/hc/en-us/articles/360001646978-GP8-List-of-keyboard-shortcuts)
- [CustomsForge: 2025 Charting Standards](https://ignition4.customsforge.com/kb/article/Charting/charting-standards)
- [CustomsForge: In-depth Charting Standards](https://ignition4.customsforge.com/kb/article/Charting/what-are-charting-standards)
- [CustomsForge: CDLC Creation Guide](https://customsforge.com/topic/35318-cdlc-creation-for-rocksmith-2014-remastered/)
- [CustomsForge: Official-looking Customs Guide](https://customsforge.com/topic/30557-guidelines-for-official-looking-customs/)
- [CustomsForge: Current Authoring Tools](https://ignition4.customsforge.com/kb/article/Charting/which-software-to-use)
- [DLC Builder](https://ignition4.customsforge.com/tools/DLCBuilder)
- [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET)
- [Rocksmith2014.NET Instrumental Checker](https://github.com/iminashi/Rocksmith2014.NET/blob/main/src/Rocksmith2014.XML.Processing/Checkers/InstrumentalChecker.fs)

## Local materials reviewed

- `validator.py` and `README.md`
- `repair.py`, `repair_eligibility.py`, and `reviewed_repair.py`
- `docs/reviewed-repairs-implementation-2026-08-12.md`
- `docs/reviewed-repairs-implementation-plan-2026-08-12.md`
- `schemas/arrangement.schema.json`, `schemas/song-timeline.schema.json`, and `schemas/UPSTREAM.md`
- FeedBack `lib/song.py`, `lib/gp2rs.py`, and relevant highway/tone-loading paths
