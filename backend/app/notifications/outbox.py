"""The outbox write (``OPEN-50``): decide, in the alert's own transaction, who is told.

Called by ``alerts.store.fire`` when -- and only when -- a **new alert row** was created. It
reads the active channels, matches each one's routing rules against the *stored* row's
severity, environment and target type, and inserts one ``PENDING`` delivery per match.

It is pure database work: it enqueues nothing and calls nothing, which is what keeps alert
handling inert (SECURITY.md §21, pinned by ``test_alert_handling_imports_nothing_...``). The
delivery worker finds the rows later. ``ON CONFLICT DO NOTHING`` on ``(alert_event_id,
channel_id)`` makes a repeated call harmless.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.db.types import uuid7
from app.notifications.models import NotificationChannel, NotificationDelivery
from app.notifications.rules import parse_stored_rules


def enqueue_for_new_alert(
    session: Session,
    *,
    alert_id: uuid.UUID,
    severity: str,
    environment: str | None,
    target_type: str | None,
    now: datetime,
) -> int:
    """Insert a ``PENDING`` delivery for every active channel that wants this alert."""
    channels = session.execute(
        select(NotificationChannel.id, NotificationChannel.routing_rules).where(
            NotificationChannel.status == "ACTIVE"
        )
    ).all()
    wanted: list[dict[str, Any]] = []
    for channel_id, raw_rules in channels:
        rules = parse_stored_rules(raw_rules)
        # A rule set that does not validate means "do not deliver", never "everything".
        if rules is not None and rules.matches(
            severity=severity, environment=environment, target_type=target_type
        ):
            wanted.append(
                {
                    "id": uuid7(),
                    "alert_event_id": alert_id,
                    "channel_id": channel_id,
                    "status": "PENDING",
                    "attempt_count": 0,
                    "next_attempt_at": now,
                }
            )
    if not wanted:
        return 0
    result = session.execute(
        pg_insert(NotificationDelivery)
        .values(wanted)
        .on_conflict_do_nothing(index_elements=["alert_event_id", "channel_id"])
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)
