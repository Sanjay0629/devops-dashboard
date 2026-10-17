"""Service registration, reads and the live reading (docs/API_CONTRACTS.md §16).

Three kinds of read, and the difference between them is the point:

* **Lists (§16.1, §16.2)** come from ``services.last_observed_*`` — a cache of
  Prometheus the refresh job writes — aged and host-checked by
  :func:`app.services.normalise.view_cached`. They never fan out to Prometheus
  per row.
* **Detail (§16.4)** asks Prometheus *now* and normalises with
  :func:`app.services.normalise.normalise`. This is the only place a request
  waits on Prometheus, and the only place ``restart_count`` is known.
* **Both** end in the same ``ServiceItem`` shape, so a client never has to know
  which one it was handed.

**Registration is the allowlist** (docs/SECURITY.md §15). A unit with no
``services`` row on that server is ``404`` everywhere, and nothing in this
module takes a unit name from anywhere but a registered row — the live reading
asks Prometheus for exactly that row's name and nothing else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import ApprovalPolicy
from app.authz.dependencies import Authorized
from app.core.errors import field_error
from app.core.logging import get_logger
from app.inventory.models import Application, Server, Service, ServiceActionPolicy
from app.inventory.service import DuplicateResourceError, NotFoundError, server_target
from app.metrics import readings
from app.metrics.client import PrometheusUnavailableError
from app.metrics.schemas import UnavailableReason
from app.metrics.templates import TemplateError
from app.services import reader
from app.services.normalise import (
    CollectorObservation,
    HostObservation,
    Normalised,
    UnitObservation,
    gate,
    normalise,
    view_cached,
)
from app.services.schemas import (
    ServiceAction,
    ServiceDetail,
    ServiceItem,
    ServiceRegisterRequest,
    ServiceUpdateRequest,
)

logger = get_logger(__name__)

#: Which permission each action in a policy needs from the caller. ``status`` is
#: a read, and ``logs_tail`` is the system-log permission
#: (``logs:read:system``). None of this authorises anything — it
#: only decides which buttons the UI offers (mandatory rule 4).
_ACTION_PERMISSION: dict[str, tuple[str, str]] = {
    "start": ("service", "start"),
    "stop": ("service", "stop"),
    "restart": ("service", "restart"),
    "status": ("service", "read"),
    "logs_tail": ("logs", "read:system"),
}


# --- loading -----------------------------------------------------------------


async def load_server(
    db: AsyncSession,
    server_id: uuid.UUID,
    authorized: Authorized,
    resource_type: str,
    action: str,
) -> Server:
    """A server the caller holds ``resource_type:action`` on, or ``404``.

    Out of scope reads as absent (§2), exactly as for the server endpoints.
    """
    server = await db.get(Server, server_id)
    if (
        server is None
        or server.deleted_at is not None
        or not authorized.permits(resource_type, action, server_target(server))
    ):
        raise NotFoundError
    return server


async def load_service(db: AsyncSession, server: Server, service_name: str) -> Service:
    """The registered row for ``service_name`` on ``server`` — the allowlist lookup."""
    row = (
        await db.execute(
            select(Service).where(
                Service.server_id == server.id,
                Service.service_name == service_name,
                Service.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError
    return row


async def policy_context(
    db: AsyncSession, row: Service
) -> tuple[ServiceActionPolicy | None, ApprovalPolicy | None]:
    if row.action_policy_id is None:
        return None, None
    policy = await db.get(ServiceActionPolicy, row.action_policy_id)
    if policy is None or policy.approval_policy_id is None:
        return policy, None
    return policy, await db.get(ApprovalPolicy, policy.approval_policy_id)


# --- presentation ------------------------------------------------------------


def allowed_actions(
    policy: ServiceActionPolicy | None, server: Server, authorized: Authorized
) -> list[ServiceAction]:
    """The policy's actions the caller could also perform. A UI hint, not a check."""
    if policy is None:
        return []
    target = server_target(server)
    permitted: list[ServiceAction] = []
    for action in policy.allowed_actions:
        needed = _ACTION_PERMISSION.get(action)
        if needed is not None and authorized.permits(needed[0], needed[1], target):
            permitted.append(action)  # type: ignore[arg-type]
    return permitted


def approval_required(approval: ApprovalPolicy | None, server: Server) -> bool:
    """Does a state-changing action here need an approval? ``*`` covers every environment."""
    return (
        approval is not None
        and approval.applies_to == "SERVICE_OPERATION"
        and approval.environment in (server.environment, "*")
    )


def _item(
    row: Service,
    server: Server,
    verdict: Normalised,
    policy: ServiceActionPolicy | None,
    approval: ApprovalPolicy | None,
    authorized: Authorized,
    *,
    restart_count: int | None = None,
) -> dict[str, Any]:
    """Built field by field, so a column added later cannot leak into a response."""
    return {
        "service_id": row.id,
        "server_id": server.id,
        "server_display_name": server.display_name,
        "environment": server.environment,
        "service_name": row.service_name,
        "display_name": row.display_name,
        "application_id": row.application_id,
        "monitoring_enabled": row.monitoring_enabled,
        "state": verdict.state,
        "sub_state": None,
        "load_state": None,
        "enabled": None,
        "main_pid": None,
        "restart_count": restart_count,
        "last_state_change_at": None,
        "data_state": verdict.data_state,
        "unavailable_reason": verdict.unavailable_reason,
        "collected_at": verdict.collected_at,
        "server_reachable": verdict.server_reachable,
        "allowed_actions": allowed_actions(policy, server, authorized),
        "approval_required": approval_required(approval, server),
    }


