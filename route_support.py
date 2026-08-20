"""Shared HTTP transport policy for Library Doctor routes."""

from collections.abc import Callable

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute


class LibraryDoctorRoute(APIRoute):
    """Keep FastAPI validation and unexpected faults in the plugin contract."""

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
                            "message": (
                                "The request did not match the Library Doctor "
                                f"contract.{field_copy}"
                            ),
                            "file_state": "unchanged",
                            "retryable": False,
                            "next_action": "correct_request",
                        }
                    },
                )
            except HTTPException:
                raise
            except Exception:
                # Never leak package paths, parser details, or database text.
                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": {
                            "code": "internal_plugin_error",
                            "message": (
                                "Library Doctor could not complete the request safely."
                            ),
                            "file_state": "unchanged",
                            "retryable": True,
                            "next_action": "retry_later",
                        }
                    },
                )

        return uniform_route_handler


class RouteErrors:
    """Build public error envelopes without importing domain modules."""

    _RETRYABLE_REPAIR_CODES = frozenset({
        "source_changed",
        "package_changed",
        "backup_cleanup_failed",
        "package_unreadable",
    })
    _REVIEW_REPAIR_CODES = frozenset({
        "invalid_plan",
        "source_changed",
        "nothing_to_repair",
    })

    def __init__(self, error_detail: Callable[..., dict]) -> None:
        self._error_detail = error_detail

    def detail(
        self,
        code: str,
        message: str,
        *,
        file_state: str | None = None,
        retryable: bool = False,
        next_action: str | None = None,
    ) -> dict:
        return self._error_detail(
            code,
            message,
            file_state=file_state,
            retryable=retryable,
            next_action=next_action,
        )

    def http_error(
        self,
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
            detail=self.detail(
                code,
                message,
                file_state=file_state,
                retryable=retryable,
                next_action=next_action,
            ),
        )

    def batch_detail(self, exc) -> dict:
        return self.detail(
            exc.code,
            str(exc),
            file_state="unchanged",
            retryable=exc.code in {"batch_busy", "scan_incomplete"},
            next_action=("retry_later" if exc.code == "batch_busy" else "review_batch"),
        )

    def repair_detail(self, exc) -> dict:
        return self.detail(
            exc.code,
            str(exc),
            file_state=exc.file_state,
            retryable=exc.code in self._RETRYABLE_REPAIR_CODES,
            next_action=(
                "resolve_recovery"
                if exc.code == "recovery_required"
                else "scan_again"
                if exc.code in {"source_changed", "package_changed"}
                else "review_repair"
                if exc.code in self._REVIEW_REPAIR_CODES
                else "retry_later"
                if exc.code in self._RETRYABLE_REPAIR_CODES
                else "inspect_package"
            ),
        )

    def receipt_detail(self, exc) -> dict:
        return self.detail(
            exc.code,
            str(exc),
            file_state=exc.file_state,
            retryable=exc.retryable,
            next_action=exc.next_action,
        )

    @staticmethod
    def receipt_status_code(
        code: str,
        *,
        store_full_is_unavailable: bool = True,
    ) -> int:
        if code == "receipt_not_found":
            return 404
        if code == "idempotency_store_unavailable":
            return 503
        if code == "idempotency_store_full" and store_full_is_unavailable:
            return 503
        return 409


def audio_response(content: bytes, range_header: str | None = None) -> Response:
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
