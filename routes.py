"""FeedBack plugin routes for Library Doctor."""

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse


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
    repair_module = load_sibling("repair")
    batch_module = load_sibling("batch_repair")
    scanner = scanner_module.LibraryScanner(
        config_dir=Path(context["config_dir"]),
        get_dlc_dir=get_dlc_dir,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=log,
        rule_metadata=validator.rule_metadata,
    )
    repair_service = repair_module.RepairService(
        config_dir=Path(context["config_dir"]),
        get_dlc_dir=get_dlc_dir,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=log,
        legacy_schemas=migration.LEGACY_SCHEMAS,
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
    def preview_batch_repairs():
        try:
            snapshot = scanner.repair_scope_snapshot(
                item["rule_code"] for item in repair_module.repair_catalog()
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

    def repair_error(exc):
        return {
            "code": exc.code,
            "message": str(exc),
            "file_state": exc.file_state,
        }

    @router.post("/repair/preview")
    def preview_repair(payload: dict = Body(...)):
        package = payload.get("package")
        rule_code = payload.get("rule_code")
        if not isinstance(package, str) or not isinstance(rule_code, str):
            raise HTTPException(status_code=400, detail="Choose a package finding to repair.")
        try:
            return repair_service.preview(package, rule_code)
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
            if all_safe:
                result = repair_service.apply_all(
                    package,
                    plan_id,
                    deep_audio=deep_audio,
                )
            else:
                result = repair_service.apply(
                    package,
                    rule_code,
                    plan_id,
                    deep_audio=deep_audio,
                )
            try:
                scanner.record_repair_result(
                    package,
                    result["report"],
                    deep_audio=deep_audio,
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
