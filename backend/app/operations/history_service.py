"""The operations history (docs/API_CONTRACTS.md §23, ``OPEN-107``, ``OPEN-108``).

:func:`app.operations.history.history_query` decides *which* rows; this module turns one page of
them into items. The names a row needs (the unit, the requester, the decider) are fetched for the
whole page at once -- three queries, not three per row.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import Approval
from app.auth.models import User
from app.authz.dependencies import Authorized
from app.core.window import Window
from app.inventory.models import Server, Service
from app.inventory.schemas import CursorPage
from app.operations import states
from app.operations.history import HistoryFilters, history_query, next_cursor
from app.operations.models import Operation
from app.operations.schemas import (
    ActorView,
    HistoryApproval,
    HistoryItem,
    HistoryList,
    TargetView,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _lookups(
    db: AsyncSession, operations: Sequence[Operation]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, Approval], dict[uuid.UUID, str]]:
    """Unit names, the latest approval per operation, and display names for the page."""
    service_ids = {o.target_service_id for o in operations if o.target_service_id is not None}
    units: dict[uuid.UUID, str] = {}
    if service_ids:
        rows = await db.execute(
            select(Service.id, Service.service_name).where(Service.id.in_(service_ids))
        )
        units = {row[0]: row[1] for row in rows}

    approvals: dict[uuid.UUID, Approval] = {}
    if operations:
        found = await db.scalars(
            select(Approval)
            .where(
                Approval.operation_type == "SERVICE_OPERATION",
                Approval.operation_id.in_([o.id for o in operations]),
            )
            .order_by(Approval.created_at, Approval.id)
        )
        approvals = {approval.operation_id: approval for approval in found}  # the latest wins

    user_ids = {o.requested_by for o in operations}
    user_ids |= {a.approved_by for a in approvals.values() if a.approved_by is not None}
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
        names = {row[0]: row[1] for row in rows}
    return units, approvals, names


async def list_history(
    db: AsyncSession,
    authorized: Authorized,
    filters: HistoryFilters,
    window: Window,
    *,
    sort: str,
    order: str,
    cursor_token: str | None,
    limit: int,
) -> HistoryList:
    rows: Sequence[Any] = (
        await db.execute(
            history_query(
                authorized.grants,
                authorized.user.id,
                filters,
                window,
                sort=sort,
                order=order,
                cursor_token=cursor_token,
                limit=limit,
            )
        )
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    operations: list[Operation] = [row.Operation for row in page]
    units, approvals, names = await _lookups(db, operations)

    items = []
    for row in page:
        operation: Operation = row.Operation
        server: Server = row.Server
        approval = approvals.get(operation.id)
        unit = units.get(operation.target_service_id, "") if operation.target_service_id else ""
        items.append(
            HistoryItem(
                operation_id=operation.id,
                execution_status=states.public_status(operation.execution_status),  # type: ignore[arg-type]
                approval_status=operation.approval_status,  # type: ignore[arg-type]
                action_type=operation.action_type,
                target=TargetView(
                    server_id=server.id,
                    server_display_name=server.display_name,
                    service_name=unit,
                    environment=operation.environment,
                ),
                requested_by=ActorView(
                    id=operation.requested_by, display_name=names.get(operation.requested_by, "")
                ),
                approval=None
                if approval is None
                else HistoryApproval(
                    approval_id=approval.id,
                    decision=approval.decision,  # type: ignore[arg-type]
                    approved_by=None
                    if approval.approved_by is None
                    else ActorView(
                        id=approval.approved_by, display_name=names.get(approval.approved_by, "")
                    ),
                    decided_at=approval.decided_at,
                ),
                created_at=operation.created_at,
                started_at=operation.started_at,
                completed_at=operation.completed_at,
                deadline_at=operation.deadline_at,
                failure_reason=operation.failure_reason,
                correlation_id=operation.correlation_id,
                external_execution_id=operation.external_execution_id,
                observed_state_before=operation.observed_state_before,
                observed_state_after=operation.observed_state_after,
            )
        )
    return HistoryList(
        items=items,
        page=CursorPage(
            limit=limit,
            next_cursor=next_cursor(operations[-1], filters, sort=sort, order=order)
            if has_more and operations
            else None,
            has_more=has_more,
        ),
    )
