"""Celery application.

The configuration here is the part of docs/TECH_STACK.md §4.3 that must be right
before any real task exists, because getting it wrong later is how duplicate
privileged operations happen:

* ``task_acks_late`` with a visibility timeout longer than the longest operation
  deadline, so a slow task is not redelivered mid-flight (AD-08).
* ``prefetch_multiplier = 1``, because operation tasks are long and must not be
  hoarded by one worker.
* JSON serialisation only. ``pickle`` is remote code execution by design
  (docs/SECURITY.md §22).
* Queues separated by blast radius, so a throttled cloud API cannot starve
  notifications (docs/ARCHITECTURE.md §2.3).

M1.1 registers exactly one task: ``ping``. Real tasks arrive from M2 onward.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

QUEUES = ("operations", "deployments", "integrations", "notifications", "maintenance")

#: Must exceed the longest operation deadline (docs/ARCHITECTURE.md §10.1:
#: deployments default to 3600s) or a long task can be redelivered while it is
#: still running — the classic cause of a double execution.
VISIBILITY_TIMEOUT_SECONDS = 4 * 60 * 60


def create_celery_app() -> Celery:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=not settings.is_local)

    broker_url = settings.redis_url.get_secret_value()
    celery_app = Celery("dcp", broker=broker_url, backend=broker_url)

    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT_SECONDS},
        result_expires=24 * 60 * 60,
        task_default_queue="maintenance",
        task_queues=tuple(Queue(name) for name in QUEUES),
        beat_schedule={
            # Daily is ample for a three-month runway, and the task is
            # idempotent so a missed run costs nothing (M1.2).
            "ensure-audit-partitions": {
                "task": "dcp.maintenance.ensure_audit_partitions",
                "schedule": crontab(hour="3", minute="7"),
                "options": {"queue": "maintenance"},
            },
        },
        imports=(
            "app.workers.tasks.ping",
            "app.workers.tasks.maintenance",
        ),
    )
    return celery_app


celery_app = create_celery_app()
