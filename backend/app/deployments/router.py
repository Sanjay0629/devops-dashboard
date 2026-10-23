"""Deployment targets, §21.8 – §21.12 (``OPEN-126``).

§21.8 is read by anyone holding ``deployment:read``, scoped **in the SQL** on the target's
environment and application (a target outside reach is absent, not forbidden), and returns the
reader shape: no parameter template, no policy id. §21.9 – §21.12 are ``admin:integrations``
(Administrator-only, like applications and cloud accounts, ``OPEN-27``).

**No route here accepts a URL, a job name, a node label or a build parameter** other than the
administrator-written ``pipeline_reference`` and ``parameter_template`` of §21.10 / §21.11, which
are validated by closed grammars and never concatenated into a URL as written. Nothing here calls
Jenkins.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.authz.queries import readable_deployment_targets
from app.core.filters import reject_unknown_query_parameters
from app.core.pagination import decode_cursor, encode_cursor, keyset_after, keyset_order
from app.db.session import get_session
from app.deployments import service
from app.deployments.models import DeploymentTarget
from app.deployments.schemas import (
    TargetCreate,
    TargetCreated,
    TargetDetail,
    TargetList,
    TargetStatus,
    TargetUpdate,
)
from app.inventory.models import Application
from app.inventory.router_servers import IdempotencyKey, _unmodified_since
from app.inventory.schemas import CursorPage, Environment

router = APIRouter(prefix="/deployment-targets", tags=["deployments"])

Db = Annotated[AsyncSession, Depends(get_session)]
TargetId = Annotated[uuid.UUID, Path(description="Deployment target identifier")]
ReadTargets = Annotated[Authorized, require_permission("deployment", "read")]
ManageTargets = Annotated[Authorized, require_permission("admin", "integrations")]

QUERY_PARAMS = frozenset({"application_id", "environment", "status", "limit", "cursor"})
SORT, ORDER = "created_at", "desc"


# --- 21.8 --------------------------------------------------------------------------------


@router.get("", dependencies=[ReadBudget], summary="List registered deployment targets")
async def list_targets(
    request: Request,
    db: Db,
    authorized: ReadTargets,
    application_id: uuid.UUID | None = None,
    environment: Annotated[list[Environment] | None, Query()] = None,
    status: Annotated[list[TargetStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> TargetList:
    """What may be deployed, as far as the caller may see it. Retired targets are never listed."""
    reject_unknown_query_parameters(request, QUERY_PARAMS)
    statement = (
        select(DeploymentTarget, Application.name)
        .join(Application, Application.id == DeploymentTarget.application_id)
        .where(
            DeploymentTarget.deleted_at.is_(None),
            readable_deployment_targets(authorized.grants, "deployment", "read"),
            DeploymentTarget.status.in_(status or ["ACTIVE"]),
        )
        .order_by(*keyset_order(DeploymentTarget.created_at, DeploymentTarget.id, order=ORDER))
        .limit(limit + 1)
    )
    if application_id is not None:
        statement = statement.where(DeploymentTarget.application_id == application_id)
    if environment:
        statement = statement.where(DeploymentTarget.environment.in_(environment))
    if cursor is not None:
        statement = statement.where(
            keyset_after(
                DeploymentTarget.created_at,
                DeploymentTarget.id,
                decode_cursor(cursor, sort=SORT, order=ORDER),
                order=ORDER,
            )
        )
    rows = (await db.execute(statement)).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(key=page[-1][0].created_at, row_id=page[-1][0].id, sort=SORT, order=ORDER)
        if has_more and page
        else None
    )
    return TargetList(
        items=[service.summary(target, name) for target, name in page],
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        jenkins=service.jenkins_configuration(),
    )


# --- 21.9 --------------------------------------------------------------------------------


@router.get("/{target_id}", dependencies=[ReadBudget], summary="One target, full configuration")
async def read_target(target_id: TargetId, db: Db, _authorized: ManageTargets) -> TargetDetail:
    return await service.detail(db, await service.get_target(db, target_id))


# --- 21.10 -------------------------------------------------------------------------------


@router.post("", status_code=201, summary="Register a deployment target")
async def register_target(
    request: Request,
    response: Response,
    body: TargetCreate,
    idempotency_key: IdempotencyKey,
    db: Db,
    authorized: ManageTargets,
) -> TargetCreated:
    target, replayed = await service.create_target(
        db, body, created_by=authorized.user.id, idempotency_key=idempotency_key
    )
    if replayed:
        response.status_code = 200
        return TargetCreated(
            **(await service.detail(db, target)).model_dump(), idempotent_replay=True
        )
    audit.record(
        db,
        action_type="DEPLOYMENT_TARGET_REGISTERED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="deployment_target",
        target_id=target.id,
        environment=target.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        # Names, never values: the reference and the template are configuration.
        metadata={
            "application_id": str(target.application_id),
            "environment": target.environment,
            "fields": sorted(body.model_fields_set - {"application_id", "environment"}),
        },
    )
    await db.commit()
    await db.refresh(target)
    return TargetCreated(**(await service.detail(db, target)).model_dump(), idempotent_replay=False)


# --- 21.11 -------------------------------------------------------------------------------


@router.patch("/{target_id}", summary="Edit, enable or disable a deployment target")
async def update_target(
    request: Request,
    target_id: TargetId,
    body: TargetUpdate,
    db: Db,
    authorized: ManageTargets,
    if_unmodified_since: Annotated[str | None, Header(alias="If-Unmodified-Since")] = None,
) -> TargetDetail:
    target = await service.get_target(db, target_id)
    _unmodified_since(if_unmodified_since, target.updated_at, "deployment target")
    target, changed = await service.update_target(db, target, body)
    if changed:
        audit.record(
            db,
            action_type="DEPLOYMENT_TARGET_CHANGED",
            outcome="SUCCESS",
            actor_id=authorized.user.id,
            actor_label=authorized.user.email,
            target_type="deployment_target",
            target_id=target.id,
            environment=target.environment,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={"application_id": str(target.application_id), "fields": sorted(changed)},
        )
    await db.commit()
    await db.refresh(target)
    return await service.detail(db, target)


# --- 21.12 -------------------------------------------------------------------------------


@router.delete("/{target_id}", status_code=204, summary="Retire a deployment target")
async def retire_target(
    request: Request, target_id: TargetId, db: Db, authorized: ManageTargets
) -> None:
    target = await service.get_target(db, target_id)
    await service.retire_target(db, target)
    audit.record(
        db,
        action_type="DEPLOYMENT_TARGET_RETIRED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="deployment_target",
        target_id=target.id,
        environment=target.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"application_id": str(target.application_id)},
    )
    await db.commit()
