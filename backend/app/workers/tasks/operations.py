"""Operation tasks (M4.2, ``OPEN-70``): the executor (operations) and the reaper (maintenance).

``execute`` takes an **id and nothing else** (SECURITY.md §22: no command, URL, credential or
domain object travels through the broker). It re-reads the row and re-authorises before it
touches a provider. The reaper is on ``maintenance`` so a saturated ``operations`` queue cannot
stop it from timing out the rows that saturated it.

The hard limit sits above the largest policy deadline (3 600 s). A task that hits it is killed
and leaves its row ``RUNNING``; the reaper then ends it as ``TIMED_OUT`` with
``reconciliation_required``. It is never re-run: a second delivery loses the claim.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.db.session import get_sync_engine
from app.operations import execute
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

HARD_LIMIT_SECONDS = 3_600 + 120
SOFT_LIMIT_SECONDS = 3_600 + 60


@celery_app.task(
    name="dcp.operations.execute",
    queue="operations",
    time_limit=HARD_LIMIT_SECONDS,
    soft_time_limit=SOFT_LIMIT_SECONDS,
)
def execute_operation(operation_id: str) -> dict[str, Any]:
    """Carry out one operation. The argument is a UUID string; anything else is refused."""
    try:
        identifier = uuid.UUID(operation_id)
    except (ValueError, AttributeError, TypeError):
        logger.error("operation_task_bad_id")
        return {"outcome": "bad_id"}
    outcome = execute.run(get_sync_engine(), identifier)
    return {"outcome": outcome}


def _requeue(operation_id: uuid.UUID) -> None:
    celery_app.send_task(
        "dcp.operations.execute", args=[str(operation_id)], queue="operations", retry=False
    )


@celery_app.task(name="dcp.operations.reap", queue="maintenance")
def reap_operations() -> dict[str, int]:
    """Time out what ran out of time and re-enqueue what was never claimed."""
    summary = execute.reap(get_sync_engine(), enqueue=_requeue)
    logger.info("operations_reaped", **summary)
    return summary
