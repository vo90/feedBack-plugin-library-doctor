"""Bounded, recoverable Feedpak preview generation.

This module is intentionally write-free. It selects a representative excerpt
from the canonical full mix, renders an in-memory Ogg candidate, and returns a
transaction plan. ``repair.py`` owns candidate package construction, complete
validation, temporary recovery storage, atomic commit, and recovery cleanup.
"""

from __future__ import annotations

import array
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path, PurePosixPath

import yaml

try:
    from repair_eligibility import preview_source_path
except ModuleNotFoundError:  # Tests and some plugin hosts load files by path.
    _eligibility_name = "_library_doctor_repair_eligibility"
    _eligibility = sys.modules.get(_eligibility_name)
    if _eligibility is None:
        _eligibility_spec = importlib.util.spec_from_file_location(
            _eligibility_name,
            Path(__file__).resolve().with_name("repair_eligibility.py"),
        )
        _eligibility = importlib.util.module_from_spec(_eligibility_spec)
        sys.modules[_eligibility_name] = _eligibility
        _eligibility_spec.loader.exec_module(_eligibility)
    preview_source_path = _eligibility.preview_source_path


PREVIEW_RULE_CODES = frozenset({
    "media.preview-missing",
    "media.preview-too-short",
    "media.preview-too-long",
    "media.preview-regenerate",
})
PREVIEW_DURATION_SECONDS = 30.0
PREVIEW_FADE_SECONDS = 1.0
MIN_SOURCE_DURATION_SECONDS = 1.0
MAX_PREVIEW_SOURCE_BYTES = 128 * 1024 * 1024
MAX_PREVIEW_CANDIDATE_BYTES = 8 * 1024 * 1024
MAX_METADATA_MEMBER_BYTES = 16 * 1024 * 1024
PLAN_TTL_SECONDS = 30 * 60
MAX_CACHED_PLANS = 4
LOUDNESS_SAMPLE_RATE = 400
MAX_LOUDNESS_ANALYSIS_SECONDS = 8 * 60 * 60
MAX_LOUDNESS_PCM_BYTES = (
    MAX_LOUDNESS_ANALYSIS_SECONDS * LOUDNESS_SAMPLE_RATE * 2
)
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_BORING_SECTION_NAMES = frozenset({
    "intro", "outro", "silence", "count", "countin", "noguitar",
})


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses ambiguous repeated mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def supports(rule_code: str) -> bool:
    return rule_code in PREVIEW_RULE_CODES


