"""``refresh.decide``: what the job writes for each unit (M2.4).

Pure, like ``normalise`` — and the point is the same: the state written to the
cache must obey the host-first order, because the lists will show it. What this
file adds to ``test_normalise`` is the job's own rules: a Prometheus that cannot
be asked writes ``UNKNOWN`` over an old ``FAILED``, a name that fails the
pattern is never believed, and the scrape age is Prometheus's, not ours.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.metrics.schemas import UnavailableReason
from app.services import reader
from app.services.normalise import UnitObservation
from app.services.refresh import Registered, decide

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
WEB = "web-1.internal:9100"
FIVE = ("active", "inactive", "failed", "activating", "deactivating")


def unit(name: str = "nginx.service", cached: str = "UNKNOWN") -> Registered:
    return Registered(uuid.uuid4(), name, WEB, None, cached)


def states(current: str) -> UnitObservation:
    return UnitObservation(states={s: 1.0 if s == current else 0.0 for s in FIVE})


def reading(
    registered: Registered,
    observation: UnitObservation | None,
    *,
    up: float | None = 1.0,
    collector: float | None = 1.0,
    scraped_ago: float = 5,
    evaluated_at: datetime = NOW,
) -> reader.Reading:
    host = reader.HostFacts(
        up=up,
        scraped_at=evaluated_at - timedelta(seconds=scraped_ago),
        collector_success=collector,
        evaluated_at=evaluated_at,
    )
    units = {} if observation is None else {(WEB, registered.service_name): observation}
    return reader.Reading(hosts={WEB: host}, units=units)


def verdict(
    registered: Registered,
    observed: reader.Reading | None,
    failure: UnavailableReason | None = None,
) -> str:
    return decide([registered], observed, failure, now=NOW)[registered.service_id]


def test_a_reporting_unit_is_written_with_its_state() -> None:
    target = unit()
    assert verdict(target, reading(target, states("failed"))) == "FAILED"
    assert verdict(target, reading(target, states("active"))) == "ACTIVE"


def test_a_dead_host_is_written_unknown_not_the_units_last_series() -> None:
    target = unit(cached="FAILED")
    assert verdict(target, reading(target, states("inactive"), up=0.0)) == "UNKNOWN"


def test_a_unit_the_exporter_does_not_report_is_uncollected() -> None:
    target = unit()
    assert verdict(target, reading(target, None)) == "UNCOLLECTED"


def test_a_failing_collector_is_uncollected() -> None:
    target = unit()
    assert verdict(target, reading(target, states("failed"), collector=0.0)) == "UNCOLLECTED"


def test_a_host_prometheus_has_no_series_for_is_unknown() -> None:
    target = unit()
    nothing = reader.Reading(hosts={}, units={})
    assert verdict(target, nothing) == "UNKNOWN"


def test_prometheus_that_cannot_be_asked_overwrites_a_cached_failed() -> None:
    """The job never keeps an answer it can no longer vouch for."""
    target = unit(cached="FAILED")
    assert verdict(target, None, failure="provider_timeout") == "UNKNOWN"
    assert verdict(target, None, failure="prometheus_not_configured") == "UNKNOWN"


def test_a_name_that_fails_the_pattern_is_never_believed() -> None:
    target = unit('x.service"} or vector(1) #.service')
    assert verdict(target, reading(target, states("active"))) == "UNKNOWN"


def test_scrape_age_is_measured_against_prometheus_not_this_machine() -> None:
    """Prometheus evaluated a year before our clock says it is: not stale for that."""
    target = unit()
    long_ago = NOW - timedelta(days=365)
    fresh = reading(target, states("active"), scraped_ago=5, evaluated_at=long_ago)
    quiet = reading(target, states("active"), scraped_ago=300, evaluated_at=long_ago)
    assert verdict(target, fresh) == "ACTIVE"
    assert verdict(target, quiet) == "UNKNOWN"


def test_every_unit_gets_a_verdict_and_they_are_independent() -> None:
    failed, running, missing = unit("a.service"), unit("b.service"), unit("c.service")
    observed = reader.Reading(
        hosts={WEB: reader.HostFacts(1.0, NOW, 1.0, NOW)},
        units={
            (WEB, "a.service"): states("failed"),
            (WEB, "b.service"): states("active"),
        },
    )
    result = decide([failed, running, missing], observed, None, now=NOW)
    assert result == {
        failed.service_id: "FAILED",
        running.service_id: "ACTIVE",
        missing.service_id: "UNCOLLECTED",
    }
