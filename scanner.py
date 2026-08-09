"""Background library scanning and the incremental report cache."""

from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
import os
import sqlite3
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath


SONG_SUFFIXES = (".feedpak", ".sloppak")
TARGET_KINDS = {"library", "folder", "file"}
RESULT_FILTERS = {
    "all", "problems", "errors", "warnings", "review", "healthy",
    "no_lyrics", "no_preview", "deep_audio_partial",
}
SIGNATURE_SAMPLE_BYTES = 8 * 1024
SIGNATURE_FULL_FILE_BYTES = 1024 * 1024
MAX_SIGNATURE_MEMBERS = 50_000
MAX_DISCOVERY_ERRORS = 20
MAX_BATCH_SCOPE_PACKAGES = 10_000


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
                    deep_audio_skipped INTEGER NOT NULL DEFAULT 0,
                    deep_audio_unsupported INTEGER NOT NULL DEFAULT 0,
                    scanned_at REAL NOT NULL
                )
                """
            )
            report_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(reports)")
            }
            for name in ("deep_audio_skipped", "deep_audio_unsupported"):
                if name not in report_columns:
                    conn.execute(
                        f"ALTER TABLE reports ADD COLUMN {name} "
                        "INTEGER NOT NULL DEFAULT 0"
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
                CREATE TABLE IF NOT EXISTS report_findings (
                    package TEXT NOT NULL,
                    code TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    finding_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (package, code)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS report_findings_code_idx "
                "ON report_findings(code, package)"
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
            coverage_index = conn.execute(
                "SELECT value FROM cache_state WHERE key = 'audio_coverage_index_version'"
            ).fetchone()
            if coverage_index is None or coverage_index["value"] != "1":
                coverage_rows = []
                for row in conn.execute("SELECT package, report_json FROM reports"):
                    try:
                        report = json.loads(row["report_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    features = (
                        report.get("features")
                        if isinstance(report.get("features"), dict)
                        else {}
                    )
                    coverage_rows.append((
                        int(features.get("deep_audio_skipped") or 0),
                        int(features.get("deep_audio_unsupported") or 0),
                        row["package"],
                    ))
                conn.executemany(
                    "UPDATE reports SET deep_audio_skipped = ?, "
                    "deep_audio_unsupported = ? WHERE package = ?",
                    coverage_rows,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                    ("audio_coverage_index_version", "1"),
                )
            finding_index = conn.execute(
                "SELECT value FROM cache_state WHERE key = 'finding_index_version'"
            ).fetchone()
            if finding_index is None or finding_index["value"] != "2":
                conn.execute("DELETE FROM report_findings")
                for row in conn.execute("SELECT package, report_json FROM reports"):
                    try:
                        report = json.loads(row["report_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    conn.executemany(
                        "INSERT INTO report_findings "
                        "(package, code, severity, category, finding_count) "
                        "VALUES (?, ?, ?, ?, ?)",
                        self._finding_rows(row["package"], report),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                    ("finding_index_version", "2"),
                )

    @staticmethod
    def _finding_rows(package: str, report: dict) -> list[tuple[str, str, str, str, int]]:
        grouped: dict[str, dict] = {}
        findings = report.get("findings") if isinstance(report.get("findings"), list) else []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            code = str(finding.get("code") or "unknown")[:200]
            row = grouped.setdefault(code, {
                "severity": str(finding.get("severity") or "info")[:20],
                "category": str(finding.get("category") or "validation")[:100],
                "count": 0,
            })
            try:
                affected_count = max(1, int(finding.get("affected_count") or 1))
            except (TypeError, ValueError):
                affected_count = 1
            row["count"] += affected_count
        return [
            (package, code, row["severity"], row["category"], row["count"])
            for code, row in grouped.items()
        ]

    def cached(
        self,
        package: str,
        signature: str,
        validator_versions: str | tuple[str, ...],
    ) -> bool:
        versions = (
            (validator_versions,)
            if isinstance(validator_versions, str)
            else tuple(validator_versions)
        )
        if not versions:
            return False
        placeholders = ", ".join("?" for _ in versions)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM reports "
                f"WHERE package = ? AND signature = ? "
                f"AND validator_version IN ({placeholders})",
                (package, signature, *versions),
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
                    info_count, lyrics_declared, preview_declared,
                    deep_audio_skipped, deep_audio_unsupported, scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    deep_audio_skipped = excluded.deep_audio_skipped,
                    deep_audio_unsupported = excluded.deep_audio_unsupported,
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
                    int(features.get("deep_audio_skipped") or 0),
                    int(features.get("deep_audio_unsupported") or 0),
                    scanned_at,
                ),
            )
            conn.execute("DELETE FROM report_findings WHERE package = ?", (package,))
            conn.executemany(
                "INSERT INTO report_findings "
                "(package, code, severity, category, finding_count) "
                "VALUES (?, ?, ?, ?, ?)",
                self._finding_rows(package, report),
            )

    def delete_stale(self, current_packages: set[str]) -> int:
        with self._lock, self._conn as conn:
            rows = conn.execute("SELECT package FROM reports").fetchall()
            stale = [(row["package"],) for row in rows if row["package"] not in current_packages]
            if stale:
                conn.executemany("DELETE FROM report_findings WHERE package = ?", stale)
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

    def record_scan(self, state: dict) -> None:
        """Persist enough provenance to distinguish complete and partial results."""
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self._lock, self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                ("last_scan", payload),
            )

    def last_scan(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM cache_state WHERE key = 'last_scan'"
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        # A process exit during a scan is an incomplete attempt, never a
        # mysteriously still-running scan after the next FeedBack launch.
        if value.get("outcome") == "running":
            value = {**value, "outcome": "interrupted", "complete": False}
        return value

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
                    SUM(CASE WHEN error_count = 0 AND warning_count = 0 AND info_count = 0 THEN 1 ELSE 0 END) AS healthy,
                    SUM(CASE WHEN error_count > 0 THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN error_count = 0 AND warning_count > 0 THEN 1 ELSE 0 END) AS warnings,
                    SUM(CASE WHEN info_count > 0 THEN 1 ELSE 0 END) AS reviews,
                    COALESCE(SUM(error_count), 0) AS error_findings,
                    COALESCE(SUM(warning_count), 0) AS warning_findings,
                    COALESCE(SUM(info_count), 0) AS review_findings,
                    SUM(CASE WHEN lyrics_declared = 0 THEN 1 ELSE 0 END) AS no_lyrics,
                    SUM(CASE WHEN preview_declared = 0 THEN 1 ELSE 0 END) AS no_preview,
                    SUM(CASE WHEN deep_audio_skipped > 0 OR deep_audio_unsupported > 0
                        THEN 1 ELSE 0 END) AS deep_audio_partial,
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
            "reviews": int(row["reviews"] or 0),
            "error_findings": int(row["error_findings"] or 0),
            "warning_findings": int(row["warning_findings"] or 0),
            "review_findings": int(row["review_findings"] or 0),
            "no_lyrics": int(row["no_lyrics"] or 0),
            "no_preview": int(row["no_preview"] or 0),
            "deep_audio_partial": int(row["deep_audio_partial"] or 0),
            "last_scanned_at": row["last_scanned_at"],
        }

    @staticmethod
    def _where(
        result_filter: str,
        query: str,
        rule_code: str = "",
    ) -> tuple[str, list[str]]:
        clauses = []
        params: list[str] = []
        if result_filter == "problems":
            clauses.append("(r.error_count > 0 OR r.warning_count > 0 OR r.info_count > 0)")
        elif result_filter == "errors":
            clauses.append("r.error_count > 0")
        elif result_filter == "warnings":
            clauses.append("r.error_count = 0 AND r.warning_count > 0")
        elif result_filter == "review":
            clauses.append("r.info_count > 0")
        elif result_filter == "healthy":
            clauses.append("r.error_count = 0 AND r.warning_count = 0 AND r.info_count = 0")
        elif result_filter == "no_lyrics":
            clauses.append("r.lyrics_declared = 0")
        elif result_filter == "no_preview":
            clauses.append("r.preview_declared = 0")
        elif result_filter == "deep_audio_partial":
            clauses.append(
                "(r.deep_audio_skipped > 0 OR r.deep_audio_unsupported > 0)"
            )
        if rule_code:
            clauses.append(
                "EXISTS (SELECT 1 FROM report_findings AS rf "
                "WHERE rf.package = r.package AND rf.code = ?)"
            )
            params.append(rule_code)
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
        rule_code: str,
        limit: int,
        offset: int,
    ) -> dict:
        where, params = self._where(result_filter, query, rule_code)
        order = (
            " ORDER BY CASE WHEN r.error_count > 0 THEN 0 "
            "WHEN r.warning_count > 0 THEN 1 WHEN r.info_count > 0 THEN 2 ELSE 3 END, "
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

    def rules(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    rf.code,
                    rf.severity,
                    rf.category,
                    COUNT(*) AS package_count,
                    COALESCE(SUM(rf.finding_count), 0) AS finding_count
                FROM report_findings AS rf
                INNER JOIN current_scope AS s ON s.package = rf.package
                GROUP BY rf.code, rf.severity, rf.category
                ORDER BY
                    CASE rf.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                    package_count DESC,
                    rf.code COLLATE NOCASE
                """
            ).fetchall()
        return [
            {
                "code": row["code"],
                "severity": row["severity"],
                "category": row["category"],
                "package_count": int(row["package_count"] or 0),
                "finding_count": int(row["finding_count"] or 0),
            }
            for row in rows
        ]

    def matching_reports(
        self,
        *,
        result_filter: str,
        query: str,
        rule_code: str,
    ) -> list[dict]:
        where, params = self._where(result_filter, query, rule_code)
        source = (
            " FROM reports AS r "
            "INNER JOIN current_scope AS s ON s.package = r.package"
        )
        order = " ORDER BY r.artist COLLATE NOCASE, r.title COLLATE NOCASE, r.package COLLATE NOCASE"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT r.report_json, r.scanned_at{source}{where}{order}",
                params,
            ).fetchall()
        reports = []
        for row in rows:
            try:
                report = json.loads(row["report_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            report["scanned_at"] = row["scanned_at"]
            reports.append(report)
        return reports

    def iter_matching_reports(
        self,
        *,
        result_filter: str,
        query: str,
        rule_code: str,
    ):
        """Yield export reports without retaining the whole result set in memory."""
        where, params = self._where(result_filter, query, rule_code)
        source = (
            " FROM reports AS r "
            "INNER JOIN current_scope AS s ON s.package = r.package"
        )
        order = (
            " ORDER BY r.artist COLLATE NOCASE, r.title COLLATE NOCASE, "
            "r.package COLLATE NOCASE"
        )
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"SELECT r.report_json, r.scanned_at{source}{where}{order}", params
            )
            while True:
                rows = cursor.fetchmany(100)
                if not rows:
                    break
                for row in rows:
                    try:
                        report = json.loads(row["report_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    report["scanned_at"] = row["scanned_at"]
                    yield report
        finally:
            conn.close()


class LibraryScanner:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_dlc_dir,
        validate_feedpak,
        validator_version: str,
        log,
        rule_metadata=None,
    ) -> None:
        self._get_dlc_dir = get_dlc_dir
        self._validate_feedpak = validate_feedpak
        validator_parameters = inspect.signature(validate_feedpak).parameters.values()
        self._validator_accepts_checkpoint = any(
            parameter.name == "scan_checkpoint"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in validator_parameters
        )
        self._validator_version = validator_version
        self._rule_metadata = rule_metadata if callable(rule_metadata) else None
        self._log = log
        self._cache = _ReportCache(Path(config_dir) / "library_doctor" / "library_doctor.db")
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._playback_condition = threading.Condition()
        self._playback_active = False
        self._paused_seconds = 0.0
        self._pause_started: float | None = None
        self._run_started_monotonic: float | None = None
        self._thread: threading.Thread | None = None
        self._status = self._initial_status(self._cache.current_target())

    @staticmethod
    def _initial_status(target: dict | None = None) -> dict:
        return {
            "schema": "library_doctor.scan.v1",
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
            "elapsed_seconds": 0.0,
            "active_seconds": 0.0,
            "packages_per_second": 0.0,
            "eta_seconds": None,
            "deep_audio": False,
            "repairing": False,
            "playback_active": False,
            "playback_paused": False,
            "scope_complete": True,
            "discovery_errors": [],
            "target": target or {"kind": "library", "label": "Whole library"},
        }

    def status(self) -> dict:
        with self._lock:
            status = dict(self._status)
        with self._playback_condition:
            status["playback_active"] = self._playback_active
        status["playback_paused"] = bool(
            status["running"] and status["stage"] == "paused"
        )
        status["summary"] = self._cache.summary()
        status["last_scan"] = self._cache.last_scan()
        return status

    def start(
        self,
        *,
        force: bool = False,
        target_kind: str = "library",
        selected_path: str | None = None,
        deep_audio: bool = False,
    ) -> bool:
        with self._lock:
            if self._status["running"] or self._status.get("repairing"):
                return False
            root, target_path, target = self._resolve_target(target_kind, selected_path)
            self._cancel.clear()
            started_at = time.time()
            self._run_started_monotonic = time.monotonic()
            with self._playback_condition:
                self._paused_seconds = 0.0
                self._pause_started = None
            self._status = {
                **self._initial_status(target),
                "running": True,
                "stage": "discovering",
                "started_at": started_at,
                "deep_audio": bool(deep_audio),
            }
            self._cache.record_scan({
                "outcome": "running",
                "complete": False,
                "target": target,
                "deep_audio": bool(deep_audio),
                "validator_version": self._validator_version,
                "started_at": started_at,
                "completed_at": None,
                "expected": None,
                "completed": 0,
                "discovery_errors": [],
            })
            self._thread = threading.Thread(
                target=self._run,
                kwargs={
                    "force": force,
                    "root": root,
                    "target_path": target_path,
                    "target": target,
                    "deep_audio": bool(deep_audio),
                    "started_at": started_at,
                },
                name="library-doctor-scan",
                daemon=True,
            )
            self._thread.start()
        return True

    def begin_repair(self) -> tuple[bool, str]:
        """Reserve package mutation so it cannot overlap scanning or playback."""
        with self._lock:
            if self._status["running"]:
                return False, "Wait for the library scan to finish before repairing a package."
            if self._status.get("repairing"):
                return False, "Another Library Doctor repair is already in progress."
            with self._playback_condition:
                if self._playback_active:
                    return False, "Exit the song player before repairing a package."
            self._status["repairing"] = True
        return True, ""

    def begin_batch_operation(self) -> tuple[bool, str]:
        """Reserve Library Doctor for a background batch preview or repair.

        Unlike a single repair, a batch may start while playback is active.
        Its worker cooperatively waits before touching each package.
        """
        with self._lock:
            if self._status["running"]:
                return False, "Wait for the library scan to finish before starting batch repair."
            if self._status.get("repairing"):
                return False, "Another Library Doctor repair is already in progress."
            self._status["repairing"] = True
        return True, ""

    def finish_repair(self) -> None:
        with self._lock:
            self._status["repairing"] = False

    def record_repair_result(
        self,
        package: str,
        report: dict,
        *,
        deep_audio: bool = False,
    ) -> dict:
        """Replace one cached report after a separately validated repair."""
        relative = PurePosixPath(str(package))
        if (
            relative.is_absolute()
            or "\\" in str(package)
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix.lower() not in SONG_SUFFIXES
        ):
            raise ValueError("The repaired package path is invalid.")
        root_value = self._get_dlc_dir()
        if not root_value:
            raise ValueError("No song library folder is configured in FeedBack Settings.")
        root = Path(root_value).resolve(strict=True)
        package_path = root.joinpath(*relative.parts).resolve(strict=True)
        package_path.relative_to(root)
        if not (package_path.is_file() or package_path.is_dir()):
            raise ValueError("The repaired package is unavailable.")
        package_name = relative.as_posix()
        root_namespace = hashlib.blake2b(
            str(root).casefold().encode("utf-8", "surrogatepass"), digest_size=12
        ).hexdigest()
        signature = self._signature(package_path, root_namespace)
        scan_version = f"{self._validator_version}:{'deep-audio' if deep_audio else 'standard'}"
        self._cache.put(
            package_name,
            signature,
            scan_version,
            report,
            time.time(),
        )
        self._enrich_report(report)
        return report

    def cancel(self) -> bool:
        with self._lock:
            if not self._status["running"]:
                return False
            self._cancel.set()
            self._status["stage"] = "cancelling"
        with self._playback_condition:
            self._playback_condition.notify_all()
        return True

    def set_playback_active(self, active: bool) -> bool:
        """Prioritize an active song session over diagnostic scanning."""
        active = bool(active)
        with self._playback_condition:
            changed = self._playback_active != active
            self._playback_active = active
            self._playback_condition.notify_all()
        return changed

    def playback_active(self) -> bool:
        with self._playback_condition:
            return self._playback_active

    def wait_for_playback(self, cancel_event: threading.Event) -> bool:
        """Wait until gameplay releases priority; return false when cancelled."""
        with self._playback_condition:
            while self._playback_active and not cancel_event.is_set():
                self._playback_condition.wait(timeout=0.5)
        return not cancel_event.is_set()

    def _playback_checkpoint(self, resume_stage: str = "scanning") -> None:
        """Cooperatively suspend scan work while FeedBack owns the player."""
        pause_started = None
        with self._playback_condition:
            if self._playback_active and not self._cancel.is_set():
                pause_started = time.monotonic()
                self._pause_started = pause_started
        if pause_started is None:
            return

        # Do not acquire the status lock while holding the playback condition;
        # status() intentionally reads them in the opposite order.
        self._set_status(stage="paused")
        with self._playback_condition:
            while self._playback_active and not self._cancel.is_set():
                self._playback_condition.wait(timeout=0.5)
            if self._pause_started is not None:
                self._paused_seconds += max(0.0, time.monotonic() - pause_started)
                self._pause_started = None
        if not self._cancel.is_set():
            self._set_status(stage=resume_stage)

    def _active_elapsed(self) -> float:
        started = self._run_started_monotonic
        if started is None:
            return 0.0
        with self._playback_condition:
            paused = self._paused_seconds
            if self._pause_started is not None:
                paused += max(0.0, time.monotonic() - self._pause_started)
        return max(0.0, time.monotonic() - started - paused)

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def results(
        self,
        *,
        result_filter: str = "all",
        query: str = "",
        rule_code: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if result_filter not in RESULT_FILTERS:
            raise ValueError(f"Unknown result filter: {result_filter}")
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        query = str(query).strip()[:200]
        rule_code = str(rule_code).strip()[:200]
        payload = self._cache.results(
            result_filter=result_filter,
            query=query,
            rule_code=rule_code,
            limit=limit,
            offset=offset,
        )
        if self._rule_metadata is not None:
            for report in payload.get("items", []):
                self._enrich_report(report)
        return payload

    def repair_scope_snapshot(self, safe_rule_codes) -> dict:
        """Return a bounded snapshot of repairable packages in current scan scope."""
        safe_codes = {
            str(code) for code in safe_rule_codes
            if isinstance(code, str) and code
        }
        if not safe_codes:
            raise ValueError("No safe repair rules are currently available.")
        last_scan = self._cache.last_scan()
        if not isinstance(last_scan, dict) or not last_scan.get("complete"):
            raise ValueError(
                "Complete the current scan scope before reviewing a batch repair."
            )
        if last_scan.get("validator_version") != self._validator_version:
            raise ValueError(
                "Run the current Library Doctor scan before reviewing a batch repair."
            )
        reports = self._cache.matching_reports(
            result_filter="all",
            query="",
            rule_code="",
        )
        if len(reports) > MAX_BATCH_SCOPE_PACKAGES:
            raise ValueError(
                "This scan scope is too large for one batch. Scan and repair smaller folders."
            )
        candidates = []
        for report in reports:
            findings = report.get("findings") if isinstance(report, dict) else None
            if not isinstance(findings, list):
                continue
            rule_codes = sorted({
                finding.get("code")
                for finding in findings
                if isinstance(finding, dict) and finding.get("code") in safe_codes
            })
            package = report.get("package")
            if not rule_codes or not isinstance(package, str) or not package:
                continue
            candidates.append({
                "package": package,
                "title": str(report.get("title") or package),
                "artist": str(report.get("artist") or ""),
                "rule_codes": rule_codes,
            })
        target = self._cache.current_target()
        return {
            "schema": "library_doctor.repair_scope.v1",
            "target": target,
            "deep_audio": bool(last_scan.get("deep_audio")),
            "validator_version": self._validator_version,
            "scanned_at": last_scan.get("completed_at"),
            "scope_package_count": len(reports),
            "candidates": candidates,
        }

    def _enrich_report(self, report: dict) -> None:
        """Add current catalog metadata to reports written by older rule sets."""
        findings = report.get("findings") if isinstance(report, dict) else None
        if not isinstance(findings, list) or self._rule_metadata is None:
            return
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "info")
            category = str(finding.get("category") or "validation")
            code = str(finding.get("code") or "unknown")
            finding["rule"] = self._rule_metadata(code, severity, category)
            finding.setdefault("affected_count", 1)
            finding.setdefault("evidence", {
                key: value
                for key, value in {
                    "location": finding.get("location"),
                    "arrangement_id": finding.get("arrangement_id"),
                    "time": finding.get("time"),
                    "string": finding.get("string"),
                }.items()
                if value not in (None, "")
            })

    def rules(self) -> dict:
        items = self._cache.rules()
        if self._rule_metadata is not None:
            for item in items:
                item["rule"] = self._rule_metadata(
                    item["code"], item["severity"], item["category"]
                )
        return {
            "schema": "library_doctor.rules.v1",
            "items": items,
        }

    @staticmethod
    def _csv_cell(value) -> str:
        text = "" if value is None else str(value)
        visible = text.lstrip(" \t\r\n")
        return "'" + text if visible.startswith(("=", "+", "-", "@")) else text

    def export(
        self,
        *,
        export_format: str,
        result_filter: str = "all",
        query: str = "",
        rule_code: str = "",
    ) -> tuple[str, str, str]:
        if result_filter not in RESULT_FILTERS:
            raise ValueError(f"Unknown result filter: {result_filter}")
        export_format = str(export_format or "").strip().lower()
        if export_format not in {"json", "csv"}:
            raise ValueError("Choose JSON or CSV export format.")
        query = str(query).strip()[:200]
        rule_code = str(rule_code).strip()[:200]
        reports = self._cache.matching_reports(
            result_filter=result_filter,
            query=query,
            rule_code=rule_code,
        )
        if export_format == "json":
            if rule_code:
                for report in reports:
                    findings = report.get("findings")
                    if isinstance(findings, list):
                        report["findings"] = [
                            finding for finding in findings
                            if isinstance(finding, dict) and finding.get("code") == rule_code
                        ]
            payload = {
                "schema": "library_doctor.export.v1",
                "generated_at": time.time(),
                "target": self._cache.current_target(),
                "summary": self._cache.summary(),
                "filters": {
                    "result": result_filter,
                    "query": query,
                    "rule": rule_code,
                },
                "packages": reports,
            }
            return (
                "library-doctor-report.json",
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2),
            )

        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow((
            "package", "title", "artist", "package_status", "severity",
            "category", "code", "message", "arrangement_id", "time_seconds",
            "string_index", "location", "deep_audio_checked", "deep_audio_files",
            "deep_audio_skipped", "deep_audio_unsupported",
        ))
        for report in reports:
            features = (
                report.get("features")
                if isinstance(report.get("features"), dict)
                else {}
            )
            findings = report.get("findings") if isinstance(report.get("findings"), list) else []
            if rule_code:
                findings = [
                    finding for finding in findings
                    if isinstance(finding, dict) and finding.get("code") == rule_code
                ]
            if not findings:
                findings = [{}]
            for finding in findings:
                writer.writerow(tuple(self._csv_cell(value) for value in (
                    report.get("package"),
                    report.get("title"),
                    report.get("artist"),
                    report.get("status"),
                    finding.get("severity"),
                    finding.get("category"),
                    finding.get("code"),
                    finding.get("message"),
                    finding.get("arrangement_id"),
                    finding.get("time"),
                    finding.get("string"),
                    finding.get("location"),
                    features.get("deep_audio_checked"),
                    features.get("deep_audio_files"),
                    features.get("deep_audio_skipped"),
                    features.get("deep_audio_unsupported"),
                )))
        return (
            "library-doctor-report.csv",
            "text/csv; charset=utf-8",
            "\ufeff" + output.getvalue(),
        )

    def export_stream(
        self,
        *,
        export_format: str,
        result_filter: str = "all",
        query: str = "",
        rule_code: str = "",
    ) -> tuple[str, str, object]:
        """Return a bounded-memory iterator for an in-game report download."""
        if result_filter not in RESULT_FILTERS:
            raise ValueError(f"Unknown result filter: {result_filter}")
        export_format = str(export_format or "").strip().lower()
        if export_format not in {"json", "csv"}:
            raise ValueError("Choose JSON or CSV export format.")
        query = str(query).strip()[:200]
        rule_code = str(rule_code).strip()[:200]

        def reports():
            for report in self._cache.iter_matching_reports(
                result_filter=result_filter,
                query=query,
                rule_code=rule_code,
            ):
                self._enrich_report(report)
                if rule_code:
                    findings = report.get("findings")
                    if isinstance(findings, list):
                        report["findings"] = [
                            finding for finding in findings
                            if isinstance(finding, dict)
                            and finding.get("code") == rule_code
                        ]
                yield report

        if export_format == "json":
            metadata = {
                "schema": "library_doctor.export.v1",
                "generated_at": time.time(),
                "target": self._cache.current_target(),
                "summary": self._cache.summary(),
                "filters": {
                    "result": result_filter,
                    "query": query,
                    "rule": rule_code,
                },
            }

            def json_chunks():
                prefix = json.dumps(
                    metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                )
                yield prefix[:-1] + ',"packages":['
                first = True
                for report in reports():
                    if not first:
                        yield ","
                    first = False
                    yield json.dumps(
                        report,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                yield "]}"

            return (
                "library-doctor-report.json",
                "application/json; charset=utf-8",
                json_chunks(),
            )

        headers = (
            "package", "title", "artist", "package_status", "severity",
            "category", "code", "message", "arrangement_id", "time_seconds",
            "string_index", "location", "deep_audio_checked", "deep_audio_files",
            "deep_audio_skipped", "deep_audio_unsupported",
        )

        def csv_line(values) -> str:
            output = io.StringIO(newline="")
            csv.writer(output, lineterminator="\n").writerow(
                tuple(self._csv_cell(value) for value in values)
            )
            return output.getvalue()

        def csv_chunks():
            yield "\ufeff" + csv_line(headers)
            for report in reports():
                features = (
                    report.get("features")
                    if isinstance(report.get("features"), dict)
                    else {}
                )
                findings = (
                    report.get("findings")
                    if isinstance(report.get("findings"), list)
                    else []
                )
                if not findings:
                    findings = [{}]
                for finding in findings:
                    yield csv_line((
                        report.get("package"),
                        report.get("title"),
                        report.get("artist"),
                        report.get("status"),
                        finding.get("severity"),
                        finding.get("category"),
                        finding.get("code"),
                        finding.get("message"),
                        finding.get("arrangement_id"),
                        finding.get("time"),
                        finding.get("string"),
                        finding.get("location"),
                        features.get("deep_audio_checked"),
                        features.get("deep_audio_files"),
                        features.get("deep_audio_skipped"),
                        features.get("deep_audio_unsupported"),
                    ))

        return (
            "library-doctor-report.csv",
            "text/csv; charset=utf-8",
            csv_chunks(),
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
    def _discover(
        root: Path,
        *,
        scan_checkpoint=None,
        cancelled=None,
    ) -> tuple[list[Path], list[str]]:
        packages: list[Path] = []
        errors: list[str] = []

        def note_error(error: OSError) -> None:
            if len(errors) >= MAX_DISCOVERY_ERRORS:
                return
            location = "a subfolder"
            if error.filename:
                try:
                    location = Path(error.filename).resolve().relative_to(root.resolve()).as_posix()
                except (OSError, ValueError):
                    location = Path(error.filename).name or location
            reason = error.strerror or type(error).__name__
            errors.append(f"Could not read {location}: {reason}")

        for dirpath, dirnames, filenames in os.walk(
            root, topdown=True, onerror=note_error, followlinks=False
        ):
            if scan_checkpoint is not None:
                scan_checkpoint()
            if callable(cancelled) and cancelled():
                break
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
        return packages, errors

    @classmethod
    def _discover_target(
        cls,
        target_path: Path,
        target_kind: str,
        *,
        scan_checkpoint=None,
        cancelled=None,
    ) -> tuple[list[Path], list[str]]:
        if target_kind == "file":
            return [target_path], []
        if target_path.name.lower().endswith(SONG_SUFFIXES):
            return [target_path], []
        return cls._discover(
            target_path,
            scan_checkpoint=scan_checkpoint,
            cancelled=cancelled,
        )

    @staticmethod
    def _signature(path: Path, namespace: str = "", scan_checkpoint=None) -> str:
        def sample(member: Path, digest) -> None:
            try:
                if scan_checkpoint is not None:
                    scan_checkpoint()
                size = member.stat().st_size
                with member.open("rb") as stream:
                    if size <= SIGNATURE_FULL_FILE_BYTES:
                        digest.update(stream.read())
                    else:
                        digest.update(stream.read(SIGNATURE_SAMPLE_BYTES))
                        stream.seek(max(0, size - SIGNATURE_SAMPLE_BYTES))
                        digest.update(stream.read(SIGNATURE_SAMPLE_BYTES))
            except OSError:
                # The validation pass will produce the user-facing read error.
                digest.update(b"<unreadable>")

        digest = hashlib.blake2b(digest_size=16)
        digest.update(namespace.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        if not path.is_dir():
            stat = path.stat()
            digest.update(f"f:{stat.st_mtime_ns}:{stat.st_size}:".encode("ascii"))
            sample(path, digest)
            try:
                with zipfile.ZipFile(path, "r") as archive:
                    infos = archive.infolist()
                    if len(infos) <= MAX_SIGNATURE_MEMBERS:
                        for info in infos:
                            if scan_checkpoint is not None:
                                scan_checkpoint()
                            digest.update(info.filename.encode("utf-8", "surrogatepass"))
                            digest.update(
                                f":{info.CRC}:{info.file_size}:{info.compress_size}".encode(
                                    "ascii"
                                )
                            )
            except (OSError, zipfile.BadZipFile):
                pass
            return "f:" + digest.hexdigest()
        root = path.resolve()
        member_count = 0
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            parent = Path(dirpath)
            for name in sorted(filenames, key=str.casefold):
                member_count += 1
                if member_count > MAX_SIGNATURE_MEMBERS:
                    digest.update(b"<member-limit-exceeded>")
                    return "d:" + digest.hexdigest()
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
                sample(member, digest)
        return "d:" + digest.hexdigest()

    def _record_finished_scan(
        self,
        *,
        outcome: str,
        complete: bool,
        target: dict,
        deep_audio: bool,
        started_at: float,
        completed_at: float,
        expected: int | None,
        completed: int,
        discovery_errors: list[str],
    ) -> None:
        self._cache.record_scan({
            "outcome": outcome,
            "complete": bool(complete),
            "target": target,
            "deep_audio": bool(deep_audio),
            "validator_version": self._validator_version,
            "started_at": started_at,
            "completed_at": completed_at,
            "expected": expected,
            "completed": completed,
            "discovery_errors": discovery_errors[:MAX_DISCOVERY_ERRORS],
        })

    def _finish_cancelled(
        self,
        *,
        target: dict,
        deep_audio: bool,
        started_at: float,
        expected: int | None,
        completed_packages: set[str] | None,
        discovery_errors: list[str],
    ) -> None:
        """Persist a cancelled attempt without presenting it as complete."""
        completed = len(completed_packages or ())
        if completed_packages is not None:
            self._cache.replace_scope(
                completed_packages,
                kind=target["kind"],
                label=target["label"],
            )
        completed_at = time.time()
        self._set_status(
            running=False,
            stage="cancelled",
            cancelled=True,
            current="",
            completed_at=completed_at,
            elapsed_seconds=max(0.0, completed_at - started_at),
            active_seconds=self._active_elapsed(),
            eta_seconds=None,
            scope_complete=False,
        )
        self._record_finished_scan(
            outcome="cancelled",
            complete=False,
            target=target,
            deep_audio=deep_audio,
            started_at=started_at,
            completed_at=completed_at,
            expected=expected,
            completed=completed,
            discovery_errors=discovery_errors,
        )

    def _failure_report(
        self,
        package: str,
        exc: Exception,
        *,
        deep_audio: bool = False,
    ) -> dict:
        message = (
            f"Validation failed unexpectedly ({type(exc).__name__}). "
            "Check that the package is readable, then scan it again."
        )
        finding = {
            "severity": "error",
            "code": "scan.validation-failed",
            "message": message[:1_000],
            "category": "validation",
            "location": "",
            "arrangement_id": None,
            "time": None,
            "string": None,
            "affected_count": 1,
            "evidence": {},
        }
        if self._rule_metadata is not None:
            finding["rule"] = self._rule_metadata(
                finding["code"], finding["severity"], finding["category"]
            )
        return {
            "schema": "library_doctor.package.v1",
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
                "deep_audio_checked": bool(deep_audio),
                "deep_audio_files": 0,
                "deep_audio_skipped": 0,
                "deep_audio_unsupported": 0,
            },
            "findings": [finding],
        }

    def _run(
        self,
        *,
        force: bool,
        root: Path,
        target_path: Path,
        target: dict,
        deep_audio: bool,
        started_at: float,
    ) -> None:
        try:
            self._playback_checkpoint(resume_stage="discovering")
            packages, discovery_errors = self._discover_target(
                target_path,
                target["kind"],
                scan_checkpoint=lambda: self._playback_checkpoint(
                    resume_stage="discovering"
                ),
                cancelled=self._cancel.is_set,
            )
            if self._cancel.is_set():
                self._finish_cancelled(
                    target=target,
                    deep_audio=deep_audio,
                    started_at=started_at,
                    expected=None,
                    completed_packages=None,
                    discovery_errors=discovery_errors,
                )
                return
            relative = [path.relative_to(root).as_posix() for path in packages]
            self._set_status(
                stage="scanning",
                total=len(packages),
                discovery_errors=discovery_errors,
                scope_complete=not discovery_errors,
            )
            self._cache.record_scan({
                "outcome": "running",
                "complete": False,
                "target": target,
                "deep_audio": bool(deep_audio),
                "validator_version": self._validator_version,
                "started_at": started_at,
                "completed_at": None,
                "expected": len(packages),
                "completed": 0,
                "discovery_errors": discovery_errors,
            })

            scanned = 0
            reused = 0
            completed_packages: set[str] = set()
            standard_version = f"{self._validator_version}:standard"
            deep_version = f"{self._validator_version}:deep-audio"
            scan_version = deep_version if deep_audio else standard_version
            root_namespace = hashlib.blake2b(
                str(root).casefold().encode("utf-8", "surrogatepass"), digest_size=12
            ).hexdigest()
            accepted_versions = (deep_version,) if deep_audio else (
                standard_version,
                deep_version,
                # Reuse reports written before scan profiles were introduced.
                self._validator_version,
            )
            for index, (path, package_name) in enumerate(zip(packages, relative)):
                self._playback_checkpoint()
                if self._cancel.is_set():
                    self._finish_cancelled(
                        target=target,
                        deep_audio=deep_audio,
                        started_at=started_at,
                        expected=len(packages),
                        completed_packages=completed_packages,
                        discovery_errors=discovery_errors,
                    )
                    return
                self._set_status(current=package_name)
                try:
                    signature = self._signature(
                        path, root_namespace, self._playback_checkpoint
                    )
                    if not force and self._cache.cached(
                        package_name, signature, accepted_versions
                    ):
                        reused += 1
                    else:
                        try:
                            validation_options = {}
                            if deep_audio:
                                validation_options["deep_audio"] = True
                            if self._validator_accepts_checkpoint:
                                validation_options["scan_checkpoint"] = (
                                    self._playback_checkpoint
                                )
                            report = self._validate_feedpak(
                                path,
                                package_name,
                                **validation_options,
                            )
                        except Exception as exc:  # One third-party rule must not abort the batch.
                            self._log.warning(
                                "Library Doctor validation failed for %s: %s",
                                package_name,
                                exc,
                            )
                            report = self._failure_report(
                                package_name,
                                exc,
                                deep_audio=deep_audio,
                            )
                        self._cache.put(
                            package_name,
                            signature,
                            scan_version,
                            report,
                            time.time(),
                        )
                        scanned += 1
                    completed_packages.add(package_name)
                except OSError as exc:
                    report = self._failure_report(
                        package_name,
                        exc,
                        deep_audio=deep_audio,
                    )
                    fallback_signature = f"unreadable:{time.time_ns()}"
                    self._cache.put(
                        package_name,
                        fallback_signature,
                        scan_version,
                        report,
                        time.time(),
                    )
                    scanned += 1
                    completed_packages.add(package_name)
                done = index + 1
                elapsed = max(0.0, time.time() - started_at)
                active_elapsed = max(0.001, self._active_elapsed())
                rate = done / active_elapsed
                eta = (len(packages) - done) / rate if rate > 0 else None
                self._set_status(
                    done=done,
                    scanned=scanned,
                    reused=reused,
                    elapsed_seconds=elapsed,
                    active_seconds=active_elapsed,
                    packages_per_second=rate,
                    eta_seconds=eta,
                )
                if self._cancel.is_set():
                    self._finish_cancelled(
                        target=target,
                        deep_audio=deep_audio,
                        started_at=started_at,
                        expected=len(packages),
                        completed_packages=completed_packages,
                        discovery_errors=discovery_errors,
                    )
                    return

            if target["kind"] == "library" and not discovery_errors:
                self._cache.delete_stale(completed_packages)
            self._cache.replace_scope(
                completed_packages,
                kind=target["kind"],
                label=target["label"],
            )
            completed_at = time.time()
            elapsed = max(0.0, completed_at - started_at)
            active_elapsed = self._active_elapsed()
            rate_elapsed = max(0.001, active_elapsed)
            complete = not discovery_errors
            stage = "complete" if complete else "incomplete"
            self._set_status(
                running=False,
                stage=stage,
                current="",
                completed_at=completed_at,
                elapsed_seconds=elapsed,
                active_seconds=active_elapsed,
                packages_per_second=(len(packages) / rate_elapsed if packages else 0.0),
                eta_seconds=0.0,
                scope_complete=complete,
            )
            self._record_finished_scan(
                outcome=stage,
                complete=complete,
                target=target,
                deep_audio=deep_audio,
                started_at=started_at,
                completed_at=completed_at,
                expected=len(packages),
                completed=len(completed_packages),
                discovery_errors=discovery_errors,
            )
        except Exception as exc:
            self._log.warning("Library Doctor scan failed: %s", exc)
            completed_at = time.time()
            self._set_status(
                running=False,
                stage="error",
                current="",
                error=(
                    f"The scan stopped unexpectedly ({type(exc).__name__}). "
                    "Check the FeedBack log for technical details."
                ),
                completed_at=completed_at,
                elapsed_seconds=max(0.0, completed_at - started_at),
                active_seconds=self._active_elapsed(),
                eta_seconds=None,
                scope_complete=False,
            )
            self._record_finished_scan(
                outcome="error",
                complete=False,
                target=target,
                deep_audio=deep_audio,
                started_at=started_at,
                completed_at=completed_at,
                expected=self._status.get("total"),
                completed=self._status.get("done", 0),
                discovery_errors=self._status.get("discovery_errors", []),
            )
