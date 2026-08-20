"""Build and verify the small public Library Doctor release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "feedBack-plugin-library-doctor"
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

ROOT_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "api_contracts.py",
    "batch_repair.py",
    "diagnostics.py",
    "library_doctor_report_cache.py",
    "library_doctor_scan_policy.py",
    "library_doctor_scan_worker.py",
    "migration.py",
    "mutation_receipts.py",
    "plugin.json",
    "preview_repair.py",
    "privacy.py",
    "repair.py",
    "repair_actions.py",
    "repair_catalog.py",
    "repair_eligibility.py",
    "repair_recovery.py",
    "repair_transaction.py",
    "repair_workspace.py",
    "repair_yaml.py",
    "requirements.txt",
    "reviewed_repair.py",
    "route_support.py",
    "routes.py",
    "scanner.py",
    "screen.html",
    "screen.js",
    "validator.py",
)
TREE_RULES = {
    "assets": {".css", ".svg"},
    "schemas": {".json", ".md", ""},
    "src": {".js"},
}
FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".test-artifacts",
    ".test-cache",
    "__pycache__",
    "dist",
    "docs",
    "node_modules",
    "tests",
}
JS_IMPORT = re.compile(r"\bfrom\s+['\"](\./[^'\"]+\.js)['\"]")


def release_files(root: Path = ROOT) -> tuple[Path, ...]:
    """Return the closed, sorted runtime allowlist."""
    files = [root / relative for relative in ROOT_FILES]
    for directory, suffixes in TREE_RULES.items():
        base = root / directory
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        )
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise ValueError(f"Release allowlist contains missing files: {missing}")
    return tuple(sorted(set(files), key=lambda path: path.relative_to(root).as_posix()))


def expected_members(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(
        f"{ARCHIVE_ROOT}/{path.relative_to(root).as_posix()}"
        for path in release_files(root)
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _archive_bytes(source: Path) -> bytes:
    """Return platform-independent bytes for an allowlisted text file."""
    data = source.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Release allowlist contains a non-UTF-8 file: {source}") from error
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def build_release(output: Path, root: Path = ROOT) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, member in zip(release_files(root), expected_members(root), strict=True):
            archive.writestr(_zip_info(member), _archive_bytes(source), compresslevel=9)
    verify_release(output, root)
    return output


def verify_release(archive_path: Path, root: Path = ROOT) -> dict[str, object]:
    expected = expected_members(root)
    with zipfile.ZipFile(archive_path) as archive:
        names = tuple(archive.namelist())
        if names != expected:
            raise ValueError("Release ZIP contents differ from the runtime allowlist")
        if len(names) != len(set(names)):
            raise ValueError("Release ZIP contains duplicate members")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe ZIP member: {name}")
            if FORBIDDEN_PARTS.intersection(path.parts):
                raise ValueError(f"Development or private data entered the ZIP: {name}")
        manifest_name = f"{ARCHIVE_ROOT}/plugin.json"
        manifest = json.loads(archive.read(manifest_name))
        if manifest.get("id") != "library_doctor" or not manifest.get("version"):
            raise ValueError("Release manifest identity or version is invalid")
        member_set = set(names)
        for name in names:
            if not name.endswith(".js"):
                continue
            source = archive.read(name).decode("utf-8")
            base = PurePosixPath(name).parent
            for relative in JS_IMPORT.findall(source):
                target = (base / relative).as_posix()
                if target not in member_set:
                    raise ValueError(f"Missing JavaScript import {target} referenced by {name}")
    return {
        "archive": str(archive_path),
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "files": len(expected),
        "version": manifest["version"],
    }


def default_output(root: Path = ROOT) -> Path:
    version = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
    return root / "dist" / f"feedBack-plugin-library-doctor-{version}.zip"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default_output())
    args = parser.parse_args()
    result = verify_release(build_release(args.output))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
