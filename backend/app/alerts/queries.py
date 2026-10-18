"""Scoped reads of ``alert_events`` (§20.1, §20.2, the dashboard) -- scope in SQL, never after.

**An alert is reached through the server it is about.** ``alert_events`` carries no scope
dimensions of its own, so a read resolves each row to a server (a server-targeted alert is
about that server, a service-targeted one about the service's server) and asks the same
``readable_servers`` predicate every other list uses. The *current* server row decides, not
the denormalised ``alert_events.environment``, which is for filtering and can be stale if a
server moves environment.

**An alert about no server** -- unlinked, a ``DCP_SYSTEM`` alert, or a target type with no
server behind it yet -- is visible **only to a grant that constrains no dimension**
(``OPEN-45``). Showing it by a claimed environment would let a payload place itself in a
scope, and an alert nobody can attribute is not one a scoped reader should be told about.

Reads need no soft-delete filter on the server: a retired server's alert history stays
readable by whoever could read it while it lived.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, and_, case, false, func, or_, select, true

from app.alerts.lifecycle import OPEN_STATUSES
from app.alerts.models import AlertEvent
from app.approvals.models import ENVIRONMENTS
from app.authz.matcher import WILDCARD, Grant, normalise_scope
from app.authz.queries import readable_servers
from app.core.pagination import Cursor
from app.inventory.models import Server, Service

#: Query parameters the list endpoint accepts (§6): anything else is a ``422``.
ALERT_QUERY_PARAMS: Final = frozenset(
    {
        "status",
        "severity",
        "environment",
        "target_type",
        "target_id",
        "from",
        "to",
        "q",
        "cursor",
        "limit",
    }
)


def unconstrained(grants: Sequence[Grant], resource_type: str, action: str) -> bool:
    """Does any grant for this permission constrain no dimension at all?

    True only for an ``ALLOW`` whose every named dimension is ``["*"]`` -- with one
    equivalence: ``environment`` may instead list **all four** environments, because that is
    how a full scope is written (``["*"]`` never reaches production, rule 4, so the
    administrator's grant names it) -- an empty or malformed scope grants nothing
    (``readable_servers`` agrees), and only when **no** ``DENY`` exists for the permission:
    with a deny in play the answer is no, because this function cannot say what the deny
    excludes.
    """
    relevant = [g for g in grants if g.resource_type == resource_type and g.action == action]
    if any(g.effect == "DENY" for g in relevant):
        return False
    for grant in relevant:
        scope = normalise_scope(grant.scope)
        if (
            grant.effect == "ALLOW"
            and scope
            and all(_covers_everything(name, values) for name, values in scope.items())
        ):
            return True
    return False


def _covers_everything(dimension: str, values: frozenset[str]) -> bool:
    if values == {WILDCARD}:
        return True
    return dimension == "environment" and set(ENVIRONMENTS) <= values


def server_of_alert() -> ColumnElement[Any]:
    """The server an alert is about, or ``NULL``."""
    of_service = (
        select(Service.server_id)
        .where(Service.id == AlertEvent.target_id)
        .correlate(AlertEvent)
        .scalar_subquery()
    )
    return case(
        (AlertEvent.target_type == "server", AlertEvent.target_id),
        (AlertEvent.target_type == "service", of_service),
        else_=None,
    )


def alert_scope(grants: Sequence[Grant]) -> ColumnElement[bool]:
    """The condition an alert row must satisfy to be readable by these grants."""
    server = server_of_alert()
    reachable = server.in_(select(Server.id).where(readable_servers(grants, "alert", "read")))
    sees_unlinked = true() if unconstrained(grants, "alert", "read") else false()
    return or_(reachable, and_(server.is_(None), sees_unlinked))


def _like(text_: str) -> str:
    escaped = text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def filter_conditions(
    *,
    statuses: Sequence[str],
    severities: Sequence[str] | None,
    environment: Sequence[str] | None,
    target_type: str | None,
    target_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    q: str | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [AlertEvent.status.in_(list(statuses))]
    if severities:
        conditions.append(AlertEvent.severity.in_(list(severities)))
    if environment:
        conditions.append(AlertEvent.environment.in_(list(environment)))
    if target_type is not None:
        conditions.append(AlertEvent.target_type == target_type)
    if target_id is not None:
        conditions.append(AlertEvent.target_id == target_id)
    if start is not None:
        conditions.append(AlertEvent.last_seen_at >= start)
    if end is not None:
        conditions.append(AlertEvent.last_seen_at <= end)
    if q:
        pattern = _like(q)
        conditions.append(
            or_(
                AlertEvent.summary.ilike(pattern, escape="\\"),
                AlertEvent.rule_name.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def list_query(
    grants: Sequence[Grant],
    *,
    conditions: Sequence[ColumnElement[bool]],
    cursor: Cursor | None,
    limit: int,
) -> Select[AlertEvent]:
    """Newest activity first, keyset-paged on ``(last_seen_at, id)``; ``limit + 1`` rows."""
    statement = select(AlertEvent).where(alert_scope(grants), *conditions)
    if cursor is not None:
        boundary = func.row(datetime.fromisoformat(cursor.key), uuid.UUID(cursor.id))
        statement = statement.where(func.row(AlertEvent.last_seen_at, AlertEvent.id) < boundary)
    return statement.order_by(AlertEvent.last_seen_at.desc(), AlertEvent.id.desc()).limit(limit + 1)


def one_query(grants: Sequence[Grant], alert_id: uuid.UUID) -> Select[AlertEvent]:
    return select(AlertEvent).where(alert_scope(grants), AlertEvent.id == alert_id)


def open_counts_query(
    grants: Sequence[Grant], server_conditions: Sequence[ColumnElement[bool]]
) -> Select[str, int]:
    """Open alerts by severity over the readable servers that also match the filters.

    Unlinked alerts have no server for the dashboard's filters to apply to, so they are
    counted only when no filter narrows the view (an empty ``server_conditions``) and the
    caller's scope allows them.
    """
    server = server_of_alert()
    through_server = server.in_(
        select(Server.id).where(
            Server.deleted_at.is_(None),
            readable_servers(grants, "alert", "read"),
            *server_conditions,
        )
    )
    sees_unlinked = (
        and_(server.is_(None), true())
        if not server_conditions and unconstrained(grants, "alert", "read")
        else false()
    )
    return (
        select(AlertEvent.severity, func.count())
        .where(AlertEvent.status.in_(OPEN_STATUSES), or_(through_server, sees_unlinked))
        .group_by(AlertEvent.severity)
    )


def open_count_per_server(grants: Sequence[Grant]) -> ColumnElement[Any]:
    """A per-``Server`` count of open alerts, for use as a correlated column.

    ``NULL`` -- not ``0`` -- when the caller cannot read alerts for that server: "you may
    not see alerts here" and "there are none" are different answers (PRD §3).
    """
    counted = (
        select(func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.status.in_(OPEN_STATUSES), server_of_alert() == Server.id)
        .correlate(Server)
        .scalar_subquery()
    )
    readable = Server.id.in_(select(Server.id).where(readable_servers(grants, "alert", "read")))
    return case((readable, counted), else_=None)
