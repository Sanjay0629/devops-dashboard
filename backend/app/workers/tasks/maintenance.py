"""Scheduled maintenance on the ``maintenance`` queue.

Partition upkeep is the first scheduled job in the system, and it exists for a
specific failure mode: an INSERT whose partition key falls outside every partition range
**fails**. For ``audit_events`` that also fails the privileged operation the row was
recording (audit writes share its transaction); for ``alert_events`` (M3.3) it makes the
alert webhook answer ``5xx``. So the runway is extended ahead of time, monitored, and --
since M3.3 -- a runway that has fallen short raises an alert (``app/alerts/runway.py``).

``expire_alerts`` (M3.3, ``OPEN-44``) is the other half of an alert's lifecycle: an open
alert nobody has refreshed for three Alertmanager repeat intervals is marked ``EXPIRED``.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.alerts import runway, store
from app.alerts.lifecycle import EXPIRE_AFTER_SECONDS
from app.core.logging import get_logger
from app.db.partitions import (
    PARTITIONED_TABLES,
    RUNWAY_WARNING_MONTHS,
    ensure_partitions,
    partition_runway_months,
)
from app.db.session import get_sync_engine
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="dcp.maintenance.ensure_partitions", queue="maintenance")
def ensure_partitions_task() -> dict[str, Any]:
    """Extend every partitioned table's runway, then alert if one is short.

    Idempotent: creating partitions that already exist is a no-op, so this is
    safe to run on any schedule or by hand.
    """
    engine = get_sync_engine()
    created: dict[str, list[str]] = {}
    runways: dict[str, int] = {}
    with engine.begin() as connection:
        for table in PARTITIONED_TABLES:
            created[table] = ensure_partitions(connection, parent=table)
            runways[table] = partition_runway_months(connection, parent=table)

    for table, partitions in created.items():
        if partitions:
            logger.info(
                "partitions_created", table=table, partitions=partitions, runway=runways[table]
            )
    for table, months in runways.items():
        if months < RUNWAY_WARNING_MONTHS:
            logger.error(
                "partition_runway_low",
                table=table,
                runway_months=months,
                warning_threshold=RUNWAY_WARNING_MONTHS,
            )

    with Session(engine) as session:
        runway.sync_alerts(session)
        session.commit()
    return {"created": created, "runway_months": runways}


@celery_app.task(name="dcp.maintenance.expire_alerts", queue="maintenance")
def expire_alerts() -> dict[str, Any]:
    """Mark open alerts nobody has refreshed for three repeat intervals ``EXPIRED``."""
    with Session(get_sync_engine()) as session:
        expired = store.expire(session, after_seconds=EXPIRE_AFTER_SECONDS)
        session.commit()
    if expired:
        logger.warning("alerts_expired", count=expired)
    return {"expired": expired}


#: DATABASE.md section 11: delivery bookkeeping is transient and kept 90 days.
NOTIFICATION_RETENTION_DAYS = 90


@celery_app.task(name="dcp.maintenance.purge_notification_deliveries", queue="maintenance")
def purge_notification_deliveries() -> dict[str, Any]:
    """Delete delivery rows older than the retention window.

    Only rows that are no longer waiting: a ``PENDING`` row is never deleted, however old.
    """
    with Session(get_sync_engine()) as session:
        result = session.execute(
            text(
                "DELETE FROM notification_deliveries"
                " WHERE created_at < now() - make_interval(days => :days)"
                " AND status <> 'PENDING'"
            ),
            {"days": NOTIFICATION_RETENTION_DAYS},
        )
        deleted = cast(CursorResult[Any], result).rowcount
        session.commit()
    if deleted:
        logger.info("notification_deliveries_purged", count=deleted)
    return {"deleted": int(deleted or 0)}
