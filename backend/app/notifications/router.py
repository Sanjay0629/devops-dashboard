"""§26.8 - §26.10 (channel administration) and §20.6 (retry a delivery).

Channel administration is ``admin:integrations``, Administrator-only like cloud accounts
(``OPEN-27``, ``OPEN-56``). Retry is ``alert:acknowledge`` and scoped through the alert it
belongs to, so an on-call operator can retry a delivery for an alert they can act on without
being able to see or edit a channel. There is deliberately **no** delete, no "send a test" and
no verify route (``OPEN-57``).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import queries
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.db.session import get_session
from app.inventory.service import NotFoundError
from app.notifications import service
from app.notifications.schemas import (
    ChannelCreate,
    ChannelList,
    ChannelOut,
    ChannelUpdate,
    DeliveryOut,
)

router = APIRouter(tags=["notifications"])

Db = Annotated[AsyncSession, Depends(get_session)]
ManageChannels = Annotated[Authorized, require_permission("admin", "integrations")]
ActOnAlerts = Annotated[Authorized, require_permission("alert", "acknowledge")]


@router.get(
    "/notification-channels",
    dependencies=[ReadBudget],
    summary="List notification channels and how their deliveries are going",
)
async def list_channels(request: Request, db: Db, authorized: ManageChannels) -> ChannelList:
    reject_unknown_query_parameters(request, frozenset())
    return await service.list_channels(db)


@router.post("/notification-channels", status_code=201, summary="Register a notification channel")
async def create_channel(
    body: ChannelCreate, request: Request, db: Db, authorized: ManageChannels
) -> ChannelOut:
    """The destination is a *reference*; a URL or token in its place is a ``422``."""
    return await service.create_channel(
        db, authorized, body, client_ip=client_ip(request), user_agent=user_agent(request)
    )


@router.patch("/notification-channels/{channel_id}", summary="Edit, enable or disable a channel")
async def update_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    request: Request,
    db: Db,
    authorized: ManageChannels,
) -> ChannelOut:
    return await service.update_channel(
        db,
        authorized,
        channel_id,
        body,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )


@router.post(
    "/alerts/{alert_id}/notifications/{delivery_id}/retry",
    status_code=202,
    summary="Retry a failed or abandoned notification delivery",
)
async def retry_delivery(
    alert_id: uuid.UUID,
    delivery_id: uuid.UUID,
    request: Request,
    db: Db,
    authorized: ActOnAlerts,
) -> DeliveryOut:
    """``FAILED`` / ``ABANDONED`` only; a ``SENT`` delivery is never re-sent by a button."""
    visible = (
        await db.execute(queries.one_query(authorized.grants, alert_id))
    ).scalar_one_or_none()
    if visible is None:
        raise NotFoundError
    return await service.retry_delivery(
        db,
        authorized,
        alert_id=alert_id,
        delivery_id=delivery_id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )
