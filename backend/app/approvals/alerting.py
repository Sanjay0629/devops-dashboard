"""The pending-approval alert (``OPEN-101``): how approvers are told, without a new channel.

Raised through the shared :func:`app.alerts.store.fire` in the request's transaction, so "a request
is waiting" and "someone is told" cannot disagree, and its deliveries go to the existing channels
by the existing routing rules. It is about the target service, and because one in-flight
operation holds a target's single slot there is at most one per service. It resolves when the
request is approved, rejected, expired or cancelled.

The text is a fixed template over closed values (an action, a registered unit name); no
free-text reason reaches it. A failure to write the alert **never** undoes the approval's own
state: it is attempted inside a savepoint and logged -- the queue, not the alert, is where
approvers act.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.alerts import store
from app.alerts.catalogue import APPROVAL_RULE
from app.alerts.lifecycle import dedup_key
from app.core.logging import get_logger

logger = get_logger(__name__)


def _key(service_id: uuid.UUID) -> str:
    return dedup_key(
        source="DCP_OPERATION",
        rule_name=APPROVAL_RULE.name,
        target=str(service_id),
        discriminators={},
    )


def raise_pending(
    session: Session,
    *,
    approval_id: uuid.UUID,
    operation_id: uuid.UUID,
    server_id: uuid.UUID,
    service_id: uuid.UUID,
    environment: str,
    action: str,
    unit: str,
    expires_at: datetime,
) -> None:
    target = store.ResolvedTarget("service", service_id, server_id, environment)
    try:
        with session.begin_nested():
            store.fire(
                session,
                source="DCP_OPERATION",
                dedup=_key(service_id),
                rule_name=APPROVAL_RULE.name,
                severity=APPROVAL_RULE.severity,
                target=target,
                summary=f"A {action} of {unit} is waiting for an approval",
                details={
                    "operation_id": str(operation_id),
                    "approval_id": str(approval_id),
                    "action": action,
                    "expires_at": expires_at.isoformat(),
                },
                now=datetime.now(UTC),
            )
    except Exception as error:
        logger.error(
            "approval_alert_not_raised", approval_id=str(approval_id), error=type(error).__name__
        )


def resolve_pending(session: Session, *, approval_id: uuid.UUID, service_id: uuid.UUID) -> None:
    try:
        with session.begin_nested():
            store.resolve(session, dedup=_key(service_id), now=datetime.now(UTC))
    except Exception as error:
        logger.error(
            "approval_alert_not_resolved", approval_id=str(approval_id), error=type(error).__name__
        )
