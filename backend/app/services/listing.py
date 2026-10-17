"""§16.1/§16.2 list queries: scope in SQL, keyset pages, and a filterable *displayed* state.

**Why the state filter is a SQL expression.** ``state=FAILED`` has to filter on
what the row will *show*, not on the raw cache column. A service whose host has
since gone quiet is cached ``FAILED`` and displayed ``UNKNOWN``; filtering on
the column would list it under "failed" and then render it as unknown, and
paging over a filter applied after the fetch would shift every page boundary.

So :func:`displayed_state` is :func:`app.services.normalise.view_cached` written
as a ``CASE``. That is **a translation of the rule, not a second decision**, the
same arrangement as ``app/authz/queries.py``'s scope predicate, and it is held
to the same standard: ``tests/integration/test_services_listing.py`` runs a table
of rows through both and fails if they ever disagree. If the order in
``view_cached`` changes, that test is what fails.

Scope is applied here, in the ``WHERE`` clause (docs/SECURITY.md §20), by the
same ``readable_servers`` the server list uses, so a service on a server the
caller cannot read is never fetched, counted or paged over.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, Select, case, not_, or_, select

from app.approvals.models import ApprovalPolicy
from app.authz.matcher import Grant
from app.authz.queries import readable_servers
from app.core.pagination import Cursor
from app.inventory.models import Server, Service, ServiceActionPolicy
from app.inventory.queries import SortSpec, _after_cursor, _like, _ordered
from app.services.normalise import CACHE_STALE_AFTER_SECONDS, LINUX, STATE_MAP

#: ``(Service, Server, ServiceActionPolicy | None, ApprovalPolicy | None)``.
SORTS: Final[dict[str, SortSpec]] = {
    "service_name": SortSpec(Service.service_name, lambda row: row[0].service_name, str),
    "server": SortSpec(Server.display_name, lambda row: row[1].display_name, str),
}

#: Query parameters each list accepts (§6). Anything else is a ``422``.
ALL_QUERY_PARAMS: Final = frozenset(
    {"state", "environment", "application_id", "server_id", "q", "limit", "cursor", "sort", "order"}
)
SERVER_QUERY_PARAMS: Final = frozenset(
    {"state", "application_id", "q", "limit", "cursor", "sort", "order"}
)


def displayed_state(now: datetime) -> ColumnElement[str]:
    """``view_cached``'s verdict as a ``CASE``. Keep in step with it."""
    stale_before = now - timedelta(seconds=CACHE_STALE_AFTER_SECONDS)
    return case(
        (Server.operating_system != LINUX, "UNSUPPORTED"),
        (
            or_(
                not_(Server.monitoring_enabled),
                not_(Service.monitoring_enabled),
                Server.metrics_instance.is_(None),
            ),
            "UNKNOWN",
        ),
        (Service.last_observed_at.is_(None), "UNKNOWN"),
        (
            or_(Server.agent_status == "UNREACHABLE", Service.last_observed_at < stale_before),
            "UNKNOWN",
        ),
        (Service.last_observed_state.in_(sorted(STATE_MAP.values())), Service.last_observed_state),
        (Service.last_observed_state == "UNCOLLECTED", "UNCOLLECTED"),
        else_="UNKNOWN",
    )


def services_query(
    grants: Sequence[Grant],
    *,
    now: datetime,
    sort: str,
    order: str,
    cursor: Cursor | None,
    limit: int,
    state: list[str] | None = None,
    environment: list[str] | None = None,
    application_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
    q: str | None = None,
) -> Select[Any]:
    """Scope-filtered, keyset-paged. Fetches ``limit + 1`` rows to answer ``has_more``."""
    spec = SORTS[sort]
    statement = cast(
        "Select[Any]",
        select(Service, Server, ServiceActionPolicy, ApprovalPolicy)
        .join(Server, Server.id == Service.server_id)
        .outerjoin(ServiceActionPolicy, ServiceActionPolicy.id == Service.action_policy_id)
        .outerjoin(ApprovalPolicy, ApprovalPolicy.id == ServiceActionPolicy.approval_policy_id)
        .where(
            Service.deleted_at.is_(None),
            Server.deleted_at.is_(None),
            readable_servers(grants, "service", "read"),
        ),
    )
    if state:
        statement = statement.where(displayed_state(now).in_(state))
    if environment:
        statement = statement.where(Server.environment.in_(environment))
    if application_id is not None:
        statement = statement.where(Service.application_id == application_id)
    if server_id is not None:
        statement = statement.where(Service.server_id == server_id)
    if q:
        pattern = _like(q)
        statement = statement.where(
            or_(
                Service.service_name.ilike(pattern, escape="\\"),
                Service.display_name.ilike(pattern, escape="\\"),
            )
        )
    if cursor is not None:
        statement = _after_cursor(statement, spec, Service.id, cursor)
    return _ordered(statement, spec, Service.id, order).limit(limit + 1)
