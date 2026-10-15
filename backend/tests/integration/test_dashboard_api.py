"""Dashboard endpoints end to end (§13.1, §13.2, IMPLEMENTATION_PLAN.md M1.6).

Here rather than in ``tests/dashboard/`` for the reason every other API suite
is: the counts have to come out of real rows through the real scope
predicate, and the cache has to be a real Redis, or none of it means
anything. The pure parts — the cache key's scope segment, and the
``data_state`` defaults — are unit-tested in ``tests/dashboard/test_summary.py``.

What this covers, from M1.6's test list and security checks:

* aggregate counts matching the rows, and matching ``SELECT count(*)``;
* filter combinations, including one that narrows to nothing;
* **counts respect scope** — the readable set, not the whole table;
* **the cache key includes scope**, proven by narrowing a live caller's grant
  and watching the answer narrow with it rather than replaying the wide one;
* cache hit and miss, observed through ``collected_at``;
* every section with no source reporting ``not_configured`` with ``null``,
  never ``0``;
* cursor paging through §13.2.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

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

SUMMARY = "/api/v1/dashboard/summary"
HEALTH = "/api/v1/dashboard/health"

#: Distinct enough to tell the status counts apart from each other and from
#: the total. Registration always lands ``PENDING`` (PRD §4.3), so the other
#: statuses are set directly — the administrative PATCH only reaches three of
#: the five, and the stale sweep that would set the rest is M2.5.
FIXTURE = [
    ("staging", "MANAGED"),
    ("staging", "MANAGED"),
    ("staging", "PENDING"),
    ("production", "MANAGED"),
    ("production", "STALE"),
    ("development", "UNREACHABLE"),
]


@pytest.fixture
async def dashboard(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> tuple[httpx.AsyncClient, uuid.UUID, str]:
    """A full-scope Administrator and six servers across three environments.

    The servers share an ``owner_team`` unique to this test, and it is handed
    back so assertions that walk rows can filter to *these* servers. The
    schema is rebuilt once per session, not per test, so by the time this file
    is halfway through, the table also holds every server the earlier tests
    registered — and a test that asserts something about "every row" would be
    asserting it about those too.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    grant(migrated, caller, "ADMINISTRATOR")
    account = account_id(migrated)
    team = f"team-{uuid.uuid4().hex[:10]}"

    for environment, status in FIXTURE:
        response = await register(client, account, environment=environment, owner_team=team)
        assert response.status_code == 201, response.text
        if status != "PENDING":
            with migrated.begin() as connection:
                connection.execute(
                    text("UPDATE servers SET status = :s WHERE id = :id"),
                    {"s": status, "id": uuid.UUID(response.json()["id"])},
                )
    return client, caller, team


@pytest.fixture
def uncached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the cache off for one test, which is also M1.6's documented rollback.

    Needed wherever a test writes and then reads: the aggregate is cached for
    ``dashboard_cache_ttl_seconds``, so within that window a read legitimately
    returns the pre-write answer. A test that did not account for that would
    be asserting the cache away rather than the behaviour it names.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("DCP_DASHBOARD_CACHE_TTL_SECONDS", "0")
    get_settings.cache_clear()
    yield  # type: ignore[misc]
    get_settings.cache_clear()


