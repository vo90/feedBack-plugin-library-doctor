"""FeedBack plugin routes for Library Doctor."""

from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import StreamingResponse


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
    route_support = load_sibling("route_support")
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

    def get_repair_dlc_dir():
        root = scanner.current_repair_root()
        if root is None:
            raise repair_module.RepairPlanningError(
                "repair_scope_unavailable",
                "The selected scan target is unavailable. Scan that folder or package "
                "again before using repairs.",
            )
        return root

    repair_service = repair_module.RepairService(
        config_dir=Path(context["config_dir"]),
        get_dlc_dir=get_repair_dlc_dir,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=log,
        legacy_schemas=migration.LEGACY_SCHEMAS,
        preview_repair=preview_repair,
        validate_reviewed_arrangement=validator.validate_reviewed_arrangement,
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
        route_class=route_support.LibraryDoctorRoute,
        responses=error_responses,
    )
    errors = route_support.RouteErrors(contracts.error_detail)
    http_error = errors.http_error
    repair_error = errors.repair_detail
    receipt_error = errors.receipt_detail
    _audio_response = route_support.audio_response

    @router.get("/status", response_model=contracts.StatusContract)
    def get_status(
        review_difficulty_scope: str = Query(default="all_authored"),
    ):
        try:
            status = scanner.status(review_difficulty_scope)
        except ValueError as exc:
            raise http_error(
                400,
                "invalid_review_difficulty_scope",
                str(exc),
                file_state="unchanged",
                next_action="correct_request",
            ) from exc
        status["batch"] = batch_manager.status()
        return status

    @router.put("/playback")
    def set_playback_state(state: contracts.PlaybackStateRequestContract):
        changed = scanner.set_playback_active(state.active)
        status = scanner.status()
        status["batch"] = batch_manager.status()
        return {"changed": changed, "status": status}

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
        status = scanner.status()
        status["batch"] = batch_manager.status()
        return {"started": started, "status": status}

    @router.post("/cancel", status_code=202)
    def cancel_scan():
        accepted = scanner.cancel()
        status = scanner.status()
        status["batch"] = batch_manager.status()
        return {"accepted": accepted, "status": status}

    @router.get("/results", response_model=contracts.ResultsContract)
    def get_results(
        result_filter: str = Query(default="all", alias="filter"),
        query: str = Query(default="", max_length=200),
        rule: str = Query(default="", max_length=200),
        review_difficulty_scope: str = Query(default="all_authored"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            payload = scanner.results(
                result_filter=result_filter,
                query=query,
                rule_code=rule,
                review_difficulty_scope=review_difficulty_scope,
                limit=limit,
                offset=offset,
            )
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list):
                recovery_states = repair_service.recovery_states(
                    item.get("package")
                    for item in items
                    if isinstance(item, dict)
                )
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    recovery = recovery_states.get(item.get("package"))
                    if recovery is None:
                        continue
                    features = item.get("features")
                    if not isinstance(features, dict):
                        features = {}
                        item["features"] = features
                    features["recovery"] = recovery
            return payload
        except ValueError as exc:
            raise http_error(
                400,
                "invalid_results_query",
                str(exc),
                file_state="unchanged",
                next_action="correct_request",
            ) from exc

    @router.get("/rules")
    def get_rules(
        review_difficulty_scope: str = Query(default="all_authored"),
    ):
        try:
            return scanner.rules(review_difficulty_scope)
        except ValueError as exc:
            raise http_error(
                400,
                "invalid_results_query",
                str(exc),
                file_state="unchanged",
                next_action="correct_request",
            ) from exc

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
            raise HTTPException(
                status_code=409,
                detail=errors.batch_detail(exc),
            ) from exc
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
            raise HTTPException(
                status_code=409,
                detail=errors.batch_detail(exc),
            ) from exc

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
            raise HTTPException(
                status_code=409,
                detail=errors.batch_detail(exc),
            ) from exc

    @router.post("/repair/batch/undo/apply", status_code=202)
    def apply_batch_undo(payload: contracts.BatchUndoApplyRequestContract):
        try:
            return batch_manager.start_undo_apply(payload.undo_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(
                status_code=409,
                detail=errors.batch_detail(exc),
            ) from exc

    @router.post("/repair/batch/finalize/preview", status_code=202)
    def preview_batch_finalization():
        try:
            return batch_manager.start_finalize_preview()
        except batch_module.BatchRepairError as exc:
            raise HTTPException(
                status_code=409,
                detail=errors.batch_detail(exc),
            ) from exc

    @router.post("/repair/batch/finalize/apply", status_code=202)
    def apply_batch_finalization(
        payload: contracts.BatchFinalizeApplyRequestContract,
    ):
        try:
            return batch_manager.start_finalize_apply(payload.finalize_plan_id)
        except batch_module.BatchRepairError as exc:
            raise HTTPException(
                status_code=409,
                detail=errors.batch_detail(exc),
            ) from exc

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
            raise HTTPException(
                status_code=errors.receipt_status_code(exc.code),
                detail=receipt_error(exc),
            ) from exc
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
            raise HTTPException(
                status_code=errors.receipt_status_code(
                    exc.code,
                    store_full_is_unavailable=False,
                ),
                detail=receipt_error(exc),
            ) from exc

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
        require_player_review(payload.package)
        try:
            return repair_service.inspect_reviewed(
                payload.package,
                payload.adapter_id,
                difficulty_scope=payload.difficulty_scope,
                offset=payload.offset,
                limit=payload.limit,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    def require_player_review(package: str) -> dict:
        availability = scanner.player_review_availability(package)
        if not availability.get("available"):
            raise http_error(
                409,
                "player_review_outside_library",
                availability.get("message")
                or "Player Review is unavailable for this package.",
                file_state="unchanged",
                retryable=False,
                next_action="use_standard_repairs",
            )
        return availability

    @router.post(
        "/reviewed-repair/player-context",
        response_model=contracts.ReviewedPlayerContextContract,
    )
    def inspect_reviewed_player(
        payload: contracts.ReviewedPlayerContextRequestContract,
    ):
        availability = require_player_review(payload.package)
        try:
            return repair_service.inspect_reviewed_player(
                payload.package,
                payload.adapter_id,
                availability["playback_filename"],
                difficulty_scope=payload.difficulty_scope,
                offset=payload.offset,
                limit=payload.limit,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/reviewed-repair/options")
    def reviewed_repair_options(
        payload: contracts.ReviewedOptionsRequestContract,
    ):
        require_player_review(payload.package)
        try:
            return repair_service.reviewed_options(
                payload.package,
                payload.adapter_id,
                payload.candidate_id,
                difficulty_scope=payload.difficulty_scope,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/reviewed-repair/preview")
    def preview_reviewed_repair(payload: contracts.ReviewedPreviewRequestContract):
        require_player_review(payload.package)
        try:
            return repair_service.preview_reviewed(
                payload.package,
                payload.adapter_id,
                [decision.model_dump() for decision in payload.decisions],
                difficulty_scope=payload.difficulty_scope,
            )
        except repair_module.RepairPlanningError as exc:
            raise HTTPException(status_code=400, detail=repair_error(exc)) from exc

    @router.post("/reviewed-repair/audio")
    def generate_reviewed_passage_audio(
        payload: contracts.ReviewedAudioRequestContract,
    ):
        require_player_review(payload.package)
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
        reviewed_difficulty_scope: str = "full_only",
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
                    difficulty_scope=reviewed_difficulty_scope,
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
        require_player_review(payload.package)
        decisions = [decision.model_dump() for decision in payload.decisions]
        ticket = begin_idempotent_mutation(
            "reviewed-repair.apply",
            payload,
            idempotency_key,
            {
                "package": payload.package,
                "adapter_id": payload.adapter_id,
                "decisions": decisions,
                "difficulty_scope": payload.difficulty_scope,
                "plan_id": payload.plan_id,
            },
        )
        return apply_repair_transaction(
            payload.package,
            payload.plan_id,
            reviewed_adapter_id=payload.adapter_id,
            reviewed_decisions=decisions,
            reviewed_difficulty_scope=payload.difficulty_scope,
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
        review_difficulty_scope: str = Query(default="all_authored"),
    ):
        try:
            filename, media_type, content = scanner.export_stream(
                export_format=export_format,
                result_filter=result_filter,
                query=query,
                rule_code=rule,
                review_difficulty_scope=review_difficulty_scope,
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
