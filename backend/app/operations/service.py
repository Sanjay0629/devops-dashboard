"""Requesting, reading and cancelling an operation (17.1 – 17.3; ``OPEN-73`` - ``OPEN-76``).

The order of checks is SECURITY.md §16 with one deliberate change (``OPEN-73``): the idempotency
replay lookup precedes the rate limit and the one-in-flight guard, so a replay of a running
operation returns the original and spends no budget. A caller who may not act on the target
learns nothing about how the portal is configured: permission is checked before the adapter and
the approval policy are consulted.

**The database is the guard, not the pre-checks.** Two concurrent submissions of one key, or two
different keys against one live target, are decided by ``uq_operations_idempotency`` and
``uq_operations_single_inflight``; the courtesy pre-check exists only for a clean message.

Nothing here executes anything on a host. The worker (``app/workers/tasks/operations.py``) does,
after re-authorising and re-validating the target.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import requests as approval_requests
from app.approvals.rules import resolve_policy
from app.approvals.standing import policy_row
from app.audit import service as audit
from app.auth.models import User
from app.authz import service as authz_service
from app.authz.dependencies import Authorized, ForbiddenError
from app.authz.matcher import permits
from app.core import ratelimit
from app.core.errors import AppError, NotConfiguredError, field_error
from app.core.logging import get_logger
from app.inventory.models import CloudAccount, Server, Service
from app.inventory.service import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    server_target,
)
from app.operations import codes, states
from app.operations.evaluator import reason_required
from app.operations.guard import OperationInProgressError
from app.operations.models import Operation
from app.operations.schemas import (
    ActorView,
    OperationAccepted,
    OperationDetail,
    OperationRequest,
    TargetView,
)
from app.operations.store import transition_statement
from app.providers import registry
from app.providers.capabilities import ensure_supported
from app.providers.errors import NotConfigured, ProviderError, to_app_error
from app.providers.types import ServiceAction
from app.services import service as services_service

logger = get_logger(__name__)

#: SECURITY.md §13: service operations, 5 per minute per identity. A privileged class, so it
#: fails closed when Redis is unavailable.
RATE_LIMIT: int = 5
RATE_WINDOW_SECONDS: int = 60
#: Re-ask after this long while an operation is not terminal (PRD §5.3; ``OPEN-19``).
POLL_AFTER_SECONDS: int = 2
TASK_NAME = "dcp.operations.execute"


@dataclass(frozen=True)
class Origin:
    """Where the request came from and who made it, for the audit row.

    The actor is a snapshot: a rollback expires every loaded row, and the async session cannot
    lazy-load the user back to write the refusal that follows it.
    """

    client_ip: str | None
    user_agent: str | None
    method: str
    path: str
    actor_id: uuid.UUID
    actor_label: str


def _identity_conflict() -> ConflictError:
    error = ConflictError("This Idempotency-Key was already used with a different request body.")
    error.details = {"reason": "idempotency_key_reused_with_different_payload"}
    return error


def _constraint(error: IntegrityError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


def _audit(
    db: AsyncSession,
    origin: Origin,
    action_type: str,
    *,
    outcome: str,
    target_type: str,
    target_id: uuid.UUID,
    environment: str,
    metadata: dict[str, Any],
) -> None:
    audit.record(
        db,
        action_type=action_type,
        outcome=outcome,
        actor_id=origin.actor_id,
        actor_label=origin.actor_label,
        target_type=target_type,
        target_id=target_id,
        environment=environment,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata=metadata,
    )


@dataclass(frozen=True)
class _Target:
    """The refused request's target, as plain values (see :class:`Origin` for why)."""

    service_id: uuid.UUID
    server_id: uuid.UUID
    service_name: str
    environment: str
    action: str


