"""Endpoints 24.1 and 24.2 end to end (M4.6, ``OPEN-109`` – ``OPEN-111``).

Real PostgreSQL and Redis through the real application. What the SQL scope selects, against the
matcher, is ``test_history_and_audit_scope.py``; here is the wire: who may read, what a read leaves
behind (``AUDIT_QUERIED``), what comes back (redacted again), the windows, and that no route can
write, change or delete an event.

The suite's database is shared and audit rows are never deleted, so every assertion about *which*
events come back is narrowed by a filter that selects this test's own rows, and every assertion
about ``AUDIT_QUERIED`` is a count before and after.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.integration.inventory_helpers import (
    FULL_SCOPE,
    STAGING_SCOPE,
    call,
    grant,
    revoke_all,
)
from tests.integration.test_approvals_api import Actor, _second_login, waiting  # noqa: F401
from tests.integration.test_auth_flow import FakeIdp, auth_app  # noqa: F401
from tests.integration.test_history_and_audit_scope import audit_event
from tests.integration.test_operations_api import (  # noqa: F401 -- fixtures used by name
    Rig,
    adapter,
    idp,
    idp_key,
    redis_url,
    rig,
    subject,
)

AUDIT = "/api/v1/audit-events"
PRODUCTION_ONLY = (
    '{"environment":["production"],"cloud_account":["*"],"server":["*"],"application":["*"]}'
)


async def listed(client: httpx.AsyncClient, **params: Any) -> httpx.Response:
    return await call(
        client, "GET", AUDIT, params={k: v for k, v in params.items() if v is not None}
    )


def ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def queried(engine: Engine, actor: uuid.UUID) -> list[dict[str, Any]]:
    """The caller's ``AUDIT_QUERIED`` rows, oldest first."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT * FROM audit_events WHERE action_type = 'AUDIT_QUERIED'"
                " AND actor_id = :a ORDER BY occurred_at, id"
            ),
            {"a": actor},
        ).mappings()
        return [dict(row) for row in rows]


def insert(engine: Engine, environment: str | None, correlation: uuid.UUID, **extra: Any) -> str:
    with engine.begin() as connection:
        return str(audit_event(connection, environment, correlation, **extra))


@pytest.fixture
async def admin(rig: Rig) -> Rig:
    """The rig's caller as an Administrator: all four environments named, as the bootstrap does."""
    rig.as_role("ADMINISTRATOR")
    return rig


@pytest.fixture
async def reader(rig: Rig, auth_app: tuple[httpx.AsyncClient, FakeIdp]) -> AsyncIterator[Actor]:
    """A second person, an ``APPROVER`` (so ``audit:read``) with whatever scope a test gives."""
    actor = await _second_login(rig, auth_app[1])
    yield actor
    revoke_all(rig.engine, actor.id)
    await actor.client.aclose()


# --- who may read ------------------------------------------------------------------------------


async def test_a_role_without_audit_read_is_refused_and_it_is_audited(rig: Rig) -> None:
    # The rig's caller is a DevOps Engineer: no audit:read.
    with rig.engine.connect() as connection:
        before = connection.execute(
            text("SELECT count(*) FROM audit_events WHERE action_type = 'AUTHZ_DENIED'")
        ).scalar_one()
    event = insert(rig.engine, "staging", uuid.uuid4())
    for path in (AUDIT, f"{AUDIT}/{event}"):
        response = await call(rig.client, "GET", path)
        assert response.status_code == 403, response.text
    with rig.engine.connect() as connection:
        after = connection.execute(
            text("SELECT count(*) FROM audit_events WHERE action_type = 'AUTHZ_DENIED'")
        ).scalar_one()
    assert after == before + 2
    assert queried(rig.engine, rig.caller) == []  # a refused read is a denial, not a query


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER"])
async def test_only_approvers_and_administrators_may_read_the_trail(rig: Rig, role: str) -> None:
    rig.as_role(role)
    assert (await listed(rig.client)).status_code == 403


@pytest.mark.parametrize("role", ["APPROVER", "ADMINISTRATOR"])
async def test_approvers_and_administrators_may(rig: Rig, role: str) -> None:
    rig.as_role(role)
    assert (await listed(rig.client, correlation_id=str(uuid.uuid4()))).status_code == 200


async def test_an_unauthenticated_caller_is_refused(rig: Rig) -> None:
    anonymous = httpx.AsyncClient(transport=rig.client._transport, base_url="http://testserver")
    try:
        assert (await anonymous.get(AUDIT)).status_code == 401
        assert (await anonymous.get(f"{AUDIT}/{uuid.uuid4()}")).status_code == 401
    finally:
        await anonymous.aclose()


