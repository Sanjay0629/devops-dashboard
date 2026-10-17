"""Dashboard aggregation and its cache (docs/API_CONTRACTS.md §13).

Three things here are load-bearing.

**Counts are scope-filtered inside the query.** ``app/authz/queries``'s
``readable_servers`` produces the same ``WHERE`` clause §14.1 uses, so the
dashboard cannot count a server its caller may not list. Rule 7 of
docs/SECURITY.md §4 is "one matcher": this module adds no second translation
of scope, and the filters it applies intersect with that predicate rather than
widening it.

**The cache key carries the caller's resolved scope.** A cache keyed on the
filters alone would serve one operator's totals to another the moment their
filters matched — a scope leak no amount of correct SQL would catch, because
the SQL would never run. :func:`_scope_fingerprint` digests the grants that
actually feed ``readable_servers``, normalised, so two callers share an entry
only when their readable set is identical by construction.
``tests/integration/test_dashboard_cache.py`` is the test that fails if this
key ever stops depending on scope.

**Redis is an optimisation, never an authority.** A cache miss and a cache
outage are the same thing: compute from PostgreSQL. Caching fails *open*
because the fallback is the correct answer, slower — which is also this
milestone's documented rollback.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import UnaryExpression

from app.authz.matcher import Grant, normalise_scope
from app.authz.queries import readable_servers
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.pagination import Cursor, encode_cursor
from app.core.redis import get_redis
from app.dashboard.schemas import (
    ALERTS_REASON,
    DEPLOYMENTS_REASON,
    HEALTH_CHANGES_REASON,
    HEALTH_REASON,
    OPERATIONS_REASON,
    UTILISATION_REASON,
    ActiveAlertsSection,
    DashboardFilters,
    DashboardHealthList,
    DashboardSummary,
    FailedServicesSection,
    RecentActivitySection,
    ServerCounts,
    ServerHealthRollup,
    ServerHealthRow,
    UtilisationSection,
)
from app.inventory.models import CloudAccount, Server, Service
from app.inventory.queries import SERVER_SORTS, server_filter_conditions
from app.inventory.schemas import CursorPage
from app.services.listing import displayed_state

logger = get_logger(__name__)

_CACHE_PREFIX = "dcp:dashboard:"


@dataclass(frozen=True, slots=True)
class DashboardFilterSet:
    """The filters §13.1 and §13.2 share, as one value.

    One object rather than six parameters threaded through four functions,
    because it is also what the cache key is built from: anything that changes
    the answer has to change the key, and a parameter that exists in only one
    of those two places is how that stops being true.
    """

    environment: tuple[str, ...] = ()
    provider: tuple[str, ...] = ()
    region: tuple[str, ...] = ()
    application_id: tuple[uuid.UUID, ...] = ()
    operating_system: tuple[str, ...] = ()
    owner_team: tuple[str, ...] = ()

    def conditions(self) -> list[Any]:
        return server_filter_conditions(
            environment=list(self.environment) or None,
            provider=list(self.provider) or None,
            region=list(self.region) or None,
            application_id=list(self.application_id) or None,
            operating_system=list(self.operating_system) or None,
            owner_team=list(self.owner_team) or None,
        )

    def echoed(self) -> DashboardFilters:
        """§13.1's ``filters_applied``: what was applied, not what was sent."""
        return DashboardFilters(
            environment=list(self.environment) or None,
            provider=list(self.provider) or None,
            region=list(self.region) or None,
            application_id=list(self.application_id) or None,
            operating_system=list(self.operating_system) or None,
            owner_team=list(self.owner_team) or None,
        )

    def cache_part(self) -> list[Any]:
        return [
            sorted(self.environment),
            sorted(self.provider),
            sorted(self.region),
            sorted(str(value) for value in self.application_id),
            sorted(self.operating_system),
            sorted(self.owner_team),
        ]