async def _refuse(
    db: AsyncSession,
    origin: Origin,
    *,
    target: _Target,
    reason: str,
    error: AppError,
) -> NoReturn:
    """Audit a refusal, commit it (the failed request must still leave a trail), then raise."""
    _audit(
        db,
        origin,
        "SERVICE_OPERATION_DENIED",
        outcome="DENIED",
        target_type="service",
        target_id=target.service_id,
        environment=target.environment,
        metadata={
            "server_id": str(target.server_id),
            "service_name": target.service_name,
            "action": target.action,
            "reason": reason,
        },
    )
    await db.commit()
    raise error


async def _find_by_key(db: AsyncSession, user_id: uuid.UUID, key: str) -> Operation | None:
    return (
        await db.execute(
            select(Operation).where(
                Operation.requested_by == user_id, Operation.idempotency_key == key
            )
        )
    ).scalar_one_or_none()


def _same_request(
    existing: Operation, *, server_id: uuid.UUID, service_id: uuid.UUID, body: OperationRequest
) -> bool:
    return (
        existing.target_server_id == server_id
        and existing.target_service_id == service_id
        and existing.action_type == body.action
        and (existing.requested_reason or None) == body.reason
    )


async def request_operation(
    db: AsyncSession,
    origin: Origin,
    authorized: Authorized,
    *,
    server_id: uuid.UUID,
    service_name: str,
    body: OperationRequest,
    idempotency_key: str,
) -> tuple[Operation, bool]:
    """§17.1. Returns ``(operation, replayed)``."""
    action = body.action
    user_id = authorized.user.id

    server = await services_service.load_server(db, server_id, authorized, "service", "read")
    if server.status != "MANAGED":
        raise NotFoundError
    row = await services_service.load_service(db, server, service_name)
    # Plain values from here on: a rollback expires every loaded row, and the async session
    # cannot lazy-load it back.
    server_pk, service_pk = server.id, row.id
    environment, unit = server.environment, row.service_name
    refusal = _Target(service_pk, server_pk, unit, environment, action)

    account = await db.get(CloudAccount, server.cloud_account_id)
    if account is None or account.deleted_at is not None or account.status != "ACTIVE":
        error = ConflictError("The server's cloud account is not active.")
        error.details = {"reason": "cloud_account_not_active"}
        await _refuse(db, origin, reason="cloud_account_not_active", error=error, target=refusal)
    provider = account.provider

    policy, approval_policy = await services_service.policy_context(db, row)
    if policy is None or action not in policy.allowed_actions:
        await _refuse(
            db, origin, reason="action_not_in_policy", error=ForbiddenError(), target=refusal
        )
    timeout_seconds = policy.operation_timeout_seconds

    decision = permits(authorized.grants, "service", action, server_target(server))
    if not decision.allowed:
        authz_service.record_denial(
            db,
            user_id=origin.actor_id,
            user_label=origin.actor_label,
            permission=f"service:{action}",
            reason=decision.reason,
            method=origin.method,
            path=origin.path,
            client_ip=origin.client_ip,
            user_agent=origin.user_agent,
            target_type="service",
            target_id=service_pk,
            environment=environment,
        )
        await _refuse(
            db,
            origin,
            reason="permission_denied",
            error=ForbiddenError(),
            target=refusal,
        )

    resolution = resolve_policy(
        environment=environment,
        action=action,
        policy_linked=policy.approval_policy_id is not None,
        policy=policy_row(approval_policy),
    )
    if body.reason is None and reason_required(
        environment=environment, approval_needed=resolution.required
    ):
        raise field_error(
            ["body", "reason"], "required", "A reason is required for this environment or action."
        )

    existing = await _find_by_key(db, user_id, idempotency_key)
    if existing is not None:
        if not _same_request(existing, server_id=server_pk, service_id=service_pk, body=body):
            raise _identity_conflict()
        return existing, True

    try:
        adapter = registry.resolve_adapter(provider)
        if adapter.demo:
            raise NotConfigured(adapter_id=adapter.adapter_id, detail_code="demonstration_mode")
        ensure_supported(
            adapter.capabilities(), ServiceAction(action), adapter_id=adapter.adapter_id
        )
    except ProviderError as provider_error:
        await _refuse(
            db,
            origin,
            reason=provider_error.detail_code or provider_error.provider_error_class,
            error=to_app_error(provider_error),
            target=refusal,
        )

    if resolution.required and resolution.unsupported:
        # OPEN-95: the governing policy asks for more than one approval. Treating that as "one
        # will do" would silently weaken it, so it is refused, not queued.
        await _refuse(
            db,
            origin,
            reason=codes.APPROVAL_POLICY_UNSUPPORTED,
            error=NotConfiguredError(
                "This operation's approval policy needs more than one approval, which is not "
                "supported.",
                details={"reason": codes.APPROVAL_POLICY_UNSUPPORTED, "basis": resolution.basis},
            ),
            target=refusal,
        )

    try:
        await ratelimit.enforce(
            "service_operation",
            str(user_id),
            limit=RATE_LIMIT,
            window_seconds=RATE_WINDOW_SECONDS,
            fail_closed=True,
        )
    except ratelimit.RateLimitedError as limited:
        await _refuse(db, origin, reason="rate_limited", error=limited, target=refusal)

    in_flight = await db.execute(
        select(Operation.id)
        .where(
            Operation.target_service_id == service_pk,
            Operation.execution_status.in_(states.IN_FLIGHT),
        )
        .limit(1)
    )
    if in_flight.first() is not None:
        # The live operation may be *this key's own*, committed by a request that raced us after
        # our first lookup: that is a replay, not a conflict.
        raced = await _find_by_key(db, user_id, idempotency_key)
        if raced is not None:
            if not _same_request(raced, server_id=server_pk, service_id=service_pk, body=body):
                raise _identity_conflict()
            return raced, True
        await _refuse(
            db,
            origin,
            reason="operation_in_progress",
            error=OperationInProgressError(),
            target=refusal,
        )

    now = datetime.now(UTC)
    terms = resolution.terms if resolution.required else None
    # OPEN-96: a request that must be approved waits, and while it waits its only deadline is
    # the end of its approval window; an approved one gets its full timeout from the approval.
    expires_at = now + timedelta(seconds=terms.approval_window_seconds) if terms else None
    operation = Operation(
        target_server_id=server_pk,
        target_service_id=service_pk,
        environment=environment,
        action_type=action,
        requested_by=user_id,
        requested_reason=body.reason,
        approval_status="PENDING" if terms else "NOT_REQUIRED",
        execution_status=states.AWAITING_APPROVAL if terms else states.PENDING,
        idempotency_key=idempotency_key,
        correlation_id=audit.correlation_uuid(),
        provider=adapter.adapter_id,
        sanitized_result={"adapter_id": adapter.adapter_id},
        created_at=now,
        deadline_at=expires_at or states.deadline_at(now, timeout_seconds),
    )
    db.add(operation)
    try:
        await db.flush()
    except IntegrityError as integrity:
        constraint = _constraint(integrity)
        await db.rollback()
        if constraint == "uq_operations_idempotency":
            # Two submissions of one key raced; the other won. This one is a replay.
            winner = await _find_by_key(db, user_id, idempotency_key)
            if winner is None:
                raise
            if not _same_request(winner, server_id=server_pk, service_id=service_pk, body=body):
                raise _identity_conflict() from integrity
            return winner, True
        if constraint == "uq_operations_single_inflight":
            raced = await _find_by_key(db, user_id, idempotency_key)
            if raced is not None:
                if not _same_request(raced, server_id=server_pk, service_id=service_pk, body=body):
                    raise _identity_conflict() from integrity
                return raced, True
            await _refuse(
                db,
                origin,
                reason="operation_in_progress",
                error=OperationInProgressError(),
                target=refusal,
            )
        raise
    approval_id = None
    if terms is not None and expires_at is not None:
        approval_id = (
            await approval_requests.open_request(
                db, operation=operation, terms=terms, unit=unit, expires_at=expires_at
            )
        ).id
    audit.record(
        db,
        action_type="SERVICE_OPERATION_REQUESTED",
        outcome="SUCCESS",
        actor_id=origin.actor_id,
        actor_label=origin.actor_label,
        target_type="operation",
        target_id=operation.id,
        environment=environment,
        approval_id=approval_id,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata={
            "server_id": str(server_pk),
            "service_id": str(service_pk),
            "service_name": unit,
            "action": action,
            "provider": adapter.adapter_id,
            "reason_given": body.reason is not None,
            "approval_required": approval_id is not None,
            **({"approval_basis": resolution.basis} if approval_id is not None else {}),
        },
    )
    await db.commit()
    await db.refresh(operation)
    logger.info("operation_requested", operation_id=str(operation.id), action=action)
    return operation, False


