"""Inventory writes, reads and the checks that stand between them (§14, §18).

The authorisation shape of this module, which is the part worth reading twice:

* The **capability** check (``server:read``, ``server:write``,
  ``admin:integrations``) is the route's ``require_permission`` dependency and
  happens before anything here runs.
* The **scoped** check is here, after the row is loaded, through
  ``Authorized.permits(…, target)`` — the same matcher, with the target's
  *stored* attributes (docs/SECURITY.md §4 rule 2, §20). Collections do it in
  SQL instead (``app/inventory/queries.py``), so hidden rows are never
  fetched.
* **Out of scope is ``404``, not ``403``** (docs/API_CONTRACTS.md §2). A
  ``403`` on a specific id confirms that id exists, which on a scoped resource
  is the leak the scope was for. The administrative endpoints answer ``403``
  because administration is not scoped; inventory is the opposite case.

**Creating** a server is scope-checked too, against the attributes it is about
to have. The id is minted before the check rather than by the database, so the
target is complete: a grant scoped to specific server ids then cannot create
new ones, while ``server: ["*"]`` can — which is what those two scopes mean.
Without a minted id the target would have no ``server`` dimension at all, and
``A-09`` would refuse every scope that mentions one, including the full-scope
administrator's.

**Idempotency** (§7) is a lookup on ``(created_by, idempotency_key)``, which
migration 0004 made unique per requester. A replay with a body matching the
stored row returns that row with ``idempotent_replay: true`` and re-executes
nothing; a replay with a different body is ``409``. The comparison is against
the row's *current* columns, because the body itself is not stored — so a key
replayed after the resource was edited reads as a different body and is
refused. That is the safe direction (it never silently returns something the
caller did not ask for), and it is recorded as a known issue.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.dependencies import Authorized
from app.authz.matcher import Target
from app.core.config import get_settings
from app.core.errors import AppError, field_error
from app.core.logging import get_logger
from app.db.types import uuid7
from app.inventory.models import (
    Application,
    ApplicationServer,
    CloudAccount,
    Server,
    Service,
)
from app.inventory.schemas import (
    PROVIDER_MANAGEMENT_METHODS,
    PROVIDER_RESOURCE_ID_PATTERNS,
    ApplicationCreateRequest,
    ApplicationDetail,
    ApplicationSummary,
    ApplicationUpdateRequest,
    AssociateApplicationRequest,
    AssociatedApplication,
    AssociatedServer,
    DataSection,
    HealthCheckConfig,
    HealthSection,
    ManagementCapabilitiesSection,
    OpenAlertsSection,
    ServerCreateRequest,
    ServerDetail,
    ServerSummary,
    ServerUpdateRequest,
)
from app.metrics import discovery

logger = get_logger(__name__)


class NotFoundError(AppError):
    """Also the answer for "exists but you cannot see it" (§2)."""

    status_code = 404
    code = "not_found"
    message = "The requested resource does not exist."


class DuplicateResourceError(AppError):
    status_code = 409
    code = "duplicate_resource"
    message = "That resource already exists."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class InvalidStateTransitionError(AppError):
    status_code = 409
    code = "invalid_state_transition"
    message = "That state transition is not allowed."


def _idempotency_conflict() -> ConflictError:
    error = ConflictError("This Idempotency-Key was already used with a different request body.")
    error.details = {"reason": "idempotency_key_reused_with_different_payload"}
    return error


# --- targets -----------------------------------------------------------------


def server_target(server: Server) -> Target:
    """The stored dimensions of a server, for the scoped matcher question.

    ``application`` stays ``None``: a server has no single application, and
    ``A-09`` makes a grant constraining one unable to reach a server. The SQL
    translation in ``app/authz/queries.py`` does exactly the same, which is
    what keeps the collection and the single-row answer consistent.
    """
    return Target(
        environment=server.environment,
        cloud_account=str(server.cloud_account_id),
        server=str(server.id),
    )


# --- server reads -------------------------------------------------------------


async def get_server(db: AsyncSession, server_id: uuid.UUID, authorized: Authorized) -> Server:
    """Load a server the caller may read, or raise ``404``."""
    server = await db.get(Server, server_id)
    if server is None or server.deleted_at is not None:
        raise NotFoundError
    if not authorized.permits("server", "read", server_target(server)):
        # Out of scope reads as absent (§2). The denial is not audited here:
        # the caller holds the permission and this is a scope miss, which on a
        # list endpoint would simply have been a shorter page.
        raise NotFoundError
    return server


async def require_write_scope(server: Server, authorized: Authorized) -> None:
    """``server:write`` against this row (§14.4–§14.7)."""
    if not authorized.permits("server", "write", server_target(server)):
        raise NotFoundError


def server_summary(server: Server) -> ServerSummary:
    """Built field by field, so a column added later cannot leak into a
    response by accident (the same reason as ``app/admin/router._user_detail``)."""
    return ServerSummary(
        id=server.id,
        cloud_account_id=server.cloud_account_id,
        provider_resource_id=server.provider_resource_id,
        hostname=server.hostname,
        display_name=server.display_name,
        operating_system=server.operating_system,
        os_version=server.os_version,
        environment=server.environment,
        region=server.region,
        private_ip=str(server.private_ip) if server.private_ip is not None else None,
        owner_team=server.owner_team,
        management_method=server.management_method,
        monitoring_enabled=server.monitoring_enabled,
        metrics_instance=server.metrics_instance,
        agent_status=server.agent_status,
        status=server.status,
        last_seen_at=server.last_seen_at,
        registered_at=server.registered_at,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _is_stale(server: Server) -> bool | None:
    """``None`` when the host has never been seen — that is not staleness."""
    if server.last_seen_at is None:
        return None
    age = datetime.now(UTC) - server.last_seen_at
    return age.total_seconds() > get_settings().server_stale_after_seconds


async def server_detail(db: AsyncSession, server: Server) -> ServerDetail:
    """§14.3. The sections with no source yet say so rather than guessing."""
    applications = (
        await db.execute(
            select(Application, ApplicationServer.role_on_server)
            .join(ApplicationServer, ApplicationServer.application_id == Application.id)
            .where(
                ApplicationServer.server_id == server.id,
                Application.deleted_at.is_(None),
            )
            .order_by(Application.name)
        )
    ).all()
    service_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Service)
                .where(Service.server_id == server.id, Service.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    return ServerDetail(
        **server_summary(server).model_dump(),
        provider_metadata=server.provider_metadata,
        stale=_is_stale(server),
        health=HealthSection(
            state=None,
            data_state="not_configured",
            stale_after_seconds=get_settings().server_stale_after_seconds,
            unavailable_reason="Server health evaluation is not implemented yet (M2.5).",
        ),
        applications=[
            AssociatedApplication(
                id=application.id,
                name=application.name,
                slug=application.slug,
                role_on_server=role,
            )
            for application, role in applications
        ],
        registered_service_count=service_count,
        open_alerts=OpenAlertsSection(
            counts=None,
            data_state="not_configured",
            unavailable_reason="Alerting is not implemented yet (M3).",
        ),
        management_capabilities=ManagementCapabilitiesSection(
            capabilities=None,
            data_state="not_configured",
            unavailable_reason="No provider adapter is configured (M4).",
        ),
    )


# --- server writes ------------------------------------------------------------


async def _load_active_account(db: AsyncSession, account_id: uuid.UUID) -> CloudAccount:
    account = await db.get(CloudAccount, account_id)
    if account is None or account.deleted_at is not None or account.status != "ACTIVE":
        # One message for all three: "not a usable account" is the whole
        # answer, and distinguishing them would confirm a disabled account
        # exists (§14.2 ⇒ 422 not_registered).
        raise field_error(
            ["body", "cloud_account_id"],
            "not_registered",
            "Must reference an active, registered cloud account.",
        )
    return account


def _check_provider_rules(account: CloudAccount, request: ServerCreateRequest) -> None:
    """§14.2's two provider-dependent validations."""
    pattern = PROVIDER_RESOURCE_ID_PATTERNS.get(account.provider)
    if pattern is not None and not pattern.match(request.provider_resource_id):
        raise field_error(
            ["body", "provider_resource_id"],
            "pattern",
            f"Does not match the expected form for a {account.provider} resource.",
        )
    allowed = PROVIDER_MANAGEMENT_METHODS.get(account.provider, frozenset())
    if request.management_method not in allowed:
        raise field_error(
            ["body", "management_method"],
            "not_permitted",
            f"Not supported by a {account.provider} account.",
            allowed=sorted(allowed),
        )


