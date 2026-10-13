"""Uniform API error responses: stable codes, safe messages, request IDs, no stack traces."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from marketlens_core.errors import ErrorCode, MarketLensError, http_status_for
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ApiError, ErrorResponse

logger = logging.getLogger("marketlens.api")


def error_response(
    request: Request | None,
    code: ErrorCode,
    message: str,
    *,
    status_code: int | None = None,
    retryable: bool = False,
    retry_after_seconds: float | None = None,
    details: dict | None = None,
) -> JSONResponse:
    request_id = getattr(getattr(request, "state", None), "request_id", None) if request else None
    body = ErrorResponse(
        error=ApiError(
            code=code,
            message=message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            details=details or {},
            request_id=request_id,
        )
    )
    headers = {"Retry-After": str(int(retry_after_seconds))} if retry_after_seconds else None
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code or http_status_for(code), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MarketLensError)
    async def _domain(request: Request, exc: MarketLensError) -> JSONResponse:
        return error_response(
            request,
            exc.code,
            exc.message,
            retryable=exc.retryable,
            retry_after_seconds=exc.retry_after_seconds,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "issue": err.get("msg", "invalid")}
            for err in exc.errors()[:12]
        ]
        return error_response(
            request,
            ErrorCode.INVALID_INPUT,
            "The request is invalid. Check the highlighted fields.",
            details={"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(
                request, ErrorCode.NOT_FOUND, "The requested resource was not found.", status_code=404
            )
        if exc.status_code == 405:
            return error_response(request, ErrorCode.INVALID_INPUT, "Method not allowed.", status_code=405)
        return error_response(request, ErrorCode.INTERNAL_ERROR, "Request failed.", status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error (request_id=%s)", getattr(request.state, "request_id", None))
        return error_response(request, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
