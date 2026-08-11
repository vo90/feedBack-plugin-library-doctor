import json
import re
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).parents[1]


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = set()

    def handle_starttag(self, _tag, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.values.add(value)


def _source(path: str) -> str:
    if path == "screen.js":
        return "\n".join(
            source.read_text(encoding="utf-8")
            for source in sorted((ROOT / "src").glob("*.js"))
        )
    return (ROOT / path).read_text(encoding="utf-8")


def _safe_plugin_file(value: str) -> Path:
    relpath = PurePosixPath(value)
    assert not relpath.is_absolute()
    assert ".." not in relpath.parts
    assert "\\" not in value
    target = (ROOT / Path(*relpath.parts)).resolve()
    target.relative_to(ROOT.resolve())
    assert target.is_file()
    return target


def test_manifest_uses_stable_plugin_namespace_and_existing_files():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "library_doctor"
    assert re.fullmatch(r"[a-z][a-z0-9_]*", manifest["id"])
    assert manifest["name"] == "Library Doctor"
    assert manifest["version"] == "0.43.0"
    assert manifest["private"] is False
    assert manifest["scriptType"] == "module"
    assert manifest["minHost"] == "0.3.0-alpha.1"
    assert manifest["diagnostics"] == {"callable": "diagnostics:collect"}
    assert manifest["nav"] == {
        "label": "Library Doctor",
        "screen": "plugin-library_doctor",
    }
    for key in ("screen", "script", "styles", "routes", "icon"):
        _safe_plugin_file(manifest[key])
    _safe_plugin_file(manifest["diagnostics"]["callable"].partition(":")[0] + ".py")
    assert manifest["styles"].startswith("assets/")
    assert manifest["icon"].startswith("assets/")


def test_screen_contains_every_element_required_by_the_script():
    parser = _Ids()
    parser.feed((ROOT / "screen.html").read_text(encoding="utf-8"))

    assert {
        "lh-targets", "lh-target-path", "lh-picker-note", "lh-choose-target",
        "lh-scan", "lh-scan-all", "lh-cancel", "lh-status", "lh-scan-live",
        "lh-progress-count", "lh-progress", "lh-error", "lh-search",
        "lh-scan-warning", "lh-repair-result", "lh-scan-provenance",
        "lh-batch-section", "lh-batch-copy", "lh-batch-preview-media", "lh-batch-review",
        "lh-batch-cancel", "lh-batch-progress", "lh-batch-status",
        "lh-batch-count", "lh-batch-live-counts", "lh-batch-progress-bar", "lh-batch-preview",
        "lh-batch-result",
        "lh-filters", "lh-results", "lh-empty", "lh-results-error",
        "lh-result-count", "lh-pagination", "lh-prev", "lh-next",
        "lh-page-label", "lh-deep-audio", "lh-rule-summary", "lh-rule-empty",
        "lh-worker-mode", "lh-worker-limit", "lh-worker-limit-wrap",
        "lh-worker-summary",
        "lh-rule-error", "lh-rule-note", "lh-export-json", "lh-export-csv",
        "lh-workspace-tabs", "lh-health-workspace", "lh-song-tools-workspace",
        "lh-song-tool-search", "lh-song-tool-count", "lh-song-tool-results",
        "lh-song-tool-error", "lh-song-tool-pagination", "lh-song-tool-prev",
        "lh-song-tool-next", "lh-song-tool-page", "lh-song-tool-selection",
        "lh-guidance", "lh-guidance-title", "lh-guidance-copy",
        "lh-scan-options", "lh-scan-options-summary", "lh-overview",
        "lh-more-filters", "lh-scan-details", "lh-activity-section",
        "lh-activity-status",
        "lh-module-status",
    } <= parser.values


def test_phase1_browser_fixtures_cover_every_audited_shell_state():
    fixtures = json.loads(
        (ROOT / "tests" / "fixtures" / "phase1_browser_states.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(fixtures) == {
        "first_run", "cached_complete", "partial", "stale",
        "repair_receipt", "batch_ready", "song_tools",
    }
    script = _source("screen.js")
    for state_name in ("first_run", "scanning", "complete", "partial", "stale", "outcome"):
        assert f"'{state_name}'" in script


def test_phase1_shell_is_progressive_result_first_and_reversible():
    screen = _source("screen.html")
    script = _source("screen.js")

    assert "Check your song library for problems. Scanning never changes songs." in screen
    assert '<details id="lh-scan-options"' in screen
    assert '<details id="lh-more-filters"' in screen
    assert '<details id="lh-scan-details"' in screen
    assert screen.index('id="lh-results"') < screen.index('id="lh-batch-section"')
    assert screen.index('id="lh-health-workspace"') < screen.index('id="lh-repair-result"')
    assert screen.index('id="lh-repair-result"') < screen.index('id="lh-song-tools-workspace"')
    assert "libraryDoctorLayout" in script
    assert "legacy" in script


def test_phase1_uses_one_filter_surface_and_state_appropriate_accessibility():
    screen = _source("screen.html")
    script = _source("screen.js")

    assert "Needs fixing" in screen
    assert "May affect FeedBack" in screen
    assert "Optional improvements" in screen
    assert 'data-workspace="health" aria-pressed="true"' in screen
    assert 'data-workspace="tools" aria-pressed="false"' in screen
    assert 'id="lh-results" class="lh-results" aria-live=' not in screen
    assert 'id="lh-rule-summary" class="lh-rule-summary" aria-live=' not in screen
    assert 'id="lh-song-tool-selection" class="lh-song-tool-selection"\n             aria-live=' not in screen
    assert "button.setAttribute('aria-pressed'" in script


def test_phase1_makes_listen_first_preview_the_primary_action():
    script = _source("screen.js")

    assert "Listen and choose a preview" in script
    assert re.search(
        r"const manual = make\(\s*'button',\s*'lh-button lh-button-primary'",
        script,
    )
    assert "mediaRepair ? 'lh-button lh-button-primary'" in script
    assert script.index("Listen and choose a preview") < script.index(
        "Create automatically and finish"
    )


def test_screen_uses_desktop_pickers_and_sends_scan_target_in_json_body():
    script = _source("screen.js")

    assert "window.feedBackDesktop.pickDirectory()" in script
    assert "window.feedBackDesktop.pickFile" in script
    assert "JSON.stringify(target)" in script
    assert "scope: state.targetKind" in script


def test_scan_worker_selection_is_automatic_with_an_advanced_custom_ceiling():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert "Automatic (recommended)" in screen
    assert "Custom maximum" in screen
    assert "A custom value is a ceiling, not a forced count" in screen
    assert "target.max_workers = maximum" in script
    assert "status.worker_policy?.selected_workers" in script
    assert "library_doctor.scan.worker_mode" in script


def test_screen_prioritizes_active_song_sessions_over_scanning():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "library-doctor.css").read_text(encoding="utf-8")

    assert re.search(
        r"\['song:loading',\s*\(\) => playback\.setPlaybackPriority\(true\)\]",
        script,
    )
    assert re.search(
        r"\['song:stop',\s*\(\) => playback\.setPlaybackPriority\(false\)\]",
        script,
    )
    assert "const unsubscribe = window.feedBack.on(name, handler)" in script
    assert "from === 'player'" in script
    assert "requestGlobal('/playback'" in script
    assert "schedulePlaybackSyncRetry" in script
    assert "playbackSyncRetryDelay = Math.min(5000" in script
    assert "Paused while a song is open" in script
    assert "Library Doctor scan paused · resumes when you exit" in script
    assert "You can keep playing during a scan" in screen
    assert ".lh-playback-notice" in styles


def test_screen_uses_catalog_labels_and_scoped_accessibility_announcements():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert "item.rule?.title" in script
    assert "function currentRule(finding)" in script
    assert "state.ruleMetadata[item.code] = item.rule" in script
    assert "function ruleLabel" not in script
    assert 'id="lh-status" role="status"' not in screen
    assert 'id="lh-scan-live" class="lh-visually-hidden" role="status"' in screen
    assert 'aria-live="polite" aria-atomic="true"' in screen
    assert 'class="lh-progress-panel" aria-live=' not in screen
    assert "No issues found by current checks" in screen


def test_screen_requires_a_preview_before_applying_supported_repairs():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert "request('/repair/preview'" in script
    assert "request('/repair/apply'" in script
    assert script.index("request('/repair/preview'") < script.index("request('/repair/apply'")
    assert "Apply safe repair" in script
    assert re.search(r"recovery\s+data", screen, re.IGNORECASE)
    assert "What Library Doctor found" in script
    assert "What you may notice in game" in script
    assert "Why fixing it matters" in script
    assert "What happens to the Feedpak" in script
    assert "request('/repair/history" in script
    assert "request('/repair/restore'" in script
    assert "function repairChangeCount" in script
    assert "without deleting or altering any authored entries" in script
    assert "Every entry and stored property is kept" in script


def test_media_preview_requires_a_new_start_before_regenerating():
    script = _source("screen.js")

    assert "const displayedStart = Math.round" in script
    assert "nextStart === displayedStart" in script
    assert "input.addEventListener('input', syncRegenerate)" in script
    assert "syncRegenerate();" in script


def test_media_preview_offers_manual_and_confirmed_automatic_creation():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert "media.preview-missing" in script
    assert "Listen and choose a preview" in script
    assert "Create automatically and finish" in script
    assert "request('/repair/media/automatic'" in script
    assert script.index("Create the preview automatically?") < script.index(
        "request('/repair/media/automatic'"
    )
    assert "For a song shorter than 30 seconds" in script
    assert "the existing preview starting point is not reused" in script.lower()
    assert "Without previews" in screen


def test_batch_preview_repairs_are_explicit_opt_in_and_explain_recovery():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "library-doctor.css").read_text(encoding="utf-8")

    assert 'id="lh-batch-preview-media" type="checkbox"' in screen
    assert "Also repair flagged audio previews automatically" in screen
    assert "include_preview_repairs: !!el.batchPreviewMedia.checked" in script
    assert "Valid previews are untouched" in screen
    assert "are not included in Undo" in script
    assert "Finalized automatic previews remain in place" in script
    assert ".lh-batch-preview-option" in styles


