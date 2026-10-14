"""Authentication dependencies.

``require_session`` is the single place a request becomes attributable. It is
**authentication only** — it answers "who is this", never "may they do this".
Permission enforcement is one separate dependency, ``require_permission``,
arriving in M1.4 (docs/SECURITY.md §4 rule 7: one matcher).

Fail closed, in the strong sense (mandatory rule 6): *any* failure resolving
the session — absent cookie, unknown id, expired deadline, unreadable record,
Redis unreachable, the user row vanished or was disabled since login — produces
``401``. There is no permissive branch to find, and no ``if DEBUG`` bypass.

:func:`require_read_budget` is the read-endpoint rate limit (docs/SECURITY.md
§13). It lives here rather than in ``app/core/ratelimit.py`` because it is
keyed on the caller's identity and so has to depend on ``require_session`` —
and ``app/core`` deliberately does not import from the layers above it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import session as session_store
from app.auth.cookies import SESSION_COOKIE_NAME
from app.auth.models import User
from app.auth.session import SessionRecord
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import get_session

logger = get_logger(__name__)


class UnauthenticatedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication required."


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: User
    record: SessionRecord
    session_id: str


def client_ip(request: Request) -> str | None:
    """The client address, from the connection only.

    ``X-Forwarded-For`` is deliberately **not** read. It is attacker-controlled
    unless a trusted proxy is known to overwrite it, and this value lands in
    ``audit_events.source_ip`` and in the rate-limit key — a spoofable rate-limit
    key is no rate limit at all. Reading a forwarded header is a change to make
    when the production proxy is configured (OPEN-12), with the trusted hop
    count explicit.
    """
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


async def require_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AuthenticatedUser:
    """Resolve the session cookie to a live user, or raise ``401``."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise UnauthenticatedError

    try:
        record = await session_store.load(session_id)
    except RedisError as error:
        # The session store is unreachable. Denying is the only safe answer:
        # treating "cannot check" as "valid" would make a Redis outage an
        # authentication bypass (mandatory rule 6).
        logger.warning("session_lookup_failed", error=str(error))
        raise UnauthenticatedError from error
    if record is None:
        raise UnauthenticatedError

    user = await db.get(User, record.user_id)
    if user is None or user.status != "ACTIVE":
        # Disabled or deleted since the session was issued. Destroy the session
        # rather than merely refusing this one request.
        await _best_effort_delete(session_id)
        logger.info("session_rejected_inactive_user", user_id=str(record.user_id))
        raise UnauthenticatedError

    return AuthenticatedUser(user=user, record=record, session_id=session_id)


async def _best_effort_delete(session_id: str) -> None:
    try:
        await session_store.delete(session_id)
    except RedisError as error:
        logger.warning("session_delete_failed", error=str(error))


async def require_read_budget(
    current: Annotated[AuthenticatedUser, Depends(require_session)],
) -> None:
    """Spend one unit of the read budget (docs/SECURITY.md §13).

    600/min per identity by default: dashboard polling headroom, not a meter.
    **Fails open** when Redis is unreachable, which is the documented
    asymmetry in §13 — a read endpoint that starts refusing traffic because
    the counter store blinked turns a Redis hiccup into an outage of the
    monitoring portal, while the privileged classes (``/auth/*``, and the
    operation and deployment classes from M4) keep failing closed.

    Keyed on the user id, never on the client address: the address is
    spoofable where a proxy is in front (see :func:`client_ip`), and the
    budget is per identity by specification.
    """
    settings = get_settings()
    await ratelimit.enforce(
        "read",
        str(current.user.id),
        limit=settings.read_rate_limit_max,
        window_seconds=settings.read_rate_limit_window_seconds,
        fail_closed=False,
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(require_session)]
#: Declared by every read endpoint, as a route-level dependency.
ReadBudget = Depends(require_read_budget)
