"""The guarded writes to ``approvals``: the arbiter of every race (``OPEN-97``, ``OPEN-100``).

Three writers can reach one open request -- an approver, the requester (or an approver) cancelling,
and the expiry sweep. Each runs one of the statements below **first**, and each is guarded on
``decision = 'PENDING'``, so of any two the loser's ``RETURNING`` is empty and it stops. Only the
winner then moves the operation. Taking the approval row's lock before the operation's, in all
three paths, is also what keeps them from deadlocking each other.

Nothing here executes; the callers own the session and the transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Update, func, update

from app.approvals.models import Approval


def decide_statement(
    approval_id: uuid.UUID, *, decision: str, decider: uuid.UUID, reason: str | None
) -> Update:
    """``APPROVED`` or ``REJECTED``, only while the request is open **and inside its window**."""
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("a decision is APPROVED or REJECTED")
    return (
        update(Approval)
        .where(
            Approval.id == approval_id,
            Approval.decision == "PENDING",
            Approval.expires_at > func.now(),
        )
        .values(decision=decision, approved_by=decider, reason=reason, decided_at=func.now())
        .returning(Approval.id, Approval.decided_at)
    )


def expire_statement(approval_id: uuid.UUID, *, only_if_due: bool) -> Update:
    """``EXPIRED``: the window passed (``only_if_due``, the sweep) or a cancel withdrew it."""
    guards = [Approval.id == approval_id, Approval.decision == "PENDING"]
    if only_if_due:
        guards.append(Approval.expires_at <= func.now())
    return (
        update(Approval)
        .where(*guards)
        .values(decision="EXPIRED", decided_at=func.now())
        .returning(Approval.id, Approval.requested_by)
    )
