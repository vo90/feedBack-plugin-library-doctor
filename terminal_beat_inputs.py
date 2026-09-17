"""Bounded, closed input inventory for terminal-tail evidence, without media reads."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import zipfile

MAX_MEMBERS = 10000
MAX_INPUTS = 1000
MAX_STRUCTURE_ITEMS = 2_000_000


def input_paths(manifest):
    result = []
    declarations = {}

    def add(path, role):
        if not valid_member_name(path) or not path.endswith(".json"):
            raise ValueError("Terminal beat repair requires ordinary declared JSON files.")
        previous = declarations.get(path.casefold())
        if previous is not None and previous != (path, role):
            raise ValueError("A JSON member has conflicting input roles or path spellings; manual review is required.")
        declarations[path.casefold()] = (path, role)
        result.append(path)
        if len(result) > MAX_INPUTS:
            raise ValueError("Too many declared terminal repair inputs; manual review is required.")

    for entry in manifest.get("arrangements", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ValueError("Every arrangement must declare a readable JSON file.")
        add(entry["file"], "arrangement")
    for key in ("song_timeline", "lyrics", "rigs"):
        if key in manifest:
            add(manifest[key], key)
    for track in manifest.get("lyric_tracks", []):
        if not isinstance(track, dict):
            raise ValueError("A lyric declaration is malformed.")
        add(track.get("file"), "lyrics")
    return list(dict.fromkeys(result))


def _walk_error(error):
    raise error


def valid_member_name(name):
    return (isinstance(name, str) and bool(name) and "\\" not in name
            and not name.startswith("/") and ":" not in name
            and all(p not in {"", ".", ".."} and p == p.rstrip(" .")
                    for p in name.split("/")))


def check_names(names):
    if (len(names) > MAX_MEMBERS or not all(valid_member_name(n) for n in names)
            or len({n.casefold() for n in names}) != len(names)):
        raise ValueError("An unsafe or ambiguous member inventory requires manual review.")


def package_inventory(package):
    """Do not follow links or silently omit inaccessible directories."""
    if package.is_symlink() or package.is_junction():
        raise ValueError("Linked packages require manual review.")
    if package.is_file():
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ValueError("Package inventory exceeds the terminal repair limit.")
            if any(stat.S_ISLNK(i.external_attr >> 16) for i in infos):
                raise ValueError("Linked package members require manual review.")
            check_names([i.filename[:-1] if i.is_dir() else i.filename for i in infos])
            names = [i.filename for i in infos if not i.is_dir()]
    else:
        names = []
        all_names = []
        for parent, dirs, files in os.walk(package, followlinks=False, onerror=_walk_error):
            for name in dirs + files:
                member = Path(parent) / name
                if member.is_symlink() or member.is_junction():
                    raise ValueError("Linked package members require manual review.")
                all_names.append(member.relative_to(package).as_posix())
            names.extend((Path(parent) / n).relative_to(package).as_posix() for n in files)
            if len(all_names) > MAX_MEMBERS:
                raise ValueError("Package inventory exceeds the terminal repair limit.")
        check_names(all_names)
    return sorted(names)


def check_inventory(manifest, names):
    check_names(names)
    known = set(input_paths(manifest)) | {"manifest.yaml"}
    for key in ("cover", "preview"):
        if key in manifest:
            known.add(manifest[key])
    for stem in manifest.get("stems", []):
        if not isinstance(stem, dict) or set(stem) - {"id", "file", "codec", "default"}:
            raise ValueError("Unknown stem metadata requires manual review.")
        known.add(stem.get("file"))
    if set(names) - known:
        raise ValueError("Undeclared package members may contain references; manual review is required.")


def check_structure(manifest, documents):
    """One aggregate parsed-value budget, shared by scan and repair assessment."""
    structure_size([manifest, *documents.values()])


def structure_size(document):
    count = 0
    stack = [document]
    seen = set()
    while stack:
        item = stack.pop()
        count += 1
        if count > MAX_STRUCTURE_ITEMS:
            raise ValueError("Terminal repair inputs exceed the shared structure safety limit.")
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            stack.extend(item.values() if isinstance(item, dict) else item)
    return count


class InputBudget:
    """Bound retained evidence before collecting the next input document."""

    def __init__(self, manifest):
        self.items = structure_size(manifest) + 1
        self.bytes = 0

    def reserve(self, size):
        self.bytes += size
        if self.bytes > 4 * 1024 * 1024 * 1024:
            raise ValueError("Terminal repair inputs exceed the aggregate read limit.")

    def include(self, document, size=0):
        self.reserve(size)
        self.items += structure_size(document)
        if self.items > MAX_STRUCTURE_ITEMS:
            raise ValueError("Terminal repair inputs exceed the shared structure safety limit.")
