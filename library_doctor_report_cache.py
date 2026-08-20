"""SQLite persistence and cached report queries for Library Doctor."""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path


class ReportCache:
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
                    difficulty_scoped INTEGER NOT NULL DEFAULT 0,
                    full_difficulty_count INTEGER NOT NULL DEFAULT 0,
                    lower_difficulty_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (package, code)
                )
                """
            )
            finding_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(report_findings)")
            }
            for name in (
                "difficulty_scoped",
                "full_difficulty_count",
                "lower_difficulty_count",
            ):
                if name not in finding_columns:
                    conn.execute(
                        f"ALTER TABLE report_findings ADD COLUMN {name} "
                        "INTEGER NOT NULL DEFAULT 0"
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
                        ("target_repairs_available", "1"),
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
            if finding_index is None or finding_index["value"] != "3":
                conn.execute("DELETE FROM report_findings")
                for row in conn.execute("SELECT package, report_json FROM reports"):
                    try:
                        report = json.loads(row["report_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    conn.executemany(
                        "INSERT INTO report_findings "
                        "(package, code, severity, category, finding_count, "
                        "difficulty_scoped, full_difficulty_count, lower_difficulty_count) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        self._finding_rows(row["package"], report),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                    ("finding_index_version", "3"),
                )

    @staticmethod
    def _finding_rows(package: str, report: dict) -> list[tuple]:
        grouped: dict[str, dict] = {}
        findings = report.get("findings") if isinstance(report.get("findings"), list) else []
        features = report.get("features") if isinstance(report.get("features"), dict) else {}
        difficulty_counts = (
            features.get("review_difficulty_counts")
            if isinstance(features.get("review_difficulty_counts"), dict)
            else {}
        )
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
            (
                package,
                code,
                row["severity"],
                row["category"],
                row["count"],
                int(code in difficulty_counts),
                int((difficulty_counts.get(code) or {}).get("full") or 0),
                int((difficulty_counts.get(code) or {}).get("lower") or 0),
            )
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
                "(package, code, severity, category, finding_count, "
                "difficulty_scoped, full_difficulty_count, lower_difficulty_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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

    def replace_scope(
        self,
        packages: set[str],
        *,
        kind: str,
        label: str,
        root: Path,
        repairs_available: bool = True,
    ) -> None:
        with self._lock, self._conn as conn:
            conn.execute("DELETE FROM current_scope")
            if packages:
                conn.executemany(
                    "INSERT INTO current_scope(package) VALUES (?)",
                    ((package,) for package in sorted(packages, key=str.casefold)),
                )
            conn.executemany(
                "INSERT OR REPLACE INTO cache_state(key, value) VALUES (?, ?)",
                (
                    ("target_kind", kind),
                    ("target_label", label),
                    ("target_root", str(root)),
                    ("target_repairs_available", "1" if repairs_available else "0"),
                ),
            )

    def current_scope_root(self) -> str | None:
        """Return the private filesystem root bound to the visible scan scope."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM cache_state WHERE key = 'target_root'"
            ).fetchone()
        if row is None or not isinstance(row["value"], str) or not row["value"]:
            return None
        return row["value"]

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
                "WHERE key IN ("
                "'target_kind', 'target_label', 'target_repairs_available'"
                ")"
            ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        target = {
            "kind": values.get("target_kind", "library"),
            "label": values.get("target_label", "Whole library"),
        }
        if values.get("target_repairs_available", "1") == "0":
            target["repairs_available"] = False
        return target

    def summary(self, review_difficulty_scope: str = "all_authored") -> dict:
        if review_difficulty_scope not in {"full_only", "all_authored"}:
            raise ValueError("Unknown review difficulty scope")
        if review_difficulty_scope == "full_only":
            visible_count = (
                "CASE WHEN rf.difficulty_scoped = 1 "
                "THEN rf.full_difficulty_count ELSE rf.finding_count END"
            )
            with self._lock:
                row = self._conn.execute(
                    f"""
                    WITH package_visibility AS (
                        SELECT
                            r.package,
                            r.lyrics_declared,
                            r.preview_declared,
                            r.deep_audio_skipped,
                            r.deep_audio_unsupported,
                            r.scanned_at,
                            MAX(CASE WHEN ({visible_count}) > 0
                                AND rf.severity = 'error' THEN 1 ELSE 0 END) AS has_error,
                            MAX(CASE WHEN ({visible_count}) > 0
                                AND rf.severity = 'warning' THEN 1 ELSE 0 END) AS has_warning,
                            MAX(CASE WHEN ({visible_count}) > 0
                                AND rf.severity = 'info' THEN 1 ELSE 0 END) AS has_review,
                            SUM(CASE WHEN ({visible_count}) > 0
                                AND rf.severity = 'error' THEN 1 ELSE 0 END) AS error_findings,
                            SUM(CASE WHEN ({visible_count}) > 0
                                AND rf.severity = 'warning' THEN 1 ELSE 0 END) AS warning_findings,
                            SUM(CASE WHEN ({visible_count}) > 0
                                AND rf.severity = 'info' THEN 1 ELSE 0 END) AS review_findings
                        FROM reports AS r
                        INNER JOIN current_scope AS s ON s.package = r.package
                        LEFT JOIN report_findings AS rf ON rf.package = r.package
                        GROUP BY r.package
                    )
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN has_error = 0 AND has_warning = 0
                            AND has_review = 0 THEN 1 ELSE 0 END) AS healthy,
                        SUM(CASE WHEN has_error > 0 THEN 1 ELSE 0 END) AS errors,
                        SUM(CASE WHEN has_error = 0 AND has_warning > 0
                            THEN 1 ELSE 0 END) AS warnings,
                        SUM(CASE WHEN has_review > 0 THEN 1 ELSE 0 END) AS reviews,
                        COALESCE(SUM(error_findings), 0) AS error_findings,
                        COALESCE(SUM(warning_findings), 0) AS warning_findings,
                        COALESCE(SUM(review_findings), 0) AS review_findings,
                        SUM(CASE WHEN lyrics_declared = 0 THEN 1 ELSE 0 END) AS no_lyrics,
                        SUM(CASE WHEN preview_declared = 0 THEN 1 ELSE 0 END) AS no_preview,
                        SUM(CASE WHEN deep_audio_skipped > 0 OR deep_audio_unsupported > 0
                            THEN 1 ELSE 0 END) AS deep_audio_partial,
                        MAX(scanned_at) AS last_scanned_at
                    FROM package_visibility
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
        review_difficulty_scope: str = "all_authored",
    ) -> tuple[str, list[str]]:
        clauses = []
        params: list[str] = []
        visible = (
            "(rf.difficulty_scoped = 0 OR rf.full_difficulty_count > 0)"
            if review_difficulty_scope == "full_only"
            else "(rf.difficulty_scoped = 0 OR "
            "rf.full_difficulty_count + rf.lower_difficulty_count > 0)"
        )
        visible_exists = (
            "EXISTS (SELECT 1 FROM report_findings AS rf "
            f"WHERE rf.package = r.package AND {visible})"
        )
        if result_filter == "problems":
            clauses.append(visible_exists)
        elif result_filter == "errors":
            clauses.append(
                "EXISTS (SELECT 1 FROM report_findings AS rf WHERE rf.package = r.package "
                f"AND rf.severity = 'error' AND {visible})"
            )
        elif result_filter == "warnings":
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM report_findings AS rf WHERE rf.package = r.package "
                f"AND rf.severity = 'error' AND {visible}) AND "
                "EXISTS (SELECT 1 FROM report_findings AS rf WHERE rf.package = r.package "
                f"AND rf.severity = 'warning' AND {visible})"
            )
        elif result_filter == "review":
            clauses.append(
                "EXISTS (SELECT 1 FROM report_findings AS rf WHERE rf.package = r.package "
                f"AND rf.severity = 'info' AND {visible})"
            )
        elif result_filter == "healthy":
            clauses.append(f"NOT {visible_exists}")
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
                f"WHERE rf.package = r.package AND rf.code = ? AND {visible})"
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
        review_difficulty_scope: str,
        limit: int,
        offset: int,
    ) -> dict:
        where, params = self._where(
            result_filter,
            query,
            rule_code,
            review_difficulty_scope,
        )
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
            report = self._review_difficulty_report(
                report, review_difficulty_scope
            )
            items.append(report)
        return {"total": int(total), "limit": limit, "offset": offset, "items": items}

    @staticmethod
    def _review_difficulty_report(report: dict, scope: str) -> dict:
        scoped = dict(report)
        features = dict(report.get("features") or {})
        counts_by_code = features.get("review_difficulty_counts")
        if not isinstance(counts_by_code, dict):
            return scoped
        visible_findings = []
        hidden_lower = 0
        for finding in report.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            difficulty = counts_by_code.get(finding.get("code"))
            if not isinstance(difficulty, dict):
                visible_findings.append(finding)
                continue
            full_count = max(0, int(difficulty.get("full") or 0))
            lower_count = max(0, int(difficulty.get("lower") or 0))
            hidden_lower += lower_count
            visible_count = (
                full_count if scope == "full_only" else full_count + lower_count
            )
            if visible_count == 0:
                continue
            visible = dict(finding)
            visible["affected_count"] = visible_count
            message = str(visible.get("message") or "")
            first, separator, rest = message.partition(" ")
            if separator and first.isdigit():
                visible["message"] = f"{visible_count} {rest}"
            visible_findings.append(visible)
        scoped["findings"] = visible_findings
        scoped["counts"] = {
            severity: sum(
                finding.get("severity") == severity
                for finding in visible_findings
                if isinstance(finding, dict)
            )
            for severity in ("error", "warning", "info")
        }
        if scoped["counts"]["error"]:
            scoped["status"] = "error"
        elif scoped["counts"]["warning"]:
            scoped["status"] = "warning"
        elif scoped["counts"]["info"]:
            scoped["status"] = "review"
        else:
            scoped["status"] = "healthy"
        features["review_difficulty_filter"] = {
            "scope": scope,
            "hidden_lower_count": hidden_lower if scope == "full_only" else 0,
        }
        scoped["features"] = features
        return scoped

    def rules(self, review_difficulty_scope: str = "all_authored") -> list[dict]:
        visible = (
            "WHERE (rf.difficulty_scoped = 0 OR rf.full_difficulty_count > 0)"
            if review_difficulty_scope == "full_only"
            else "WHERE (rf.difficulty_scoped = 0 OR "
            "rf.full_difficulty_count + rf.lower_difficulty_count > 0)"
        )
        finding_count = (
            "CASE WHEN rf.difficulty_scoped = 1 "
            "THEN rf.full_difficulty_count ELSE rf.finding_count END"
            if review_difficulty_scope == "full_only"
            else "CASE WHEN rf.difficulty_scoped = 1 THEN "
            "rf.full_difficulty_count + rf.lower_difficulty_count "
            "ELSE rf.finding_count END"
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    rf.code,
                    rf.severity,
                    rf.category,
                    COUNT(*) AS package_count,
                    COALESCE(SUM({finding_count}), 0) AS finding_count
                FROM report_findings AS rf
                INNER JOIN current_scope AS s ON s.package = rf.package
                {visible}
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
        review_difficulty_scope: str = "all_authored",
        include_signature: bool = False,
    ) -> list[dict]:
        where, params = self._where(
            result_filter, query, rule_code, review_difficulty_scope
        )
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
        review_difficulty_scope: str = "all_authored",
    ):
        """Yield export reports without retaining the whole result set in memory."""
        where, params = self._where(
            result_filter, query, rule_code, review_difficulty_scope
        )
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
