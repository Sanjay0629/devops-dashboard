"""A single round-trip task, so the broker wiring is verifiable in M1.1."""

from __future__ import annotations

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="dcp.ping", queue="maintenance")
def ping() -> str:
    """Return a fixed token. Carries no payload, by design: tasks take ids only."""
    logger.info("ping_task_executed")
    return "pong"
