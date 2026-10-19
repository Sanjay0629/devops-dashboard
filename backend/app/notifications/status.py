"""§26.1's notification rows: one per channel, from what its deliveries actually did.

The page answers "is the portal broken, or is the thing it monitors broken?", so each row is
decided by the newest *finished* delivery, with a closed reason code and never a URL or the
words of the peer:

* newest finished delivery ``SENT``                     -> ``ok``
* newest finished delivery ``FAILED`` / ``ABANDONED``   -> ``unavailable`` (its reason)
* nothing ever attempted -> ``degraded``, ``no_delivery_attempted_yet`` (or
  ``delivery_in_progress`` while the first delivery is still being retried)
  (unproven is not ``ok``; this is the first use of ``degraded``, which M2.1 held back because
  nothing persisted could decide it)
* channel ``DISABLED``                                   -> ``not_configured``, ``channel_disabled``
* no channels at all                                     -> one row, ``not_configured``

Never raises: if the database cannot answer, the row says so.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.metrics.schemas import IntegrationStatus
from app.notifications import service
from app.notifications.models import NotificationChannel

NAME = "Notification channels"


async def channel_rows(db: AsyncSession) -> list[IntegrationStatus]:
    demo = get_settings().demo_mode
    try:
        channels = (
            (await db.execute(select(NotificationChannel).order_by(NotificationChannel.name)))
            .scalars()
            .all()
        )
        health = await service.channel_health(db)
    except SQLAlchemyError:
        return [
            IntegrationStatus(
                name=NAME,
                type="notifications",
                status="unavailable",
                last_error_message="database_unreadable",
                demo_mode=demo,
            )
        ]
    if not channels:
        return [
            IntegrationStatus(
                name=NAME,
                type="notifications",
                status="not_configured",
                last_error_message="no_channel_registered",
                demo_mode=demo,
            )
        ]
    rows: list[IntegrationStatus] = []
    for channel in channels:
        h = health.get(channel.id, service.Health())
        if channel.status != "ACTIVE":
            state, reason = "not_configured", "channel_disabled"
        elif h.newest_finished == "SENT":
            state, reason = "ok", None
        elif h.newest_finished in ("FAILED", "ABANDONED"):
            state, reason = "unavailable", h.newest_finished_error or "delivery_failed"
        elif h.attempted:
            # Tried, and nothing has finished yet: still being retried, or first in flight.
            state, reason = "degraded", "delivery_in_progress"
        else:
            state, reason = "degraded", "no_delivery_attempted_yet"
        rows.append(
            IntegrationStatus(
                name=f"Notifications: {channel.name}",
                type="notifications",
                status=state,  # type: ignore[arg-type]
                last_success_at=h.last_success_at,
                last_error_at=h.last_failure_at,
                last_error_message=reason,
                demo_mode=demo,
            )
        )
    return rows
