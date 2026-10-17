"""§16.1–§16.7 end to end: real sessions, grants, scope predicate, rows, audit.

Prometheus is scripted (``tests.metrics.fake_prometheus``) because what is
established here is how the *portal* treats an answer — and an absence — not
whether a ``node_exporter`` is reachable (TS-02 was tested against a real
systemd; M2.4's smoke step covers it in Compose). The transport is substituted
at ``readings.make_client`` and nowhere else.

The assertions that carry the milestone:

* **an unreachable host is never rendered as inactive services** (PRD §4.5),
  on the list path (the cache) and the detail path (the live reading);
* **registration is the allowlist**: an unregistered unit is ``404``, and no
  body field can carry a command (CLAUDE.md §4 rule 1);
* **permissions are enforced on the backend** and scope narrows reads to a
  ``404``, never a ``403`` (§2);
* **an action policy is a privilege**: only an Administrator assigns or changes
  one, and the change is audited under its own action type.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.inventory.models import SERVICE_STATES
from app.metrics import readings, templates
from app.metrics.client import CircuitBreaker, PrometheusClient, reset_breaker
from app.services import queries
from tests.integration.inventory_helpers import (
    STAGING_SCOPE,
    account_id,
    audit_rows,
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

STATES = ("active", "inactive", "failed", "activating", "deactivating")


@dataclass
class Fleet:
    client: httpx.AsyncClient
    engine: Engine
    caller: uuid.UUID
    fake: FakePrometheus
    #: name -> (server id, metrics_instance or "")
    servers: dict[str, tuple[str, str]]

    def id(self, name: str) -> str:
        return self.servers[name][0]

    def instance(self, name: str) -> str:
        return self.servers[name][1]

    def url(self, name: str, unit: str | None = None, suffix: str = "") -> str:
        base = f"/api/v1/servers/{self.id(name)}/services"
        return base if unit is None else f"{base}/{unit}{suffix}"


def _instance() -> str:
    return f"h{uuid.uuid4().hex[:10]}.example:9100"


@pytest.fixture
async def fleet(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Fleet:
    """Staging web, a production server, a Windows server and one with no exporter."""
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    account = account_id(migrated)

    rows: list[tuple[str, str, str, str, str | None]] = [
        ("web", "A web", "staging", "LINUX", _instance()),
        ("prod", "B prod", "production", "LINUX", _instance()),
        ("win", "C win", "staging", "WINDOWS", _instance()),
        ("bare", "D bare", "staging", "LINUX", None),
    ]
    servers: dict[str, tuple[str, str]] = {}
    for name, display, environment, os_family, instance in rows:
        response = await register(
            client,
            account,
            display_name=display,
            environment=environment,
            operating_system=os_family,
            metrics_instance=instance,
            monitoring_enabled=True,
        )
        assert response.status_code == 201, response.text
        servers[name] = (response.json()["id"], instance or "")

    monkeypatch.setenv("DCP_PROMETHEUS_URL", "http://prometheus.invalid:9090")
    get_settings.cache_clear()
    reset_breaker()
    fake = FakePrometheus()

    def make_client() -> PrometheusClient:
        return PrometheusClient(
            settings=get_settings(), transport=fake.transport(), circuit=CircuitBreaker()
        )

    monkeypatch.setattr(readings, "make_client", make_client)
    return Fleet(client, migrated, caller, fake, servers)


# --- helpers -----------------------------------------------------------------


async def add(fleet: Fleet, server: str, unit: str, **body: Any) -> dict[str, Any]:
    response = await call(fleet.client, "POST", fleet.url(server), {"service_name": unit, **body})
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def policy_id(engine: Engine, name: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT id FROM service_action_policies WHERE name = :n"), {"n": name}
            ).scalar_one()
        )


def cache(engine: Engine, service_id: str, state: str, age_seconds: float | None) -> None:
    """Write what the refresh job will write: a state and when it was observed."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE services SET last_observed_state = :s,"
                " last_observed_at = CASE WHEN CAST(:a AS float8) IS NULL THEN NULL"
                " ELSE now() - make_interval(secs => CAST(:a AS float8)) END WHERE id = :id"
            ),
            {"s": state, "a": age_seconds, "id": service_id},
        )