def _digest(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _member_path(value) -> str | None:
    if not isinstance(value, str) or not value or "\0" in value:
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _report_codes(report: dict) -> set[str]:
    return {
        finding.get("code")
        for finding in report.get("findings", [])
        if isinstance(finding, dict) and isinstance(finding.get("code"), str)
    }


def _finite_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _format_bytes(size: int) -> str:
    value = float(max(0, size))
    units = ("bytes", "KB", "MB", "GB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024 or candidate == units[-1]:
            break
        value /= 1024
    return f"{value:.0f} {unit}" if unit in {"bytes", "KB"} else f"{value:.1f} {unit}"


def _resolve_ffmpeg() -> str | None:
    explicit = os.environ.get("FEEDBACK_FFMPEG")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())

    roots = [Path(sys.executable).resolve().parent.parent]
    roots.extend(Path(__file__).resolve().parents[:6])
    for root in roots:
        bundled = root / "bin"
        if not any(
            (bundled / name).is_file()
            for name in ("vgmstream-cli", "vgmstream-cli.exe")
        ):
            continue
        for name in ("ffmpeg", "ffmpeg.exe"):
            candidate = bundled / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("ffmpeg")


def _hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _probe_with_ffmpeg(source: bytes) -> float:
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "Library Doctor could not find FeedBack's audio converter. Repair or reinstall "
            "FeedBack, or configure FEEDBACK_FFMPEG, then restart the game."
        )
    with tempfile.TemporaryDirectory(prefix="library-doctor-preview-") as raw_dir:
        source_path = Path(raw_dir) / "source.ogg"
        source_path.write_bytes(source)
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-i", str(source_path)],
                capture_output=True,
                timeout=30,
                **_hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("FFmpeg could not inspect the song audio.") from exc
    match = _DURATION_RE.search(result.stderr.decode(errors="replace"))
    if not match:
        raise RuntimeError("The song audio duration could not be confirmed.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _render_with_ffmpeg(
    source: bytes,
    start: float,
    target_duration: float,
) -> bytes:
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "Library Doctor could not find FeedBack's audio converter. Repair or reinstall "
            "FeedBack, or configure FEEDBACK_FFMPEG, then restart the game."
        )
    with tempfile.TemporaryDirectory(prefix="library-doctor-preview-") as raw_dir:
        root = Path(raw_dir)
        source_path = root / "source.ogg"
        output_path = root / "candidate.ogg"
        source_path.write_bytes(source)
        fade = min(PREVIEW_FADE_SECONDS, max(0.1, target_duration / 4))
        fade_out = max(0.0, target_duration - fade)
        common = [
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(source_path),
            "-t", f"{target_duration:.3f}", "-vn", "-map_metadata", "-1",
            "-af", (
                f"afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={fade_out:.3f}:d={fade:.3f}"
            ),
        ]
        commands = (
            common + ["-c:a", "libvorbis", "-q:a", "3", str(output_path)],
            common + [
                "-c:a", "vorbis", "-strict", "experimental", "-q:a", "3",
                str(output_path),
            ],
        )
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=180,
                    **_hidden_subprocess_kwargs(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("FFmpeg could not generate the proposed preview.") from exc
            if result.returncode == 0 and output_path.is_file():
                candidate = output_path.read_bytes()
                if 1_000 <= len(candidate) <= MAX_PREVIEW_CANDIDATE_BYTES:
                    return candidate
            output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "FFmpeg could not encode a playable Ogg preview. The Feedpak was left unchanged."
        )


