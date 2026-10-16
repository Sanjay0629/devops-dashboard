"""§15.1–§15.3 end to end: real sessions, grants, scope predicate and rows.

Prometheus is scripted (``tests.metrics.fake_prometheus``) because what this
file establishes is how the *portal* treats an answer — who may ask, which rows
the answer is about, and what is said when there is no number — not whether a
``node_exporter`` is reachable (M2.1's smoke test covers that against a real
one). The transport is substituted at ``readings.make_client`` and nowhere else.

M2.2's stated security check lives here: **per-server scope is enforced on
metric reads**, and a comparison drops out-of-scope servers rather than
failing. Both are asserted by narrowing a live caller's grant and watching the
answer narrow, not by reading the handler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.metrics import readings, templates
from app.metrics.client import CircuitBreaker, PrometheusClient, reset_breaker
from tests.integration.inventory_helpers import (
    STAGING_SCOPE,
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

START = "2025-10-09T07:53:20Z"  # NOW - 3600
END = "2025-10-09T08:53:20Z"  # NOW
STEP = 900


@dataclass
class Fleet:
    client: httpx.AsyncClient
    engine: Engine
    caller: uuid.UUID
    fake: FakePrometheus
    #: name -> (server id, metrics_instance)
    servers: dict[str, tuple[str, str]]

    def id(self, name: str) -> str:
        return self.servers[name][0]

    def instance(self, name: str) -> str:
        return self.servers[name][1]


def _unique_instance() -> str:
    return f"h{uuid.uuid4().hex[:10]}:9100"


@pytest.fixture
async def fleet(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Fleet:
    """Three staging servers and a production one, plus a scripted Prometheus.

    Named so that ``display_name`` ordering (which §15.3 uses) is stable:
    ``A web`` < ``B db`` < ``C bare`` < ``D prod``.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    account = account_id(migrated)

    rows = [
        ("web", "A web", "staging", _unique_instance()),
        ("db", "B db", "staging", _unique_instance()),
        ("bare", "C bare", "staging", None),
        ("prod", "D prod", "production", _unique_instance()),
    ]
    servers: dict[str, tuple[str, str]] = {}
    for name, display, environment, instance in rows:
        response = await register(
            client,
            account,
            display_name=display,
            environment=environment,
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


def narrow_to_staging(fleet: Fleet) -> None:
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "VIEWER", STAGING_SCOPE)


def script_current(fleet: Fleet, name: str, template_id: str, value: str, age: float = 5) -> None:
    instance = fleet.instance(name)
    fleet.fake.instant[templates.render(template_id, "LINUX", [instance])] = [(instance, value)]
    fleet.fake.instant[templates.render_scrape_timestamp("LINUX", [instance])] = [
        (instance, str(NOW - age))
    ]


def metrics_url(fleet: Fleet, name: str, suffix: str = "") -> str:
    return f"/api/v1/servers/{fleet.id(name)}/metrics{suffix}"


# --- §15.1 ---------------------------------------------------------------------


