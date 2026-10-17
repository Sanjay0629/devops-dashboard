"""The status sweep: ``servers.status`` from how long ago a host was seen (M2.5).

One job, ``dcp.monitoring.sweep_status`` (beat, 60 s), that moves a scraped
server between exactly three states — ``MANAGED``, ``STALE``, ``UNREACHABLE`` —
by :func:`app.monitoring.health.classify_health`. Everything else is deliberate
refusal:

* **Ownership (D1).** Administrators own ``PENDING``→``MANAGED`` and ``RETIRED``;
  the sweep owns the three-way split. It never writes ``PENDING`` or ``RETIRED``
  and never reads past a row in either, and every UPDATE is conditional on the
  status it read, so an administrator's change made mid-sweep wins.
* **Only hosts Prometheus scrapes.** The set is
  :func:`app.metrics.discovery.select_targets` — the one predicate that builds
  the target file and selects the agent probe — so a demo host, an unscraped host
  and a server with no instance are never judged by silence nobody was listening
  to.
* **No evidence is not old evidence (D2).** Age is measured against the last
  time Prometheus *answered*, a marker the agent probe writes, not the wall
  clock. While Prometheus is down the marker stops and so does every host's age:
  15 minutes of outage cannot turn a fleet ``UNREACHABLE`` (PROGRESS issue 67).
  A missing or unreadable marker means no sweep at all — not even recovery, which
  needs the same proof.
* **Every move is audited** to a named system actor, in the transaction that made
  it (mandatory rule 7).

``warning`` (a host lagging past its first line) is a *health* state, not a
status: there is no ``servers.status`` for it, so it stays ``MANAGED``.

A sweep flip does not move ``servers.updated_at`` (migration 0006), or an open
edit form would answer ``409`` because a host went quiet.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, cast

from sqlalchemy import Engine, insert, text

from app.audit.models import AuditEvent
from app.core.config import Settings
from app.core.logging import get_logger
from app.metrics import discovery
from app.monitoring.health import classify_health
from app.monitoring.marker import read_marker, redis_client

logger = get_logger(__name__)

ACTOR_LABEL: Final = "system:health-sweep"
ACTION_TYPE: Final = "SERVER_STATUS_CHANGED"

#: The three states the sweep moves between, and the only ones it will read.
OWNED: Final = ("MANAGED", "STALE", "UNREACHABLE")

#: ``None`` for ``unknown``: no verdict is no move.
_STATUS_FOR_HEALTH: Final = {
    "healthy": "MANAGED",
    "warning": "MANAGED",
    "stale": "STALE",
    "unreachable": "UNREACHABLE",
}


def sweep(engine: Engine, settings: Settings, reference: datetime | None) -> dict[str, int | str]:
    """One pass. ``reference`` is the last time Prometheus answered, or ``None``."""
    if reference is None:
        logger.warning("status_sweep_no_reference")
        return {"outcome": "no_reference", "targets": 0, "changed": 0}

    correlation_id = uuid.uuid4()
    changed = 0
    with engine.begin() as connection:
        targets = discovery.select_targets(connection, settings.metrics_target_ports)
        rows = connection.execute(
            text(
                "SELECT id, status, environment, last_seen_at FROM servers"
                " WHERE id::text = ANY(:ids) AND deleted_at IS NULL"
                " AND status = ANY(:owned)"
            ),
            {"ids": [target.server_id for target in targets], "owned": list(OWNED)},
        ).all()
        for server_id, status, environment, last_seen_at in rows:
            if last_seen_at is None:
                continue  # never seen is unknown, which is no move
            thresholds = settings.health_thresholds.for_environment(str(environment))
            health = classify_health(thresholds, cast(datetime, last_seen_at), reference)
            desired = _STATUS_FOR_HEALTH.get(health.state)
            if desired is None or desired == status:
                continue
            # Conditional on the status just read: a person's change in the
            # meantime wins, and the sweep does not overwrite it.
            moved = connection.execute(
                text("UPDATE servers SET status = :new WHERE id = :id AND status = :old"),
                {"id": server_id, "old": status, "new": desired},
            ).rowcount
            if moved != 1:
                continue
            connection.execute(
                insert(AuditEvent).values(
                    actor_type="SYSTEM",
                    actor_label=ACTOR_LABEL,
                    action_type=ACTION_TYPE,
                    target_type="server",
                    target_id=server_id,
                    environment=environment,
                    correlation_id=correlation_id,
                    outcome="SUCCESS",
                    sanitized_metadata={
                        "from": status,
                        "to": desired,
                        "health_state": health.state,
                        "age_seconds": round(
                            (reference - cast(datetime, last_seen_at)).total_seconds()
                        ),
                        "reference_at": reference.isoformat(),
                    },
                )
            )
            changed += 1
    logger.info("status_swept", targets=len(targets), changed=changed)
    return {"outcome": "swept", "targets": len(targets), "changed": changed}


def run(engine: Engine, settings: Settings) -> dict[str, int | str]:
    """Read the marker, then sweep against it."""
    client = redis_client(settings)
    try:
        return sweep(engine, settings, read_marker(client))
    finally:
        client.close()