def _check_metrics_target(value: str | None) -> None:
    """A ``metrics_instance`` becomes a scrape target, so it must be a permitted one.

    The policy is ``app/metrics/discovery.target_problem``: the exporter port,
    and not loopback, link-local, a reserved domain or a non-unicast address.
    Applied here so an administrator gets a field error; applied *again* when
    the target file is built, so a row that arrived any other way cannot widen
    it. The rejected value is not echoed (§8).
    """
    if value is None:
        return
    ports = get_settings().metrics_target_ports
    problem = discovery.target_problem(value, ports)
    if problem is None:
        return
    messages = {
        "port_not_allowed": "That port is not one Prometheus may scrape.",
        "address_not_allowed": "That address cannot be a scrape target.",
        "reserved_domain": "That domain is reserved and can never resolve.",
        "not_host_port": "Must be host:port, matching the Prometheus instance label.",
    }
    extra = {"allowed": [str(port) for port in ports]} if problem == "port_not_allowed" else {}
    raise field_error(
        ["body", "metrics_instance"],
        "not_permitted",
        messages.get(problem, "Not permitted."),
        **extra,
    )


async def _check_metrics_instance_free(
    db: AsyncSession, value: str | None, exclude: uuid.UUID | None = None
) -> None:
    """``metrics_instance`` is unique across servers (§14.2).

    Checked before the insert so the client gets a field-level ``422`` rather
    than the bare ``409`` the unique index would produce — two servers sharing
    a Prometheus ``instance`` label is a configuration mistake, not a duplicate
    registration. The index is still what guarantees it under a race.
    """
    if value is None:
        return
    statement = select(Server.id).where(
        Server.metrics_instance == value, Server.deleted_at.is_(None)
    )
    if exclude is not None:
        statement = statement.where(Server.id != exclude)
    if (await db.execute(statement.limit(1))).first() is not None:
        raise field_error(
            ["body", "metrics_instance"],
            "not_permitted",
            "Another server already claims this Prometheus instance label.",
        )


