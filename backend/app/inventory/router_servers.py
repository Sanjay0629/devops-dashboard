"""Server inventory endpoints 14.1–14.7 (docs/API_CONTRACTS.md §14).

The first *scoped* resource in the product, so this is where the two halves of
the authorisation layer M1.4 built finally meet a real row:

* ``require_permission("server", …)`` on every route — the capability, checked
  before the handler runs, audited on denial, and gated by read-only mode.
* ``Authorized.permits(…, target)`` in ``service.py`` after the row is loaded,
  and ``readable_servers`` inside the list query so out-of-scope rows are
  never fetched (docs/SECURITY.md §20).

14.8 (connectivity check) is **not** here. It is a synchronous probe through a
provider adapter, and no adapter exists before M4.1 — shipping it against a
stub would be presenting an unimplemented integration as operational
(CLAUDE.md §7).

Audit coverage, every mutation in the same transaction as the change (§10):
``SERVER_REGISTERED``, ``SERVER_UPDATED``, ``SERVER_ENVIRONMENT_CHANGED``,
``SERVER_RETIRED``. An environment change is privilege-relevant — it moves the
authorisation boundary for every future operation on that host — so it gets
its own action type alongside the update, rather than being one field in a
diff somebody has to notice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.config import get_settings
from app.core.errors import field_error
from app.core.filters import reject_unknown_query_parameters
from app.core.logging import get_logger
from app.core.pagination import Cursor, decode_cursor
from app.db.session import get_session
from app.inventory import queries, service
from app.inventory.schemas import (
    AssociateApplicationRequest,
    CursorPage,
    Environment,
    OperatingSystem,
    ServerCreated,
    ServerCreateRequest,
    ServerDetail,
    ServerList,
    ServerStatus,
    ServerUpdateRequest,
)
from app.metrics import discovery

logger = get_logger(__name__)

router = APIRouter(prefix="/servers", tags=["inventory"])

Db = Annotated[AsyncSession, Depends(get_session)]
ServerId = Annotated[uuid.UUID, Path(description="Server identifier")]
ReadServers = Annotated[Authorized, require_permission("server", "read")]
WriteServers = Annotated[Authorized, require_permission("server", "write")]

ServerSort = Literal["display_name", "hostname", "last_seen_at", "created_at", "environment"]

#: A PATCH touching any of these can add, remove or relabel a Prometheus target
#: (``app/metrics/discovery.select_targets``), so only these enqueue a rebuild.
TARGET_FIELDS = frozenset({"metrics_instance", "monitoring_enabled", "environment", "status"})
Order = Literal["asc", "desc"]

#: §7: absent ⇒ 422. Declared as a required header so FastAPI produces the
#: ``422`` with ``loc: ["header", "idempotency-key"]`` rather than the handler
#: inventing one.
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=64,
        description="Client-generated key, unique per logical intent (§7).",
    ),
]


def _unmodified_since(header: str | None, updated_at: datetime) -> None:
    """Optimistic concurrency for ``PATCH`` (§14.4).

    A malformed date is ``422`` rather than ignored: silently dropping the
    precondition would turn "do not overwrite someone else's edit" into "go
    ahead", which is the opposite of what the client asked for.

    ``updated_at`` is compared at whole-second resolution because an HTTP-date
    has no sub-second part — without the truncation, a row written at
    ``…:07.5`` is always "newer" than the ``…:07`` the client was handed, and
    every conditional request would conflict.
    """
    if header is None:
        return
    try:
        boundary = parsedate_to_datetime(header)
    except (TypeError, ValueError) as error:
        raise field_error(
            ["header", "if-unmodified-since"],
            "type",
            "Must be an HTTP-date (RFC 9110).",
        ) from error
    if boundary.tzinfo is None:
        boundary = boundary.replace(tzinfo=UTC)
    if updated_at.replace(microsecond=0) > boundary:
        raise service.ConflictError(
            "The server was modified after the time given in If-Unmodified-Since."
        )


def _cursor(value: str | None, *, sort: str, order: str) -> Cursor | None:
    return None if value is None else decode_cursor(value, sort=sort, order=order)


# --- 14.1 --------------------------------------------------------------------


@router.get("", dependencies=[ReadBudget], summary="List and search server inventory")
async def list_servers(
    request: Request,
    db: Db,
    authorized: ReadServers,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    sort: ServerSort = "display_name",
    order: Order = "asc",
    environment: Annotated[list[Environment] | None, Query()] = None,
    provider: Annotated[list[Literal["AWS", "AZURE", "ON_PREM", "OTHER"]] | None, Query()] = None,
    cloud_account_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    region: Annotated[list[str] | None, Query(max_length=64)] = None,
    status: Annotated[list[ServerStatus] | None, Query()] = None,
    operating_system: Annotated[list[OperatingSystem] | None, Query()] = None,
    application_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    owner_team: Annotated[list[str] | None, Query(max_length=128)] = None,
    monitoring_enabled: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> ServerList:
    """Scope-filtered, cursor-paged (§5, §6, §14.1).

    ``page.total`` is ``null`` by specification: §5 keeps counts off cursor
    pages, and names scope-filtered data as a reason — a total over rows the
    caller cannot see is an inference channel about the ones they cannot.
    """
    reject_unknown_query_parameters(request, queries.SERVER_QUERY_PARAMS)
    statement = queries.servers_query(
        authorized.grants,
        sort=sort,
        order=order,
        cursor=_cursor(cursor, sort=sort, order=order),
        limit=limit,
        environment=list(environment) if environment else None,
        provider=list(provider) if provider else None,
        cloud_account_id=cloud_account_id,
        region=region,
        status=list(status) if status else None,
        operating_system=list(operating_system) if operating_system else None,
        application_id=application_id,
        owner_team=owner_team,
        monitoring_enabled=monitoring_enabled,
        q=q,
    )
    rows = (await db.execute(statement)).scalars().all()
    items, next_cursor, has_more = queries.page_of(
        rows, limit=limit, sort=sort, order=order, sorts=queries.SERVER_SORTS
    )
    return ServerList(
        items=[service.server_summary(row) for row in items],
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        demo_mode=get_settings().demo_mode,
    )


# --- 14.2 --------------------------------------------------------------------


@router.post("", status_code=201, summary="Register a server")
async def register_server(
    request: Request,
    response: Response,
    body: ServerCreateRequest,
    idempotency_key: IdempotencyKey,
    db: Db,
    authorized: WriteServers,
) -> ServerCreated:
    """§14.2. ``201`` on a create, ``200`` on an idempotent replay (§7)."""
    server, replayed = await service.create_server(
        db, body, authorized=authorized, idempotency_key=idempotency_key
    )
    if replayed:
        # Nothing was re-executed, so nothing is audited either: the original
        # registration already has its row, and a second one would record an
        # event that did not happen.
        response.status_code = 200
        detail = await service.server_detail(db, server, authorized.grants)
        return ServerCreated(**detail.model_dump(), idempotent_replay=True)

    audit.record(
        db,
        action_type="SERVER_REGISTERED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="server",
        target_id=server.id,
        environment=server.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={
            "hostname": server.hostname,
            "cloud_account_id": str(server.cloud_account_id),
            "provider_resource_id": server.provider_resource_id,
            "management_method": server.management_method,
            "status": server.status,
        },
    )
    await db.commit()
    await db.refresh(server)
    await discovery.request_regeneration()
    logger.info("server_registered", server_id=str(server.id), environment=server.environment)
    detail = await service.server_detail(db, server, authorized.grants)
    return ServerCreated(**detail.model_dump(), idempotent_replay=False)


# --- 14.3 --------------------------------------------------------------------


@router.get("/{server_id}", dependencies=[ReadBudget], summary="Server detail")
async def read_server(server_id: ServerId, db: Db, authorized: ReadServers) -> ServerDetail:
    """§14.3. Out of scope ⇒ ``404``, never ``403`` (§2)."""
    server = await service.get_server(db, server_id, authorized)
    return await service.server_detail(db, server, authorized.grants)


# --- 14.4 --------------------------------------------------------------------


@router.patch("/{server_id}", summary="Edit metadata, enable or disable monitoring")
async def update_server(
    request: Request,
    server_id: ServerId,
    body: ServerUpdateRequest,
    db: Db,
    authorized: WriteServers,
    if_unmodified_since: Annotated[str | None, Header(alias="If-Unmodified-Since")] = None,
) -> ServerDetail:
    """§14.4.

    An **environment change requires Administrator**, checked here rather than
    by a second permission on the route: the route's permission is
    ``server:write``, and only this one field needs more. ``admin:users`` is
    the stand-in for "is an Administrator" — it is Administrator-only in the
    seeded matrix (docs/SECURITY.md §4) and resolving it costs nothing,
    because the grants are already in hand.
    """
    server = await service.get_server(db, server_id, authorized)
    await service.require_write_scope(server, authorized)
    _unmodified_since(if_unmodified_since, server.updated_at)

    moving_environment = body.environment is not None and body.environment != server.environment
    if moving_environment and not authorized.permits("admin", "users"):
        raise service.NotFoundError

    if moving_environment:
        # The *destination* must be in scope too, or a staging-scoped caller
        # could push a host into production and lose sight of it.
        destination = service.server_target(server)
        if not authorized.permits(
            "server",
            "write",
            type(destination)(
                environment=body.environment,
                cloud_account=destination.cloud_account,
                server=destination.server,
            ),
        ):
            raise service.NotFoundError

    previous_environment = server.environment
    server, changes, environment_changed = await service.update_server(db, server, body)

    if changes:
        audit.record(
            db,
            action_type="SERVER_UPDATED",
            outcome="SUCCESS",
            actor_id=authorized.user.id,
            actor_label=authorized.user.email,
            target_type="server",
            target_id=server.id,
            environment=server.environment,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={"changes": changes},
        )
    if environment_changed:
        audit.record(
            db,
            action_type="SERVER_ENVIRONMENT_CHANGED",
            outcome="SUCCESS",
            actor_id=authorized.user.id,
            actor_label=authorized.user.email,
            target_type="server",
            target_id=server.id,
            environment=server.environment,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={"from": previous_environment, "to": server.environment},
        )
        logger.warning(
            "server_environment_changed",
            server_id=str(server.id),
            to=server.environment,
        )
    await db.commit()
    await db.refresh(server)
    if TARGET_FIELDS & changes.keys():
        await discovery.request_regeneration()
    return await service.server_detail(db, server, authorized.grants)


# --- 14.5 --------------------------------------------------------------------


@router.delete("/{server_id}", status_code=204, summary="Retire a server (soft delete)")
async def retire_server(
    request: Request,
    server_id: ServerId,
    db: Db,
    authorized: WriteServers,
) -> None:
    """§14.5. History is retained; the row is marked, never removed."""
    server = await service.get_server(db, server_id, authorized)
    await service.require_write_scope(server, authorized)
    await service.retire_server(db, server)
    audit.record(
        db,
        action_type="SERVER_RETIRED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="server",
        target_id=server.id,
        environment=server.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"hostname": server.hostname},
    )
    await db.commit()
    await discovery.request_regeneration()
    logger.info("server_retired", server_id=str(server.id))


# --- 14.6 / 14.7 -------------------------------------------------------------


@router.post(
    "/{server_id}/applications",
    status_code=201,
    summary="Associate an application with this server",
)
async def associate_application(
    request: Request,
    server_id: ServerId,
    body: AssociateApplicationRequest,
    db: Db,
    authorized: WriteServers,
) -> ServerDetail:
    """§14.6."""
    server = await service.get_server(db, server_id, authorized)
    await service.require_write_scope(server, authorized)
    application = await service.associate_application(db, server, body)
    audit.record(
        db,
        action_type="SERVER_UPDATED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="server",
        target_id=server.id,
        environment=server.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={
            "association": "added",
            "application_id": str(application.id),
            "application_slug": application.slug,
        },
    )
    await db.commit()
    return await service.server_detail(db, server, authorized.grants)


@router.delete(
    "/{server_id}/applications/{application_id}",
    status_code=204,
    summary="Remove an application association",
)
async def remove_association(
    request: Request,
    server_id: ServerId,
    application_id: Annotated[uuid.UUID, Path(description="Application identifier")],
    db: Db,
    authorized: WriteServers,
) -> None:
    """§14.7."""
    server = await service.get_server(db, server_id, authorized)
    await service.require_write_scope(server, authorized)
    application = await service.remove_association(db, server, application_id)
    audit.record(
        db,
        action_type="SERVER_UPDATED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="server",
        target_id=server.id,
        environment=server.environment,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={
            "association": "removed",
            "application_id": str(application.id),
            "application_slug": application.slug,
        },
    )
    await db.commit()
