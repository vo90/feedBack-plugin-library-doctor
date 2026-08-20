import hashlib
import json
import py_compile
import re
import zipfile

from tools import build_release


SIBLING_REFERENCE = re.compile(
    r'(?:load_sibling\(["\']([^"\']+)["\']\)|with_name\(["\']([^"\']+\.py)["\']\))'
)


def test_release_zip_is_deterministic_allowlisted_and_installable(tmp_path):
    first = build_release.build_release(tmp_path / "first.zip")
    second = build_release.build_release(tmp_path / "second.zip")
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()

    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert f"{build_release.ARCHIVE_ROOT}/plugin.json" in names
        assert not any("tests/" in name or "node_modules/" in name or "__pycache__/" in name for name in names)
        assert not any(name.endswith("release-signoff.json") for name in names)

        plugins_dir = tmp_path / "feedback-desktop" / "plugins"
        archive.extractall(plugins_dir)

    plugin_dir = plugins_dir / build_release.ARCHIVE_ROOT
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "library_doctor"
    assert (plugin_dir / manifest["screen"]).is_file()
    assert (plugin_dir / manifest["script"]).is_file()
    assert (plugin_dir / manifest["routes"]).is_file()
    assert (plugin_dir / manifest["styles"]).is_file()

    for source in plugin_dir.glob("*.py"):
        py_compile.compile(str(source), doraise=True)


def test_release_zip_is_identical_across_line_ending_styles(tmp_path):
    lf_root = tmp_path / "lf"
    crlf_root = tmp_path / "crlf"
    for source in build_release.release_files():
        relative = source.relative_to(build_release.ROOT)
        lf_target = lf_root / relative
        crlf_target = crlf_root / relative
        lf_target.parent.mkdir(parents=True, exist_ok=True)
        crlf_target.parent.mkdir(parents=True, exist_ok=True)
        normalized = build_release._archive_bytes(source)
        lf_target.write_bytes(normalized)
        crlf_target.write_bytes(normalized.replace(b"\n", b"\r\n"))

    lf_zip = build_release.build_release(tmp_path / "lf.zip", root=lf_root)
    crlf_zip = build_release.build_release(tmp_path / "crlf.zip", root=crlf_root)

    assert lf_zip.read_bytes() == crlf_zip.read_bytes()


def test_release_contains_every_runtime_sibling_reference():
    relative_files = {
        path.relative_to(build_release.ROOT).as_posix()
        for path in build_release.release_files()
    }
    for source in build_release.ROOT.glob("*.py"):
        if source.name not in relative_files:
            continue
        for match in SIBLING_REFERENCE.finditer(source.read_text(encoding="utf-8")):
            sibling = f"{match.group(1)}.py" if match.group(1) else match.group(2)
            assert sibling in relative_files, f"{source.name} references omitted runtime file {sibling}"


def test_repository_root_also_matches_github_source_zip_layout():
    manifest = json.loads((build_release.ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "library_doctor"
    assert all((build_release.ROOT / name).is_file() for name in ("screen.html", "screen.js", "routes.py"))

    readme = (build_release.ROOT / "README.md").read_text(encoding="utf-8")
    assert "Code → Download ZIP" in readme
    assert "%APPDATA%\\feedback-desktop\\plugins" in readme
    assert "0.3.0-alpha.1" in readme
