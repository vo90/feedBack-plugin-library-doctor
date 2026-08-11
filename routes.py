"""FeedBack plugin routes for Library Doctor."""

from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import Response, StreamingResponse


def _audio_response(content: bytes, range_header: str | None = None) -> Response:
    """Return in-memory Ogg audio with the single byte ranges browsers require."""
    total = len(content)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    if not range_header:
        return Response(content=content, media_type="audio/ogg", headers=headers)

    def unsatisfied() -> Response:
        return Response(
            content=b"",
            status_code=416,
            media_type="audio/ogg",
            headers={**headers, "Content-Range": f"bytes */{total}"},
        )

    if total == 0 or not range_header.startswith("bytes="):
        return unsatisfied()
    requested = range_header[6:].strip()
    if not requested or "," in requested:
        return unsatisfied()
    first, separator, last = requested.partition("-")
    if not separator or (not first and not last):
        return unsatisfied()
    try:
        if first:
            start = int(first)
            end = int(last) if last else total - 1
            if start < 0 or end < start or start >= total:
                return unsatisfied()
            end = min(end, total - 1)
        else:
            suffix_length = int(last)
            if suffix_length <= 0:
                return unsatisfied()
            start = max(0, total - suffix_length)
            end = total - 1
    except ValueError:
        return unsatisfied()

    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(
        content=content[start : end + 1],
        status_code=206,
        media_type="audio/ogg",
        headers=headers,
    )