async def _summary(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await call(client, "GET", SUMMARY, params=params or None)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _count(migrated: Engine, where: str, **params: Any) -> int:
    """``SELECT count(*)``, to check the endpoint against the database itself.

    ``where`` is interpolated, and every caller in this file passes a literal.
    Values never are — they go through ``params`` as bind parameters — so the
    only thing a caller can put into the SQL is SQL they wrote themselves, in
    a test, against ``dcp_test``.
    """
    with migrated.connect() as connection:
        sql = f"SELECT count(*) FROM servers WHERE deleted_at IS NULL AND {where}"  # noqa: S608
        return connection.execute(text(sql), params).scalar_one()  # type: ignore[no-any-return]


# --- 13.1 counts -------------------------------------------------------------


async def test_the_counts_match_the_rows(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    """Checked against ``SELECT count(*)``, not against the fixture's arithmetic.

    Counting the fixture would prove the test can add up. Counting the table
    proves the query selects what the database holds.
    """
    client, _caller, _team = dashboard
    body = await _summary(client)
    servers = body["servers"]

    assert servers["total"] == _count(migrated, "TRUE")
    for field, status in (
        ("pending", "PENDING"),
        ("managed", "MANAGED"),
        ("stale", "STALE"),
        ("unreachable", "UNREACHABLE"),
        ("retired", "RETIRED"),
    ):
        assert servers[field] == _count(migrated, "status = :s", s=status), field

    assert servers["data_state"] == "ok"
    assert servers["source"] == "postgresql"
    assert servers["collected_at"] is not None


async def test_a_retired_server_leaves_the_total(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    uncached: None,
) -> None:
    """Retirement is a soft delete, and the dashboard counts live inventory.

    The same ``deleted_at IS NULL`` the list query applies, so the two screens
    cannot disagree about how many servers exist.

    Runs ``uncached``: within the TTL the second read would legitimately
    replay the pre-retirement answer. That lag is the cache working as
    specified, not this behaviour failing — see PROGRESS.md §6.
    """
    client, _caller, team = dashboard
    before = (await _summary(client, owner_team=team))["servers"]
    assert before["total"] == len(FIXTURE)

    listed = await call(client, "GET", HEALTH, params={"owner_team": team, "limit": 100})
    retired = await call(
        client, "DELETE", f"/api/v1/servers/{listed.json()['items'][0]['server_id']}"
    )
    assert retired.status_code == 204, retired.text

    after = (await _summary(client, owner_team=team))["servers"]
    assert after["total"] == len(FIXTURE) - 1


async def test_failed_services_is_a_real_zero(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """``services`` exists and is empty, so ``0`` is a fact, not a stand-in.

    The distinction this milestone turns on: this section may say ``0``
    precisely because it has a source. The sections below may not.
    """
    client, _caller, _team = dashboard
    failed = (await _summary(client))["failed_services"]
    assert failed["count"] == 0
    assert failed["data_state"] == "ok"
    assert failed["source"] == "postgresql"


async def test_a_failed_unit_is_counted_through_its_server(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    uncached: None,
) -> None:
    client, _caller, team = dashboard
    listed = await call(client, "GET", HEALTH, params={"owner_team": team, "limit": 100})
    server_id = listed.json()["items"][0]["server_id"]
    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO services (id, server_id, service_name, monitoring_enabled,"
                " last_observed_state, created_at, updated_at)"
                " VALUES (gen_random_uuid(), :server, 'nginx.service', true, 'FAILED',"
                " now(), now())"
            ),
            {"server": uuid.UUID(server_id)},
        )
    body = await _summary(client, owner_team=team)
    assert body["failed_services"]["count"] == 1
    assert body["failed_services"]["data_state"] == "ok"


# --- honest data states (PRD §4.2 acceptance criterion) -----------------------


async def test_every_section_without_a_source_is_null_and_says_why(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """The acceptance criterion, over the wire, for all six absent sources."""
    client, _caller, _team = dashboard
    body = await _summary(client)

    for name, fields in (
        ("server_health", ("healthy", "warning", "critical", "unknown")),
        (
            "utilisation",
            (
                "cpu_percent_avg",
                "memory_percent_avg",
                "disk_percent_max",
                "network_bytes_per_second",
            ),
        ),
        ("active_alerts", ("critical", "warning", "info")),
        ("recent_deployments", ("items",)),
        ("recent_operations", ("items",)),
        ("recent_health_changes", ("items",)),
    ):
        section = body[name]
        assert section["data_state"] == "not_configured", name
        assert section["collected_at"] is None, name
        # Names the milestone, so the reason is actionable rather than "n/a".
        assert section["unavailable_reason"], name
        for field in fields:
            assert section[field] is None, f"{name}.{field} must be null, never 0 or []"


# --- filters -----------------------------------------------------------------


async def test_an_environment_filter_narrows_the_counts(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    client, _caller, _team = dashboard
    body = await _summary(client, environment="staging")
    assert body["servers"]["total"] == _count(migrated, "environment = 'staging'")
    assert body["filters_applied"]["environment"] == ["staging"]


async def test_filters_combine_as_an_intersection(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    client, _caller, _team = dashboard
    body = await _summary(client, environment="staging", operating_system="LINUX")
    expected = _count(migrated, "environment = 'staging' AND operating_system = 'LINUX'")
    assert body["servers"]["total"] == expected


async def test_a_filter_matching_nothing_counts_zero_rather_than_everything(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """A dropped filter would return the unfiltered totals looking filtered."""
    client, _caller, _team = dashboard
    body = await _summary(client, owner_team="a-team-that-owns-nothing")
    assert body["servers"]["total"] == 0


async def test_an_unknown_filter_is_rejected(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """§6: a typo'd filter is a ``422``, never a silently unfiltered answer."""
    client, _caller, _team = dashboard
    response = await call(client, "GET", SUMMARY, params={"environmnet": "staging"})
    assert response.status_code == 422, response.text


async def test_an_unknown_filter_value_is_rejected(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    client, _caller, _team = dashboard
    response = await call(client, "GET", SUMMARY, params={"environment": "staaging"})
    assert response.status_code == 422, response.text


# --- scope (M1.6 security check: "dashboard counts respect scope") -----------


async def test_counts_are_scope_filtered(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    """A staging-scoped Viewer counts staging servers, not the table.

    The count is filtered inside the query (docs/SECURITY.md §20), so the
    production rows are never selected — a total over them would be an
    inference channel about inventory the caller cannot list.
    """
    client, caller, _team = dashboard
    revoke_all(migrated, caller)
    grant(migrated, caller, "VIEWER", scope=STAGING_SCOPE)

    body = await _summary(client)
    assert body["servers"]["total"] == _count(migrated, "environment = 'staging'")
    assert body["servers"]["total"] < _count(migrated, "TRUE")


async def test_a_caller_with_no_grants_counts_nothing(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    """Fail closed: no grant is zero readable servers, not an unfiltered count."""
    client, caller, _team = dashboard
    revoke_all(migrated, caller)
    response = await call(client, "GET", SUMMARY)
    # No role at all means no `dashboard:read` either, so the request never
    # reaches the query. Both answers are correct; neither is a count.
    assert response.status_code == 403, response.text


async def test_a_scope_change_is_not_served_from_the_previous_cache_entry(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    """**The cache-key security check, end to end.**

    Same session, same cookie, same URL, same filters — only the caller's
    grant narrows, well inside the 15-second TTL. A cache keyed on the
    filters alone would replay the full-scope totals to a caller who may now
    see only staging, and no amount of correct SQL would catch it because the
    SQL would not run.

    It asserts the narrowed *value*, not merely that something changed: a key
    that happened to differ for an unrelated reason would still pass a
    "not equal" assertion.
    """
    client, caller, _team = dashboard
    wide = (await _summary(client))["servers"]["total"]

    revoke_all(migrated, caller)
    grant(migrated, caller, "VIEWER", scope=STAGING_SCOPE)

    narrow = (await _summary(client))["servers"]["total"]
    assert narrow == _count(migrated, "environment = 'staging'")
    assert narrow < wide


# --- caching -----------------------------------------------------------------


async def test_a_repeated_request_is_served_from_the_cache(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """Observed through ``collected_at``: a recomputed answer restamps it."""
    client, _caller, _team = dashboard
    first = await _summary(client)
    second = await _summary(client)
    assert first["servers"]["collected_at"] == second["servers"]["collected_at"]


async def test_different_filters_are_a_cache_miss(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    client, _caller, _team = dashboard
    everything = await _summary(client)
    staging = await _summary(client, environment="staging")
    assert staging["servers"]["total"] != everything["servers"]["total"]
    assert staging["filters_applied"]["environment"] == ["staging"]


async def test_the_cache_ttl_is_reported(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """So the client does not have to guess how often polling is worth anything."""
    client, _caller, _team = dashboard
    assert (await _summary(client))["cache_ttl_seconds"] >= 0


# --- 13.2 health list --------------------------------------------------------


async def test_the_health_list_never_defaults_a_server_to_healthy(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """Nothing has looked at these hosts, so none of them is known to be well."""
    client, _caller, team = dashboard
    response = await call(client, "GET", HEALTH, params={"owner_team": team, "limit": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == len(FIXTURE)
    for row in body["items"]:
        assert row["health_state"] is None, row["hostname"]
        assert row["active_alert_count"] is None, row["hostname"]
        # Never seen is not stale: reporting `false` would claim a freshness
        # nothing has observed.
        assert row["stale"] is None, row["hostname"]
        # Real, because `services` is a table we can count.
        assert row["failed_service_count"] == 0, row["hostname"]
    assert body["health"]["data_state"] == "not_configured"
    assert body["alerts"]["data_state"] == "not_configured"


async def test_the_health_list_is_scope_filtered(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
) -> None:
    client, caller, _team = dashboard
    revoke_all(migrated, caller)
    grant(migrated, caller, "VIEWER", scope=STAGING_SCOPE)
    response = await call(client, "GET", HEALTH, params={"limit": 100})
    assert response.status_code == 200, response.text
    environments = {row["environment"] for row in response.json()["items"]}
    assert environments <= {"staging"}


async def test_the_health_list_pages_with_a_cursor(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """§5 keyset paging, and every row appearing exactly once across pages."""
    client, _caller, team = dashboard
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 2, "owner_team": team}
        if cursor is not None:
            params["cursor"] = cursor
        response = await call(client, "GET", HEALTH, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(row["server_id"] for row in body["items"])
        if not body["page"]["has_more"]:
            break
        cursor = body["page"]["next_cursor"]
        assert cursor is not None
    else:  # pragma: no cover - a runaway loop is a bug, not a slow test
        pytest.fail("paging did not terminate")

    assert len(seen) == len(set(seen)), "a row appeared on two pages"
    assert len(seen) == len(FIXTURE)
    single = await call(client, "GET", HEALTH, params={"owner_team": team, "limit": 200})
    assert set(seen) == {row["server_id"] for row in single.json()["items"]}


async def test_an_environment_filter_updates_the_health_list(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """PRD §4.2 acceptance: selecting a filter updates the server list."""
    client, _caller, team = dashboard
    response = await call(
        client,
        "GET",
        HEALTH,
        params={"limit": 100, "environment": "production", "owner_team": team},
    )
    assert response.status_code == 200, response.text
    assert {row["environment"] for row in response.json()["items"]} == {"production"}


async def test_filtering_on_health_status_returns_nothing_rather_than_everything(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """The filter has no source yet, so it matches nothing — and says so.

    The failure this guards against is the opposite one: a filter the backend
    does not implement being dropped, so the operator sees the whole list and
    believes it is the healthy subset.
    """
    client, _caller, _team = dashboard
    response = await call(client, "GET", HEALTH, params={"health_status": "healthy"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["health"]["data_state"] == "not_configured"
    assert body["health"]["unavailable_reason"]


async def test_the_health_list_reports_the_provider_of_each_server(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
) -> None:
    """§13.2 names ``provider``, which lives on the cloud account, not the server."""
    client, _caller, team = dashboard
    response = await call(client, "GET", HEALTH, params={"owner_team": team, "limit": 100})
    assert {row["provider"] for row in response.json()["items"]} == {"ON_PREM"}
