"""Notification delivery on the ``notifications`` queue (``OPEN-50``, ``OPEN-53``).

``dispatch`` runs from beat every 15 seconds: it leases the due deliveries (first sends and
retries are the same thing -- a ``PENDING`` row whose time has come) and enqueues one
``deliver`` per row. ``deliver`` takes a **delivery id and nothing else** (SECURITY.md §22):
no URL, no credential, no message -- it rebuilds all of that from the registered rows.

A lost enqueue costs nothing permanent: the lease expires and the row is due again.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_sync_engine
from app.notifications import deliver
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

#: Short, because the sender's own budget is 10 s and nothing here should hold a worker.
SOFT_TIME_LIMIT_SECONDS = 45
TIME_LIMIT_SECONDS = 60


@celery_app.task(name="dcp.notifications.dispatch", queue="notifications")
def dispatch() -> dict[str, Any]:
    """Lease due deliveries and hand each to a ``deliver`` task by id."""
    with Session(get_sync_engine()) as session:
        ids = deliver.claim_due(session)
        session.commit()
    enqueued = 0
    for delivery_id in ids:
        try:
            deliver_task.apply_async(args=[str(delivery_id)], queue="notifications")
            enqueued += 1
        except Exception:
            # The lease will expire and the row will be picked up again.
            logger.exception("notification_enqueue_failed", delivery_id=str(delivery_id))
    if ids:
        logger.info("notifications_dispatched", leased=len(ids), enqueued=enqueued)
    return {"leased": len(ids), "enqueued": enqueued}


@celery_app.task(
    name="dcp.notifications.deliver",
    queue="notifications",
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=TIME_LIMIT_SECONDS,
)
def deliver_task(delivery_id: str) -> str:
    """Send one leased delivery. The argument is an id, parsed here and nowhere trusted."""
    with Session(get_sync_engine()) as session:
        return deliver.process(session, uuid.UUID(delivery_id))
