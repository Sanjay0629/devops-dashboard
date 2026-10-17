"""The refresh job against real rows (M2.4).

What needs a real PostgreSQL: the batched ``UPDATE … FROM unnest``, the
never-backwards guard, and the one predicate that decides which hosts are read.
The schema is shared across the session, so ``isolated_fleet`` (from the M2.3
suite) switches other tests' monitoring off for the duration — otherwise the
job's one batched query would name hosts this test never scripted.

Prometheus is scripted; whether a real ``node_exporter`` produces these series
was established against a real systemd for ``TS-02`` and is the smoke test's
job in Compose.
"""

# ruff: noqa: F811 — the imported `isolated_fleet` fixture is requested by parameter name

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.metrics import templates
from app.services import queries, refresh
from tests.integration.test_monitoring import (  # noqa: F401 — fixtures are used by name
    client_for,
    insert_server,
    isolated_fleet,
    unique,
)
from tests.metrics.fake_prometheus import NOW, FakePrometheus

FIVE = ("active", "inactive", "failed", "activating", "deactivating")


def add_service(
    engine: Engine,
    server_id: str,
    name: str,
    *,
    monitoring: bool = True,
    deleted: bool = False,
    cached: str = "UNKNOWN",
    observed_ago: float | None = None,
) -> str:
    service_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO services (id, server_id, service_name, monitoring_enabled,"
                " last_observed_state, last_observed_at, deleted_at, created_at, updated_at)"
                " VALUES (:id, :server, :name, :monitoring, :state,"
                " CASE WHEN CAST(:ago AS float8) IS NULL THEN NULL"
                "      ELSE now() - make_interval(secs => CAST(:ago AS float8)) END,"
                " CASE WHEN :deleted THEN now() END, now(), now())"
            ),
            {
                "id": service_id,
                "server": uuid.UUID(server_id),
                "name": name,
                "monitoring": monitoring,
                "state": cached,
                "ago": observed_ago,
                "deleted": deleted,
            },
        )
    return str(service_id)


def cached(engine: Engine, service_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT last_observed_state, last_observed_at, updated_at"
                    " FROM services WHERE id = :id"
                ),
                {"id": service_id},
            )
            .mappings()
            .one()
        )


def script(
    fake: FakePrometheus,
    wanted: dict[str, list[str]],
    *,
    up: dict[str, str],
    collector: dict[str, str] | None = None,
    units: dict[tuple[str, str], str] | None = None,
) -> None:
    """Script the six queries one batch sends, keyed as the reader builds them."""
    hosts = sorted(wanted)
    names = sorted({name for listed in wanted.values() for name in listed})
    fake.instant[templates.render("agent_up", "LINUX", hosts)] = list(up.items())
    fake.instant[templates.render_scrape_timestamp("LINUX", hosts)] = [
        (instance, str(NOW - 5)) for instance in up
    ]
    fake.instant[queries.collector_success(hosts)] = list(
        (collector if collector is not None else dict.fromkeys(up, "1")).items()
    )
    series: list[tuple[str, str, dict[str, str]]] = []
    for (instance, name), current in (units or {}).items():
        series += [
            (instance, "1" if s == current else "0", {"name": name, "state": s}) for s in FIVE
        ]
    fake.instant[queries.unit_state(hosts, names)] = series
    fake.instant[queries.restart_total(hosts, names)] = []
    fake.instant[queries.start_time(hosts, names)] = []


def run(engine: Engine, fake: FakePrometheus) -> dict[str, int | str]:
    return refresh.refresh(engine, get_settings(), client_for(fake))


