"""Monitoring tasks on the ``integrations`` queue (M2.3, ``OPEN-14``).

Two tasks, both **idempotent and argument-free**. CLAUDE.md §5 says a task
receives ids, never credentials, commands, URLs or domain objects; these take
nothing at all, because each rebuilds the whole picture from the database. That
is what makes them safe to run on any schedule, to run twice, and to run after
a missed enqueue.

* ``regenerate_targets`` writes the ``file_sd`` file. It runs after every
  inventory write that can change a target (best effort — see
  ``app/metrics/discovery.request_regeneration``) and on a slow timer, so a
  lost message costs minutes of staleness rather than a permanent gap.
* ``probe_agents`` reads ``up`` from Prometheus and records
  ``agent_status`` / ``last_seen_at``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sync_engine
from app.metrics import agents, discovery
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="dcp.monitoring.regenerate_targets", queue="integrations")
def regenerate_targets() -> dict[str, Any]:
    """Rebuild the Prometheus target file from inventory."""
    settings = get_settings()
    if settings.metrics_targets_file is None:
        # Said, not skipped silently: with discovery off a registered server is
        # never scraped, and that has to be findable in the log.
        logger.warning("target_discovery_not_configured")
        return {"outcome": "not_configured"}

    with get_sync_engine().begin() as connection:
        result = discovery.regenerate(
            connection, Path(settings.metrics_targets_file), settings.metrics_target_ports
        )
    logger.info("targets_regenerated", written=result.written, targets=result.targets)
    return {"outcome": "ok", "written": result.written, "targets": result.targets}


@celery_app.task(name="dcp.monitoring.probe_agents", queue="integrations")
def probe_agents() -> dict[str, Any]:
    """Record each monitored host's agent health from Prometheus."""
    settings = get_settings()
    summary = agents.probe(get_sync_engine(), settings)
    logger.info("agents_probed", **summary)
    return dict(summary)
