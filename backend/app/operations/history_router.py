"""Operations history, endpoints 23.1 and 23.2 (docs/API_CONTRACTS.md §23).

Both routes carry ``operation:read``; *which* operations is decided in SQL by
:func:`app.operations.history.history_query` (the requester's own, or ``operation:read`` over the
operation's stored environment). Filters only narrow. Unknown query parameters are ``422`` (§6).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.core.window import resolve_window
from app.db.session import get_session
from app.inventory.models import Server
from app.inventory.service import NotFoundError, server_target
from app.operations import history_service
from app.operations.history import DEFAULT_LIMIT, MAX_LIMIT, HistoryFilters
from app.operations.schemas import ExecutionStatus, HistoryList

router = APIRouter(tags=["operations"])

Db = Annotated[AsyncSession, Depends(get_session)]
ReadOperations = Annotated[Authorized, require_permission("operation", "read")]
Environment = Literal["development", "staging", "production", "other"]
Action = Literal["start", "stop", "restart", "status", "logs_tail"]

#: The names §23 lists, per endpoint; anything else is ``422`` naming this set.
SERVER_HISTORY_PARAMS = frozenset(
    {
        "execution_status",
        "action_type",
        "environment",
        "service_id",
        "requested_by",
        "from",
        "to",
        "sort",
        "order",
        "limit",
        "cursor",
    }
)
#: 23.2 fixes the server in the path, so a ``server_id`` query parameter is only accepted on 23.1.
HISTORY_PARAMS = SERVER_HISTORY_PARAMS | {"server_id"}


async def _history(
    request: Request,
    db: AsyncSession,
    authorized: Authorized,
    allowed: frozenset[str],
    *,
    filters: HistoryFilters,
    from_: datetime | None,
    to: datetime | None,
    sort: str,
    order: str,
    cursor: str | None,
    limit: int,
) -> HistoryList:
    reject_unknown_query_parameters(request, allowed)
    window = resolve_window(from_, to, now=history_service.now_utc())
    return await history_service.list_history(
        db,
        authorized,
        filters,
        window,
        sort=sort,
        order=order,
        cursor_token=cursor,
        limit=limit,
    )


@router.get("/operations", dependencies=[ReadBudget], summary="Operation history")
async def list_operations(
    request: Request,
    db: Db,
    authorized: ReadOperations,
    execution_status: Annotated[list[ExecutionStatus] | None, Query()] = None,
    action_type: Annotated[list[Action] | None, Query()] = None,
    environment: Annotated[list[Environment] | None, Query()] = None,
    server_id: Annotated[uuid.UUID | None, Query()] = None,
    service_id: Annotated[uuid.UUID | None, Query()] = None,
    requested_by: Annotated[uuid.UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    sort: Literal["created_at", "completed_at"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> HistoryList:
    """§23.1. Your own requests, plus those you may read; filters never widen that."""
    return await _history(
        request,
        db,
        authorized,
        HISTORY_PARAMS,
        filters=HistoryFilters(
            execution_status=tuple(execution_status or ()),
            action_type=tuple(action_type or ()),
            environment=tuple(environment or ()),
            server_id=server_id,
            service_id=service_id,
            requested_by=requested_by,
        ),
        from_=from_,
        to=to,
        sort=sort,
        order=order,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/servers/{server_id}/operations", dependencies=[ReadBudget], summary="Per-server history"
)
async def list_server_operations(
    request: Request,
    server_id: Annotated[uuid.UUID, Path(description="Server identifier")],
    db: Db,
    authorized: ReadOperations,
    execution_status: Annotated[list[ExecutionStatus] | None, Query()] = None,
    action_type: Annotated[list[Action] | None, Query()] = None,
    environment: Annotated[list[Environment] | None, Query()] = None,
    service_id: Annotated[uuid.UUID | None, Query()] = None,
    requested_by: Annotated[uuid.UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    sort: Literal["created_at", "completed_at"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> HistoryList:
    """§23.2. The server must be one the caller may read (``server:read``), else ``404``."""
    server = await db.get(Server, server_id)
    if server is None or not authorized.permits("server", "read", server_target(server)):
        raise NotFoundError
    return await _history(
        request,
        db,
        authorized,
        SERVER_HISTORY_PARAMS,
        filters=HistoryFilters(
            execution_status=tuple(execution_status or ()),
            action_type=tuple(action_type or ()),
            environment=tuple(environment or ()),
            server_id=server_id,
            service_id=service_id,
            requested_by=requested_by,
        ),
        from_=from_,
        to=to,
        sort=sort,
        order=order,
        cursor=cursor,
        limit=limit,
    )
