"""Target discovery, the agent probe and the write-path hooks, against real rows (M2.3).

What needs a real PostgreSQL and cannot be settled by reading the code:

* **One predicate** selects targets — the file and the probe must never
  disagree about which servers are watched.
* **The probe must not move ``updated_at``.** Migration 0005 exists because a
  30-second system write would otherwise make every ``If-Unmodified-Since``
  edit a ``409``. That is asserted here end to end, and so is the other half:
  a *person's* edit still moves it.
* **A registration that targets something it should not is refused**, and a
  registration that is fine causes exactly one regeneration request.

Prometheus is scripted (``tests.metrics.fake_prometheus``); whether a real one
answers these queries is the smoke test's job (step 18).

The schema is shared across the whole session, so other tests' servers are in
the table. ``isolated_fleet`` switches their monitoring off for the duration of
a probe test and restores it afterwards, otherwise the probe's one fleet-wide
query would include hosts the test never scripted.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.metrics import agents, discovery, templates
from app.metrics.client import CircuitBreaker, PrometheusClient, reset_breaker
from tests.integration.inventory_helpers import (
    account_id,
    call,
    grant,
    register,
    revoke_all,
    user_id,
)
from tests.integration.test_auth_flow import (  # noqa: F401 — fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)
from tests.metrics.fake_prometheus import NOW, FakePrometheus

PORTS = (9100,)
WINDOW = 60


def unique(prefix: str = "mon") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}:9100"


def insert_server(
    engine: Engine,
    *,
    instance: str | None,
    environment: str = "staging",
    status: str = "PENDING",
    monitoring: bool = True,
    operating_system: str = "LINUX",
    deleted: bool = False,
    agent_status: str = "UNKNOWN",
    display_name: str = "Monitored",
    owner_team: str | None = None,
) -> str:
    server_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO servers (id, cloud_account_id, provider_resource_id, hostname,"
                " display_name, operating_system, environment, management_method,"
                " monitoring_enabled, agent_status, status, registered_at, provider_metadata,"
                " metrics_instance, owner_team, deleted_at, created_at, updated_at)"
                " VALUES (:id, :account, :resource, :hostname, :display, :os, :env,"
                " 'RESTRICTED_AGENT', :monitoring, :agent, :status, now(), '{}'::jsonb,"
                " :instance, :team, CASE WHEN :deleted THEN now() END, now(), now())"
            ),
            {
                "id": server_id,
                "account": account_id(engine),
                "resource": f"res-{server_id}",
                "hostname": f"h{server_id.hex[:10]}.internal.example",
                "display": display_name,
                "os": operating_system,
                "env": environment,
                "monitoring": monitoring,
                "agent": agent_status,
                "status": status,
                "instance": instance,
                "team": owner_team,
                "deleted": deleted,
            },
        )
    return str(server_id)


def row(engine: Engine, server_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT agent_status, last_seen_at, updated_at, deleted_at"
                    " FROM servers WHERE id = :id"
                ),
                {"id": server_id},
            )
            .mappings()
            .one()
        )


def listed(engine: Engine, instances: set[str]) -> set[str]:
    """Which of ``instances`` the one predicate selects as targets."""
    with engine.connect() as connection:
        chosen = discovery.select_targets(connection, PORTS)
    return {target.instance for target in chosen} & instances


# --- the one predicate -------------------------------------------------------------


def test_only_eligible_servers_become_targets(migrated: Engine) -> None:
    ok = unique("ok")
    pending = unique("pending")
    cases = {
        ok: {"status": "MANAGED"},
        # Registration grants no operational permission, but monitoring is a read.
        pending: {"status": "PENDING"},
        unique("retired"): {"status": "RETIRED"},
        unique("deleted"): {"deleted": True},
        unique("off"): {"monitoring": False},
        unique("win"): {"operating_system": "WINDOWS"},
        unique("other"): {"operating_system": "OTHER"},
        # Seeded demo servers look like this; a guaranteed-failing scrape would
        # become an UNREACHABLE asserted about a host that never existed.
        f"demo-{uuid.uuid4().hex[:6]}.demo.invalid:9100": {},
        f"wrongport{uuid.uuid4().hex[:6]}:9101": {},
    }
    for instance, options in cases.items():
        insert_server(migrated, instance=instance, **options)  # type: ignore[arg-type]
    insert_server(migrated, instance=None)

    assert listed(migrated, set(cases)) == {ok, pending}


def test_the_file_holds_ids_and_an_environment_and_nothing_a_person_typed(
    migrated: Engine, tmp_path: Path
) -> None:
    instance = unique("clean")
    server_id = insert_server(
        migrated,
        instance=instance,
        environment="production",
        display_name='Evil "name"\n{x}',
        owner_team="payments\n; drop",
    )
    path = tmp_path / "node.json"

    with migrated.begin() as connection:
        discovery.regenerate(connection, path, PORTS)

    text_ = path.read_text(encoding="utf-8")
    mine = [g for g in json.loads(text_) if g["targets"] == [instance]]
    assert mine == [
        {"targets": [instance], "labels": {"environment": "production", "server_id": server_id}}
    ]
    assert "Evil" not in text_
    assert "payments" not in text_


def test_regenerating_twice_writes_once(migrated: Engine, tmp_path: Path) -> None:
    path = tmp_path / "node.json"
    with migrated.begin() as connection:
        first = discovery.regenerate(connection, path, PORTS)
    with migrated.begin() as connection:
        second = discovery.regenerate(connection, path, PORTS)

    assert first.written is True
    assert second.written is False
    assert first.targets == second.targets


def test_a_changed_inventory_changes_the_file(migrated: Engine, tmp_path: Path) -> None:
    path = tmp_path / "node.json"
    with migrated.begin() as connection:
        discovery.regenerate(connection, path, PORTS)
    instance = unique("added")
    insert_server(migrated, instance=instance)

    with migrated.begin() as connection:
        again = discovery.regenerate(connection, path, PORTS)

    assert again.written is True
    assert instance in path.read_text(encoding="utf-8")


# --- the probe ---------------------------------------------------------------------


@pytest.fixture
def isolated_fleet(migrated: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Only this test's servers are monitored while it runs."""
    monkeypatch.setenv("DCP_PROMETHEUS_URL", "http://prometheus.invalid:9090")
    get_settings.cache_clear()
    reset_breaker()
    with migrated.begin() as connection:
        others = [
            str(entry[0])
            for entry in connection.execute(
                text("SELECT id FROM servers WHERE monitoring_enabled")
            ).all()
        ]
        connection.execute(text("UPDATE servers SET monitoring_enabled = false"))
    yield migrated
    with migrated.begin() as connection:
        connection.execute(
            text("UPDATE servers SET monitoring_enabled = true WHERE id::text = ANY(:ids)"),
            {"ids": others},
        )


