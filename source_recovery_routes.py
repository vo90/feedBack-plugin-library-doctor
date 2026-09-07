"""HTTP boundary for folder-based source bend recovery jobs."""

from fastapi import HTTPException, Query


def register(router, *, manager, scanner, contracts, errors, error_type):
    def invoke(operation, *args):
        try:
            return operation(*args)
        except error_type as exc:
            raise HTTPException(status_code=409, detail=errors.batch_detail(exc)) from exc
        except ValueError as exc:
            raise errors.http_error(
                409, "source_batch_unavailable", str(exc), file_state="unchanged",
                next_action="review_source_batch",
            ) from exc

    @router.get("/source-recovery/batch/status")
    def source_batch_status():
        return manager.status()

    @router.post("/source-recovery/batch/preview", status_code=202)
    def source_batch_preview(payload: contracts.SourceRecoveryBatchPreviewRequestContract):
        def start():
            return manager.start_preview(
                scanner.source_recovery_scope_snapshot(), payload.source_folder,
            )
        return invoke(start)

    @router.get("/source-recovery/batch/details")
    def source_batch_details(package: str = Query(min_length=1, max_length=4096)):
        return invoke(manager.preview_details, package)

    @router.post("/source-recovery/batch/reuse", status_code=202)
    def source_batch_reuse(payload: contracts.SourceRecoveryReuseRequestContract):
        def start():
            return manager.start_preview(scanner.source_recovery_scope_snapshot(), "", reuse_report=payload.report)
        return invoke(start)

    @router.post("/source-recovery/batch/apply", status_code=202)
    def source_batch_apply(payload: contracts.BatchApplyRequestContract):
        return invoke(manager.start_apply, payload.batch_plan_id)

    @router.post("/source-recovery/batch/cancel", status_code=202)
    def source_batch_cancel():
        return {"accepted": manager.cancel(), "status": manager.status()}

    @router.post("/source-recovery/batch/undo/preview", status_code=202)
    def source_batch_undo_preview():
        return invoke(manager.start_undo_preview)

    @router.post("/source-recovery/batch/undo/apply", status_code=202)
    def source_batch_undo_apply(payload: contracts.BatchUndoApplyRequestContract):
        return invoke(manager.start_undo_apply, payload.undo_plan_id)
