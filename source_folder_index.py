"""Read-only, bounded candidate discovery; exact source matching stays elsewhere."""
import copy
import hashlib
import json
import os
import stat
from pathlib import Path


MAX_ARCHIVES = 10000
MAX_ENTRIES = 250000
MAX_DEPTH = 128
MAX_DETAILS = 1000


def topology_key(document):
    """A necessary, not sufficient, condition for source_chart.recovery_patch.

    Only flat ordered string/fret topology and chord membership are compared.
    A target may omit its difficulty ladder. Timing, techniques, tuning, names,
    and every bend field are left to the exact matcher, never guessed here.
    """
    if not isinstance(document, dict):
        raise ValueError("An arrangement must be a JSON object.")
    digest = hashlib.sha256()

    def member(note):
        if (not isinstance(note, dict) or type(note.get("s")) is not int
                or type(note.get("f")) is not int):
            raise ValueError("An event has an ambiguous string or fret.")
        fret = 0 if note["f"] == 127 and note.get("mt") is True else note["f"]
        return note["s"], fret

    for field in ("notes", "chords"):
        values = document.get(field, [])
        if not isinstance(values, list):
            raise ValueError("An arrangement event collection must be a list.")
        digest.update(f"{field}:{len(values)}:".encode())
        for value in values:
            if field == "notes":
                identity = member(value)
            else:
                if not isinstance(value, dict) or not isinstance(value.get("notes", []), list):
                    raise ValueError("A chord must contain an ordinary note list.")
                identity = [member(note) for note in value.get("notes", [])]
            digest.update(json.dumps(identity, separators=(",", ":")).encode())
            digest.update(b";")
    return digest.hexdigest()


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


