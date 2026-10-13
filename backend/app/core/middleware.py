"""Request middleware: correlation ids, timing, security headers.

Security headers follow docs/SECURITY.md §7. They are set here rather than at the
proxy so they hold in local development and in tests too — a header that only
exists in production is a header nobody has verified.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.logging import (
    correlation_id_var,
    get_logger,
    new_correlation_id,
)

logger = get_logger(__name__)

CORRELATION_HEADER = "X-Correlation-ID"
DURATION_HEADER = "X-Request-Duration-ms"

#: A client-supplied correlation id is untrusted input that ends up in logs and
#: audit rows, so it is length- and charset-bounded before use. Anything else is
#: replaced with a fresh id rather than rejected — observability must not be a
#: reason to fail a request.
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
}


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accept or generate a correlation id, bind it, and echo it back (AD-18)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get(CORRELATION_HEADER)
        correlation_id = (
            supplied if supplied and _SAFE_CORRELATION_ID.match(supplied) else new_correlation_id()
        )
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers[CORRELATION_HEADER] = correlation_id
        response.headers[DURATION_HEADER] = f"{elapsed_ms:.1f}"
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(elapsed_ms, 1),
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the standard security headers to every response.

    HSTS is added only when the request arrived over TLS, so local HTTP
    development is not poisoned by a cached Strict-Transport-Security policy.
    """

    def __init__(self, app: ASGIApp, *, enable_hsts: bool = True) -> None:
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if self.enable_hsts and request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
