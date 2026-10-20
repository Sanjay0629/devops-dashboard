"""Deciding a request, and the approver's queue (endpoints 22.1 – 22.4; ``OPEN-97`` – ``OPEN-103``).

**A decision is one transaction and the approval row arbitrates.** The guarded ``UPDATE approvals
... WHERE decision = 'PENDING' AND expires_at > now()`` runs first (`app/approvals/store.py`); only
its winner moves the operation, writes the audit row and resolves the alert, and they commit
together. A second approver, a cancel and the expiry sweep all go through the same guard, so a
loser sees an empty ``RETURNING`` and answers ``409`` -- never a false success.

The order of checks is API_CONTRACTS §22.3: session and ``operation:approve`` (the route), the
operation exists and is visible (``404``), it is waiting and inside its window (``409``), scope,
separation of duties, role (``403``, audited), a reason on a rejection (``422``). Everything is
read from the database by the id in the path; the body carries a reason and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Select, and_, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.approvals import alerting, sweep
from app.approvals import requests as approval_requests
from app.approvals.models import Approval, ApprovalPolicy
from app.approvals.rules import (
    APPROVER_ROLE_REQUIRED,
    SELF_APPROVAL_NOT_PERMITTED,
    SERVICE_OPERATION,
    may_decide,
)
from app.approvals.schemas import (
    ApprovalDecision,
    ApprovalDetail,
    ApprovalItem,
    ApprovalList,
    DecisionSoFar,
    PolicyView,
    TargetState,
)
from app.approvals.standing import roles_held
from app.approvals.store import decide_statement
from app.audit import service as audit
from app.auth.models import User
from app.authz.dependencies import Authorized, ForbiddenError
from app.authz.matcher import Target
from app.authz.queries import readable_servers
from app.core.errors import field_error
from app.core.logging import get_logger
from app.core.pagination import Cursor, decode_cursor, encode_cursor
from app.inventory.models import Server, Service
from app.inventory.schemas import CursorPage
from app.inventory.service import InvalidStateTransitionError, NotFoundError
from app.operations import service as operations_service
from app.operations import states
from app.operations.models import Operation
from app.operations.schemas import ActorView, OperationDetail, TargetView
from app.operations.store import transition_statement
from app.services.normalise import is_stale_cache

logger = get_logger(__name__)

#: ``details.reason`` on a ``409``.
APPROVAL_EXPIRED: Final = "approval_expired"
NOT_AWAITING_APPROVAL: Final = "not_awaiting_approval"


def _conflict(reason: str) -> InvalidStateTransitionError:
    error = InvalidStateTransitionError("That request can no longer be decided.")
    error.details = {"reason": reason}
    return error


def _refused(reason: str) -> ForbiddenError:
    error = ForbiddenError()
    # Only the two reasons the contract names are said aloud; scope stays the uniform 403.
    if reason in (SELF_APPROVAL_NOT_PERMITTED, APPROVER_ROLE_REQUIRED):
        error.details = {"reason": reason}
    return error


# --- the decision ---------------------------------------------------------------------------


async def decide(
    db: AsyncSession,
    origin: operations_service.Origin,
    authorized: Authorized,
    operation_id: uuid.UUID,
    *,
    approve: bool,
    reason: str | None,
) -> tuple[OperationDetail, bool]:
    """§22.3 / §22.4. Returns the operation and whether the caller must now enqueue it."""
    operation, server = await operations_service.visible_operation(db, authorized, operation_id)
    if server is None:
        raise NotFoundError
    approval = await approval_requests.approval_for(db, operation.id)
    if approval is None or operation.execution_status != states.AWAITING_APPROVAL:
        raise _conflict(await _why_not(db, operation, approval))
    if approval.decision != "PENDING":
        raise _conflict(await _why_not(db, operation, approval))
    approval_pk, operation_pk = approval.id, operation.id
    requester, environment = operation.requested_by, operation.environment
    action, server_pk = operation.action_type, operation.target_server_id
    service_pk = operation.target_service_id
    cloud_account = str(server.cloud_account_id)
    window_over = approval.expires_at <= datetime.now(UTC)
    if window_over:
        # The sweep's effect, applied now, so the answer below is true and the slot is free.
        expired_now = await db.run_sync(
            lambda session: sweep.expire_in_session(session, approval_pk)
        )
        if not expired_now:
            await db.rollback()  # someone else moved it first; say what it is now
            raise _conflict(await _lost(db, approval_pk))
        await db.commit()
        raise _conflict(APPROVAL_EXPIRED)

    context = await approval_requests.context_for(db, operation)
    verdict = may_decide(
        requester_id=requester,
        decider_id=authorized.user.id,
        policy=context.terms,
        grants=authorized.grants,
        # The operation's *stored* environment, never the server's current label (SECURITY §4).
        target=Target(environment=environment, cloud_account=cloud_account, server=str(server_pk)),
        decider_roles=await roles_held(db, authorized.user.id),
    )
    decider_id = authorized.user.id
    if not verdict.allowed:
        audit.record(
            db,
            action_type="SERVICE_OPERATION_DENIED",
            outcome="DENIED",
            actor_id=decider_id,
            actor_label=origin.actor_label,
            target_type="operation",
            target_id=operation_pk,
            environment=environment,
            approval_id=approval_pk,
            client_ip=origin.client_ip,
            user_agent=origin.user_agent,
            metadata={
                "phase": "approval_decision",
                "decision": "approve" if approve else "reject",
                "reason": verdict.reason,
                "action": action,
                "server_id": str(server_pk),
            },
        )
        await db.commit()
        raise _refused(verdict.reason or "")
    if not approve and reason is None:
        raise field_error(["body", "reason"], "required", "A reason is required to reject.")

    decision = "APPROVED" if approve else "REJECTED"
    won = (
        await db.execute(
            decide_statement(approval_pk, decision=decision, decider=decider_id, reason=reason)
        )
    ).first()
    if won is None:
        await db.rollback()
        raise _conflict(await _lost(db, approval_pk))
    moved = (
        await db.execute(
            transition_statement(
                operation_pk,
                states.AWAITING_APPROVAL,
                states.APPROVED if approve else states.REJECTED,
                approval_status=decision,
                deadline_after_seconds=context.timeout_seconds if approve else None,
                result=None if approve else {"reason_code": "approval_rejected"},
            )
        )
    ).first()
    if moved is None:
        # The approval was ours but the operation had already left AWAITING_APPROVAL: an
        # inconsistent row. Nothing is written; the caller is told the truth.
        await db.rollback()
        raise _conflict(NOT_AWAITING_APPROVAL)
    audit.record(
        db,
        action_type="SERVICE_OPERATION_APPROVED" if approve else "SERVICE_OPERATION_REJECTED",
        outcome="SUCCESS",
        actor_id=decider_id,
        actor_label=origin.actor_label,
        target_type="operation",
        target_id=operation_pk,
        environment=environment,
        approval_id=approval_pk,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata={
            "requested_by": str(requester),
            "action": action,
            "server_id": str(server_pk),
            # A rejecter's words are user text: audited by length, stored on the approval row.
            "reason_length": 0 if reason is None else len(reason),
            "self_decided": requester == decider_id,
        },
    )
    if service_pk is not None:
        await db.run_sync(
            lambda session: alerting.resolve_pending(
                session, approval_id=approval_pk, service_id=service_pk
            )
        )
    await db.commit()
    db.expire_all()
    refreshed = await db.get(Operation, operation_pk)
    if refreshed is None:  # pragma: no cover -- rows are never deleted
        raise NotFoundError
    logger.info("approval_decided", approval_id=str(approval_pk), decision=decision)
    return await operations_service.detail_view(db, refreshed), approve


async def _why_not(db: AsyncSession, operation: Operation, approval: Approval | None) -> str:
    """Why a request that is not open cannot be decided: a lapsed window, or anything else."""
    if (
        approval is not None
        and approval.decision == "EXPIRED"
        and (operation.sanitized_result or {}).get("reason_code") == sweep.REASON_EXPIRED
    ):
        return APPROVAL_EXPIRED
    return NOT_AWAITING_APPROVAL


async def _lost(db: AsyncSession, approval_id: uuid.UUID) -> str:
    """Name the race a decision lost, from the row as it stands now."""
    db.expire_all()
    current = await db.get(Approval, approval_id)
    if current is not None and current.decision == "PENDING":
        return APPROVAL_EXPIRED  # still open, so the guard that failed was the window
    return NOT_AWAITING_APPROVAL


# --- the queue ------------------------------------------------------------------------------


def _base_query(authorized: Authorized) -> Select[*tuple[Any, ...]]:
    """Every approval the caller may decide on, scope applied **in the SQL** (§20).

    ``operations.environment = servers.environment`` keeps a request whose server was re-labelled
    since it was made out of the queue: the worker would refuse it (``TARGET_VALIDATION_FAILED``)
    and an approver should not be asked to decide something that cannot run.
    """
    requester = aliased(User)
    decider = aliased(User)
    statement: Select[*tuple[Any, ...]] = (
        select(
            Approval,
            Operation,
            Server.display_name,
            Service.service_name,
            requester.display_name,
            decider.display_name,
            ApprovalPolicy.allow_self_approval,
        )
        .join(
            Operation,
            and_(
                Approval.operation_type == SERVICE_OPERATION, Operation.id == Approval.operation_id
            ),
        )
        .join(Server, Server.id == Operation.target_server_id)
        .join(Service, Service.id == Operation.target_service_id)
        .join(requester, requester.id == Approval.requested_by)
        .outerjoin(decider, decider.id == Approval.approved_by)
        .outerjoin(ApprovalPolicy, ApprovalPolicy.id == Approval.approval_policy_id)
        .where(
            Operation.environment == Server.environment,
            readable_servers(authorized.grants, "operation", "approve"),
        )
    )
    return statement


def _item(row: Any, caller: uuid.UUID) -> ApprovalItem:
    approval, operation, server_name, unit, requester_name, decider_name, allows_self = tuple(row)
    return ApprovalItem(
        approval_id=approval.id,
        operation_id=operation.id,
        operation_type="SERVICE_OPERATION",
        requested_by=ActorView(id=approval.requested_by, display_name=requester_name or ""),
        target=TargetView(
            server_id=operation.target_server_id,
            server_display_name=server_name or "",
            service_name=unit or "",
            environment=operation.environment,
        ),
        action_type=operation.action_type,
        reason=operation.requested_reason,
        decision=approval.decision,
        expires_at=approval.expires_at,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
        approved_by=None
        if approval.approved_by is None
        else ActorView(id=approval.approved_by, display_name=decider_name or ""),
        self_requested=approval.requested_by == caller,
        self_approval_allowed=bool(allows_self),
    )


def _fingerprint(**filters: Any) -> str:
    encoded = json.dumps(filters, sort_keys=True, default=str, separators=(",", ":"))
    return "approvals:" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


async def list_approvals(
    db: AsyncSession,
    authorized: Authorized,
    *,
    decision: ApprovalDecision,
    environment: str | None,
    cursor_token: str | None,
    limit: int,
) -> ApprovalList:
    sort = _fingerprint(decision=decision, environment=environment)
    cursor: Cursor | None = (
        decode_cursor(cursor_token, sort=sort, order="desc") if cursor_token else None
    )
    statement = _base_query(authorized).where(Approval.decision == decision)
    if decision == "PENDING":
        # A lapsed request is the sweep's, not a decision anyone can still make.
        statement = statement.where(Approval.expires_at > func.now())
    if environment is not None:
        statement = statement.where(Operation.environment == environment)
    if cursor is not None:
        statement = statement.where(
            tuple_(Approval.created_at, Approval.id)
            < tuple_(datetime.fromisoformat(cursor.key), uuid.UUID(cursor.id))
        )
    rows = (
        await db.execute(
            statement.order_by(Approval.created_at.desc(), Approval.id.desc()).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(key=page[-1][0].created_at, row_id=page[-1][0].id, sort=sort, order="desc")
        if has_more and page
        else None
    )
    return ApprovalList(
        items=[_item(row, authorized.user.id) for row in page],
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
    )


async def read_approval(
    db: AsyncSession, authorized: Authorized, approval_id: uuid.UUID
) -> ApprovalDetail:
    row = (await db.execute(_base_query(authorized).where(Approval.id == approval_id))).first()
    if row is None:
        raise NotFoundError
    item = _item(row, authorized.user.id)
    approval, operation = row[0], row[1]
    context = await approval_requests.context_for(db, operation)
    terms = context.terms
    service = (
        None
        if operation.target_service_id is None
        else await db.get(Service, operation.target_service_id)
    )
    observed_at = None if service is None else service.last_observed_at
    if service is None or observed_at is None:
        target_state = TargetState(state="UNKNOWN", observed_at=None, data_state="unavailable")
    else:
        age = (datetime.now(UTC) - observed_at).total_seconds()
        target_state = TargetState(
            state=service.last_observed_state,
            observed_at=observed_at,
            data_state="stale" if is_stale_cache(age) else "ok",
        )
    decided = approval.decision in ("APPROVED", "REJECTED") and item.approved_by is not None
    return ApprovalDetail(
        **item.model_dump(),
        policy=PolicyView(
            policy_id=terms.policy_id,
            required_approvals=terms.required_approvals,
            allow_self_approval=terms.allow_self_approval,
            required_role=terms.required_role,
            approval_window_seconds=terms.approval_window_seconds,
        ),
        decisions_so_far=[
            DecisionSoFar(
                approved_by=item.approved_by,
                decision=approval.decision,
                reason=approval.reason,
                decided_at=approval.decided_at,
            )
        ]
        if decided and item.approved_by is not None and approval.decided_at is not None
        else [],
        target_state=target_state,
    )