def setup(app, context):
    load_sibling = context.get("load_sibling")
    get_dlc_dir = context.get("get_dlc_dir")
    log = context.get("log")
    if not callable(load_sibling):
        raise RuntimeError("Library Doctor requires FeedBack's load_sibling plugin API.")
    if not callable(get_dlc_dir):
        raise RuntimeError("Library Doctor requires FeedBack's song-library plugin API.")
    if log is None:
        raise RuntimeError("Library Doctor requires FeedBack's plugin logger.")

    migration = load_sibling("migration")
    migration.migrate_legacy_state(Path(context["config_dir"]), log)
    validator = load_sibling("validator")
    scanner_module = load_sibling("scanner")
    scan_worker_module = load_sibling("library_doctor_scan_worker")
    repair_module = load_sibling("repair")
    preview_module = load_sibling("preview_repair")
    batch_module = load_sibling("batch_repair")
    scanner = scanner_module.LibraryScanner(
        config_dir=Path(context["config_dir"]),
        get_dlc_dir=get_dlc_dir,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=log,
        rule_metadata=validator.rule_metadata,
        worker_pool_factory=lambda max_workers, validator_version: (
            scan_worker_module.ValidationProcessPool(
                max_workers=max_workers,
                validator_version=validator_version,
            )
        ),
    )
    preview_repair = preview_module.PreviewRepairEngine(
        validate_feedpak=validator.validate_feedpak,
        error_type=repair_module.RepairPlanningError,
        log=log,
        probe_duration=validator.probe_ogg_duration,
    )
    repair_service = repair_module.RepairService(
        config_dir=Path(context["config_dir"]),
        get_dlc_dir=get_dlc_dir,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=log,
        legacy_schemas=migration.LEGACY_SCHEMAS,
        preview_repair=preview_repair,
    )
    batch_manager = batch_module.BatchRepairManager(
        config_dir=Path(context["config_dir"]),
        scanner=scanner,
        repair_service=repair_service,
        repair_error_type=repair_module.RepairPlanningError,
        log=log,
        legacy_schemas=migration.LEGACY_SCHEMAS,
    )

    router = APIRouter(prefix="/api/plugins/library_doctor", tags=["library_doctor"])

    @router.get("/status")
    def get_status():
        status = scanner.status()
        status["batch"] = batch_manager.status()
        return status

    @router.put("/playback")
    def set_playback_state(state: dict = Body(...)):
        active = state.get("active")
        if not isinstance(active, bool):
            raise HTTPException(status_code=400, detail="Playback active must be true or false.")
        changed = scanner.set_playback_active(active)
        return {"changed": changed, "status": get_status()}

    @router.post("/scan", status_code=202)
    def start_scan(
        force: bool = Query(default=False),
        target: dict | None = Body(default=None),
    ):
        payload = target or {}
        try:
            started = scanner.start(
                force=force,
                target_kind=payload.get("scope", "library"),
                selected_path=payload.get("path"),
                deep_audio=payload.get("deep_audio") is True,
                max_workers=payload.get("max_workers"),
            )
            if started:
                batch_manager.invalidate_ready(
                    "A new scan started. Review the batch again after it finishes."
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"started": started, "status": get_status()}

    @router.post("/cancel", status_code=202)
    def cancel_scan():
        accepted = scanner.cancel()
        return {"accepted": accepted, "status": get_status()}

    @router.get("/results")
    def get_results(
        result_filter: str = Query(default="all", alias="filter"),
        query: str = Query(default="", max_length=200),
        rule: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            return scanner.results(
                result_filter=result_filter,
                query=query,
                rule_code=rule,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/rules")
    def get_rules():
        return scanner.rules()

    @router.get("/repairs")
    def get_repairs():
        return {
            "schema": "library_doctor.repair_catalog.v1",
            "items": repair_module.repair_catalog(),
            "combined": repair_module.all_safe_repair_definition(),
        }

    @router.get("/repair/history")
    def get_repair_history(limit: int = Query(default=5, ge=1, le=20)):
        return repair_service.history(limit)

    def batch_error(exc):
        return {"code": exc.code, "message": str(exc)}

    @router.get("/repair/batch/status")
    def get_batch_status():
        return batch_manager.status()

    @router.post("/repair/batch/preview", status_code=202)
    def preview_batch_repairs(payload: dict | None = Body(default=None)):
        try:
            include_preview_repairs = bool(
                isinstance(payload, dict)
                and payload.get("include_preview_repairs") is True
            )
            snapshot = scanner.repair_scope_snapshot(
                tuple(
                    item["rule_code"]
                    for item in repair_module.repair_catalog()
                    if item.get("safety") == "safe_automatic"
                ),
                preview_rule_codes=(
                    preview_module.PREVIEW_RULE_CODES - {"media.preview-regenerate"}
                    if include_preview_repairs else ()
                ),
            )
            return batch_manager.start_preview(snapshot)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/repair/batch/apply", status_code=202)
    def apply_batch_repairs(payload: dict = Body(...)):
        batch_plan_id = payload.get("batch_plan_id")
        if not isinstance(batch_plan_id, str):
            raise HTTPException(
                status_code=400,
                detail="Review the batch before applying it.",
            )
        try:
            return batch_manager.start_apply(batch_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    @router.post("/repair/batch/cancel", status_code=202)
    def cancel_batch_repairs():
        return {
            "accepted": batch_manager.cancel(),
            "status": batch_manager.status(),
        }

    @router.post("/repair/batch/undo/preview", status_code=202)
    def preview_batch_undo():
        try:
            return batch_manager.start_undo_preview()
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    @router.post("/repair/batch/undo/apply", status_code=202)
    def apply_batch_undo(payload: dict = Body(...)):
        undo_plan_id = payload.get("undo_plan_id")
        if not isinstance(undo_plan_id, str):
            raise HTTPException(
                status_code=400,
                detail="Review Undo all before applying it.",
            )
        try:
            return batch_manager.start_undo_apply(undo_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    @router.post("/repair/batch/finalize/preview", status_code=202)
    def preview_batch_finalization():
        try:
            return batch_manager.start_finalize_preview()
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    @router.post("/repair/batch/finalize/apply", status_code=202)
    def apply_batch_finalization(payload: dict = Body(...)):
        finalize_plan_id = payload.get("finalize_plan_id")
        if not isinstance(finalize_plan_id, str):
            raise HTTPException(
                status_code=400,
                detail="Review Finalize all before applying it.",
            )
        try:
            return batch_manager.start_finalize_apply(finalize_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    def repair_error(exc):
        return {
            "code": exc.code,
            "message": str(exc),
            "file_state": exc.file_state,
        }

    def verified_deep_audio_options(package: str) -> dict:
        """Build service arguments that remain guarded through commit."""
        context = scanner.current_deep_audio_repair_context(package)
        if not isinstance(context, dict):
            return {}
        signature = context.get("signature")
        report = context.get("report")
        if not isinstance(signature, str) or not isinstance(report, dict):
            return {}
        return {
            "verified_before_report": report,
            "source_guard": lambda: scanner.package_matches_signature(
                package, signature
            ),
        }

    @router.post("/repair/preview")
    def preview_repair(payload: dict = Body(...)):
        package = payload.get("package")
        rule_code = payload.get("rule_code")
        if not isinstance(package, str) or not isinstance(rule_code, str):
            raise HTTPException(status_code=400, detail="Choose a package finding to repair.")
        try:
            return repair_service.preview(
                package,
                rule_code,
                start_seconds=payload.get("start_seconds"),
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.get("/repair/media/candidate/{plan_id}")
    def get_media_repair_candidate(
        plan_id: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ):
        try:
            return _audio_response(
                repair_service.preview_audio(plan_id),
                range_header,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=404, detail=repair_error(exc)) from exc

    @router.get("/repair/media/current")
    def get_current_media_preview(
        package: str = Query(...),
        range_header: str | None = Header(default=None, alias="Range"),
    ):
        try:
            return _audio_response(
                repair_service.current_preview_audio(package),
                range_header,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=404, detail=repair_error(exc)) from exc

    @router.get("/repair/media/tool/status")
    def get_preview_tool_status(package: str = Query(...)):
        try:
            return repair_service.preview_tool_status(package)
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/repair/all/preview")
    def preview_all_safe_repairs(payload: dict = Body(...)):
        package = payload.get("package")
        if not isinstance(package, str):
            raise HTTPException(status_code=400, detail="Choose a package to repair.")
        try:
            return repair_service.preview_all(package)
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    def apply_repair_transaction(
        package: str,
        plan_id: str,
        *,
        rule_code: str | None = None,
        all_safe: bool = False,
    ):
        reserved, reason = scanner.begin_repair()
        if not reserved:
            raise HTTPException(status_code=409, detail=reason)
        try:
            status = scanner.status()
            last_scan = status.get("last_scan") if isinstance(status.get("last_scan"), dict) else {}
            deep_audio = bool(last_scan.get("deep_audio"))
            verified_options = (
                verified_deep_audio_options(package) if deep_audio else {}
            )
            if all_safe:
                result = repair_service.apply_all(
                    package,
                    plan_id,
                    deep_audio=deep_audio,
                    **verified_options,
                )
            else:
                result = repair_service.apply(
                    package,
                    rule_code,
                    plan_id,
                    deep_audio=deep_audio,
                    **verified_options,
                )
            try:
                result_deep_audio = bool(result.get("deep_audio", deep_audio))
                scanner.record_repair_result(
                    package,
                    result["report"],
                    deep_audio=result_deep_audio,
                )
                result["cache_updated"] = True
            except Exception as exc:  # The package repair itself already succeeded.
                log.warning(
                    "Library Doctor repaired a package but could not refresh its report: %s",
                    exc,
                )
                result["cache_updated"] = False
            batch_manager.invalidate_ready(
                "A Feedpak changed after the batch preview. Review the batch again."
            )
            return result
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor repair failed safely: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "unexpected_repair_failure",
                    "message": (
                        "The repair could not be completed. Do not assume a change was applied; "
                        "scan this package again before using Repair."
                    ),
                    "file_state": "verify_required",
                },
            ) from exc
        finally:
            scanner.finish_repair()

    @router.post("/repair/apply")
    def apply_repair(payload: dict = Body(...)):
        package = payload.get("package")
        rule_code = payload.get("rule_code")
        plan_id = payload.get("plan_id")
        if not all(isinstance(value, str) for value in (package, rule_code, plan_id)):
            raise HTTPException(status_code=400, detail="Review the safe fix before applying it.")
        return apply_repair_transaction(
            package,
            plan_id,
            rule_code=rule_code,
        )

    @router.post("/repair/media/automatic")
    def apply_automatic_media_repair(payload: dict = Body(...)):
        package = payload.get("package")
        rule_code = payload.get("rule_code")
        if not isinstance(package, str) or not isinstance(rule_code, str):
            raise HTTPException(
                status_code=400,
                detail="Choose a preview recommendation to repair automatically.",
            )
        reserved, reason = scanner.begin_repair()
        if not reserved:
            raise HTTPException(status_code=409, detail=reason)
        try:
            result = repair_service.apply_automatic_preview(
                package,
                rule_code,
                **verified_deep_audio_options(package),
            )
            try:
                scanner.record_repair_result(
                    package,
                    result["report"],
                    deep_audio=True,
                )
                result["cache_updated"] = True
            except Exception as exc:
                log.warning(
                    "Library Doctor created a preview but could not refresh its report: %s",
                    exc,
                )
                result["cache_updated"] = False
            batch_manager.invalidate_ready(
                "A Feedpak changed after the batch preview. Review the batch again."
            )
            return result
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor automatic preview repair failed safely: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "unexpected_preview_repair_failure",
                    "message": (
                        "The preview could not be created automatically. Scan this Feedpak again before retrying."
                    ),
                    "file_state": "verify_required",
                },
            ) from exc
        finally:
            scanner.finish_repair()

    @router.post("/repair/all/apply")
    def apply_all_safe_repairs(payload: dict = Body(...)):
        package = payload.get("package")
        plan_id = payload.get("plan_id")
        if not all(isinstance(value, str) for value in (package, plan_id)):
            raise HTTPException(
                status_code=400,
                detail="Review all safe fixes before applying them.",
            )
        return apply_repair_transaction(package, plan_id, all_safe=True)

    @router.post("/repair/restore")
    def restore_repair(payload: dict = Body(...)):
        package = payload.get("package")
        backup_id = payload.get("backup_id")
        if not isinstance(package, str) or not isinstance(backup_id, str):
            raise HTTPException(status_code=400, detail="Choose a repair receipt to restore.")
        reserved, reason = scanner.begin_repair()
        if not reserved:
            raise HTTPException(status_code=409, detail=reason)
        try:
            status = scanner.status()
            last_scan = status.get("last_scan") if isinstance(status.get("last_scan"), dict) else {}
            deep_audio = bool(last_scan.get("deep_audio"))
            result = repair_service.restore(package, backup_id, deep_audio=deep_audio)
            try:
                scanner.record_repair_result(package, result["report"], deep_audio=deep_audio)
                result["cache_updated"] = True
            except Exception as exc:
                log.warning(
                    "Library Doctor restored a package but could not refresh its report: %s",
                    exc,
                )
                result["cache_updated"] = False
            batch_manager.invalidate_ready(
                "A Feedpak changed after the batch preview. Review the batch again."
            )
            batch_manager.mark_restored(
                package,
                backup_id,
                cache_updated=result["cache_updated"],
            )
            return result
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor recovery failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "unexpected_restore_failure",
                    "message": (
                        "Recovery could not be completed. Scan the package again before making another change."
                    ),
                    "file_state": "verify_required",
                },
            ) from exc
        finally:
            scanner.finish_repair()

    @router.post("/repair/recovery/finalize")
    def finalize_recovery(payload: dict = Body(...)):
        package = payload.get("package")
        backup_id = payload.get("backup_id")
        if not isinstance(package, str) or not isinstance(backup_id, str):
            raise HTTPException(
                status_code=400,
                detail="Choose a repair recovery copy to finalize.",
            )
        try:
            result = repair_service.finalize_backup(package, backup_id)
            batch_manager.invalidate_ready(
                "A recovery copy changed after the batch recovery review. Review the operation again."
            )
            batch_manager.mark_finalized(
                package,
                backup_id,
                package_state=result.get("package_state"),
            )
            return result
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor recovery finalization failed safely: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "unexpected_recovery_cleanup_failure",
                    "message": (
                        "The recovery copy could not be removed. The Feedpak was not changed."
                    ),
                    "file_state": "unchanged",
                },
            ) from exc

    @router.get("/export")
    def export_results(
        export_format: str = Query(alias="format"),
        result_filter: str = Query(default="all", alias="filter"),
        query: str = Query(default="", max_length=200),
        rule: str = Query(default="", max_length=200),
    ):
        try:
            filename, media_type, content = scanner.export_stream(
                export_format=export_format,
                result_filter=result_filter,
                query=query,
                rule_code=rule,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.include_router(router)
