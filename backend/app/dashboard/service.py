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
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

from redis.exceptions import RedisError
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import UnaryExpression

from app.alerts import counts as alert_counts
from app.alerts import queries as alert_queries
from app.audit.models import AuditEvent
from app.authz.matcher import Grant, normalise_scope
from app.authz.queries import readable_servers
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.pagination import Cursor, encode_cursor
from app.core.redis import get_redis
from app.dashboard.schemas import (
    ALERTS_NOT_PERMITTED,
    DEPLOYMENTS_REASON,
    OPERATIONS_REASON,
    UTILISATION_REASON,
    ActiveAlertsSection,
    DashboardFilters,
    DashboardHealthList,
    DashboardSummary,
    FailedServicesSection,
    HealthChange,
    RecentActivitySection,
    RecentHealthChangesSection,
    ServerCounts,
    ServerHealthRollup,
    ServerHealthRow,
    UtilisationSection,
)
from app.inventory.models import CloudAccount, Server, Service
from app.inventory.queries import SERVER_SORTS, server_filter_conditions
from app.inventory.schemas import CursorPage
from app.monitoring.health import HealthState
from app.monitoring.read import Reference, current_reference, evaluate, health_state_expr
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


async def _health_rollup(
    db: AsyncSession,
    grants: Sequence[Grant],
    filters: DashboardFilterSet,
    reference: Reference,
) -> ServerHealthRollup:
    """The five health counts, or the reason there are none (never ``0``)."""
    usable = reference.usable
    if usable is None:
        return ServerHealthRollup(
            data_state=reference.data_state,
            collected_at=reference.at if reference.data_state == "stale" else None,
            source="prometheus",
            unavailable_reason=reference.reason,
        )
    state = health_state_expr(get_settings().health_thresholds, usable)
    rows = (
        await db.execute(
            select(state, func.count()).where(*_readable(grants, filters)).group_by(state)
        )
    ).all()
    counts = {name: int(count) for name, count in rows}
    return ServerHealthRollup(
        healthy=counts.get("healthy", 0),
        warning=counts.get("warning", 0),
        stale=counts.get("stale", 0),
        unreachable=counts.get("unreachable", 0),
        unknown=counts.get("unknown", 0),
        data_state="ok",
        collected_at=usable,
        source="prometheus",
    )


#: How far back and how many sweep moves the dashboard shows. A window as well
#: as a limit so the query prunes ``audit_events`` partitions instead of reading
#: all of them.
_HEALTH_CHANGES_LIMIT = 10
_HEALTH_CHANGES_WINDOW = timedelta(days=30)


async def _recent_health_changes(
    db: AsyncSession, grants: Sequence[Grant], filters: DashboardFilterSet, collected_at: datetime
) -> RecentHealthChangesSection:
    """The latest status moves the sweep made, on servers the caller can read.

    Scope reaches an audit row through its target: ``audit_events`` carries no
    scope of its own, and listing a move on a server the caller cannot see
    would leak that it exists. Audit metadata is read as data, not trusted: an
    unexpected shape degrades the field to ``null`` rather than failing the page.
    """
    known = set(get_args(HealthState))
    rows: Sequence[Any] = (
        await db.execute(
            select(
                AuditEvent.occurred_at,
                AuditEvent.sanitized_metadata,
                Server.id,
                Server.display_name,
                Server.environment,
            )
            .join(Server, Server.id == AuditEvent.target_id)
            .where(
                AuditEvent.action_type == "SERVER_STATUS_CHANGED",
                AuditEvent.target_type == "server",
                AuditEvent.occurred_at >= collected_at - _HEALTH_CHANGES_WINDOW,
                *_readable(grants, filters),
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(_HEALTH_CHANGES_LIMIT)
        )
    ).all()
    items = []
    for occurred_at, metadata, server_id, display_name, environment in rows:
        health = metadata.get("health_state")
        items.append(
            HealthChange(
                server_id=server_id,
                display_name=display_name,
                environment=environment,
                previous_status=str(metadata.get("from", "")),
                new_status=str(metadata.get("to", "")),
                health_state=health if health in known else None,
                occurred_at=occurred_at,
            )
        )
    return RecentHealthChangesSection(
        items=items, data_state="ok", collected_at=collected_at, source="postgresql"
    )


async def _alerts_section(
    db: AsyncSession, grants: Sequence[Grant], filters: DashboardFilterSet
) -> ActiveAlertsSection:
    """Open alerts by severity over the servers the caller may read and the filters keep.

    A caller who cannot read alerts is told so rather than shown zeros, and ``pipeline``
    says whether the alerting pipeline has been confirmed: a ``0`` is only good news when
    something would have told us otherwise (PRD §3).
    """
    if not alert_counts.can_read(grants):
        return ActiveAlertsSection(
            data_state="unavailable", source="postgresql", unavailable_reason=ALERTS_NOT_PERMITTED
        )
    counts = await alert_counts.open_counts(db, grants, filters.conditions())
    return ActiveAlertsSection(
        critical=counts["CRITICAL"],
        warning=counts["WARNING"],
        info=counts["INFO"],
        data_state="ok",
        collected_at=datetime.now(UTC),
        source="postgresql",
        pipeline=await alert_counts.pipeline_state(),
    )


async def summary(
    db: AsyncSession, grants: Sequence[Grant], filters: DashboardFilterSet
) -> DashboardSummary:
    """§13.1, computed fresh from PostgreSQL."""
    settings = get_settings()
    counts = await _status_counts(db, grants, filters)
    failed, unobserved = await _failed_service_counts(db, grants, filters)
    reference = await current_reference(settings)
    rollup = await _health_rollup(db, grants, filters, reference)
    # Stamped after the reads, so it describes the data rather than the request.
    collected_at = datetime.now(UTC)
    changes = await _recent_health_changes(db, grants, filters, collected_at)

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
        server_health=rollup,
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
        active_alerts=await _alerts_section(db, grants, filters),
        recent_deployments=RecentActivitySection(
            data_state="not_configured", unavailable_reason=DEPLOYMENTS_REASON
        ),
        recent_operations=RecentActivitySection(
            data_state="not_configured", unavailable_reason=OPERATIONS_REASON
        ),
        recent_health_changes=changes,
        filters_applied=filters.echoed(),
        demo_mode=settings.demo_mode,
        cache_ttl_seconds=settings.dashboard_cache_ttl_seconds,
    )


