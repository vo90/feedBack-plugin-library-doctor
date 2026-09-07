"""Folder-source orchestration; exact matching and all writes stay in recovery services."""
import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

SCHEMA = "library_doctor.source_recovery_batch.v1"
MAX_PACKAGES = 10000
MAX_STATE_BYTES = 32 * 1024 * 1024

_state_spec = importlib.util.spec_from_file_location(
    "_doctor_source_recovery_state", Path(__file__).with_name("source_recovery_state.py"))
_state_codec = importlib.util.module_from_spec(_state_spec)
_state_spec.loader.exec_module(_state_codec)


class SourceRecoveryBatchError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.file_state = "unchanged"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


class SourceRecoveryBatchManager:
    def __init__(self, *, config_dir, scanner, recovery, index_factory, log):
        self.scanner, self.recovery = scanner, recovery
        self.index_factory, self.log = index_factory, log
        self._lock, self._cancel = threading.RLock(), threading.Event()
        self._thread = None
        self._bindings, self._undo_bindings = {}, {}
        self._root, self._snapshot = None, None
        self._active = None
        self._storage_error = None
        self._path = Path(config_dir) / "library_doctor" / "source_recovery_batch.json"
        self._state = {"schema": SCHEMA, "phase": "idle", "running": False,
                       "mode": None, "message": "Select an original-source folder to preview.",
                       "total": 0, "done": 0, "current": None, "preview": None,
                       "result": None, "undo_preview": None, "last_result": None}
        self._load()

    def _root_key(self):
        root = self.scanner.current_repair_root()
        return str(Path(root).resolve()).casefold() if root is not None else None

    def status(self):
        with self._lock:
            state = dict(self._state)
            if state["running"]:
                # Polling progress must not resend thousands of review rows.
                for key, collection in (("preview", "packages"), ("undo_preview", "packages"),
                                        ("result", "outcomes"), ("last_result", "outcomes")):
                    if isinstance(state.get(key), dict):
                        state[key] = {k: v for k, v in state[key].items() if k != collection}
            return copy.deepcopy(state)

    def join(self, timeout=None):
        thread = self._thread
        if thread:
            thread.join(timeout)

    def _save(self):
        """Write before each mutation and after its receipt; never resume a job."""
        payload = {"schema": SCHEMA, "running": self._state["running"],
                   "mode": self._state["mode"], "root": self._root,
                   "done": self._state["done"], "total": self._state["total"],
                   "active": self._active, "result": self._state["result"],
                   "last_result": self._state["last_result"]}
        if self._state["phase"] == "ready" and not self._state["running"] and not self._active:
            payload["ready_preview"] = _state_codec.pack_ready(
                self._root, self._snapshot, self._bindings, self._state["preview"],
                catalog_version=self.recovery.module.REPAIR_CATALOG_VERSION,
                validator_version=self.recovery.repair._validator_version)
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > MAX_STATE_BYTES:
            raise SourceRecoveryBatchError("checkpoint_too_large", "The batch result exceeds its storage bound.")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self._path.parent, prefix=".source-batch-",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _load(self):
        if not self._path.exists():
            return
        try:
            if self._path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("The saved source batch exceeds its size bound.")
            saved = json.loads(self._path.read_text(encoding="utf-8"))
            if saved.get("schema") != SCHEMA:
                raise ValueError("The saved source batch uses an unknown schema.")
            self._state["last_result"] = saved.get("last_result")
            self._state["result"] = saved.get("result")
            self._root = saved.get("root")
            if not saved.get("running") and not saved.get("active"):
                if saved.get("ready_preview") is not None:
                    try:
                        ready = _state_codec.unpack_ready(saved["ready_preview"],
                            catalog_version=self.recovery.module.REPAIR_CATALOG_VERSION,
                            validator_version=self.recovery.repair._validator_version)
                        scope_matches = getattr(self.scanner, "source_recovery_scope_matches", None)
                        if self._root_key() != ready["root"] or (scope_matches and not scope_matches(ready["snapshot"])):
                            raise ValueError("The saved preview's library scan changed.")
                        self._root, self._snapshot, self._bindings = ready["root"], ready["snapshot"], ready["bindings"]
                        self._state.update(phase="ready", mode="preview", preview=ready["preview"],
                            total=ready["preview"]["scope_package_count"], done=ready["preview"]["scope_package_count"],
                            message="Saved source preview restored. Inputs are rechecked before saving repairs.")
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        self._state.update(phase="stale", message=f"Saved preview requires a new review: {exc}")
                        self.log.warning("Saved source preview was not restored: %s", exc)
                return
            result = saved.get("result") or {"outcomes": [], "mode": saved.get("mode"),
                                              "root": self._root}
            active = saved.get("active")
            if active:
                # A transaction may have committed immediately before process exit.
                # Recover its durable receipt, without issuing another mutation.
                receipt = self.recovery.repair.receipt_for_request(
                    active["request_id"], active["operation"], active["fingerprint"])
                if receipt:
                    row = self._receipt_row(active["row"], receipt, saved["mode"] == "undo")
                else:
                    row = {**active["row"], "outcome": "interrupted",
                           "message": "The operation was interrupted; review Activity and recovery before retrying."}
                result.setdefault("outcomes", []).append(row)
                if saved["mode"] == "undo" and receipt:
                    self._mark_restored(row)
            result.update(interrupted=True, cancelled=False,
                          message="The previous source batch was interrupted. No operation resumed automatically.")
            self._counts(result)
            self._state.update(phase="interrupted", mode=saved.get("mode"),
                               done=len(result["outcomes"]), total=saved.get("total", 0),
                               result=result, message=result["message"])
            if saved.get("mode") == "apply":
                self._state["last_result"] = result
            self._save()
        except Exception as exc:
            self._storage_error = str(exc)
            self._state.update(phase="failed", message="Saved source-batch recovery information cannot be read safely.")
            self.log.warning("Source batch checkpoint unavailable: %s", type(exc).__name__)

    def _launch(self, mode, phase, worker, total=0):
        if self._storage_error:
            raise SourceRecoveryBatchError("checkpoint_unavailable", self._state["message"])
        if self._state["running"]:
            raise SourceRecoveryBatchError("batch_busy", "Wait for the current source batch to finish.")
        accepted, reason = self.scanner.begin_batch_operation()
        if not accepted:
            raise SourceRecoveryBatchError("batch_busy", reason or "Another scan or repair is active.")
        scope_matches = getattr(self.scanner, "source_recovery_scope_matches", None)
        try:
            if mode in {"preview", "apply"} and scope_matches and not scope_matches(self._snapshot):
                raise SourceRecoveryBatchError("scope_changed", "The completed scan scope changed. Create a new source preview.")
        except Exception:
            self.scanner.finish_repair()
            raise
        self._cancel.clear()
        self._state.update(running=True, mode=mode, phase=phase, done=0, total=total,
                           current=None, message="Preparing source recovery.", result=None)
        self._active = None
        try:
            self._save()
            self._thread = threading.Thread(target=self._run, args=(worker,), daemon=True,
                                            name="library-doctor-source-batch")
            self._thread.start()
        except Exception:
            self._state["running"] = False
            self.scanner.finish_repair()
            raise
        return self.status()

    def _run(self, worker):
        try:
            worker()
        except Exception as exc:
            self.log.exception("Source recovery batch stopped")
            with self._lock:
                self._state.update(phase="failed", message=str(exc))
                result = self._state.get("result")
                if result is not None:
                    result.update(failed=True, message=str(exc))
                    if self._state["mode"] == "apply":
                        self._state["last_result"] = copy.deepcopy(result)
        finally:
            with self._lock:
                self._state.update(running=False, current=None)
                try:
                    self._save()
                except Exception:
                    self._storage_error = "Checkpoint write failed."
                    self._state.update(phase="failed", message="The batch stopped because its recovery checkpoint could not be saved.")
                    self.log.exception("Source recovery batch checkpoint failed")
            self.scanner.finish_repair()

    def _wait(self, phase):
        if self._cancel.is_set():
            return False
        if self.scanner.playback_active():
            with self._lock:
                self._state.update(phase="paused", message="Waiting for playback to stop.")
        if not self.scanner.wait_for_playback(self._cancel):
            return False
        with self._lock:
            self._state["phase"] = phase
        return not self._cancel.is_set()

    def _progress(self, phase, current, done, total):
        with self._lock:
            self._state.update(phase=phase, current=current, done=done, total=total,
                               message="Inspecting original sources." if phase == "indexing" else "Checking source recovery candidates.")

    def cancel(self):
        with self._lock:
            if not self._state["running"]:
                return False
            self._cancel.set()
            self._state["message"] = "Stopping after the current guarded operation."
            return True

    def invalidate_ready(self, reason):
        with self._lock:
            if self._state["running"] or self._state["phase"] not in {"ready", "undo_ready"}:
                return False
            self._bindings, self._undo_bindings = {}, {}
            self._state.update(phase="stale", preview=None, undo_preview=None, message=str(reason))
            self._save()
            return True

    def _package_current(self, row):
        return self._root_key() == self._root and self.scanner.package_matches_signature(
            row["package"], row.get("scan_signature"))

    @staticmethod
    def _row(item):
        return {"package": item["package"], "title": item.get("title") or item["package"],
                "artist": item.get("artist") or "", "source_name": "", "source_path": "",
                "change_count": 0, "excluded_count": 0, "member_count": 0,
                "status": "blocked", "reason": "No exact original source match was found."}

    def start_preview(self, snapshot, source_folder, *, reuse_report=None):
        with self._lock:
            if self._state["running"]:
                raise SourceRecoveryBatchError("batch_busy", "Wait for the current source batch to finish.")
            if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("candidates"), list)
                    or len(snapshot["candidates"]) > MAX_PACKAGES
                    or snapshot.get("scope_package_count") != len(snapshot["candidates"])):
                raise SourceRecoveryBatchError("invalid_scope", "Use a complete current scan of at most 10,000 packages.")
            rows = snapshot["candidates"]
            if any(not isinstance(row, dict) or not isinstance(row.get("package"), str)
                   or not row.get("scan_signature") for row in rows) or len({r["package"] for r in rows}) != len(rows):
                raise SourceRecoveryBatchError("invalid_scope", "The scan scope contains incomplete or duplicate package identities.")
            root = self._root_key()
            if root is None:
                raise SourceRecoveryBatchError("invalid_scope", "Select and scan a song folder first.")
            reuse = _state_codec.parse_reuse_report(reuse_report, snapshot) if reuse_report is not None else None
            if reuse is not None:
                source_folder = reuse["source_folder"]
            self._root, self._snapshot = root, copy.deepcopy(snapshot)
            self._bindings, self._undo_bindings = {}, {}
            self._state.update(preview=None, undo_preview=None)
            return self._launch("preview", "indexing", lambda: self._preview(str(source_folder), reuse=reuse), len(rows))

    def _preview(self, source_folder, *, reuse=None):
        index = self.index_factory()
        def progress(item):
            if not self._wait("indexing"):
                return False
            self._progress("indexing", item.get("current"), item.get("done", 0), item.get("total", 0))
            return True
        options = {"selected_paths": sorted(set(reuse["selections"].values()))} if reuse else {}
        summary = index.build(source_folder, cancel_event=self._cancel, on_progress=progress, **options)
        rows, work = [], []
        candidates = self._snapshot["candidates"]
        for number, item in enumerate(candidates):
            if not self._wait("previewing"):
                break
            self._progress("previewing", item["package"], number, len(candidates))
            row = self._row(item)
            rows.append(row)
            skipped = reuse["skipped"].get(item["package"]) if reuse else None
            if skipped:
                row.update(status=skipped["status"], code="previously_unproposed_not_rechecked",
                    reason="Previous preview proposed no repair; not rechecked." if skipped["status"] == "unchanged"
                    else "Not rechecked. Previous preview: " + (skipped["reason"] or "No verified source repair was available."))
                continue  # No current file/status claim and no binding or mutation offered.
            try:
                if not self._package_current(item):
                    raise SourceRecoveryBatchError("package_changed", "The package changed after the scan. Scan again.")
                inspected = self.recovery.inspect_package(item["package"])
                if not inspected["has_bend_potential"]:
                    row.update(status="unchanged", reason="No stored bend value or curve needs source inspection.")
                    continue
                if reuse and item["package"] not in reuse["selections"]:
                    row.update(code="source_choice_missing", reason="No previous source choice is available. Use a folder preview to search for this song.")
                    continue
                self.recovery.assert_available_for_apply(item["package"])
                if not summary.get("complete") and not reuse:
                    raise SourceRecoveryBatchError("source_scope_incomplete", "The source folder could not be completely indexed. Resolve the listed source errors before claiming a unique match.")
                sources = index.candidates(inspected["documents"])
                if reuse:
                    try:
                        selected = Path(reuse["selections"][item["package"]]).resolve(strict=True)
                        selected.relative_to(Path(summary["folder"]))
                    except (OSError, ValueError) as exc:
                        raise SourceRecoveryBatchError("selected_source_unavailable",
                            "The previously selected source is missing or outside its reviewed folder.") from exc
                    sources = [{**source, "path": str(selected),
                                "relative_path": selected.relative_to(Path(summary["folder"])).as_posix()}
                               for source in sources if selected in
                               {Path(path).resolve(strict=True) for path in (source["path"], *source["duplicate_paths"])}]
                    if not sources:
                        row.update(code="selected_source_unavailable", reason=
                            "The selected source could not be indexed or no longer matches this song. Review its source error or select another original.")
                        continue
                # Keep only light identities; decoded charts and package JSON are not retained.
                work.append((item, row, sources))
            except Exception as exc:
                row.update(reason=str(exc), code=getattr(exc, "code", "package_unavailable"))
        # Related audio variants share their source SHA, keeping the bounded cache useful.
        work.sort(key=lambda entry: tuple(source["sha256"] for source in entry[2]))
        for number, (item, row, sources) in enumerate(work):
            if not self._wait("previewing"):
                break
            self._progress("previewing", item["package"], number, len(work))
            matches, failures = [], []
            for source in sources:
                if not self._wait("previewing"):
                    break
                try:
                    if not self._package_current(item):
                        raise SourceRecoveryBatchError("package_changed", "The package changed after the scan. Scan again.")
                    plan = self.recovery.preview(item["package"], source["path"], source_members=source.get("members"))
                    if plan["source_sha256"] != source["sha256"]:
                        raise SourceRecoveryBatchError("source_changed", "An original source changed after indexing. Preview again.")
                    blockers = plan.get("blockers", [])
                    if not blockers:
                        matches.append((source, plan))
                        if len(matches) > 1:
                            break
                    elif any(blocker.get("code") != "source_match_missing" for blocker in blockers):
                        for blocker in blockers:
                            if blocker.get("code") != "source_match_missing":
                                failures.append({"code": blocker.get("code", "source_candidate_unavailable"),
                                    "message": f"{source['relative_path']}: {blocker.get('source_member') or blocker.get('member_path', '')}: {blocker['message']}"})
                        break
                except Exception as exc:
                    failures.append({"code": "source_candidate_unavailable", "message": str(exc)})
                    break
            if failures:
                row.update(reason="; ".join(failure["message"] for failure in failures[:3]),
                           code=failures[0]["code"])
            elif len(matches) > 1:
                row.update(reason="Multiple different original source files match this package. Select one source individually after review.", code="source_match_ambiguous")
            elif len(matches) == 1:
                source, plan = matches[0]
                row.update(source_name=plan["source_name"], source_path=source["path"],
                           change_count=plan["change_count"], excluded_count=plan.get("excluded_count", 0),
                           member_count=plan["member_count"], status="eligible" if plan["available"] else "unchanged",
                           reason=("Selected source matched; proposed chart changes validated." if reuse else
                                   "Unique exact source match; proposed chart changes validated.") if plan["available"] else
                                  "No source-proven changes are available; existing edited curves are preserved.")
                self._bindings[item["package"]] = {**item, "source_path": source["path"],
                    "source_sha256": source["sha256"], "source_members": source.get("members"),
                    "plan_id": plan["plan_id"], "available": plan["available"], "source_indexed": True}
        with self._lock:
            if self._cancel.is_set():
                self._bindings = {}
                self._state.update(phase="cancelled", preview=None, message="Preview cancelled. No song files changed.")
                return
            preview = {"source_folder": summary.get("folder", source_folder),
                "provenance": "reused_selected_sources" if reuse else "folder_search",
                "validation_scope": "arrangements",
                "skipped_count": sum(r.get("code") == "previously_unproposed_not_rechecked" for r in rows),
                "scope_package_count": len(candidates), "eligible_count": sum(r["status"] == "eligible" for r in rows),
                "blocked_count": sum(r["status"] == "blocked" for r in rows),
                "unchanged_count": sum(r["status"] == "unchanged" for r in rows),
                "change_count": sum(r["change_count"] for r in rows if r["status"] == "eligible"),
                "packages": rows, "source_errors": summary.get("errors", []), "source_index": summary}
            if reuse:
                preview["prior_report_digest"] = reuse["report_digest"]
            preview["batch_plan_id"] = _digest({"snapshot": self._snapshot, "root": self._root,
                                                "bindings": self._bindings, "preview": preview})
            self._state.update(phase="ready", done=len(candidates), total=len(candidates), preview=preview,
                               message="Source preview is ready. Review the proposed changes before applying.")

    def preview_details(self, package):
        with self._lock:
            if self._state["running"] or self._state["phase"] != "ready":
                raise SourceRecoveryBatchError("preview_unavailable", "Finish a source preview before opening details.")
            binding = copy.deepcopy(self._bindings.get(package))
            rows = self._state["preview"]["packages"]
            row = next((r for r in rows if r["package"] == package), None)
            if row is None:
                raise SourceRecoveryBatchError("package_not_in_preview", "The package is not in this source preview.")
            if binding is None:
                return {**copy.deepcopy(row), "available": False, "changes": [], "blockers": [{"message": row["reason"]}]}
            if not self._package_current(binding):
                raise SourceRecoveryBatchError("package_changed", "The package changed after preview. Scan and preview again.")
            plan = self.recovery.preview(package, binding["source_path"], source_members=binding.get("source_members"))
            if plan["plan_id"] != binding["plan_id"]:
                raise SourceRecoveryBatchError("source_changed", "The source or package changed after preview. Preview again.")
            return plan

    def start_apply(self, batch_plan_id):
        with self._lock:
            preview = self._state.get("preview")
            if (self._state["phase"] != "ready" or not preview or
                    preview["batch_plan_id"] != batch_plan_id or not preview["eligible_count"]):
                raise SourceRecoveryBatchError("stale_preview", "Create a current source preview with eligible changes first.")
            rows = [copy.deepcopy(r) for r in preview["packages"] if r["status"] == "eligible"]
            bindings = copy.deepcopy(self._bindings)
            self._state["undo_preview"] = None
            return self._launch("apply", "applying", lambda: self._apply(rows, bindings, batch_plan_id), len(rows))

    def _new_result(self, mode, plan_id, total):
        result = {"mode": mode, "plan_id": plan_id, "root": self._root, "total": total,
                  "started_at": time.time(), "outcomes": [], "success_count": 0,
                  "blocked_count": 0, "change_count": 0, "cancelled": False}
        with self._lock:
            self._state["result"] = result
        return result

    def _begin_mutation(self, row, plan_id, undo=False):
        active = {"row": row, "operation": "repair.restore" if undo else "source-recovery.apply",
                  "request_id": uuid.uuid4().hex,
                  "fingerprint": _digest({"row": row, "plan_id": plan_id, "root": self._root})}
        with self._lock:
            self._active = active
            self._save()
        return active

    @staticmethod
    def _receipt_row(row, receipt, undo=False):
        return {**row, "outcome": "restored" if undo else "success",
                "message": "Saved original song data restored." if undo else "Source bend data recovered.",
                "backup_id": receipt.get("backup_id", row.get("backup_id")),
                "change_count": receipt.get("change_count", row.get("change_count", 0))}

    def _record_receipt(self, row, receipt, undo=False):
        outcome = self._receipt_row(row, receipt, undo)
        try:
            self.scanner.record_repair_result(row["package"], receipt["report"], deep_audio=False)
            outcome["cache_updated"] = True
        except Exception:
            outcome["cache_updated"] = False
            outcome["message"] += " Scan again to refresh the Library Doctor report."
            self.log.exception("Source batch scan cache update failed")
        return outcome

    def _mutation_failure(self, row, exc, undo=False):
        if self._active:
            try:
                active = self._active
                receipt = self.recovery.repair.receipt_for_request(
                    active["request_id"], active["operation"], active["fingerprint"])
                if receipt:
                    return self._record_receipt(row, receipt, undo)
            except Exception:
                self.log.exception("Source batch receipt reconciliation failed")
        file_state = getattr(exc, "file_state", "unknown")
        return {**row, "outcome": "blocked" if file_state == "unchanged" else "needs_recovery",
                "message": str(exc), "file_state": file_state,
                "backup_id": getattr(exc, "backup_id", row.get("backup_id")),
                "code": getattr(exc, "code", "undo_failed" if undo else "source_recovery_failed")}

    @staticmethod
    def _counts(result):
        good = [row for row in result["outcomes"] if row["outcome"] in {"success", "restored"}]
        result.update(success_count=len(good), blocked_count=len(result["outcomes"]) - len(good),
                      change_count=sum(row.get("change_count", 0) for row in good))

    def _complete_item(self, result, outcome):
        with self._lock:
            result["outcomes"].append(outcome)
            good = outcome["outcome"] in {"success", "restored"}
            self._counts(result)
            self._state["done"] = len(result["outcomes"])
            if result["mode"] == "apply":
                self._state["last_result"] = copy.deepcopy(result)
            elif good:
                self._mark_restored(outcome)
            self._active = None
            self._save()

    def _finish_result(self, result, undo=False):
        with self._lock:
            cancelled = self._cancel.is_set()
            result.update(cancelled=cancelled, completed_at=time.time(),
                          remaining_count=result["total"] - len(result["outcomes"]))
            phase = ("undo_" if undo else "") + ("cancelled" if cancelled else "completed")
            self._state.update(phase=phase, message="Batch stopped; completed operations remain available in the results." if cancelled else "Batch finished. Review the per-package results.")
            if not undo:
                self._state["last_result"] = copy.deepcopy(result)

    def _apply(self, rows, bindings, plan_id):
        result = self._new_result("apply", plan_id, len(rows))
        # Retain source grouping during mutation as well as heavy preview.
        rows.sort(key=lambda row: bindings[row["package"]]["source_sha256"])
        for row in rows:
            if not self._wait("applying"):
                break
            self._progress("applying", row["package"], len(result["outcomes"]), len(rows))
            binding = bindings[row["package"]]
            try:
                if not self._package_current(binding):
                    raise SourceRecoveryBatchError("package_changed", "The package changed after preview. Scan and preview it again.")
                self.recovery.assert_available_for_apply(row["package"])
                active = self._begin_mutation(row, binding["plan_id"])
                report_lookup = getattr(self.scanner, "source_recovery_report_for_signature", None)
                verified = report_lookup(row["package"], binding["scan_signature"]) if report_lookup else None
                receipt = self.recovery.apply(row["package"], binding["source_path"], binding["plan_id"],
                    source_members=binding.get("source_members"),
                    verified_before_report=verified,
                    source_guard=lambda: self._package_current(binding),
                    operation_guard=lambda: not self._cancel.is_set() and not self.scanner.playback_active()
                        and self._package_current(binding),
                    request_id=active["request_id"], request_operation=active["operation"],
                    request_fingerprint=active["fingerprint"])
                outcome = self._record_receipt(row, receipt)
            except Exception as exc:
                outcome = self._mutation_failure(row, exc)
            self._complete_item(result, outcome)
        self._finish_result(result)

    def start_undo_preview(self):
        with self._lock:
            if self._state["running"]:
                raise SourceRecoveryBatchError("batch_busy", "Wait for the current source batch to finish.")
            result = self._state.get("last_result")
            if not result or result.get("root") != self._root_key():
                raise SourceRecoveryBatchError("undo_unavailable", "Select the same song folder used by the source batch before previewing Undo.")
            rows = [copy.deepcopy(r) for r in result.get("outcomes", [])
                    if r.get("outcome") == "success" and r.get("backup_id")]
            if not rows:
                raise SourceRecoveryBatchError("undo_unavailable", "This source batch has no retained repairs to undo.")
            self._root = result["root"]
            self._undo_bindings = {}
            self._state["undo_preview"] = None
            return self._launch("undo_preview", "undo_previewing", lambda: self._preview_undo(rows), len(rows))

    def _preview_undo(self, rows):
        packages = []
        for number, row in enumerate(rows):
            if not self._wait("undo_previewing"):
                break
            self._progress("undo_previewing", row["package"], number, len(rows))
            item = {key: row.get(key) for key in ("package", "title", "artist", "backup_id", "change_count")}
            try:
                if self._root_key() != self._root:
                    raise SourceRecoveryBatchError("scope_changed", "The selected song folder changed.")
                plan = self.recovery.repair.preview_restore(row["package"], row["backup_id"], deep_audio=False)
                if not plan.get("available"):
                    raise SourceRecoveryBatchError("undo_unavailable", "The saved repair cannot currently be restored.")
                item.update(status="eligible", reason="Saved originals and current repaired members were verified.")
                self._undo_bindings[row["package"]] = {"plan_id": plan["plan_id"], "backup_id": row["backup_id"]}
            except Exception as exc:
                item.update(status="blocked", reason=str(exc), code=getattr(exc, "code", "undo_unavailable"))
            packages.append(item)
        with self._lock:
            if self._cancel.is_set():
                self._undo_bindings = {}
                self._state.update(phase="undo_cancelled", undo_preview=None, message="Undo preview cancelled. No song files changed.")
                return
            preview = {"packages": packages, "eligible_count": sum(r["status"] == "eligible" for r in packages),
                       "blocked_count": sum(r["status"] == "blocked" for r in packages),
                       "change_count": sum(r["change_count"] or 0 for r in packages if r["status"] == "eligible")}
            preview["undo_plan_id"] = _digest({"root": self._root, "bindings": self._undo_bindings, "preview": preview})
            self._state.update(phase="undo_ready", undo_preview=preview, done=len(rows),
                               message="Undo preview is ready. Review the packages before restoring.")

    def start_undo_apply(self, undo_plan_id):
        with self._lock:
            preview = self._state.get("undo_preview")
            if (self._state["phase"] != "undo_ready" or not preview or
                    preview["undo_plan_id"] != undo_plan_id or not preview["eligible_count"]):
                raise SourceRecoveryBatchError("stale_undo_preview", "Create a current Undo preview with eligible packages first.")
            rows = [copy.deepcopy(r) for r in preview["packages"] if r["status"] == "eligible"]
            bindings = copy.deepcopy(self._undo_bindings)
            return self._launch("undo", "undoing", lambda: self._undo(rows, bindings, undo_plan_id), len(rows))

    def _mark_restored(self, outcome):
        last = self._state.get("last_result") or {}
        for row in last.get("outcomes", []):
            if row["package"] == outcome["package"] and row.get("backup_id") == outcome.get("backup_id"):
                row.update(outcome="restored", message="Restored through batch Undo.")

    def _undo(self, rows, bindings, plan_id):
        result = self._new_result("undo", plan_id, len(rows))
        for row in rows:
            if not self._wait("undoing"):
                break
            self._progress("undoing", row["package"], len(result["outcomes"]), len(rows))
            try:
                if self._root_key() != self._root:
                    raise SourceRecoveryBatchError("scope_changed", "The selected song folder changed.")
                current = self.recovery.repair.preview_restore(row["package"], row["backup_id"], deep_audio=False)
                if current["plan_id"] != bindings[row["package"]]["plan_id"]:
                    raise SourceRecoveryBatchError("stale_undo_preview", "The restore inputs changed after Undo preview. Preview again.")
                if not self._wait("undoing"):
                    break
                active = self._begin_mutation(row, current["plan_id"], undo=True)
                receipt = self.recovery.repair.restore(row["package"], row["backup_id"], deep_audio=False,
                    request_id=active["request_id"], request_fingerprint=active["fingerprint"])
                outcome = self._record_receipt(row, receipt, undo=True)
            except Exception as exc:
                outcome = self._mutation_failure(row, exc, undo=True)
            self._complete_item(result, outcome)
        self._finish_result(result, undo=True)
