"""The operations history query (docs/API_CONTRACTS.md §23, ``OPEN-107``, ``OPEN-108``).

Builds the statement and nothing else: filters in, a scoped ``SELECT`` out. The router and view
models are the next commit's.

**Whose rows.** A row is listed when the caller requested it, **or** holds ``operation:read`` over
the operation's *stored* environment and its server's account and id. Both halves are in the
``WHERE`` clause, so rows outside them are never fetched, counted or paged over. ``requested_by`` is
a filter and therefore only ever narrows: asking for another person's operations returns the ones
the caller may see, not the ones that person made.

**Why the cursor carries the filters.** The cursor pins a fingerprint of the supplied filters as
its ``sort``, as the approval queue's does: replaying a cursor against a different query is
``422 invalid_cursor`` rather than a position in the wrong result set. The *window* is not part of
it -- an omitted ``from`` slides with the clock, and a sliding lower bound can only drop old rows.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, or_, select

from app.approvals.models import ENVIRONMENTS
from app.authz.matcher import Grant
from app.authz.queries import readable_operations
from app.core.errors import field_error
from app.core.pagination import (
    Cursor,
    cursor_key,
    decode_cursor,
    encode_cursor,
    keyset_after,
    keyset_order,
)
from app.core.window import Window
from app.inventory.models import Server
from app.operations.codes import ACTION_TYPES
from app.operations.models import Operation
from app.operations.states import STATUSES

SORTS: Final[tuple[str, ...]] = ("created_at", "completed_at")
ORDERS: Final[tuple[str, ...]] = ("asc", "desc")
#: §5: ``limit`` default 50, max 200; more than the maximum is ``422``, never a clamp.
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


@dataclass(frozen=True, slots=True)
class HistoryFilters:
    """The supplied filters. Plural ones repeat (§6); the three ids are single."""

    execution_status: tuple[str, ...] = ()
    action_type: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()
    server_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    requested_by: uuid.UUID | None = None

    def validate(self) -> None:
        """``422`` for a value outside a closed set -- the allowlist, not a pattern (§6)."""
        for name, values, allowed in (
            ("execution_status", self.execution_status, STATUSES),
            ("action_type", self.action_type, ACTION_TYPES),
            ("environment", self.environment, ENVIRONMENTS),
        ):
            if any(value not in allowed for value in values):
                raise field_error(
                    ["query", name], "invalid_choice", f"Allowed: {', '.join(sorted(allowed))}."
                )

    def fingerprint(self, sort: str) -> str:
        encoded = json.dumps(
            {
                "execution_status": sorted(self.execution_status),
                "action_type": sorted(self.action_type),
                "environment": sorted(self.environment),
                "server_id": self.server_id,
                "service_id": self.service_id,
                "requested_by": self.requested_by,
                "sort": sort,
            },
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return "operations:" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


def validate_paging(sort: str, order: str, limit: int) -> None:
    """The allowlists of §6 and §5's bound on ``limit``."""
    if sort not in SORTS:
        raise field_error(["query", "sort"], "invalid_choice", f"Allowed: {', '.join(SORTS)}.")
    if order not in ORDERS:
        raise field_error(["query", "order"], "invalid_choice", "Allowed: asc, desc.")
    if not 1 <= limit <= MAX_LIMIT:
        raise field_error(["query", "limit"], "out_of_range", f"1 to {MAX_LIMIT}.")


def history_query(
    grants: list[Grant] | tuple[Grant, ...],
    caller_id: uuid.UUID,
    filters: HistoryFilters,
    window: Window,
    *,
    sort: str,
    order: str,
    cursor_token: str | None,
    limit: int,
) -> Select[Operation, Server]:
    """§23.1: ``(Operation, Server)`` rows, scoped, filtered, windowed and one page plus one.

    ``limit + 1`` rows are selected so the caller can tell ``has_more`` without a count (§5).
    """
    validate_paging(sort, order, limit)
    filters.validate()
    # The cursor is checked against the fingerprint before anything is built from it.
    cursor: Cursor | None = (
        decode_cursor(cursor_token, sort=filters.fingerprint(sort), order=order)
        if cursor_token
        else None
    )

    sort_column = Operation.completed_at if sort == "completed_at" else Operation.created_at
    nullable = sort == "completed_at"

    statement = (
        select(Operation, Server)
        .join(Server, Server.id == Operation.target_server_id)
        .where(
            or_(
                Operation.requested_by == caller_id,
                readable_operations(grants, "operation", "read"),
            )
        )
        # ``from`` / ``to`` bound ``created_at`` whichever way the page is sorted (§23).
        .where(Operation.created_at >= window.start, Operation.created_at <= window.end)
    )
    if filters.execution_status:
        statement = statement.where(Operation.execution_status.in_(filters.execution_status))
    if filters.action_type:
        statement = statement.where(Operation.action_type.in_(filters.action_type))
    if filters.environment:
        # The stored environment: what the operation was authorised against.
        statement = statement.where(Operation.environment.in_(filters.environment))
    if filters.server_id is not None:
        statement = statement.where(Operation.target_server_id == filters.server_id)
    if filters.service_id is not None:
        statement = statement.where(Operation.target_service_id == filters.service_id)
    if filters.requested_by is not None:
        statement = statement.where(Operation.requested_by == filters.requested_by)
    if cursor is not None:
        statement = statement.where(
            keyset_after(sort_column, Operation.id, cursor, order=order, nullable=nullable)
        )
    return statement.order_by(
        *keyset_order(sort_column, Operation.id, order=order, nullable=nullable)
    ).limit(limit + 1)


def next_cursor(last: Operation, filters: HistoryFilters, *, sort: str, order: str) -> str:
    """The cursor for the row a page ended on."""
    value = last.completed_at if sort == "completed_at" else last.created_at
    return encode_cursor(
        key=cursor_key(value), row_id=last.id, sort=filters.fingerprint(sort), order=order
    )
