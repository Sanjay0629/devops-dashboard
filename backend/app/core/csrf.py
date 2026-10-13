"""CSRF defence: double-submit token plus origin validation (SECURITY.md §11).

Layer 1 is ``SameSite=Strict`` on the session cookie, set in
``app/auth/cookies.py``. This module is layers 2 and 3, which exist because
`SameSite` is a browser-enforced control and a browser that does not enforce it
(or a request that is not browser-originated) would otherwise be unchecked:

2. a non-``HttpOnly`` CSRF cookie whose value must be echoed in an
   ``X-CSRF-Token`` header, compared in constant time;
3. ``Origin``/``Referer`` checked against the expected origin.

Both run on ``POST``/``PUT``/``PATCH``/``DELETE`` only — ``GET`` never changes
state (§11 rule 4), so a safe method has nothing to protect.

Note what is *not* exempted: a state-changing request without a session is
still checked. Exempting it would make "no session" a way to skip the check,
and a login-CSRF is a real attack.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Final
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.errors import error_body
from app.core.logging import get_logger

logger = get_logger(__name__)

CSRF_COOKIE_NAME: Final = "dcp_csrf"
CSRF_HEADER_NAME: Final = "X-CSRF-Token"
_UNSAFE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_TOKEN_BYTES: Final = 32


def new_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class CsrfMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests without a matching double-submit token.

    ``exempt_paths`` is for callers that are not browser-originated and carry
    their own authentication — the Alertmanager webhook and agent callbacks
    (§11 rule 5). It is a set of exact paths, not a prefix match, so a new
    endpoint cannot inherit an exemption by sitting under an exempt one.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: tuple[str, ...] = (),
        exempt_paths: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(app)
        self.allowed_origins = allowed_origins
        self.exempt_paths = exempt_paths

    def _origin_allowed(self, request: Request, origin: str) -> bool:
        if origin in self.allowed_origins:
            return True
        # Same-origin is the normal operating mode: the SPA is served from the
        # same origin as the API (docs/SECURITY.md §12), so the request's own
        # base URL is the expected origin and needs no configuration.
        own = _origin_of(str(request.base_url))
        return own is not None and origin == own

    def _reject(self, reason: str, request: Request) -> JSONResponse:
        logger.warning("csrf_rejected", reason=reason, method=request.method, path=request.url.path)
        return JSONResponse(
            status_code=403,
            # Through the standard envelope, so a CSRF failure looks like every
            # other error to a client (docs/API_CONTRACTS.md §3).
            content=error_body("forbidden", "CSRF validation failed."),
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in _UNSAFE_METHODS or request.url.path in self.exempt_paths:
            return await call_next(request)

        # Layer 3. Checked first: an absent Origin and Referer on a
        # state-changing request means the browser told us nothing about where
        # it came from, which is not a reason to proceed.
        source = request.headers.get("origin") or request.headers.get("referer")
        origin = _origin_of(source) if source else None
        if origin is None or not self._origin_allowed(request, origin):
            return self._reject("origin", request)

        # Layer 2.
        cookie = request.cookies.get(CSRF_COOKIE_NAME)
        header = request.headers.get(CSRF_HEADER_NAME)
        if not cookie or not header:
            return self._reject("missing_token", request)
        if not secrets.compare_digest(cookie, header):
            return self._reject("token_mismatch", request)

        return await call_next(request)
