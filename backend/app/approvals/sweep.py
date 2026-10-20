"""An approval window nobody used ends the request, not just the row (``OPEN-100``).

Runs inside the existing reaper (every 30 s, ``maintenance`` queue). For each ``PENDING`` approval
past ``expires_at``: the approval becomes ``EXPIRED``, its operation ``AWAITING_APPROVAL ->
CANCELLED`` (``approval_status = EXPIRED``, ``reason_code = approval_expired``), an audit row is
written by the named system actor on behalf of the requester, and the pending-approval alert
resolves -- one guarded transaction per row. The approval write goes first and is guarded on
``decision = 'PENDING'``, so a decision that won the race is not undone and the sweep simply loses.

The cancelled operation leaves ``IN_FLIGHT``, so the target's single slot is free the moment this
commits. Never calls a provider: nothing was ever sent for a request nobody approved.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.approvals import alerting
from app.approvals.models import Approval
from app.approvals.store import expire_statement
from app.audit import service as audit
from app.core.logging import get_logger
from app.operations import states
from app.operations.models import Operation
from app.operations.store import transition_statement

logger = get_logger(__name__)

ACTOR_LABEL: Final = "system:approval-sweeper"
REASON_EXPIRED: Final = "approval_expired"
REASON_CANCELLED: Final = "cancelled"
#: Rows per pass; the next pass (30 s later) takes the rest.
BATCH: Final = 100


def sweep(engine: Engine) -> int:
    """Expire every approval whose window has passed. Returns how many this call ended."""
    with Session(engine) as session:
        due = list(
            session.execute(
                select(Approval.id)
                .where(Approval.decision == "PENDING", Approval.expires_at <= func.now())
                .order_by(Approval.expires_at)
                .limit(BATCH)
            ).scalars()
        )
    return sum(expire_one(engine, approval_id) for approval_id in due)


def expire_in_session(session: Session, approval_id: uuid.UUID) -> bool:
    """End one due request on ``session``; the **caller commits** (or rolls back on ``False``).

    ``False`` means this writer lost (a decision, a cancel or another sweep got there first) and
    nothing has been written. Also used by the decision endpoint, which applies the sweep's effect
    to a request it finds past its window before it answers ``409 approval_expired``.
    """
    approval = session.get(Approval, approval_id)
    if approval is None:
        return False
    operation_id, requester = approval.operation_id, approval.requested_by
    if session.execute(expire_statement(approval_id, only_if_due=True)).first() is None:
        return False
    operation = session.get(Operation, operation_id)
    moved = None
    if operation is not None and operation.execution_status == states.AWAITING_APPROVAL:
        moved = session.execute(
            transition_statement(
                operation.id,
                states.AWAITING_APPROVAL,
                states.CANCELLED,
                approval_status="EXPIRED",
                result={"reason_code": REASON_EXPIRED},
            )
        ).first()
    if moved is None or operation is None:
        # An open request whose operation is not waiting: close the request, say so, and leave
        # the operation to whatever moved it. Never touch a running row from here.
        logger.warning("approval_expired_without_waiting_operation", approval_id=str(approval_id))
        return True
    audit.record(
        session,
        action_type="SERVICE_OPERATION_CANCELLED",
        outcome="SUCCESS",
        actor_id=requester,
        actor_label=ACTOR_LABEL,
        target_type="operation",
        target_id=operation.id,
        environment=operation.environment,
        approval_id=approval_id,
        correlation_id=operation.correlation_id,
        metadata={
            "on_behalf_of": str(requester),
            "action": operation.action_type,
            "from": states.AWAITING_APPROVAL,
            "reason": REASON_EXPIRED,
        },
    )
    if operation.target_service_id is not None:
        alerting.resolve_pending(
            session, approval_id=approval_id, service_id=operation.target_service_id
        )
    logger.info("approval_expired", approval_id=str(approval_id), operation_id=str(operation_id))
    return True


def expire_one(engine: Engine, approval_id: uuid.UUID) -> bool:
    with Session(engine) as session:
        if not expire_in_session(session, approval_id):
            session.rollback()
            return False
        session.commit()
    return True