class SourceFolderIndex:
    """Keeps hashes and coarse keys only; injected archive adapters may cache.

    An incomplete summary must block automatic uniqueness claims. Unreadable
    archives could contain another matching version. Linked entries are not
    followed and also make the selected scope incomplete. A single bounded
    archive-reader call is synchronous; cancellation is checked around it.
    """

    def __init__(self, *, archive, chart):
        self.archive, self.chart = archive, chart
        self._groups, self._by_key, self._members = {}, {}, {}
        self._summary = None

    @property
    def summary(self):
        return copy.deepcopy(self._summary)

    def build(self, folder, cancel_event=None, on_progress=None, *, selected_paths=None):
        self._groups, self._by_key, self._members = {}, {}, {}
        self._summary = None
        selected = Path(folder).expanduser()
        try:
            ordinary_folder = not _linked(selected) and selected.is_dir()
        except OSError:
            ordinary_folder = False
        if not ordinary_folder:
            raise ValueError("Select an existing ordinary folder of original PSARC files.")
        root = selected.resolve(strict=True)
        summary = self._summary = {
            "folder": str(root), "complete": False, "cancelled": False,
            "scope": "selected_sources" if selected_paths is not None else "folder",
            "archive_count": 0, "indexed_archive_count": 0, "unique_archive_count": 0,
            "duplicate_archive_count": 0, "chart_count": 0, "errors": [], "error_count": 0,
            "skipped_links": [], "skipped_link_count": 0, "limit_reached": False,
            "errors_truncated": False, "skipped_links_truncated": False,
            "empty_members": [], "empty_member_count": 0, "empty_members_truncated": False,
        }

        def cancelled():
            if cancel_event is not None and cancel_event.is_set():
                summary["cancelled"] = True
            return summary["cancelled"]

        def progress(phase, current=""):
            if on_progress is not None and on_progress({
                "phase": phase, "current": current,
                "done": summary["indexed_archive_count"], "total": summary["archive_count"],
                "error_count": summary["error_count"],
            }) is False:
                summary["cancelled"] = True
            return not cancelled()

        def error(relative, message, *, limit=False):
            summary["error_count"] += 1
            if len(summary["errors"]) < MAX_DETAILS:
                summary["errors"].append({"relative_path": relative, "message": str(message)[:1000]})
            else:
                summary["errors_truncated"] = True
            summary["limit_reached"] |= limit

        def skip_link(relative):
            summary["skipped_link_count"] += 1
            if len(summary["skipped_links"]) < MAX_DETAILS:
                summary["skipped_links"].append(relative)
            else:
                summary["skipped_links_truncated"] = True

        paths, pending, visited = [], [(root, 0)], 0
        if selected_paths is not None:
            if not isinstance(selected_paths, list) or len(selected_paths) > MAX_ARCHIVES:
                raise ValueError("Reuse requires at most 10,000 selected source paths.")
            pending = []  # Recheck the chosen sources; never search the folder again.
            for value in selected_paths:
                if cancelled():
                    break
                path = Path(value).absolute()
                paths.append(path)
            paths = list(dict.fromkeys(paths))
            summary["archive_count"] = len(paths)
        while pending and not cancelled() and not summary["limit_reached"]:
            directory, depth = pending.pop()
            relative = directory.relative_to(root).as_posix()
            if not progress("discovering", relative):
                break
            try:
                if _linked(directory):
                    skip_link(relative)
                    continue
                directory.resolve(strict=True).relative_to(root)
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if cancelled():
                            break
                        visited += 1
                        if visited > MAX_ENTRIES:
                            error(relative, "The source folder exceeds the supported entry limit.", limit=True)
                            break
                        path = Path(entry.path)
                        rel = path.relative_to(root).as_posix()
                        try:
                            if _linked(path):
                                skip_link(rel)
                            elif entry.is_dir(follow_symlinks=False):
                                if depth >= MAX_DEPTH:
                                    error(rel, "The source folder exceeds the supported nesting limit.", limit=True)
                                    break
                                pending.append((path, depth + 1))
                            elif entry.is_file(follow_symlinks=False) and path.suffix.lower() == ".psarc":
                                if len(paths) >= MAX_ARCHIVES:
                                    error(rel, "The source folder exceeds the 10,000 archive limit.", limit=True)
                                    break
                                paths.append(path)
                                summary["archive_count"] = len(paths)
                        except (OSError, ValueError) as exc:
                            error(rel, exc)
            except (OSError, ValueError) as exc:
                error(relative, exc)

        for path in sorted(paths, key=lambda p: p.as_posix()):
            if cancelled() or summary["limit_reached"]:
                break
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = str(path)
            if not progress("indexing", relative):
                break
            try:
                if path.suffix.lower() != ".psarc":
                    raise ValueError("A selected source must be an original PSARC.")
                self._check_path(path, root)
                member_keys, chart_count = {}, 0
                def visit(source):
                    nonlocal chart_count
                    if not progress("indexing", relative):
                        return False
                    chart_count += 1
                    if chart_count > 1024:
                        raise ValueError("The source reader exceeded its 1,024 chart inspection bound.")
                    member = source["member"]
                    if not isinstance(member, str) or not member:
                        raise ValueError("The source reader returned an invalid chart member name.")
                    key = topology_key(self.chart.source_document(source["song"], include_bends=False))
                    member_keys.setdefault(key, set()).add(member)
                    return not cancelled()
                inspector = getattr(self.archive, "inspect_source_charts", None)
                if callable(inspector):
                    result = inspector(str(path), visit)
                    if result.get("cancelled"):
                        summary["cancelled"] = True
                    elif not result.get("complete"):
                        raise ValueError("The source reader did not complete this archive's inspection.")
                else:
                    result = self.archive.read_source_charts(str(path))
                    charts = result["charts"]
                    if not isinstance(charts, list) or len(charts) > 128:
                        raise ValueError("The source reader exceeded its selected chart arrangement bound.")
                    for source in charts:
                        if visit(source) is False:
                            break
                if cancelled():
                    break
                self._check_path(path, root)
                if Path(result["path"]).resolve(strict=True) != path:
                    raise ValueError("The inspected archive path changed during indexing.")
                sha = result["sha256"]
                if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                    raise ValueError("The source reader returned an invalid content hash.")
                for member in result.get("empty_members", []):
                    summary["empty_member_count"] += 1
                    if len(summary["empty_members"]) < MAX_DETAILS:
                        summary["empty_members"].append({"relative_path": relative, "member": member})
                    else:
                        summary["empty_members_truncated"] = True
                if sha in self._groups:
                    self._groups[sha]["duplicate_paths"].append(str(path))
                    summary["duplicate_archive_count"] += 1
                else:
                    self._groups[sha] = {"path": str(path), "sha256": sha,
                                         "relative_path": relative, "duplicate_paths": []}
                    self._members[sha] = member_keys
                    for key in member_keys:
                        self._by_key.setdefault(key, set()).add(sha)
                    summary["unique_archive_count"] += 1
                    summary["chart_count"] += chart_count
                summary["indexed_archive_count"] += 1
            except Exception as exc:
                # The bounded decoder uses several parser-specific exception
                # types. One malformed source must not abort the folder batch.
                error(relative, exc)
            finally:
                # Never retain parsed source Songs beyond an injected bounded cache.
                result = None
        summary["complete"] = not (summary["cancelled"] or summary["error_count"]
                                   or summary["skipped_link_count"] or summary["limit_reached"])
        return copy.deepcopy(summary)

    @staticmethod
    def _check_path(path, root):
        path.relative_to(root)  # Reject escapes before inspecting an outside path.
        if ".." in path.parts:
            raise ValueError("A selected source must stay inside its reviewed folder.")
        for parent in (path, *path.parents):
            if _linked(parent):
                raise ValueError("A linked archive or parent requires a new folder review.")
            if parent == root:
                break
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError("The source archive moved or no longer resolves inside the selected folder.")
        path.relative_to(root)

    def candidates(self, documents):
        """Return content-distinct coarse candidates compatible with every chart.

        Empty input has no candidates. Duplicated source files remain visible in
        duplicate_paths. The caller must inspect summary.complete and exact-match
        ALL candidates before claiming uniqueness or applying a repair.
        """
        if self._summary is None:
            raise ValueError("Build the selected source-folder index first.")
        if not isinstance(documents, list):
            raise ValueError("Provide the package's pitched arrangement documents.")
        if not documents:
            return []
        keys = {topology_key(document) for document in documents}
        matching = set.intersection(*(self._by_key.get(key, set()) for key in keys))
        return [{**copy.deepcopy(self._groups[sha]), "members": sorted(set().union(
            *(self._members[sha][key] for key in keys)))} for sha in sorted(
                matching, key=lambda value: self._groups[value]["relative_path"])]
