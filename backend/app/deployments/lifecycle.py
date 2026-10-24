"""Requesting, reading and cancelling a deployment (§21.1 – §21.3, §21.6; ``OPEN-133`` – ``142``).

The order of checks is ``M5.2_PLAN.md`` D8 with one deliberate change: **the idempotency replay
lookup comes straight after the permission check**, so a replay returns the original whatever
Jenkins' or the target's state is now, and spends no rate-limit budget. A caller who may not
deploy learns nothing about registration or configuration: permission is checked before the target
is looked up.

**The database is the guard, not the pre-checks.** Two concurrent submissions of one key, or two
different keys against one application and environment, are decided by
``uq_deployments_idempotency`` and ``uq_deployments_single_inflight``; the courtesy pre-check
exists only for a clean message.

Nothing here talks to Jenkins. The worker (``OPEN-136``) does, after re-authorising and
re-validating everything this module checked, and it never trusts a flag on the row.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.authz import service as authz_service
from app.authz.dependencies import Authorized, ForbiddenError
from app.authz.matcher import Target, permits
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import AppError, NotConfiguredError, field_error
from app.core.logging import get_logger
from app.deployments import refs, states
from app.deployments.approval import APPROVAL_WORKFLOW_UNAVAILABLE, approval_required
from app.deployments.deployment_schemas import (
    DeploymentAccepted,
    DeploymentDetail,
    DeploymentItem,
    DeploymentRequest,
    TargetEcho,
)
from app.deployments.digest import target_digest
from app.deployments.models import Deployment, DeploymentTarget
from app.deployments.service import NotRegisteredError, resolve_target
from app.deployments.store import transition_statement
from app.inventory.models import Application
from app.inventory.service import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    get_application,
)
from app.operations.schemas import ActorView
from app.operations.service import Origin

logger = get_logger(__name__)

#: SECURITY.md §13: deployment creation, 10 per hour per identity per environment. A privileged
#: class, so it fails closed when Redis is unavailable.
RATE_LIMIT: int = 10
RATE_WINDOW_SECONDS: int = 3_600
#: Re-ask after this long while a deployment is not terminal.
POLL_AFTER_SECONDS: int = 5
TASK_NAME = "dcp.deployments.execute"


class DeploymentInProgressError(AppError):
    status_code = 409
    code = "operation_in_progress"
    message = "A deployment is already in progress for this application and environment."


class RefNotPermittedError(AppError):
    """The ref is well formed and no allowed pattern admits it. The patterns are not a secret:
    §21.8 shows them to any reader, and the contract puts them in ``details``."""

    status_code = 422
    code = "not_permitted"
    message = "That ref is not permitted for this deployment target."

    def __init__(self, patterns: list[str]) -> None:
        super().__init__(
            self.message, details={"reason": "ref_not_permitted", "allowed_ref_patterns": patterns}
        )


def _identity_conflict() -> ConflictError:
    error = ConflictError("This Idempotency-Key was already used with a different request body.")
    error.details = {"reason": "idempotency_key_reused_with_different_payload"}
    return error


def _constraint(error: IntegrityError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


def matcher_target(environment: str, application_id: uuid.UUID) -> Target:
    """What the matcher judges a deployment by: environment and application, **stored values**."""
    return Target(environment=environment, application=str(application_id))


async def _refuse(
    db: AsyncSession,
    origin: Origin,
    *,
    application_id: uuid.UUID,
    environment: str,
    reason: str,
    error: AppError,
) -> NoReturn:
    """Audit a refusal, commit it (the failed request must still leave a trail), then raise.

    The metadata is the closed reason alone: nothing the caller typed, so nothing to forge a log
    line with.
    """
    audit.record(
        db,
        action_type="DEPLOYMENT_DENIED",
        outcome="DENIED",
        actor_id=origin.actor_id,
        actor_label=origin.actor_label,
        target_type="application",
        target_id=application_id,
        environment=environment,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata={"reason": reason},
    )
    await db.commit()
    raise error


async def _find_by_key(db: AsyncSession, user_id: uuid.UUID, key: str) -> Deployment | None:
    return (
        await db.execute(
            select(Deployment).where(
                Deployment.requested_by == user_id, Deployment.idempotency_key == key
            )
        )
    ).scalar_one_or_none()


def _same_request(existing: Deployment, body: DeploymentRequest) -> bool:
    return (
        existing.application_id == body.application_id
        and existing.environment == body.environment
        and existing.requested_ref == body.ref
        and (existing.requested_reason or None) == body.reason
    )


async def request_deployment(
    db: AsyncSession,
    origin: Origin,
    authorized: Authorized,
    *,
    body: DeploymentRequest,
    idempotency_key: str,
) -> tuple[Deployment, bool]:
    """§21.2. Returns ``(deployment, replayed)``."""
    user_id = authorized.user.id
    application_id, environment = body.application_id, body.environment

    # 1. Permission, on the environment and the application, before anything else is revealed.
    decision = permits(
        authorized.grants, "deployment", "create", matcher_target(environment, application_id)
    )
    if not decision.allowed:
        authz_service.record_denial(
            db,
            user_id=origin.actor_id,
            user_label=origin.actor_label,
            permission="deployment:create",
            reason=decision.reason,
            method=origin.method,
            path=origin.path,
            client_ip=origin.client_ip,
            user_agent=origin.user_agent,
            target_type="application",
            target_id=application_id,
            environment=environment,
        )
        await _refuse(
            db,
            origin,
            application_id=application_id,
            environment=environment,
            reason="permission_denied",
            error=ForbiddenError(),
        )

    # 2. A replay returns the original: whatever the target or Jenkins is now, and for free.
    existing = await _find_by_key(db, user_id, idempotency_key)
    if existing is not None:
        if not _same_request(existing, body):
            raise _identity_conflict()
        return existing, True

    # 3. The application, then the one registered target (never a default, never "closest").
    application = await get_application(db, application_id)  # 404 for a missing or archived one
    refusal: dict[str, Any] = {"application_id": application_id, "environment": environment}
    try:
        target = await resolve_target(db, application_id, environment)
    except NotRegisteredError as not_registered:
        await _refuse(db, origin, reason="not_registered", error=not_registered, **refusal)
    # Plain values from here on: a rollback expires every loaded row, and the async session
    # cannot lazy-load it back.
    application_name = application.name
    target_pk, pipeline = target.id, target.pipeline_reference
    template = dict(target.parameter_template)
    patterns = list(target.allowed_ref_patterns)
    policy_linked = target.approval_policy_id is not None

    # 4. The ref: its grammar first (a field error naming the rule), then the target's patterns.
    if not refs.is_valid_ref(body.ref):
        raise field_error(
            ["body", "ref"],
            "invalid_ref",
            "A ref is a branch, tag or commit name: letters, digits and . _ / - only.",
        )
    if not refs.ref_permitted(patterns, body.ref):
        await _refuse(
            db, origin, reason="ref_not_permitted", error=RefNotPermittedError(patterns), **refusal
        )

    # 5. Is there a Jenkins to run it, and is this a real deployment of DCP?
    settings = get_settings()
    if settings.demo_mode:
        await _refuse(
            db,
            origin,
            reason="demonstration_mode",
            error=NotConfiguredError(
                "Demonstration mode does not run deployments.",
                details={"reason": "demonstration_mode"},
            ),
            **refusal,
        )
    if settings.jenkins_url is None:
        await _refuse(
            db,
            origin,
            reason="jenkins_not_configured",
            error=NotConfiguredError(
                "Jenkins is not configured.", details={"reason": "jenkins_not_configured"}
            ),
            **refusal,
        )

    # 6. An approval cannot be given until M5.4: a request that needs one is refused, not queued.
    if approval_required(environment=environment, policy_linked=policy_linked):
        await _refuse(
            db,
            origin,
            reason=APPROVAL_WORKFLOW_UNAVAILABLE,
            error=NotConfiguredError(
                "This deployment needs an approval, and the approval workflow is not available "
                "yet.",
                details={"reason": APPROVAL_WORKFLOW_UNAVAILABLE},
            ),
            **refusal,
        )

    # 7. The rate limit: 10 an hour per identity per environment, failing closed.
    try:
        await ratelimit.enforce(
            "deployment_create",
            f"{user_id}:{environment}",
            limit=RATE_LIMIT,
            window_seconds=RATE_WINDOW_SECONDS,
            fail_closed=True,
        )
    except ratelimit.RateLimitedError as limited:
        await _refuse(db, origin, reason="rate_limited", error=limited, **refusal)

    # 8. One active deployment per application and environment. This pre-check only gives a clean
    #    message; the unique index below is the guarantee.
    in_flight = await db.execute(
        select(Deployment.id)
        .where(
            Deployment.application_id == application_id,
            Deployment.environment == environment,
            Deployment.execution_status.in_(states.IN_FLIGHT),
        )
        .limit(1)
    )
    if in_flight.first() is not None:
        # The live deployment may be *this key's own*, committed by a request that raced us after
        # our first lookup: that is a replay, not a conflict.
        raced = await _find_by_key(db, user_id, idempotency_key)
        if raced is not None:
            if not _same_request(raced, body):
                raise _identity_conflict()
            return raced, True
        await _refuse(
            db,
            origin,
            reason="operation_in_progress",
            error=DeploymentInProgressError(),
            **refusal,
        )

    now = datetime.now(UTC)
    deployment = Deployment(
        application_id=application_id,
        deployment_target_id=target_pk,
        environment=environment,
        pipeline_reference=pipeline,
        requested_ref=body.ref,
        kind="DEPLOY",
        requested_by=user_id,
        requested_reason=body.reason,
        approval_status="NOT_REQUIRED",
        execution_status=states.REQUESTED,
        idempotency_key=idempotency_key,
        correlation_id=audit.correlation_uuid(),
        target_digest=target_digest(pipeline, template),
        created_at=now,
        deadline_at=states.deadline_at(now, settings.deployment_deadline_seconds),
    )
    db.add(deployment)
    try:
        await db.flush()
    except IntegrityError as integrity:
        constraint = _constraint(integrity)
        await db.rollback()
        if constraint in {"uq_deployments_idempotency", "uq_deployments_single_inflight"}:
            raced = await _find_by_key(db, user_id, idempotency_key)
            if raced is not None:
                # Two submissions of one key raced; the other won. This one is a replay.
                if not _same_request(raced, body):
                    raise _identity_conflict() from integrity
                return raced, True
            if constraint == "uq_deployments_single_inflight":
                await _refuse(
                    db,
                    origin,
                    reason="operation_in_progress",
                    error=DeploymentInProgressError(),
                    **refusal,
                )
        raise
    audit.record(
        db,
        action_type="DEPLOYMENT_REQUESTED",
        outcome="SUCCESS",
        actor_id=origin.actor_id,
        actor_label=origin.actor_label,
        target_type="deployment",
        target_id=deployment.id,
        environment=environment,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        correlation_id=deployment.correlation_id,
        # Ids and the validated ref; the reason by its length only (OPEN-142).
        metadata={
            "application_id": str(application_id),
            "deployment_target_id": str(target_pk),
            "requested_ref": body.ref,
            "reason_length": len(body.reason) if body.reason is not None else None,
        },
    )
    await db.commit()
    await db.refresh(deployment)
    logger.info(
        "deployment_requested", deployment_id=str(deployment.id), application=application_name
    )
    return deployment, False


async def enqueue_execution(deployment_id: uuid.UUID) -> None:
    """Hand the id to the worker. Best effort, and says so.

    Called after the row has **committed** (ARCHITECTURE §14.1: DB first). A broker that cannot be
    reached must not fail a request that already succeeded; the healer re-enqueues an unclaimed row
    (``OPEN-136``). The task takes the id and nothing else (SECURITY §22).
    """

    def publish() -> None:
        from app.workers.celery_app import celery_app

        celery_app.send_task(TASK_NAME, args=[str(deployment_id)], queue="deployments", retry=False)

    try:
        await asyncio.to_thread(publish)
    except Exception as error:
        logger.warning("deployment_enqueue_failed", error=type(error).__name__)


# --- reads --------------------------------------------------------------------------------


def _poll(deployment: Deployment) -> int | None:
    return None if states.is_terminal(deployment.execution_status) else POLL_AFTER_SECONDS


def _echo(deployment: Deployment, application_name: str, rollback_supported: bool) -> TargetEcho:
    return TargetEcho(
        application_id=deployment.application_id,
        application_name=application_name,
        environment=deployment.environment,
        pipeline_reference=deployment.pipeline_reference,
        requested_ref=deployment.requested_ref,
        rollback_supported=rollback_supported,
    )


async def _names(db: AsyncSession, deployment: Deployment) -> tuple[str, str, bool]:
    """Application name, requester name and the target's rollback support."""
    application = (
        await db.execute(
            select(Application.name).where(Application.id == deployment.application_id)
        )
    ).scalar_one_or_none()
    requester = (
        await db.execute(select(User.display_name).where(User.id == deployment.requested_by))
    ).scalar_one_or_none()
    rollback = (
        await db.execute(
            select(DeploymentTarget.rollback_supported).where(
                DeploymentTarget.id == deployment.deployment_target_id
            )
        )
    ).scalar_one_or_none()
    return application or "", requester or "", bool(rollback)


async def accepted_view(
    db: AsyncSession, deployment: Deployment, *, replayed: bool
) -> DeploymentAccepted:
    application, _, rollback = await _names(db, deployment)
    return DeploymentAccepted(
        deployment_id=deployment.id,
        execution_status=deployment.execution_status,  # type: ignore[arg-type]
        approval_status=deployment.approval_status,  # type: ignore[arg-type]
        target=_echo(deployment, application, rollback),
        approval=None,
        deadline_at=deployment.deadline_at,
        correlation_id=deployment.correlation_id,
        poll_after_seconds=_poll(deployment),
        idempotent_replay=replayed,
    )


def item_view(deployment: Deployment, application_name: str, requester_name: str) -> DeploymentItem:
    return DeploymentItem(
        deployment_id=deployment.id,
        kind=deployment.kind,  # type: ignore[arg-type]
        application_id=deployment.application_id,
        application_name=application_name,
        environment=deployment.environment,
        requested_ref=deployment.requested_ref,
        commit_sha=deployment.commit_sha,
        execution_status=deployment.execution_status,  # type: ignore[arg-type]
        approval_status=deployment.approval_status,  # type: ignore[arg-type]
        requested_by=ActorView(id=deployment.requested_by, display_name=requester_name),
        created_at=deployment.created_at,
        started_at=deployment.started_at,
        completed_at=deployment.completed_at,
        deadline_at=deployment.deadline_at,
        failure_reason=deployment.failure_reason,
        correlation_id=deployment.correlation_id,
    )


