"""The audit trail query (docs/API_CONTRACTS.md §24, ``OPEN-109``, ``OPEN-111``).

Builds the statement and nothing else. The router, the view model, the second redaction and the
``AUDIT_QUERIED`` row are the next commit's; the export task (commit 4) reuses :func:`audit_query`
so the file and the screen are the same arithmetic.

**Scope.** ``audit:read`` is matched on the event's ``environment`` alone, inside the ``WHERE``
clause (``readable_audit_events``): a grant constraining a ``cloud_account``, ``server`` or
``application`` reaches no event, and an event with no environment is reached only by a grant that
constrains nothing. Rows outside it are never fetched, counted or paged over.

**Filters are closed.** Enums and UUIDs only; ``action_type`` is an exact match on a bounded token
(the catalogue is extensible, so it is not an enum) and never a pattern. There is no free-text
search and no search of ``sanitized_metadata``.

**Order** is ``(occurred_at, id)`` -- the table's primary key and its index order -- and the window
is a range on ``occurred_at``, which is what lets PostgreSQL skip the monthly partitions outside it.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, select

from app.approvals.models import ENVIRONMENTS
from app.audit.models import ACTOR_TYPES, OUTCOMES, TARGET_TYPES, AuditEvent
from app.authz.matcher import Grant
from app.authz.queries import readable_audit_events
from app.core.errors import field_error
from app.core.pagination import (
    Cursor,
    decode_cursor,
    encode_cursor,
    keyset_after,
    keyset_order,
)
from app.core.window import Window
from app.operations.history import MAX_LIMIT, ORDERS

ACTION_TYPE_TOKEN: Final = re.compile(r"^[A-Z_]{1,64}$")


@dataclass(frozen=True, slots=True)
class AuditFilters:
    actor_id: uuid.UUID | None = None
    actor_type: str | None = None
    action_type: str | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    environment: str | None = None
    outcome: str | None = None
    correlation_id: uuid.UUID | None = None

    def validate(self) -> None:
        for name, value, allowed in (
            ("actor_type", self.actor_type, ACTOR_TYPES),
            ("target_type", self.target_type, TARGET_TYPES),
            ("environment", self.environment, ENVIRONMENTS),
            ("outcome", self.outcome, OUTCOMES),
        ):
            if value is not None and value not in allowed:
                raise field_error(
                    ["query", name], "invalid_choice", f"Allowed: {', '.join(sorted(allowed))}."
                )
        if self.action_type is not None and not ACTION_TYPE_TOKEN.fullmatch(self.action_type):
            raise field_error(
                ["query", "action_type"], "invalid_format", "Upper-case letters and underscores."
            )

    def supplied(self) -> dict[str, str]:
        """The filters actually given, as plain text -- ids and enum values, never content.

        This is what ``AUDIT_QUERIED`` records (``OPEN-110``) and what the cursor pins.
        """
        return {
            name: str(value)
            for name, value in (
                ("actor_id", self.actor_id),
                ("actor_type", self.actor_type),
                ("action_type", self.action_type),
                ("target_type", self.target_type),
                ("target_id", self.target_id),
                ("environment", self.environment),
                ("outcome", self.outcome),
                ("correlation_id", self.correlation_id),
            )
            if value is not None
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(self.supplied(), sort_keys=True, separators=(",", ":"))
        return "audit:" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


def audit_query(
    grants: list[Grant] | tuple[Grant, ...],
    filters: AuditFilters,
    window: Window,
    *,
    order: str,
    cursor_token: str | None,
    limit: int,
) -> Select[AuditEvent]:
    """§24.1: the visible events in the window, one page plus one (``has_more`` without a count)."""
    if order not in ORDERS:
        raise field_error(["query", "order"], "invalid_choice", "Allowed: asc, desc.")
    if not 1 <= limit <= MAX_LIMIT:
        raise field_error(["query", "limit"], "out_of_range", f"1 to {MAX_LIMIT}.")
    filters.validate()
    cursor: Cursor | None = (
        decode_cursor(cursor_token, sort=filters.fingerprint(), order=order)
        if cursor_token
        else None
    )

    statement = (
        select(AuditEvent)
        .where(readable_audit_events(grants))
        .where(AuditEvent.occurred_at >= window.start, AuditEvent.occurred_at <= window.end)
    )
    for column, value in (
        (AuditEvent.actor_id, filters.actor_id),
        (AuditEvent.actor_type, filters.actor_type),
        (AuditEvent.action_type, filters.action_type),
        (AuditEvent.target_type, filters.target_type),
        (AuditEvent.target_id, filters.target_id),
        (AuditEvent.environment, filters.environment),
        (AuditEvent.outcome, filters.outcome),
        (AuditEvent.correlation_id, filters.correlation_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if cursor is not None:
        statement = statement.where(
            keyset_after(AuditEvent.occurred_at, AuditEvent.id, cursor, order=order)
        )
    return statement.order_by(
        *keyset_order(AuditEvent.occurred_at, AuditEvent.id, order=order)
    ).limit(limit + 1)


def audit_event_query(
    grants: list[Grant] | tuple[Grant, ...], event_id: uuid.UUID
) -> Select[AuditEvent]:
    """§24.2: one event, only if the caller's scope reaches it (otherwise no row -- ``404``)."""
    return select(AuditEvent).where(AuditEvent.id == event_id, readable_audit_events(grants))


def next_cursor(last: AuditEvent, filters: AuditFilters, *, order: str) -> str:
    return encode_cursor(
        key=last.occurred_at, row_id=last.id, sort=filters.fingerprint(), order=order
    )