def _loudest_start_with_ffmpeg(
    source: bytes,
    duration: float,
    target_duration: float,
) -> float | None:
    """Choose the loudest contiguous target-duration window from bounded PCM."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg or duration <= target_duration:
        return 0.0
    if duration > MAX_LOUDNESS_ANALYSIS_SECONDS:
        return None
    with tempfile.TemporaryDirectory(prefix="library-doctor-preview-analysis-") as raw_dir:
        source_path = Path(raw_dir) / "source.ogg"
        pcm_path = Path(raw_dir) / "analysis.pcm"
        source_path.write_bytes(source)
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(source_path), "-t", f"{duration:.3f}", "-vn", "-ac", "1",
            "-ar", str(LOUDNESS_SAMPLE_RATE), "-fs", str(MAX_LOUDNESS_PCM_BYTES),
            "-f", "s16le", "-acodec", "pcm_s16le", str(pcm_path),
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                **_hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        try:
            pcm_size = pcm_path.stat().st_size
            if (
                result.returncode != 0
                or pcm_size < 2
                or pcm_size > MAX_LOUDNESS_PCM_BYTES
            ):
                return None
            pcm = pcm_path.read_bytes()
        except OSError:
            return None
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    window = max(1, round(target_duration * LOUDNESS_SAMPLE_RATE))
    if len(samples) <= window:
        return 0.0
    energy = sum(int(sample) * int(sample) for sample in samples[:window])
    best_energy = energy
    best_index = 0
    stride = max(1, LOUDNESS_SAMPLE_RATE // 2)
    for index in range(window, len(samples)):
        entering = int(samples[index])
        leaving = int(samples[index - window])
        energy += entering * entering - leaving * leaving
        start_index = index - window + 1
        if start_index % stride == 0 and energy > best_energy:
            best_energy = energy
            best_index = start_index
    return best_index / LOUDNESS_SAMPLE_RATE


def _full_mix_path(manifest: dict) -> str | None:
    return preview_source_path(manifest)


def _manifest_with_preview(
    manifest_raw: bytes,
    preview_path: str,
    manifest: dict,
) -> bytes:
    """Set one top-level preview pointer while preserving YAML text when possible."""
    try:
        text = manifest_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("manifest is not UTF-8") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    insertion = f"preview: {preview_path}{newline}"
    lines = text.splitlines(keepends=True)
    if "preview" in manifest:
        for index, line in enumerate(lines):
            if re.match(r"^preview\s*:", line):
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                comment = ""
                value_text = line.split(":", 1)[1]
                if " #" in value_text:
                    comment = " #" + value_text.split(" #", 1)[1].rstrip("\r\n")
                lines[index] = f"preview: {preview_path}{comment}{ending}"
                return "".join(lines).encode("utf-8")
        updated = dict(manifest)
        updated["preview"] = preview_path
        return yaml.safe_dump(
            updated,
            allow_unicode=True,
            sort_keys=False,
            line_break=newline,
        ).encode("utf-8")
    insert_at = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        if re.match(r"^\.\.\.\s*(?:#.*)?(?:\r?\n)?$", lines[index]):
            insert_at = index
            break
        if lines[index].strip():
            break
    prefix = "".join(lines[:insert_at])
    suffix = "".join(lines[insert_at:])
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += newline
    rendered = (prefix + insertion + suffix).encode("utf-8")
    return rendered


class PreviewRepairEngine:
    """Create listenable preview candidates bound to current Feedpak bytes."""

    def __init__(
        self,
        *,
        validate_feedpak,
        error_type,
        log,
        probe_duration=None,
        render_preview=None,
        select_loudest_start=None,
    ) -> None:
        self._validate_feedpak = validate_feedpak
        self._error_type = error_type
        self._log = log
        self._probe_duration = probe_duration or _probe_with_ffmpeg
        self._render_preview = render_preview or _render_with_ffmpeg
        self._select_loudest_start = (
            select_loudest_start or _loudest_start_with_ffmpeg
        )
        self._plans: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def supports(rule_code: str) -> bool:
        return supports(rule_code)

    def tool_status(
        self,
        _package_path: Path,
        package_name: str,
        read_member,
        member_exists,
    ) -> dict:
        """Describe Preview Creator eligibility from bounded package metadata."""
        _manifest_raw, manifest = self._load_manifest(read_member)

        preview_path = _member_path(manifest.get("preview"))
        preview_declared = bool(preview_path)
        current_preview_available = bool(
            preview_declared
            and preview_path.lower().endswith(".ogg")
            and member_exists(preview_path)
        )

        full_mix_path = _full_mix_path(manifest)
        full_mix_available = bool(
            full_mix_path
            and full_mix_path.lower().endswith(".ogg")
            and member_exists(full_mix_path)
        )

        rule_code = None
        message = "Preview Creator is ready."
        if not preview_declared:
            rule_code = "media.preview-missing"
        elif current_preview_available:
            rule_code = "media.preview-regenerate"
        elif preview_path and not preview_path.lower().endswith(".ogg"):
            message = (
                "This Feedpak declares a preview format Library Doctor cannot replace safely. "
                "Resolve that package problem before using Preview Creator."
            )
        else:
            message = (
                "This Feedpak declares a preview file that is unavailable. Resolve that "
                "package problem before using Preview Creator."
            )

        if not full_mix_available:
            message = (
                "Preview Creator needs one available manifest-declared Ogg full mix. "
                "This Feedpak does not provide one unambiguously."
            )

        available = bool(rule_code and full_mix_available)
        title = manifest.get("title")
        artist = manifest.get("artist")
        return {
            "schema": "library_doctor.preview_tool_status.v1",
            "package": package_name,
            "title": title if isinstance(title, str) else None,
            "artist": artist if isinstance(artist, str) else None,
            "available": available,
            "rule_code": rule_code if available else None,
            "preview_declared": preview_declared,
            "current_preview_available": current_preview_available,
            "full_mix_available": full_mix_available,
            "message": message,
        }

    def preview(
        self,
        package_path: Path,
        package_name: str,
        rule_code: str,
        read_member,
        *,
        catalog_version: str,
        validator_version: str,
        start_seconds: float | None = None,
        verified_before_report: dict | None = None,
    ) -> dict:
        if not supports(rule_code):
            raise self._error_type(
                "unsupported_repair", "This finding does not have an audio preview repair."
            )
        with self._lock:
            self._prune()
            before = (
                verified_before_report
                if isinstance(verified_before_report, dict)
                else self._validate_feedpak(
                    package_path, package_name, deep_audio=True
                )
            )
            missing_preview = (
                rule_code == "media.preview-missing"
                and not bool(before.get("features", {}).get("preview_declared"))
            )
            regenerate_preview = bool(
                rule_code == "media.preview-regenerate"
                and before.get("features", {}).get("preview_declared")
                and before.get("features", {}).get("preview_available")
            )
            if (
                rule_code not in _report_codes(before)
                and not missing_preview
                and not regenerate_preview
            ):
                raise self._error_type(
                    "nothing_to_repair",
                    "This preview recommendation is no longer present in the package.",
                )

            manifest_raw, manifest = self._load_manifest(read_member)

            full_mix_path = _full_mix_path(manifest)
            if not full_mix_path or not full_mix_path.lower().endswith(".ogg"):
                raise self._error_type(
                    "full_mix_unavailable",
                    "Library Doctor needs one manifest-declared Ogg full mix to generate a preview safely.",
                )
            source = read_member(full_mix_path, MAX_PREVIEW_SOURCE_BYTES)
            if len(source) < 1_000:
                raise self._error_type(
                    "full_mix_unavailable", "The full song mix is too small to use safely."
                )
            try:
                duration = float(self._probe_duration(source))
            except (TypeError, ValueError, RuntimeError) as exc:
                raise self._error_type("audio_tool_failed", str(exc)) from exc
            if not math.isfinite(duration) or duration < MIN_SOURCE_DURATION_SECONDS:
                raise self._error_type(
                    "song_too_short",
                    "The available song audio is too short to create a useful preview.",
                )

            declared_preview_path = _member_path(manifest.get("preview"))
            target_original = None
            target_original_present = False
            manifest_replacement = None
            if declared_preview_path and declared_preview_path != full_mix_path:
                if not declared_preview_path.lower().endswith(".ogg"):
                    raise self._error_type(
                        "preview_unsupported",
                        "Library Doctor only replaces manifest-declared Ogg previews.",
                    )
                target_path = declared_preview_path
                target_original = read_member(target_path, MAX_PREVIEW_SOURCE_BYTES)
                target_original_present = True
            else:
                target_path = self._available_preview_path(read_member)
                try:
                    manifest_replacement = _manifest_with_preview(
                        manifest_raw, target_path, manifest
                    )
                    parsed = yaml.load(
                        manifest_replacement.decode("utf-8"),
                        Loader=_UniqueSafeLoader,
                    )
                except (ValueError, UnicodeDecodeError, yaml.YAMLError) as exc:
                    raise self._error_type(
                        "manifest_unavailable",
                        "Library Doctor could not add a safe preview pointer to this manifest.",
                    ) from exc
                expected = dict(manifest)
                expected["preview"] = target_path
                if parsed != expected:
                    raise self._error_type(
                        "manifest_unavailable",
                        "Library Doctor could not preserve the manifest while adding its preview pointer.",
                    )

            target_duration = min(PREVIEW_DURATION_SECONDS, duration)
            max_start = max(0.0, duration - target_duration)
            chosen = _finite_number(start_seconds)
            if start_seconds is not None and chosen is None:
                raise self._error_type(
                    "invalid_preview_start", "Choose a valid preview start time."
                )
            if chosen is None:
                chosen, reason = self._automatic_start(
                    manifest,
                    read_member,
                    source,
                    duration,
                    target_duration,
                )
            else:
                if chosen < 0 or chosen > max_start:
                    raise self._error_type(
                        "invalid_preview_start",
                        f"Choose a start between 0 and {max_start:.1f} seconds.",
                    )
                reason = "user-selected position"
            start = min(max(0.0, float(chosen)), max_start)
            try:
                candidate = self._render_preview(source, start, target_duration)
                candidate_duration = float(self._probe_duration(candidate))
            except (TypeError, ValueError, RuntimeError) as exc:
                raise self._error_type("audio_tool_failed", str(exc)) from exc
            minimum_candidate = max(0.5, target_duration - 2.0)
            if (
                not candidate.startswith(b"OggS")
                or not minimum_candidate <= candidate_duration <= target_duration + 0.5
                or len(candidate) > MAX_PREVIEW_CANDIDATE_BYTES
            ):
                raise self._error_type(
                    "candidate_failed",
                    "The generated audio did not pass preview length and Ogg checks.",
                )
            if regenerate_preview and target_original == candidate:
                raise self._error_type(
                    "preview_unchanged",
                    "The proposed excerpt exactly matches the current preview. Choose another start time to create a different preview.",
                )

            source_sha = hashlib.sha256(source).hexdigest()
            target_sha = (
                hashlib.sha256(target_original).hexdigest()
                if target_original_present else None
            )
            unsigned = {
                "schema": "library_doctor.preview_repair_plan.v2",
                "catalog_version": catalog_version,
                "validator_version": validator_version,
                "package": package_name,
                "rule_code": rule_code,
                "full_mix_path": full_mix_path,
                "preview_path": target_path,
                "source_sha256": source_sha,
                "target_original_present": target_original_present,
                "target_original_sha256": target_sha,
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
                "start_seconds": round(start, 3),
                "duration_seconds": round(duration, 3),
                "target_duration_seconds": round(target_duration, 3),
            }
            plan_id = _digest(unsigned)
            original_size = len(target_original) if target_original is not None else 0
            savings = original_size - len(candidate)
            creating = not target_original_present
            members = []
            if manifest_replacement is not None:
                members.append({
                    "member_path": "manifest.yaml",
                    "raw": manifest_raw,
                    "replacement": manifest_replacement,
                })
            members.append({
                "member_path": target_path,
                "raw": target_original,
                "replacement": candidate,
            })
            plan = {
                **unsigned,
                "plan_id": plan_id,
                "available": True,
                "action_kind": (
                    "create_song_preview" if creating else "replace_song_preview"
                ),
                "source_kind": "full_mix",
                "item_name": "audio preview",
                "safety": "review_required",
                "automatic_available": True,
                "title": (
                    "Create a song preview" if rule_code == "media.preview-missing"
                    else "Create a different song preview"
                    if rule_code == "media.preview-regenerate"
                    else "Create a new song preview"
                ),
                "description": (
                    "Generate a new preview from the Feedpak's full song mix. Library Doctor "
                    "uses the same selection standard for manual and automatic repair; manual "
                    "review lets you listen or choose another start before applying it."
                ),
                "player_result": (
                    "Library browsing plays the new representative excerpt. The full song mix "
                    "and gameplay audio remain unchanged."
                ),
                "user_value": (
                    "The song has a useful, compact library preview without requiring the user "
                    "to choose an excerpt manually."
                ),
                "change_kind": "replace_media",
                "change_count": 1,
                "removed_count": 0,
                "arrays_affected": 0,
                "musical_positions": 1,
                "member_count": len(members),
                "media": {
                    "original_duration_seconds": (
                        round(float(self._probe_duration(target_original)), 1)
                        if target_original_present else 0.0
                    ),
                    "candidate_duration_seconds": round(candidate_duration, 1),
                    "start_seconds": round(start, 1),
                    "max_start_seconds": round(max_start, 1),
                    "selection_reason": reason,
                    "original_bytes": original_size,
                    "candidate_bytes": len(candidate),
                    "estimated_package_savings_bytes": savings,
                    "original_size": _format_bytes(original_size),
                    "candidate_size": _format_bytes(len(candidate)),
                    "estimated_package_savings": _format_bytes(max(0, savings)),
                    "creates_preview": creating,
                    "source_path": full_mix_path,
                },
                "file_handling": {
                    "package_replaced": True,
                    "duplicate_library_package_created": False,
                    "backup_created": True,
                    "backup_contents": "original_preview_and_manifest_state",
                    "summary": (
                        "Library Doctor validates a complete candidate before replacing the Feedpak "
                        "at the same path. Temporary recovery data protects the write and is removed "
                        "automatically after success; no duplicate playable song or pending preview backup remains."
                    ),
                },
                "_members": members,
                "_manifest_raw": manifest_raw,
                "_created_at": time.monotonic(),
                "_candidate": candidate,
            }
            self._plans[plan_id] = plan
            self._plans.move_to_end(plan_id)
            self._prune()
            return plan

    def _load_manifest(self, read_member) -> tuple[bytes, dict]:
        manifest_raw = read_member("manifest.yaml", MAX_METADATA_MEMBER_BYTES)
        try:
            manifest = yaml.load(
                manifest_raw.decode("utf-8"), Loader=_UniqueSafeLoader
            ) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise self._error_type(
                "manifest_unavailable",
                "The Feedpak manifest cannot be read safely for preview repair.",
            ) from exc
        if not isinstance(manifest, dict):
            raise self._error_type(
                "manifest_unavailable",
                "The Feedpak manifest cannot be read safely for preview repair.",
            )
        return manifest_raw, manifest

    def claim(
        self,
        package_path: Path,
        package_name: str,
        rule_code: str,
        plan_id: str,
        read_member,
    ) -> dict:
        with self._lock:
            self._prune()
            plan = self._plans.get(plan_id)
            if not isinstance(plan, dict):
                raise self._error_type(
                    "invalid_plan",
                    "The proposed audio preview expired. Review it again before applying.",
                )
            if plan.get("package") != package_name or plan.get("rule_code") != rule_code:
                raise self._error_type(
                    "invalid_plan", "This audio preview belongs to a different repair."
                )
            source = read_member(plan["full_mix_path"], MAX_PREVIEW_SOURCE_BYTES)
            manifest_raw = read_member("manifest.yaml", MAX_METADATA_MEMBER_BYTES)
            if (
                hashlib.sha256(source).hexdigest() != plan["source_sha256"]
                or hashlib.sha256(manifest_raw).hexdigest() != plan["manifest_sha256"]
            ):
                raise self._error_type(
                    "source_changed",
                    "The song changed after this audio preview was generated. Review it again before applying.",
                )
            if plan["target_original_present"]:
                current = read_member(plan["preview_path"], MAX_PREVIEW_SOURCE_BYTES)
                if hashlib.sha256(current).hexdigest() != plan["target_original_sha256"]:
                    raise self._error_type(
                        "source_changed",
                        "The song preview changed after this proposal was generated. Review it again before applying.",
                    )
            elif self._optional_member(plan["preview_path"], read_member) is not None:
                raise self._error_type(
                    "source_changed",
                    "A song file now occupies the proposed preview path. Review the repair again.",
                )
            self._plans.move_to_end(plan_id)
            return plan

    def audio(self, plan_id: str) -> bytes:
        with self._lock:
            self._prune()
            plan = self._plans.get(plan_id)
            candidate = plan.get("_candidate") if isinstance(plan, dict) else None
            if not isinstance(candidate, bytes):
                raise self._error_type(
                    "preview_expired", "This proposed preview expired. Generate it again."
                )
            self._plans.move_to_end(plan_id)
            return candidate

    def discard(self, plan_id: str) -> None:
        with self._lock:
            self._plans.pop(plan_id, None)

    def _automatic_start(
        self,
        manifest: dict,
        read_member,
        source: bytes,
        duration: float,
        target_duration: float,
    ) -> tuple[float, str]:
        lyrics_path = _member_path(manifest.get("lyrics"))
        if lyrics_path:
            data = self._read_json(lyrics_path, read_member)
            if isinstance(data, list):
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    word = entry.get("w")
                    lyric_time = _finite_number(entry.get("t"))
                    if (
                        isinstance(word, str)
                        and word.strip() not in {"", "+", "-"}
                        and lyric_time is not None
                        and lyric_time >= 0
                    ):
                        return max(0.0, lyric_time - 2.0), (
                            f"first vocal at {lyric_time:.1f}s with a 2-second lead-in"
                        )

        timeline_paths = []
        timeline = _member_path(manifest.get("song_timeline"))
        if timeline:
            timeline_paths.append(timeline)
        for arrangement in manifest.get("arrangements", []) or []:
            if isinstance(arrangement, dict):
                path = _member_path(arrangement.get("file"))
                if path and path not in timeline_paths:
                    timeline_paths.append(path)
        section_hits = []
        for path in timeline_paths[:50]:
            data = self._read_json(path, read_member)
            if not isinstance(data, dict):
                continue
            for section in data.get("sections", []) or []:
                if not isinstance(section, dict):
                    continue
                section_time = _finite_number(section.get("time"))
                name = str(section.get("name") or "").strip()
                normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
                if (
                    section_time is not None
                    and section_time >= 2.0
                    and section_time <= duration - target_duration
                    and normalized
                    and normalized not in _BORING_SECTION_NAMES
                ):
                    section_hits.append((section_time, name or "song section"))
        if section_hits:
            section_time, name = min(section_hits, key=lambda item: item[0])
            return section_time, f"first representative section, {name}, at {section_time:.1f}s"

        try:
            loudest = self._select_loudest_start(
                source, duration, target_duration
            )
        except Exception as exc:
            self._log.debug("Library Doctor loudness selection failed: %s", exc)
            loudest = None
        if loudest is not None and math.isfinite(float(loudest)):
            return float(loudest), "loudest representative section of the full mix"

        return duration * 0.25, (
            "25% into the song because no usable vocal, section, or loudness cue was available"
        )

    def _available_preview_path(self, read_member) -> str:
        candidates = ["preview.ogg", "library-doctor-preview.ogg"]
        candidates.extend(
            f"library-doctor-preview-{index}.ogg" for index in range(2, 100)
        )
        for path in candidates:
            if self._optional_member(path, read_member) is None:
                return path
        raise self._error_type(
            "preview_path_unavailable",
            "Library Doctor could not find an unused safe path for the generated preview.",
        )

    @staticmethod
    def _optional_member(member_path: str, read_member):
        try:
            return read_member(member_path, MAX_PREVIEW_SOURCE_BYTES)
        except Exception as exc:
            if getattr(exc, "code", None) in {None, "member_unavailable"}:
                return None
            raise

    def _read_json(self, member_path: str, read_member):
        try:
            raw = read_member(member_path, MAX_METADATA_MEMBER_BYTES)
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._log.debug(
                "Library Doctor could not use %s for preview selection: %s",
                member_path,
                exc,
            )
            return None

    def _prune(self) -> None:
        cutoff = time.monotonic() - PLAN_TTL_SECONDS
        stale = [
            plan_id for plan_id, plan in self._plans.items()
            if float(plan.get("_created_at") or 0.0) < cutoff
        ]
        for plan_id in stale:
            self._plans.pop(plan_id, None)
        while len(self._plans) > MAX_CACHED_PLANS:
            self._plans.popitem(last=False)
