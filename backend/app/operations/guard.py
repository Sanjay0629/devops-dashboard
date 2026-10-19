"""The in-flight guard for things an operation points at (``OPEN-73``).

Deregistering a service or retiring a server while an operation is pending or running against it
would leave a row nobody can finish honestly: the worker re-validates the target and would
refuse it. So the inventory writes refuse first. Kept apart from ``service.py`` so
``inventory`` can use it without importing the request path (which imports ``inventory``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.operations.models import Operation
from app.operations.states import IN_FLIGHT


class OperationInProgressError(AppError):
    status_code = 409
    code = "operation_in_progress"
    message = "An operation is already in progress for this target."


async def refuse_if_in_flight(
    db: AsyncSession,
    *,
    server_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
) -> None:
    """Raise :class:`OperationInProgressError` if a live operation targets the server or service."""
    if (server_id is None) == (service_id is None):
        raise ValueError("name exactly one of server_id and service_id")
    condition = (
        Operation.target_server_id == server_id
        if server_id is not None
        else Operation.target_service_id == service_id
    )
    live = await db.execute(
        select(Operation.id).where(condition, Operation.execution_status.in_(IN_FLIGHT)).limit(1)
    )
    if live.first() is not None:
        raise OperationInProgressError
