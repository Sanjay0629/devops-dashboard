"""Health on the read path, against real rows (M2.5 commit 3).

* **Parity.** ``monitoring.read.health_state_expr`` is ``evaluate``'s state
  written as a ``CASE`` so the dashboard can count and filter by it. This runs a
  table of servers — every gate against every side of every line, in
  differently-configured environments — through both and fails if they ever
  disagree. It is the same arrangement as ``displayed_state``'s.
* **The three rules over the wire.** A card's number is the length of the list
  it links to; nothing is judged without a fresh Prometheus reference (D2); and
  ``recent_health_changes`` is scope-filtered through the audit row's target.
"""
# ruff: noqa: F811 — fixtures are imported by name and used as arguments

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.inventory.models import Server
from app.monitoring import marker
from app.monitoring.health import HealthThresholdTable
from app.monitoring.read import Reference, evaluate, health_state_expr
from tests.integration.inventory_helpers import STAGING_SCOPE, call, grant, revoke_all
from tests.integration.test_auth_flow import (  # noqa: F401
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)
from tests.integration.test_dashboard_api import (  # noqa: F401
    HEALTH,
    SUMMARY,
    dashboard,
    uncached,
)
from tests.integration.test_monitoring import insert_server, unique

REF = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
TABLE = HealthThresholdTable.model_validate({"production": {"stale_after_seconds": 120}})

# --- parity ----------------------------------------------------------------------------

AGES: list[float | None] = [
    None,
    -5,  # last_seen_at after the reference: fresh, not an error
    0,
    29.999,
    30,
    30.001,
    119.999,
    120,
    120.001,
    300,
    300.001,
    899.999,
    900,
    900.001,
    100_000,
]
GATES: dict[str, dict[str, Any]] = {
    "plain": {},
    "retired": {"status": "RETIRED"},
    "windows": {"operating_system": "WINDOWS"},
    "unmonitored": {"monitoring": False},
    "no_instance": {"instance": None},
}


def test_the_sql_twin_agrees_with_evaluate_on_every_row(migrated: Engine) -> None:
    ids: list[uuid.UUID] = []
    for environment in ("production", "staging", "other"):
        for gate, options in GATES.items():
            if environment == "other" and gate != "plain":
                continue
            for age in AGES:
                kwargs = {"instance": unique("par"), "environment": environment, **options}
                server_id = insert_server(migrated, **kwargs)
                with migrated.begin() as connection:
                    connection.execute(
                        text("UPDATE servers SET last_seen_at = :at WHERE id = :id"),
                        {
                            "id": server_id,
                            "at": None if age is None else REF - timedelta(seconds=age),
                        },
                    )
                ids.append(uuid.UUID(server_id))

    reference = Reference("ok", REF, None)
    with Session(migrated) as session:
        expected = {
            server.id: evaluate(server, TABLE, reference).state
            for server in session.scalars(select(Server).where(Server.id.in_(ids)))
        }
        actual = dict(
            session.execute(
                select(Server.id, health_state_expr(TABLE, REF)).where(Server.id.in_(ids))
            ).all()
        )

    assert len(expected) == len(ids)
    assert actual == expected
    # The table is only a test if it reaches every verdict the twin can give.
    assert set(expected.values()) == {"healthy", "warning", "stale", "unreachable", "unknown"}


# --- over the wire ---------------------------------------------------------------------------