# --- what comes back ---------------------------------------------------------------------------


async def test_an_operation_request_is_traceable_to_its_initiator(rig: Rig) -> None:
    asked = (await rig.ask()).json()
    rig.as_role("ADMINISTRATOR")

    response = await listed(rig.client, target_id=asked["operation_id"])
    assert response.status_code == 200, response.text
    [event] = [
        e for e in response.json()["items"] if e["action_type"] == "SERVICE_OPERATION_REQUESTED"
    ]
    assert event["actor_type"] == "USER" and event["actor_id"] == str(rig.caller)
    assert event["target_type"] == "operation" and event["environment"] == "staging"
    assert event["outcome"] == "SUCCESS" and event["correlation_id"] == asked["correlation_id"]
    assert set(event) == {
        "id", "occurred_at", "actor_type", "actor_id", "actor_label", "action_type",
        "target_type", "target_id", "environment", "correlation_id", "approval_id", "outcome",
        "source_ip", "user_agent", "sanitized_metadata",
    }  # fmt: skip
    one = await call(rig.client, "GET", f"{AUDIT}/{event['id']}")
    assert one.status_code == 200 and one.json() == event


async def test_metadata_is_redacted_again_on_the_way_out(admin: Rig) -> None:
    correlation = uuid.uuid4()
    with admin.engine.begin() as connection:  # written raw, as a row from before a rule improved
        connection.execute(
            text(
                "INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type,"
                " environment, correlation_id, outcome, sanitized_metadata)"
                " VALUES (:i, now(), 'SYSTEM', 'system:test', 'SCOPE_TEST', 'staging', :c,"
                " 'SUCCESS', CAST(:m AS jsonb))"
            ),
            {
                "i": (event := uuid.uuid4()),
                "c": correlation,
                "m": '{"note": "password=hunter2hunter2 Bearer abcdefghijklmnop", "ok": "kept"}',
            },
        )
    for response in (
        await listed(admin.client, correlation_id=str(correlation)),
        await call(admin.client, "GET", f"{AUDIT}/{event}"),
    ):
        assert response.status_code == 200
        assert "hunter2hunter2" not in response.text and "abcdefghijklmnop" not in response.text
        assert "[REDACTED]" in response.text and "kept" in response.text


# --- reading the trail is audited --------------------------------------------------------------


async def test_a_list_leaves_an_audit_query_row_holding_the_filters_and_counts_and_no_events(
    admin: Rig,
) -> None:
    correlation = uuid.uuid4()
    insert(admin.engine, "staging", correlation)
    insert(admin.engine, "staging", correlation)
    before = len(queried(admin.engine, admin.caller))

    response = await listed(
        admin.client, correlation_id=str(correlation), outcome="SUCCESS", limit=1
    )
    assert response.status_code == 200 and response.json()["page"]["has_more"] is True

    rows = queried(admin.engine, admin.caller)
    assert len(rows) == before + 1
    row = rows[-1]
    assert row["outcome"] == "SUCCESS" and row["target_type"] == "audit"
    assert row["environment"] is None and row["actor_label"]
    metadata = row["sanitized_metadata"]
    assert metadata["endpoint"] == "list"
    assert metadata["filters"] == {"correlation_id": str(correlation), "outcome": "SUCCESS"}
    assert (metadata["returned"], metadata["has_more"], metadata["limit"]) == (1, True, 1)
    assert metadata["from"] and metadata["to"]
    # Counts, never content: no event, no cursor.
    assert set(metadata) == {
        "endpoint",
        "filters",
        "from",
        "to",
        "order",
        "limit",
        "returned",
        "has_more",
    }
    assert "next_cursor" not in str(metadata)


async def test_an_empty_answer_is_still_a_read(admin: Rig) -> None:
    before = len(queried(admin.engine, admin.caller))
    response = await listed(admin.client, correlation_id=str(uuid.uuid4()))
    assert response.status_code == 200 and response.json()["items"] == []
    rows = queried(admin.engine, admin.caller)
    assert len(rows) == before + 1 and rows[-1]["sanitized_metadata"]["returned"] == 0


async def test_reading_one_event_is_audited_with_its_id(admin: Rig) -> None:
    event = insert(admin.engine, "staging", uuid.uuid4())
    before = len(queried(admin.engine, admin.caller))
    assert (await call(admin.client, "GET", f"{AUDIT}/{event}")).status_code == 200
    rows = queried(admin.engine, admin.caller)
    assert len(rows) == before + 1
    assert rows[-1]["sanitized_metadata"] == {"endpoint": "read", "event_id": event, "returned": 1}