async def enqueue_execution(operation_id: uuid.UUID) -> None:
    """Hand the id to the worker. Best effort, and says so.

    Called after the row has **committed** (ARCHITECTURE §14.1: DB first). A broker that cannot
    be reached must not fail a request that already succeeded; the healer re-enqueues an
    unclaimed row (``OPEN-70``). The task takes the id and nothing else (SECURITY §22).
    """

    def publish() -> None:
        from app.workers.celery_app import celery_app

        celery_app.send_task(TASK_NAME, args=[str(operation_id)], queue="operations", retry=False)

    try:
        await asyncio.to_thread(publish)
    except Exception as error:
        logger.warning("operation_enqueue_failed", error=type(error).__name__)


# --- reads --------------------------------------------------------------------------------


async def _labels(db: AsyncSession, operation: Operation) -> tuple[str, str, str]:
    """Server display name, unit name and requester display name, for the response."""
    server_name = (
        await db.execute(select(Server.display_name).where(Server.id == operation.target_server_id))
    ).scalar_one_or_none()
    unit = None
    if operation.target_service_id is not None:
        unit = (
            await db.execute(
                select(Service.service_name).where(Service.id == operation.target_service_id)
            )
        ).scalar_one_or_none()
    requester = (
        await db.execute(select(User.display_name).where(User.id == operation.requested_by))
    ).scalar_one_or_none()
    return server_name or "", unit or "", requester or ""


