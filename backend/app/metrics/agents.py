"""Agent health: ``servers.agent_status`` and ``last_seen_at`` from Prometheus (M2.3).

**Where the answer comes from.** Prometheus's own ``up`` series, read through the
existing client — not a probe DCP opens to the host. A direct probe would be a
second outbound path to administrator-supplied addresses, with its own timeouts,
its own allowlist and its own way to be steered; ``up`` already records whether
the scrape Prometheus makes anyway succeeded.

**What it says, and what it refuses to say.** Three states are emitted:

* ``HEALTHY`` — at least one scrape succeeded within the window;
* ``UNREACHABLE`` — a series exists and *every* scrape in the window failed;
* ``UNKNOWN`` — no series at all, or Prometheus could not be asked.

``DEGRADED`` and ``NOT_INSTALLED`` are permitted by the schema and **not
emitted**: nothing here can tell a slow exporter from a healthy one, or an
uninstalled exporter from a stopped one, and writing either would be asserting a
fact nobody observed — the reason M2.1 declined to emit ``degraded`` on §26.1.

**The window is the hysteresis.** ``max_over_time(up[W])`` is 1 if any scrape in
the last ``W`` seconds worked, so a single lost scrape cannot flip a healthy
host and a dead exporter becomes ``UNREACHABLE`` after ``W``. No state is kept
between probes, so a restarted worker has nothing to forget.

**It never asserts what it could not observe.** If Prometheus cannot be queried,
every monitored server becomes ``UNKNOWN`` rather than keeping a ``HEALTHY`` the
probe can no longer vouch for. That is the cost of flapping to ``UNKNOWN`` during
a Prometheus restart, accepted over a status that outlives its evidence.

``last_seen_at`` is set to the host's latest successful scrape and only ever
moves forward. It is the field the M2.5 stale sweep reads (AD-14).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, text

from app.core.config import Settings
from app.core.logging import get_logger
from app.metrics import discovery, templates
from app.metrics.client import PrometheusClient, PrometheusUnavailableError, Sample

logger = get_logger(__name__)

#: Instances per query. One selector with an alternation of this many hosts is
#: well inside what Prometheus parses, and keeps the request line short.
BATCH_SIZE = 50

#: The two states this probe owns. Anything else in the column is somebody
#: else's (a future agent integration) and is never overwritten.
OWNED = ("HEALTHY", "UNREACHABLE")


@dataclass(frozen=True, slots=True)
class Observation:
    status: str
    #: The host's latest *successful* scrape, or ``None`` if there was none to report.
    seen_at: datetime | None = None


UNOBSERVED = Observation("UNKNOWN")


def _by_instance(samples: Sequence[Sample]) -> dict[str, float | None]:
    return {sample.labels.get("instance", ""): sample.value for sample in samples}


async def observe(
    client: PrometheusClient, instances: Sequence[str], window_seconds: int
) -> dict[str, Observation]:
    """Read health for ``instances``. Raises ``PrometheusUnavailableError``."""
    found: dict[str, Observation] = {}
    for start in range(0, len(instances), BATCH_SIZE):
        chunk = list(instances[start : start + BATCH_SIZE])
        window, latest, scraped = await asyncio.gather(
            client.query(templates.render_agent_window("LINUX", chunk, window_seconds)),
            client.query(templates.render("agent_up", "LINUX", chunk)),
            client.query(templates.render_scrape_timestamp("LINUX", chunk)),
        )
        in_window, now_up, at = _by_instance(window), _by_instance(latest), _by_instance(scraped)
        for instance in chunk:
            reading = in_window.get(instance)
            if reading is None:
                found[instance] = UNOBSERVED
                continue
            stamp = at.get(instance)
            seen = (
                datetime.fromtimestamp(stamp, tz=UTC)
                # Only a scrape that *succeeded* counts as having seen the host.
                if now_up.get(instance) == 1.0 and stamp is not None
                else None
            )
            found[instance] = Observation("HEALTHY" if reading >= 1.0 else "UNREACHABLE", seen)
    return found


def apply(
    connection: Connection,
    targets: Sequence[discovery.Target],
    observations: Mapping[str, Observation] | None,
) -> int:
    """Write observations; ``None`` means Prometheus could not be asked.

    Returns the number of rows changed. Each update is conditional on something
    actually differing, because the ``servers`` update trigger fires on every
    UPDATE and a probe that rewrote unchanged rows every 30 s would be churn.
    """
    changed = 0
    for target in targets:
        seen = (observations or {}).get(target.instance, UNOBSERVED)
        changed += connection.execute(
            text(
                "UPDATE servers SET agent_status = :status,"
                " last_seen_at = CASE WHEN CAST(:seen AS timestamptz) IS NULL THEN last_seen_at"
                "   ELSE GREATEST(COALESCE(last_seen_at, :seen), :seen) END"
                " WHERE id = :id AND agent_status IN ('HEALTHY', 'UNREACHABLE', 'UNKNOWN')"
                " AND (agent_status <> :status OR (CAST(:seen AS timestamptz) IS NOT NULL"
                "   AND (last_seen_at IS NULL OR last_seen_at < :seen)))"
            ),
            {"id": target.server_id, "status": seen.status, "seen": seen.seen_at},
        ).rowcount
    # A server that is no longer a target (monitoring turned off, instance
    # cleared, retired) must not keep reporting a health nobody is watching.
    changed += connection.execute(
        text(
            "UPDATE servers SET agent_status = 'UNKNOWN'"
            " WHERE agent_status IN ('HEALTHY', 'UNREACHABLE')"
            " AND NOT (id::text = ANY(:ids))"
        ),
        {"ids": [target.server_id for target in targets]},
    ).rowcount
    return changed


def probe(
    engine: Engine, settings: Settings, client: PrometheusClient | None = None
) -> dict[str, int | str]:
    """One probe cycle: select targets, ask Prometheus, write the result."""
    with engine.connect() as connection:
        targets = discovery.select_targets(connection, settings.metrics_target_ports)

    observations: dict[str, Observation] | None = None
    outcome = "observed"
    if targets:
        try:
            observations = asyncio.run(
                observe(
                    client or PrometheusClient(settings=settings),
                    [target.instance for target in targets],
                    settings.agent_unreachable_after_seconds,
                )
            )
        except PrometheusUnavailableError as error:
            # The reason code, never the exception text (docs/SECURITY.md §20).
            outcome = f"unobservable:{error.reason}"
            logger.warning("agent_probe_unobservable", reason=error.reason)

    with engine.begin() as connection:
        changed = apply(connection, targets, observations)
    return {"targets": len(targets), "changed": changed, "outcome": outcome}