def script(
    fake: FakePrometheus,
    instances: list[str],
    window: dict[str, str],
    up: dict[str, str],
    seen: float,
) -> None:
    """Script the three queries a probe sends for exactly ``instances``.

    The probe asks about every monitored server in one selector, so the query
    string (and therefore the key the fake answers by) depends on the full,
    sorted list — including hosts that have no series.
    """
    instances = sorted(instances)
    fake.instant[templates.render_agent_window("LINUX", instances, WINDOW)] = list(window.items())
    fake.instant[templates.render("agent_up", "LINUX", instances)] = list(up.items())
    fake.instant[templates.render_scrape_timestamp("LINUX", instances)] = [
        (instance, str(seen)) for instance in up
    ]


def client_for(fake: FakePrometheus) -> PrometheusClient:
    return PrometheusClient(
        settings=get_settings(), transport=fake.transport(), circuit=CircuitBreaker()
    )


def run_probe(engine: Engine, fake: FakePrometheus) -> dict[str, int | str]:
    return agents.probe(engine, get_settings(), client_for(fake))


def test_the_probe_records_three_states_and_when_a_host_was_last_seen(
    isolated_fleet: Engine,
) -> None:
    healthy, down, unseen = unique("a"), unique("b"), unique("c")
    ids = {name: insert_server(isolated_fleet, instance=name) for name in (healthy, down, unseen)}
    fake = FakePrometheus()
    script(
        fake,
        [healthy, down, unseen],
        window={healthy: "1", down: "0"},
        up={healthy: "1", down: "0"},
        seen=NOW - 3,
    )

    outcome = run_probe(isolated_fleet, fake)

    assert outcome["outcome"] == "observed"
    good, bad, none = (row(isolated_fleet, ids[name]) for name in (healthy, down, unseen))
    assert good["agent_status"] == "HEALTHY"
    assert good["last_seen_at"] == datetime.fromtimestamp(NOW - 3, tz=UTC)
    assert bad["agent_status"] == "UNREACHABLE"
    # An exporter that is down has not been *seen*: nothing is recorded.
    assert bad["last_seen_at"] is None
    assert none["agent_status"] == "UNKNOWN"
    assert none["last_seen_at"] is None