def _poll(operation: Operation) -> int | None:
    return None if states.is_terminal(operation.execution_status) else POLL_AFTER_SECONDS


async def accepted_view(
    db: AsyncSession, operation: Operation, *, replayed: bool
) -> OperationAccepted:
    server_name, unit, requester = await _labels(db, operation)
    return OperationAccepted(
        operation_id=operation.id,
        execution_status=states.public_status(operation.execution_status),  # type: ignore[arg-type]
        approval_status=operation.approval_status,  # type: ignore[arg-type]
        action_type=operation.action_type,
        target=TargetView(
            server_id=operation.target_server_id,
            server_display_name=server_name,
            service_name=unit,
            environment=operation.environment,
        ),
        requested_by=ActorView(id=operation.requested_by, display_name=requester),
        requested_reason=operation.requested_reason,
        approval=await approval_requests.summary(db, operation),
        deadline_at=operation.deadline_at,
        created_at=operation.created_at,
        correlation_id=operation.correlation_id,
        poll_after_seconds=_poll(operation),
        idempotent_replay=replayed,
    )


async def detail_view(db: AsyncSession, operation: Operation) -> OperationDetail:
    base = (await accepted_view(db, operation, replayed=False)).model_dump(
        exclude={"idempotent_replay"}
    )
    succeeded = operation.execution_status == states.SUCCEEDED
    return OperationDetail(
        **base,
        observed_state_before=operation.observed_state_before,
        observed_state_after=operation.observed_state_after,
        verified_at=operation.completed_at if succeeded else None,
        sanitized_result=operation.sanitized_result,
        failure_reason=operation.failure_reason,
        external_execution_id=operation.external_execution_id,
        parent_operation_id=operation.parent_operation_id,
        started_at=operation.started_at,
        completed_at=operation.completed_at,
    )


