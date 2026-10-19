"""Service operations, endpoints 17.1 – 17.3 (docs/API_CONTRACTS.md §17).

The most security-sensitive group in the API (SECURITY.md §16). Permissions, every one explicit:

* **17.1** -- the contract's permission is ``service:{action}`` on the target's environment, and
  the action is in the body, so no route dependency can name it. The route carries ``service:read``
  as its floor (which also brings the ``401``, the audited denial and read-only mode's ``423``)
  and the handler asks for ``service:{action}`` against the stored environment, recording its own
  ``AUTHZ_DENIED``. The floor is not the control; the handler's check, and the worker's, are.
* **17.2** and **17.3** -- ``operation:read``; the handler narrows to the requester, or to
  ``operation:read`` / ``operation:approve`` over the target's environment.

Execution is asynchronous: this module answers in well under a second (PRD §5.3) and the worker
does the work.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.db.session import get_session
from app.operations import service
from app.operations.schemas import OperationAccepted, OperationDetail, OperationRequest

router = APIRouter(tags=["operations"])

Db = Annotated[AsyncSession, Depends(get_session)]
ServerId = Annotated[uuid.UUID, Path(description="Server identifier")]
OperationId = Annotated[uuid.UUID, Path(description="Operation identifier")]
#: Defence in depth only; *registration* is the control (SECURITY.md §15).
ServiceName = Annotated[
    str,
    Path(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9@._\-]{1,128}\.(service|socket|timer|target)$",
        description="A registered unit name",
    ),
]
ReadServices = Annotated[Authorized, require_permission("service", "read")]
ReadOperations = Annotated[Authorized, require_permission("operation", "read")]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=64,
        description="Client-generated key, unique per logical intent (§7).",
    ),
]


def _origin(request: Request, authorized: Authorized) -> service.Origin:
    return service.Origin(
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        method=request.method,
        path=request.url.path,
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
    )


@router.post(
    "/servers/{server_id}/services/{service_name}/operations",
    status_code=202,
    summary="Request an allowlisted operation",
    responses={
        200: {"description": "An idempotent replay: the original operation, nothing re-executed."},
        503: {"description": "Demonstration mode, no adapter, or an approval is required (M4.5)."},
    },
)
async def request_operation(
    request: Request,
    response: Response,
    server_id: ServerId,
    service_name: ServiceName,
    body: OperationRequest,
    idempotency_key: IdempotencyKey,
    db: Db,
    authorized: ReadServices,
) -> OperationAccepted:
    """§17.1. ``202`` for a new operation, ``200`` for an idempotent replay."""
    operation, replayed = await service.request_operation(
        db,
        _origin(request, authorized),
        authorized,
        server_id=server_id,
        service_name=service_name,
        body=body,
        idempotency_key=idempotency_key,
    )
    if replayed:
        response.status_code = 200
    else:
        await service.enqueue_execution(operation.id)
    return await service.accepted_view(db, operation, replayed=replayed)


@router.get("/operations/{operation_id}", dependencies=[ReadBudget], summary="Operation status")
async def read_operation(
    operation_id: OperationId, db: Db, authorized: ReadOperations
) -> OperationDetail:
    """§17.2. Unknown, or out of scope for a caller who is not the requester ⇒ ``404``."""
    return await service.read_operation(db, authorized, operation_id)


@router.post("/operations/{operation_id}/cancel", summary="Cancel before execution")
async def cancel_operation(
    request: Request, operation_id: OperationId, db: Db, authorized: ReadOperations
) -> OperationDetail:
    """§17.3. Legal only from ``PENDING`` or ``AWAITING_APPROVAL``; a running one is ``409``."""
    return await service.cancel_operation(
        db, _origin(request, authorized), authorized, operation_id
    )