def test_preview_tools_can_replace_a_valid_preview_and_show_the_finished_audio():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "library-doctor.css").read_text(encoding="utf-8")

    assert "function loadSongTools()" in script
    assert "function selectSongTool(song)" in script
    assert "function openPreviewCreator(song, trigger, region" in script
    assert "coreRequest(`/api/library?${params}`)" in script
    assert "/repair/media/tool/status?package=" in script
    assert "media.preview-regenerate" in script
    assert "Listen and choose a replacement preview" in script
    assert "Your finished preview" in script
    assert "/repair/media/current?package=" in script
    assert 'data-workspace="tools"' in screen
    assert 'id="lh-song-tools-workspace"' in screen
    assert ".lh-song-tools" in styles


def test_song_tools_keep_the_selected_panel_outside_the_result_list():
    script = _source("screen.js")
    styles = (ROOT / "assets" / "library-doctor.css").read_text(encoding="utf-8")

    assert "row.setAttribute('role', 'listitem')" in script
    assert "item.setAttribute('aria-expanded', String(selected))" in script
    assert "row.appendChild(el.songToolSelection)" not in script
    assert "focus(el.songToolSelection)" in script
    assert "titleHeading.id = 'lh-song-tool-selection-title'" in script
    assert "function closeSongToolSelection" in script
    assert "renderSongToolMenu(state.songTools.selected, { openTool: 'preview' })" in script
    assert "Available tools" in script
    assert "Opening Preview Creator..." in script
    assert script.index("function selectSongTool(song)") < script.index(
        "function refreshSelectedSongTool(packageName)"
    )
    assert ".lh-song-tool-row" in styles
    assert ".lh-song-tool-choice" in styles


