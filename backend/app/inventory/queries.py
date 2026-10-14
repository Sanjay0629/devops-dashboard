"""List queries: allowlisted filters and sorts, keyset pages, scope inside SQL.

Three jobs, all of them things that go wrong quietly when they are spread
across a handler:

1. **Scope is applied here, in the ``WHERE`` clause** (docs/SECURITY.md §20),
   via ``app/authz/queries.readable_servers``. Filtering after the fetch would
   leak the existence of hidden rows through page sizes and ``has_more``, and
   §5 is explicit that filters intersect with scope rather than widening it.
2. **Filters and sorts come from an allowlist** (§6). The maps below *are* the
   allowlist: a sort key that is not in one cannot reach the ORDER BY, and the
   ``Literal`` on the handler parameter rejects it with the permitted set
   before it gets here.
3. **Pages are keyset, not offset** (§5). ``(sort key, id)`` is compared as a
   row value, so a concurrent insert cannot shift the window and make a page
   repeat or skip a row.

**Free-text search is parameterised.** ``q`` is bound as a parameter and its
own wildcards are escaped, so ``%`` and ``_`` in user input match themselves
rather than widening the search; it is never interpolated into SQL.

**Applications are not scope-filtered**, and that is a decision rather than an
omission. An application has no environment, no cloud account and no server
(AD-12), and docs/SECURITY.md §4's assumption ``A-09`` says a grant
constraining a dimension the target does not have is a *denial* — so under the
rules as written, every realistic grant (including the bootstrap
administrator's, which names all four dimensions) reaches no application at
all, and §18.1 and §18.3 would return nothing to everybody. The working
default taken here is that ``server:read`` is the whole check for application
metadata, which costs nothing operationally: an application row carries name,
slug, owner, repository reference and health-check config, while every
privileged action is targeted at a *server* or a deployment target and is
scope-filtered on its own dimensions. Writes stay narrow regardless —
``admin:integrations`` is Administrator-only. Recorded as ``OPEN-27``; it
needs a product decision, not a silent pick.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Select, func, or_, select
from sqlalchemy.sql import ColumnExpressionArgument
from sqlalchemy.sql.elements import UnaryExpression

from app.authz.matcher import Grant
from app.authz.queries import readable_servers
from app.core.pagination import Cursor, encode_cursor
from app.inventory.models import Application, ApplicationServer, CloudAccount, Server

#: Stands in for a NULL sort key so a keyset comparison keeps working.
#: A row-value comparison against NULL evaluates to NULL and drops the row, so
#: paging would stop dead at the first server that has never been seen — which
#: at this milestone is every server, because nothing writes ``last_seen_at``
#: until the M2 collectors. A representable sentinel (rather than
#: ``-infinity``) keeps the value round-trippable through the cursor's JSON.
NULL_TIMESTAMP_SENTINEL: Final[datetime] = datetime(1, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SortSpec:
    """One allowlisted sort key: how to order by it, and how to page on it."""

    expression: ColumnExpressionArgument[Any]
    #: Reads the key out of a loaded row, so the cursor is minted from the
    #: same value the ORDER BY used.
    key_of: Callable[[Any], Any]
    #: Turns the cursor's text form back into a bindable value.
    parse: Callable[[str], Any]


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


SERVER_SORTS: Final[dict[str, SortSpec]] = {
    "display_name": SortSpec(Server.display_name, lambda row: row.display_name, str),
    "hostname": SortSpec(Server.hostname, lambda row: row.hostname, str),
    "last_seen_at": SortSpec(
        func.coalesce(Server.last_seen_at, NULL_TIMESTAMP_SENTINEL),
        lambda row: row.last_seen_at or NULL_TIMESTAMP_SENTINEL,
        _timestamp,
    ),
    "created_at": SortSpec(Server.created_at, lambda row: row.created_at, _timestamp),
    "environment": SortSpec(Server.environment, lambda row: row.environment, str),
}

APPLICATION_SORTS: Final[dict[str, SortSpec]] = {
    "name": SortSpec(Application.name, lambda row: row.name, str),
    "slug": SortSpec(Application.slug, lambda row: row.slug, str),
    "created_at": SortSpec(Application.created_at, lambda row: row.created_at, _timestamp),
}

#: Query parameter names each endpoint accepts (§6). Anything else is a
#: ``422``: a typo'd filter that is silently ignored returns the *unfiltered*
#: collection while looking like it worked.
SERVER_QUERY_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "environment",
        "provider",
        "cloud_account_id",
        "region",
        "status",
        "operating_system",
        "application_id",
        "owner_team",
        "monitoring_enabled",
        "q",
        "limit",
        "cursor",
        "sort",
        "order",
    }
)

APPLICATION_QUERY_PARAMS: Final[frozenset[str]] = frozenset(
    {"owner_team", "status", "q", "limit", "cursor", "sort", "order"}
)


def _like(term: str) -> str:
    """A prefix pattern whose own wildcards are literal.

    Without the escape, a search for ``%`` matches every row — not an
    injection, but a filter that silently stops filtering.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def _ordered(statement: Select[Any], spec: SortSpec, id_column: Any, order: str) -> Select[Any]:
    direction: Callable[[Any], UnaryExpression[Any]] = (
        (lambda column: column.desc()) if order == "desc" else (lambda column: column.asc())
    )
    # The id is the tiebreaker, in the same direction, so the pair is a total
    # order and a page boundary cannot fall inside a group of equal keys.
    return statement.order_by(direction(spec.expression), direction(id_column))


