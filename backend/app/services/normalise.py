"""systemd unit state: Prometheus observations → the PRD §4.5 state set (M2.4).

The single implementation of the normalisation table in
docs/ARCHITECTURE.md §6.1, as a **pure function**: observations in, a verdict
out, no I/O and no clock of its own. Everything that touches Prometheus or the
database lives elsewhere, so every row of the table can be tested without a
transport.

**The order is the contract.** The first rule that applies wins, and the order
is what makes "an unreachable server does not render as all-services-inactive"
(PRD §4.5) true rather than hoped for:

1. a platform with no systemd is ``UNSUPPORTED``;
2. monitoring that is switched off, or has nothing to scrape, is ``UNKNOWN``
   with ``not_configured``;
3. a Prometheus that could not be asked is ``UNKNOWN`` — we know nothing, and
   say so, rather than keeping a last answer the evidence no longer backs;
4. **the host** — no ``up`` series, a failed scrape, or a scrape too old to
   call current — is ``UNKNOWN`` *whatever the unit series say*;
5. the systemd collector not reporting, or reporting nothing for this unit, is
   ``UNCOLLECTED`` — distinct from ``UNKNOWN`` (the host is fine) and from
   ``INACTIVE`` (nobody observed the unit stopped);
6. only then does ``active_state`` choose ``ACTIVE``, ``INACTIVE``, ``FAILED``,
   ``ACTIVATING`` or ``DEACTIVATING``.

**What this source cannot say.** ``node_exporter``'s systemd collector exposes
``active_state`` but not ``sub_state``, ``load_state``, the enabled state or the
main PID (``TS-02``, ``OPEN-29``). So ``sub_state`` is deliberately *not an
input*: ``active/running`` and ``active/exited`` both read ``ACTIVE``, which
loses a distinction rather than inventing one. The four fields are ``null`` at
the API, never a default.

**Fail closed on a malformed unit.** The collector emits five ``state`` series
per unit, exactly one of them ``1``. Anything else — none, two, an unknown
label, a missing sibling, a ``NaN`` — is not guessed at: it is ``UNKNOWN`` with
``provider_error``. A unit that reads as ``ACTIVE`` because the first of two
``1`` values happened to be it would be an invented fact.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from app.inventory.schemas import DataState
from app.metrics.schemas import UnavailableReason
from app.metrics.thresholds import is_stale

ServiceState = Literal[
    "ACTIVE",
    "INACTIVE",
    "FAILED",
    "ACTIVATING",
    "DEACTIVATING",
    "UNCOLLECTED",
    "UNSUPPORTED",
    "UNKNOWN",
]

#: ``node_systemd_unit_state{state=…}`` values, mapped to the normalised set.
#: Exactly these five exist; the collector emits all five for every unit.
STATE_MAP: Final[Mapping[str, ServiceState]] = {
    "active": "ACTIVE",
    "inactive": "INACTIVE",
    "failed": "FAILED",
    "activating": "ACTIVATING",
    "deactivating": "DEACTIVATING",
}

LINUX: Final = "LINUX"


@dataclass(frozen=True, slots=True)
class HostObservation:
    """What is known about the host before any unit is looked at."""

    os_family: str
    monitoring_enabled: bool
    has_metrics_instance: bool
    #: Set when Prometheus could not be asked at all; ``None`` when it answered.
    prometheus_failure: UnavailableReason | None
    #: Latest ``up`` for the host: ``1.0``/``0.0``, or ``None`` for no series.
    up: float | None
    #: When that ``up`` sample was taken (a scrape, successful or not).
    scraped_at: datetime | None
    #: The host's last *successful* scrape (``servers.last_seen_at``).
    last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class CollectorObservation:
    """``node_scrape_collector_success{collector="systemd"}``.

    ``None`` is *no such series*: the collector is not enabled, or the exporter
    is not reporting at all. ``0.0`` is the collector failing (for example no
    system bus). Both mean the same thing here and are deliberately not told
    apart — the operator's fix is the exporter's configuration either way.
    """

    success: float | None


@dataclass(frozen=True, slots=True)
class UnitObservation:
    """The series for one registered unit. All empty/``None`` when none came back."""

    #: ``state`` label → value of ``node_systemd_unit_state``. Empty = no series.
    states: Mapping[str, float | None]
    restart_total: float | None = None
    #: ``node_systemd_unit_start_time_seconds``. ``0`` means *not running*.
    start_time_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class Normalised:
    state: ServiceState
    data_state: DataState
    unavailable_reason: UnavailableReason | None
    #: ``True`` only for a fresh successful scrape; ``False`` when the host
    #: failed or went quiet; ``None`` when that is not known.
    server_reachable: bool | None
    #: When this unit was last successfully collected. ``None`` if never.
    collected_at: datetime | None = None
    restart_count: int | None = None
    #: Activation time. **Not** a last-state-change time (``OPEN-29``).
    started_at: datetime | None = None


def _unknown(
    data_state: DataState,
    reason: UnavailableReason,
    *,
    reachable: bool | None = None,
    collected_at: datetime | None = None,
) -> Normalised:
    return Normalised("UNKNOWN", data_state, reason, reachable, collected_at)


def gate(os_family: str, monitoring_enabled: bool, has_metrics_instance: bool) -> Normalised | None:
    """Rules 1–3: the answers that need no query, shared by the live and cached views."""
    if os_family != LINUX:
        return Normalised("UNSUPPORTED", "unsupported", "unsupported_os", None)
    if not monitoring_enabled:
        return _unknown("not_configured", "monitoring_disabled")
    if not has_metrics_instance:
        return _unknown("not_configured", "no_metrics_instance")
    return None


def _host_verdict(host: HostObservation, now: datetime) -> Normalised | None:
    """A verdict that stops the walk, or ``None`` if the host is fresh and up."""
    gated = gate(host.os_family, host.monitoring_enabled, host.has_metrics_instance)
    if gated is not None:
        return gated
    if host.prometheus_failure is not None:
        if host.prometheus_failure == "prometheus_not_configured":
            return _unknown("not_configured", "prometheus_not_configured")
        return _unknown("unavailable", host.prometheus_failure)
    if host.up is None:
        return _unknown("unavailable", "no_data")
    if host.up == 0.0:
        # The scrape failed: the host is not reachable. What we may still say
        # is when it last was, which is the unit's last good collection too.
        if host.last_seen_at is None:
            return _unknown("unavailable", "no_data", reachable=False)
        return _unknown("stale", "no_data", reachable=False, collected_at=host.last_seen_at)
    if host.up != 1.0 or host.scraped_at is None:
        return _unknown("unavailable", "no_data")
    if is_stale((now - host.scraped_at).total_seconds()):
        return _unknown("stale", "no_data", reachable=False, collected_at=host.scraped_at)
    return None


def _exactly_one_active(states: Mapping[str, float | None]) -> str | None:
    """The one ``state`` label equal to ``1``, or ``None`` if the shape is wrong."""
    if set(states) != set(STATE_MAP):
        return None
    current = [label for label, value in states.items() if value == 1.0]
    if len(current) != 1:
        return None
    if any(value != 0.0 for label, value in states.items() if label != current[0]):
        return None
    return current[0]


def _count(value: float | None) -> int | None:
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _started_at(value: float | None) -> datetime | None:
    """``0`` is the exporter's "not running", not 1 January 1970."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def normalise(
    host: HostObservation,
    collector: CollectorObservation,
    unit: UnitObservation,
    *,
    now: datetime,
) -> Normalised:
    """The state of one registered unit. See the module docstring for the order."""
    stopped = _host_verdict(host, now)
    if stopped is not None:
        return stopped
    # `scraped_at` is set: `_host_verdict` returns early without one.
    seen = host.scraped_at

    if collector.success != 1.0 or not unit.states:
        return Normalised("UNCOLLECTED", "unavailable", "no_data", True)

    label = _exactly_one_active(unit.states)
    if label is None:
        return _unknown("unavailable", "provider_error", reachable=True)

    return Normalised(
        STATE_MAP[label],
        "ok",
        None,
        True,
        collected_at=seen,
        restart_count=_count(unit.restart_total),
        started_at=_started_at(unit.start_time_seconds),
    )


#: Age beyond which a cached verdict is no longer called current. Three refresh
#: intervals (the refresh job runs every 60 s): one missed run is noise, three
#: is a job that is not running — and the list must say so rather than keep
#: showing the last answer as though it were this minute's (PRD §4.5).
CACHE_STALE_AFTER_SECONDS: Final = 180

_CACHED_PASSTHROUGH: Final[Mapping[str, ServiceState]] = {v: v for v in STATE_MAP.values()}


def view_cached(
    *,
    os_family: str,
    monitoring_enabled: bool,
    has_metrics_instance: bool,
    agent_status: str,
    cached_state: str,
    observed_at: datetime | None,
    now: datetime,
) -> Normalised:
    """A list row from ``services.last_observed_*`` — the cache, honestly aged.

    Lists read the cache so they do not fan out to Prometheus per row
    (docs/DATABASE.md §4.5). The cost of that is that the cache can be old, so
    this applies the same ordering as :func:`normalise`: the configuration
    gates first, then **the host** (a probe that says it is unreachable beats
    any cached unit state, which is how a dead host stays ``UNKNOWN`` rather
    than showing the ``ACTIVE`` it had five minutes ago), then the age of the
    cache itself, and only then the cached state.

    A cached ``UNKNOWN`` or ``UNCOLLECTED`` stays what it was; it does not
    become ``INACTIVE`` by being old. A cached ``UNSUPPORTED`` on a Linux host
    is a row the gates would not have produced, so it is not believed.
    """
    gated = gate(os_family, monitoring_enabled, has_metrics_instance)
    if gated is not None:
        return gated
    reachable = {"HEALTHY": True, "UNREACHABLE": False}.get(agent_status)
    if observed_at is None:
        return _unknown("unavailable", "no_data", reachable=reachable)
    if reachable is False or is_stale_cache((now - observed_at).total_seconds()):
        return _unknown("stale", "no_data", reachable=reachable, collected_at=observed_at)
    passthrough = _CACHED_PASSTHROUGH.get(cached_state)
    if passthrough is not None:
        return Normalised(passthrough, "ok", None, reachable, collected_at=observed_at)
    if cached_state == "UNCOLLECTED":
        return Normalised("UNCOLLECTED", "unavailable", "no_data", reachable)
    return _unknown("unavailable", "no_data", reachable=reachable)


def is_stale_cache(age_seconds: float) -> bool:
    """Strictly greater, like :func:`app.metrics.thresholds.is_stale`."""
    return age_seconds > CACHE_STALE_AFTER_SECONDS
