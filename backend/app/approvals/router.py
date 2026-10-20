"""Approvals, endpoints 22.1 – 22.4 (docs/API_CONTRACTS.md §22).

Every route carries ``operation:approve`` explicitly. That is the floor (it also brings the ``401``,
the audited denial and read-only mode's ``423``); scope, separation of duties and the policy's role
are checked again, per row, by :func:`app.approvals.service.decide` against the stored environment
of the target. The queue is filtered in SQL to the servers the caller may approve on, so a request
outside their reach is absent rather than forbidden.

Deciding returns the operation (the §17.2 shape). On approval the execution task is enqueued
**after** the commit; if the broker is down the healer picks the ``APPROVED`` row up.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.approvals.schemas import (
    ApprovalDecision,
    ApprovalDetail,
    ApprovalList,
    DecisionRequest,
)
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.errors import field_error
from app.db.session import get_session
from app.operations import service as operations_service
from app.operations.schemas import OperationDetail

router = APIRouter(tags=["approvals"])

Db = Annotated[AsyncSession, Depends(get_session)]
ApprovalId = Annotated[uuid.UUID, Path(description="Approval identifier")]
OperationId = Annotated[uuid.UUID, Path(description="Operation identifier")]
Approvers = Annotated[Authorized, require_permission("operation", "approve")]


def _origin(request: Request, authorized: Authorized) -> operations_service.Origin:
    return operations_service.Origin(
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        method=request.method,
        path=request.url.path,
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
    )


@router.get("/approvals", dependencies=[ReadBudget], summary="The approver queue")
async def list_approvals(
    db: Db,
    authorized: Approvers,
    decision: ApprovalDecision = "PENDING",
    operation_type: Literal["SERVICE_OPERATION", "DEPLOYMENT"] = "SERVICE_OPERATION",
    environment: Literal["development", "staging", "production", "other"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ApprovalList:
    """§22.1. Scope-filtered in SQL; ``DEPLOYMENT`` is refused until M5.4."""
    if operation_type != "SERVICE_OPERATION":
        raise field_error(
            ["query", "operation_type"],
            "not_permitted",
            "Only SERVICE_OPERATION approvals exist yet.",
        )
    return await service.list_approvals(
        db,
        authorized,
        decision=decision,
        environment=environment,
        cursor_token=cursor,
        limit=limit,
    )


@router.get("/approvals/{approval_id}", dependencies=[ReadBudget], summary="Approval detail")
async def read_approval(approval_id: ApprovalId, db: Db, authorized: Approvers) -> ApprovalDetail:
    """§22.2. Unknown, or outside the caller's approval scope ⇒ ``404``."""
    return await service.read_approval(db, authorized, approval_id)


async def _decide(
    request: Request,
    db: AsyncSession,
    authorized: Authorized,
    operation_id: uuid.UUID,
    body: DecisionRequest,
    *,
    approve: bool,
) -> OperationDetail:
    detail, enqueue = await service.decide(
        db,
        _origin(request, authorized),
        authorized,
        operation_id,
        approve=approve,
        reason=body.reason,
    )
    if enqueue:
        await operations_service.enqueue_execution(operation_id)
    return detail


@router.post("/operations/{operation_id}/approve", summary="Approve an operation")
async def approve_operation(
    request: Request,
    operation_id: OperationId,
    db: Db,
    authorized: Approvers,
    body: DecisionRequest = DecisionRequest(),  # noqa: B008 -- an immutable-in-practice model
) -> OperationDetail:
    """§22.3. Another person, in scope and in the policy's role; ``409`` on a lost race."""
    return await _decide(request, db, authorized, operation_id, body, approve=True)


@router.post("/operations/{operation_id}/reject", summary="Reject an operation")
async def reject_operation(
    request: Request,
    operation_id: OperationId,
    db: Db,
    authorized: Approvers,
    body: DecisionRequest = DecisionRequest(),  # noqa: B008
) -> OperationDetail:
    """§22.4. A reason is required."""
    return await _decide(request, db, authorized, operation_id, body, approve=False)
