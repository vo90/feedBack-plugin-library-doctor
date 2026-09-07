"""Pure, bounded validation for saved source reviews and legacy source choices.

A digest detects inconsistent saved state; it is not proof of authenticity or
current files. The manager must still check scope, paths and fresh input hashes.
"""
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath


CHECKPOINT_SCHEMA = "library_doctor.source_recovery_ready.v1"
BATCH_SCHEMA = "library_doctor.source_recovery_batch.v1"
SCOPE_SCHEMA = "library_doctor.source_recovery_scope.v1"
POLICY_VERSION = "source-recovery-fast-preview-v1"
MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_PACKAGES = 10000
MAX_NODES = 1000000


def _wire(value):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > MAX_NODES:
            raise ValueError("The source review exceeds supported structure bounds.")
        if type(item) is dict:
            if not all(type(key) is str for key in item):
                raise ValueError("Source review object keys must be strings.")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Source review numbers must be finite.")
        elif type(item) is int:
            if not -(2 ** 63) <= item < 2 ** 63:
                raise ValueError("Source review integers exceed supported bounds.")
        elif item is not None and type(item) not in {str, int, bool}:
            raise ValueError("The source review must contain ordinary JSON values.")
    chunks, size = [], 0
    try:
        for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size > MAX_STATE_BYTES:
                raise ValueError("The source review exceeds its 32 MiB storage bound.")
            chunks.append(encoded)
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("The source review cannot be serialized safely.") from exc
    return b"".join(chunks)


def _digest(value):
    return hashlib.sha256(_wire(value)).hexdigest()


def _text(value, label, *, empty=False, limit=4096):
    if type(value) is not str or (not empty and not value.strip()) or "\0" in value or len(value) > limit:
        raise ValueError(f"The source review has an invalid {label}.")
    return value


def _count(value, label, maximum=100000000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"The source review has an invalid {label}.")
    return value


def _path(value, label, *, suffix=None):
    value = _text(value, label)
    if not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
        raise ValueError(f"The source review {label} must be absolute.")
    if suffix and not value.lower().endswith(suffix):
        raise ValueError(f"The source review has an invalid {label} extension.")
    return value


def _package(value):
    value = _text(value, "package path")
    if ("\\" in value or ":" in value or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).suffix.lower() not in {".feedpak", ".sloppak"}):
        raise ValueError("The source review has an invalid relative package path.")
    return value


def _hash(value, label):
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"The source review has an invalid {label}.")


def _snapshot_rows(snapshot):
    if type(snapshot) is not dict or snapshot.get("schema") != SCOPE_SCHEMA:
        raise ValueError("The source review has an unknown scan scope.")
    target = snapshot.get("target")
    if type(target) is dict:
        if not {"kind", "label"}.issubset(target) or set(target) - {"kind", "label", "repairs_available"}:
            raise ValueError("The source review has an invalid scan target.")
        _text(target["kind"], "scan target kind")
        _text(target["label"], "scan target label")
        if "repairs_available" in target and type(target["repairs_available"]) is not bool:
            raise ValueError("The source review has invalid scan repair availability.")
    else:
        _text(target, "scan target")  # Older synthetic/legacy scope producers.
    _text(snapshot.get("validator_version"), "validator version", limit=256)
    scanned = snapshot.get("scanned_at")
    if type(scanned) not in {int, float} or not math.isfinite(scanned) or scanned < 0:
        raise ValueError("The source review has an invalid scan timestamp.")
    rows = snapshot.get("candidates")
    if type(rows) is not list or len(rows) > MAX_PACKAGES:
        raise ValueError("The source review has an invalid scan package list.")
    if _count(snapshot.get("scope_package_count"), "scan package count", MAX_PACKAGES) != len(rows):
        raise ValueError("The source review scan package count is inconsistent.")
    indexed = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("The source review has an invalid scan package row.")
        name = _package(row.get("package"))
        if name in indexed:
            raise ValueError("The source review repeats a scan package.")
        _text(row.get("scan_signature"), "scan signature")
        indexed[name] = row
    return indexed