def test_successful_preview_repair_finishes_without_a_pending_recovery_choice():
    script = _source("screen.js")

    assert "Create preview and finish" in script
    assert "Keep this preview" in script
    assert "function confirmReviewedPreviewRepair" in script
    assert "Replace the Feedpak preview?" in script
    assert "Confirm replacement and finish" in script
    assert script.index("Replace the Feedpak preview?") < script.index(
        "request('/repair/apply'"
    )
    assert "removed automatically after a successful repair" in script
    assert "Decide later" not in script


def test_repair_result_explains_persisted_validation_performance():
    script = _source("screen.js")

    assert "performance?.elapsed_seconds" in script
    assert "Repair checks:" in script
    assert "Reused the completed Deep Audio scan for unchanged audio" in script
    assert "Ran fresh Deep Audio validation" in script


def test_recovery_result_offers_explicit_undo_or_irreversible_finalization():
    script = _source("screen.js")

    assert "Undo repair" in script
    assert "Delete Undo backup…" in script
    assert "request('/repair/recovery/finalize'" in script
    assert "this repair can no longer be undone from Library Doctor" in script
    assert "The generated preview was removed" in script


def test_screen_groups_cascading_duration_and_matching_muted_findings():
    script = _source("screen.js")

    assert "Content extends beyond the declared song duration" in script
    assert "Muted events have no playable fret or visible chord shape" in script
    assert "Deep audio verification was partial for this package" in script


