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

    assert manifest["id"] == "library_health"
    assert re.fullmatch(r"[a-z][a-z0-9_]*", manifest["id"])
    assert manifest["name"] == "Library Health"
    assert manifest["version"] == "0.4.0"
    assert manifest["private"] is False
    assert manifest["nav"] == {
        "label": "Library Health",
        "screen": "plugin-library_health",
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
        "lh-filters", "lh-results", "lh-empty", "lh-results-error",
        "lh-result-count", "lh-pagination", "lh-prev", "lh-next",
        "lh-page-label",
    } <= parser.values


def test_screen_uses_desktop_pickers_and_sends_scan_target_in_json_body():
    script = (ROOT / "screen.js").read_text(encoding="utf-8")

    assert "window.feedBackDesktop.pickDirectory()" in script
    assert "window.feedBackDesktop.pickFile" in script
    assert "JSON.stringify(target)" in script
    assert "scope: state.targetKind" in script
