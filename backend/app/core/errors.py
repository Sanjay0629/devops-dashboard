"""Error envelope and exception handlers (docs/API_CONTRACTS.md §3 and §8).

Every non-2xx response leaves through here, in exactly one shape::

    {"error": {"code": ..., "message": ..., "correlation_id": ..., "details": ...}}

``message`` is operator-facing and must never carry a stack trace, SQL fragment,
provider payload, internal hostname or path (docs/SECURITY.md §20). Unexpected
exceptions are logged in full server-side and reported to the client as a bare
``internal_error``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_correlation_id, get_logger
from app.core.redaction import redact_text, redact_value

logger = get_logger(__name__)

#: HTTP status -> stable error code, for errors raised as plain HTTPExceptions.
_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_failed",
    423: "read_only_mode",
    429: "rate_limited",
    500: "internal_error",
    502: "provider_error",
    503: "provider_unavailable",
    504: "provider_timeout",
}

#: Pydantic error type -> the validation codes published in API_CONTRACTS.md §8.
_VALIDATION_CODES: dict[str, str] = {
    "missing": "required",
    "extra_forbidden": "unknown_field",
    "enum": "enum",
    "literal_error": "enum",
    "string_pattern_mismatch": "pattern",
    "string_too_short": "min_length",
    "string_too_long": "max_length",
    "greater_than": "min_value",
    "greater_than_equal": "min_value",
    "less_than": "max_value",
    "less_than_equal": "max_value",
}


class AppError(Exception):
    """Base class for errors that carry a deliberate client-facing code."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        self.details = details


class NotConfiguredError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "not_configured"
    message = "This integration is not configured."


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": redact_text(message),
            "correlation_id": get_correlation_id(),
            "details": redact_value(details) if details is not None else None,
        }
    }


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    for item in exc.errors():
        entry: dict[str, Any] = {
            "loc": [str(part) for part in item.get("loc", ())],
            "code": _VALIDATION_CODES.get(str(item.get("type")), str(item.get("type"))),
            "message": redact_text(str(item.get("msg", ""))),
        }
        # Deliberately NOT included: item["input"]. Echoing the offending value
        # back would leak a rejected credential straight into the response
        # (docs/API_CONTRACTS.md §8).
        context = item.get("ctx")
        if isinstance(context, dict) and "expected" in context:
            entry["allowed"] = redact_value(context["expected"])
        fields.append(entry)
    return {"fields": fields}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            # The constant's name churned upstream; the number has not.
            status_code=422,
            content=error_body(
                "validation_failed",
                "The request contains invalid fields.",
                _validation_details(exc),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "internal_error")
        detail = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Full detail to the server log, nothing but a code to the client.
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("internal_error", "An unexpected error occurred."),
        )