def test_screen_groups_package_wide_repairs_into_one_control_per_rule():
    script = _source("screen.js")

    assert "function repairableFindingGroupNode" in script
    assert "const repairGroups = new Map()" in script
    assert "group && group.length > 1" in script
    assert "These arrangement-level findings share one package-wide repair" in script


def test_screen_offers_one_combined_transaction_for_multiple_safe_repair_types():
    script = _source("screen.js")

    assert "report.features?.repair_scan_current === false" in script
    assert "Library Doctor checks were updated after this scan" in script
    assert "function allSafeRepairControls" in script
    assert "ruleCodes.size <= 1" in script
    assert "request('/repair/all/preview'" in script
    assert "request('/repair/all/apply'" in script
    assert script.index("request('/repair/all/preview'") < script.index(
        "request('/repair/all/apply'"
    )
    assert "one validation, one backup, and one Undo" in script
    assert "Apply all safe fixes" in script
    assert "repair_summaries" in script


def test_screen_requires_batch_preview_and_confirmation_with_progress_and_recovery():
    script = _source("screen.js")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "library-doctor.css").read_text(encoding="utf-8")

    assert "request('/repair/batch/preview'" in script
    assert "request('/repair/batch/apply'" in script
    assert script.index("request('/repair/batch/preview'") < script.index(
        "request('/repair/batch/apply'"
    )
    assert "Continue to confirmation" in script
    assert "Apply batch repair" in script
    assert "Stop after current Feedpak" in script
    assert "Library Doctor batch paused - resumes when you exit" in script
    assert "Review Undo" in script
    assert "request('/repair/batch/undo/preview'" in script
    assert "request('/repair/batch/undo/apply'" in script
    assert script.index("request('/repair/batch/undo/preview'") < script.index(
        "request('/repair/batch/undo/apply'"
    )
    assert "Continue to Undo confirmation" in script
    assert "Review Undo all remaining repairs" in script
    assert "request('/repair/batch/finalize/preview'" in script
    assert "request('/repair/batch/finalize/apply'" in script
    assert script.index("request('/repair/batch/finalize/preview'") < script.index(
        "request('/repair/batch/finalize/apply'"
    )
    assert "Review Finalize all remaining repairs" in script
    assert "Continue to finalization confirmation" in script
    assert "This cannot be undone" in script
    assert "Packages changed since repair will be excluded" in script
    assert "Currently repaired" in script
    assert "Originals restored" in script
    assert "Number(result.completed_count || 0)" in script
    assert "Package outcome details will return when the current operation finishes." in script
    assert "Review safe repairs" in screen
    assert ".lh-batch-progress" in styles
    assert ".lh-batch-undo-card" in styles


def test_package_badges_count_grouped_finding_cards_not_raw_arrangement_records():
    script = _source("screen.js")

    assert "const findingNodes = (findings.length || !report.features?.preview_declared)" in script
    assert "? actionRegistry.displayFindingNodes(report) : [];" in script
    assert "findingNodes.forEach((node) =>" in script
    assert "const counts = report.counts || {};" not in script
    assert "Affected ${arrangements.size ? 'arrangements' : 'source findings'}" in script