def _preview_rows(preview, scope):
    if type(preview) is not dict:
        raise ValueError("The source review has no completed preview.")
    _path(preview.get("source_folder"), "source folder")
    rows = preview.get("packages")
    if type(rows) is not list or len(rows) > MAX_PACKAGES:
        raise ValueError("The source review has an invalid package list.")
    indexed, counts, changes = {}, {"eligible": 0, "blocked": 0, "unchanged": 0}, 0
    for row in rows:
        if type(row) is not dict:
            raise ValueError("The source review has an invalid package row.")
        name = _package(row.get("package"))
        if name in indexed:
            raise ValueError("The source review repeats a package.")
        state = row.get("status")
        if type(state) is not str or state not in counts:
            raise ValueError("The source review has an unknown package status.")
        counts[state] += 1
        amount = _count(row.get("change_count"), "package change count", 10000)
        _count(row.get("excluded_count"), "excluded count")
        members = _count(row.get("member_count"), "member count", 1000)
        source_path = _text(row.get("source_path"), "source path", empty=True)
        if source_path:
            _path(source_path, "source path", suffix=".psarc")
        if state == "eligible":
            if not amount or not members or not source_path:
                raise ValueError("An eligible source review row has no changes, members or source.")
            changes += amount
        elif amount:
            raise ValueError("A noneligible source review row claims changes.")
        indexed[name] = row
    if set(indexed) != set(scope):
        raise ValueError("The source review package list does not match the complete current scan scope.")
    for state, amount in counts.items():
        if _count(preview.get(f"{state}_count"), f"{state} count", MAX_PACKAGES) != amount:
            raise ValueError("The source review package totals are inconsistent.")
    if (_count(preview.get("scope_package_count"), "scope count", MAX_PACKAGES) != len(scope)
            or _count(preview.get("change_count"), "total change count") != changes):
        raise ValueError("The source review scope or change total is inconsistent.")
    return indexed


def _source_members(value):
    if value is None:
        return
    if type(value) is not list or not 1 <= len(value) <= 128:
        raise ValueError("The source review has an invalid selected chart list.")
    names = set()
    for member in value:
        member = _text(member, "source chart member")
        path = member.replace("\\", "/")
        if (":" in path or any(part in {"", ".", ".."} for part in path.split("/"))
                or not path.lower().endswith(".sng") or "/songs/bin/" not in "/" + path.lower()
                or member in names):
            raise ValueError("The source review has an invalid or duplicate selected chart member.")
        names.add(member)


def _source_mode(preview):
    provenance = preview.get("provenance")
    index = preview.get("source_index")
    if provenance not in (None, "folder_search", "reused_selected_sources") or type(index) is not dict:
        raise ValueError("The source review has an unknown source-selection policy.")
    allowed = ("selected_sources",) if provenance == "reused_selected_sources" else ("folder", "folder_search")
    if index.get("scope", "folder") not in allowed:
        raise ValueError("The source review provenance disagrees with its indexed source scope.")
    return index


def _validate_ready(root, snapshot, bindings, preview, *, validator_version):
    _path(root, "library root")
    scope = _snapshot_rows(snapshot)
    if snapshot["validator_version"] != validator_version:
        raise ValueError("The saved source review uses a different validator version.")
    rows = _preview_rows(preview, scope)
    index = _source_mode(preview)
    if type(bindings) is not dict:
        raise ValueError("The saved source review has invalid source bindings.")
    expected = {name for name, row in rows.items() if row["status"] != "blocked" and row["source_path"]}
    if set(bindings) != expected:
        raise ValueError("The saved source bindings do not match the reviewed package rows.")
    for name, binding in bindings.items():
        if (type(binding) is not dict or binding.get("package") != name
                or binding.get("scan_signature") != scope[name]["scan_signature"]
                or binding.get("source_path") != rows[name]["source_path"]
                or type(binding.get("available")) is not bool
                or binding["available"] != (rows[name]["status"] == "eligible")):
            raise ValueError("A saved source binding disagrees with its reviewed package.")
        _hash(binding.get("source_sha256"), "source hash")
        _hash(binding.get("plan_id"), "package plan ID")
        if "source_members" not in binding:
            raise ValueError("A saved source binding omits its selected chart scope.")
        _source_members(binding["source_members"])
    if preview["eligible_count"] and index.get("complete") is not True:
        # Explicit prior source choices do not depend on unrelated failed sources.
        # Every saved binding still identifies a fully indexed and exact-matched
        # source; Apply recomputes its hash-bound plan before any mutation.
        partial_selected = (preview.get("provenance") == "reused_selected_sources"
            and index.get("scope") == "selected_sources" and index.get("cancelled") is False
            and index.get("limit_reached") is False
            and all(binding.get("source_indexed") is True for binding in bindings.values()))
        if not partial_selected:
            raise ValueError("A source review cannot claim eligible matches from an incomplete index.")
    _hash(preview.get("batch_plan_id"), "batch plan ID")
    unsigned = {key: value for key, value in preview.items() if key != "batch_plan_id"}
    if preview["batch_plan_id"] != _digest({"snapshot": snapshot, "root": root, "bindings": bindings, "preview": unsigned}):
        raise ValueError("The saved source review batch plan ID does not match its inputs.")