def agent(engine: Engine, server_id: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE servers SET agent_status = :s WHERE id = :id"),
            {"s": status, "id": server_id},
        )


def own(fleet: Fleet, response: httpx.Response) -> list[dict[str, Any]]:
    """The items on a list that belong to this test's servers.

    The test database persists across tests and runs, so a global list holds
    other tests' rows; every assertion is about this fleet's own.
    """
    assert response.status_code == 200, response.text
    ours = {server_id for server_id, _ in fleet.servers.values()}
    items: list[dict[str, Any]] = response.json()["items"]
    return [item for item in items if item["server_id"] in ours]


def audit_for(engine: Engine, action: str, service_id: str) -> list[dict[str, Any]]:
    return [row for row in audit_rows(engine, action) if str(row["target_id"]) == service_id]


def unit_series(instance: str, name: str, current: str) -> list[tuple[str, str, dict[str, str]]]:
    return [
        (instance, "1" if state == current else "0", {"name": name, "state": state})
        for state in STATES
    ]


def script(
    fleet: Fleet,
    server: str,
    unit: str,
    state: str | None,
    *,
    up: str = "1",
    collector: str = "1",
    restarts: str | None = None,
    started: str | None = None,
    age: float = 5,
) -> None:
    """Script the six queries §16.4 makes. ``state=None`` is "no series for the unit"."""
    instance, names = fleet.instance(server), [unit]
    fleet.fake.instant[templates.render("agent_up", "LINUX", [instance])] = [(instance, up)]
    fleet.fake.instant[templates.render_scrape_timestamp("LINUX", [instance])] = [
        (instance, str(NOW - age))
    ]
    fleet.fake.instant[queries.collector_success([instance])] = [(instance, collector)]
    fleet.fake.instant[queries.unit_state([instance], names)] = (
        [] if state is None else unit_series(instance, unit, state)
    )
    fleet.fake.instant[queries.restart_total([instance], names)] = (
        [] if restarts is None else [(instance, restarts, {"name": unit})]
    )
    fleet.fake.instant[queries.start_time([instance], names)] = (
        [] if started is None else [(instance, started, {"name": unit})]
    )


def denials(engine: Engine, actor: uuid.UUID) -> list[str]:
    with engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT sanitized_metadata->>'permission' FROM audit_events"
                    " WHERE action_type = 'AUTHZ_DENIED' AND actor_id = :a"
                ),
                {"a": actor},
            )
        ]


# --- §16.3 registration ------------------------------------------------------


async def test_registering_a_service_adds_it_to_the_allowlist_and_is_audited(fleet: Fleet) -> None:
    created = await add(fleet, "web", "nginx.service", display_name="Web server")

    assert created["service_name"] == "nginx.service"
    assert created["server_id"] == fleet.id("web")
    assert created["environment"] == "staging"
    # Never observed: a state is not invented for a unit nobody has read.
    assert (created["state"], created["data_state"]) == ("UNKNOWN", "unavailable")
    assert created["collected_at"] is None
    # Omitting the policy means no actions at all (fail closed).
    assert created["allowed_actions"] == []
    assert created["approval_required"] is False
    [event] = audit_for(fleet.engine, "SERVICE_REGISTERED", created["service_id"])
    assert event["actor_id"] == fleet.caller
    assert event["environment"] == "staging"
    assert event["sanitized_metadata"]["service_name"] == "nginx.service"


