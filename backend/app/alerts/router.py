"""§20.1 - §20.4: list, read, acknowledge and assign alerts.

Reads are ``alert:read``, the two lifecycle writes ``alert:acknowledge`` (§20.4 shares it);
both are scoped to what the caller can read through the server an alert is about
(``app/alerts/queries.py``). The webhook that *creates* alerts is a different router
(``app/alerts/webhook.py``) with no session at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import queries, read
from app.alerts.schemas import (
    AcknowledgeRequest,
    AlertDetail,
    AlertList,
    AlertSeverity,
    AlertStatus,
    AssignRequest,
)
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.db.session import get_session
from app.inventory.schemas import Environment

router = APIRouter(tags=["alerts"])

Db = Annotated[AsyncSession, Depends(get_session)]
ReadAlerts = Annotated[Authorized, require_permission("alert", "read")]
ActOnAlerts = Annotated[Authorized, require_permission("alert", "acknowledge")]

#: §20.1's default: what is still open.
DEFAULT_STATUSES: tuple[AlertStatus, ...] = ("FIRING", "ACKNOWLEDGED")


@router.get("/alerts", dependencies=[ReadBudget], summary="List alerts")
async def list_alerts(
    request: Request,
    db: Db,
    authorized: ReadAlerts,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    status: Annotated[list[AlertStatus] | None, Query()] = None,
    severity: Annotated[list[AlertSeverity] | None, Query()] = None,
    environment: Annotated[list[Environment] | None, Query()] = None,
    target_type: Annotated[
        str | None, Query(pattern=r"^(server|service|application|deployment|operation)$")
    ] = None,
    target_id: Annotated[uuid.UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> AlertList:
    """Scope-filtered, newest activity first. Alert text is untrusted: render it as text."""
    reject_unknown_query_parameters(request, queries.ALERT_QUERY_PARAMS)
    return await read.list_alerts(
        db,
        authorized,
        statuses=list(status) if status else list(DEFAULT_STATUSES),
        severities=list(severity) if severity else None,
        environment=environment,
        target_type=target_type,
        target_id=target_id,
        start=from_,
        end=to,
        q=q,
        cursor_token=cursor,
        limit=limit,
    )


@router.get("/alerts/{alert_id}", dependencies=[ReadBudget], summary="Alert detail and history")
async def get_alert(
    alert_id: uuid.UUID, request: Request, db: Db, authorized: ReadAlerts
) -> AlertDetail:
    reject_unknown_query_parameters(request, frozenset())
    return await read.detail(db, authorized, alert_id)


@router.post("/alerts/{alert_id}/acknowledge", summary="Acknowledge an alert")
async def acknowledge_alert(
    alert_id: uuid.UUID,
    body: AcknowledgeRequest,
    request: Request,
    db: Db,
    authorized: ActOnAlerts,
) -> AlertDetail:
    """FIRING only. **Never marks the problem resolved**: only the source does that."""
    return await read.acknowledge(
        db,
        authorized,
        alert_id,
        note=body.note,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )


@router.post("/alerts/{alert_id}/assign", summary="Assign an alert to a user, or unassign it")
async def assign_alert(
    alert_id: uuid.UUID,
    body: AssignRequest,
    request: Request,
    db: Db,
    authorized: ActOnAlerts,
) -> AlertDetail:
    return await read.assign(
        db,
        authorized,
        alert_id,
        assignee_id=body.assignee_id,
        note=body.note,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )
