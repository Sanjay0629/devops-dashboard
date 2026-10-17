"""Prometheus → observations for registered units (M2.4).

The transport half of unit state. It asks Prometheus six questions per batch,
hands back **facts** — a value, an absence, or nothing — and never decides what
a fact means: :mod:`app.services.normalise` does that, and
``metrics.readings``/``metrics.agents`` remain the only other places a
Prometheus result becomes a state.

**An absent series is not a zero.** Every field of every observation is
``None`` or missing unless Prometheus returned it. A host with no ``up`` series
yields ``up=None``; a unit the exporter does not report yields an empty
``states`` mapping. Collapsing either to ``0`` would turn "nobody observed this"
into "it is stopped", which is the exact failure PRD §4.5 is written against.

**Only what was asked for is read.** Results are keyed by ``(instance, unit)``
and filtered against the requested pairs, because one query names the *union*
of the units on a batch of hosts: a unit registered on host A would otherwise
leak into host B's reading if B's exporter happened to report it. A pair nobody
registered is dropped here and never reaches a caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.metrics import templates
from app.metrics.client import PrometheusClient, Sample, gather_all
from app.services import queries
from app.services.normalise import UnitObservation

#: Hosts per query. The same bound ``metrics.agents`` uses: an alternation of
#: this many ``host:port`` values is well inside what Prometheus parses.
HOST_BATCH: Final = 50

#: Units per query, for the same reason. A server with more registered units
#: than this is read in several passes, not refused.
UNIT_BATCH: Final = 100


@dataclass(frozen=True, slots=True)
class HostFacts:
    #: Latest ``up``: ``1.0``/``0.0``, or ``None`` when there is no series.
    up: float | None = None
    #: When that sample was taken (a scrape, successful or not).
    scraped_at: datetime | None = None
    #: ``node_scrape_collector_success{collector="systemd"}``; ``None`` = absent.
    collector_success: float | None = None
    #: When Prometheus evaluated the ``up`` query. The age of a scrape is
    #: measured against **this**, not against DCP's own clock, so a skew
    #: between the two machines cannot make a healthy host look stale (the
    #: same choice ``metrics.readings`` makes).
    evaluated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Reading:
    """What Prometheus said, for the pairs that were asked about."""

    hosts: Mapping[str, HostFacts]
    units: Mapping[tuple[str, str], UnitObservation]


def _by_instance(samples: Sequence[Sample]) -> dict[str, Sample]:
    return {sample.labels.get("instance", ""): sample for sample in samples}


def _by_unit(samples: Sequence[Sample]) -> dict[tuple[str, str], list[Sample]]:
    grouped: dict[tuple[str, str], list[Sample]] = {}
    for sample in samples:
        key = (sample.labels.get("instance", ""), sample.labels.get("name", ""))
        grouped.setdefault(key, []).append(sample)
    return grouped


def _chunks[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


async def _hosts(client: PrometheusClient, instances: Sequence[str]) -> dict[str, HostFacts]:
    up, scraped, collector = await gather_all(
        client.query(templates.render("agent_up", "LINUX", instances)),
        client.query(templates.render_scrape_timestamp("LINUX", instances)),
        client.query(queries.collector_success(instances)),
    )
    up_by, at_by, collector_by = _by_instance(up), _by_instance(scraped), _by_instance(collector)
    facts: dict[str, HostFacts] = {}
    for instance in instances:
        sample_up, sample_at = up_by.get(instance), at_by.get(instance)
        sample_collector = collector_by.get(instance)
        facts[instance] = HostFacts(
            up=None if sample_up is None else sample_up.value,
            scraped_at=(
                None
                if sample_at is None or sample_at.value is None
                else datetime.fromtimestamp(sample_at.value, tz=UTC)
            ),
            collector_success=None if sample_collector is None else sample_collector.value,
            evaluated_at=None if sample_up is None else sample_up.timestamp,
        )
    return facts


async def _units(
    client: PrometheusClient,
    instances: Sequence[str],
    names: Sequence[str],
    wanted: frozenset[tuple[str, str]],
) -> dict[tuple[str, str], UnitObservation]:
    state, restarts, started = await gather_all(
        client.query(queries.unit_state(instances, names)),
        client.query(queries.restart_total(instances, names)),
        client.query(queries.start_time(instances, names)),
    )
    states: dict[tuple[str, str], dict[str, float | None]] = {}
    for key, samples in _by_unit(state).items():
        if key in wanted:
            states[key] = {s.labels.get("state", ""): s.value for s in samples}
    restart_by, started_by = _by_unit(restarts), _by_unit(started)

    def only(grouped: Mapping[tuple[str, str], list[Sample]], key: tuple[str, str]) -> float | None:
        # Two series for one (instance, unit) is a labelling surprise, not a
        # value: refuse to pick one rather than report whichever came first.
        found = grouped.get(key, [])
        return found[0].value if len(found) == 1 else None

    return {
        key: UnitObservation(
            states=unit_states,
            restart_total=only(restart_by, key),
            start_time_seconds=only(started_by, key),
        )
        for key, unit_states in states.items()
    }


async def read(client: PrometheusClient, wanted: Mapping[str, Sequence[str]]) -> Reading:
    """Read host health and unit state for ``wanted`` (instance → unit names).

    Raises ``PrometheusUnavailableError`` if Prometheus cannot be asked; the
    caller decides what that means for each unit (``UNKNOWN``, never a stale
    answer). A unit with no series is simply absent from ``units``.
    """
    hosts: dict[str, HostFacts] = {}
    units: dict[tuple[str, str], UnitObservation] = {}
    pairs = frozenset((instance, name) for instance, names in wanted.items() for name in names)
    for host_chunk in _chunks(sorted(wanted), HOST_BATCH):
        hosts.update(await _hosts(client, host_chunk))
        union = sorted({name for instance in host_chunk for name in wanted[instance]})
        for name_chunk in _chunks(union, UNIT_BATCH):
            units.update(await _units(client, host_chunk, name_chunk, pairs))
    return Reading(hosts=hosts, units=units)