async def detail_view(db: AsyncSession, deployment: Deployment) -> DeploymentDetail:
    application, requester, rollback = await _names(db, deployment)
    pipeline_result = deployment.sanitized_result.get("pipeline_result")
    return DeploymentDetail(
        **item_view(deployment, application, requester).model_dump(),
        target=_echo(deployment, application, rollback),
        requested_reason=deployment.requested_reason,
        external_execution_id=deployment.external_execution_id,
        pipeline_result=pipeline_result if isinstance(pipeline_result, str) else None,  # type: ignore[arg-type]
        health_check_result=deployment.health_check_result,
        sanitized_result=deployment.sanitized_result,
        last_poll_error=deployment.last_poll_error,
        last_polled_at=deployment.last_polled_at,
        rollback_of_deployment_id=deployment.rollback_of_deployment_id,
        poll_after_seconds=_poll(deployment),
    )


async def visible_deployment(
    db: AsyncSession, authorized: Authorized, deployment_id: uuid.UUID
) -> Deployment:
    """The deployment, if this caller may see it -- otherwise ``404``, never ``403`` (§2).

    The requester always sees their own; anyone else needs ``deployment:read`` over the stored
    environment and application (``OPEN-140``).
    """
    deployment = await db.get(Deployment, deployment_id)
    if deployment is None:
        raise NotFoundError
    if deployment.requested_by == authorized.user.id:
        return deployment
    if not authorized.permits(
        "deployment", "read", matcher_target(deployment.environment, deployment.application_id)
    ):
        raise NotFoundError
    return deployment