def _after_cursor(
    statement: Select[Any],
    spec: SortSpec,
    id_column: Any,
    cursor: Cursor,
) -> Select[Any]:
    """Resume after the row the cursor names, as a row-value comparison."""
    key = spec.parse(cursor.key)
    row_id = uuid.UUID(cursor.id)
    pair = func.row(spec.expression, id_column)
    boundary = func.row(key, row_id)
    return statement.where(pair > boundary if cursor.order == "asc" else pair < boundary)


def servers_query(
    grants: Sequence[Grant],
    *,
    sort: str,
    order: str,
    cursor: Cursor | None,
    limit: int,
    environment: list[str] | None = None,
    provider: list[str] | None = None,
    cloud_account_id: list[uuid.UUID] | None = None,
    region: list[str] | None = None,
    status: list[str] | None = None,
    operating_system: list[str] | None = None,
    application_id: list[uuid.UUID] | None = None,
    owner_team: list[str] | None = None,
    monitoring_enabled: bool | None = None,
    q: str | None = None,
) -> Select[Any]:
    """§14.1, scope-filtered. Fetches ``limit + 1`` rows to answer ``has_more``.

    One extra row rather than a second ``COUNT``: it answers the only question
    the page object asks about what follows, and §5 keeps ``total`` ``null`` on
    cursor pages anyway.
    """
    spec = SERVER_SORTS[sort]
    statement = select(Server).where(
        Server.deleted_at.is_(None),
        readable_servers(grants, "server", "read"),
    )

    if environment:
        statement = statement.where(Server.environment.in_(environment))
    if cloud_account_id:
        statement = statement.where(Server.cloud_account_id.in_(cloud_account_id))
    if region:
        statement = statement.where(Server.region.in_(region))
    if status:
        statement = statement.where(Server.status.in_(status))
    if operating_system:
        statement = statement.where(Server.operating_system.in_(operating_system))
    if owner_team:
        statement = statement.where(Server.owner_team.in_(owner_team))
    if monitoring_enabled is not None:
        statement = statement.where(Server.monitoring_enabled.is_(monitoring_enabled))
    if provider:
        statement = statement.where(
            Server.cloud_account_id.in_(
                select(CloudAccount.id).where(CloudAccount.provider.in_(provider))
            )
        )
    if application_id:
        statement = statement.where(
            Server.id.in_(
                select(ApplicationServer.server_id).where(
                    ApplicationServer.application_id.in_(application_id)
                )
            )
        )
    if q:
        pattern = _like(q)
        statement = statement.where(
            or_(
                Server.hostname.ilike(pattern, escape="\\"),
                Server.display_name.ilike(pattern, escape="\\"),
                Server.provider_resource_id.ilike(pattern, escape="\\"),
            )
        )

    if cursor is not None:
        statement = _after_cursor(statement, spec, Server.id, cursor)
    return _ordered(statement, spec, Server.id, order).limit(limit + 1)


def applications_query(
    *,
    sort: str,
    order: str,
    cursor: Cursor | None,
    limit: int,
    owner_team: list[str] | None = None,
    status: list[str] | None = None,
    q: str | None = None,
) -> Select[Any]:
    """§18.1. Not scope-filtered — see the module docstring and ``OPEN-27``."""
    spec = APPLICATION_SORTS[sort]
    statement = select(Application).where(Application.deleted_at.is_(None))
    if owner_team:
        statement = statement.where(Application.owner_team.in_(owner_team))
    if status:
        statement = statement.where(Application.status.in_(status))
    if q:
        pattern = _like(q)
        statement = statement.where(
            or_(
                Application.name.ilike(pattern, escape="\\"),
                Application.slug.ilike(pattern, escape="\\"),
            )
        )
    if cursor is not None:
        statement = _after_cursor(statement, spec, Application.id, cursor)
    return _ordered(statement, spec, Application.id, order).limit(limit + 1)


def page_of(
    rows: Sequence[Any],
    *,
    limit: int,
    sort: str,
    order: str,
    sorts: dict[str, SortSpec],
) -> tuple[list[Any], str | None, bool]:
    """Trim the ``limit + 1`` probe row and mint the next cursor."""
    has_more = len(rows) > limit
    items = list(rows[:limit])
    if not has_more or not items:
        return items, None, False
    last = items[-1]
    next_cursor = encode_cursor(
        key=sorts[sort].key_of(last),
        row_id=last.id,
        sort=sort,
        order=order,
    )
    return items, next_cursor, True