async def test_the_audit_query_rows_are_themselves_in_the_trail(admin: Rig) -> None:
    await listed(admin.client, correlation_id=str(uuid.uuid4()))
    found = await listed(admin.client, action_type="AUDIT_QUERIED", actor_id=str(admin.caller))
    assert ids(found), "reading the trail must leave a trace that the trail can show"


async def test_a_failed_audit_write_fails_the_read_and_returns_no_events(
    admin: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SECURITY §18: nothing is handed back that was not recorded as handed back."""
    correlation = uuid.uuid4()
    insert(admin.engine, "staging", correlation)
    before = len(queried(admin.engine, admin.caller))

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the audit table is unavailable")

    monkeypatch.setattr("app.audit.read.audit.record", refuse)
    try:
        response = await listed(admin.client, correlation_id=str(correlation))
    except RuntimeError:  # the test transport re-raises server errors; either way, no page
        response = None
    if response is not None:
        assert response.status_code == 500 and "items" not in response.text
    assert len(queried(admin.engine, admin.caller)) == before


# --- scope -------------------------------------------------------------------------------------


async def test_a_staging_approver_sees_staging_events_only_and_not_a_login(
    admin: Rig, reader: Actor
) -> None:
    correlation = uuid.uuid4()
    staging = insert(admin.engine, "staging", correlation)
    production = insert(admin.engine, "production", correlation)
    login = insert(admin.engine, None, correlation, action="LOGIN_SUCCEEDED")  # no environment

    revoke_all(admin.engine, reader.id)
    grant(admin.engine, reader.id, "APPROVER", STAGING_SCOPE)
    assert ids(await listed(reader.client, correlation_id=str(correlation))) == [staging]
    for hidden in (production, login):
        # 404, never 403 (§2), and nothing was read, so nothing is audited as a query.
        before = len(queried(admin.engine, reader.id))
        assert (await call(reader.client, "GET", f"{AUDIT}/{hidden}")).status_code == 404
        assert len(queried(admin.engine, reader.id)) == before


async def test_a_production_only_approver_sees_production_and_not_events_with_no_environment(
    admin: Rig, reader: Actor
) -> None:
    correlation = uuid.uuid4()
    staging = insert(admin.engine, "staging", correlation)
    production = insert(admin.engine, "production", correlation)
    login = insert(admin.engine, None, correlation, action="LOGIN_SUCCEEDED")

    revoke_all(admin.engine, reader.id)
    grant(admin.engine, reader.id, "APPROVER", PRODUCTION_ONLY)
    assert ids(await listed(reader.client, correlation_id=str(correlation))) == [production]
    assert staging and login


async def test_the_bootstrap_administrator_sees_every_event_including_those_with_no_environment(
    admin: Rig,
) -> None:
    """The reason ``OPEN-109`` was amended while it was built: this grant names all four
    environments, so a rule of "a grant that constrains nothing" would have hidden these."""
    correlation = uuid.uuid4()
    with_none = insert(admin.engine, None, correlation, action="LOGIN_SUCCEEDED")
    others = [
        insert(admin.engine, env, correlation)
        for env in ("development", "staging", "production", "other")
    ]
    assert set(ids(await listed(admin.client, correlation_id=str(correlation)))) == {
        with_none,
        *others,
    }
    assert FULL_SCOPE  # the scope the rig grants


async def test_a_server_scoped_grant_reaches_no_audit_event(admin: Rig, reader: Actor) -> None:
    correlation = uuid.uuid4()
    event = insert(admin.engine, "staging", correlation)
    revoke_all(admin.engine, reader.id)
    grant(
        admin.engine,
        reader.id,
        "APPROVER",
        '{"environment":["staging"],"server":["' + admin.staging + '"]}',
    )
    assert ids(await listed(reader.client, correlation_id=str(correlation))) == []
    assert (await call(reader.client, "GET", f"{AUDIT}/{event}")).status_code == 404


async def test_a_filter_narrows_and_never_widens_scope(admin: Rig, reader: Actor) -> None:
    correlation = uuid.uuid4()
    insert(admin.engine, "production", correlation)
    revoke_all(admin.engine, reader.id)
    grant(admin.engine, reader.id, "APPROVER", STAGING_SCOPE)
    asked = await listed(reader.client, correlation_id=str(correlation), environment="production")
    assert ids(asked) == []


# --- windows -----------------------------------------------------------------------------------


async def test_the_default_window_is_seven_days_and_from_to_bound_occurred_at(admin: Rig) -> None:
    correlation = uuid.uuid4()
    now = insert(admin.engine, "staging", correlation)
    two_hours_ago = insert(admin.engine, "staging", correlation, seconds=2 * 3600)
    moment = datetime.now(UTC)

    assert set(ids(await listed(admin.client, correlation_id=str(correlation)))) == {
        now,
        two_hours_ago,
    }
    window = {
        "from": (moment - timedelta(hours=3)).isoformat(),
        "to": (moment - timedelta(hours=1)).isoformat(),
    }
    assert ids(await listed(admin.client, correlation_id=str(correlation), **window)) == [
        two_hours_ago
    ]


async def test_looking_beyond_seven_days_needs_both_ends(admin: Rig) -> None:
    now = datetime.now(UTC)
    alone = await listed(admin.client, **{"from": (now - timedelta(days=30)).isoformat()})
    assert alone.status_code == 422, alone.text
    assert alone.json()["error"]["details"]["fields"][0]["code"] == "range_requires_from_and_to"
    both = await listed(
        admin.client,
        correlation_id=str(uuid.uuid4()),
        **{"from": (now - timedelta(days=30)).isoformat(), "to": now.isoformat()},
    )
    assert both.status_code == 200, both.text


@pytest.mark.parametrize("which", ["ninety-one-days", "from-after-to", "no-offset"])
async def test_a_window_outside_the_rules_is_a_422(admin: Rig, which: str) -> None:
    now = datetime.now(UTC)
    params = {
        "ninety-one-days": {"from": (now - timedelta(days=91)).isoformat(), "to": now.isoformat()},
        "from-after-to": {"from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()},
        "no-offset": {"from": "2026-01-01T00:00:00", "to": "2026-01-02T00:00:00"},
    }[which]
    assert (await listed(admin.client, **params)).status_code == 422


# --- bad parameters ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"actor_type": "ROBOT"},
        {"outcome": "OK"},
        {"environment": "prod"},
        {"environment": "*"},
        {"target_type": "everything"},
        {"action_type": "login_failed"},
        {"action_type": "LOGIN%"},
        {"action_type": "X; DROP TABLE audit_events"},
        {"actor_id": "not-a-uuid"},
        {"correlation_id": "x"},
        {"order": "sideways"},
        {"limit": 0},
        {"limit": 201},
        {"cursor": "garbage"},
        {"q": "free text search does not exist"},
        {"metadata": "anything"},
        {"sort": "action_type"},
    ],
)
async def test_a_bad_parameter_is_a_422(admin: Rig, params: dict[str, Any]) -> None:
    assert (await listed(admin.client, **params)).status_code == 422


async def test_a_cursor_from_another_query_is_a_422(admin: Rig) -> None:
    correlation = uuid.uuid4()
    for _ in range(3):
        insert(admin.engine, "staging", correlation)
    first = await listed(admin.client, correlation_id=str(correlation), limit=1)
    cursor = first.json()["page"]["next_cursor"]
    assert cursor
    for changed in ({"outcome": "SUCCESS"}, {"order": "asc"}):
        again = await listed(
            admin.client, correlation_id=str(correlation), limit=1, cursor=cursor, **changed
        )
        assert again.status_code == 422


async def test_a_422_never_echoes_the_rejected_value(admin: Rig) -> None:
    response = await listed(admin.client, environment="prod-SECRET-VALUE")
    assert response.status_code == 422 and "SECRET-VALUE" not in response.text


# --- paging ------------------------------------------------------------------------------------


async def test_paging_over_the_wire_visits_every_event_once_and_audits_every_page(
    admin: Rig,
) -> None:
    correlation = uuid.uuid4()
    with admin.engine.begin() as connection:  # one transaction: the five share one occurred_at
        created = [str(audit_event(connection, "staging", correlation)) for _ in range(5)]
    before = len(queried(admin.engine, admin.caller))

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    for _ in range(10):
        page = await listed(admin.client, correlation_id=str(correlation), limit=2, cursor=cursor)
        assert page.status_code == 200, page.text
        pages += 1
        seen.extend(item["id"] for item in page.json()["items"])
        cursor = page.json()["page"]["next_cursor"]
        assert page.json()["page"]["total"] is None
        if not page.json()["page"]["has_more"]:
            break
    assert sorted(seen) == sorted(created) and len(seen) == len(set(seen))
    assert pages == 3
    assert len(queried(admin.engine, admin.caller)) == before + pages


# --- nothing can write, change or delete an event ----------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_no_route_under_audit_events_accepts_anything_but_a_get(
    admin: Rig, method: str
) -> None:
    event = insert(admin.engine, "staging", uuid.uuid4())
    for path in (AUDIT, f"{AUDIT}/{event}"):
        response = await call(admin.client, method, path, {"outcome": "SUCCESS"})
        assert response.status_code == 405, f"{method} {path}: {response.status_code}"
    with admin.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT outcome FROM audit_events WHERE id = :i"), {"i": event}
            ).scalar_one()
            == "SUCCESS"
        )