async def test_current_values_come_back_with_their_state_and_timestamp(fleet: Fleet) -> None:
    script_current(fleet, "web", "cpu_utilisation", "91.5")

    response = await call(
        fleet.client, "GET", metrics_url(fleet, "web"), params={"metrics": "cpu_utilisation"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    [reading] = body["metrics"]
    assert reading["value"] == 91.5
    assert reading["data_state"] == "ok"
    assert reading["threshold_state"] == "critical"
    assert reading["collected_at"] == "2025-10-09T08:53:15Z"
    assert body["thresholds"] == {"cpu_utilisation": {"warning": 80.0, "critical": 90.0}}
    assert body["source"] == "prometheus"
    assert body["server_id"] == fleet.id("web")


async def test_the_default_is_every_template_and_none_leaks_promql(fleet: Fleet) -> None:
    script_current(fleet, "web", "cpu_utilisation", "10")

    response = await call(fleet.client, "GET", metrics_url(fleet, "web"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert [m["template"] for m in body["metrics"]] == list(templates.TEMPLATE_IDS)
    # The values for templates nothing scripted are unavailable, not zero.
    others = [m for m in body["metrics"] if m["template"] != "cpu_utilisation"]
    assert all(m["value"] is None and m["data_state"] == "unavailable" for m in others)
    for fragment in ("node_", "rate(", "instance=", "timestamp("):
        assert fragment not in response.text


async def test_a_server_with_no_series_is_unavailable_with_a_200(fleet: Fleet) -> None:
    fleet.fake.instant[templates.render("cpu_utilisation", "LINUX", [fleet.instance("web")])] = []
    fleet.fake.instant[templates.render_scrape_timestamp("LINUX", [fleet.instance("web")])] = []

    response = await call(
        fleet.client, "GET", metrics_url(fleet, "web"), params={"metrics": "cpu_utilisation"}
    )

    assert response.status_code == 200
    [reading] = response.json()["metrics"]
    assert reading["value"] is None
    assert reading["data_state"] == "unavailable"
    assert reading["unavailable_reason"] == "no_data"


async def test_a_server_with_no_metrics_instance_is_not_configured_and_not_queried(
    fleet: Fleet,
) -> None:
    response = await call(
        fleet.client, "GET", metrics_url(fleet, "bare"), params={"metrics": "cpu_utilisation"}
    )

    assert response.status_code == 200
    [reading] = response.json()["metrics"]
    assert (reading["data_state"], reading["unavailable_reason"]) == (
        "not_configured",
        "no_metrics_instance",
    )
    assert fleet.fake.queries == []


async def test_a_dead_prometheus_is_a_section_state_not_a_5xx(fleet: Fleet) -> None:
    script_current(fleet, "web", "cpu_utilisation", "10")
    for query in list(fleet.fake.instant):
        fleet.fake.errors[query] = lambda _r: httpx.Response(503)

    response = await call(
        fleet.client, "GET", metrics_url(fleet, "web"), params={"metrics": "cpu_utilisation"}
    )

    assert response.status_code == 200
    [reading] = response.json()["metrics"]
    assert (reading["value"], reading["data_state"]) == (None, "unavailable")
    assert reading["unavailable_reason"] == "provider_error"


async def test_scope_is_enforced_on_a_single_server_read(fleet: Fleet) -> None:
    """The M2.2 security check: a production server is invisible to a staging caller."""
    script_current(fleet, "web", "cpu_utilisation", "10")
    script_current(fleet, "prod", "cpu_utilisation", "10")
    params = {"metrics": "cpu_utilisation"}

    assert (
        await call(fleet.client, "GET", metrics_url(fleet, "prod"), params=params)
    ).status_code == 200

    narrow_to_staging(fleet)
    assert (
        await call(fleet.client, "GET", metrics_url(fleet, "web"), params=params)
    ).status_code == 200
    hidden = await call(fleet.client, "GET", metrics_url(fleet, "prod"), params=params)
    missing = await call(
        fleet.client, "GET", f"/api/v1/servers/{uuid.uuid4()}/metrics", params=params
    )
    # Out of scope reads as absent, indistinguishably from a server that does not exist.
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_a_caller_without_metrics_read_is_refused_for_the_right_reason(
    fleet: Fleet,
) -> None:
    revoke_all(fleet.engine, fleet.caller)

    response = await call(fleet.client, "GET", metrics_url(fleet, "web"))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "csrf" not in response.json()["error"]["message"].lower()
    assert fleet.fake.queries == []


@pytest.mark.parametrize(
    "params",
    [{"metric": "cpu_utilisation"}, {"metrics": "nonsense"}, {"promql": "up"}],
)
async def test_unknown_parameters_and_templates_are_refused_not_ignored(
    fleet: Fleet, params: dict[str, str]
) -> None:
    response = await call(fleet.client, "GET", metrics_url(fleet, "web"), params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert fleet.fake.queries == []


# --- §15.2 ---------------------------------------------------------------------


def range_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "metrics": "cpu_utilisation",
        "from": START,
        "to": END,
        "step_seconds": STEP,
    }
    params.update(overrides)
    return params


def script_range(fleet: Fleet, name: str, values: list[tuple[float, str]]) -> None:
    fleet.fake.ranges[
        templates.render(
            "cpu_utilisation", "LINUX", [fleet.instance(name)], window_seconds=max(300, STEP)
        )
    ] = [(fleet.instance(name), values)]


async def test_a_gap_is_a_null_point_at_its_own_timestamp(fleet: Fleet) -> None:
    t0 = NOW - 3600
    script_range(fleet, "web", [(t0, "10"), (t0 + 900, "20"), (t0 + 2700, "30")])

    response = await call(
        fleet.client, "GET", metrics_url(fleet, "web", "/range"), params=range_params()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    [series] = body["series"]
    assert series["data_state"] == "ok"
    assert series["points"] == [
        ["2025-10-09T07:53:20Z", 10.0],
        ["2025-10-09T08:08:20Z", 20.0],
        ["2025-10-09T08:23:20Z", None],
        ["2025-10-09T08:38:20Z", 30.0],
        ["2025-10-09T08:53:20Z", None],
    ]
    # The window is echoed so the UI can show it (PRD §4.4).
    assert (body["start"], body["end"], body["step_seconds"]) == (START, END, STEP)


async def test_a_range_for_a_server_with_no_series_is_unavailable(fleet: Fleet) -> None:
    script_range(fleet, "web", [])

    response = await call(
        fleet.client, "GET", metrics_url(fleet, "web", "/range"), params=range_params()
    )

    assert response.status_code == 200
    [series] = response.json()["series"]
    assert series["points"] is None
    assert (series["data_state"], series["unavailable_reason"]) == ("unavailable", "no_data")


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"step_seconds": 5}, "query_limit_exceeded"),
        ({"from": "2025-09-01T00:00:00Z"}, "query_limit_exceeded"),
        ({"from": END, "to": START}, "validation_failed"),
        ({"from": "2025-10-09T08:13:20"}, "validation_failed"),
        ({"metrics": ["a", "b", "c", "d", "e", "f"]}, "validation_failed"),
    ],
)
async def test_range_bounds_refuse_before_any_query(
    fleet: Fleet, overrides: dict[str, object], code: str
) -> None:
    response = await call(
        fleet.client,
        "GET",
        metrics_url(fleet, "web", "/range"),
        params=range_params(**overrides),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert fleet.fake.queries == []


async def test_range_is_scope_checked_too(fleet: Fleet) -> None:
    narrow_to_staging(fleet)

    response = await call(
        fleet.client, "GET", metrics_url(fleet, "prod", "/range"), params=range_params()
    )

    assert response.status_code == 404
    assert fleet.fake.queries == []


async def test_a_range_requires_its_parameters(fleet: Fleet) -> None:
    response = await call(
        fleet.client, "GET", metrics_url(fleet, "web", "/range"), params={"metrics": "load_average"}
    )
    assert response.status_code == 422


# --- §15.3 ---------------------------------------------------------------------

COMPARE = "/api/v1/metrics/compare"


def compare_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "metric": "cpu_utilisation",
        "from": START,
        "to": END,
        "step_seconds": STEP,
    }
    body.update(overrides)
    return body


def script_compare(
    fleet: Fleet, names: list[str], data: dict[str, list[tuple[float, str]]]
) -> None:
    instances = [fleet.instance(n) for n in names]
    fleet.fake.ranges[
        templates.render("cpu_utilisation", "LINUX", instances, window_seconds=max(300, STEP))
    ] = [(fleet.instance(n), data[n]) for n in names if n in data]


async def test_a_comparison_has_one_series_per_server_in_a_single_query(fleet: Fleet) -> None:
    t0 = NOW - 3600
    script_compare(
        fleet, ["web", "db"], {"web": [(t0, "10"), (t0 + 900, "20")], "db": [(t0, "50")]}
    )

    response = await call(
        fleet.client,
        "POST",
        COMPARE,
        compare_body(server_ids=[fleet.id("db"), fleet.id("web")]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # Ordered by display name, not by the order requested.
    assert [s["display_name"] for s in body["series"]] == ["A web", "B db"]
    assert [s["environment"] for s in body["series"]] == ["staging", "staging"]
    web, db = body["series"]
    assert [v for _, v in web["points"]] == [10.0, 20.0, None, None, None]
    assert [v for _, v in db["points"]] == [50.0, None, None, None, None]
    assert body["excluded_count"] == 0
    assert body["thresholds"] == {"warning": 80.0, "critical": 90.0}
    assert len(fleet.fake.queries) == 1  # one round trip, not one per server


async def test_a_server_without_data_stays_in_the_comparison_with_its_reason(
    fleet: Fleet,
) -> None:
    script_compare(fleet, ["web"], {"web": [(NOW - 3600, "10")]})

    response = await call(
        fleet.client,
        "POST",
        COMPARE,
        compare_body(server_ids=[fleet.id("web"), fleet.id("bare")]),
    )

    assert response.status_code == 200, response.text
    web, bare = response.json()["series"]
    assert web["data_state"] == "ok"
    # Listed rather than dropped: the comparison is never silently about fewer
    # servers than the caller asked for.
    assert (bare["data_state"], bare["unavailable_reason"], bare["points"]) == (
        "not_configured",
        "no_metrics_instance",
        None,
    )


async def test_a_listed_server_whose_instance_returned_nothing_is_unavailable(
    fleet: Fleet,
) -> None:
    script_compare(fleet, ["web", "db"], {"web": [(NOW - 3600, "10")]})

    response = await call(
        fleet.client, "POST", COMPARE, compare_body(server_ids=[fleet.id("web"), fleet.id("db")])
    )

    _, db = response.json()["series"]
    assert (db["data_state"], db["unavailable_reason"]) == ("unavailable", "no_data")


async def test_out_of_scope_servers_are_dropped_and_counted(fleet: Fleet) -> None:
    script_compare(fleet, ["web"], {"web": [(NOW - 3600, "10")]})
    narrow_to_staging(fleet)

    response = await call(
        fleet.client, "POST", COMPARE, compare_body(server_ids=[fleet.id("web"), fleet.id("prod")])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [s["server_id"] for s in body["series"]] == [fleet.id("web")]
    assert body["excluded_count"] == 1
    # The hidden server is not named, queried or described anywhere.
    assert fleet.id("prod") not in response.text
    assert fleet.instance("prod") not in " ".join(fleet.fake.queries)


async def test_a_nonexistent_id_is_counted_exactly_like_an_out_of_scope_one(
    fleet: Fleet,
) -> None:
    script_compare(fleet, ["web"], {"web": [(NOW - 3600, "10")]})

    response = await call(
        fleet.client,
        "POST",
        COMPARE,
        compare_body(server_ids=[fleet.id("web"), str(uuid.uuid4())]),
    )

    assert response.status_code == 200
    assert response.json()["excluded_count"] == 1


async def test_an_environment_selection_returns_what_the_caller_may_read_there(
    fleet: Fleet,
) -> None:
    # Scoped to this fleet's staging servers by id: the schema is shared across
    # the session, so "everything in staging" would also be every server an
    # earlier test registered (and would, correctly, trip the 20-server cap).
    ids = ",".join(f'"{fleet.id(n)}"' for n in ("web", "db", "bare"))
    revoke_all(fleet.engine, fleet.caller)
    grant(
        fleet.engine,
        fleet.caller,
        "VIEWER",
        f'{{"environment":["staging"],"cloud_account":["*"],"server":[{ids}]}}',
    )
    script_compare(fleet, ["web", "db"], {"web": [(NOW - 3600, "10")]})

    response = await call(
        fleet.client, "POST", COMPARE, compare_body(environments=["staging", "production"])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {s["display_name"] for s in body["series"]} == {"A web", "B db", "C bare"}
    # Reporting a real count here would disclose how many servers exist
    # outside the caller's scope.
    assert body["excluded_count"] == 0
    assert fleet.id("prod") not in response.text


async def test_a_comparison_cap_counts_what_will_be_queried(fleet: Fleet) -> None:
    response = await call(
        fleet.client,
        "POST",
        COMPARE,
        compare_body(server_ids=[str(uuid.uuid4()) for _ in range(21)]),
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "query_limit_exceeded"
    assert body["details"]["maximum"] == 20
    assert fleet.fake.queries == []


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # neither selector
        {"server_ids": ["00000000-0000-0000-0000-000000000001"], "environments": ["staging"]},
        {"server_ids": []},
        {"metric": "nonsense", "environments": ["staging"]},
        {"environments": ["staging"], "promql": "up"},
        {"environments": ["moon"]},
    ],
)
async def test_a_malformed_comparison_is_refused(
    fleet: Fleet, overrides: dict[str, object]
) -> None:
    response = await call(fleet.client, "POST", COMPARE, compare_body(**overrides))

    assert response.status_code == 422, response.text
    assert fleet.fake.queries == []


async def test_a_comparison_needs_the_permission_and_not_just_a_session(fleet: Fleet) -> None:
    revoke_all(fleet.engine, fleet.caller)

    response = await call(fleet.client, "POST", COMPARE, compare_body(environments=["staging"]))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "csrf" not in response.json()["error"]["message"].lower()


async def test_a_comparison_still_works_in_read_only_mode(fleet: Fleet) -> None:
    """It is a POST that reads, so containment must not blind the operator."""
    script_compare(fleet, ["web"], {"web": [(NOW - 3600, "10")]})
    with fleet.engine.begin() as connection:
        connection.execute(
            text("UPDATE system_settings SET value = 'true'::jsonb WHERE key = 'read_only_mode'")
        )
    try:
        response = await call(
            fleet.client, "POST", COMPARE, compare_body(server_ids=[fleet.id("web")])
        )
    finally:
        with fleet.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE system_settings SET value = 'false'::jsonb WHERE key = 'read_only_mode'"
                )
            )

    assert response.status_code == 200, response.text


async def test_a_windows_server_in_a_comparison_is_unsupported(fleet: Fleet) -> None:
    with fleet.engine.begin() as connection:
        connection.execute(
            text("UPDATE servers SET operating_system = 'WINDOWS' WHERE id = :id"),
            {"id": uuid.UUID(fleet.id("db"))},
        )
    script_compare(fleet, ["web"], {"web": [(NOW - 3600, "10")]})

    response = await call(
        fleet.client, "POST", COMPARE, compare_body(server_ids=[fleet.id("web"), fleet.id("db")])
    )

    assert response.status_code == 200, response.text
    web, db = response.json()["series"]
    assert web["data_state"] == "ok"
    assert (db["data_state"], db["unavailable_reason"], db["points"]) == (
        "unsupported",
        "unsupported_os",
        None,
    )
    assert fleet.instance("db") not in " ".join(fleet.fake.queries)