def health_rows_query(
    grants: Sequence[Grant],
    filters: DashboardFilterSet,
    *,
    sort: str,
    order: str,
    cursor: Cursor | None,
    limit: int,
    extra: Sequence[Any] = (),
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
        select(
            Server,
            CloudAccount.provider,
            failed_units.label("failed_service_count"),
            alert_queries.open_count_per_server(grants).label("active_alert_count"),
        )
        .join(CloudAccount, CloudAccount.id == Server.cloud_account_id)
        .where(*_readable(grants, filters), *extra)
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

    ``health_status`` is applied in SQL by the twin of the rule that produces
    each row's ``health_state`` (``monitoring.read``), so the page a filter
    selects is the page of rows that show that state, and the cursor pages over
    it. With no usable reference nothing can be evaluated, so the filter matches
    nothing — honoured rather than ignored, because silently dropping it would
    return the *whole* list looking as though the filter applied. The ``health``
    section is what says why the page is empty.
    """
    settings = get_settings()
    table = settings.health_thresholds
    reference = await current_reference(settings)
    items: list[ServerHealthRow] = []
    next_cursor: str | None = None
    has_more = False

    extra: list[Any] = []
    unanswerable = False
    if health_status:
        if reference.usable is None:
            unanswerable = True
        else:
            extra.append(health_state_expr(table, reference.usable).in_(health_status))

    if not unanswerable:
        fetched = (
            await db.execute(
                health_rows_query(
                    grants,
                    filters,
                    sort=sort,
                    order=order,
                    cursor=cursor,
                    limit=limit,
                    extra=extra,
                )
            )
        ).all()
        has_more = len(fetched) > limit
        page_rows = fetched[:limit]
        items = []
        for server, provider, failed_count, alert_count in page_rows:
            health = evaluate(server, table, reference)
            items.append(
                ServerHealthRow(
                    server_id=server.id,
                    display_name=server.display_name,
                    hostname=server.hostname,
                    environment=server.environment,
                    provider=provider,
                    status=server.status,
                    health_state=health.state,
                    last_seen_at=server.last_seen_at,
                    stale=health.stale,
                    failed_service_count=int(failed_count),
                    active_alert_count=None if alert_count is None else int(alert_count),
                )
            )
        if has_more and page_rows:
            last = page_rows[-1][0]
            next_cursor = encode_cursor(
                key=SERVER_SORTS[sort].key_of(last), row_id=last.id, sort=sort, order=order
            )

    collected_at = datetime.now(UTC)
    return DashboardHealthList(
        items=items,
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        health=await _health_rollup(db, grants, filters, reference),
        alerts=await _alerts_section(db, grants, filters),
        collected_at=collected_at,
        filters_applied=filters.echoed(),
        demo_mode=settings.demo_mode,
        cache_ttl_seconds=settings.dashboard_cache_ttl_seconds,
    )
