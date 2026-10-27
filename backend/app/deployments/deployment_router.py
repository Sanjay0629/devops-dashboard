"""Deployments, §21.1 – §21.3 and §21.6 (``OPEN-139`` – ``OPEN-141``).

Permissions, every one explicit:

* **21.2** -- the contract's permission is ``deployment:create`` on the environment, and the
  environment is in the body, so no route dependency can name it. The route carries
  ``deployment:read`` as its floor (which also brings the ``401``, the audited denial and read-only
  mode's ``423``) and the handler asks ``deployment:create`` against the environment **and**
  application, recording its own ``AUTHZ_DENIED``. The floor is not the control; the handler's
  check, and the worker's, are.
* **21.1**, **21.3** -- ``deployment:read``; *which* deployments is decided in SQL (the requester's
  own, or ``deployment:read`` over the stored environment and application).
* **21.6** -- ``deployment:read`` as the floor; the handler narrows to the requester, or to a
  ``deployment:approve`` holder in scope.

**No route here accepts a Jenkins URL, a job name, a build parameter or a node label.** The pipeline
and its parameters come from the registered target. Execution is asynchronous: this module answers
in well under a second (PRD §5.3) and the worker does the work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.core.window import resolve_window
from app.db.session import get_session
from app.deployments import lifecycle, states
from app.deployments.deployment_schemas import (
    DeploymentAccepted,
    DeploymentDetail,
    DeploymentList,
    DeploymentRequest,
    ExecutionStatus,
    Kind,
)
from app.deployments.listing import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    DeploymentFilters,
    deployment_query,
    next_cursor,
)
from app.inventory.schemas import CursorPage, Environment
from app.operations.service import Origin

router = APIRouter(prefix="/deployments", tags=["deployments"])

Db = Annotated[AsyncSession, Depends(get_session)]
DeploymentId = Annotated[uuid.UUID, Path(description="Deployment identifier")]
ReadDeployments = Annotated[Authorized, require_permission("deployment", "read")]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=64,
        description="Client-generated key, unique per logical intent (§7).",
    ),
]

#: The names §21.1 lists; anything else is ``422`` naming this set.
QUERY_PARAMS = frozenset(
    {
        "application_id",
        "environment",
        "execution_status",
        "requested_by",
        "kind",
        "from",
        "to",
        "limit",
        "cursor",
    }
)


def _origin(request: Request, authorized: Authorized) -> Origin:
    return Origin(
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        method=request.method,
        path=request.url.path,
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
    )


@router.get("", dependencies=[ReadBudget], summary="Deployment history")
async def list_deployments(
    request: Request,
    db: Db,
    authorized: ReadDeployments,
    application_id: Annotated[uuid.UUID | None, Query()] = None,
    environment: Annotated[list[Environment] | None, Query()] = None,
    execution_status: Annotated[list[ExecutionStatus] | None, Query()] = None,
    requested_by: Annotated[uuid.UUID | None, Query()] = None,
    kind: Annotated[Kind | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> DeploymentList:
    """§21.1. Your own requests, plus those you may read; filters never widen that."""
    reject_unknown_query_parameters(request, QUERY_PARAMS)
    filters = DeploymentFilters(
        application_id=application_id,
        environment=tuple(environment or ()),
        execution_status=tuple(execution_status or ()),
        requested_by=requested_by,
        kind=kind,
    )
    window = resolve_window(from_, to, now=datetime.now(UTC))
    rows = (
        await db.execute(
            deployment_query(
                authorized.grants,
                authorized.user.id,
                filters,
                window,
                cursor_token=cursor,
                limit=limit,
            )
        )
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    return DeploymentList(
        items=[lifecycle.item_view(row[0], row[1], row[2]) for row in page],
        page=CursorPage(
            limit=limit,
            next_cursor=next_cursor(page[-1][0], filters) if has_more and page else None,
            has_more=has_more,
        ),
    )


@router.post(
    "",
    status_code=202,
    summary="Request a deployment",
    responses={
        200: {"description": "An idempotent replay: the original deployment, nothing re-run."},
        503: {
            "description": "Demonstration mode, no Jenkins, or an approval that cannot be given."
        },
    },
)
async def request_deployment(
    request: Request,
    response: Response,
    body: DeploymentRequest,
    idempotency_key: IdempotencyKey,
    db: Db,
    authorized: ReadDeployments,
) -> DeploymentAccepted:
    """§21.2. ``202`` for a new deployment, ``200`` for an idempotent replay."""
    deployment, replayed = await lifecycle.request_deployment(
        db,
        _origin(request, authorized),
        authorized,
        body=body,
        idempotency_key=idempotency_key,
    )
    if replayed:
        response.status_code = 200
    elif deployment.execution_status == states.REQUESTED:
        await lifecycle.enqueue_execution(deployment.id)
    return await lifecycle.accepted_view(db, deployment, replayed=replayed)


@router.get("/{deployment_id}", dependencies=[ReadBudget], summary="Deployment status")
async def read_deployment(
    deployment_id: DeploymentId, db: Db, authorized: ReadDeployments
) -> DeploymentDetail:
    """§21.3. Unknown, or out of scope for a caller who is not the requester ⇒ ``404``."""
    return await lifecycle.read_deployment(db, authorized, deployment_id)


@router.post("/{deployment_id}/cancel", summary="Cancel before execution")
async def cancel_deployment(
    request: Request, deployment_id: DeploymentId, db: Db, authorized: ReadDeployments
) -> DeploymentDetail:
    """§21.6. Legal only from ``REQUESTED``; a queued or running one is ``409``."""
    return await lifecycle.cancel_deployment(
        db, _origin(request, authorized), authorized, deployment_id
    )
