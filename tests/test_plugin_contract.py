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
    assert manifest["version"] == "0.17.0"
    assert manifest["private"] is False
    assert manifest["nav"] == {
        "label": "Library Doctor",
        "screen": "plugin-library_doctor",
    }
    for key in ("screen", "script", "styles", "routes", "icon"):
        _safe_plugin_file(manifest[key])
    assert manifest["styles"].startswith("assets/")
    assert manifest["icon"].startswith("assets/")


def test_screen_contains_every_element_required_by_the_script():
    parser = _Ids()
    parser.feed((ROOT / "screen.html").read_text(encoding="utf-8"))

    assert {
        "lh-targets", "lh-target-path", "lh-picker-note", "lh-choose-target",
        "lh-scan", "lh-scan-all", "lh-cancel", "lh-status",
        "lh-progress-count", "lh-progress", "lh-error", "lh-search",
        "lh-scan-warning", "lh-repair-result", "lh-scan-provenance",
        "lh-batch-section", "lh-batch-copy", "lh-batch-review",
        "lh-batch-cancel", "lh-batch-progress", "lh-batch-status",
        "lh-batch-count", "lh-batch-progress-bar", "lh-batch-preview",
        "lh-batch-result",
        "lh-filters", "lh-results", "lh-empty", "lh-results-error",
        "lh-result-count", "lh-pagination", "lh-prev", "lh-next",
        "lh-page-label", "lh-deep-audio", "lh-rule-summary", "lh-rule-empty",
        "lh-rule-error", "lh-rule-note", "lh-export-json", "lh-export-csv",
    } <= parser.values


def test_screen_uses_desktop_pickers_and_sends_scan_target_in_json_body():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")

    assert "window.feedBackDesktop.pickDirectory()" in script
    assert "window.feedBackDesktop.pickFile" in script
    assert "JSON.stringify(target)" in script
    assert "scope: state.targetKind" in script


def test_screen_prioritizes_active_song_sessions_over_scanning():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "library-doctor.css").read_text(encoding="utf-8")

    assert "window.feedBack.on('song:loading'" in script
    assert "window.feedBack.on('song:stop'" in script
    assert "from === 'player'" in script
    assert "request('/playback'" in script
    assert "schedulePlaybackSyncRetry" in script
    assert "playbackSyncRetryDelay = Math.min(5000" in script
    assert "Paused while a song is open" in script
    assert "Library Doctor scan paused · resumes when you exit" in script
    assert "You can keep playing while a scan is running" in screen
    assert ".lh-playback-notice" in styles


def test_screen_uses_catalog_labels_and_scoped_accessibility_announcements():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert "item.rule?.title" in script
    assert "finding.rule || {}" in script
    assert "function ruleLabel" not in script
    assert 'id="lh-status" role="status" aria-live="polite"' in screen
    assert 'class="lh-progress-panel" aria-live=' not in screen
    assert "No issues found by current checks" in screen


def test_screen_requires_a_preview_before_applying_supported_repairs():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")
    screen = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert "request('/repair/preview'" in script
    assert "request('/repair/apply'" in script
    assert script.index("request('/repair/preview'") < script.index("request('/repair/apply'")
    assert "Apply safe repair" in script
    assert re.search(r"recovery\s+backup", screen, re.IGNORECASE)
    assert "What Library Doctor found" in script
    assert "What you may notice in game" in script
    assert "Why fixing it matters" in script
    assert "What happens to the Feedpak" in script
    assert "request('/repair/history" in script
    assert "request('/repair/restore'" in script
    assert "function repairChangeCount" in script
    assert "without removing any bend points" in script
    assert "Every point and stored property is kept" in script


def test_screen_groups_cascading_duration_and_matching_muted_findings():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")

    assert "Content extends beyond the declared song duration" in script
    assert "Muted events have no playable fret or visible chord shape" in script
    assert "Deep audio verification was partial for this package" in script


def test_screen_groups_package_wide_repairs_into_one_control_per_rule():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")

    assert "function repairableFindingGroupNode" in script
    assert "const repairGroups = new Map()" in script
    assert "group && group.length > 1" in script
    assert "These arrangement-level findings share one package-wide repair" in script


def test_screen_offers_one_combined_transaction_for_multiple_safe_repair_types():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")

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
    script = (ROOT / "screen.js").read_text(encoding="utf-8")
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
    assert "Packages changed since repair will be excluded" in script
    assert "Currently repaired" in script
    assert "Originals restored" in script
    assert "Number(result.completed_count || 0)" in script
    assert "Package outcome details will return when the current operation finishes." in script
    assert "Safe batch repair" in screen
    assert ".lh-batch-progress" in styles
    assert ".lh-batch-undo-card" in styles


def test_package_badges_count_grouped_finding_cards_not_raw_arrangement_records():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")

    assert "const findingNodes = findings.length ? displayFindingNodes(report) : [];" in script
    assert "findingNodes.forEach((node) =>" in script
    assert "const counts = report.counts || {};" not in script
    assert "Affected ${arrangements.size ? 'arrangements' : 'source findings'}" in script
