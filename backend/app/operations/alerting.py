"""An operation that failed is an alert; the next one that succeeds closes it (``OPEN-89``).

Raised through the shared :func:`app.alerts.store.fire`, in the transaction that ends the
operation, so "the operation failed" and "someone is told" cannot disagree. The alert is about
the target (the registered service, on its server) and one row stands for the target however many
times it keeps failing; a later ``SUCCEEDED`` operation on the same target resolves it, because
the source of a ``DCP_OPERATION`` alert is the one that resolves it (``OPEN-44``).

**Denials are not failures.** A worker refusal because the requester's permission was revoked, an
approval was missing, the account was disabled or the policy no longer allows the action is the
controls working; alerting on it would train people to ignore this rule. Everything else that ends
a claimed or expired operation badly -- including a target that failed re-validation (stale
inventory) and a resumer that had to stop following a running command -- is an operational
failure someone should hear about.

The text is a fixed template over closed values (an action, a status, a reason code); no provider
word can reach it. A failure to write the alert **never** undoes the operation's own terminal
state: it is attempted inside a savepoint and logged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.orm import Session

from app.alerts import store
from app.alerts.catalogue import OPERATION_RULE
from app.alerts.lifecycle import dedup_key
from app.core.logging import get_logger
from app.operations import codes

logger = get_logger(__name__)

#: The controls working, not an operation failing.
DENIALS: Final[frozenset[str]] = frozenset(
    {
        codes.PERMISSION_REVOKED,
        codes.APPROVAL_REQUIRED,
        codes.ACCOUNT_DISABLED,
        codes.ACTION_NOT_ALLOWED,
    }
)


def _key(server_id: uuid.UUID, service_id: uuid.UUID | None) -> str:
    return dedup_key(
        source="DCP_OPERATION",
        rule_name=OPERATION_RULE.name,
        target=str(service_id or server_id),
        discriminators={},
    )


def raise_failure(
    session: Session,
    *,
    operation_id: uuid.UUID,
    server_id: uuid.UUID,
    service_id: uuid.UUID | None,
    environment: str,
    action: str,
    status: str,
    reason: str,
) -> None:
    """Open (or count another occurrence of) the target's operation-failure alert."""
    if reason in DENIALS:
        return
    target = store.ResolvedTarget(
        "service" if service_id is not None else "server",
        service_id or server_id,
        server_id,
        environment,
    )
    try:
        with session.begin_nested():
            store.fire(
                session,
                source="DCP_OPERATION",
                dedup=_key(server_id, service_id),
                rule_name=OPERATION_RULE.name,
                severity=OPERATION_RULE.severity,
                target=target,
                summary=f"A {action} operation ended {status}: {reason}",
                details={
                    "operation_id": str(operation_id),
                    "action": action,
                    "status": status,
                    "failure_reason": reason,
                },
                now=datetime.now(UTC),
            )
    except Exception as error:
        logger.error(
            "operation_alert_not_raised", operation_id=str(operation_id), error=type(error).__name__
        )


def clear_failure(
    session: Session, *, operation_id: uuid.UUID, server_id: uuid.UUID, service_id: uuid.UUID | None
) -> None:
    """A success on the target closes its open failure alert, if there is one."""
    try:
        with session.begin_nested():
            store.resolve(session, dedup=_key(server_id, service_id), now=datetime.now(UTC))
    except Exception as error:
        logger.error(
            "operation_alert_not_cleared",
            operation_id=str(operation_id),
            error=type(error).__name__,
        )
