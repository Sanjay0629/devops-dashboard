"""The audit trail, endpoints 24.1 and 24.2 (docs/API_CONTRACTS.md §24).

**There is no ``POST``, ``PATCH``, ``PUT`` or ``DELETE`` on an audit event** and none may be added
here (AD-11); the export request (24.3) creates an export row, not an event, and lives with the
export. Both routes carry ``audit:read`` -- Approver and Administrator -- and read through
:mod:`app.audit.read`, which scopes in SQL, redacts again and records ``AUDIT_QUERIED``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import read
from app.audit.query import AuditFilters
from app.audit.schemas import AuditEventList, AuditEventView
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.core.window import resolve_window
from app.db.session import get_session
from app.operations.history import DEFAULT_LIMIT, MAX_LIMIT
from app.operations.history_service import now_utc

router = APIRouter(tags=["audit"])

Db = Annotated[AsyncSession, Depends(get_session)]
ReadAudit = Annotated[Authorized, require_permission("audit", "read")]

AUDIT_PARAMS = frozenset(
    {
        "actor_id",
        "actor_type",
        "action_type",
        "target_type",
        "target_id",
        "environment",
        "outcome",
        "correlation_id",
        "from",
        "to",
        "order",
        "limit",
        "cursor",
    }
)
#: §24.1: a range beyond this needs both ``from`` and ``to``.
EXPLICIT_BEYOND_DAYS = 7


def _origin(request: Request) -> read.Origin:
    return read.Origin(client_ip=client_ip(request), user_agent=user_agent(request))


@router.get("/audit-events", dependencies=[ReadBudget], summary="Query the audit trail")
async def list_audit_events(
    request: Request,
    db: Db,
    authorized: ReadAudit,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_type: Annotated[Literal["USER", "SYSTEM", "INTEGRATION"] | None, Query()] = None,
    action_type: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Z_]{1,64}$")] = None,
    target_type: Annotated[str | None, Query(max_length=32)] = None,
    target_id: Annotated[uuid.UUID | None, Query()] = None,
    environment: Annotated[
        Literal["development", "staging", "production", "other"] | None, Query()
    ] = None,
    outcome: Annotated[Literal["SUCCESS", "FAILURE", "DENIED"] | None, Query()] = None,
    correlation_id: Annotated[uuid.UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> AuditEventList:
    """§24.1. Scope-filtered in SQL; reading the trail is itself audited (``AUDIT_QUERIED``)."""
    reject_unknown_query_parameters(request, AUDIT_PARAMS)
    window = resolve_window(
        from_, to, now=now_utc(), explicit_beyond=timedelta(days=EXPLICIT_BEYOND_DAYS)
    )
    return await read.list_events(
        db,
        authorized,
        _origin(request),
        AuditFilters(
            actor_id=actor_id,
            actor_type=actor_type,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            environment=environment,
            outcome=outcome,
            correlation_id=correlation_id,
        ),
        window,
        order=order,
        cursor_token=cursor,
        limit=limit,
    )


@router.get("/audit-events/{event_id}", dependencies=[ReadBudget], summary="One audit event")
async def read_audit_event(
    request: Request,
    event_id: Annotated[uuid.UUID, Path(description="Audit event identifier")],
    db: Db,
    authorized: ReadAudit,
) -> AuditEventView:
    """§24.2. Unknown, or outside the caller's scope ⇒ ``404``; a read is audited."""
    return await read.read_event(db, authorized, _origin(request), event_id)
