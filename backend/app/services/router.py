"""systemd services, endpoints 16.1–16.7 (docs/API_CONTRACTS.md §16).

Reads (16.1, 16.2, 16.4) and registration (16.3, 16.5, 16.6). **Nothing here
executes anything on a host**: unit state is read from metrics, and the closed
action enum of §17 is a different group, not built yet.

Permissions, every one explicit on its route (mandatory rule 4):

* reads — ``service:read``, checked against the **server** the unit is on, so a
  caller scoped to staging servers sees no production unit and gets ``404``,
  never ``403`` (§2);
* registration — ``server:write``, the contract's permission, again against the
  server's scope;
* logs — ``logs:read:system`` (renamed from the seeded ``logs:query`` by
  migration 0007, OPEN-32). The route's permission denial is audited by the dependency; a scope
  miss is a ``404``.

**An action policy is a privilege, and only an Administrator may assign or
change one** (§16.5). That is checked in the handler, not by a second
permission on the route, for the reason ``PATCH /servers`` checks an
environment change there: the route's permission is ``server:write`` and only
one field needs more. ``admin:users`` is the stand-in for "is an
Administrator", exactly as in ``inventory/router_servers.py``, and a refusal
reads as ``404`` for the same reason it does there.

**16.7 is answered by the log engine (M3.2).** It is the same code as
``POST /logs/query`` (``app/logs/service.py``) restricted to one registered
unit's journald lines, so scope, redaction, the ingestion state and the
"unavailable is never empty" rule are not reimplemented here. With no Loki
configured it answers ``503 not_configured`` -- after the permission, scope,
registration and parameter checks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.config import get_settings
from app.core.errors import field_error
from app.core.filters import reject_unknown_query_parameters
from app.core.logging import get_logger
from app.core.pagination import Cursor, decode_cursor, encode_cursor
from app.db.session import get_session
from app.inventory.models import SERVICE_NAME_PATTERN, Server, Service
from app.inventory.schemas import CursorPage, Environment
from app.inventory.service import NotFoundError
from app.logs import service as logs_service
from app.logs.schemas import LogSearchResponse
from app.services import listing, service
from app.services.normalise import ServiceState
from app.services.schemas import (
    LogLevel,
    ServiceDetail,
    ServiceItem,
    ServiceList,
    ServiceRegisterRequest,
    ServiceUpdateRequest,
)

logger = get_logger(__name__)

router = APIRouter(tags=["services"])

Db = Annotated[AsyncSession, Depends(get_session)]
ServerId = Annotated[uuid.UUID, Path(description="Server identifier")]
#: Defence in depth only; *registration* is the control (§16, SECURITY.md §15).
ServiceName = Annotated[
    str,
    Path(min_length=3, max_length=128, pattern=SERVICE_NAME_PATTERN, description="Unit name"),
]
ReadServices = Annotated[Authorized, require_permission("service", "read")]
WriteServers = Annotated[Authorized, require_permission("server", "write")]
QueryLogs = Annotated[Authorized, require_permission("logs", "read:system")]

Sort = Literal["service_name", "server"]
Order = Literal["asc", "desc"]

#: §16.7: at most a day, and at most a thousand lines.
MAX_LOG_RANGE = timedelta(hours=24)
LOG_QUERY_PARAMS = frozenset({"limit", "from", "to", "level"})


def _cursor(value: str | None, *, sort: str, order: str) -> Cursor | None:
    return None if value is None else decode_cursor(value, sort=sort, order=order)


async def _page(
    db: AsyncSession,
    authorized: Authorized,
    *,
    sort: str,
    order: str,
    limit: int,
    cursor: str | None,
    state: list[ServiceState] | None,
    environment: list[Environment] | None = None,
    application_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
    q: str | None = None,
) -> ServiceList:
    now = datetime.now(UTC)
    statement = listing.services_query(
        authorized.grants,
        now=now,
        sort=sort,
        order=order,
        cursor=_cursor(cursor, sort=sort, order=order),
        limit=limit,
        state=list(state) if state else None,
        environment=list(environment) if environment else None,
        application_id=application_id,
        server_id=server_id,
        q=q,
    )
    rows = (await db.execute(statement)).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            key=listing.SORTS[sort].key_of(last), row_id=last[0].id, sort=sort, order=order
        )
    return ServiceList(
        items=[
            service.cached_item(row[0], row[1], row[2], row[3], authorized, now=now) for row in page
        ],
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        demo_mode=get_settings().demo_mode,
    )


def _require_administrator(authorized: Authorized) -> None:
    if not authorized.permits("admin", "users"):
        raise NotFoundError


def _audit(
    db: AsyncSession,
    request: Request,
    authorized: Authorized,
    action_type: str,
    server: Server,
    row: Service,
    metadata: dict[str, Any],
) -> None:
    audit.record(
        db,
        action_type=action_type,
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="service",
        target_id=row.id,
        environment=server.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"server_id": str(server.id), "service_name": row.service_name, **metadata},
    )


# --- 16.1 / 16.2 -------------------------------------------------------------


@router.get("/services", dependencies=[ReadBudget], summary="Cross-server service list")
async def list_services(
    request: Request,
    db: Db,
    authorized: ReadServices,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    sort: Sort = "service_name",
    order: Order = "asc",
    state: Annotated[list[ServiceState] | None, Query()] = None,
    environment: Annotated[list[Environment] | None, Query()] = None,
    application_id: Annotated[uuid.UUID | None, Query()] = None,
    server_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> ServiceList:
    """§16.1. Scope-filtered in SQL; ``state`` filters on the state a row *shows*."""
    reject_unknown_query_parameters(request, listing.ALL_QUERY_PARAMS)
    return await _page(
        db,
        authorized,
        sort=sort,
        order=order,
        limit=limit,
        cursor=cursor,
        state=state,
        environment=environment,
        application_id=application_id,
        server_id=server_id,
        q=q,
    )


@router.get(
    "/servers/{server_id}/services", dependencies=[ReadBudget], summary="Services on one server"
)
async def list_server_services(
    request: Request,
    server_id: ServerId,
    db: Db,
    authorized: ReadServices,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    sort: Sort = "service_name",
    order: Order = "asc",
    state: Annotated[list[ServiceState] | None, Query()] = None,
    application_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> ServiceList:
    """§16.2. An unknown or out-of-scope server is ``404``, not an empty page."""
    reject_unknown_query_parameters(request, listing.SERVER_QUERY_PARAMS)
    server = await service.load_server(db, server_id, authorized, "service", "read")
    return await _page(
        db,
        authorized,
        sort=sort,
        order=order,
        limit=limit,
        cursor=cursor,
        state=state,
        application_id=application_id,
        server_id=server.id,
        q=q,
    )


# --- 16.3 --------------------------------------------------------------------


@router.post("/servers/{server_id}/services", status_code=201, summary="Register a service")
async def register_service(
    request: Request,
    server_id: ServerId,
    body: ServiceRegisterRequest,
    db: Db,
    authorized: WriteServers,
) -> ServiceItem:
    """§16.3. Registering adds a unit to the allowlist; it grants no action by itself."""
    server = await service.load_server(db, server_id, authorized, "server", "write")
    if body.action_policy_id is not None:
        _require_administrator(authorized)
    row = await service.register_service(db, server, body)
    _audit(
        db,
        request,
        authorized,
        "SERVICE_REGISTERED",
        server,
        row,
        {
            "application_id": None if row.application_id is None else str(row.application_id),
            "action_policy_id": None if row.action_policy_id is None else str(row.action_policy_id),
            "monitoring_enabled": row.monitoring_enabled,
        },
    )
    await db.commit()
    await db.refresh(row)
    logger.info("service_registered", service_id=str(row.id), environment=server.environment)
    policy, approval = await service.policy_context(db, row)
    return service.cached_item(row, server, policy, approval, authorized, now=datetime.now(UTC))


# --- 16.4 --------------------------------------------------------------------


@router.get(
    "/servers/{server_id}/services/{service_name}",
    dependencies=[ReadBudget],
    summary="Service detail (a live reading)",
)
async def read_service(
    server_id: ServerId,
    service_name: ServiceName,
    db: Db,
    authorized: ReadServices,
) -> ServiceDetail:
    """§16.4. The one read that asks Prometheus now."""
    server = await service.load_server(db, server_id, authorized, "service", "read")
    row = await service.load_service(db, server, service_name)
    return await service.live_detail(db, server, row, authorized)


# --- 16.5 --------------------------------------------------------------------


@router.patch(
    "/servers/{server_id}/services/{service_name}", summary="Edit monitoring or action policy"
)
async def update_service(
    request: Request,
    server_id: ServerId,
    service_name: ServiceName,
    body: ServiceUpdateRequest,
    db: Db,
    authorized: WriteServers,
) -> ServiceItem:
    """§16.5. Changing the action policy is Administrator-only and audited on its own."""
    server = await service.load_server(db, server_id, authorized, "server", "write")
    row = await service.load_service(db, server, service_name)
    previous_policy = row.action_policy_id
    if "action_policy_id" in body.model_fields_set and body.action_policy_id != previous_policy:
        _require_administrator(authorized)
    changes = await service.update_service(db, row, body)
    if changes:
        _audit(db, request, authorized, "SERVICE_UPDATED", server, row, {"changes": changes})
    if "action_policy_id" in changes:
        _audit(
            db,
            request,
            authorized,
            "SERVICE_ACTION_POLICY_CHANGED",
            server,
            row,
            {
                "from": None if previous_policy is None else str(previous_policy),
                "to": None if row.action_policy_id is None else str(row.action_policy_id),
            },
        )
        logger.warning("service_action_policy_changed", service_id=str(row.id))
    await db.commit()
    await db.refresh(row)
    policy, approval = await service.policy_context(db, row)
    return service.cached_item(row, server, policy, approval, authorized, now=datetime.now(UTC))


# --- 16.6 --------------------------------------------------------------------


@router.delete(
    "/servers/{server_id}/services/{service_name}",
    status_code=204,
    summary="Deregister a service (soft delete)",
)
async def deregister_service(
    request: Request,
    server_id: ServerId,
    service_name: ServiceName,
    db: Db,
    authorized: WriteServers,
) -> None:
    """§16.6. History is retained; the in-flight-operation guard arrives with ``operations``."""
    server = await service.load_server(db, server_id, authorized, "server", "write")
    row = await service.load_service(db, server, service_name)
    await service.deregister_service(db, row)
    _audit(db, request, authorized, "SERVICE_DEREGISTERED", server, row, {})
    await db.commit()
    logger.info("service_deregistered", service_id=str(row.id))


# --- 16.7 --------------------------------------------------------------------


@router.get(
    "/servers/{server_id}/services/{service_name}/logs",
    dependencies=[ReadBudget],
    summary="Recent service logs",
    responses={
        503: {"description": "Loki is unavailable or not configured. Never an empty page."},
        504: {"description": "Loki did not answer within the deadline."},
    },
)
async def service_logs(
    request: Request,
    server_id: ServerId,
    service_name: ServiceName,
    db: Db,
    authorized: QueryLogs,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    level: Annotated[LogLevel | None, Query()] = None,
) -> LogSearchResponse:
    """§16.7 — one registered unit's lines, from Loki, redacted."""
    reject_unknown_query_parameters(request, LOG_QUERY_PARAMS)
    for name, value in (("from", from_), ("to", to)):
        if value is not None and value.tzinfo is None:
            raise field_error(["query", name], "type", "Must carry a timezone offset.")
    end = to or (from_ + timedelta(hours=1) if from_ else datetime.now(UTC))
    start = from_ or end - timedelta(hours=1)
    if start >= end:
        raise field_error(["query", "from"], "range", "`from` must be before `to`.")
    if end - start > MAX_LOG_RANGE:
        raise field_error(["query", "to"], "range", "The range may not exceed 24 hours.")
    server = await service.load_server(db, server_id, authorized, "logs", "read:system")
    await service.load_service(db, server, service_name)
    return await logs_service.search_unit(
        db, authorized, server, service_name, start=start, end=end, limit=limit, level=level
    )
