"""The deployment list query (``OPEN-140``; docs/API_CONTRACTS.md §21.1).

Builds the statement and nothing else: filters in, a scoped ``SELECT`` out.

**Whose rows.** A deployment is listed when the caller requested it, **or** holds
``deployment:read`` over its *stored* environment and application. Both halves are in the ``WHERE``
clause, so rows outside them are never fetched, counted or paged over. ``requested_by`` is a filter
and only narrows. This is the operations history's rule (``app.operations.history``), over the two
dimensions a deployment has.

The cursor pins a fingerprint of the supplied filters, so replaying it against a different query is
``422 invalid_cursor`` and not a position in the wrong result set. The window is not part of it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, or_, select

from app.approvals.models import ENVIRONMENTS
from app.auth.models import User
from app.authz.matcher import Grant
from app.authz.queries import readable_deployments
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
from app.deployments.codes import KINDS
from app.deployments.models import Deployment
from app.deployments.states import STATUSES
from app.inventory.models import Application

SORT: Final = "created_at"
ORDER: Final = "desc"
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


@dataclass(frozen=True, slots=True)
class DeploymentFilters:
    """The supplied filters. Plural ones repeat (§6)."""

    application_id: uuid.UUID | None = None
    environment: tuple[str, ...] = ()
    execution_status: tuple[str, ...] = ()
    requested_by: uuid.UUID | None = None
    kind: str | None = None

    def validate(self) -> None:
        """``422`` for a value outside a closed set -- the allowlist, not a pattern (§6)."""
        for name, values, allowed in (
            ("environment", self.environment, ENVIRONMENTS),
            ("execution_status", self.execution_status, STATUSES),
            ("kind", (self.kind,) if self.kind is not None else (), KINDS),
        ):
            if any(value not in allowed for value in values):
                raise field_error(
                    ["query", name], "invalid_choice", f"Allowed: {', '.join(sorted(allowed))}."
                )

    def fingerprint(self) -> str:
        encoded = json.dumps(
            {
                "application_id": self.application_id,
                "environment": sorted(self.environment),
                "execution_status": sorted(self.execution_status),
                "requested_by": self.requested_by,
                "kind": self.kind,
            },
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return "deployments:" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


def deployment_query(
    grants: list[Grant] | tuple[Grant, ...],
    caller_id: uuid.UUID,
    filters: DeploymentFilters,
    window: Window,
    *,
    cursor_token: str | None,
    limit: int,
) -> Select[Deployment, str, str]:
    """``(Deployment, application name, requester name)`` rows: one page plus one."""
    if not 1 <= limit <= MAX_LIMIT:
        raise field_error(["query", "limit"], "out_of_range", f"1 to {MAX_LIMIT}.")
    filters.validate()
    cursor: Cursor | None = (
        decode_cursor(cursor_token, sort=filters.fingerprint(), order=ORDER)
        if cursor_token
        else None
    )
    statement = (
        select(Deployment, Application.name, User.display_name)
        .join(Application, Application.id == Deployment.application_id)
        .join(User, User.id == Deployment.requested_by)
        .where(
            or_(
                Deployment.requested_by == caller_id,
                readable_deployments(grants, "deployment", "read"),
            )
        )
        .where(Deployment.created_at >= window.start, Deployment.created_at <= window.end)
    )
    if filters.application_id is not None:
        statement = statement.where(Deployment.application_id == filters.application_id)
    if filters.environment:
        # The stored environment: what the deployment was authorised against.
        statement = statement.where(Deployment.environment.in_(filters.environment))
    if filters.execution_status:
        statement = statement.where(Deployment.execution_status.in_(filters.execution_status))
    if filters.requested_by is not None:
        statement = statement.where(Deployment.requested_by == filters.requested_by)
    if filters.kind is not None:
        statement = statement.where(Deployment.kind == filters.kind)
    if cursor is not None:
        statement = statement.where(
            keyset_after(Deployment.created_at, Deployment.id, cursor, order=ORDER)
        )
    ordering = keyset_order(Deployment.created_at, Deployment.id, order=ORDER)
    return statement.order_by(*ordering).limit(limit + 1)


def next_cursor(last: Deployment, filters: DeploymentFilters) -> str:
    """The cursor for the row a page ended on."""
    return encode_cursor(
        key=cursor_key(last.created_at), row_id=last.id, sort=filters.fingerprint(), order=ORDER
    )