async def visible_operation(
    db: AsyncSession, authorized: Authorized, operation_id: uuid.UUID
) -> tuple[Operation, Server | None]:
    """The operation, if this caller may see it -- otherwise ``404``, never ``403`` (§2).

    The requester always sees their own; anyone else needs ``operation:read`` over the stored
    environment of the target server. The route's own dependency already refused a caller with
    no ``operation:read`` anywhere.
    """
    operation = await db.get(Operation, operation_id)
    if operation is None:
        raise NotFoundError
    server = await db.get(Server, operation.target_server_id)
    if operation.requested_by == authorized.user.id:
        return operation, server
    if server is None or not authorized.permits("operation", "read", server_target(server)):
        raise NotFoundError
    return operation, server


async def read_operation(
    db: AsyncSession, authorized: Authorized, operation_id: uuid.UUID
) -> OperationDetail:
    operation, _ = await visible_operation(db, authorized, operation_id)
    return await detail_view(db, operation)


async def cancel_operation(
    db: AsyncSession, origin: Origin, authorized: Authorized, operation_id: uuid.UUID
) -> OperationDetail:
    """§17.3. Legal only before a worker has claimed the row."""
    operation, server = await visible_operation(db, authorized, operation_id)
    is_requester = operation.requested_by == authorized.user.id
    if not is_requester and (
        server is None or not authorized.permits("operation", "approve", server_target(server))
    ):
        authz_service.record_denial(
            db,
            user_id=origin.actor_id,
            user_label=origin.actor_label,
            permission="operation:approve",
            reason="not_requester_or_approver",
            method=origin.method,
            path=origin.path,
            client_ip=origin.client_ip,
            user_agent=origin.user_agent,
            target_type="operation",
            target_id=operation.id,
            environment=operation.environment,
        )
        await db.commit()
        raise ForbiddenError

    awaiting = operation.execution_status == states.AWAITING_APPROVAL
    try:
        statement = transition_statement(
            operation.id,
            operation.execution_status,
            states.CANCELLED,
            approval_status="EXPIRED" if awaiting else None,
            result={"reason_code": "cancelled"} if awaiting else None,
        )
    except states.InvalidTransitionError as illegal:
        raise InvalidStateTransitionError(
            "That operation can no longer be cancelled.",
        ) from illegal
    operation_pk, environment = operation.id, operation.environment
    metadata = {
        "server_id": str(operation.target_server_id),
        "action": operation.action_type,
        "from": operation.execution_status,
    }
    # OPEN-100: the approval row is written first. Its guard decides a race with a decision or
    # the sweep, and a cancel ends the open request so none outlives its operation.
    approval_id = await approval_requests.withdraw(db, operation) if awaiting else None
    if (await db.execute(statement)).first() is None:
        # A worker claimed it, or an approver decided it, between the read and the write: a
        # false "cancelled" would be a lie about system state (API_CONTRACTS §17.3).
        await db.rollback()
        raise InvalidStateTransitionError("That operation can no longer be cancelled.")
    audit.record(
        db,
        action_type="SERVICE_OPERATION_CANCELLED",
        outcome="SUCCESS",
        actor_id=origin.actor_id,
        actor_label=origin.actor_label,
        target_type="operation",
        target_id=operation_pk,
        environment=environment,
        approval_id=approval_id,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata=metadata,
    )
    await db.commit()
    db.expire_all()
    refreshed = await db.get(Operation, operation_pk)
    if refreshed is None:  # pragma: no cover -- rows are never deleted
        raise NotFoundError
    logger.info("operation_cancelled", operation_id=str(operation_pk))
    return await detail_view(db, refreshed)
