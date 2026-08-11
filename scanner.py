"""Background library scanning and the incremental report cache."""

from __future__ import annotations

import csv
import concurrent.futures
import hashlib
import inspect
import io
import json
import os
import sqlite3
import sys
import threading
import time
import zipfile
from collections import deque
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
MIN_PARALLEL_PACKAGES = 3
MAX_SOURCE_CHANGE_RETRIES = 2
WINDOWS_PROCESS_POOL_LIMIT = 61
STANDARD_WORKER_MEMORY_BYTES = 384 * 1024 * 1024
DEEP_AUDIO_WORKER_MEMORY_BYTES = 768 * 1024 * 1024
STANDARD_WORKER_RSS_LIMIT_BYTES = 768 * 1024 * 1024
DEEP_AUDIO_WORKER_RSS_LIMIT_BYTES = 1536 * 1024 * 1024
STANDARD_PACKAGE_TIMEOUT_SECONDS = 5 * 60
DEEP_AUDIO_PACKAGE_TIMEOUT_SECONDS = 15 * 60
WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5.0
MIN_SYSTEM_MEMORY_RESERVE_BYTES = 1024 * 1024 * 1024
MAX_SYSTEM_MEMORY_RESERVE_BYTES = 4 * 1024 * 1024 * 1024
_BATCH_PREVIEW_RULE_PRIORITY = (
    "media.preview-missing",
    "media.preview-too-long",
    "media.preview-too-short",
)


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def choose_worker_policy(
    pending_packages: int,
    *,
    deep_audio: bool,
    requested_max: int | None = None,
    worker_backend_available: bool = True,
    logical_cpus: int | None = None,
    physical_cpus: int | None = None,
    total_memory: int | None = None,
    available_memory: int | None = None,
    platform: str | None = None,
    environment: dict | None = None,
) -> dict:
    """Choose a bounded worker maximum from work, CPU, RAM, host and user limits."""
    pending = max(0, int(pending_packages or 0))
    env = os.environ if environment is None else environment
    platform_name = sys.platform if platform is None else platform

    if logical_cpus is None:
        logical_cpus = os.cpu_count() or 1
    if physical_cpus is None or total_memory is None or available_memory is None:
        try:
            import psutil  # FeedBack dependency; optional for standalone tests.

            if physical_cpus is None:
                physical_cpus = psutil.cpu_count(logical=False)
            memory = psutil.virtual_memory()
            if total_memory is None:
                total_memory = int(memory.total)
            if available_memory is None:
                available_memory = int(memory.available)
        except (ImportError, OSError, RuntimeError, ValueError):
            pass

    logical = max(1, _positive_int(logical_cpus) or 1)
    physical = max(1, _positive_int(physical_cpus) or max(1, (logical + 1) // 2))
    global_value = env.get("FEEDBACK_MAX_SCAN_WORKERS") or env.get("SCAN_MAX_WORKERS")
    global_limit = _positive_int(global_value)
    user_limit = _positive_int(requested_max)
    platform_limit = WINDOWS_PROCESS_POOL_LIMIT if platform_name == "win32" else physical

    memory_per_worker = (
        DEEP_AUDIO_WORKER_MEMORY_BYTES if deep_audio else STANDARD_WORKER_MEMORY_BYTES
    )
    memory_limit = physical
    if _positive_int(total_memory) and _positive_int(available_memory):
        reserve = min(
            MAX_SYSTEM_MEMORY_RESERVE_BYTES,
            max(MIN_SYSTEM_MEMORY_RESERVE_BYTES, int(total_memory * 0.15)),
        )
        usable = max(0, int(available_memory) - reserve)
        memory_limit = max(1, usable // memory_per_worker)

    limits = {
        "packages": max(1, pending),
        "physical_cpu": physical,
        "memory": max(1, memory_limit),
        "platform": max(1, platform_limit),
    }
    if global_limit is not None:
        limits["feedback"] = global_limit
    if user_limit is not None:
        limits["user"] = user_limit

    selected = min(limits.values())
    reason = "automatic"
    if not worker_backend_available:
        selected = 1
        reason = "worker_backend_unavailable"
    elif pending < MIN_PARALLEL_PACKAGES:
        selected = 1
        reason = "small_scope"
    elif selected <= 1:
        reason = "limited_to_one"

    return {
        "schema": "library_doctor.worker_policy.v1",
        "mode": "custom" if user_limit is not None else "automatic",
        "reason": reason,
        "selected_workers": max(1, selected),
        "pending_packages": pending,
        "deep_audio": bool(deep_audio),
        "logical_cpus": logical,
        "physical_cpus": physical,
        "memory_per_worker_bytes": memory_per_worker,
        "total_memory_bytes": _positive_int(total_memory),
        "available_memory_bytes": _positive_int(available_memory),
        "limits": limits,
    }


class _ReportCache:
    _BUSY_RETRIES = 3
    _BUSY_RETRY_SECONDS = 0.05

    def __init__(self, path: Path, log=None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._open_with_recovery()

    @staticmethod
    def _is_busy_error(exc: sqlite3.Error) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    @staticmethod
    def _is_corrupt_error(exc: sqlite3.Error) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "not a database",
                "malformed",
                "file is encrypted",
                "disk image is malformed",
            )
        )

    def _close_connection(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
        self._conn = None

    def _quarantine_corrupt_database(self) -> None:
        suffix = f".corrupt-{time.time_ns()}"
        for source in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if not source.exists():
                continue
            target = source.with_name(f"{source.name}{suffix}")
            os.replace(source, target)
        if self._log is not None:
            self._log.warning(
                "Library Doctor quarantined an unreadable report cache and created a clean cache."
            )

    def _open_with_recovery(self) -> None:
        quarantined = False
        for attempt in range(self._BUSY_RETRIES + 1):
            try:
                self._conn = self._connect()
                self._initialize()
                return
            except sqlite3.DatabaseError as exc:
                self._close_connection()
                if self._is_corrupt_error(exc) and not quarantined:
                    self._quarantine_corrupt_database()
                    quarantined = True
                    continue
                if self._is_busy_error(exc) and attempt < self._BUSY_RETRIES:
                    time.sleep(self._BUSY_RETRY_SECONDS * (attempt + 1))
                    continue
                raise
        raise sqlite3.OperationalError("report cache initialization did not complete")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        assert self._conn is not None
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

    def verified_report(
        self,
        package: str,
        signature: str,
        validator_version: str,
    ) -> dict | None:
        """Return one report only when its exact scan binding still matches."""
        with self._lock:
            row = self._conn.execute(
                "SELECT report_json FROM reports WHERE package = ? "
                "AND signature = ? AND validator_version = ?",
                (package, signature, validator_version),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["report_json"])
        except (TypeError, json.JSONDecodeError):
            return None

    def current_report_binding(
        self,
        package: str,
        validator_version: str,
    ) -> dict | None:
        """Return one current-scope report and the signature that binds it."""
        with self._lock:
            row = self._conn.execute(
                "SELECT r.signature, r.report_json FROM reports AS r "
                "INNER JOIN current_scope AS s ON s.package = r.package "
                "WHERE r.package = ? AND r.validator_version = ?",
                (package, validator_version),
            ).fetchone()
        if row is None:
            return None
        try:
            report = json.loads(row["report_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(report, dict):
            return None
        return {
            "signature": row["signature"],
            "report": report,
        }

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
        include_signature: bool = False,
    ) -> list[dict]:
        where, params = self._where(result_filter, query, rule_code)
        source = (
            " FROM reports AS r "
            "INNER JOIN current_scope AS s ON s.package = r.package"
        )
        order = " ORDER BY r.artist COLLATE NOCASE, r.title COLLATE NOCASE, r.package COLLATE NOCASE"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT r.report_json, r.scanned_at, r.signature{source}{where}{order}",
                params,
            ).fetchall()
        reports = []
        for row in rows:
            try:
                report = json.loads(row["report_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            report["scanned_at"] = row["scanned_at"]
            if include_signature:
                report["_scan_signature"] = row["signature"]
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
        worker_pool_factory=None,
        package_timeout_seconds: float | None = None,
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
        self._worker_pool_factory = (
            worker_pool_factory if callable(worker_pool_factory) else None
        )
        self._package_timeout_seconds = (
            float(package_timeout_seconds)
            if isinstance(package_timeout_seconds, (int, float))
            and not isinstance(package_timeout_seconds, bool)
            and package_timeout_seconds > 0
            else None
        )
        self._log = log
        self._cache = _ReportCache(
            Path(config_dir) / "library_doctor" / "library_doctor.db",
            log=log,
        )
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._playback_condition = threading.Condition()
        self._playback_active = False
        self._paused_seconds = 0.0
        self._pause_started: float | None = None
        self._run_started_monotonic: float | None = None
        self._thread: threading.Thread | None = None
        self._worker_pool = None
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
            "performance": {
                "discovery_seconds": 0.0,
                "signature_seconds": 0.0,
                "cache_lookup_seconds": 0.0,
                "validation_seconds": 0.0,
                "parallel_validation_wall_seconds": 0.0,
                "worker_validation_seconds": 0.0,
                "worker_queue_seconds": 0.0,
                "source_recheck_seconds": 0.0,
                "source_change_retries": 0,
                "parallel_fallbacks": 0,
                "worker_timeouts": 0,
                "worker_restarts": 0,
                "peak_worker_rss_bytes": 0,
                "worker_rss_limit_bytes": STANDARD_WORKER_RSS_LIMIT_BYTES,
                "worker_memory_limit_exceeded": 0,
                "worker_memory_restarts": 0,
                "cache_write_seconds": 0.0,
                "scope_update_seconds": 0.0,
            },
            "worker_policy": {
                "schema": "library_doctor.worker_policy.v1",
                "mode": "automatic",
                "reason": "not_started",
                "selected_workers": 1,
                "pending_packages": 0,
                "deep_audio": False,
                "limits": {},
            },
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
        last_scan = self._cache.last_scan()
        scan_current = bool(
            isinstance(last_scan, dict)
            and last_scan.get("complete")
            and last_scan.get("validator_version") == self._validator_version
        )
        status["validator_version"] = self._validator_version
        status["scan_current"] = scan_current
        status["scope_complete"] = bool(
            status.get("scope_complete") and scan_current
        )
        status["last_scan"] = last_scan
        return status

    def start(
        self,
        *,
        force: bool = False,
        target_kind: str = "library",
        selected_path: str | None = None,
        deep_audio: bool = False,
        max_workers: int | None = None,
    ) -> bool:
        if max_workers is not None and (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
        ):
            raise ValueError("Maximum scan workers must be a positive whole number.")
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
                    "max_workers": max_workers,
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
        worker_pool = None
        with self._lock:
            if not self._status["running"]:
                return False
            self._cancel.set()
            self._status["stage"] = "cancelling"
            worker_pool = self._worker_pool
        if worker_pool is not None:
            try:
                worker_pool.cancel()
            except Exception as exc:
                self._log.warning(
                    "Library Doctor could not signal its validation workers: %s",
                    exc,
                )
        with self._playback_condition:
            self._playback_condition.notify_all()
        return True

    def set_playback_active(self, active: bool) -> bool:
        """Prioritize an active song session over diagnostic scanning."""
        active = bool(active)
        with self._lock:
            worker_pool = self._worker_pool
            running = bool(self._status.get("running"))
            with self._playback_condition:
                changed = self._playback_active != active
                self._playback_active = active
                if running and active and self._pause_started is None:
                    self._pause_started = time.monotonic()
                elif running and not active and self._pause_started is not None:
                    self._paused_seconds += max(
                        0.0, time.monotonic() - self._pause_started
                    )
                    self._pause_started = None
                self._playback_condition.notify_all()
            if running and active and self._status.get("stage") not in {
                "cancelling", "discovering",
            }:
                self._status["stage"] = "paused"
            elif running and not active and self._status.get("stage") == "paused":
                self._status["stage"] = "scanning"
        if worker_pool is not None:
            worker_pool.set_paused(active)
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
                if self._pause_started is None:
                    self._pause_started = time.monotonic()
                pause_started = self._pause_started
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
        for report in payload.get("items", []):
            self._enrich_report(report)
        return payload

    def repair_scope_snapshot(
        self,
        safe_rule_codes,
        preview_rule_codes=(),
    ) -> dict:
        """Return a bounded snapshot of repairable packages in current scan scope."""
        safe_codes = {
            str(code) for code in safe_rule_codes
            if isinstance(code, str) and code
        }
        preview_codes = {
            str(code) for code in preview_rule_codes
            if isinstance(code, str) and code
        }
        if not safe_codes and not preview_codes:
            raise ValueError("No batch repair rules are currently available.")
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
            include_signature=True,
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
            reported_preview_codes = {
                finding.get("code")
                for finding in findings
                if (
                    isinstance(finding, dict)
                    and finding.get("code") in preview_codes
                )
            }
            features = (
                report.get("features")
                if isinstance(report.get("features"), dict)
                else {}
            )
            eligibility = (
                features.get("repair_eligibility")
                if isinstance(features.get("repair_eligibility"), dict)
                else {}
            )

            def automatically_repairable(code: str) -> bool:
                item = eligibility.get(code)
                return not isinstance(item, dict) or item.get("status") == "automatic"

            rule_codes = [
                code for code in rule_codes if automatically_repairable(code)
            ]
            reported_preview_codes = {
                code for code in reported_preview_codes
                if automatically_repairable(code)
            }
            if (
                "media.preview-missing" in preview_codes
                and not bool(features.get("preview_declared"))
                and bool(features.get("preview_source_available"))
                and automatically_repairable("media.preview-missing")
            ):
                reported_preview_codes.add("media.preview-missing")
            preview_rule_code = next(
                (
                    code for code in _BATCH_PREVIEW_RULE_PRIORITY
                    if code in reported_preview_codes
                ),
                None,
            )
            package = report.get("package")
            if (
                (not rule_codes and preview_rule_code is None)
                or not isinstance(package, str)
                or not package
            ):
                continue
            safe_findings = []
            for code in rule_codes:
                matching = [
                    finding for finding in findings
                    if isinstance(finding, dict) and finding.get("code") == code
                ]
                first = matching[0] if matching else {}
                rule = (
                    first.get("rule")
                    if isinstance(first.get("rule"), dict)
                    else {}
                )
                affected_count = 0
                for finding in matching:
                    try:
                        affected_count += max(
                            1, int(finding.get("affected_count") or 1)
                        )
                    except (TypeError, ValueError):
                        affected_count += 1
                safe_findings.append({
                    "rule_code": code,
                    "title": str(rule.get("title") or code),
                    "finding_count": len(matching),
                    "reported_affected_count": affected_count,
                })
            candidates.append({
                "package": package,
                "title": str(report.get("title") or package),
                "artist": str(report.get("artist") or ""),
                "rule_codes": rule_codes,
                "safe_findings": safe_findings,
                "preview_rule_code": preview_rule_code,
                "scan_signature": report.get("_scan_signature"),
            })
        target = self._cache.current_target()
        return {
            "schema": "library_doctor.repair_scope.v1",
            "target": target,
            "deep_audio": bool(last_scan.get("deep_audio")),
            "include_preview_repairs": bool(preview_codes),
            "validator_version": self._validator_version,
            "scanned_at": last_scan.get("completed_at"),
            "scope_package_count": len(reports),
            "candidates": candidates,
        }

    def package_matches_signature(self, package: str, expected: str) -> bool:
        """Confirm that one package still matches its completed scan snapshot."""
        if not isinstance(expected, str) or not expected:
            return False
        relative = PurePosixPath(str(package))
        if (
            relative.is_absolute()
            or "\\" in str(package)
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix.lower() not in SONG_SUFFIXES
        ):
            return False
        try:
            root_value = self._get_dlc_dir()
            if not root_value:
                return False
            root = Path(root_value).resolve(strict=True)
            package_path = root.joinpath(*relative.parts).resolve(strict=True)
            package_path.relative_to(root)
            if not (package_path.is_file() or package_path.is_dir()):
                return False
            root_namespace = hashlib.blake2b(
                str(root).casefold().encode("utf-8", "surrogatepass"),
                digest_size=12,
            ).hexdigest()
            return self._signature(package_path, root_namespace) == expected
        except (OSError, ValueError):
            return False

    def current_deep_audio_repair_context(self, package: str) -> dict | None:
        """Return a guarded Deep Audio binding for one current-scope repair.

        The caller must use ``package_matches_signature`` immediately before
        trusting the report and again before commit. Keeping the signature with
        the report avoids a second package hash just to read the cache.
        """
        last_scan = self._cache.last_scan()
        if not isinstance(last_scan, dict):
            return None
        if (
            not last_scan.get("complete")
            or not last_scan.get("deep_audio")
            or last_scan.get("validator_version") != self._validator_version
        ):
            return None
        return self._cache.current_report_binding(
            package,
            f"{self._validator_version}:deep-audio",
        )

    def verified_deep_audio_report(
        self, package: str, expected_signature: str
    ) -> dict | None:
        """Return the completed Deep Audio report bound to unchanged bytes."""
        if not self.package_matches_signature(package, expected_signature):
            return None
        return self.deep_audio_report_for_signature(package, expected_signature)

    def deep_audio_report_for_signature(
        self, package: str, expected_signature: str
    ) -> dict | None:
        """Read a Deep Audio report after the caller verified this signature.

        This cache-only variant avoids hashing a package twice in one guarded
        batch step. Callers must first compare the current package with the same
        signature; commit-time source guards remain independently required.
        """
        if not isinstance(expected_signature, str) or not expected_signature:
            return None
        return self._cache.verified_report(
            package,
            expected_signature,
            f"{self._validator_version}:deep-audio",
        )

    def _enrich_report(self, report: dict) -> None:
        """Add current catalog metadata to reports written by older rule sets."""
        findings = report.get("findings") if isinstance(report, dict) else None
        if not isinstance(findings, list):
            return
        features = (
            report.get("features")
            if isinstance(report.get("features"), dict)
            else {}
        )
        features["repair_scan_current"] = (
            report.get("validator_version") == self._validator_version
        )
        report["features"] = features
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "info")
            category = str(finding.get("category") or "validation")
            code = str(finding.get("code") or "unknown")
            if self._rule_metadata is not None:
                finding["rule"] = self._rule_metadata(code, severity, category)
            eligibility = (
                features.get("repair_eligibility")
                if isinstance(features.get("repair_eligibility"), dict)
                else {}
            )
            repair_status = eligibility.get(code)
            rule = finding.get("rule")
            if isinstance(repair_status, dict) and isinstance(rule, dict):
                status = repair_status.get("status")
                if status == "author_review":
                    rule["repairability"] = "review_required"
                    rule["guidance"] = repair_status.get("message") or (
                        "This occurrence needs author review before it is changed."
                    )
                elif status == "unavailable":
                    rule["repairability"] = "manual"
                    rule["guidance"] = repair_status.get("message") or (
                        "The package does not contain the source data required for this automatic repair."
                    )
                elif status == "automatic":
                    rule["repairability"] = "safe_candidate"
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
        performance: dict | None = None,
        worker_policy: dict | None = None,
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
            "performance": dict(performance or {}),
            "worker_policy": dict(worker_policy or {}),
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
            performance=self._status.get("performance"),
            worker_policy=self._status.get("worker_policy"),
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

    def _timeout_report(
        self,
        package: str,
        *,
        deep_audio: bool,
        timeout_seconds: float,
    ) -> dict:
        report = self._failure_report(
            package,
            TimeoutError("package validation deadline exceeded"),
            deep_audio=deep_audio,
        )
        finding = report["findings"][0]
        finding["code"] = "package.validation-timeout"
        finding["message"] = (
            "Validation exceeded Library Doctor's bounded package deadline and "
            "the isolated worker was stopped. The package was not changed."
        )
        finding["evidence"] = {
            "timeout_seconds": round(max(0.0, timeout_seconds), 3),
        }
        if self._rule_metadata is not None:
            finding["rule"] = self._rule_metadata(
                finding["code"], finding["severity"], finding["category"]
            )
        return report

    def _memory_limit_report(
        self,
        package: str,
        *,
        deep_audio: bool,
        rss_bytes: int,
        limit_bytes: int,
    ) -> dict:
        report = self._failure_report(
            package,
            MemoryError("package validation worker exceeded its RSS budget"),
            deep_audio=deep_audio,
        )
        finding = report["findings"][0]
        finding["code"] = "package.validation-memory-limit"
        finding["message"] = (
            "Validation exceeded Library Doctor's bounded worker memory budget and "
            "the isolated worker was stopped. The package was not changed."
        )
        finding["evidence"] = {
            "rss_bytes": max(0, int(rss_bytes)),
            "limit_bytes": max(0, int(limit_bytes)),
        }
        if self._rule_metadata is not None:
            finding["rule"] = self._rule_metadata(
                finding["code"], finding["severity"], finding["category"]
            )
        return report

    def _close_worker_pool(self, pool, *, force: bool) -> None:
        """Use the bounded pool API while retaining test/host compatibility."""
        try:
            pool.shutdown(
                force=force,
                timeout_seconds=WORKER_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except TypeError:
            pool.shutdown()

    def _run(
        self,
        *,
        force: bool,
        root: Path,
        target_path: Path,
        target: dict,
        deep_audio: bool,
        max_workers: int | None,
        started_at: float,
    ) -> None:
        performance = {
            "discovery_seconds": 0.0,
            "signature_seconds": 0.0,
            "cache_lookup_seconds": 0.0,
            "validation_seconds": 0.0,
            "parallel_validation_wall_seconds": 0.0,
            "worker_validation_seconds": 0.0,
            "worker_queue_seconds": 0.0,
            "source_recheck_seconds": 0.0,
            "source_change_retries": 0,
            "parallel_fallbacks": 0,
            "worker_timeouts": 0,
            "worker_restarts": 0,
            "peak_worker_rss_bytes": 0,
            "worker_rss_limit_bytes": (
                DEEP_AUDIO_WORKER_RSS_LIMIT_BYTES
                if deep_audio else STANDARD_WORKER_RSS_LIMIT_BYTES
            ),
            "worker_memory_limit_exceeded": 0,
            "worker_memory_restarts": 0,
            "cache_write_seconds": 0.0,
            "scope_update_seconds": 0.0,
        }

        def timed_phase(key: str, started_phase: float) -> None:
            performance[key] += max(0.0, time.perf_counter() - started_phase)

        def public_performance() -> dict:
            return {
                key: round(value, 6)
                for key, value in performance.items()
            }

        def validation_options() -> dict:
            options = {}
            if deep_audio:
                options["deep_audio"] = True
            if self._validator_accepts_checkpoint:
                options["scan_checkpoint"] = self._playback_checkpoint
            return options

        try:
            discovery_started = time.perf_counter()
            self._playback_checkpoint(resume_stage="discovering")
            packages, discovery_errors = self._discover_target(
                target_path,
                target["kind"],
                scan_checkpoint=lambda: self._playback_checkpoint(
                    resume_stage="discovering"
                ),
                cancelled=self._cancel.is_set,
            )
            timed_phase("discovery_seconds", discovery_started)
            self._set_status(performance=public_performance())
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
            pending: list[dict] = []
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

            def update_progress(current: str = "") -> None:
                done = len(completed_packages)
                elapsed = max(0.0, time.time() - started_at)
                active_elapsed = max(0.001, self._active_elapsed())
                rate = done / active_elapsed
                eta = (len(packages) - done) / rate if rate > 0 else None
                self._set_status(
                    current=current,
                    done=done,
                    scanned=scanned,
                    reused=reused,
                    elapsed_seconds=elapsed,
                    active_seconds=active_elapsed,
                    packages_per_second=rate,
                    eta_seconds=eta,
                    performance=public_performance(),
                )

            def cache_report(package_name: str, signature: str, report: dict) -> None:
                nonlocal scanned
                operation_started = time.perf_counter()
                try:
                    self._cache.put(
                        package_name,
                        signature,
                        scan_version,
                        report,
                        time.time(),
                    )
                finally:
                    timed_phase("cache_write_seconds", operation_started)
                scanned += 1
                completed_packages.add(package_name)
                update_progress(package_name)

            def cache_unreadable(package_name: str, exc: Exception) -> None:
                cache_report(
                    package_name,
                    f"unreadable:{time.time_ns()}",
                    self._failure_report(package_name, exc, deep_audio=deep_audio),
                )

            # Signatures and cache ownership stay in this parent process.  A
            # forced scan normally leaves every package in ``pending``; an
            # incremental scan avoids starting a pool when everything is cached.
            for path, package_name in zip(packages, relative):
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
                    operation_started = time.perf_counter()
                    signature = self._signature(
                        path, root_namespace, self._playback_checkpoint
                    )
                    timed_phase("signature_seconds", operation_started)
                    operation_started = time.perf_counter()
                    cached = bool(
                        not force
                        and self._cache.cached(
                            package_name, signature, accepted_versions
                        )
                    )
                    timed_phase("cache_lookup_seconds", operation_started)
                    if cached:
                        reused += 1
                        completed_packages.add(package_name)
                        update_progress(package_name)
                    else:
                        pending.append({
                            "path": path,
                            "package": package_name,
                            "signature": signature,
                            "retries": 0,
                        })
                except OSError as exc:
                    cache_unreadable(package_name, exc)

            worker_policy = choose_worker_policy(
                len(pending),
                deep_audio=deep_audio,
                requested_max=max_workers,
                worker_backend_available=self._worker_pool_factory is not None,
            )
            self._set_status(worker_policy=worker_policy)

            def stable_signature(item: dict) -> tuple[bool, str | None]:
                operation_started = time.perf_counter()
                try:
                    current_signature = self._signature(
                        item["path"], root_namespace, self._playback_checkpoint
                    )
                except OSError:
                    current_signature = None
                finally:
                    timed_phase("source_recheck_seconds", operation_started)
                return current_signature == item["signature"], current_signature

            def accept_report(item: dict, report: dict) -> bool:
                unchanged, current_signature = stable_signature(item)
                if unchanged:
                    cache_report(item["package"], item["signature"], report)
                    return True
                item["retries"] += 1
                performance["source_change_retries"] += 1
                if current_signature is not None and item["retries"] <= MAX_SOURCE_CHANGE_RETRIES:
                    item["signature"] = current_signature
                    return False
                exc = RuntimeError("The Feedpak kept changing while it was being scanned.")
                self._log.warning(
                    "Library Doctor could not capture a stable version of %s.",
                    item["package"],
                )
                cache_unreadable(item["package"], exc)
                return True

            def validate_sequential(items) -> None:
                queue = deque(items)
                while queue and not self._cancel.is_set():
                    item = queue.popleft()
                    self._playback_checkpoint()
                    self._set_status(current=item["package"])
                    operation_started = time.perf_counter()
                    try:
                        report = self._validate_feedpak(
                            item["path"],
                            item["package"],
                            **validation_options(),
                        )
                    except Exception as exc:  # One package must not abort the scan.
                        self._log.warning(
                            "Library Doctor validation failed for %s: %s",
                            item["package"],
                            exc,
                        )
                        report = self._failure_report(
                            item["package"], exc, deep_audio=deep_audio
                        )
                    finally:
                        timed_phase("validation_seconds", operation_started)
                    if not accept_report(item, report):
                        queue.append(item)

            selected_workers = int(worker_policy["selected_workers"])
            if not pending:
                pass
            elif self._worker_pool_factory is None:
                validate_sequential(pending)
            else:
                parallel_remaining = deque(pending)
                fallback_items: list[dict] = []
                parallel_started = time.perf_counter()
                active_worker_limit = selected_workers
                worker_rss_limit = int(performance["worker_rss_limit_bytes"])
                package_timeout = self._package_timeout_seconds or (
                    DEEP_AUDIO_PACKAGE_TIMEOUT_SECONDS
                    if deep_audio else STANDARD_PACKAGE_TIMEOUT_SECONDS
                )
                while (
                    parallel_remaining
                    and not fallback_items
                    and not self._cancel.is_set()
                ):
                    pool = None
                    futures: dict = {}
                    restart_after_timeout = False
                    restart_after_memory = False
                    force_close = False
                    try:
                        pool = self._worker_pool_factory(
                            active_worker_limit, self._validator_version
                        )
                        with self._lock:
                            # Publish the pool and copy the current playback state
                            # atomically. ``set_playback_active`` takes these locks
                            # in the same order and cannot miss a replacement pool.
                            with self._playback_condition:
                                self._worker_pool = pool
                                pool.set_paused(self._playback_active)

                        def fill_workers() -> None:
                            # Do not queue behind active work: each submitted
                            # deadline is then a real package execution deadline.
                            capacity = max(1, active_worker_limit)
                            while (
                                parallel_remaining
                                and len(futures) < capacity
                                and not self._cancel.is_set()
                            ):
                                item = parallel_remaining.popleft()
                                future = pool.submit(
                                    item["path"], item["package"], deep_audio
                                )
                                futures[future] = (
                                    item,
                                    self._active_elapsed(),
                                    time.perf_counter(),
                                )

                        fill_workers()
                        while futures and not self._cancel.is_set():
                            finished, _ = concurrent.futures.wait(
                                tuple(futures),
                                timeout=min(0.25, package_timeout),
                                return_when=concurrent.futures.FIRST_COMPLETED,
                            )
                            memory_usage = {}
                            read_memory = getattr(pool, "memory_usage", None)
                            if callable(read_memory):
                                try:
                                    memory_usage = read_memory() or {}
                                except (OSError, RuntimeError, ValueError):
                                    memory_usage = {}
                            peak_rss = max(
                                (int(value) for value in memory_usage.values()),
                                default=0,
                            )
                            performance["peak_worker_rss_bytes"] = max(
                                performance["peak_worker_rss_bytes"], peak_rss
                            )
                            if peak_rss > worker_rss_limit:
                                force_close = True
                                performance["worker_memory_limit_exceeded"] += 1
                                unfinished = [value[0] for value in futures.values()]
                                futures.clear()
                                if active_worker_limit > 1:
                                    # A multi-worker pool cannot safely attribute RSS to
                                    # one future. Retry unfinished work with one isolated
                                    # process, where a repeat is package-specific.
                                    active_worker_limit = 1
                                    parallel_remaining.extendleft(reversed(unfinished))
                                elif unfinished:
                                    item = unfinished[0]
                                    self._log.warning(
                                        "Library Doctor stopped validation for %s after its "
                                        "worker reached %s bytes RSS.",
                                        item["package"],
                                        peak_rss,
                                    )
                                    report = self._memory_limit_report(
                                        item["package"],
                                        deep_audio=deep_audio,
                                        rss_bytes=peak_rss,
                                        limit_bytes=worker_rss_limit,
                                    )
                                    if not accept_report(item, report):
                                        parallel_remaining.append(item)
                                    parallel_remaining.extendleft(
                                        reversed(unfinished[1:])
                                    )
                                restart_after_memory = bool(parallel_remaining)
                                if restart_after_memory:
                                    performance["worker_restarts"] += 1
                                    performance["worker_memory_restarts"] += 1
                                break
                            if not finished:
                                active_now = self._active_elapsed()
                                timed_out = [
                                    future
                                    for future, value in futures.items()
                                    if active_now - value[1] >= package_timeout
                                ]
                                if not timed_out:
                                    continue
                                force_close = True
                                performance["worker_timeouts"] += len(timed_out)
                                for future in timed_out:
                                    item, _active_started, _submitted_at = futures.pop(
                                        future
                                    )
                                    self._log.warning(
                                        "Library Doctor stopped validation for %s after %.3f seconds.",
                                        item["package"],
                                        package_timeout,
                                    )
                                    report = self._timeout_report(
                                        item["package"],
                                        deep_audio=deep_audio,
                                        timeout_seconds=package_timeout,
                                    )
                                    if not accept_report(item, report):
                                        parallel_remaining.append(item)
                                unfinished = [
                                    value[0] for value in futures.values()
                                ]
                                futures.clear()
                                parallel_remaining.extendleft(reversed(unfinished))
                                restart_after_timeout = bool(parallel_remaining)
                                if restart_after_timeout:
                                    performance["worker_restarts"] += 1
                                break

                            for future in finished:
                                item, _active_started, submitted_at = futures.pop(
                                    future
                                )
                                try:
                                    result = future.result()
                                except Exception as exc:
                                    fallback_items = [
                                        item,
                                        *(value[0] for value in futures.values()),
                                        *parallel_remaining,
                                    ]
                                    raise RuntimeError(
                                        "The validation process pool stopped unexpectedly."
                                    ) from exc
                                worker_elapsed = max(
                                    0.0, float(result.get("elapsed_seconds") or 0.0)
                                )
                                performance["worker_validation_seconds"] += worker_elapsed
                                performance["worker_queue_seconds"] += max(
                                    0.0,
                                    time.perf_counter() - submitted_at - worker_elapsed,
                                )
                                outcome = result.get("outcome")
                                if outcome == "cancelled":
                                    if not self._cancel.is_set():
                                        fallback_items = [
                                            item,
                                            *(value[0] for value in futures.values()),
                                            *parallel_remaining,
                                        ]
                                        raise RuntimeError(
                                            "A validation worker stopped before cancellation."
                                        )
                                    break
                                if outcome == "complete" and isinstance(
                                    result.get("report"), dict
                                ):
                                    report = result["report"]
                                else:
                                    error_type = str(
                                        result.get("error_type") or "WorkerError"
                                    )
                                    detail = str(
                                        result.get("error") or "Unknown worker error"
                                    )
                                    self._log.warning(
                                        "Library Doctor validation failed for %s in %s: %s",
                                        item["package"],
                                        error_type,
                                        detail,
                                    )
                                    report = self._failure_report(
                                        item["package"],
                                        RuntimeError(f"{error_type}: {detail}"),
                                        deep_audio=deep_audio,
                                    )
                                if not accept_report(item, report):
                                    parallel_remaining.append(item)
                            fill_workers()
                    except Exception as exc:
                        force_close = True
                        if not self._cancel.is_set():
                            performance["parallel_fallbacks"] += 1
                            self._log.warning(
                                "Library Doctor parallel validation is unavailable; "
                                "continuing safely with one worker: %s",
                                exc,
                            )
                            if not fallback_items:
                                fallback_items = [
                                    *(value[0] for value in futures.values()),
                                    *parallel_remaining,
                                ]
                    finally:
                        if pool is not None:
                            if self._cancel.is_set() or force_close:
                                try:
                                    pool.cancel()
                                except Exception as exc:
                                    self._log.warning(
                                        "Library Doctor could not stop its validation workers: %s",
                                        exc,
                                    )
                            with self._lock:
                                if self._worker_pool is pool:
                                    self._worker_pool = None
                            try:
                                self._close_worker_pool(pool, force=force_close)
                            except Exception as exc:
                                if not fallback_items and not self._cancel.is_set():
                                    self._log.warning(
                                        "Library Doctor validation workers did not close cleanly: %s",
                                        exc,
                                    )
                    if restart_after_timeout or restart_after_memory:
                        continue
                performance["parallel_validation_wall_seconds"] += max(
                    0.0, time.perf_counter() - parallel_started
                )
                if fallback_items and not self._cancel.is_set():
                    # A broken pool can leave the same item in more than one
                    # bookkeeping collection.  Revalidate each unfinished
                    # package once; validation is read-only and deterministic.
                    unique = {}
                    for item in fallback_items:
                        if item["package"] not in completed_packages:
                            unique[item["package"]] = item
                    validate_sequential(unique.values())

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

            if len(completed_packages) != len(packages):
                missing = [
                    name for name in relative if name not in completed_packages
                ]
                raise RuntimeError(
                    f"Validation finished without reports for {len(missing)} package(s)."
                )

            # ``validation_seconds`` remains end-to-end validation wall time
            # for comparable telemetry; worker CPU time is reported separately.
            if selected_workers > 1:
                performance["validation_seconds"] += performance[
                    "parallel_validation_wall_seconds"
                ]

            operation_started = time.perf_counter()
            try:
                if target["kind"] == "library" and not discovery_errors:
                    self._cache.delete_stale(completed_packages)
                self._cache.replace_scope(
                    completed_packages,
                    kind=target["kind"],
                    label=target["label"],
                )
            finally:
                timed_phase("scope_update_seconds", operation_started)
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
                performance=public_performance(),
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
                performance=public_performance(),
                worker_policy=worker_policy,
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
                performance=public_performance(),
                worker_policy=self._status.get("worker_policy"),
            )
