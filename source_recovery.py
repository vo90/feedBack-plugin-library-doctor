"""Selected-source bend recovery composed with Doctor's existing transaction engine."""
import hashlib
import time

RULE_CODE = "source.bend-recovery"


class SourceRecovery:
    def __init__(self, *, repair, repair_module, archive, chart):
        self.repair = repair
        self.module = repair_module
        self.archive = archive
        self.chart = chart
        self.error = repair_module.RepairPlanningError

    def _plan(self, package_path, package_name, selected_source):
        try:
            source = self.archive.read_source_charts(selected_source)
        except Exception as exc:
            raise self.error("source_unavailable", str(exc)) from exc
        service, module = self.repair, self.module
        manifest_raw = service._read_member(package_path, "manifest.yaml", module.MAX_REPAIR_MANIFEST_BYTES)
        manifest = service._read_repair_manifest(package_path)
        members, changes, blocked, excluded, matches, evidence = [], [], [], [], [], {}
        evidence["manifest.yaml"] = hashlib.sha256(manifest_raw).hexdigest()
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
            candidates = []
            for entry in source["charts"]:
                try:
                    result = self.chart.recovery_patch(document, entry["song"])
                except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
                    continue
                candidates.append((entry, result))
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

    def preview(self, package, selected_source):
        service = self.repair
        with service._lock:
            _, path, name = service._resolve_package(package)
            plan = self._plan(path, name, selected_source)
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

    def apply(self, package, selected_source, plan_id, **options):
        service = self.repair
        options.setdefault("deep_audio", False)
        with service._lock:
            _, path, name = service._resolve_package(package)
            service._assert_package_mutation_allowed(name, operation="repair")
            if service._pending_recovery_for_package(name):
                raise self.error("reviewed_recovery_pending", "Undo or finalize the current repair before recovering source bends.")
            plan = self._plan(path, name, selected_source)
            if plan["plan_id"] != plan_id or not self._guard(path, plan):
                raise self.error("source_changed", "The package or original source changed after preview. Inspect again.")
            if not plan["available"]:
                raise self.error("nothing_to_repair", "No unambiguous source bend recovery is available.")
            return service._apply_internal(path, name, plan, transaction_started=time.monotonic(),
                additional_source_guard=lambda: self._guard(path, plan), **options)