def cached_item(
    row: Service,
    server: Server,
    policy: ServiceActionPolicy | None,
    approval: ApprovalPolicy | None,
    authorized: Authorized,
    *,
    now: datetime,
) -> ServiceItem:
    """A list row, from the cache."""
    verdict = view_cached(
        os_family=server.operating_system,
        monitoring_enabled=server.monitoring_enabled and row.monitoring_enabled,
        has_metrics_instance=server.metrics_instance is not None,
        agent_status=server.agent_status,
        cached_state=row.last_observed_state,
        observed_at=row.last_observed_at,
        now=now,
    )
    return ServiceItem(**_item(row, server, verdict, policy, approval, authorized))


# --- the live reading --------------------------------------------------------


def _failure_reason(error: PrometheusUnavailableError) -> UnavailableReason:
    return "prometheus_not_configured" if error.reason == "not_configured" else error.reason


async def live_detail(
    db: AsyncSession, server: Server, row: Service, authorized: Authorized
) -> ServiceDetail:
    """§16.4: ask Prometheus now and normalise.

    Anything that stops the question being asked is an answer in its own right,
    never an exception: a dead Prometheus is ``UNKNOWN``/``unavailable`` on a
    successful response (``PrometheusUnavailableError`` is deliberately not an
    ``AppError``), and a ``metrics_instance`` the query renderer refuses is
    treated as a failed read rather than escaped and tried.
    """
    policy, approval = await policy_context(db, row)
    instance = server.metrics_instance
    failure: UnavailableReason | None = None
    facts = reader.HostFacts()
    unit = UnitObservation(states={})

    # The gates are the first three rules of the table; if one applies there is
    # nothing to ask Prometheus, and `normalise` below returns the same verdict.
    monitored = server.monitoring_enabled and row.monitoring_enabled
    if instance is not None and gate(server.operating_system, monitored, True) is None:
        try:
            observed = await reader.read(readings.make_client(), {instance: [row.service_name]})
            facts = observed.hosts.get(instance, facts)
            unit = observed.units.get((instance, row.service_name), unit)
        except PrometheusUnavailableError as error:
            failure = _failure_reason(error)
        except TemplateError:
            logger.warning("service_query_refused", service_id=str(row.id))
            failure = "provider_error"

    verdict = normalise(
        HostObservation(
            os_family=server.operating_system,
            monitoring_enabled=server.monitoring_enabled and row.monitoring_enabled,
            has_metrics_instance=instance is not None,
            prometheus_failure=failure,
            up=facts.up,
            scraped_at=facts.scraped_at,
            last_seen_at=server.last_seen_at,
        ),
        CollectorObservation(success=facts.collector_success),
        unit,
        # Prometheus's clock when it answered; ours only if it did not.
        now=facts.evaluated_at or datetime.now(UTC),
    )
    return ServiceDetail(
        **_item(
            row, server, verdict, policy, approval, authorized, restart_count=verdict.restart_count
        ),
        started_at=verdict.started_at,
    )


# --- registration ------------------------------------------------------------


async def _require_application(db: AsyncSession, application_id: uuid.UUID | None) -> None:
    if application_id is None:
        return
    application = await db.get(Application, application_id)
    if application is None or application.deleted_at is not None:
        raise field_error(
            ["body", "application_id"], "not_registered", "That application does not exist."
        )


async def _require_policy(db: AsyncSession, policy_id: uuid.UUID | None) -> None:
    if policy_id is None:
        return
    if await db.get(ServiceActionPolicy, policy_id) is None:
        raise field_error(
            ["body", "action_policy_id"], "not_registered", "That action policy does not exist."
        )


async def register_service(
    db: AsyncSession, server: Server, request: ServiceRegisterRequest
) -> Service:
    """§16.3. Omitting ``action_policy_id`` leaves the row with **no actions** (fail closed)."""
    await _require_application(db, request.application_id)
    await _require_policy(db, request.action_policy_id)
    row = Service(
        server_id=server.id,
        service_name=request.service_name,
        display_name=request.display_name,
        application_id=request.application_id,
        monitoring_enabled=request.monitoring_enabled,
        action_policy_id=request.action_policy_id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        if "uq_services_server_id_service_name" in str(error.orig):
            raise DuplicateResourceError(
                "That service is already registered on this server."
            ) from error
        raise
    return row


async def update_service(
    db: AsyncSession, row: Service, request: ServiceUpdateRequest
) -> dict[str, Any]:
    """§16.5. Returns the before/after map of what actually moved."""
    fields = request.model_dump(exclude_unset=True)
    if fields.get("monitoring_enabled", True) is None:
        # NOT NULL in the schema, and "null" would otherwise mean "unchanged"
        # to one client and "off" to another.
        raise field_error(["body", "monitoring_enabled"], "type", "Must be true or false.")
    if "application_id" in fields:
        await _require_application(db, fields["application_id"])
    if "action_policy_id" in fields:
        await _require_policy(db, fields["action_policy_id"])

    changes: dict[str, Any] = {}
    for name, value in fields.items():
        current = getattr(row, name)
        if current == value:
            continue
        changes[name] = {
            "from": None if current is None else str(current),
            "to": None if value is None else str(value),
        }
        setattr(row, name, value)
    if changes:
        await db.flush()
    return changes


async def deregister_service(db: AsyncSession, row: Service) -> Service:
    """§16.6 — soft delete, so operation and audit history keep a row to point at."""
    row.deleted_at = datetime.now(UTC)
    await db.flush()
    return row
