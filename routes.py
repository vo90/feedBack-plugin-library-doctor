"""FeedBack plugin routes for Library Health."""

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query


def setup(app, context):
    load_sibling = context.get("load_sibling")
    get_dlc_dir = context.get("get_dlc_dir")
    log = context.get("log")
    if not callable(load_sibling):
        raise RuntimeError("Library Health requires FeedBack's load_sibling plugin API.")
    if not callable(get_dlc_dir):
        raise RuntimeError("Library Health requires FeedBack's song-library plugin API.")
    if log is None:
        raise RuntimeError("Library Health requires FeedBack's plugin logger.")

    validator = load_sibling("validator")
    scanner_module = load_sibling("scanner")
    scanner = scanner_module.LibraryScanner(
        config_dir=Path(context["config_dir"]),
        get_dlc_dir=get_dlc_dir,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=log,
    )

    router = APIRouter(prefix="/api/plugins/library_health", tags=["library_health"])

    @router.get("/status")
    def get_status():
        return scanner.status()

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
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"started": started, "status": scanner.status()}

    @router.post("/cancel", status_code=202)
    def cancel_scan():
        accepted = scanner.cancel()
        return {"accepted": accepted, "status": scanner.status()}

    @router.get("/results")
    def get_results(
        result_filter: str = Query(default="all", alias="filter"),
        query: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            return scanner.results(
                result_filter=result_filter,
                query=query,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.include_router(router)
