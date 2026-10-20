"""The request-side half of the approval workflow, which ``operations.service`` calls.

Kept apart from :mod:`app.approvals.service` (the decision and the queue) because that module reads
operations through ``operations.service``, and the request path in ``operations.service`` needs
these functions: one direction of import each.

* :func:`terms_for` -- the policy a request is decided under, resolved **fresh** from the service's
  action policy (the same pure :func:`app.approvals.rules.resolve_policy` the worker uses), falling
  back to the strict built-in defaults when it no longer applies.
* :func:`open_request` -- the ``approvals`` row and the pending-approval alert, in the caller's
  transaction.
* :func:`withdraw` -- a cancel ends the open request in the same transaction, so no open request
  outlives its operation (``OPEN-100``).
* :func:`summary` -- the ``approval`` object of §17.1 / §17.2.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import alerting
from app.approvals.models import Approval, ApprovalPolicy
from app.approvals.rules import DEFAULT_TERMS, SERVICE_OPERATION, PolicyTerms, resolve_policy
from app.approvals.standing import policy_row
from app.approvals.store import expire_statement
from app.auth.models import User
from app.inventory.models import Service, ServiceActionPolicy
from app.operations import states
from app.operations.models import Operation
from app.operations.schemas import ActorView, ApprovalSummary

#: Used only when the action policy cannot be read at decision time; the worker re-checks it.
FALLBACK_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class Context:
    terms: PolicyTerms
    #: The action policy's timeout, which an approved operation gets afresh (``OPEN-96``).
    timeout_seconds: int


async def context_for(db: AsyncSession, operation: Operation) -> Context:
    """The terms this operation's approval is decided under, read now."""
    service = (
        None
        if operation.target_service_id is None
        else await db.get(Service, operation.target_service_id)
    )
    policy = (
        None
        if service is None or service.action_policy_id is None
        else await db.get(ServiceActionPolicy, service.action_policy_id)
    )
    approval_policy = (
        None
        if policy is None or policy.approval_policy_id is None
        else await db.get(ApprovalPolicy, policy.approval_policy_id)
    )
    resolution = resolve_policy(
        environment=operation.environment,
        action=operation.action_type,
        policy_linked=policy is not None and policy.approval_policy_id is not None,
        policy=policy_row(approval_policy),
    )
    timeout = FALLBACK_TIMEOUT_SECONDS
    if policy is not None and (
        states.MIN_TIMEOUT_SECONDS <= policy.operation_timeout_seconds <= states.MAX_TIMEOUT_SECONDS
    ):
        timeout = policy.operation_timeout_seconds
    # A request that is waiting but no longer needs approval is still decided under the strict
    # defaults: a doubt never loosens who may decide.
    return Context(resolution.terms or DEFAULT_TERMS, timeout)


async def open_request(
    db: AsyncSession,
    *,
    operation: Operation,
    terms: PolicyTerms,
    unit: str,
    expires_at: datetime,
) -> Approval:
    """Create the ``PENDING`` approval and raise the alert. The caller commits."""
    approval = Approval(
        operation_type=SERVICE_OPERATION,
        operation_id=operation.id,
        approval_policy_id=terms.policy_id,
        requested_by=operation.requested_by,
        decision="PENDING",
        expires_at=expires_at,
        created_at=operation.created_at,
    )
    db.add(approval)
    await db.flush()
    if operation.target_service_id is None:
        raise ValueError("an approval request needs a service target")
    server_id, service_id = operation.target_server_id, operation.target_service_id
    await db.run_sync(
        lambda session: alerting.raise_pending(
            session,
            approval_id=approval.id,
            operation_id=operation.id,
            server_id=server_id,
            service_id=service_id,
            environment=operation.environment,
            action=operation.action_type,
            unit=unit,
            expires_at=expires_at,
        )
    )
    return approval


async def approval_for(db: AsyncSession, operation_id: uuid.UUID) -> Approval | None:
    return (
        await db.execute(
            select(Approval)
            .where(
                Approval.operation_type == SERVICE_OPERATION, Approval.operation_id == operation_id
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def withdraw(db: AsyncSession, operation: Operation) -> uuid.UUID | None:
    """End the operation's open request because it is being cancelled. The caller commits.

    The approval row is written **first** (its guard is the arbiter against a concurrent decision
    or sweep). ``None`` means there was no open request to end: the cancel then loses to whatever
    moved it, or finds an operation with no approval at all (an inconsistent row), and the guarded
    transition that follows decides.
    """
    approval = await approval_for(db, operation.id)
    if approval is None:
        return None
    approval_id = approval.id
    if (await db.execute(expire_statement(approval_id, only_if_due=False))).first() is None:
        return None
    if operation.target_service_id is not None:
        service_id = operation.target_service_id
        await db.run_sync(
            lambda session: alerting.resolve_pending(
                session, approval_id=approval_id, service_id=service_id
            )
        )
    return approval_id


async def summary(db: AsyncSession, operation: Operation) -> ApprovalSummary | None:
    """§17.1 / §17.2's ``approval`` object; ``None`` for an operation that never needed one."""
    approval = await approval_for(db, operation.id)
    if approval is None:
        return None
    context = await context_for(db, operation)
    decider = None
    if approval.approved_by is not None:
        name = (
            await db.execute(select(User.display_name).where(User.id == approval.approved_by))
        ).scalar_one_or_none()
        decider = ActorView(id=approval.approved_by, display_name=name or "")
    return ApprovalSummary(
        approval_id=approval.id,
        required_approvals=1,
        expires_at=approval.expires_at,
        decision=approval.decision,  # type: ignore[arg-type]
        required_role=context.terms.required_role,
        approved_by=decider,
        decided_at=approval.decided_at,
    )