def _scope_fingerprint(grants: Sequence[Grant]) -> str:
    """A stable digest of the grants that decide which servers are readable.

    Only ``server:read`` grants are digested, because those are exactly what
    ``readable_servers`` consumes — including the ``DENY`` rows, which change
    the readable set as much as an allow does.

    Normalised through ``normalise_scope`` and sorted, so two callers whose
    grants differ only in ordering share an entry, while a difference that
    changes a single readable row changes the digest. A hash rather than the
    scope itself: a cache key shows up in logs and in ``redis-cli --scan``,
    and a grant naming specific server ids is an inventory listing.
    """
    relevant = sorted(
        [
            grant.effect,
            sorted(
                [dimension, sorted(values)]
                for dimension, values in (normalise_scope(grant.scope) or {}).items()
            ),
        ]
        for grant in grants
        if grant.resource_type == "server" and grant.action == "read"
    )
    payload = json.dumps(relevant, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def cache_key(endpoint: str, grants: Sequence[Grant], parts: list[Any]) -> str:
    """``prefix:endpoint:scope:filters``. The scope segment is never optional."""
    digest = hashlib.sha256(
        json.dumps(parts, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:32]
    return f"{_CACHE_PREFIX}{endpoint}:{_scope_fingerprint(grants)}:{digest}"


async def cached[T: (DashboardSummary, DashboardHealthList)](key: str, model: type[T]) -> T | None:
    """A previous answer, or ``None`` for a miss *or* an unreachable Redis."""
    if get_settings().dashboard_cache_ttl_seconds <= 0:
        return None
    try:
        raw = await get_redis().get(key)
    except RedisError as error:
        # Not an error for the caller: the uncached path is correct, and a
        # dashboard that 500s because a cache blinked is worse than a slow one.
        logger.warning("dashboard_cache_unavailable", error=str(error))
        return None
    if raw is None:
        return None
    try:
        return model.model_validate_json(raw)
    except ValueError:
        # Written by an older shape of this model. Recompute rather than fail;
        # the write that follows replaces it.
        logger.info("dashboard_cache_discarded")
        return None


async def store(key: str, value: DashboardSummary | DashboardHealthList) -> None:
    ttl = get_settings().dashboard_cache_ttl_seconds
    if ttl <= 0:
        return
    try:
        await get_redis().set(key, value.model_dump_json(), ex=ttl)
    except RedisError as error:
        logger.warning("dashboard_cache_write_failed", error=str(error))


def _readable(grants: Sequence[Grant], filters: DashboardFilterSet) -> list[Any]:
    """Scope first, then the filters, as one list of conditions."""
    return [
        Server.deleted_at.is_(None),
        readable_servers(grants, "server", "read"),
        *filters.conditions(),
    ]


async def _status_counts(
    db: AsyncSession, grants: Sequence[Grant], filters: DashboardFilterSet
) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Server.status, func.count())
            .where(*_readable(grants, filters))
            .group_by(Server.status)
        )
    ).all()
    return {status: int(count) for status, count in rows}


async def _failed_service_counts(
    db: AsyncSession, grants: Sequence[Grant], filters: DashboardFilterSet
) -> tuple[int, int]:
    """``(failed, unobserved)`` over the registered units on the readable servers.

    Counted by the state a row **shows** (``services.listing.displayed_state``),
    not by the raw cache column. A unit cached ``FAILED`` on a host that has
    since gone quiet is not a failed service, it is an unknown one — and
    counting it as failed would be as wrong as the reverse. The other half is
    ``unobserved``: units that show ``UNKNOWN`` or ``UNCOLLECTED``. Without it,
    a ``0`` over a registry nobody has been able to read would sit on the page
    looking like good news (PRD §2: never healthy merely because nothing said
    otherwise).

    Scope reaches a service through its server: a service row carries no scope
    dimensions of its own, and counting one whose host the caller cannot see
    would leak the host.
    """
    shown = displayed_state(datetime.now(UTC))
    rows = (
        await db.execute(
            select(shown, func.count())
            .select_from(Service)
            .join(Server, Server.id == Service.server_id)
            .where(Service.deleted_at.is_(None), *_readable(grants, filters))
            .group_by(shown)
        )
    ).all()
    counts = {state: int(count) for state, count in rows}
    return counts.get("FAILED", 0), counts.get("UNKNOWN", 0) + counts.get("UNCOLLECTED", 0)


def _unknown_health() -> ServerHealthRollup:
    return ServerHealthRollup(
        data_state="not_configured",
        source="prometheus",
        unavailable_reason=HEALTH_REASON,
    )


def _unknown_alerts() -> ActiveAlertsSection:
    return ActiveAlertsSection(data_state="not_configured", unavailable_reason=ALERTS_REASON)


async def summary(
    db: AsyncSession, grants: Sequence[Grant], filters: DashboardFilterSet
) -> DashboardSummary:
    """§13.1, computed fresh from PostgreSQL."""
    settings = get_settings()
    counts = await _status_counts(db, grants, filters)
    failed, unobserved = await _failed_service_counts(db, grants, filters)
    # Stamped after the reads, so it describes the data rather than the request.
    collected_at = datetime.now(UTC)

    return DashboardSummary(
        servers=ServerCounts(
            total=sum(counts.values()),
            pending=counts.get("PENDING", 0),
            managed=counts.get("MANAGED", 0),
            stale=counts.get("STALE", 0),
            unreachable=counts.get("UNREACHABLE", 0),
            retired=counts.get("RETIRED", 0),
            data_state="ok",
            collected_at=collected_at,
            source="postgresql",
        ),
        server_health=_unknown_health(),
        utilisation=UtilisationSection(
            data_state="not_configured",
            source="prometheus",
            unavailable_reason=UTILISATION_REASON,
        ),
        failed_services=FailedServicesSection(
            count=failed,
            unobserved=unobserved,
            data_state="ok",
            collected_at=collected_at,
            source="postgresql",
        ),
        active_alerts=_unknown_alerts(),
        recent_deployments=RecentActivitySection(
            data_state="not_configured", unavailable_reason=DEPLOYMENTS_REASON
        ),
        recent_operations=RecentActivitySection(
            data_state="not_configured", unavailable_reason=OPERATIONS_REASON
        ),
        recent_health_changes=RecentActivitySection(
            data_state="not_configured", unavailable_reason=HEALTH_CHANGES_REASON
        ),
        filters_applied=filters.echoed(),
        demo_mode=settings.demo_mode,
        cache_ttl_seconds=settings.dashboard_cache_ttl_seconds,
    )


