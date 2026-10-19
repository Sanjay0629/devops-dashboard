"""Writing audit events (PRD §4.12, mandatory rule 10, docs/DATABASE.md §8).

One function, deliberately. Every privileged path in the product funnels
through it, so the invariants hold in one place rather than in each caller:

* **Attribution.** ``actor_type='USER'`` requires an ``actor_id``; the database
  enforces it (``ck_audit_events_actor_attribution``) and this function makes
  getting it right the easy path.
* **No token material, ever.** ``metadata`` goes through
  :func:`app.core.redaction.redact_value` on the way in. That is the net, not
  the control — callers still pass ids and outcomes, never credentials.
* **One transaction with the state change.** The event is added to the caller's
  session, which the per-request dependency commits (docs/DATABASE.md §12). An
  audit row cannot be committed for a change that rolled back, and a committed
  change cannot be missing its audit row.
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.logging import get_correlation_id, get_logger
from app.core.redaction import redact_value

logger = get_logger(__name__)

#: Namespace for deriving a UUID from a non-UUID correlation id. A client may
#: supply any bounded token as ``X-Correlation-ID`` (app/core/middleware.py)
#: while ``audit_events.correlation_id`` is a NOT NULL uuid, so a stable
#: derivation beats either rejecting the request or losing the correlation.
_CORRELATION_NAMESPACE: Final = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def correlation_uuid() -> uuid.UUID:
    """The current correlation id as a UUID, derived if it is not already one."""
    raw = get_correlation_id()
    if raw is None:
        return uuid.uuid4()
    try:
        return uuid.UUID(raw)
    except ValueError:
        return uuid.uuid5(_CORRELATION_NAMESPACE, raw)


def _parse_ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        # A malformed forwarded address is not worth failing a request over,
        # and must not be stored in an INET column.
        return None


def record(
    db: AsyncSession | Session,
    *,
    action_type: str,
    outcome: str,
    actor_label: str,
    actor_type: str = "USER",
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    environment: str | None = None,
    approval_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Stage one audit event on ``db`` (async API session or the worker's sync one).

    The caller's transaction commits it.
    """
    sanitized = redact_value(metadata or {})
    event = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label[:256],
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        environment=environment,
        correlation_id=correlation_id or correlation_uuid(),
        approval_id=approval_id,
        outcome=outcome,
        source_ip=_parse_ip(client_ip),
        user_agent=user_agent[:512] if user_agent else None,
        sanitized_metadata=sanitized if isinstance(sanitized, dict) else {},
    )
    db.add(event)
    logger.info("audit_event_recorded", action_type=action_type, outcome=outcome)
    return event
