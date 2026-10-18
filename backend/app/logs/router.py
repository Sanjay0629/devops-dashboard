"""§19.1 and §19.2: centralised log search (AD-05).

``POST /logs/query`` is a ``POST`` only because the filter must not land in an
access log or a browser history (``OPEN-20``); it changes nothing, writes no audit
row of its own, needs the CSRF token like any ``POST``, and is allowed in read-only
mode so a contained portal can still be looked at.

**The route's permission is ``logs:read:application``.** Every role holding
``logs:read:system`` also holds it, so it is the right floor; the system permission
is then enforced *per server and per source type* inside the query, because that is
where the caller's servers can differ. A request that names a source type the
caller can read on none of the selected servers is a ``403`` and an audited one.

Rate limit: 30 a minute per identity (§19.1). It fails open when Redis is down, as
the read class does -- and with Redis down no session resolves anyway.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, ReadBudget, client_ip, user_agent
from app.authz import service as authz_service
from app.authz.dependencies import Authorized, ForbiddenError, require_permission
from app.core import ratelimit
from app.core.filters import reject_unknown_query_parameters
from app.db.session import get_session
from app.logs import service
from app.logs.schemas import LogQueryRequest, LogSearchResponse, LogSources

router = APIRouter(tags=["logs"])

Db = Annotated[AsyncSession, Depends(get_session)]
ReadApplicationLogs = Annotated[Authorized, require_permission("logs", "read:application")]
#: A POST that reads: see the module docstring.
SearchLogs = Annotated[
    Authorized, require_permission("logs", "read:application", allow_in_read_only=True)
]

QUERY_RATE_LIMIT: int = 30
QUERY_RATE_WINDOW_SECONDS: int = 60


async def log_query_budget(current: CurrentUser) -> None:
    await ratelimit.enforce(
        "log_query",
        str(current.user.id),
        limit=QUERY_RATE_LIMIT,
        window_seconds=QUERY_RATE_WINDOW_SECONDS,
        fail_closed=False,
    )


LogQueryBudget = Depends(log_query_budget)


@router.post(
    "/logs/query",
    dependencies=[ReadBudget, LogQueryBudget],
    summary="Search logs across authorised servers",
    responses={
        503: {"description": "Loki is unavailable or not configured. Never an empty page."},
        504: {"description": "Loki did not answer within the deadline."},
    },
)
async def query_logs(
    request: Request, body: LogQueryRequest, db: Db, authorized: SearchLogs
) -> LogSearchResponse:
    """§19.1. Structured filters in, redacted lines out; no LogQL is ever accepted."""
    try:
        return await service.search(db, authorized, body)
    except service.NoReadableSourceError:
        authz_service.record_denial(
            db,
            user_id=authorized.user.id,
            user_label=authorized.user.email,
            permission="logs:read:system",
            reason="source_type_not_readable_on_selected_servers",
            method=request.method,
            path=request.url.path,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
        )
        # The per-request session rolls back on the exception that carries the
        # 403, so the denial is committed first (as the permission dependency does).
        await db.commit()
        raise ForbiddenError from None


@router.get(
    "/logs/sources",
    dependencies=[ReadBudget],
    summary="Filter values for the log search form",
)
async def log_sources(request: Request, authorized: ReadApplicationLogs) -> LogSources:
    """§19.2. The values are closed (AD-21), so this is the catalogue itself."""
    reject_unknown_query_parameters(request, frozenset[str]())
    return service.sources(authorized)
