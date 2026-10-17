"""The reader: Prometheus answers → observations (M2.4).

Scripted by exact query string (``tests.metrics.fake_prometheus``): the fake
answers only the query the renderer produces, so a reader that asked something
else fails with a ``400`` instead of quietly receiving an empty result. What is
established here is how an answer — and an *absence* — becomes an observation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from app.core.config import Settings, get_settings
from app.metrics import templates
from app.metrics.client import CircuitBreaker, PrometheusClient, PrometheusUnavailableError
from app.services import queries, reader
from tests.metrics.fake_prometheus import NOW, FakePrometheus

WEB = "web-1.internal:9100"
DB = "db-1.internal:9100"
UP = templates.render("agent_up", "LINUX", [WEB])
SCRAPED = templates.render_scrape_timestamp("LINUX", [WEB])

STATES = ("active", "inactive", "failed", "activating", "deactivating")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    monkeypatch.setenv("DCP_PROMETHEUS_URL", "http://prometheus.invalid:9090")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def unit_series(instance: str, name: str, current: str) -> list[tuple[str, str, dict[str, str]]]:
    """The five series the collector emits, one of them `1`."""
    return [
        (instance, "1" if state == current else "0", {"name": name, "state": state})
        for state in STATES
    ]


def script(fake: FakePrometheus, instance: str = WEB, names: tuple[str, ...] = ("nginx.service",)):
    """Answer the six queries for one host and one unit."""
    inst, unit_names = [instance], list(names)
    fake.instant[templates.render("agent_up", "LINUX", inst)] = [(instance, "1")]
    fake.instant[templates.render_scrape_timestamp("LINUX", inst)] = [(instance, str(NOW - 4))]
    fake.instant[queries.collector_success(inst)] = [(instance, "1")]
    fake.instant[queries.unit_state(inst, unit_names)] = []
    fake.instant[queries.restart_total(inst, unit_names)] = []
    fake.instant[queries.start_time(inst, unit_names)] = []


def make(fake: FakePrometheus, settings: Settings) -> PrometheusClient:
    return PrometheusClient(settings=settings, transport=fake.transport(), circuit=CircuitBreaker())


async def test_a_reporting_unit_becomes_an_observation(settings: Settings) -> None:
    fake = FakePrometheus()
    script(fake)
    fake.instant[queries.unit_state([WEB], ["nginx.service"])] = unit_series(
        WEB, "nginx.service", "failed"
    )
    fake.instant[queries.restart_total([WEB], ["nginx.service"])] = [
        (WEB, "3", {"name": "nginx.service"})
    ]
    fake.instant[queries.start_time([WEB], ["nginx.service"])] = [
        (WEB, "1792222259.5", {"name": "nginx.service"})
    ]

    result = await reader.read(make(fake, settings), {WEB: ["nginx.service"]})

    host = result.hosts[WEB]
    assert (host.up, host.collector_success) == (1.0, 1.0)
    assert host.scraped_at == datetime.fromtimestamp(NOW - 4, tz=UTC)
    assert host.evaluated_at == datetime.fromtimestamp(NOW, tz=UTC)
    observed = result.units[(WEB, "nginx.service")]
    assert observed.states == {
        "active": 0.0,
        "inactive": 0.0,
        "failed": 1.0,
        "activating": 0.0,
        "deactivating": 0.0,
    }
    assert observed.restart_total == 3.0
    assert observed.start_time_seconds == 1792222259.5


async def test_a_unit_with_no_series_is_absent_not_zero(settings: Settings) -> None:
    """The collector reports nothing for a unit it does not know: no entry at all."""
    fake = FakePrometheus()
    script(fake)

    result = await reader.read(make(fake, settings), {WEB: ["nginx.service"]})

    assert (WEB, "nginx.service") not in result.units
    assert result.hosts[WEB].up == 1.0


async def test_a_host_with_no_series_has_every_fact_unknown(settings: Settings) -> None:
    fake = FakePrometheus()
    script(fake)
    fake.instant[UP] = []
    fake.instant[SCRAPED] = []
    fake.instant[queries.collector_success([WEB])] = []

    host = (await reader.read(make(fake, settings), {WEB: ["nginx.service"]})).hosts[WEB]

    assert host == reader.HostFacts()


async def test_a_nan_value_is_unknown_not_zero(settings: Settings) -> None:
    fake = FakePrometheus()
    script(fake)
    fake.instant[queries.restart_total([WEB], ["nginx.service"])] = [
        (WEB, "NaN", {"name": "nginx.service"})
    ]
    fake.instant[queries.unit_state([WEB], ["nginx.service"])] = unit_series(
        WEB, "nginx.service", "active"
    )

    observed = (await reader.read(make(fake, settings), {WEB: ["nginx.service"]})).units[
        (WEB, "nginx.service")
    ]

    assert observed.restart_total is None


async def test_two_series_for_one_unit_is_unknown_not_whichever_came_first(
    settings: Settings,
) -> None:
    fake = FakePrometheus()
    script(fake)
    fake.instant[queries.unit_state([WEB], ["nginx.service"])] = unit_series(
        WEB, "nginx.service", "active"
    )
    fake.instant[queries.restart_total([WEB], ["nginx.service"])] = [
        (WEB, "1", {"name": "nginx.service"}),
        (WEB, "9", {"name": "nginx.service"}),
    ]

    observed = (await reader.read(make(fake, settings), {WEB: ["nginx.service"]})).units[
        (WEB, "nginx.service")
    ]

    assert observed.restart_total is None


async def test_a_unit_registered_elsewhere_does_not_leak_into_another_host(
    settings: Settings,
) -> None:
    """One query names the union of units; only the registered pairs are kept.

    `redis.service` is registered on `db` only. If `web`'s exporter also happens
    to report it, `web` must not acquire a service nobody registered there.
    """
    fake = FakePrometheus()
    both = [WEB, DB]
    names = ["nginx.service", "redis.service"]
    for instance in both:
        fake.instant[templates.render("agent_up", "LINUX", [instance])] = []
    fake.instant[templates.render("agent_up", "LINUX", sorted(both))] = [(WEB, "1"), (DB, "1")]
    fake.instant[templates.render_scrape_timestamp("LINUX", sorted(both))] = [
        (WEB, str(NOW)),
        (DB, str(NOW)),
    ]
    fake.instant[queries.collector_success(sorted(both))] = [(WEB, "1"), (DB, "1")]
    fake.instant[queries.unit_state(sorted(both), names)] = [
        *unit_series(WEB, "nginx.service", "active"),
        *unit_series(WEB, "redis.service", "active"),  # not registered on web
        *unit_series(DB, "redis.service", "failed"),
    ]
    fake.instant[queries.restart_total(sorted(both), names)] = []
    fake.instant[queries.start_time(sorted(both), names)] = []

    result = await reader.read(
        make(fake, settings), {WEB: ["nginx.service"], DB: ["redis.service"]}
    )

    assert set(result.units) == {(WEB, "nginx.service"), (DB, "redis.service")}
    assert (WEB, "redis.service") not in result.units


async def test_a_host_with_no_registered_units_is_read_for_health_only(settings: Settings) -> None:
    """No names means no unit query at all: an empty selector would match every unit."""
    fake = FakePrometheus()
    script(fake)

    result = await reader.read(make(fake, settings), {WEB: []})

    assert result.hosts[WEB].up == 1.0
    assert result.units == {}
    assert not any("node_systemd_unit_state" in query for query in fake.queries)


async def test_nothing_wanted_asks_nothing(settings: Settings) -> None:
    fake = FakePrometheus()
    result = await reader.read(make(fake, settings), {})
    assert (result.hosts, result.units, fake.queries) == ({}, {}, [])


async def test_prometheus_failing_propagates_for_the_caller_to_decide(
    settings: Settings,
) -> None:
    """Never swallowed into an empty reading, which would read as "nothing is running"."""
    fake = FakePrometheus()  # answers every unscripted query with a 400
    with pytest.raises(PrometheusUnavailableError):
        await reader.read(make(fake, settings), {WEB: ["nginx.service"]})


async def test_many_units_are_read_in_several_passes_not_refused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "UNIT_BATCH", 2)
    fake = FakePrometheus()
    names = ["a.service", "b.service", "c.service"]
    script(fake, names=("a.service", "b.service"))
    fake.instant[queries.unit_state([WEB], ["c.service"])] = unit_series(WEB, "c.service", "active")
    fake.instant[queries.restart_total([WEB], ["c.service"])] = []
    fake.instant[queries.start_time([WEB], ["c.service"])] = []
    fake.instant[queries.unit_state([WEB], ["a.service", "b.service"])] = unit_series(
        WEB, "a.service", "inactive"
    )

    result = await reader.read(make(fake, settings), {WEB: names})

    assert set(result.units) == {(WEB, "a.service"), (WEB, "c.service")}