def test_each_unit_is_written_with_the_verdict_and_when_it_was_produced(
    isolated_fleet: Engine,
) -> None:
    good, dead, noisy = unique("ok"), unique("dead"), unique("quiet")
    servers = {name: insert_server(isolated_fleet, instance=name) for name in (good, dead, noisy)}
    failed = add_service(isolated_fleet, servers[good], "api.service")
    running = add_service(isolated_fleet, servers[good], "web.service")
    absent = add_service(isolated_fleet, servers[good], "ghost.service")
    on_dead_host = add_service(isolated_fleet, servers[dead], "api.service", cached="ACTIVE")
    uncollected = add_service(isolated_fleet, servers[noisy], "api.service")
    fake = FakePrometheus()
    wanted = {
        good: ["api.service", "web.service", "ghost.service"],
        dead: ["api.service"],
        noisy: ["api.service"],
    }
    script(
        fake,
        wanted,
        up={good: "1", dead: "0", noisy: "1"},
        collector={good: "1", dead: "1", noisy: "0"},
        units={
            (good, "api.service"): "failed",
            (good, "web.service"): "active",
            (dead, "api.service"): "inactive",
            (noisy, "api.service"): "active",
        },
    )

    outcome = run(isolated_fleet, fake)

    assert outcome["outcome"] == "observed"
    assert outcome["services"] == 5
    assert cached(isolated_fleet, failed)["last_observed_state"] == "FAILED"
    assert cached(isolated_fleet, running)["last_observed_state"] == "ACTIVE"
    assert cached(isolated_fleet, absent)["last_observed_state"] == "UNCOLLECTED"
    # The acceptance criterion: a dead host is unknown, never inactive and never
    # the ACTIVE it was cached as.
    assert cached(isolated_fleet, on_dead_host)["last_observed_state"] == "UNKNOWN"
    assert cached(isolated_fleet, uncollected)["last_observed_state"] == "UNCOLLECTED"
    written = cached(isolated_fleet, failed)["last_observed_at"]
    assert abs((datetime.now(UTC) - written).total_seconds()) < 60


def test_when_prometheus_cannot_be_asked_nothing_keeps_its_old_answer(
    isolated_fleet: Engine,
) -> None:
    instance = unique("down")
    server = insert_server(isolated_fleet, instance=instance)
    was_failed = add_service(
        isolated_fleet, server, "api.service", cached="FAILED", observed_ago=30
    )
    fake = FakePrometheus()
    for query in (
        templates.render("agent_up", "LINUX", [instance]),
        templates.render_scrape_timestamp("LINUX", [instance]),
        queries.collector_success([instance]),
        queries.unit_state([instance], ["api.service"]),
        queries.restart_total([instance], ["api.service"]),
        queries.start_time([instance], ["api.service"]),
    ):
        fake.errors[query] = lambda _r: httpx.Response(503)

    outcome = run(isolated_fleet, fake)

    assert str(outcome["outcome"]).startswith("unobservable:")
    after = cached(isolated_fleet, was_failed)
    assert after["last_observed_state"] == "UNKNOWN"
    assert (datetime.now(UTC) - after["last_observed_at"]).total_seconds() < 60


def test_a_unit_on_a_host_nothing_scrapes_is_left_alone(isolated_fleet: Engine) -> None:
    """One predicate selects hosts, for the target file, the probe and this job."""
    scraped = unique("scraped")
    kept = insert_server(isolated_fleet, instance=scraped)
    skipped = {
        "monitoring off": insert_server(isolated_fleet, instance=unique("off"), monitoring=False),
        "windows": insert_server(
            isolated_fleet, instance=unique("win"), operating_system="WINDOWS"
        ),
        "no instance": insert_server(isolated_fleet, instance=None),
        "retired": insert_server(isolated_fleet, instance=unique("gone"), status="RETIRED"),
        "deleted": insert_server(isolated_fleet, instance=unique("del"), deleted=True),
    }
    watched = add_service(isolated_fleet, kept, "api.service")
    untouched = {
        why: add_service(isolated_fleet, server, "api.service", cached="FAILED", observed_ago=90)
        for why, server in skipped.items()
    }
    fake = FakePrometheus()
    script(
        fake,
        {scraped: ["api.service"]},
        up={scraped: "1"},
        units={(scraped, "api.service"): "active"},
    )

    run(isolated_fleet, fake)

    assert cached(isolated_fleet, watched)["last_observed_state"] == "ACTIVE"
    for why, service_id in untouched.items():
        row = cached(isolated_fleet, service_id)
        assert row["last_observed_state"] == "FAILED", why
        assert (datetime.now(UTC) - row["last_observed_at"]).total_seconds() > 80, why