def test_last_seen_only_moves_forward(isolated_fleet: Engine) -> None:
    instance = unique("fwd")
    server_id = insert_server(isolated_fleet, instance=instance)
    fake = FakePrometheus()
    script(fake, [instance], {instance: "1"}, {instance: "1"}, seen=NOW)
    run_probe(isolated_fleet, fake)

    older = FakePrometheus()
    script(older, [instance], {instance: "1"}, {instance: "1"}, seen=NOW - 500)
    run_probe(isolated_fleet, older)

    assert row(isolated_fleet, server_id)["last_seen_at"] == datetime.fromtimestamp(NOW, tz=UTC)


def test_a_probe_does_not_move_updated_at_but_a_person_does(isolated_fleet: Engine) -> None:
    """Migration 0005. Otherwise every If-Unmodified-Since edit would be a 409."""
    instance = unique("tok")
    server_id = insert_server(isolated_fleet, instance=instance)
    before = row(isolated_fleet, server_id)["updated_at"]
    fake = FakePrometheus()
    script(fake, [instance], {instance: "1"}, {instance: "1"}, seen=NOW)

    run_probe(isolated_fleet, fake)

    after = row(isolated_fleet, server_id)
    assert after["agent_status"] == "HEALTHY"
    assert after["updated_at"] == before

    with isolated_fleet.begin() as connection:
        connection.execute(
            text("UPDATE servers SET display_name = 'renamed' WHERE id = :id"), {"id": server_id}
        )
    assert row(isolated_fleet, server_id)["updated_at"] > before


def test_when_prometheus_cannot_be_asked_nothing_stays_healthy(isolated_fleet: Engine) -> None:
    """A status that outlives its evidence is a lie."""
    instance = unique("out")
    server_id = insert_server(isolated_fleet, instance=instance, agent_status="HEALTHY")
    with isolated_fleet.begin() as connection:
        connection.execute(
            text("UPDATE servers SET last_seen_at = :t WHERE id = :id"),
            {"t": datetime.fromtimestamp(NOW, tz=UTC), "id": server_id},
        )
    fake = FakePrometheus()  # no scripted answers: every query is refused

    outcome = run_probe(isolated_fleet, fake)

    assert str(outcome["outcome"]).startswith("unobservable:")
    after = row(isolated_fleet, server_id)
    assert after["agent_status"] == "UNKNOWN"
    # What was last seen is a fact about the past and survives.
    assert after["last_seen_at"] == datetime.fromtimestamp(NOW, tz=UTC)


