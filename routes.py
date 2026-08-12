"""FeedBack plugin routes for Library Doctor."""

from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute


class _LibraryDoctorRoute(APIRoute):
    """Keep FastAPI body/query validation inside the plugin error contract."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def uniform_route_handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                fields = sorted({
                    str(item)
                    for error in exc.errors()
                    for item in error.get("loc", ())
                    if item not in {"body", "query", "header"}
                })
                field_copy = f" Check: {', '.join(fields[:5])}." if fields else ""
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": {
                            "code": "invalid_request",
                            "message": f"The request did not match the Library Doctor contract.{field_copy}",
                            "file_state": "unchanged",
                            "retryable": False,
                            "next_action": "correct_request",
                        }
                    },
                )
            except HTTPException:
                raise
            except Exception:
                # Do not leak package paths, parser details, or database text.
                # Mutation endpoints provide more precise state where known;
                # this is the final contract boundary for read/startup faults.
                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": {
                            "code": "internal_plugin_error",
                            "message": "Library Doctor could not complete the request safely.",
                            "file_state": "unchanged",
                            "retryable": True,
                            "next_action": "retry_later",
                        }
                    },
                )

        return uniform_route_handler


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
    host_log = context.get("log")
    if not callable(load_sibling):
        raise RuntimeError("Library Doctor requires FeedBack's load_sibling plugin API.")
    if not callable(get_dlc_dir):
        raise RuntimeError("Library Doctor requires FeedBack's song-library plugin API.")
    if host_log is None:
        raise RuntimeError("Library Doctor requires FeedBack's plugin logger.")

    privacy_module = load_sibling("privacy")
    log = privacy_module.PrivacySafeLog(host_log)
    migration = load_sibling("migration")
    migration.migrate_legacy_state(Path(context["config_dir"]), log)
    validator = load_sibling("validator")
    scanner_module = load_sibling("scanner")
    scan_worker_module = load_sibling("library_doctor_scan_worker")
    repair_module = load_sibling("repair")
    preview_module = load_sibling("preview_repair")
    batch_module = load_sibling("batch_repair")
    contracts = load_sibling("api_contracts")
    receipt_module = load_sibling("mutation_receipts")
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
    mutation_receipts = receipt_module.MutationReceiptStore(
        Path(context["config_dir"]),
        log,
    )

    error_responses = {
        status: {"model": contracts.ErrorEnvelopeContract}
        for status in (400, 404, 409, 422, 500, 503)
    }
    router = APIRouter(
        prefix="/api/plugins/library_doctor",
        tags=["library_doctor"],
        route_class=_LibraryDoctorRoute,
        responses=error_responses,
    )

    def detail(
        code: str,
        message: str,
        *,
        file_state: str | None = None,
        retryable: bool = False,
        next_action: str | None = None,
    ) -> dict:
        return contracts.error_detail(
            code,
            message,
            file_state=file_state,
            retryable=retryable,
            next_action=next_action,
        )

    def http_error(
        status_code: int,
        code: str,
        message: str,
        *,
        file_state: str | None = None,
        retryable: bool = False,
        next_action: str | None = None,
    ) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail=detail(
                code,
                message,
                file_state=file_state,
                retryable=retryable,
                next_action=next_action,
            ),
        )

    @router.get("/status", response_model=contracts.StatusContract)
    def get_status():
        status = scanner.status()
        status["batch"] = batch_manager.status()
        return status

    @router.put("/playback")
    def set_playback_state(state: contracts.PlaybackStateRequestContract):
        changed = scanner.set_playback_active(state.active)
        return {"changed": changed, "status": get_status()}

    @router.post("/scan", status_code=202)
    def start_scan(
        force: bool = Query(default=False),
        target: contracts.ScanRequestContract | None = Body(default=None),
    ):
        payload = target or contracts.ScanRequestContract()
        try:
            started = scanner.start(
                force=force,
                target_kind=payload.scope,
                selected_path=payload.path,
                deep_audio=payload.deep_audio,
                max_workers=payload.max_workers,
            )
            if started:
                batch_manager.invalidate_ready(
                    "A new scan started. Review the batch again after it finishes."
                )
        except ValueError as exc:
            raise http_error(
                400,
                "invalid_scan_target",
                str(exc),
                file_state="unchanged",
                next_action="correct_request",
            ) from exc
        return {"started": started, "status": get_status()}

    @router.post("/cancel", status_code=202)
    def cancel_scan():
        accepted = scanner.cancel()
        return {"accepted": accepted, "status": get_status()}

    @router.get("/results", response_model=contracts.ResultsContract)
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
            raise http_error(
                400,
                "invalid_results_query",
                str(exc),
                file_state="unchanged",
                next_action="correct_request",
            ) from exc

    @router.get("/rules")
    def get_rules():
        return scanner.rules()

    @router.get("/repairs", response_model=contracts.RepairCatalogContract)
    def get_repairs():
        return {
            "schema": "library_doctor.repair_catalog.v1",
            "catalog_version": repair_module.REPAIR_CATALOG_VERSION,
            "items": repair_module.repair_catalog(),
            "combined": repair_module.all_safe_repair_definition(),
        }

    @router.get(
        "/reviewed-repairs",
        response_model=contracts.ReviewedRepairCatalogContract,
    )
    def get_reviewed_repairs():
        return {
            "schema": "library_doctor.reviewed_repair_catalog.v1",
            "catalog_version": repair_module.REPAIR_CATALOG_VERSION,
            "registry_version": repair_module.REVIEWED_REPAIR_REGISTRY_VERSION,
            "items": repair_module.reviewed_repair_catalog(),
        }

    @router.get("/repair/history")
    def get_repair_history(limit: int = Query(default=5, ge=1, le=20)):
        return repair_service.history(limit)

    def batch_error(exc):
        return detail(
            exc.code,
            str(exc),
            file_state="unchanged",
            retryable=exc.code in {"batch_busy", "scan_incomplete"},
            next_action=(
                "retry_later"
                if exc.code == "batch_busy"
                else "review_batch"
            ),
        )

    @router.get("/repair/batch/status")
    def get_batch_status():
        return batch_manager.status()

    @router.post("/repair/batch/preview", status_code=202)
    def preview_batch_repairs(
        payload: contracts.BatchPreviewRequestContract | None = Body(default=None),
    ):
        try:
            include_preview_repairs = bool(payload and payload.include_preview_repairs)
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
            raise http_error(
                409,
                "batch_preview_unavailable",
                str(exc),
                file_state="unchanged",
                retryable=True,
                next_action="complete_scan",
            ) from exc

    @router.post("/repair/batch/apply", status_code=202)
    def apply_batch_repairs(payload: contracts.BatchApplyRequestContract):
        try:
            return batch_manager.start_apply(payload.batch_plan_id)
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
    def apply_batch_undo(payload: contracts.BatchUndoApplyRequestContract):
        try:
            return batch_manager.start_undo_apply(payload.undo_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    @router.post("/repair/batch/finalize/preview", status_code=202)
    def preview_batch_finalization():
        try:
            return batch_manager.start_finalize_preview()
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    @router.post("/repair/batch/finalize/apply", status_code=202)
    def apply_batch_finalization(
        payload: contracts.BatchFinalizeApplyRequestContract,
    ):
        try:
            return batch_manager.start_finalize_apply(payload.finalize_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(status_code=409, detail=batch_error(exc)) from exc

    def repair_error(exc):
        retryable_codes = {
            "source_changed",
            "package_changed",
            "backup_cleanup_failed",
            "package_unreadable",
        }
        review_codes = {
            "invalid_plan",
            "source_changed",
            "nothing_to_repair",
        }
        return detail(
            exc.code,
            str(exc),
            file_state=exc.file_state,
            retryable=exc.code in retryable_codes,
            next_action=(
                "scan_again"
                if exc.code in {"source_changed", "package_changed"}
                else "review_repair"
                if exc.code in review_codes
                else "retry_later"
                if exc.code in retryable_codes
                else "inspect_package"
            ),
        )

    def receipt_error(exc):
        return detail(
            exc.code,
            str(exc),
            file_state=exc.file_state,
            retryable=exc.retryable,
            next_action=exc.next_action,
        )

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

    def begin_idempotent_mutation(
        operation: str,
        payload,
        idempotency_key: str | None,
        facts: dict,
    ) -> dict | None:
        body_request_id = getattr(payload, "request_id", None)
        if body_request_id and idempotency_key and body_request_id != idempotency_key:
            raise http_error(
                409,
                "idempotency_key_mismatch",
                "The request body and Idempotency-Key header must match.",
                file_state="unchanged",
                next_action="create_new_request",
            )
        request_id = body_request_id or idempotency_key
        if request_id is None:
            return None
        fingerprint = receipt_module.request_fingerprint(operation, facts)
        try:
            recovered = repair_service.receipt_for_request(
                request_id,
                operation,
                fingerprint,
            )
            replay = mutation_receipts.begin(
                request_id,
                operation,
                fingerprint,
                recovered_receipt=recovered,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except receipt_module.MutationReceiptError as exc:
            status_code = 404 if exc.code == "receipt_not_found" else 409
            if exc.code in {"idempotency_store_unavailable", "idempotency_store_full"}:
                status_code = 503
            raise HTTPException(status_code=status_code, detail=receipt_error(exc)) from exc
        return {
            "request_id": request_id,
            "operation": operation,
            "fingerprint": fingerprint,
            "replay": replay,
        }

    def abandon_idempotent_mutation(ticket: dict | None) -> None:
        if ticket is None or ticket.get("replay") is not None:
            return
        try:
            mutation_receipts.abandon(
                ticket["request_id"],
                ticket["operation"],
                ticket["fingerprint"],
            )
        except receipt_module.MutationReceiptError as exc:
            log.warning(
                "Library Doctor could not release a failed mutation reservation: %s",
                exc.code,
            )

    def complete_idempotent_mutation(ticket: dict | None, result: dict) -> dict:
        if ticket is None:
            return result
        if ticket.get("replay") is not None:
            return ticket["replay"]
        result["request_id"] = ticket["request_id"]
        result["idempotent_replay"] = False
        result["idempotency_persisted"] = True
        try:
            mutation_receipts.complete(
                ticket["request_id"],
                ticket["operation"],
                ticket["fingerprint"],
                result,
            )
        except receipt_module.MutationReceiptError as exc:
            # The domain mutation already completed and its service history was
            # asked to retain the same request identity. Never misreport it as
            # a failed package transaction solely because the replay ledger failed.
            log.warning(
                "Library Doctor completed a mutation but could not save its retry receipt: %s",
                exc.code,
            )
            result["idempotency_persisted"] = False
        return result

    @router.get(
        "/repair/receipt/{request_id}",
        response_model=contracts.MutationReceiptLookupContract,
    )
    def get_mutation_receipt(request_id: str):
        try:
            return mutation_receipts.lookup(request_id)
        except receipt_module.MutationReceiptError as exc:
            status_code = 404 if exc.code == "receipt_not_found" else 409
            if exc.code == "idempotency_store_unavailable":
                status_code = 503
            raise HTTPException(status_code=status_code, detail=receipt_error(exc)) from exc

    @router.post("/repair/preview")
    def preview_repair(payload: contracts.RepairPreviewRequestContract):
        try:
            return repair_service.preview(
                payload.package,
                payload.rule_code,
                start_seconds=payload.start_seconds,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/reviewed-repair/inspect")
    def inspect_reviewed_repair(payload: contracts.ReviewedInspectRequestContract):
        try:
            return repair_service.inspect_reviewed(
                payload.package,
                payload.adapter_id,
                offset=payload.offset,
                limit=payload.limit,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/reviewed-repair/preview")
    def preview_reviewed_repair(payload: contracts.ReviewedPreviewRequestContract):
        try:
            return repair_service.preview_reviewed(
                payload.package,
                payload.adapter_id,
                [decision.model_dump() for decision in payload.decisions],
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/reviewed-repair/audio")
    def generate_reviewed_passage_audio(
        payload: contracts.ReviewedAudioRequestContract,
    ):
        try:
            return repair_service.reviewed_passage(
                payload.package,
                payload.adapter_id,
                payload.candidate_id,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.get("/reviewed-repair/audio/{audio_token}")
    def get_reviewed_passage_audio(
        audio_token: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ):
        try:
            return _audio_response(
                repair_service.reviewed_passage_audio(audio_token),
                range_header,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=404, detail=repair_error(exc)) from exc

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
    def preview_all_safe_repairs(payload: contracts.AllSafePreviewRequestContract):
        try:
            return repair_service.preview_all(payload.package)
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    def apply_repair_transaction(
        package: str,
        plan_id: str,
        *,
        rule_code: str | None = None,
        all_safe: bool = False,
        reviewed_adapter_id: str | None = None,
        reviewed_decisions: list[dict] | None = None,
        ticket: dict | None = None,
    ):
        if ticket is not None and ticket.get("replay") is not None:
            return ticket["replay"]
        reserved, reason = scanner.begin_repair()
        if not reserved:
            abandon_idempotent_mutation(ticket)
            raise http_error(
                409,
                "operation_busy",
                reason,
                file_state="unchanged",
                retryable=True,
                next_action="retry_later",
            )
        try:
            status = scanner.status()
            last_scan = status.get("last_scan") if isinstance(status.get("last_scan"), dict) else {}
            deep_audio = bool(last_scan.get("deep_audio"))
            verified_options = (
                verified_deep_audio_options(package) if deep_audio else {}
            )
            if reviewed_adapter_id is not None:
                result = repair_service.apply_reviewed(
                    package,
                    reviewed_adapter_id,
                    reviewed_decisions or [],
                    plan_id,
                    deep_audio=deep_audio,
                    request_id=ticket and ticket["request_id"],
                    request_fingerprint=ticket and ticket["fingerprint"],
                    **verified_options,
                )
            elif all_safe:
                result = repair_service.apply_all(
                    package,
                    plan_id,
                    deep_audio=deep_audio,
                    request_id=ticket and ticket["request_id"],
                    request_fingerprint=ticket and ticket["fingerprint"],
                    **verified_options,
                )
            else:
                result = repair_service.apply(
                    package,
                    rule_code,
                    plan_id,
                    deep_audio=deep_audio,
                    request_id=ticket and ticket["request_id"],
                    request_fingerprint=ticket and ticket["fingerprint"],
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
            return complete_idempotent_mutation(ticket, result)
        except repair_module.RepairPlanningError as exc:
            if exc.file_state == "unchanged":
                abandon_idempotent_mutation(ticket)
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor repair failed safely: %s", exc)
            raise http_error(
                500,
                "unexpected_repair_failure",
                "The repair could not be completed. Do not assume a change was applied; scan this package again before using Repair.",
                file_state="verify_required",
                retryable=False,
                next_action="scan_again",
            ) from exc
        finally:
            scanner.finish_repair()

    @router.post(
        "/repair/apply",
        response_model=contracts.MutationReceiptContract,
    )
    def apply_repair(
        payload: contracts.RepairApplyRequestContract,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        ticket = begin_idempotent_mutation(
            "repair.apply",
            payload,
            idempotency_key,
            {
                "package": payload.package,
                "rule_code": payload.rule_code,
                "plan_id": payload.plan_id,
            },
        )
        return apply_repair_transaction(
            payload.package,
            payload.plan_id,
            rule_code=payload.rule_code,
            ticket=ticket,
        )

    @router.post(
        "/reviewed-repair/apply",
        response_model=contracts.MutationReceiptContract,
    )
    def apply_reviewed_repair(
        payload: contracts.ReviewedApplyRequestContract,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        decisions = [decision.model_dump() for decision in payload.decisions]
        ticket = begin_idempotent_mutation(
            "reviewed-repair.apply",
            payload,
            idempotency_key,
            {
                "package": payload.package,
                "adapter_id": payload.adapter_id,
                "decisions": decisions,
                "plan_id": payload.plan_id,
            },
        )
        return apply_repair_transaction(
            payload.package,
            payload.plan_id,
            reviewed_adapter_id=payload.adapter_id,
            reviewed_decisions=decisions,
            ticket=ticket,
        )

    @router.post(
        "/repair/media/automatic",
        response_model=contracts.MutationReceiptContract,
    )
    def apply_automatic_media_repair(
        payload: contracts.AutomaticPreviewRequestContract,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        ticket = begin_idempotent_mutation(
            "repair.automatic",
            payload,
            idempotency_key,
            {"package": payload.package, "rule_code": payload.rule_code},
        )
        if ticket is not None and ticket.get("replay") is not None:
            return ticket["replay"]
        reserved, reason = scanner.begin_repair()
        if not reserved:
            abandon_idempotent_mutation(ticket)
            raise http_error(
                409,
                "operation_busy",
                reason,
                file_state="unchanged",
                retryable=True,
                next_action="retry_later",
            )
        try:
            result = repair_service.apply_automatic_preview(
                payload.package,
                payload.rule_code,
                request_id=ticket and ticket["request_id"],
                request_fingerprint=ticket and ticket["fingerprint"],
                **verified_deep_audio_options(payload.package),
            )
            try:
                scanner.record_repair_result(
                    payload.package,
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
            return complete_idempotent_mutation(ticket, result)
        except repair_module.RepairPlanningError as exc:
            if exc.file_state == "unchanged":
                abandon_idempotent_mutation(ticket)
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor automatic preview repair failed safely: %s", exc)
            raise http_error(
                500,
                "unexpected_preview_repair_failure",
                "The preview could not be created automatically. Scan this Feedpak again before retrying.",
                file_state="verify_required",
                retryable=False,
                next_action="scan_again",
            ) from exc
        finally:
            scanner.finish_repair()
    @router.post(
        "/repair/all/apply",
        response_model=contracts.MutationReceiptContract,
    )
    def apply_all_safe_repairs(
        payload: contracts.AllSafeApplyRequestContract,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        ticket = begin_idempotent_mutation(
            "repair.all.apply",
            payload,
            idempotency_key,
            {"package": payload.package, "plan_id": payload.plan_id},
        )
        return apply_repair_transaction(
            payload.package,
            payload.plan_id,
            all_safe=True,
            ticket=ticket,
        )

    @router.post(
        "/repair/restore",
        response_model=contracts.MutationReceiptContract,
    )
    def restore_repair(
        payload: contracts.RecoveryMutationRequestContract,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        ticket = begin_idempotent_mutation(
            "repair.restore",
            payload,
            idempotency_key,
            {"package": payload.package, "backup_id": payload.backup_id},
        )
        if ticket is not None and ticket.get("replay") is not None:
            return ticket["replay"]
        reserved, reason = scanner.begin_repair()
        if not reserved:
            abandon_idempotent_mutation(ticket)
            raise http_error(
                409,
                "operation_busy",
                reason,
                file_state="unchanged",
                retryable=True,
                next_action="retry_later",
            )
        try:
            status = scanner.status()
            last_scan = status.get("last_scan") if isinstance(status.get("last_scan"), dict) else {}
            deep_audio = bool(last_scan.get("deep_audio"))
            result = repair_service.restore(
                payload.package,
                payload.backup_id,
                deep_audio=deep_audio,
                request_id=ticket and ticket["request_id"],
                request_fingerprint=ticket and ticket["fingerprint"],
            )
            try:
                scanner.record_repair_result(
                    payload.package,
                    result["report"],
                    deep_audio=deep_audio,
                )
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
                payload.package,
                payload.backup_id,
                cache_updated=result["cache_updated"],
            )
            return complete_idempotent_mutation(ticket, result)
        except repair_module.RepairPlanningError as exc:
            if exc.file_state == "unchanged":
                abandon_idempotent_mutation(ticket)
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor recovery failed: %s", exc)
            raise http_error(
                500,
                "unexpected_restore_failure",
                "Recovery could not be completed. Scan the package again before making another change.",
                file_state="verify_required",
                retryable=False,
                next_action="scan_again",
            ) from exc
        finally:
            scanner.finish_repair()

    @router.post(
        "/repair/recovery/finalize",
        response_model=contracts.MutationReceiptContract,
    )
    def finalize_recovery(
        payload: contracts.RecoveryMutationRequestContract,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        ticket = begin_idempotent_mutation(
            "repair.finalize",
            payload,
            idempotency_key,
            {"package": payload.package, "backup_id": payload.backup_id},
        )
        if ticket is not None and ticket.get("replay") is not None:
            return ticket["replay"]
        reserved, reason = scanner.begin_repair()
        if not reserved:
            abandon_idempotent_mutation(ticket)
            raise http_error(
                409,
                "operation_busy",
                reason,
                file_state="unchanged",
                retryable=True,
                next_action="retry_later",
            )
        try:
            result = repair_service.finalize_backup(
                payload.package,
                payload.backup_id,
                request_id=ticket and ticket["request_id"],
                request_fingerprint=ticket and ticket["fingerprint"],
            )
            batch_manager.invalidate_ready(
                "A recovery copy changed after the batch recovery review. Review the operation again."
            )
            batch_manager.mark_finalized(
                payload.package,
                payload.backup_id,
                package_state=result.get("package_state"),
            )
            return complete_idempotent_mutation(ticket, result)
        except repair_module.RepairPlanningError as exc:
            if exc.file_state == "unchanged":
                abandon_idempotent_mutation(ticket)
            raise HTTPException(status_code=409, detail=repair_error(exc)) from exc
        except Exception as exc:
            log.exception("Library Doctor recovery finalization failed safely: %s", exc)
            raise http_error(
                500,
                "unexpected_recovery_cleanup_failure",
                "The recovery copy could not be removed. The Feedpak was not changed.",
                file_state="unchanged",
                retryable=True,
                next_action="check_receipt",
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
            raise http_error(
                400,
                "invalid_export_request",
                str(exc),
                file_state="unchanged",
                next_action="correct_request",
            ) from exc
        return StreamingResponse(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.include_router(router)