def test_unmonitored_and_deregistered_units_are_neither_read_nor_written(
    isolated_fleet: Engine,
) -> None:
    instance = unique("mix")
    server = insert_server(isolated_fleet, instance=instance)
    live = add_service(isolated_fleet, server, "live.service")
    off = add_service(isolated_fleet, server, "off.service", monitoring=False, cached="FAILED")
    gone = add_service(isolated_fleet, server, "gone.service", deleted=True, cached="FAILED")
    fake = FakePrometheus()
    script(
        fake,
        {instance: ["live.service"]},
        up={instance: "1"},
        units={(instance, "live.service"): "active"},
    )

    run(isolated_fleet, fake)

    assert cached(isolated_fleet, live)["last_observed_state"] == "ACTIVE"
    assert cached(isolated_fleet, off)["last_observed_state"] == "FAILED"
    assert cached(isolated_fleet, gone)["last_observed_state"] == "FAILED"
    assert not any("off.service" in q or "gone.service" in q for q in fake.queries)


def test_a_row_is_never_moved_backwards(isolated_fleet: Engine) -> None:
    """A slow cycle finishing after a newer one must not overwrite it."""
    instance = unique("late")
    server = insert_server(isolated_fleet, instance=instance)
    service = add_service(isolated_fleet, server, "api.service", cached="FAILED")
    future = datetime.now(UTC) + timedelta(minutes=5)
    with isolated_fleet.begin() as connection:
        connection.execute(
            text("UPDATE services SET last_observed_at = :at WHERE id = :id"),
            {"at": future, "id": service},
        )
    fake = FakePrometheus()
    script(
        fake,
        {instance: ["api.service"]},
        up={instance: "1"},
        units={(instance, "api.service"): "active"},
    )

    run(isolated_fleet, fake)

    after = cached(isolated_fleet, service)
    assert after["last_observed_state"] == "FAILED"
    assert after["last_observed_at"] == future


def test_nothing_registered_asks_prometheus_nothing(isolated_fleet: Engine) -> None:
    insert_server(isolated_fleet, instance=unique("empty"))
    fake = FakePrometheus()

    outcome = run(isolated_fleet, fake)

    assert outcome == {"services": 0, "changed": 0, "outcome": "observed"}
    assert fake.queries == []


def test_a_cycle_reports_how_many_rows_changed(isolated_fleet: Engine) -> None:
    instance = unique("chg")
    server = insert_server(isolated_fleet, instance=instance)
    add_service(isolated_fleet, server, "a.service", cached="ACTIVE")
    add_service(isolated_fleet, server, "b.service", cached="ACTIVE")
    fake = FakePrometheus()
    script(
        fake,
        {instance: ["a.service", "b.service"]},
        up={instance: "1"},
        units={(instance, "a.service"): "active", (instance, "b.service"): "failed"},
    )

    assert run(isolated_fleet, fake)["changed"] == 1


def test_the_task_with_no_prometheus_writes_unknown_not_an_error(
    isolated_fleet: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers.tasks import monitoring

    instance = unique("noprom")
    server = insert_server(isolated_fleet, instance=instance)
    service = add_service(isolated_fleet, server, "api.service", cached="FAILED")
    monkeypatch.delenv("DCP_PROMETHEUS_URL")
    get_settings.cache_clear()

    result = monitoring.refresh_services()

    assert result["outcome"] == "unobservable:not_configured"
    assert cached(isolated_fleet, service)["last_observed_state"] == "UNKNOWN"