def test_a_server_that_stops_being_a_target_stops_reporting_health(
    isolated_fleet: Engine,
) -> None:
    instance = unique("off")
    server_id = insert_server(isolated_fleet, instance=instance, agent_status="HEALTHY")
    other = unique("kept")
    insert_server(isolated_fleet, instance=other)
    with isolated_fleet.begin() as connection:
        connection.execute(
            text("UPDATE servers SET monitoring_enabled = false WHERE id = :id"), {"id": server_id}
        )
    fake = FakePrometheus()
    script(fake, [other], {other: "1"}, {other: "1"}, seen=NOW)

    run_probe(isolated_fleet, fake)

    assert row(isolated_fleet, server_id)["agent_status"] == "UNKNOWN"


def test_a_status_the_probe_does_not_own_is_left_alone(isolated_fleet: Engine) -> None:
    instance = unique("own")
    server_id = insert_server(isolated_fleet, instance=instance, agent_status="NOT_INSTALLED")
    fake = FakePrometheus()
    script(fake, [instance], {instance: "1"}, {instance: "1"}, seen=NOW)

    run_probe(isolated_fleet, fake)

    assert row(isolated_fleet, server_id)["agent_status"] == "NOT_INSTALLED"


def test_a_demo_server_is_never_queried_and_stays_unknown(isolated_fleet: Engine) -> None:
    demo = f"demo-{uuid.uuid4().hex[:6]}.demo.invalid:9100"
    server_id = insert_server(isolated_fleet, instance=demo)
    real = unique("real")
    insert_server(isolated_fleet, instance=real)
    fake = FakePrometheus()
    script(fake, [real], {real: "1"}, {real: "1"}, seen=NOW)

    run_probe(isolated_fleet, fake)

    assert row(isolated_fleet, server_id)["agent_status"] == "UNKNOWN"
    assert not any("demo-" in query for query in fake.queries)


# --- the API hooks -----------------------------------------------------------------


