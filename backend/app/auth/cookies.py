"""Cookie assembly (docs/SECURITY.md §5).

Flags live here, in one place, so there is exactly one answer to "how is the
session cookie set" and a test can assert it. Written as functions on a
response rather than as strings, so a handler cannot accidentally omit a flag.

``Secure`` comes from ``Settings.cookie_secure``, which is derived from the
environment rather than configurable: §5 permits exactly one deviation —
``Secure=false`` on localhost — and a deployment should not be able to turn it
off in production by setting a variable.

``SameSite=Strict`` is the primary CSRF control. The cost is real and accepted:
a cross-site link into the app arrives without the cookie, so the first
navigation from an external page shows the login screen. For an internal
operations console that is the right trade.
"""

from __future__ import annotations

from typing import Final

from starlette.responses import Response

from app.core.config import Settings
from app.core.csrf import CSRF_COOKIE_NAME

SESSION_COOKIE_NAME: Final = "dcp_session"
#: Host-only: no Domain attribute, so the cookie is never sent to a sibling
#: subdomain that something else might be serving.
_PATH: Final = "/"


def set_session_cookie(
    response: Response,
    session_id: str,
    settings: Settings,
    *,
    max_age: int | None = None,
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=max_age if max_age is not None else settings.session_absolute_timeout_seconds,
        path=_PATH,
        # Not readable by JavaScript: the browser holds no token and cannot
        # exfiltrate the session through an XSS payload (AD-02).
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def set_csrf_cookie(
    response: Response,
    token: str,
    settings: Settings,
    *,
    max_age: int | None = None,
) -> None:
    """The double-submit partner cookie.

    Deliberately **not** ``HttpOnly``: the SPA has to read it to echo it in the
    ``X-CSRF-Token`` header. That is safe because the token is not a
    credential — it authenticates nothing on its own, it only proves the
    request came from a page that can read this origin's cookies.
    """
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=max_age if max_age is not None else settings.session_absolute_timeout_seconds,
        path=_PATH,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Expire both cookies.

    The flags must match the ones used to set them, or some browsers keep the
    original cookie alongside the deletion and the user stays "logged in" in
    the UI while the server-side session is gone.
    """
    for name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(
            name,
            path=_PATH,
            secure=settings.cookie_secure,
            httponly=name == SESSION_COOKIE_NAME,
            samesite="strict",
        )