def _is_stale(last_seen_at: datetime | None, environment: str) -> bool | None:
    """``None`` when the host has never been seen — that is not staleness.

    The same rule as ``app/inventory/service._is_stale``; that one takes a
    ``Server`` and this one the column, because §13.2 reads rows, not
    entities.
    """
    if last_seen_at is None:
        return None
    age = datetime.now(UTC) - last_seen_at
    stale_after = get_settings().health_thresholds.for_environment(environment).stale_after_seconds
    return age.total_seconds() > stale_after


def health_rows_query(
    grants: Sequence[Grant],
    filters: DashboardFilterSet,
    *,
    sort: str,
    order: str,
    cursor: Cursor | None,
    limit: int,
) -> Select[Any]:
    """§13.2's page, with the per-server failed-unit count as a subquery.

    A correlated subquery rather than an aggregate join: the keyset comparison
    pages on ``(sort key, id)`` as a row value, and a ``GROUP BY`` would put a
    ``HAVING`` between that comparison and the rows it orders. Fetches
    ``limit + 1`` to answer ``has_more``, as §14.1 does.
    """
    failed_units = (
        select(func.count())
        .select_from(Service)
        .where(
            Service.server_id == Server.id,
            Service.deleted_at.is_(None),
            Service.last_observed_state == "FAILED",
        )
        .correlate(Server)
        .scalar_subquery()
    )
    spec = SERVER_SORTS[sort]
    statement = (
        select(Server, CloudAccount.provider, failed_units.label("failed_service_count"))
        .join(CloudAccount, CloudAccount.id == Server.cloud_account_id)
        .where(*_readable(grants, filters))
    )
    if cursor is not None:
        pair = func.row(spec.expression, Server.id)
        boundary = func.row(spec.parse(cursor.key), uuid.UUID(cursor.id))
        statement = statement.where(pair > boundary if cursor.order == "asc" else pair < boundary)
    direction: Callable[[Any], UnaryExpression[Any]] = (
        (lambda column: column.asc()) if order == "asc" else (lambda column: column.desc())
    )
    return statement.order_by(direction(spec.expression), direction(Server.id)).limit(limit + 1)


async def health_list(
    db: AsyncSession,
    grants: Sequence[Grant],
    filters: DashboardFilterSet,
    *,
    sort: str,
    order: str,
    cursor: Cursor | None,
    limit: int,
    health_status: tuple[str, ...] = (),
) -> DashboardHealthList:
    """§13.2, computed fresh from PostgreSQL.

    ``health_status`` filters a field that has no source yet, so it can only
    match nothing, and it is honoured rather than ignored: silently dropping a
    filter returns the *whole* list looking as though the filter applied. The
    ``health`` section on the response is what says why the page is empty.
    """
    settings = get_settings()
    items: list[ServerHealthRow] = []
    next_cursor: str | None = None
    has_more = False

    if not health_status:
        fetched = (
            await db.execute(
                health_rows_query(
                    grants, filters, sort=sort, order=order, cursor=cursor, limit=limit
                )
            )
        ).all()
        has_more = len(fetched) > limit
        page_rows = fetched[:limit]
        items = [
            ServerHealthRow(
                server_id=server.id,
                display_name=server.display_name,
                hostname=server.hostname,
                environment=server.environment,
                provider=provider,
                status=server.status,
                health_state=None,
                last_seen_at=server.last_seen_at,
                stale=_is_stale(server.last_seen_at, server.environment),
                failed_service_count=int(failed_count),
                active_alert_count=None,
            )
            for server, provider, failed_count in page_rows
        ]
        if has_more and page_rows:
            last = page_rows[-1][0]
            next_cursor = encode_cursor(
                key=SERVER_SORTS[sort].key_of(last), row_id=last.id, sort=sort, order=order
            )

    collected_at = datetime.now(UTC)
    return DashboardHealthList(
        items=items,
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        health=_unknown_health(),
        alerts=_unknown_alerts(),
        collected_at=collected_at,
        filters_applied=filters.echoed(),
        demo_mode=settings.demo_mode,
        cache_ttl_seconds=settings.dashboard_cache_ttl_seconds,
    )
