"""Audit export tasks (M4.6, ``OPEN-112``), on ``maintenance``.

``export_audit`` takes an **id and nothing else** (SECURITY.md §22): no window, no filter, no
requester travels through the broker. The row holds the window; the worker re-reads it, claims it
and re-authorises the requester before it reads a single event. The sweep is the other half of the
lifecycle: it ends exports nobody downloaded and exports a dead worker abandoned.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.audit import exports
from app.core.logging import get_logger
from app.db.session import get_sync_engine
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

#: Reading 100 000 events and a 64 MiB file takes well under this; a task past it is killed and
#: its export is failed by the sweep after ``STALE_AFTER``.
SOFT_LIMIT_SECONDS = 600
HARD_LIMIT_SECONDS = 660


@celery_app.task(
    name=exports.TASK_NAME,
    queue="maintenance",
    soft_time_limit=SOFT_LIMIT_SECONDS,
    time_limit=HARD_LIMIT_SECONDS,
)
def export_audit(export_id: str) -> dict[str, Any]:
    """Build one export. The argument is a UUID string; anything else is refused."""
    try:
        identifier = uuid.UUID(export_id)
    except (ValueError, AttributeError, TypeError):
        logger.error("audit_export_task_bad_id")
        return {"outcome": "bad_id"}
    outcome = exports.build(get_sync_engine(), identifier)
    logger.info("audit_export_built", outcome=outcome)
    return {"outcome": outcome}


@celery_app.task(name="dcp.audit.sweep_exports", queue="maintenance")
def sweep_exports() -> dict[str, int]:
    """Expire what nobody collected; fail what was abandoned."""
    summary = exports.sweep(get_sync_engine())
    if any(summary.values()):
        logger.info("audit_exports_swept", **summary)
    return summary