async def test_the_four_fields_this_source_cannot_supply_are_null_not_defaults(
    fleet: Fleet,
) -> None:
    """OPEN-29: unknown is null, never 0, false or an empty string."""
    created = await add(fleet, "web", "nginx.service")
    for field in ("sub_state", "load_state", "enabled", "main_pid", "last_state_change_at"):
        assert created[field] is None, field


async def test_a_duplicate_is_409_and_a_deregistered_name_can_be_registered_again(
    fleet: Fleet,
) -> None:
    await add(fleet, "web", "nginx.service")
    duplicate = await call(
        fleet.client, "POST", fleet.url("web"), {"service_name": "nginx.service"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"

    # The same unit on another server is a different allowlist entry.
    await add(fleet, "prod", "nginx.service")

    assert (
        await call(fleet.client, "DELETE", fleet.url("web", "nginx.service"))
    ).status_code == 204
    await add(fleet, "web", "nginx.service")


@pytest.mark.parametrize(
    "name",
    ["nginx", "nginx.exe", "a b.service", "a/b.service", "x.service\n", ".service", "a;b.service"],
)
async def test_a_malformed_unit_name_is_422_and_not_echoed(fleet: Fleet, name: str) -> None:
    response = await call(fleet.client, "POST", fleet.url("web"), {"service_name": name})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["loc"] == ["body", "service_name"]
    assert "a;b" not in response.text and "a/b" not in response.text


async def test_no_body_field_can_carry_a_command(fleet: Fleet) -> None:
    """CLAUDE.md §4 rule 1: there is nowhere to put one, so the schema says so."""
    for field in ("command", "script", "argv", "exec", "action"):
        response = await call(
            fleet.client,
            "POST",
            fleet.url("web"),
            {"service_name": "nginx.service", field: "rm -rf /"},
        )
        assert response.status_code == 422, field
        assert response.json()["error"]["details"]["fields"][0]["code"] == "unknown_field"
    assert own(fleet, await call(fleet.client, "GET", "/api/v1/services")) == []


async def test_an_unknown_policy_or_application_is_422_not_registered(fleet: Fleet) -> None:
    ghost = str(uuid.uuid4())
    bad_policy = await call(
        fleet.client,
        "POST",
        fleet.url("web"),
        {"service_name": "a.service", "action_policy_id": ghost},
    )
    bad_app = await call(
        fleet.client,
        "POST",
        fleet.url("web"),
        {"service_name": "a.service", "application_id": ghost},
    )
    assert bad_policy.status_code == bad_app.status_code == 422
    assert bad_policy.json()["error"]["details"]["fields"][0]["code"] == "not_registered"
    assert bad_policy.json()["error"]["details"]["fields"][0]["loc"] == ["body", "action_policy_id"]
    assert bad_app.json()["error"]["details"]["fields"][0]["loc"] == ["body", "application_id"]


async def test_the_policy_decides_allowed_actions_and_the_environment_decides_approval(
    fleet: Fleet,
) -> None:
    policy = policy_id(fleet.engine, "restart-only")
    staging = await add(fleet, "web", "nginx.service", action_policy_id=policy)
    production = await add(fleet, "prod", "nginx.service", action_policy_id=policy)

    assert staging["allowed_actions"] == ["status", "logs_tail", "restart"]
    assert staging["approval_required"] is False
    assert production["approval_required"] is True, "production is gated by the seeded policy"


async def test_a_viewer_cannot_register_and_the_denial_is_audited(fleet: Fleet) -> None:
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "VIEWER")

    response = await call(fleet.client, "POST", fleet.url("web"), {"service_name": "nginx.service"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "server:write" in denials(fleet.engine, fleet.caller)
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "ADMINISTRATOR")
    assert own(fleet, await call(fleet.client, "GET", "/api/v1/services")) == []


async def test_a_server_outside_the_callers_scope_is_404_for_every_write(fleet: Fleet) -> None:
    created = await add(fleet, "prod", "nginx.service")
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "ADMINISTRATOR", STAGING_SCOPE)

    register_response = await call(
        fleet.client, "POST", fleet.url("prod"), {"service_name": "other.service"}
    )
    patch = await call(
        fleet.client, "PATCH", fleet.url("prod", "nginx.service"), {"display_name": "x"}
    )
    delete = await call(fleet.client, "DELETE", fleet.url("prod", "nginx.service"))

    assert (register_response.status_code, patch.status_code, delete.status_code) == (404,) * 3
    assert audit_for(fleet.engine, "SERVICE_UPDATED", created["service_id"]) == []


# --- §16.5 / §16.6 -----------------------------------------------------------


async def test_editing_a_service_audits_what_changed(fleet: Fleet) -> None:
    created = await add(fleet, "web", "nginx.service")

    response = await call(
        fleet.client,
        "PATCH",
        fleet.url("web", "nginx.service"),
        {"display_name": "Front door", "monitoring_enabled": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Front door"
    [event] = audit_for(fleet.engine, "SERVICE_UPDATED", created["service_id"])
    assert set(event["sanitized_metadata"]["changes"]) == {"display_name", "monitoring_enabled"}
    # Disabled monitoring is "not configured", not a state.
    assert response.json()["data_state"] == "not_configured"
    assert response.json()["unavailable_reason"] == "monitoring_disabled"


async def test_monitoring_cannot_be_set_to_null(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    response = await call(
        fleet.client, "PATCH", fleet.url("web", "nginx.service"), {"monitoring_enabled": None}
    )
    assert response.status_code == 422


async def test_changing_the_action_policy_is_audited_on_its_own(fleet: Fleet) -> None:
    created = await add(fleet, "web", "nginx.service")
    policy = policy_id(fleet.engine, "restart-only")

    response = await call(
        fleet.client, "PATCH", fleet.url("web", "nginx.service"), {"action_policy_id": policy}
    )

    assert response.status_code == 200, response.text
    [event] = audit_for(fleet.engine, "SERVICE_ACTION_POLICY_CHANGED", created["service_id"])
    assert event["sanitized_metadata"]["from"] is None
    assert event["sanitized_metadata"]["to"] == policy


async def test_resubmitting_the_current_policy_is_not_a_policy_change(fleet: Fleet) -> None:
    """Only a policy that actually moves is a privilege change worth its own audit row."""
    policy = policy_id(fleet.engine, "restart-only")
    created = await add(fleet, "web", "nginx.service", action_policy_id=policy)

    response = await call(
        fleet.client,
        "PATCH",
        fleet.url("web", "nginx.service"),
        {"action_policy_id": policy, "display_name": "same policy"},
    )

    assert response.status_code == 200, response.text
    assert audit_for(fleet.engine, "SERVICE_ACTION_POLICY_CHANGED", created["service_id"]) == []
    [event] = audit_for(fleet.engine, "SERVICE_UPDATED", created["service_id"])
    assert set(event["sanitized_metadata"]["changes"]) == {"display_name"}


async def test_deregistering_is_a_soft_delete_and_removes_the_unit_from_every_read(
    fleet: Fleet,
) -> None:
    created = await add(fleet, "web", "nginx.service")

    response = await call(fleet.client, "DELETE", fleet.url("web", "nginx.service"))

    assert response.status_code == 204
    [event] = audit_for(fleet.engine, "SERVICE_DEREGISTERED", created["service_id"])
    assert event["actor_id"] == fleet.caller
    assert (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).status_code == 404
    assert (await call(fleet.client, "GET", fleet.url("web"))).json()["items"] == []
    with fleet.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT deleted_at IS NOT NULL FROM services WHERE id = :i"),
                {"i": created["service_id"]},
            ).scalar_one()
            is True
        ), "history keeps a row to point at"
    assert (
        await call(fleet.client, "DELETE", fleet.url("web", "nginx.service"))
    ).status_code == 404


# --- §16.1 / §16.2 lists: the cache, aged and host-checked --------------------


async def test_the_list_shows_the_cached_state_with_its_collection_time(fleet: Fleet) -> None:
    created = await add(fleet, "web", "nginx.service")
    agent(fleet.engine, fleet.id("web"), "HEALTHY")
    cache(fleet.engine, created["service_id"], "FAILED", 20)

    [item] = own(fleet, await call(fleet.client, "GET", "/api/v1/services"))

    assert (item["state"], item["data_state"]) == ("FAILED", "ok")
    assert item["collected_at"] is not None
    assert item["server_reachable"] is True
    assert item["server_display_name"] == "A web"


@pytest.mark.parametrize("cached", [s for s in SERVICE_STATES if s not in ("UNSUPPORTED",)])
async def test_an_unreachable_host_is_unknown_on_the_list_whatever_was_cached(
    fleet: Fleet, cached: str
) -> None:
    """PRD §4.5, acceptance-critical, end to end through the real query."""
    created = await add(fleet, "web", "nginx.service")
    cache(fleet.engine, created["service_id"], cached, 20)
    agent(fleet.engine, fleet.id("web"), "UNREACHABLE")

    [item] = own(fleet, await call(fleet.client, "GET", "/api/v1/services"))

    assert item["state"] == "UNKNOWN"
    assert item["state"] != "INACTIVE"
    assert (item["data_state"], item["server_reachable"]) == ("stale", False)


async def test_the_state_filter_follows_what_the_row_shows_not_the_raw_cache(
    fleet: Fleet,
) -> None:
    created = await add(fleet, "web", "nginx.service")
    cache(fleet.engine, created["service_id"], "FAILED", 20)
    agent(fleet.engine, fleet.id("web"), "HEALTHY")

    async def names(state: str) -> list[str]:
        response = await call(fleet.client, "GET", "/api/v1/services", params={"state": state})
        return [item["service_name"] for item in own(fleet, response)]

    assert await names("FAILED") == ["nginx.service"]
    assert await names("UNKNOWN") == []

    agent(fleet.engine, fleet.id("web"), "UNREACHABLE")
    assert await names("FAILED") == [], "a dead host's cached FAILED is not listed as failed"
    assert await names("UNKNOWN") == ["nginx.service"]


async def test_a_stale_cache_is_stale_not_the_last_known_good_value(fleet: Fleet) -> None:
    created = await add(fleet, "web", "nginx.service")
    agent(fleet.engine, fleet.id("web"), "HEALTHY")
    cache(fleet.engine, created["service_id"], "ACTIVE", 600)

    [item] = own(fleet, await call(fleet.client, "GET", "/api/v1/services"))

    assert (item["state"], item["data_state"]) == ("UNKNOWN", "stale")
    assert item["collected_at"] is not None, "when it was last true is still reported"


async def test_a_windows_server_lists_unsupported_and_a_bare_one_not_configured(
    fleet: Fleet,
) -> None:
    await add(fleet, "win", "spooler.service")
    await add(fleet, "bare", "nginx.service")

    items = {
        item["server_display_name"]: item
        for item in own(fleet, await call(fleet.client, "GET", "/api/v1/services"))
    }

    assert (items["C win"]["state"], items["C win"]["data_state"]) == ("UNSUPPORTED", "unsupported")
    assert (items["D bare"]["data_state"], items["D bare"]["unavailable_reason"]) == (
        "not_configured",
        "no_metrics_instance",
    )


async def test_lists_page_without_repeating_or_skipping_and_a_cursor_is_bound_to_its_sort(
    fleet: Fleet,
) -> None:
    names = [f"unit{i}.service" for i in range(5)]
    for name in names:
        await add(fleet, "web", name)

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await call(fleet.client, "GET", fleet.url("web"), params=params)).json()
        seen += [item["service_name"] for item in body["items"]]
        pages += 1
        assert body["page"]["total"] is None
        cursor = body["page"]["next_cursor"]
        if not body["page"]["has_more"]:
            break

    assert seen == names
    assert pages == 3

    first = await call(fleet.client, "GET", fleet.url("web"), params={"limit": 1})
    minted = first.json()["page"]["next_cursor"]
    replay = await call(
        fleet.client,
        "GET",
        fleet.url("web"),
        params={"limit": 1, "cursor": minted, "sort": "server"},
    )
    assert replay.status_code == 422


async def test_search_treats_its_own_wildcards_as_literals(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    matched = await call(fleet.client, "GET", "/api/v1/services", params={"q": "ngi"})
    wildcard = await call(fleet.client, "GET", "/api/v1/services", params={"q": "%"})
    assert [i["service_name"] for i in own(fleet, matched)] == ["nginx.service"]
    assert wildcard.json()["items"] == []


async def test_a_typo_d_filter_is_422_not_an_unfiltered_list(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    for path, params in (
        ("/api/v1/services", {"stat": "FAILED"}),
        (fleet.url("web"), {"environment": "staging"}),
        ("/api/v1/services", {"state": "BROKEN"}),
    ):
        response = await call(fleet.client, "GET", path, params=params)
        assert response.status_code == 422, (path, params)


async def test_scope_narrows_lists_and_a_server_list_for_an_unseen_server_is_404(
    fleet: Fleet,
) -> None:
    await add(fleet, "web", "nginx.service")
    await add(fleet, "prod", "nginx.service")
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "VIEWER", STAGING_SCOPE)

    items = own(fleet, await call(fleet.client, "GET", "/api/v1/services"))
    assert [i["server_id"] for i in items] == [fleet.id("web")], "the production unit is invisible"
    assert (await call(fleet.client, "GET", fleet.url("prod"))).status_code == 404
    assert (await call(fleet.client, "GET", fleet.url("prod", "nginx.service"))).status_code == 404
    assert (await call(fleet.client, "GET", fleet.url("web"))).status_code == 200


async def test_a_caller_with_no_grant_is_refused_for_the_right_reason(fleet: Fleet) -> None:
    revoke_all(fleet.engine, fleet.caller)
    response = await call(fleet.client, "GET", "/api/v1/services")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "service:read" in denials(fleet.engine, fleet.caller)


# --- §16.4 detail: the live reading -------------------------------------------


async def test_a_failed_unit_is_identifiable_with_its_restart_count(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", "failed", restarts="3", started="0")

    response = await call(fleet.client, "GET", fleet.url("web", "nginx.service"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["data_state"]) == ("FAILED", "ok")
    assert body["restart_count"] == 3
    assert body["started_at"] is None, "a start time of 0 is unknown, not 1970"
    assert body["server_reachable"] is True
    assert body["collected_at"] is not None
    for field in ("sub_state", "load_state", "enabled", "main_pid", "last_state_change_at"):
        assert body[field] is None, field


async def test_a_running_unit_reports_when_it_started(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", "active", restarts="0", started="1760000000")

    body = (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).json()

    assert (body["state"], body["restart_count"]) == ("ACTIVE", 0)
    assert body["started_at"] == "2025-10-09T08:53:20Z"


async def test_a_dead_host_makes_the_unit_unknown_even_when_its_series_say_inactive(
    fleet: Fleet,
) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", "inactive", up="0")

    body = (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).json()

    assert body["state"] == "UNKNOWN"
    assert body["state"] != "INACTIVE"
    assert body["server_reachable"] is False


async def test_a_collector_that_is_not_succeeding_is_uncollected(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", "failed", collector="0")

    body = (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).json()

    assert body["state"] == "UNCOLLECTED"
    assert body["server_reachable"] is True, "the host is fine; only the collection is not"


async def test_a_unit_the_exporter_does_not_report_is_uncollected_not_inactive(
    fleet: Fleet,
) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", None)

    body = (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).json()

    assert body["state"] == "UNCOLLECTED"


async def test_a_scrape_that_has_gone_quiet_is_stale_and_unknown(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", "active", age=300)

    body = (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).json()

    assert (body["state"], body["data_state"]) == ("UNKNOWN", "stale")


async def test_a_dead_prometheus_is_an_unavailable_unit_on_a_200(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    script(fleet, "web", "nginx.service", "active")
    for query in list(fleet.fake.instant):
        fleet.fake.errors[query] = lambda _r: httpx.Response(503)

    response = await call(fleet.client, "GET", fleet.url("web", "nginx.service"))

    assert response.status_code == 200
    body = response.json()
    assert (body["state"], body["data_state"]) == ("UNKNOWN", "unavailable")
    assert body["unavailable_reason"] == "provider_error"


async def test_an_unconfigured_prometheus_is_not_configured(
    fleet: Fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add(fleet, "web", "nginx.service")
    monkeypatch.delenv("DCP_PROMETHEUS_URL")
    get_settings.cache_clear()
    monkeypatch.setattr(
        readings,
        "make_client",
        lambda: PrometheusClient(settings=get_settings(), circuit=CircuitBreaker()),
    )

    body = (await call(fleet.client, "GET", fleet.url("web", "nginx.service"))).json()

    assert (body["state"], body["data_state"]) == ("UNKNOWN", "not_configured")
    assert body["unavailable_reason"] == "prometheus_not_configured"


async def test_hosts_that_cannot_be_asked_are_not_queried(fleet: Fleet) -> None:
    await add(fleet, "win", "spooler.service")
    await add(fleet, "bare", "nginx.service")

    windows = (await call(fleet.client, "GET", fleet.url("win", "spooler.service"))).json()
    bare = (await call(fleet.client, "GET", fleet.url("bare", "nginx.service"))).json()

    assert (windows["state"], windows["data_state"]) == ("UNSUPPORTED", "unsupported")
    assert bare["unavailable_reason"] == "no_metrics_instance"
    assert fleet.fake.queries == []


async def test_an_unregistered_unit_is_404_and_never_queried(fleet: Fleet) -> None:
    """Registration is the control: nothing is asked of Prometheus for a name nobody registered."""
    script(fleet, "web", "nginx.service", "active")

    response = await call(fleet.client, "GET", fleet.url("web", "nginx.service"))

    assert response.status_code == 404
    assert fleet.fake.queries == []


async def test_a_malformed_name_in_the_path_is_422_before_any_lookup(fleet: Fleet) -> None:
    response = await call(fleet.client, "GET", fleet.url("web", "not-a-unit"))
    assert response.status_code == 422
    assert fleet.fake.queries == []


# --- §16.7 logs ---------------------------------------------------------------


async def test_logs_are_not_configured_until_loki_exists_and_say_so(fleet: Fleet) -> None:
    """Not an empty page: that would read as "no errors in the last hour"."""
    await add(fleet, "web", "nginx.service")

    response = await call(fleet.client, "GET", fleet.url("web", "nginx.service", "/logs"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


async def test_logs_need_the_system_log_permission_which_a_viewer_lacks(fleet: Fleet) -> None:
    await add(fleet, "web", "nginx.service")
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "VIEWER")

    response = await call(fleet.client, "GET", fleet.url("web", "nginx.service", "/logs"))

    assert response.status_code == 403
    assert "logs:query" in denials(fleet.engine, fleet.caller)


async def test_logs_for_an_unregistered_unit_or_unseen_server_are_404(fleet: Fleet) -> None:
    await add(fleet, "prod", "nginx.service")
    unregistered = await call(fleet.client, "GET", fleet.url("web", "ghost.service", "/logs"))
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "ADMINISTRATOR", STAGING_SCOPE)
    unseen = await call(fleet.client, "GET", fleet.url("prod", "nginx.service", "/logs"))
    assert (unregistered.status_code, unseen.status_code) == (404, 404)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 1001},
        {"limit": 0},
        {"level": "loud"},
        {"from": "2026-01-01T00:00:00", "to": "2026-01-01T01:00:00Z"},
        {"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        {"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:01Z"},
        {"query": '{job=~".+"}'},
    ],
    ids=["too-many", "none", "level", "naive-time", "reversed", "over-24h", "logql-smuggled"],
)
async def test_log_parameters_are_validated_before_anything_else(
    fleet: Fleet, params: dict[str, Any]
) -> None:
    await add(fleet, "web", "nginx.service")
    response = await call(
        fleet.client, "GET", fleet.url("web", "nginx.service", "/logs"), params=params
    )
    assert response.status_code == 422, response.text


# --- the SQL state filter against the Python rule ------------------------------


# ~860 registrations over HTTP: 77-84 s alone on the M3.1 development machine
# (2026-10-17), against the suite-wide 120 s hang detector, and past it under
# coverage inside the full suite. The limit exists to catch hangs, so this test
# gets its own rather than the suite's being loosened (PROGRESS issue 91).
@pytest.mark.timeout(400)
async def test_the_sql_state_expression_agrees_with_view_cached_on_every_combination(
    fleet: Fleet,
) -> None:
    """The filter is a translation of `view_cached`, not a second decision.

    Every combination of the inputs the rule reads is created, then each state
    is requested and the set of rows returned is compared with what
    `view_cached` says for the same inputs. If either changes, this fails.
    """
    import itertools
    from datetime import UTC, datetime, timedelta

    from app.services.normalise import view_cached

    account = account_id(fleet.engine)
    ages = {"none": None, "fresh": 30.0, "stale": 600.0}
    cached_states = list(SERVICE_STATES)
    expected: dict[str, str] = {}
    run = uuid.uuid4().hex[:8]
    created_servers: set[str] = set()

    for os_family, server_monitoring, has_instance, agent_status in itertools.product(
        ("LINUX", "WINDOWS"), (True, False), (True, False), ("HEALTHY", "UNREACHABLE", "UNKNOWN")
    ):
        created = await register(
            fleet.client,
            account,
            operating_system=os_family,
            monitoring_enabled=server_monitoring,
            metrics_instance=_instance() if has_instance else None,
        )
        assert created.status_code == 201, created.text
        server_id = created.json()["id"]
        created_servers.add(server_id)
        agent(fleet.engine, server_id, agent_status)
        for service_monitoring, cached, age in itertools.product(
            (True, False), cached_states, ages
        ):
            name = f"u{run}n{len(expected)}.service"
            service = (
                await call(
                    fleet.client,
                    "POST",
                    f"/api/v1/servers/{server_id}/services",
                    {"service_name": name, "monitoring_enabled": service_monitoring},
                )
            ).json()
            cache(fleet.engine, service["service_id"], cached, ages[age])
            expected[name] = view_cached(
                os_family=os_family,
                monitoring_enabled=server_monitoring and service_monitoring,
                has_metrics_instance=has_instance,
                agent_status=agent_status,
                cached_state=cached,
                observed_at=None
                if ages[age] is None
                else datetime.now(UTC) - timedelta(seconds=ages[age] or 0),
                now=datetime.now(UTC),
            ).state

    for state in SERVICE_STATES:
        got: set[str] = set()
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"state": state, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            body = (await call(fleet.client, "GET", "/api/v1/services", params=params)).json()
            assert all(item["state"] == state for item in body["items"])
            got |= {
                item["service_name"]
                for item in body["items"]
                if item["server_id"] in created_servers
            }
            cursor = body["page"]["next_cursor"]
            if not body["page"]["has_more"]:
                break
        assert got == {name for name, shown in expected.items() if shown == state}, state
