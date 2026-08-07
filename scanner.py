"""Background library scanning and the incremental report cache."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path


SONG_SUFFIXES = (".feedpak", ".sloppak")
TARGET_KINDS = {"library", "folder", "file"}
RESULT_FILTERS = {
    "all", "problems", "errors", "warnings", "healthy",
    "no_lyrics", "no_preview",
}


class _ReportCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._conn as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    package TEXT PRIMARY KEY,
                    signature TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    warning_count INTEGER NOT NULL DEFAULT 0,
                    info_count INTEGER NOT NULL DEFAULT 0,
                    lyrics_declared INTEGER NOT NULL DEFAULT 0,
                    preview_declared INTEGER NOT NULL DEFAULT 0,
                    scanned_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS reports_status_idx "
                "ON reports(error_count, warning_count)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS reports_title_idx "
                "ON reports(title COLLATE NOCASE, artist COLLATE NOCASE)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS current_scope (
                    package TEXT PRIMARY KEY
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            initialized = conn.execute(
                "SELECT 1 FROM cache_state WHERE key = 'scope_initialized'"
            ).fetchone()
            if initialized is None:
                conn.execute(
                    "INSERT OR IGNORE INTO current_scope(package) "
                    "SELECT package FROM reports"
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                    (
                        ("scope_initialized", "1"),
                        ("target_kind", "library"),
                        ("target_label", "Whole library"),
                    ),
                )

    def cached(self, package: str, signature: str, validator_version: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM reports "
                "WHERE package = ? AND signature = ? AND validator_version = ?",
                (package, signature, validator_version),
            ).fetchone()
        return row is not None

    def put(
        self,
        package: str,
        signature: str,
        validator_version: str,
        report: dict,
        scanned_at: float,
    ) -> None:
        counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
        features = report.get("features") if isinstance(report.get("features"), dict) else {}
        payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self._lock, self._conn as conn:
            conn.execute(
                """
                INSERT INTO reports (
                    package, signature, validator_version, report_json,
                    title, artist, status, error_count, warning_count,
                    info_count, lyrics_declared, preview_declared, scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(package) DO UPDATE SET
                    signature = excluded.signature,
                    validator_version = excluded.validator_version,
                    report_json = excluded.report_json,
                    title = excluded.title,
                    artist = excluded.artist,
                    status = excluded.status,
                    error_count = excluded.error_count,
                    warning_count = excluded.warning_count,
                    info_count = excluded.info_count,
                    lyrics_declared = excluded.lyrics_declared,
                    preview_declared = excluded.preview_declared,
                    scanned_at = excluded.scanned_at
                """,
                (
                    package,
                    signature,
                    validator_version,
                    payload,
                    str(report.get("title") or "")[:500],
                    str(report.get("artist") or "")[:500],
                    str(report.get("status") or "error"),
                    int(counts.get("error") or 0),
                    int(counts.get("warning") or 0),
                    int(counts.get("info") or 0),
                    int(bool(features.get("lyrics_declared"))),
                    int(bool(features.get("preview_declared"))),
                    scanned_at,
                ),
            )

    def delete_stale(self, current_packages: set[str]) -> int:
        with self._lock, self._conn as conn:
            rows = conn.execute("SELECT package FROM reports").fetchall()
            stale = [(row["package"],) for row in rows if row["package"] not in current_packages]
            if stale:
                conn.executemany("DELETE FROM reports WHERE package = ?", stale)
        return len(stale)

    def replace_scope(self, packages: set[str], *, kind: str, label: str) -> None:
        with self._lock, self._conn as conn:
            conn.execute("DELETE FROM current_scope")
            if packages:
                conn.executemany(
                    "INSERT INTO current_scope(package) VALUES (?)",
                    ((package,) for package in sorted(packages, key=str.casefold)),
                )
            conn.executemany(
                "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                (("target_kind", kind), ("target_label", label)),
            )

    def current_target(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM cache_state "
                "WHERE key IN ('target_kind', 'target_label')"
            ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        return {
            "kind": values.get("target_kind", "library"),
            "label": values.get("target_label", "Whole library"),
        }

    def summary(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN error_count = 0 AND warning_count = 0 THEN 1 ELSE 0 END) AS healthy,
                    SUM(CASE WHEN error_count > 0 THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN error_count = 0 AND warning_count > 0 THEN 1 ELSE 0 END) AS warnings,
                    COALESCE(SUM(error_count), 0) AS error_findings,
                    COALESCE(SUM(warning_count), 0) AS warning_findings,
                    SUM(CASE WHEN lyrics_declared = 0 THEN 1 ELSE 0 END) AS no_lyrics,
                    SUM(CASE WHEN preview_declared = 0 THEN 1 ELSE 0 END) AS no_preview,
                    MAX(scanned_at) AS last_scanned_at
                FROM reports AS r
                INNER JOIN current_scope AS s ON s.package = r.package
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "healthy": int(row["healthy"] or 0),
            "errors": int(row["errors"] or 0),
            "warnings": int(row["warnings"] or 0),
            "error_findings": int(row["error_findings"] or 0),
            "warning_findings": int(row["warning_findings"] or 0),
            "no_lyrics": int(row["no_lyrics"] or 0),
            "no_preview": int(row["no_preview"] or 0),
            "last_scanned_at": row["last_scanned_at"],
        }

    @staticmethod
    def _where(result_filter: str, query: str) -> tuple[str, list[str]]:
        clauses = []
        params: list[str] = []
        if result_filter == "problems":
            clauses.append("(r.error_count > 0 OR r.warning_count > 0)")
        elif result_filter == "errors":
            clauses.append("r.error_count > 0")
        elif result_filter == "warnings":
            clauses.append("r.error_count = 0 AND r.warning_count > 0")
        elif result_filter == "healthy":
            clauses.append("r.error_count = 0 AND r.warning_count = 0")
        elif result_filter == "no_lyrics":
            clauses.append("r.lyrics_declared = 0")
        elif result_filter == "no_preview":
            clauses.append("r.preview_declared = 0")
        if query:
            clauses.append(
                "(LOWER(r.package) LIKE ? ESCAPE '\\' OR LOWER(r.title) LIKE ? ESCAPE '\\' "
                "OR LOWER(r.artist) LIKE ? ESCAPE '\\')"
            )
            escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%"] * 3)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def results(
        self,
        *,
        result_filter: str,
        query: str,
        limit: int,
        offset: int,
    ) -> dict:
        where, params = self._where(result_filter, query)
        order = (
            " ORDER BY CASE WHEN r.error_count > 0 THEN 0 "
            "WHEN r.warning_count > 0 THEN 1 ELSE 2 END, "
            "r.artist COLLATE NOCASE, r.title COLLATE NOCASE, r.package COLLATE NOCASE"
        )
        source = (
            " FROM reports AS r "
            "INNER JOIN current_scope AS s ON s.package = r.package"
        )
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*){source}{where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT r.report_json, r.scanned_at{source}{where}{order} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            try:
                report = json.loads(row["report_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            report["scanned_at"] = row["scanned_at"]
            items.append(report)
        return {"total": int(total), "limit": limit, "offset": offset, "items": items}


class LibraryScanner:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_dlc_dir,
        validate_feedpak,
        validator_version: str,
        log,
    ) -> None:
        self._get_dlc_dir = get_dlc_dir
        self._validate_feedpak = validate_feedpak
        self._validator_version = validator_version
        self._log = log
        self._cache = _ReportCache(Path(config_dir) / "library_health" / "library_health.db")
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = self._initial_status(self._cache.current_target())

    @staticmethod
    def _initial_status(target: dict | None = None) -> dict:
        return {
            "schema": "library_health.scan.v1",
            "running": False,
            "stage": "idle",
            "total": 0,
            "done": 0,
            "scanned": 0,
            "reused": 0,
            "current": "",
            "cancelled": False,
            "error": "",
            "started_at": None,
            "completed_at": None,
            "target": target or {"kind": "library", "label": "Whole library"},
        }

    def status(self) -> dict:
        with self._lock:
            status = dict(self._status)
        status["summary"] = self._cache.summary()
        return status

    def start(
        self,
        *,
        force: bool = False,
        target_kind: str = "library",
        selected_path: str | None = None,
    ) -> bool:
        with self._lock:
            if self._status["running"]:
                return False
            root, target_path, target = self._resolve_target(target_kind, selected_path)
            self._cancel.clear()
            self._status = {
                **self._initial_status(target),
                "running": True,
                "stage": "discovering",
                "started_at": time.time(),
            }
            self._thread = threading.Thread(
                target=self._run,
                kwargs={
                    "force": force,
                    "root": root,
                    "target_path": target_path,
                    "target": target,
                },
                name="library-health-scan",
                daemon=True,
            )
            self._thread.start()
        return True

    def cancel(self) -> bool:
        with self._lock:
            if not self._status["running"]:
                return False
            self._cancel.set()
            self._status["stage"] = "cancelling"
        return True

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def results(
        self,
        *,
        result_filter: str = "all",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if result_filter not in RESULT_FILTERS:
            raise ValueError(f"Unknown result filter: {result_filter}")
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        query = str(query).strip()[:200]
        return self._cache.results(
            result_filter=result_filter,
            query=query,
            limit=limit,
            offset=offset,
        )

    def _set_status(self, **changes) -> None:
        with self._lock:
            self._status.update(changes)

    def _resolve_target(
        self,
        target_kind: str,
        selected_path: str | None,
    ) -> tuple[Path, Path, dict]:
        kind = str(target_kind or "").strip().lower()
        if kind not in TARGET_KINDS:
            raise ValueError("Choose the whole library, a folder, or a Feedpak file.")

        root_value = self._get_dlc_dir()
        if not root_value:
            raise ValueError("No song library folder is configured in FeedBack Settings.")
        configured_root = Path(root_value).absolute()
        try:
            root = configured_root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("The configured song library folder is unavailable.") from exc
        if not root.is_dir():
            raise ValueError("The configured song library folder is unavailable.")

        if kind == "library":
            return root, root, {"kind": kind, "label": "Whole library"}

        if not isinstance(selected_path, str) or not selected_path.strip():
            noun = "folder" if kind == "folder" else "Feedpak file"
            raise ValueError(f"Choose a {noun} inside the configured song library.")
        if len(selected_path) > 4_096 or "\0" in selected_path:
            raise ValueError("The selected path is invalid.")

        candidate = Path(selected_path.strip())
        if not candidate.is_absolute():
            candidate = configured_root / candidate
        lexical = candidate.absolute()
        try:
            lexical.relative_to(configured_root)
        except ValueError:
            try:
                lexical.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    "Choose an item inside the configured song library."
                ) from exc

        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "The selected item is unavailable or outside the configured song library."
            ) from exc

        if kind == "folder":
            if not resolved.is_dir():
                raise ValueError("The selected scan target is not a folder.")
            if resolved == root:
                return root, root, {"kind": "library", "label": "Whole library"}
        else:
            if not resolved.is_file() or resolved.suffix.lower() not in SONG_SUFFIXES:
                raise ValueError("Choose a .feedpak or .sloppak file.")

        label = relative.as_posix() or "Whole library"
        return root, resolved, {"kind": kind, "label": label}

    @staticmethod
    def _discover(root: Path) -> list[Path]:
        packages: list[Path] = []

        def fail(error: OSError) -> None:
            raise error

        for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=fail, followlinks=False):
            parent = Path(dirpath)
            package_dirs = [name for name in dirnames if name.lower().endswith(SONG_SUFFIXES)]
            for name in package_dirs:
                packages.append(parent / name)
            dirnames[:] = [name for name in dirnames if name not in package_dirs]
            packages.extend(
                parent / name
                for name in filenames
                if name.lower().endswith(SONG_SUFFIXES)
            )
        packages.sort(key=lambda path: path.as_posix().casefold())
        return packages

    @classmethod
    def _discover_target(cls, target_path: Path, target_kind: str) -> list[Path]:
        if target_kind == "file":
            return [target_path]
        if target_path.name.lower().endswith(SONG_SUFFIXES):
            return [target_path]
        return cls._discover(target_path)

    @staticmethod
    def _signature(path: Path) -> str:
        if not path.is_dir():
            stat = path.stat()
            return f"f:{stat.st_mtime_ns}:{stat.st_size}"
        digest = hashlib.blake2b(digest_size=16)
        root = path.resolve()
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            parent = Path(dirpath)
            for name in sorted(filenames, key=str.casefold):
                member = parent / name
                try:
                    stat = member.stat()
                    relpath = member.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                digest.update(relpath.encode("utf-8", "surrogatepass"))
                digest.update(b"\0")
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
                digest.update(b":")
                digest.update(str(stat.st_size).encode("ascii"))
                digest.update(b"\0")
        return "d:" + digest.hexdigest()

    def _failure_report(self, package: str, exc: Exception) -> dict:
        message = f"Validation failed unexpectedly ({type(exc).__name__}: {exc})."
        return {
            "schema": "library_health.package.v1",
            "validator_version": self._validator_version,
            "package": package,
            "title": "",
            "artist": "",
            "status": "error",
            "counts": {"error": 1, "warning": 0, "info": 0},
            "features": {
                "lyrics_declared": False,
                "lyrics_entries": 0,
                "preview_declared": False,
                "preview_available": False,
            },
            "findings": [{
                "severity": "error",
                "code": "scan.validation-failed",
                "message": message[:1_000],
                "location": "",
                "arrangement_id": None,
                "time": None,
                "string": None,
            }],
        }

    def _run(
        self,
        *,
        force: bool,
        root: Path,
        target_path: Path,
        target: dict,
    ) -> None:
        try:
            packages = self._discover_target(target_path, target["kind"])
            relative = [path.relative_to(root).as_posix() for path in packages]
            self._set_status(stage="scanning", total=len(packages))

            scanned = 0
            reused = 0
            completed_packages: set[str] = set()
            for index, (path, package_name) in enumerate(zip(packages, relative)):
                if self._cancel.is_set():
                    self._cache.replace_scope(
                        completed_packages,
                        kind=target["kind"],
                        label=target["label"],
                    )
                    self._set_status(
                        running=False,
                        stage="cancelled",
                        cancelled=True,
                        current="",
                        completed_at=time.time(),
                    )
                    return
                self._set_status(current=package_name)
                try:
                    signature = self._signature(path)
                    if not force and self._cache.cached(
                        package_name, signature, self._validator_version
                    ):
                        reused += 1
                    else:
                        try:
                            report = self._validate_feedpak(path, package_name)
                        except Exception as exc:  # One third-party rule must not abort the batch.
                            self._log.warning(
                                "Library Health validation failed for %s: %s",
                                package_name,
                                exc,
                            )
                            report = self._failure_report(package_name, exc)
                        self._cache.put(
                            package_name,
                            signature,
                            self._validator_version,
                            report,
                            time.time(),
                        )
                        scanned += 1
                    completed_packages.add(package_name)
                except OSError as exc:
                    report = self._failure_report(package_name, exc)
                    fallback_signature = f"unreadable:{time.time_ns()}"
                    self._cache.put(
                        package_name,
                        fallback_signature,
                        self._validator_version,
                        report,
                        time.time(),
                    )
                    scanned += 1
                    completed_packages.add(package_name)
                self._set_status(done=index + 1, scanned=scanned, reused=reused)

            if target["kind"] == "library":
                self._cache.delete_stale(completed_packages)
            self._cache.replace_scope(
                completed_packages,
                kind=target["kind"],
                label=target["label"],
            )
            self._set_status(
                running=False,
                stage="complete",
                current="",
                completed_at=time.time(),
            )
        except Exception as exc:
            self._log.warning("Library Health scan failed: %s", exc)
            self._set_status(
                running=False,
                stage="error",
                current="",
                error=str(exc)[:1_000],
                completed_at=time.time(),
            )