def pack_ready(root, snapshot, bindings, preview, *, catalog_version, validator_version):
    """Package a finished review; this does not grant permission to Apply it."""
    _text(catalog_version, "catalog version", limit=256)
    _text(validator_version, "validator version", limit=256)
    unsigned = {"schema": CHECKPOINT_SCHEMA, "policy_version": POLICY_VERSION,
        "catalog_version": catalog_version, "validator_version": validator_version,
        "root": root, "snapshot": snapshot, "bindings": bindings, "preview": preview}
    _wire(unsigned)
    _validate_ready(root, snapshot, bindings, preview, validator_version=validator_version)
    payload = {**unsigned, "digest": _digest(unsigned)}
    _wire(payload)
    return copy.deepcopy(payload)


def unpack_ready(payload, *, catalog_version, validator_version):
    """Check saved structure and return detached values; caller checks live files."""
    _wire(payload)
    expected = {"schema", "policy_version", "catalog_version", "validator_version",
                "root", "snapshot", "bindings", "preview", "digest"}
    if type(payload) is not dict or set(payload) != expected:
        raise ValueError("The saved source review has an invalid checkpoint envelope.")
    if (payload["schema"] != CHECKPOINT_SCHEMA or payload["policy_version"] != POLICY_VERSION
            or payload["catalog_version"] != catalog_version or payload["validator_version"] != validator_version):
        raise ValueError("The saved source review uses a different schema, catalog or policy version.")
    _hash(payload["digest"], "checkpoint digest")
    if payload["digest"] != _digest({key: value for key, value in payload.items() if key != "digest"}):
        raise ValueError("The saved source review digest does not match its contents.")
    _validate_ready(payload["root"], payload["snapshot"], payload["bindings"], payload["preview"],
                    validator_version=validator_version)
    return copy.deepcopy({key: payload[key] for key in ("root", "snapshot", "bindings", "preview")})


def parse_reuse_report(report, snapshot):
    """Extract source-choice hints only, never old plan IDs or proposed edits.

    The caller must check filesystem containment/links and freshly calculate and
    review every selected source. This does not renew folder-wide uniqueness.
    """
    report_digest = _digest(report)
    _wire(snapshot)
    scope = _snapshot_rows(snapshot)
    if (type(report) is not dict or report.get("schema") != BATCH_SCHEMA
            or report.get("phase") != "ready" or report.get("running") is not False
            or report.get("mode") != "preview"):
        raise ValueError("Reuse requires a completed, idle source-folder preview report.")
    preview = report.get("preview")
    rows = _preview_rows(preview, scope)
    if (_count(report.get("done"), "completed package count", MAX_PACKAGES) != len(scope)
            or _count(report.get("total"), "report package count", MAX_PACKAGES) != len(scope)):
        raise ValueError("The source preview report is incomplete.")
    index = _source_mode(preview)
    if (index.get("complete") is not True or index.get("cancelled") is not False
            or index.get("limit_reached") is not False
            or index.get("errors_truncated") is not False or index.get("skipped_links_truncated") is not False
            or index.get("error_count") != 0 or index.get("skipped_link_count") != 0
            or index.get("errors") != [] or index.get("skipped_links") != []
            or preview.get("source_errors") != [] or index.get("folder") != preview["source_folder"]):
        raise ValueError("Reuse requires a complete original folder index without errors or skipped links.")
    archives = _count(index.get("archive_count"), "source archive count", MAX_PACKAGES)
    indexed = _count(index.get("indexed_archive_count"), "indexed source count", MAX_PACKAGES)
    unique = _count(index.get("unique_archive_count"), "unique source count", MAX_PACKAGES)
    duplicate = _count(index.get("duplicate_archive_count"), "duplicate source count", MAX_PACKAGES)
    _count(index.get("chart_count"), "source chart count", MAX_PACKAGES * 1024)
    _count(index.get("error_count"), "source error count")
    _count(index.get("skipped_link_count"), "skipped source link count")
    if archives != indexed or unique + duplicate != indexed:
        raise ValueError("The source folder index totals are incomplete or inconsistent.")
    selections = {name: row["source_path"] for name, row in rows.items() if row["status"] == "eligible"}
    skipped = {name: {"status": row["status"], "reason": _text(row.get("reason", ""),
                "previous package reason", empty=True)}
               for name, row in rows.items() if row["status"] != "eligible"}
    return {"source_folder": preview["source_folder"], "selections": selections,
            "skipped": skipped, "report_digest": report_digest}
