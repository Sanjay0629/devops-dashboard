"""Reading the audit trail (docs/API_CONTRACTS.md §24.1, §24.2; ``OPEN-109`` – ``OPEN-111``).

:func:`app.audit.query.audit_query` decides *which* events, inside the SQL. This module adds the
two things reading the trail is: **redaction again on the way out**, and **an audit row about the
read**.

``AUDIT_QUERIED`` is written in the request's own transaction and committed **before** the page is
returned, so a failed write fails the read (SECURITY §18) -- nothing is handed back that was not
recorded as handed back. It records which filters were supplied (ids and enum values), the window,
how many events were returned and whether there are more; never an event, never a cursor. It is
written for an empty result too: an empty answer is still a read.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.audit.models import AuditEvent
from app.audit.query import AuditFilters, audit_event_query, audit_query, next_cursor
from app.audit.schemas import AuditEventList, AuditEventView
from app.authz.dependencies import Authorized
from app.core.redaction import redact_value
from app.core.window import Window
from app.inventory.schemas import CursorPage
from app.inventory.service import NotFoundError


@dataclass(frozen=True, slots=True)
class Origin:
    client_ip: str | None
    user_agent: str | None


def view(event: AuditEvent) -> AuditEventView:
    redacted = redact_value(event.sanitized_metadata)
    return AuditEventView(
        id=event.id,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        actor_label=event.actor_label,
        action_type=event.action_type,
        target_type=event.target_type,
        target_id=event.target_id,
        environment=event.environment,
        correlation_id=event.correlation_id,
        approval_id=event.approval_id,
        outcome=event.outcome,
        source_ip=None if event.source_ip is None else str(event.source_ip),
        user_agent=event.user_agent,
        sanitized_metadata=redacted if isinstance(redacted, dict) else {},
    )


def _record_query(
    db: AsyncSession,
    authorized: Authorized,
    origin: Origin,
    *,
    metadata: dict[str, object],
) -> None:
    audit.record(
        db,
        action_type="AUDIT_QUERIED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="audit",
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata=metadata,
    )


async def list_events(
    db: AsyncSession,
    authorized: Authorized,
    origin: Origin,
    filters: AuditFilters,
    window: Window,
    *,
    order: str,
    cursor_token: str | None,
    limit: int,
) -> AuditEventList:
    rows: Sequence[AuditEvent] = list(
        await db.scalars(
            audit_query(
                authorized.grants,
                filters,
                window,
                order=order,
                cursor_token=cursor_token,
                limit=limit,
            )
        )
    )
    has_more = len(rows) > limit
    page = list(rows[:limit])
    items = [view(event) for event in page]

    _record_query(
        db,
        authorized,
        origin,
        metadata={
            "endpoint": "list",
            "filters": filters.supplied(),
            "from": window.start.isoformat(),
            "to": window.end.isoformat(),
            "order": order,
            "limit": limit,
            "returned": len(items),
            "has_more": has_more,
        },
    )
    # Explicit, before the page is returned: if this fails the caller gets an error, not the page.
    await db.commit()
    return AuditEventList(
        items=items,
        page=CursorPage(
            limit=limit,
            next_cursor=next_cursor(page[-1], filters, order=order) if has_more and page else None,
            has_more=has_more,
        ),
    )


async def read_event(
    db: AsyncSession, authorized: Authorized, origin: Origin, event_id: uuid.UUID
) -> AuditEventView:
    """§24.2. Unknown, or outside the caller's scope ⇒ ``404`` (and nothing was read)."""
    event = (await db.scalars(audit_event_query(authorized.grants, event_id))).first()
    if event is None:
        raise NotFoundError
    item = view(event)
    _record_query(
        db,
        authorized,
        origin,
        metadata={"endpoint": "read", "event_id": str(event_id), "returned": 1},
    )
    await db.commit()
    return item
