"""The refresh job: Prometheus → ``services.last_observed_*`` (M2.4).

Lists read a cache (docs/DATABASE.md §4.5) so a page of services does not fan
out to Prometheus per row; this is what keeps that cache true. One cycle asks
Prometheus about every registered unit on every host Prometheus scrapes,
normalises each with :func:`app.services.normalise.normalise`, and writes the
verdict and the moment it was produced.

**It looks at exactly the hosts Prometheus scrapes.** The set comes from
:func:`app.metrics.discovery.select_targets` — the one predicate that builds the
target file and selects the agent probe — so a service is never read from a host
nothing scrapes, and its verdict never describes a host that was never asked.
Services on hosts outside that set are left alone: the read path's gates
(unsupported platform, monitoring off, no instance) answer for them without a
cache, and a cache that ages out is the honest state for a host that stopped
being scraped.

**It never asserts what it could not observe.** If Prometheus cannot be asked,
every unit it would have read is written ``UNKNOWN`` rather than keeping a
``FAILED`` or an ``ACTIVE`` that the job can no longer vouch for — the same
choice, for the same reason, as ``metrics.agents``: a status that outlives its
evidence is worse than one that flaps to unknown during a Prometheus restart.

**Every cycle writes every row it read**, because ``last_observed_at`` *is* the
product: it is what lets a reader tell a fresh verdict from a stale one, so a
job that skipped unchanged rows would make every steady service look stale after
three minutes. The write is one statement per batch, and it never moves a row
backwards — a slow cycle that finishes after a newer one must not overwrite it.

``services.updated_at`` moves with those writes (the trigger fires on every
UPDATE). Nothing reads it for concurrency — §16.5 has no ``If-Unmodified-Since``
— so this costs only the meaning of that one column, recorded in PROGRESS.md as
a known issue rather than fixed with a trigger migration nobody needs yet.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Connection, Engine, text

from app.core.config import Settings
from app.core.logging import get_logger
from app.inventory.models import SERVICE_NAME_PATTERN
from app.metrics import discovery
from app.metrics.client import PrometheusClient, PrometheusUnavailableError
from app.metrics.schemas import UnavailableReason
from app.services import reader
from app.services.normalise import (
    CollectorObservation,
    HostObservation,
    UnitObservation,
    normalise,
)

logger = get_logger(__name__)

_UNIT_NAME: Final = re.compile(SERVICE_NAME_PATTERN)

#: Rows per ``UPDATE``. Keeps a statement's parameter arrays a sane size.
WRITE_BATCH: Final = 500


@dataclass(frozen=True, slots=True)
class Registered:
    """One registered unit on a scraped host, with what it last said."""

    service_id: uuid.UUID
    service_name: str
    instance: str
    last_seen_at: datetime | None
    cached_state: str


def select_registered(
    connection: Connection, targets: Sequence[discovery.Target]
) -> list[Registered]:
    """Monitored, live units on the hosts Prometheus scrapes."""
    if not targets:
        return []
    by_server = {uuid.UUID(target.server_id): target.instance for target in targets}
    rows = connection.execute(
        text(
            "SELECT s.id, s.service_name, s.server_id, sv.last_seen_at, s.last_observed_state"
            " FROM services s JOIN servers sv ON sv.id = s.server_id"
            " WHERE s.deleted_at IS NULL AND s.monitoring_enabled"
            " AND sv.deleted_at IS NULL AND sv.id = ANY(:ids)"
            " ORDER BY s.id"
        ),
        {"ids": list(by_server)},
    ).all()
    return [
        Registered(row[0], row[1], by_server[row[2]], row[3], row[4])
        for row in rows
        if row[2] in by_server
    ]


def decide(
    registered: Sequence[Registered],
    observed: reader.Reading | None,
    failure: UnavailableReason | None,
    *,
    now: datetime,
) -> dict[uuid.UUID, str]:
    """The state to write for each unit. ``observed`` is ``None`` iff ``failure`` is set."""
    states: dict[uuid.UUID, str] = {}
    for unit in registered:
        if _UNIT_NAME.fullmatch(unit.service_name) is None:
            # A row the CHECK constraint should have made impossible. Not
            # queried, not guessed at: unknown, and said in the log.
            logger.warning("service_name_refused", service_id=str(unit.service_id))
            states[unit.service_id] = "UNKNOWN"
            continue
        facts = observed.hosts.get(unit.instance) if observed is not None else None
        facts = facts or reader.HostFacts()
        observation = (
            observed.units.get((unit.instance, unit.service_name)) if observed is not None else None
        )
        verdict = normalise(
            HostObservation(
                os_family="LINUX",  # `select_targets` admits no other family
                monitoring_enabled=True,
                has_metrics_instance=True,
                prometheus_failure=failure,
                up=facts.up,
                scraped_at=facts.scraped_at,
                last_seen_at=unit.last_seen_at,
            ),
            CollectorObservation(success=facts.collector_success),
            observation or UnitObservation(states={}),
            now=facts.evaluated_at or now,
        )
        states[unit.service_id] = verdict.state
    return states


def apply(connection: Connection, states: Mapping[uuid.UUID, str], *, now: datetime) -> None:
    """Write verdicts and the moment they were produced. Never moves a row backwards."""
    items = list(states.items())
    for start in range(0, len(items), WRITE_BATCH):
        chunk = items[start : start + WRITE_BATCH]
        connection.execute(
            text(
                "UPDATE services SET last_observed_state = v.state,"
                " last_observed_at = :now"
                " FROM (SELECT unnest(CAST(:ids AS uuid[])) AS id,"
                "              unnest(CAST(:states AS text[])) AS state) v"
                " WHERE services.id = v.id AND services.deleted_at IS NULL"
                " AND (services.last_observed_at IS NULL OR services.last_observed_at <= :now)"
            ),
            {"ids": [item[0] for item in chunk], "states": [item[1] for item in chunk], "now": now},
        )


def refresh(
    engine: Engine, settings: Settings, client: PrometheusClient | None = None
) -> dict[str, int | str]:
    """One cycle: select, ask Prometheus, normalise, write."""
    now = datetime.now(UTC)
    with engine.connect() as connection:
        targets = discovery.select_targets(connection, settings.metrics_target_ports)
        registered = select_registered(connection, targets)

    observed: reader.Reading | None = None
    failure: UnavailableReason | None = None
    outcome = "observed"
    if registered:
        wanted: dict[str, list[str]] = {}
        for unit in registered:
            # Names that fail the pattern are decided in `decide`, not queried.
            if _UNIT_NAME.fullmatch(unit.service_name) is not None:
                wanted.setdefault(unit.instance, []).append(unit.service_name)
        try:
            observed = asyncio.run(
                reader.read(client or PrometheusClient(settings=settings), wanted)
            )
        except PrometheusUnavailableError as error:
            # The reason code, never the exception text (docs/SECURITY.md §20).
            failure = (
                "prometheus_not_configured" if error.reason == "not_configured" else error.reason
            )
            outcome = f"unobservable:{error.reason}"
            logger.warning("service_refresh_unobservable", reason=error.reason)

    states = decide(registered, observed, failure, now=now)
    changed = sum(1 for unit in registered if states[unit.service_id] != unit.cached_state)
    with engine.begin() as connection:
        apply(connection, states, now=now)
    return {"services": len(registered), "changed": changed, "outcome": outcome}