@pytest.fixture
async def admin(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    calls: list[int] = []

    async def counted() -> None:
        calls.append(1)

    monkeypatch.setattr(discovery, "request_regeneration", counted)
    return client, migrated, caller, calls


async def test_a_good_registration_requests_exactly_one_regeneration(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
) -> None:
    client, engine, _caller, calls = admin

    response = await register(client, account_id(engine), metrics_instance=unique("reg"))

    assert response.status_code == 201, response.text
    assert calls == [1]


async def test_an_idempotent_replay_requests_nothing(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
) -> None:
    client, engine, _caller, calls = admin
    key = str(uuid.uuid4())
    account = account_id(engine)
    # A replay is only a replay if the body is identical, so everything that
    # `server_body` would randomise is pinned.
    body = {
        "metrics_instance": unique("rep"),
        "hostname": f"h{uuid.uuid4().hex[:8]}.example",
        "provider_resource_id": f"res-{uuid.uuid4()}",
    }

    first = await register(client, account, key=key, **body)
    second = await register(client, account, key=key, **body)

    assert (first.status_code, second.status_code) == (201, 200)
    assert calls == [1]


@pytest.mark.parametrize(
    ("instance", "needs_allowed"),
    [
        ("web1.internal.example:22", True),
        ("127.0.0.1:9100", False),
        ("169.254.169.254:9100", False),
        ("demo.demo.invalid:9100", False),
    ],
)
async def test_a_forbidden_target_is_refused_without_echoing_it(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
    instance: str,
    needs_allowed: bool,
) -> None:
    client, engine, _caller, calls = admin

    response = await register(client, account_id(engine), metrics_instance=instance)

    assert response.status_code == 422, response.text
    [problem] = response.json()["error"]["details"]["fields"]
    assert problem["loc"] == ["body", "metrics_instance"]
    assert problem["code"] == "not_permitted"
    assert (problem.get("allowed") == ["9100"]) is needs_allowed
    assert instance not in response.text
    assert calls == []


async def test_only_edits_that_can_change_a_target_request_a_rebuild(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
) -> None:
    client, engine, _caller, calls = admin
    created = await register(client, account_id(engine), metrics_instance=unique("edit"))
    server_id = created.json()["id"]
    calls.clear()

    await call(client, "PATCH", f"/api/v1/servers/{server_id}", {"display_name": "Renamed"})
    assert calls == []

    for change in (
        {"metrics_instance": unique("moved")},
        {"monitoring_enabled": False},
        {"environment": "development"},
    ):
        response = await call(client, "PATCH", f"/api/v1/servers/{server_id}", change)
        assert response.status_code == 200, response.text
    assert calls == [1, 1, 1]


async def test_retiring_requests_a_rebuild(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
) -> None:
    client, engine, _caller, calls = admin
    created = await register(client, account_id(engine), metrics_instance=unique("ret"))
    calls.clear()

    response = await call(client, "DELETE", f"/api/v1/servers/{created.json()['id']}")

    assert response.status_code == 204
    assert calls == [1]


async def test_resubmitting_a_row_s_existing_instance_is_not_refused(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
) -> None:
    """A seeded ``.invalid`` row must stay editable; only a *changed* value is policed."""
    client, engine, _caller, _calls = admin
    demo = f"demo-{uuid.uuid4().hex[:6]}.demo.invalid:9100"
    server_id = insert_server(engine, instance=demo)

    response = await call(
        client,
        "PATCH",
        f"/api/v1/servers/{server_id}",
        {"display_name": "Edited", "metrics_instance": demo},
    )

    assert response.status_code == 200, response.text


async def test_an_edit_survives_a_probe_that_landed_after_the_form_loaded(
    admin: tuple[httpx.AsyncClient, Engine, uuid.UUID, list[int]],
) -> None:
    """The user-visible half of migration 0005."""
    client, engine, _caller, _calls = admin
    created = await register(client, account_id(engine), metrics_instance=unique("race"))
    server_id = created.json()["id"]
    loaded = (await client.get(f"/api/v1/servers/{server_id}")).json()["updated_at"]

    with engine.begin() as connection:  # the probe, thirty seconds later
        connection.execute(
            text(
                "UPDATE servers SET agent_status = 'HEALTHY', last_seen_at = now() WHERE id = :id"
            ),
            {"id": server_id},
        )
    stamp = datetime.fromisoformat(loaded).strftime("%a, %d %b %Y %H:%M:%S GMT")

    response = await call(
        client,
        "PATCH",
        f"/api/v1/servers/{server_id}",
        {"display_name": "After the probe"},
        headers={"If-Unmodified-Since": stamp},
    )

    assert response.status_code == 200, response.text


# --- the tasks themselves ----------------------------------------------------------


def test_the_regenerate_task_says_so_when_discovery_is_off(
    migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers.tasks import monitoring

    monkeypatch.delenv("DCP_METRICS_TARGETS_FILE", raising=False)
    get_settings.cache_clear()

    assert monitoring.regenerate_targets() == {"outcome": "not_configured"}


def test_the_regenerate_task_writes_the_configured_file(
    migrated: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.workers.tasks import monitoring

    instance = unique("task")
    insert_server(migrated, instance=instance)
    target = tmp_path / "node.json"
    monkeypatch.setenv("DCP_METRICS_TARGETS_FILE", str(target))
    get_settings.cache_clear()

    result = monitoring.regenerate_targets()

    assert result["outcome"] == "ok"
    assert instance in target.read_text(encoding="utf-8")


def test_the_probe_task_with_no_prometheus_reports_unknown_not_an_error(
    isolated_fleet: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers.tasks import monitoring

    instance = unique("noprom")
    server_id = insert_server(isolated_fleet, instance=instance, agent_status="HEALTHY")
    monkeypatch.delenv("DCP_PROMETHEUS_URL")
    get_settings.cache_clear()

    result = monitoring.probe_agents()

    assert result["outcome"] == "unobservable:not_configured"
    assert row(isolated_fleet, server_id)["agent_status"] == "UNKNOWN"
