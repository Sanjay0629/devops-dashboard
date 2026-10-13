"""Scheduled maintenance on the ``maintenance`` queue.

Partition upkeep is the first scheduled job in the system, and it exists for a
specific failure mode: an ``audit_events`` INSERT whose ``occurred_at`` falls
outside every partition range **fails**, and because audit writes share the
transaction of the privileged change they record, that failure also fails the
operation. So the runway is extended ahead of time and monitored.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.partitions import (
    RUNWAY_WARNING_MONTHS,
    ensure_partitions,
    partition_runway_months,
)
from app.db.session import get_sync_engine
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="dcp.maintenance.ensure_audit_partitions", queue="maintenance")
def ensure_audit_partitions() -> dict[str, Any]:
    """Extend the audit partition runway, then report what it is.

    Idempotent: creating partitions that already exist is a no-op, so this is
    safe to run on any schedule or by hand.
    """
    engine = get_sync_engine()
    with engine.begin() as connection:
        created = ensure_partitions(connection)
        runway = partition_runway_months(connection)

    if created:
        logger.info("audit_partitions_created", partitions=created, runway_months=runway)
    if runway < RUNWAY_WARNING_MONTHS:
        # Loud on purpose: when this reaches zero, the next month's first
        # privileged operation fails. M3.3 wires an alert rule to this.
        logger.error(
            "audit_partition_runway_low",
            runway_months=runway,
            warning_threshold=RUNWAY_WARNING_MONTHS,
        )
    return {"created": created, "runway_months": runway}
