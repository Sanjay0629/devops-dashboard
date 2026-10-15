"""Dashboard endpoints 13.1 and 13.2 (docs/API_CONTRACTS.md §13).

Both are reads: `dashboard:read`, the read-class rate budget, and **no audit**
(§13 says so explicitly — an audit row per dashboard poll would bury the
privileged operations the audit log exists for).

Scope is not re-checked here. It is applied inside the query by
``app/authz/queries.readable_servers``, which is the §20 requirement: a
counter cannot be filtered after the fact without the count itself having
leaked. The handler's job is to validate the query string and hand the
caller's grants to the service unchanged.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.core.pagination import Cursor, decode_cursor
from app.dashboard import service
from app.dashboard.schemas import DashboardHealthList, DashboardSummary, HealthStatusFilter
from app.dashboard.service import DashboardFilterSet
from app.db.session import get_session
from app.inventory.schemas import Environment, OperatingSystem

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

Db = Annotated[AsyncSession, Depends(get_session)]
ReadDashboard = Annotated[Authorized, require_permission("dashboard", "read")]

Provider = Literal["AWS", "AZURE", "ON_PREM", "OTHER"]
Order = Literal["asc", "desc"]
HealthSort = Literal["display_name", "hostname", "last_seen_at", "created_at", "environment"]

#: §13.1's query string. Anything else is a ``422`` rather than ignored: a
#: typo'd filter that is silently dropped returns the *unfiltered* totals
#: while looking as though it worked, which on a dashboard is a number an
#: operator will act on.
SUMMARY_QUERY_PARAMS = frozenset(
    {"environment", "provider", "region", "application_id", "operating_system", "owner_team"}
)
HEALTH_QUERY_PARAMS = SUMMARY_QUERY_PARAMS | {"health_status", "limit", "cursor", "sort", "order"}


def _filters(
    environment: list[str] | None,
    provider: list[str] | None,
    region: list[str] | None,
    application_id: list[uuid.UUID] | None,
    operating_system: list[str] | None,
    owner_team: list[str] | None,
) -> DashboardFilterSet:
    return DashboardFilterSet(
        environment=tuple(environment or ()),
        provider=tuple(provider or ()),
        region=tuple(region or ()),
        application_id=tuple(application_id or ()),
        operating_system=tuple(operating_system or ()),
        owner_team=tuple(owner_team or ()),
    )


# --- 13.1 --------------------------------------------------------------------


@router.get("/summary", dependencies=[ReadBudget], summary="Dashboard aggregate counters")
async def dashboard_summary(
    request: Request,
    db: Db,
    authorized: ReadDashboard,
    environment: Annotated[list[Environment] | None, Query()] = None,
    provider: Annotated[list[Provider] | None, Query()] = None,
    region: Annotated[list[str] | None, Query(max_length=64)] = None,
    application_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    operating_system: Annotated[list[OperatingSystem] | None, Query()] = None,
    owner_team: Annotated[list[str] | None, Query(max_length=128)] = None,
) -> DashboardSummary:
    """§13.1. Every section says which source it came from, or that it has none."""
    reject_unknown_query_parameters(request, SUMMARY_QUERY_PARAMS)
    filters = _filters(
        list(environment) if environment else None,
        list(provider) if provider else None,
        region,
        application_id,
        list(operating_system) if operating_system else None,
        owner_team,
    )
    key = service.cache_key("summary", authorized.grants, filters.cache_part())
    hit = await service.cached(key, DashboardSummary)
    if hit is not None:
        return hit
    payload = await service.summary(db, authorized.grants, filters)
    await service.store(key, payload)
    return payload


# --- 13.2 --------------------------------------------------------------------


@router.get("/health", dependencies=[ReadBudget], summary="Server health breakdown")
async def dashboard_health(
    request: Request,
    db: Db,
    authorized: ReadDashboard,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    sort: HealthSort = "display_name",
    order: Order = "asc",
    environment: Annotated[list[Environment] | None, Query()] = None,
    provider: Annotated[list[Provider] | None, Query()] = None,
    region: Annotated[list[str] | None, Query(max_length=64)] = None,
    application_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    operating_system: Annotated[list[OperatingSystem] | None, Query()] = None,
    owner_team: Annotated[list[str] | None, Query(max_length=128)] = None,
    health_status: Annotated[list[HealthStatusFilter] | None, Query()] = None,
) -> DashboardHealthList:
    """§13.2. Scope-filtered and cursor-paged, like §14.1 and for the same reasons."""
    reject_unknown_query_parameters(request, HEALTH_QUERY_PARAMS)
    filters = _filters(
        list(environment) if environment else None,
        list(provider) if provider else None,
        region,
        application_id,
        list(operating_system) if operating_system else None,
        owner_team,
    )
    decoded: Cursor | None = (
        None if cursor is None else decode_cursor(cursor, sort=sort, order=order)
    )
    statuses = tuple(health_status or ())
    # The cursor is part of the key: two pages of the same filters are two
    # different answers, and serving page 1's payload for page 2 would be a
    # silent truncation rather than a visible error.
    key = service.cache_key(
        "health",
        authorized.grants,
        [*filters.cache_part(), sorted(statuses), limit, cursor, sort, order],
    )
    hit = await service.cached(key, DashboardHealthList)
    if hit is not None:
        return hit
    payload = await service.health_list(
        db,
        authorized.grants,
        filters,
        sort=sort,
        order=order,
        cursor=decoded,
        limit=limit,
        health_status=statuses,
    )
    await service.store(key, payload)
    return payload
