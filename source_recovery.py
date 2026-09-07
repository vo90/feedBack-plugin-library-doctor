"""Selected-source bend recovery composed with Doctor's existing transaction engine."""
import hashlib
import math
import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock

RULE_CODE = "source.bend-recovery"


class SourceRecovery:
    def __init__(self, *, repair, repair_module, archive, chart):
        self.repair = repair
        self.module = repair_module
        self.archive = archive
        self.chart = chart
        self.error = repair_module.RepairPlanningError
        self._source_cache = OrderedDict()
        self._source_cache_lock = RLock()

    def read_source_charts(self, selected_source, *, source_members=None):
        """Share at most two decoded archives, while checking their current bytes."""
        validate_path = getattr(self.archive, "source_path", Path)
        path = validate_path(selected_source)
        with self._source_cache_lock:
            digest = self.archive.source_hash(path)
            selected = tuple(sorted(set(source_members))) if source_members is not None else None
            key = (digest, selected)
            source = self._source_cache.get(key)
            if source is None:
                source = (self.archive.read_source_charts(path, members=selected) if selected is not None
                          else self.archive.read_source_charts(path))
                if source["sha256"] != digest:
                    raise self.error("source_changed", "The original source changed while it was read.")
                # Expanded bytes are only a lower bound on parsed Python objects.
                # Large compilations and broad selections are never retained.
                if len(source["charts"]) <= 8 and source.get("expanded_bytes", 0) <= 16 * 1024 * 1024:
                    self._source_cache[key] = source
                    while len(self._source_cache) > 2:
                        self._source_cache.popitem(last=False)
            if key in self._source_cache:
                self._source_cache.move_to_end(key)
            return {**source, "path": path}

    @staticmethod
    def _bend_potential(value):
        if isinstance(value, list):
            return any(SourceRecovery._bend_potential(item) for item in value)
        if not isinstance(value, dict):
            return False
        bend = value.get("bn")
        if (isinstance(bend, (int, float)) and not isinstance(bend, bool)
                and math.isfinite(bend) and bend > 0) or value.get("bnv"):
            return True
        return any(SourceRecovery._bend_potential(value.get(key))
                   for key in ("notes", "chords", "phrases", "levels"))

    def inspect_package(self, package):
        """Read arrangement topology cheaply, without decoding any original source."""
        with self.repair._lock:
            _, path, name = self.repair._resolve_package(package)
            documents, evidence = self._documents(path)
            return {"package": name, "documents": [row[2] for row in documents],
                    "evidence": evidence,
                    "has_bend_potential": any(self._bend_potential(row[2]) for row in documents)}

    def assert_available_for_apply(self, package):
        with self.repair._lock:
            _, _, name = self.repair._resolve_package(package)
            self.repair._assert_package_mutation_allowed(name, operation="repair")
            if self.repair._pending_recovery_for_package(name):
                raise self.error("reviewed_recovery_pending", "Finalize the earlier repair in Activity and recovery (or Undo it), then preview again.")

    def _documents(self, package_path):
        service, module = self.repair, self.module
        manifest_raw = service._read_member(package_path, "manifest.yaml", module.MAX_REPAIR_MANIFEST_BYTES)
        manifest = service._read_repair_manifest(package_path)
        evidence = {"manifest.yaml": hashlib.sha256(manifest_raw).hexdigest()}
        documents = []
        declarations = manifest.get("arrangements")
        if not isinstance(declarations, list) or not all(isinstance(e, dict) for e in declarations):
            raise self.error("invalid_source_arrangement", "Every arrangement must have an explicit valid declaration before source recovery.")
        pitched = set()
        for entry in declarations:
            if str(entry.get("type", "")).strip().lower() in {"vocal", "vocals", "drum", "drums", "lyrics"}:
                continue
            pitched.add(module._validate_member_path(entry.get("file")))
        resolved = service._resolved_repair_member_paths(package_path, manifest, "arrangement")
        if not pitched.issubset(set(resolved)):
            raise self.error("unresolved_source_arrangement", "Every playable arrangement must resolve to its original stored JSON file before source recovery.")
        for member in resolved:
            if member not in pitched:
                continue
            if not member.lower().endswith(".json"):
                raise self.error("unsupported_text_format", "Source recovery requires ordinary JSON arrangement files.")
            raw = service._read_member(package_path, member, module.MAX_REPAIR_TEXT_BYTES)
            evidence[member] = hashlib.sha256(raw).hexdigest()
            document = module._parse_json(raw)
            module._inspect_structure(document)
            documents.append((member, raw, document))
        return documents, evidence

    def _plan(self, package_path, package_name, selected_source, *, source_members=None):
        try:
            source = self.read_source_charts(selected_source, source_members=source_members)
        except Exception as exc:
            raise self.error("source_unavailable", str(exc)) from exc
        service, module = self.repair, self.module
        documents, evidence = self._documents(package_path)
        members, changes, blocked, excluded, matches = [], [], [], [], []
        for member, raw, document in documents:
            candidates, invalid_curves = [], []
            for entry in source["charts"]:
                try:
                    result = self.chart.recovery_patch(document, entry["song"])
                except self.chart.SourceBendError as exc:
                    # Exact chart correspondence was established before bend
                    # interpretation. Keep this candidate even if another
                    # matching source chart has a usable curve.
                    invalid_curves.append({"member_path": member, "source_member": entry["member"],
                        "code": "source_bend_invalid", "message": str(exc)})
                    continue
                except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
                    continue
                candidates.append((entry, result))
            if invalid_curves:
                blocked.extend(invalid_curves)
                continue
            if len(candidates) != 1:
                blocked.append({"member_path": member, "code": "source_match_ambiguous" if candidates else "source_match_missing",
                    "message": "Exactly one source arrangement must match all stored events, techniques, tuning and difficulty copies."})
                continue
            entry, (repaired, edits, occurrence_exclusions) = candidates[0]
            matches.append({"member_path": member, "source_member": entry["member"], "source_chart_sha256": entry["sha256"]})
            excluded.extend({"member_path": member, "code": "source_event_requires_review",
                            "message": item["reason"], "path": item["path"]} for item in occurrence_exclusions)
            changes.extend({"member_path": member, **edit} for edit in edits)
            if edits:
                replacement = module._render_json(repaired, raw)
                members.append({"member_path": member, "raw": raw, "replacement": replacement,
                                "source_kind": "arrangement"})
        if len(changes) > 10000:
            raise self.error("source_recovery_too_large", "This source recovery exceeds the 10,000 changed-note preview bound.")
        # Mixed or unmatched copies never cause a partial package rewrite.
        unsigned = {"schema": module.PACKAGE_REPAIR_SCHEMA, "catalog_version": module.REPAIR_CATALOG_VERSION,
            "validator_version": service._validator_version, "package": package_name,
            "rule_code": RULE_CODE, "source_sha256": source["sha256"], "source_name": source["path"].name,
            "evidence": evidence, "matches": matches, "changes": changes, "blockers": blocked,
            "excluded_count": len(excluded), "excluded": excluded[:500]}
        internal = {**unsigned, "plan_id": module._digest_json(unsigned), "available": bool(changes) and not blocked,
            "title": "Recover bend trajectories from original source", "safety": "review_required",
            "description": "Restore only source-proven bend fields across every matching stored difficulty. Review the exact before/after values and any source boundary adjustments.",
            "player_result": "Bends follow the selected original's retained timing and pitch trajectory.",
            "user_value": "Restore discarded timing without reconstructing it from a scalar bend value.",
            "file_handling": service._file_handling(None), "item_name": "source bend trajectory",
            "change_kind": "normalize_values", "change_count": len(changes), "removed_count": 0,
            "musical_positions": len(changes), "member_count": len(members), "arrays_affected": len(members),
            "_members": members, "_verification": {"mode": "reviewed"},
            "_source_path": source["path"]}
        return internal

    def _guard(self, package_path, plan):
        try:
            if self.archive.source_hash(plan["_source_path"]) != plan["source_sha256"]:
                return False
            return all(hashlib.sha256(self.repair._read_member(package_path, member,
                self.module.MAX_REPAIR_TEXT_BYTES)).hexdigest() == digest for member, digest in plan["evidence"].items())
        except (OSError, ValueError, self.error):
            return False

    def preview(self, package, selected_source, *, source_members=None):
        service = self.repair
        with service._lock:
            _, path, name = service._resolve_package(package)
            plan = self._plan(path, name, selected_source, source_members=source_members)
            if plan["available"]:
                replacements = {m["member_path"]: m["replacement"] for m in plan["_members"]}
                before = service._validate_feedpak(path, name, deep_audio=False)
                candidate, cleanup = service._candidate(path, replacements)
                try:
                    after = service._validate_feedpak(candidate, name, deep_audio=False)
                    service._verify_reviewed_validation(before, after)
                    if not self._guard(path, plan):
                        raise self.error("source_changed", "A source input changed during preview. Inspect again.")
                finally:
                    cleanup()
                plan["candidate_validated"] = True
            return service._public_plan(plan)

    def apply(self, package, selected_source, plan_id, *, operation_guard=None, source_members=None, **options):
        service = self.repair
        options.setdefault("deep_audio", False)
        with service._lock:
            _, path, name = service._resolve_package(package)
            service._assert_package_mutation_allowed(name, operation="repair")
            if service._pending_recovery_for_package(name):
                raise self.error("reviewed_recovery_pending", "Undo or finalize the current repair before recovering source bends.")
            plan = self._plan(path, name, selected_source, source_members=source_members)
            if plan["plan_id"] != plan_id or not self._guard(path, plan):
                raise self.error("source_changed", "The package or original source changed after preview. Inspect again.")
            if not plan["available"]:
                raise self.error("nothing_to_repair", "No unambiguous source bend recovery is available.")
            return service._apply_internal(path, name, plan, transaction_started=time.monotonic(),
                additional_source_guard=lambda: self._guard(path, plan) and
                    (operation_guard is None or operation_guard()), **options)