@pytest.fixture
def prometheus(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[redis.Redis]:
    """Prometheus configured, and a handle on the marker Redis the app reads."""
    monkeypatch.setenv("DCP_PROMETHEUS_URL", "http://prometheus.invalid:9090")
    get_settings.cache_clear()
    client = redis.Redis.from_url(redis_url)
    client.delete(marker.MARKER_KEY)
    yield client
    client.delete(marker.MARKER_KEY)
    get_settings.cache_clear()


def watch(engine: Engine, team: str, ages: list[float | None], answered: datetime) -> None:
    """Make the team's servers scraped, last seen ``ages`` before ``answered``."""
    with engine.begin() as connection:
        ids = [
            row[0]
            for row in connection.execute(
                text("SELECT id FROM servers WHERE owner_team = :t ORDER BY id"), {"t": team}
            )
        ]
        assert len(ids) == len(ages)
        for server_id, age in zip(ids, ages, strict=True):
            connection.execute(
                text(
                    "UPDATE servers SET monitoring_enabled = true, operating_system = 'LINUX',"
                    " metrics_instance = :instance, last_seen_at = :at WHERE id = :id"
                ),
                {
                    "id": server_id,
                    "instance": unique("wire"),
                    "at": None if age is None else answered - timedelta(seconds=age),
                },
            )


#: Six servers at default thresholds (30 s / 5 min / 15 min).
AGES6: list[float | None] = [5, 100, 400, 1_000, None, 5]
EXPECTED6 = {"healthy": 2, "warning": 1, "stale": 1, "unreachable": 1, "unknown": 1}


async def rows(client: httpx.AsyncClient, team: str, **params: Any) -> list[dict[str, Any]]:
    response = await call(
        client, "GET", HEALTH, params={"owner_team": team, "limit": 100, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


async def test_the_rollup_counts_every_server_and_the_list_agrees(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    prometheus: redis.Redis,
    uncached: None,
) -> None:
    client, _caller, team = dashboard
    answered = datetime.now(UTC)
    marker.write_marker(prometheus, answered)
    watch(migrated, team, AGES6, answered)

    response = await call(client, "GET", SUMMARY, params={"owner_team": team})
    rollup = response.json()["server_health"]

    assert rollup["data_state"] == "ok"
    assert {k: rollup[k] for k in EXPECTED6} == EXPECTED6
    assert sum(rollup[k] for k in EXPECTED6) == response.json()["servers"]["total"]
    # Never claimed: it would come from utilisation, which is not evaluated.
    assert rollup["critical"] is None
    assert datetime.fromisoformat(rollup["collected_at"]) == answered

    listed = await rows(client, team)
    for state, count in EXPECTED6.items():
        assert [row["health_state"] for row in listed].count(state) == count
        assert len(await rows(client, team, health_status=state)) == count


async def test_a_row_is_stale_only_when_its_verdict_says_so(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    prometheus: redis.Redis,
) -> None:
    client, _caller, team = dashboard
    answered = datetime.now(UTC)
    marker.write_marker(prometheus, answered)
    watch(migrated, team, AGES6, answered)

    by_state: dict[str, set[bool | None]] = {}
    for row in await rows(client, team):
        by_state.setdefault(row["health_state"], set()).add(row["stale"])

    assert by_state == {
        "healthy": {False},
        "warning": {False},
        "stale": {True},
        "unreachable": {True},
        "unknown": {None},
    }


async def test_a_filter_pages_over_what_it_selects(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    prometheus: redis.Redis,
) -> None:
    client, _caller, team = dashboard
    answered = datetime.now(UTC)
    marker.write_marker(prometheus, answered)
    watch(migrated, team, AGES6, answered)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {
            "owner_team": team,
            "health_status": ["healthy", "warning"],
            "limit": 1,
        }
        if cursor:
            params["cursor"] = cursor
        body = (await call(client, "GET", HEALTH, params=params)).json()
        seen += [row["hostname"] for row in body["items"]]
        cursor = body["page"]["next_cursor"]
        if not body["page"]["has_more"]:
            break

    assert len(seen) == len(set(seen)) == 3


async def test_nothing_is_judged_when_prometheus_has_not_answered(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    prometheus: redis.Redis,
    uncached: None,
) -> None:
    """D2 / issue 67. No marker: counts are null (never 0), rows null, the filter empty."""
    client, _caller, team = dashboard
    watch(migrated, team, AGES6, datetime.now(UTC))

    rollup = (await call(client, "GET", SUMMARY, params={"owner_team": team})).json()[
        "server_health"
    ]
    assert rollup["data_state"] == "unavailable"
    assert rollup["unavailable_reason"]
    assert all(rollup[k] is None for k in (*EXPECTED6, "critical"))

    listed = await rows(client, team)
    assert len(listed) == 6
    assert {row["health_state"] for row in listed} == {None}
    assert {row["stale"] for row in listed} == {None}
    # Honoured, not dropped: dropping it would show all six as the "healthy" subset.
    assert await rows(client, team, health_status="healthy") == []


async def test_an_outage_does_not_age_the_fleet_into_unreachable(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    prometheus: redis.Redis,
    uncached: None,
) -> None:
    """The marker stopped two hours ago: hosts are *dated*, not judged by it."""
    client, _caller, team = dashboard
    answered = datetime.now(UTC) - timedelta(hours=2)
    marker.write_marker(prometheus, answered)
    watch(migrated, team, AGES6, answered)

    rollup = (await call(client, "GET", SUMMARY, params={"owner_team": team})).json()[
        "server_health"
    ]

    assert rollup["data_state"] == "stale"
    assert datetime.fromisoformat(rollup["collected_at"]) == answered
    assert all(rollup[k] is None for k in EXPECTED6)
    assert {row["health_state"] for row in await rows(client, team)} == {None}


async def test_the_server_detail_carries_the_same_verdict(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    prometheus: redis.Redis,
) -> None:
    client, _caller, team = dashboard
    answered = datetime.now(UTC)
    marker.write_marker(prometheus, answered)
    watch(migrated, team, AGES6, answered)

    seen: dict[str, tuple[str | None, bool | None, str]] = {}
    for row in await rows(client, team):
        detail = (await call(client, "GET", f"/api/v1/servers/{row['server_id']}")).json()
        assert detail["health"]["state"] == row["health_state"]
        assert detail["stale"] == row["stale"]
        seen[row["health_state"]] = (
            detail["health"]["state"],
            detail["stale"],
            detail["health"]["data_state"],
        )

    assert seen["healthy"] == ("healthy", False, "ok")
    assert seen["stale"] == ("stale", True, "stale")
    assert seen["unreachable"] == ("unreachable", True, "stale")
    assert seen["unknown"] == ("unknown", None, "unavailable")


# --- recent_health_changes (D4) ----------------------------------------------------------------


def audit_move(engine: Engine, server_id: str, environment: str, *, ago: int, to: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type,"
                " target_type, target_id, environment, correlation_id, outcome,"
                " sanitized_metadata) VALUES (:id, :at, 'SYSTEM', 'system:health-sweep',"
                " 'SERVER_STATUS_CHANGED', 'server', :target, :env, :corr, 'SUCCESS',"
                " CAST(:meta AS jsonb))"
            ),
            {
                "id": uuid.uuid4(),
                "at": datetime.now(UTC) - timedelta(minutes=ago),
                "target": server_id,
                "env": environment,
                "corr": uuid.uuid4(),
                "meta": f'{{"from": "MANAGED", "to": "{to}", "health_state": "stale"}}',
            },
        )


async def test_recent_health_changes_are_real_ordered_and_scoped(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    migrated: Engine,
    uncached: None,
) -> None:
    client, caller, team = dashboard
    with migrated.connect() as connection:
        staging, production = (
            str(
                connection.execute(
                    text(
                        "SELECT id FROM servers WHERE owner_team = :t AND environment = :e"
                        " ORDER BY id LIMIT 1"
                    ),
                    {"t": team, "e": environment},
                ).scalar_one()
            )
            for environment in ("staging", "production")
        )
    audit_move(migrated, staging, "staging", ago=30, to="STALE")
    audit_move(migrated, production, "production", ago=5, to="UNREACHABLE")

    body = (await call(client, "GET", SUMMARY, params={"owner_team": team})).json()
    section = body["recent_health_changes"]
    assert section["data_state"] == "ok"
    assert [(i["server_id"], i["new_status"]) for i in section["items"]] == [
        (production, "UNREACHABLE"),
        (staging, "STALE"),
    ]
    assert section["items"][0]["previous_status"] == "MANAGED"
    assert section["items"][0]["health_state"] == "stale"

    # A caller who cannot read production must not learn it moved.
    revoke_all(migrated, caller)
    grant(migrated, caller, "VIEWER", scope=STAGING_SCOPE)
    narrow = (await call(client, "GET", SUMMARY, params={"owner_team": team})).json()
    assert [i["server_id"] for i in narrow["recent_health_changes"]["items"]] == [staging]


async def test_no_recorded_move_is_an_empty_list_not_a_null(
    dashboard: tuple[httpx.AsyncClient, uuid.UUID, str],
    uncached: None,
) -> None:
    """A real table: `[]` is "no recorded change", and `null` would say "unavailable"."""
    client, _caller, team = dashboard
    section = (await call(client, "GET", SUMMARY, params={"owner_team": team})).json()[
        "recent_health_changes"
    ]
    assert (section["data_state"], section["items"]) == ("ok", [])