async def read_deployment(
    db: AsyncSession, authorized: Authorized, deployment_id: uuid.UUID
) -> DeploymentDetail:
    return await detail_view(db, await visible_deployment(db, authorized, deployment_id))


async def cancel_deployment(
    db: AsyncSession, origin: Origin, authorized: Authorized, deployment_id: uuid.UUID
) -> DeploymentDetail:
    """§21.6. Legal only before a worker has claimed the row."""
    deployment = await visible_deployment(db, authorized, deployment_id)
    is_requester = deployment.requested_by == authorized.user.id
    target = matcher_target(deployment.environment, deployment.application_id)
    if not is_requester and not authorized.permits("deployment", "approve", target):
        authz_service.record_denial(
            db,
            user_id=origin.actor_id,
            user_label=origin.actor_label,
            permission="deployment:approve",
            reason="not_requester_or_approver",
            method=origin.method,
            path=origin.path,
            client_ip=origin.client_ip,
            user_agent=origin.user_agent,
            target_type="deployment",
            target_id=deployment.id,
            environment=deployment.environment,
        )
        await db.commit()
        raise ForbiddenError

    refusal = InvalidStateTransitionError("That deployment can no longer be cancelled.")
    # AWAITING_APPROVAL is cancellable in the table, but the approval row's side of a cancel is
    # M5.4's and nothing in M5.2 creates one: refuse it rather than half-handle it.
    if deployment.execution_status != states.REQUESTED:
        raise refusal
    deployment_pk, environment = deployment.id, deployment.environment
    metadata = {
        "application_id": str(deployment.application_id),
        "from": deployment.execution_status,
    }
    statement = transition_statement(deployment_pk, deployment.execution_status, states.CANCELLED)
    if (await db.execute(statement)).first() is None:
        # A worker claimed it between the read and the write: a false "cancelled" would be a lie
        # about system state (API_CONTRACTS §21.6).
        await db.rollback()
        raise refusal
    audit.record(
        db,
        action_type="DEPLOYMENT_CANCELLED",
        outcome="SUCCESS",
        actor_id=origin.actor_id,
        actor_label=origin.actor_label,
        target_type="deployment",
        target_id=deployment_pk,
        environment=environment,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata=metadata,
    )
    await db.commit()
    await db.refresh(deployment)
    return await detail_view(db, deployment)
