"""Deployment tasks (M5.2, ``OPEN-136``): execute and poll on ``deployments``, reap on maintenance.

``execute`` takes an **id and nothing else** (SECURITY.md §22: no command, URL, credential or domain
object travels through the broker). It re-reads the row and re-authorises before it touches Jenkins.
``poll_due`` takes nothing at all: it asks the table which rows are due. The reaper is on
``maintenance`` so a saturated ``deployments`` queue cannot stop it from ending the rows that
saturated it.

None of these retries a trigger. ``acks_late`` and the visibility timeout are configured so a
message lost with its worker is redelivered; a second delivery of ``execute`` loses the claim.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from app.core.logging import get_logger
from app.db.session import get_sync_engine
from app.deployments import execute, poll, reap
from app.deployments.jenkins import JenkinsClient
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

#: ``execute`` is one claim and one trigger. ``poll_due`` is a batch of sequential reads.
EXECUTE_HARD_LIMIT_SECONDS = 120
POLL_HARD_LIMIT_SECONDS = 300


@celery_app.task(
    name="dcp.deployments.execute",
    queue="deployments",
    time_limit=EXECUTE_HARD_LIMIT_SECONDS,
    soft_time_limit=EXECUTE_HARD_LIMIT_SECONDS - 30,
)
def execute_deployment(deployment_id: str) -> dict[str, Any]:
    """Claim and trigger one deployment. The argument is a UUID string; anything else is refused."""
    try:
        identifier = uuid.UUID(deployment_id)
    except (ValueError, AttributeError, TypeError):
        logger.error("deployment_task_bad_id")
        return {"outcome": "bad_id"}
    engine = get_sync_engine()
    try:
        return {"outcome": execute.run(engine, identifier)}
    finally:
        engine.dispose()


@celery_app.task(
    name="dcp.deployments.poll_due",
    queue="deployments",
    time_limit=POLL_HARD_LIMIT_SECONDS,
    soft_time_limit=POLL_HARD_LIMIT_SECONDS - 60,
)
def poll_due() -> dict[str, int]:
    """Lease the rows that are due and look at each. One bad row does not stop the others."""
    engine = get_sync_engine()
    outcomes: Counter[str] = Counter()
    try:
        ids = poll.lease_due(engine)
        client = JenkinsClient()
        for identifier in ids:
            try:
                outcomes[poll.poll_one(engine, client, identifier)] += 1
            except Exception:
                logger.exception("deployment_poll_error", deployment_id=str(identifier))
                outcomes["error"] += 1
    finally:
        engine.dispose()
    return dict(outcomes)


def _requeue(deployment_id: uuid.UUID) -> None:
    celery_app.send_task(
        "dcp.deployments.execute", args=[str(deployment_id)], queue="deployments", retry=False
    )


@celery_app.task(name="dcp.deployments.reap", queue="maintenance")
def reap_deployments() -> dict[str, int]:
    """End what ran out of time and hand unclaimed rows to the queue again."""
    engine = get_sync_engine()
    try:
        summary = reap.reap(engine, enqueue=_requeue)
    finally:
        engine.dispose()
    logger.info("deployments_reaped", **summary)
    return summary
