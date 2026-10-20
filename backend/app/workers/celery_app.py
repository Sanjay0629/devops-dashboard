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

M1.1 registered exactly one task: ``ping``. M2.3 adds the monitoring tasks; M2.4
adds the service refresh.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.services.normalise import REFRESH_INTERVAL_SECONDS

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
            # M2.3. The first heals a lost enqueue (the API enqueues on every
            # inventory write, best effort); the second is the agent probe,
            # whose cadence is what "UNREACHABLE within the configured
            # interval" is measured against.
            "regenerate-prometheus-targets": {
                "task": "dcp.monitoring.regenerate_targets",
                "schedule": 300.0,
                "options": {"queue": "integrations"},
            },
            "probe-agent-health": {
                "task": "dcp.monitoring.probe_agents",
                "schedule": float(settings.agent_probe_interval_seconds),
                "options": {"queue": "integrations"},
            },
            # M2.5. Once a minute against thresholds measured in minutes; the
            # sweep ages hosts against the last time Prometheus answered, so a
            # slow beat costs a little latency and never a false verdict.
            "sweep-server-status": {
                "task": "dcp.monitoring.sweep_status",
                "schedule": 60.0,
                "options": {"queue": "integrations"},
            },
            # M2.4. The cache the service lists read; the interval is shared
            # with the staleness bound (3x) so the two cannot drift apart.
            "refresh-service-state": {
                "task": "dcp.monitoring.refresh_services",
                "schedule": float(REFRESH_INTERVAL_SECONDS),
                "options": {"queue": "integrations"},
            },
            # Daily is ample for a three-month runway, and the task is
            # idempotent so a missed run costs nothing (M1.2).
            "ensure-partitions": {
                "task": "dcp.maintenance.ensure_partitions",
                "schedule": crontab(hour="3", minute="7"),
                "options": {"queue": "maintenance"},
            },
            # M3.3 (OPEN-44). Every five minutes against a three-hour bound: a slow
            # beat costs a little latency on an expiry, never a wrong one.
            "expire-alerts": {
                "task": "dcp.maintenance.expire_alerts",
                "schedule": 300.0,
                "options": {"queue": "maintenance"},
            },
            # M3.4 (OPEN-50). Leases the due deliveries -- first sends and retries alike.
            # Fifteen seconds is the latency a notification can add to an alert.
            "dispatch-notifications": {
                "task": "dcp.notifications.dispatch",
                "schedule": 15.0,
                "options": {"queue": "notifications"},
            },
            # M4.2 (OPEN-70). Times out a crashed worker's operation at its deadline and hands
            # an unclaimed one back to the queue. On maintenance so a saturated operations
            # queue cannot stop the thing that clears it.
            "reap-operations": {
                "task": "dcp.operations.reap",
                "schedule": 30.0,
                "options": {"queue": "maintenance"},
            },
            # M3.4 (DATABASE.md section 11): delivery bookkeeping is kept 90 days.
            "purge-notification-deliveries": {
                "task": "dcp.maintenance.purge_notification_deliveries",
                "schedule": crontab(hour="3", minute="37"),
                "options": {"queue": "maintenance"},
            },
        },
        imports=(
            "app.workers.tasks.ping",
            "app.workers.tasks.maintenance",
            "app.workers.tasks.monitoring",
            "app.workers.tasks.notifications",
            "app.workers.tasks.operations",
            "app.workers.tasks.cloud",
        ),
    )
    return celery_app


celery_app = create_celery_app()
