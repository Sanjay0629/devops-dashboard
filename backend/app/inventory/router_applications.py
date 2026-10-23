"""Application endpoints 18.1–18.5 (docs/API_CONTRACTS.md §18).

18.6 (health per environment) and 18.7 (deployment history) are **not** here:
the first needs the health-check runner and host health from Phase 2/3, the
second needs ``deployments`` from Phase 5. Both would have to return invented
data today.

Permissions follow §18 exactly: reads are ``server:read``, writes are
``admin:integrations`` — which migration 0004 renames from the seeded
``integration:manage``, this being the first endpoint to require it
(PROGRESS.md issue 39).

Applications are **not** scope-filtered. That is a decision with a reason and
an open question behind it; both are in ``app/inventory/queries.py`` and in
PROGRESS.md as ``OPEN-27``.

Audit coverage: ``APPLICATION_REGISTERED``, ``APPLICATION_UPDATED``,
``APPLICATION_ARCHIVED``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.config import get_settings
from app.core.filters import reject_unknown_query_parameters
from app.core.logging import get_logger
from app.db.session import get_session
from app.inventory import queries, service
from app.inventory.router_servers import IdempotencyKey, Order, _cursor
from app.inventory.schemas import (
    ApplicationCreated,
    ApplicationCreateRequest,
    ApplicationDetail,
    ApplicationList,
    ApplicationStatus,
    ApplicationUpdateRequest,
    CursorPage,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/applications", tags=["inventory"])

Db = Annotated[AsyncSession, Depends(get_session)]
ApplicationId = Annotated[uuid.UUID, Path(description="Application identifier")]
ReadApplications = Annotated[Authorized, require_permission("server", "read")]
ManageApplications = Annotated[Authorized, require_permission("admin", "integrations")]

ApplicationSort = Literal["name", "slug", "created_at"]


# --- 18.1 --------------------------------------------------------------------


@router.get("", dependencies=[ReadBudget], summary="List applications")
async def list_applications(
    request: Request,
    db: Db,
    _authorized: ReadApplications,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    sort: ApplicationSort = "name",
    order: Order = "asc",
    owner_team: Annotated[list[str] | None, Query(max_length=128)] = None,
    status: Annotated[list[ApplicationStatus] | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> ApplicationList:
    """§18.1, cursor-paged like §14.1 so one client paginator serves both."""
    reject_unknown_query_parameters(request, queries.APPLICATION_QUERY_PARAMS)
    statement = queries.applications_query(
        sort=sort,
        order=order,
        cursor=_cursor(cursor, sort=sort, order=order),
        limit=limit,
        owner_team=owner_team,
        status=list(status) if status else None,
        q=q,
    )
    rows = (await db.execute(statement)).scalars().all()
    items, next_cursor, has_more = queries.page_of(
        rows, limit=limit, sort=sort, order=order, sorts=queries.APPLICATION_SORTS
    )
    return ApplicationList(
        items=[service.application_summary(row) for row in items],
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        demo_mode=get_settings().demo_mode,
    )


# --- 18.2 --------------------------------------------------------------------


@router.post("", status_code=201, summary="Register an application")
async def register_application(
    request: Request,
    response: Response,
    body: ApplicationCreateRequest,
    idempotency_key: IdempotencyKey,
    db: Db,
    authorized: ManageApplications,
) -> ApplicationCreated:
    """§18.2. Every ``health_check_config`` URL is allowlist-checked at write
    time by the request model (docs/SECURITY.md §19)."""
    application, replayed = await service.create_application(
        db, body, created_by=authorized.user.id, idempotency_key=idempotency_key
    )
    if replayed:
        response.status_code = 200
        detail = await service.application_detail(db, application, authorized.grants)
        return ApplicationCreated(**detail.model_dump(), idempotent_replay=True)

    audit.record(
        db,
        action_type="APPLICATION_REGISTERED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="application",
        target_id=application.id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"name": application.name, "slug": application.slug},
    )
    await db.commit()
    await db.refresh(application)
    logger.info("application_registered", application_id=str(application.id))
    detail = await service.application_detail(db, application, authorized.grants)
    return ApplicationCreated(**detail.model_dump(), idempotent_replay=False)


# --- 18.3 --------------------------------------------------------------------


@router.get(
    "/{application_id}",
    dependencies=[ReadBudget],
    summary="Application detail: servers, services, targets",
)
async def read_application(
    application_id: ApplicationId, db: Db, authorized: ReadApplications
) -> ApplicationDetail:
    """§18.3."""
    application = await service.get_application(db, application_id)
    return await service.application_detail(db, application, authorized.grants)


# --- 18.4 --------------------------------------------------------------------


@router.patch("/{application_id}", summary="Edit application metadata")
async def update_application(
    request: Request,
    application_id: ApplicationId,
    body: ApplicationUpdateRequest,
    db: Db,
    authorized: ManageApplications,
) -> ApplicationDetail:
    """§18.4. ``slug`` is absent from the request model and so is immutable."""
    application = await service.get_application(db, application_id)
    application, changes = await service.update_application(db, application, body)
    if changes:
        audit.record(
            db,
            action_type="APPLICATION_UPDATED",
            outcome="SUCCESS",
            actor_id=authorized.user.id,
            actor_label=authorized.user.email,
            target_type="application",
            target_id=application.id,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={"changes": changes},
        )
    await db.commit()
    await db.refresh(application)
    return await service.application_detail(db, application, authorized.grants)


# --- 18.5 --------------------------------------------------------------------


@router.delete("/{application_id}", status_code=204, summary="Archive an application")
async def archive_application(
    request: Request,
    application_id: ApplicationId,
    db: Db,
    authorized: ManageApplications,
) -> None:
    """§18.5 — soft delete. Associations and history are retained."""
    application = await service.get_application(db, application_id)
    await service.archive_application(db, application)
    audit.record(
        db,
        action_type="APPLICATION_ARCHIVED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="application",
        target_id=application.id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"slug": application.slug},
    )
    await db.commit()
    logger.info("application_archived", application_id=str(application.id))
