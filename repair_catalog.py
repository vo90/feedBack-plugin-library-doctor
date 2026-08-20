"""Declarative allowlist metadata for Library Doctor repairs."""

import importlib.util
import sys
from pathlib import Path


_ACTIONS_MODULE_NAME = "_library_doctor_repair_actions"
_actions = sys.modules.get(_ACTIONS_MODULE_NAME)
if _actions is None:
    _actions_spec = importlib.util.spec_from_file_location(
        _ACTIONS_MODULE_NAME,
        Path(__file__).resolve().with_name("repair_actions.py"),
    )
    _actions = importlib.util.module_from_spec(_actions_spec)
    sys.modules[_ACTIONS_MODULE_NAME] = _actions
    _actions_spec.loader.exec_module(_actions)
RepairDefinition = _actions.RepairDefinition


SAFE_REPAIR_DEFINITIONS = (
    RepairDefinition(
        rule_code="chart.negative-muted-fret",
        action_kind="normalize_muted_negative_frets",
        source_kind="arrangement",
        item_name="muted note fret",
        safety="safe_automatic",
        title="Normalize negative string-mute frets",
        description=(
            "Change only the negative fret value of notes carrying the exact "
            "string-mute flag `mt: true` to fret 0. Keep every string, time, "
            "sustain, technique flag, and unknown stored property unchanged."
        ),
        player_result=(
            "The same pitchless muted strikes remain on the same strings and "
            "at the same times. FeedBack still excludes them from pitch scoring."
        ),
        user_value=(
            "Editors and Feedpak tools receive the standard fret 0 value instead "
            "of an invalid negative fret, without changing what the player is "
            "asked to perform."
        ),
        change_kind="normalize",
    ),
    RepairDefinition(
        rule_code="chart.empty-phrases-key",
        action_kind="omit_empty_phrases_key",
        source_kind="arrangement",
        item_name="phrase-ladder key",
        safety="safe_automatic",
        title="Omit the empty phrase ladder",
        description=(
            "Remove only an arrangement's root `phrases` key when its value is "
            "exactly an empty array. Every playable event and other property "
            "is preserved."
        ),
        player_result=(
            "The arrangement still has no difficulty ladder; it now expresses "
            "that state using the Feedpak-defined omission."
        ),
        user_value=(
            "The arrangement conforms to the Feedpak contract without deleting "
            "or changing any authored musical event."
        ),
        change_kind="omit_empty",
    ),
    RepairDefinition(
        rule_code="timeline.empty-arrangement-tempos-key",
        action_kind="omit_empty_arrangement_tempos_key",
        source_kind="arrangement",
        item_name="arrangement-tempo key",
        safety="safe_automatic",
        title="Omit the empty arrangement tempo override",
        description=(
            "Remove only an arrangement's root `tempos` key when its value is "
            "exactly an empty array. The chart continues to follow the song tempo."
        ),
        player_result=(
            "The chart still follows the song-level tempo map; no tempo event is "
            "invented, removed, retimed, or changed."
        ),
        user_value=(
            "The arrangement uses the Feedpak-defined omission for a chart with "
            "no per-arrangement tempo override."
        ),
        change_kind="omit_empty",
    ),
    RepairDefinition(
        rule_code="timeline.duplicate-tempo",
        action_kind="remove_exact_duplicate_tempo_events",
        source_kind="timeline",
        item_name="tempo event",
        safety="safe_automatic",
        title="Remove exact duplicate tempo events",
        description=(
            "Keep the first tempo event and remove only later complete JSON-identical "
            "copies from each active arrangement or song tempo list."
        ),
        player_result=(
            "Every distinct authored tempo and timestamp remains. Conflicting "
            "same-time tempo values remain for review."
        ),
        user_value=(
            "FeedBack receives one stored copy of each identical tempo instruction "
            "without changing the rhythm map."
        ),
    ),
    RepairDefinition(
        rule_code="timeline.tempos-out-of-order",
        action_kind="reorder_tempo_events",
        source_kind="timeline",
        item_name="tempo timeline",
        safety="safe_automatic",
        title="Put tempo events in chronological order",
        description=(
            "Stable-sort each valid active tempo list by its existing finite "
            "numeric `time`, preserving every object, property, and equal-time order."
        ),
        player_result=(
            "The same tempo instructions reach FeedBack in playback order, without "
            "retiming or choosing between conflicts."
        ),
        user_value=(
            "Tempo lookup becomes deterministic across FeedBack and other Feedpak "
            "tools while the authored tempo data remains unchanged."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="timeline.duplicate-time-signature",
        action_kind="remove_exact_duplicate_time_signature_events",
        source_kind="timeline",
        item_name="time-signature event",
        safety="safe_automatic",
        title="Remove exact duplicate time signatures",
        description=(
            "Keep the first time-signature event and remove only later complete "
            "JSON-identical copies from the declared song timeline."
        ),
        player_result=(
            "Every distinct meter and timestamp remains. Conflicting same-time "
            "meters remain for review."
        ),
        user_value=(
            "The song timeline has one stored copy of each identical meter "
            "instruction without changing the rhythm map."
        ),
    ),
    RepairDefinition(
        rule_code="timeline.time-signatures-out-of-order",
        action_kind="reorder_time_signature_events",
        source_kind="timeline",
        item_name="time-signature timeline",
        safety="safe_automatic",
        title="Put time signatures in chronological order",
        description=(
            "Stable-sort the valid song-level time-signature list by its existing "
            "finite numeric `time`, preserving every object and equal-time order."
        ),
        player_result=(
            "The same meter instructions reach FeedBack in playback order, without "
            "retiming or choosing between conflicts."
        ),
        user_value=(
            "Meter lookup becomes deterministic while every authored signature and "
            "additional property remains intact."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="tones.duplicate-change",
        action_kind="remove_exact_duplicate_tone_changes",
        source_kind="arrangement",
        item_name="tone change",
        safety="safe_automatic",
        title="Remove exact duplicate tone changes",
        description=(
            "Keep the first tone change and remove only later complete JSON-identical "
            "copies from an effective inline arrangement tone block."
        ),
        player_result=(
            "Every distinct tone selection, rig binding, and timestamp remains. "
            "Different same-time changes remain untouched."
        ),
        user_value=(
            "The arrangement carries one stored copy of each identical tone switch "
            "without changing the selected sound."
        ),
    ),
    RepairDefinition(
        rule_code="tones.changes-out-of-order",
        action_kind="reorder_tone_changes",
        source_kind="arrangement",
        item_name="tone-change timeline",
        safety="safe_automatic",
        title="Put tone changes in chronological order",
        description=(
            "Stable-sort a valid effective inline tone-change list by canonical "
            "finite numeric `t`, preserving every object and equal-time order."
        ),
        player_result=(
            "The same tone switches reach FeedBack in playback order, without "
            "changing their names, rigs, times, or unknown properties."
        ),
        user_value=(
            "Tone automation becomes portable and predictable without choosing or "
            "rewriting any authored sound."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="chart.duplicate-note",
        action_kind="remove_exact_duplicate_notes",
        source_kind="arrangement",
        item_name="note",
        safety="safe_automatic",
        title="Remove exact duplicate notes",
        description=(
            "Keep the first note and remove only copies with identical stored "
            "values and properties from the same note list."
        ),
        player_result=(
            "The song keeps one note at every repaired position. Its timing, "
            "fret, sustain, and techniques remain unchanged."
        ),
        user_value=(
            "The highway has one unambiguous gem to display and process instead "
            "of redundant copies of the same authored note."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-chord-note",
        action_kind="remove_exact_duplicate_chord_notes",
        source_kind="arrangement",
        item_name="chord note",
        safety="safe_automatic",
        title="Remove exact duplicate chord notes",
        description=(
            "Keep the first chord member and remove only identical copies from "
            "inside that same chord."
        ),
        player_result=(
            "The chord keeps the same strings, frets, timing, and techniques, "
            "with one stored instruction per intended chord member."
        ),
        user_value=(
            "The editor and highway no longer have redundant gems stacked on "
            "one string inside the chord."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-chord",
        action_kind="remove_exact_duplicate_chords",
        source_kind="arrangement",
        item_name="chord",
        safety="safe_automatic",
        title="Remove exact duplicate chords",
        description=(
            "Keep the first complete chord and remove only copies with identical "
            "timing, shape, notes, techniques, and stored properties from the same list."
        ),
        player_result=(
            "One complete chord remains at each repaired position with all of "
            "its authored notes and techniques unchanged."
        ),
        user_value=(
            "The editor and highway have one unambiguous chord event instead of "
            "processing identical copies at the same moment."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-anchor",
        action_kind="remove_exact_duplicate_anchors",
        source_kind="arrangement",
        item_name="anchor",
        safety="safe_automatic",
        title="Remove exact duplicate anchors",
        description=(
            "Keep the first anchor and remove only copies with identical timing, "
            "fret window, width, and stored properties from the same list."
        ),
        player_result=(
            "The same fret-window instruction remains at each repaired position."
        ),
        user_value=(
            "The highway receives one clear hand-position instruction without "
            "redundant anchor data."
        ),
    ),
    RepairDefinition(
        rule_code="chart.duplicate-handshape",
        action_kind="remove_exact_duplicate_handshapes",
        source_kind="arrangement",
        item_name="handshape",
        safety="safe_automatic",
        title="Remove exact duplicate handshapes",
        description=(
            "Keep the first handshape and remove only copies with identical chord, "
            "time span, and stored properties from the same list."
        ),
        player_result=(
            "The same chord-shape guide remains over the same time span."
        ),
        user_value=(
            "The highway has one clear shape guide instead of redundant overlay data."
        ),
    ),
    RepairDefinition(
        rule_code="chart.zero-length-handshape",
        action_kind="remove_redundant_zero_length_handshapes",
        source_kind="arrangement",
        item_name="zero-length handshape",
        safety="safe_automatic",
        title="Remove redundant zero-length handshapes",
        description=(
            "Remove a zero-duration, non-arpeggio handshape only when exactly one "
            "chord with the same template ID already exists at the exact same time "
            "in the same event list. Handshapes with unmatched chords, arpeggio "
            "intent, or additional stored properties are left unchanged."
        ),
        player_result=(
            "The matching authored chord remains at the same time with the same "
            "shape and playable notes. Only a redundant shape guide that has no "
            "duration is removed; any handshape that could supply a chord or other "
            "meaning remains for manual review."
        ),
        user_value=(
            "The chart no longer carries unusable zero-duration overlay records "
            "where the real chord already provides the complete player instruction."
        ),
        change_kind="remove_redundant",
    ),
    RepairDefinition(
        rule_code="chart.invalid-handshape-span",
        action_kind="remove_redundant_reversed_handshapes",
        source_kind="arrangement",
        item_name="reversed handshape",
        safety="safe_automatic",
        title="Remove redundant reversed handshapes",
        description=(
            "Remove a non-arpeggio handshape whose end precedes its start only "
            "when exactly one playable chord with the same template ID already "
            "exists at the exact same time in the same event list. Missing or "
            "negative times, unmatched chords, arpeggio intent, and additional "
            "stored properties remain unchanged for manual review."
        ),
        player_result=(
            "The matching authored chord remains at the same time with the same "
            "shape, notes, and techniques. Only a reversed-duration shape guide "
            "that cannot describe a playable interval is removed."
        ),
        user_value=(
            "The highway no longer receives an impossible backward shape interval "
            "where the complete playable chord already provides the instruction."
        ),
        change_kind="remove_redundant",
    ),
    RepairDefinition(
        rule_code="chart.note-duplicates-chord",
        action_kind="remove_notes_duplicating_chords",
        source_kind="arrangement",
        item_name="standalone note",
        safety="safe_automatic",
        title="Remove notes already contained in chords",
        description=(
            "Keep the complete chord and remove only standalone notes at the "
            "same time whose string, fret, and every stored playable property "
            "exactly match one explicit chord member."
        ),
        player_result=(
            "The complete chord remains at every repaired position, including "
            "all of its strings and techniques. Only the redundant standalone "
            "copy is removed."
        ),
        user_value=(
            "The editor and highway show one clear chord instruction instead "
            "of stacking an extra gem on one of the chord strings."
        ),
    ),
    RepairDefinition(
        rule_code="chart.bend-points-out-of-order",
        action_kind="reorder_bend_points",
        source_kind="arrangement",
        item_name="bend curve",
        safety="safe_automatic",
        title="Put bend points in chronological order",
        description=(
            "Stable-sort each affected bend curve by its existing relative "
            "timestamps. Every bend point and stored property is preserved, "
            "and points with equal timestamps keep their authored order."
        ),
        player_result=(
            "FeedBack receives each bend curve in playback order directly from "
            "the Feedpak instead of repairing its order temporarily while loading."
        ),
        user_value=(
            "Bend animation becomes portable and predictable in FeedBack, the "
            "editor, and other Feedpak tools without changing the authored curve."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="lyrics.out-of-order",
        action_kind="reorder_lyric_cues",
        source_kind="lyrics",
        item_name="lyric timeline",
        safety="safe_automatic",
        title="Put lyric cues in chronological order",
        description=(
            "Stable-sort the existing lyric cues by their start times. Every "
            "cue, word, duration, and stored property is preserved, and cues "
            "with equal start times keep their authored order."
        ),
        player_result=(
            "FeedBack receives the same lyric cues in playback order, so the "
            "lyric display no longer has to process a cue after a later one."
        ),
        user_value=(
            "Lyrics advance predictably with the song without deleting, "
            "rewriting, or retiming any authored text."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="timeline.duplicate-beat",
        action_kind="remove_exact_duplicate_beat_markers",
        source_kind="timeline",
        item_name="beat marker",
        safety="safe_automatic",
        title="Remove exact duplicate beat markers",
        description=(
            "Keep the first beat marker and remove only later copies with "
            "identical time, measure, and every other stored property from the "
            "active song timeline."
        ),
        player_result=(
            "The same beat and measure grid remains, with one stored instruction "
            "at each repaired position. Conflicting beat markers, if present, "
            "remain visible for manual review."
        ),
        user_value=(
            "FeedBack receives a clean rhythm grid without changing any authored "
            "beat time or measure and without guessing between conflicting data."
        ),
    ),
    RepairDefinition(
        rule_code="timeline.beats-out-of-order",
        action_kind="reorder_beat_markers",
        source_kind="timeline",
        item_name="beat timeline",
        safety="safe_automatic",
        title="Put beat markers in chronological order",
        description=(
            "Stable-sort the active song timeline's existing beat markers by "
            "their stored times. Every marker and property is preserved, and "
            "markers with equal times keep their authored relative order."
        ),
        player_result=(
            "FeedBack receives the same beat and measure instructions in "
            "chronological order, so rhythm-grid searches and measure tracking "
            "no longer jump backward."
        ),
        user_value=(
            "The highway gets a predictable rhythm grid without deleting, "
            "retiming, or inventing any beat marker."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="timeline.duplicate-section",
        action_kind="remove_exact_duplicate_section_markers",
        source_kind="timeline",
        item_name="section marker",
        safety="safe_automatic",
        title="Remove exact duplicate section markers",
        description=(
            "Keep the first section marker and remove only later copies with "
            "identical name, time, number, and every other stored property from "
            "the active song timeline."
        ),
        player_result=(
            "The same named song sections and boundaries remain, with one stored "
            "instruction at each repaired position. Conflicting section markers, "
            "if present, remain visible for manual review."
        ),
        user_value=(
            "FeedBack receives clean section and navigation data without changing "
            "any authored label or time and without guessing between conflicting data."
        ),
    ),
    RepairDefinition(
        rule_code="timeline.sections-out-of-order",
        action_kind="reorder_section_markers",
        source_kind="timeline",
        item_name="section timeline",
        safety="safe_automatic",
        title="Put section markers in chronological order",
        description=(
            "Stable-sort the active song timeline's existing section markers "
            "by their stored times. Every name, number, time, and additional "
            "property is preserved, and equal-time markers keep their authored "
            "relative order."
        ),
        player_result=(
            "FeedBack receives the same section boundaries in playback order, "
            "so section labels and navigation no longer jump backward."
        ),
        user_value=(
            "Song navigation and structure follow playback without deleting, "
            "renaming, or retiming any section marker."
        ),
        change_kind="reorder",
    ),
    RepairDefinition(
        rule_code="drums.duplicate-hit",
        action_kind="remove_exact_duplicate_drum_hits",
        source_kind="drum_tab",
        item_name="drum hit",
        safety="safe_automatic",
        title="Remove exact duplicate drum hits",
        description=(
            "Keep the first hit and remove only copies with identical stored "
            "values and properties from the same drum-hit list."
        ),
        player_result=(
            "The song keeps one drum hit at every repaired position, with the "
            "same timing and authored properties."
        ),
        user_value=(
            "The drum highway has one unambiguous hit to display and process "
            "instead of redundant copies."
        ),
    ),
)
MEDIA_REPAIR_DEFINITIONS = (
    RepairDefinition(
        rule_code="media.preview-missing",
        action_kind="create_song_preview",
        source_kind="full_mix",
        item_name="audio preview",
        safety="review_required",
        title="Create a song preview",
        description=(
            "Generate a 30-second Ogg excerpt from the full song mix and add it "
            "to the existing Feedpak. Manual review and automatic creation use "
            "the same selection standard."
        ),
        player_result=(
            "Library browsing can play a short representative excerpt for this song."
        ),
        user_value=(
            "The song becomes easier to recognize while browsing without changing gameplay audio."
        ),
        change_kind="replace_media",
    ),
    RepairDefinition(
        rule_code="media.preview-too-short",
        action_kind="replace_song_preview",
        source_kind="full_mix",
        item_name="audio preview",
        safety="review_required",
        title="Create a standard song preview",
        description=(
            "Generate a new 30-second Ogg excerpt from the full song mix. Manual "
            "review and automatic creation use the same selection standard."
        ),
        player_result=(
            "Library browsing plays a longer representative excerpt instead of an unusually short preview."
        ),
        user_value=(
            "The preview gives the player a more useful sense of the song while remaining compact."
        ),
        change_kind="replace_media",
    ),
    RepairDefinition(
        rule_code="media.preview-too-long",
        action_kind="replace_song_preview",
        source_kind="full_mix",
        item_name="audio preview",
        safety="review_required",
        title="Create a short song preview",
        description=(
            "Generate a new 30-second Ogg excerpt from the full song mix. Manual "
            "review and automatic creation use the same selection standard."
        ),
        player_result=(
            "Library browsing plays a compact representative excerpt instead of an unusually long preview."
        ),
        user_value=(
            "The Feedpak uses less unnecessary preview storage while preserving a useful sample."
        ),
        change_kind="replace_media",
    ),
    RepairDefinition(
        rule_code="media.preview-regenerate",
        action_kind="regenerate_song_preview",
        source_kind="full_mix",
        item_name="audio preview",
        safety="review_required",
        title="Create a different song preview",
        description=(
            "Generate a new 30-second Ogg excerpt from the full song mix even "
            "when the current preview already passes the duration policy."
        ),
        player_result=(
            "Library browsing plays the newly selected excerpt instead of the current preview."
        ),
        user_value=(
            "A technically valid but unhelpful preview can be replaced without editing the Feedpak manually."
        ),
        change_kind="replace_media",
    ),
)