def _server_matches(server: Server, request: ServerCreateRequest) -> bool:
    """Is this stored row the one that request would have created?"""
    return all(
        (
            server.cloud_account_id == request.cloud_account_id,
            server.provider_resource_id == request.provider_resource_id,
            server.hostname == request.hostname,
            server.display_name == request.display_name,
            server.operating_system == request.operating_system,
            server.os_version == request.os_version,
            server.environment == request.environment,
            server.region == request.region,
            (str(server.private_ip) if server.private_ip is not None else None)
            == request.private_ip,
            server.owner_team == request.owner_team,
            server.management_method == request.management_method,
            server.monitoring_enabled == request.monitoring_enabled,
            server.metrics_instance == request.metrics_instance,
        )
    )


async def create_server(
    db: AsyncSession,
    request: ServerCreateRequest,
    *,
    authorized: Authorized,
    idempotency_key: str,
) -> tuple[Server, bool]:
    """§14.2. Returns ``(server, replayed)``."""
    replay = (
        await db.execute(
            select(Server).where(
                Server.created_by == authorized.user.id,
                Server.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if not _server_matches(replay, request):
            raise _idempotency_conflict()
        return replay, True

    account = await _load_active_account(db, request.cloud_account_id)
    _check_provider_rules(account, request)
    _check_metrics_target(request.metrics_instance)
    await _check_metrics_instance_free(db, request.metrics_instance)

    # Minted here so the scope check below has a complete target. See the
    # module docstring.
    server_id = uuid7()
    target = Target(
        environment=request.environment,
        cloud_account=str(request.cloud_account_id),
        server=str(server_id),
    )
    if not authorized.permits("server", "write", target):
        # A create is refused with 404 for the same reason a read is: the
        # caller is telling us about a resource outside their scope.
        raise NotFoundError

    applications = await _load_applications(db, request.application_ids)

    server = Server(
        id=server_id,
        cloud_account_id=request.cloud_account_id,
        provider_resource_id=request.provider_resource_id,
        hostname=request.hostname,
        display_name=request.display_name,
        operating_system=request.operating_system,
        os_version=request.os_version,
        environment=request.environment,
        region=request.region,
        private_ip=request.private_ip,
        owner_team=request.owner_team,
        management_method=request.management_method,
        monitoring_enabled=request.monitoring_enabled,
        metrics_instance=request.metrics_instance,
        # PRD §4.3: a newly registered server is PENDING and grants no
        # operational permission. Not settable from the request.
        status="PENDING",
        idempotency_key=idempotency_key,
        created_by=authorized.user.id,
    )
    db.add(server)
    for application in applications:
        db.add(ApplicationServer(application_id=application.id, server_id=server_id))
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        # uq_servers_cloud_account_id_provider_resource_id — the duplicate
        # PRD §4.3's acceptance criterion requires us to detect.
        raise DuplicateResourceError(
            "A server with that provider resource id is already registered in this account."
        ) from error
    return server, False


async def update_server(
    db: AsyncSession, server: Server, request: ServerUpdateRequest
) -> tuple[Server, dict[str, Any], bool]:
    """§14.4. Returns ``(server, changes, environment_changed)``.

    ``changes`` is a before/after map for the audit row, built from the fields
    that actually moved rather than from everything the body mentioned.
    """
    fields = request.model_dump(exclude_unset=True)
    if "metrics_instance" in fields:
        # Only a *changed* value is checked: an edit form that resubmits the
        # current one must not be refused over a row it did not touch (a seeded
        # `.invalid` instance, for one).
        if fields["metrics_instance"] != server.metrics_instance:
            _check_metrics_target(fields["metrics_instance"])
        await _check_metrics_instance_free(db, fields["metrics_instance"], exclude=server.id)

    changes: dict[str, Any] = {}
    for name, value in fields.items():
        current = getattr(server, name)
        current_text = str(current) if name == "private_ip" and current is not None else current
        if current_text == value:
            continue
        changes[name] = {"from": current_text, "to": value}
        setattr(server, name, value)

    if changes:
        await db.flush()
    return server, changes, "environment" in changes


async def retire_server(db: AsyncSession, server: Server) -> Server:
    """§14.5 — soft delete.

    The "blocked while a non-terminal operation targets the server" rule in
    §14.5 has nothing to check yet: ``operations`` arrives with M4.2. The guard
    is deliberately *not* written as a no-op that looks like a control; it goes
    in with the table it depends on.
    """
    if server.status == "RETIRED":
        raise InvalidStateTransitionError("That server is already retired.")
    server.status = "RETIRED"
    server.deleted_at = datetime.now(UTC)
    await db.flush()
    return server


async def _load_applications(
    db: AsyncSession, application_ids: list[uuid.UUID]
) -> list[Application]:
    if not application_ids:
        return []
    wanted = set(application_ids)
    found = list(
        (
            await db.execute(
                select(Application).where(
                    Application.id.in_(wanted), Application.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if len(found) != len(wanted):
        raise field_error(
            ["body", "application_ids"],
            "not_registered",
            "One or more application ids do not exist.",
        )
    return found


async def associate_application(
    db: AsyncSession, server: Server, request: AssociateApplicationRequest
) -> Application:
    """§14.6. PRD §4.9 both ways — many servers per application and back."""
    application = await db.get(Application, request.application_id)
    if application is None or application.deleted_at is not None:
        raise field_error(
            ["body", "application_id"],
            "not_registered",
            "That application does not exist.",
        )
    existing = await db.get(ApplicationServer, (application.id, server.id))
    if existing is not None:
        raise DuplicateResourceError("That application is already associated with this server.")
    db.add(
        ApplicationServer(
            application_id=application.id,
            server_id=server.id,
            role_on_server=request.role_on_server,
        )
    )
    await db.flush()
    return application


async def remove_association(
    db: AsyncSession, server: Server, application_id: uuid.UUID
) -> Application:
    """§14.7."""
    association = await db.get(ApplicationServer, (application_id, server.id))
    if association is None:
        raise NotFoundError("That association does not exist.")
    application = await db.get(Application, application_id)
    if application is None:
        raise NotFoundError
    await db.delete(association)
    await db.flush()
    return application


# --- applications -------------------------------------------------------------


def application_summary(application: Application) -> ApplicationSummary:
    return ApplicationSummary(
        id=application.id,
        name=application.name,
        slug=application.slug,
        description=application.description,
        owner_team=application.owner_team,
        repository_reference=application.repository_reference,
        status=application.status,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


async def get_application(db: AsyncSession, application_id: uuid.UUID) -> Application:
    application = await db.get(Application, application_id)
    if application is None or application.deleted_at is not None:
        raise NotFoundError
    return application


async def application_detail(db: AsyncSession, application: Application) -> ApplicationDetail:
    """§18.3: servers, services, targets."""
    servers = (
        await db.execute(
            select(Server, ApplicationServer.role_on_server)
            .join(ApplicationServer, ApplicationServer.server_id == Server.id)
            .where(
                ApplicationServer.application_id == application.id,
                Server.deleted_at.is_(None),
            )
            .order_by(Server.display_name)
        )
    ).all()
    service_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Service)
                .where(
                    Service.application_id == application.id,
                    Service.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )
    return ApplicationDetail(
        **application_summary(application).model_dump(),
        health_check_config=HealthCheckConfig.model_validate(application.health_check_config),
        servers=[
            AssociatedServer(
                id=server.id,
                hostname=server.hostname,
                display_name=server.display_name,
                environment=server.environment,
                status=server.status,
                role_on_server=role,
            )
            for server, role in servers
        ],
        registered_service_count=service_count,
        deployment_targets=DataSection(
            data_state="not_configured",
            unavailable_reason="Deployment targets are not implemented yet (M5).",
        ),
    )


def _application_matches(application: Application, request: ApplicationCreateRequest) -> bool:
    return all(
        (
            application.name == request.name,
            application.slug == request.slug,
            application.description == request.description,
            application.owner_team == request.owner_team,
            application.repository_reference == request.repository_reference,
            application.health_check_config == request.health_check_config.model_dump(),
        )
    )


async def create_application(
    db: AsyncSession,
    request: ApplicationCreateRequest,
    *,
    created_by: uuid.UUID,
    idempotency_key: str,
) -> tuple[Application, bool]:
    """§18.2. Returns ``(application, replayed)``.

    No scoped check: applications are not scope-filtered at this milestone
    (``OPEN-27``, see ``app/inventory/queries.py``), and the capability this
    route requires — ``admin:integrations`` — is Administrator-only anyway.
    """
    replay = (
        await db.execute(
            select(Application).where(
                Application.created_by == created_by,
                Application.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if not _application_matches(replay, request):
            raise _idempotency_conflict()
        return replay, True

    application = Application(
        name=request.name,
        slug=request.slug,
        description=request.description,
        owner_team=request.owner_team,
        repository_reference=request.repository_reference,
        health_check_config=request.health_check_config.model_dump(),
        status="ACTIVE",
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        # Either uq_applications_slug or uq_applications_name. Both are
        # duplicates from the caller's point of view.
        raise DuplicateResourceError(
            "An application with that name or slug already exists."
        ) from error
    return application, False


async def update_application(
    db: AsyncSession, application: Application, request: ApplicationUpdateRequest
) -> tuple[Application, dict[str, Any]]:
    """§18.4. ``slug`` is not in the request model, so it cannot be changed."""
    fields = request.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}
    for name, value in fields.items():
        if name == "health_check_config" and request.health_check_config is not None:
            value = request.health_check_config.model_dump()
        current = getattr(application, name)
        if current == value or value is None:
            continue
        changes[name] = {"from": current, "to": value}
        setattr(application, name, value)
    if changes:
        try:
            await db.flush()
        except IntegrityError as error:
            await db.rollback()
            raise DuplicateResourceError("An application with that name already exists.") from error
    return application, changes


async def archive_application(db: AsyncSession, application: Application) -> Application:
    """§18.5 — archive, which is the soft delete for this table."""
    if application.status == "ARCHIVED":
        raise InvalidStateTransitionError("That application is already archived.")
    application.status = "ARCHIVED"
    application.deleted_at = datetime.now(UTC)
    await db.flush()
    return application
