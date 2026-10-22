"""Endpoints 23.1 and 23.2 end to end (M4.6, ``OPEN-107`` / ``OPEN-108``).

Real PostgreSQL and Redis through the real application. The pure parts (allowlists, the cursor's
pin, the keyset's NULL rule) are ``tests/operations/test_history_query.py``; scope against the
matcher and paging against an oracle are ``test_history_and_audit_scope.py``. Here: the wire --
who sees what, what a row carries, what a filter can and cannot do, and the errors.

The suite's database is shared and other suites commit operations, so every list below is narrowed
to the rig's own servers (``server_id``), which are new for each test.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.integration.inventory_helpers import STAGING_SCOPE, call, grant, revoke_all
from tests.integration.test_approvals_api import Actor, _second_login, approver, waiting  # noqa: F401
from tests.integration.test_auth_flow import FakeIdp, auth_app  # noqa: F401
from tests.integration.test_operations_api import (  # noqa: F401 -- fixtures used by name
    Rig,
    adapter,
    idp,
    idp_key,
    redis_url,
    rig,
    subject,
)

OPERATIONS = "/api/v1/operations"
#: Keys a list row must not carry: they are §17.2's, to keep list payloads small and the redaction
#: surface narrow (API_CONTRACTS §23).
DETAIL_ONLY = {"requested_reason", "sanitized_result", "parent_operation_id", "poll_after_seconds"}


async def listed(
    client: httpx.AsyncClient, path: str = OPERATIONS, **params: Any
) -> httpx.Response:
    return await call(
        client, "GET", path, params={k: v for k, v in params.items() if v is not None}
    )


def ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["operation_id"] for item in response.json()["items"]]


def set_times(rig: Rig, operation: str, *, created_minutes_ago: int, completed: bool) -> None:
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE operations SET created_at = now() - :m * interval '1 minute',"
                " completed_at = CASE WHEN :done THEN now() - :m * interval '1 minute' END"
                " WHERE id = :i"
            ),
            {"m": created_minutes_ago, "done": completed, "i": operation},
        )


@pytest.fixture
async def reader(rig: Rig, auth_app: tuple[httpx.AsyncClient, FakeIdp]) -> AsyncIterator[Actor]:
    """A second person who reads operations everywhere (``APPROVER``) and requested none."""
    actor = await _second_login(rig, auth_app[1])
    grant(rig.engine, actor.id, "APPROVER")
    yield actor
    revoke_all(rig.engine, actor.id)
    await actor.client.aclose()


# --- what a row carries ------------------------------------------------------------------------


async def test_a_row_carries_the_result_and_the_execution_id_and_not_the_detail(rig: Rig) -> None:
    asked = (await rig.ask()).json()
    operation = asked["operation_id"]
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE operations SET execution_status = 'SUCCEEDED', started_at = now(),"
                " completed_at = now(), observed_state_before = 'INACTIVE',"
                " observed_state_after = 'ACTIVE', external_execution_id = 'cmd-0001',"
                " requested_reason = 'the reason', sanitized_result = '{\"steps\": [\"x\"]}'"
                " WHERE id = :i"
            ),
            {"i": operation},
        )

    response = await listed(rig.client, server_id=rig.staging)
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["operation_id"] == operation
    assert item["execution_status"] == "SUCCEEDED" and item["approval_status"] == "NOT_REQUIRED"
    assert item["external_execution_id"] == "cmd-0001"
    assert (item["observed_state_before"], item["observed_state_after"]) == ("INACTIVE", "ACTIVE")
    assert item["target"] == {
        "server_id": rig.staging,
        "server_display_name": "Staging",
        "service_name": "web.service",
        "environment": "staging",
    }
    assert item["requested_by"]["id"] == str(rig.caller)
    assert item["approval"] is None
    assert item["started_at"] and item["completed_at"] and item["deadline_at"]
    assert item["correlation_id"] == asked["correlation_id"]
    assert DETAIL_ONLY.isdisjoint(item), "the list must not carry the detail endpoint's fields"
    assert response.json()["page"] == {
        "limit": 50,
        "next_cursor": None,
        "has_more": False,
        "total": None,
    }


async def test_an_operation_that_needed_an_approval_names_the_approval_and_who_decided(
    rig: Rig, approver: Actor
) -> None:
    body = await waiting(rig)
    pending = (await listed(rig.client, server_id=rig.production)).json()["items"]
    assert pending[0]["approval"]["decision"] == "PENDING"
    assert pending[0]["approval"]["approved_by"] is None

    decided = await call(
        approver.client, "POST", f"/api/v1/operations/{body['operation_id']}/approve", None
    )
    assert decided.status_code == 200, decided.text
    [item] = (await listed(rig.client, server_id=rig.production)).json()["items"]
    assert item["approval_status"] == "APPROVED"
    assert item["approval"]["decision"] == "APPROVED"
    assert item["approval"]["approved_by"]["id"] == str(approver.id)
    assert item["approval"]["decided_at"]


# --- whose rows --------------------------------------------------------------------------------


async def test_a_reader_sees_others_operations_inside_their_scope_only(
    rig: Rig, reader: Actor
) -> None:
    staging = rig.foreign_operation(rig.staging, "web.service", status="FAILED")
    production = rig.foreign_operation(rig.production, "api.service", status="FAILED")

    revoke_all(rig.engine, reader.id)
    grant(rig.engine, reader.id, "APPROVER", STAGING_SCOPE)
    got = await listed(reader.client, server_id=rig.staging)
    assert ids(got) == [staging]
    hidden = await listed(reader.client, server_id=rig.production)
    assert ids(hidden) == []  # not an error and not a count: the rows simply are not there
    assert hidden.json()["page"]["has_more"] is False

    revoke_all(rig.engine, reader.id)
    grant(rig.engine, reader.id, "APPROVER")  # production named
    assert ids(await listed(reader.client, server_id=rig.production)) == [production]


async def test_a_requester_keeps_sight_of_their_own_request_after_their_scope_shrinks(
    rig: Rig,
) -> None:
    body = await waiting(rig)  # the caller's own production request
    mine = body["operation_id"]
    rig.as_role("VIEWER", STAGING_SCOPE)  # may no longer read production at all

    assert ids(await listed(rig.client, server_id=rig.production)) == [mine]
    assert (await call(rig.client, "GET", f"{OPERATIONS}/{mine}")).status_code == 200
    # But not other people's production operations.
    rig.foreign_operation(rig.production, "api.service", status="FAILED")
    assert ids(await listed(rig.client, server_id=rig.production)) == [mine]


async def test_the_list_and_the_detail_agree_after_a_server_is_relabelled(
    rig: Rig, reader: Actor
) -> None:
    """Issue 164: the stored environment decides both, not the server's current label."""
    operation = rig.foreign_operation(rig.production, "api.service", status="FAILED")
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE servers SET environment = 'staging' WHERE id = :i"), {"i": rig.production}
        )
    try:
        revoke_all(rig.engine, reader.id)
        grant(rig.engine, reader.id, "APPROVER", STAGING_SCOPE)
        assert ids(await listed(reader.client, server_id=rig.production)) == []
        assert (await call(reader.client, "GET", f"{OPERATIONS}/{operation}")).status_code == 404

        revoke_all(rig.engine, reader.id)
        grant(rig.engine, reader.id, "APPROVER")  # production named
        assert ids(await listed(reader.client, server_id=rig.production)) == [operation]
        assert (await call(reader.client, "GET", f"{OPERATIONS}/{operation}")).status_code == 200
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text("UPDATE servers SET environment = 'production' WHERE id = :i"),
                {"i": rig.production},
            )


async def test_requested_by_narrows_and_never_widens(rig: Rig, reader: Actor) -> None:
    mine = (await rig.ask()).json()["operation_id"]
    theirs = rig.foreign_operation(rig.staging, "restart.service", status="FAILED")
    stranger = str(uuid.uuid4())

    # A DevOps Engineer reads operations in scope, so sees both; `requested_by` picks one.
    assert set(ids(await listed(rig.client, server_id=rig.staging))) == {mine, theirs}
    assert ids(await listed(rig.client, server_id=rig.staging, requested_by=stranger)) == []
    assert ids(await listed(rig.client, server_id=rig.staging, requested_by=str(rig.caller))) == [
        mine
    ]
    # The reader sees both, and `requested_by` picks one.
    both = await listed(reader.client, server_id=rig.staging)
    assert set(ids(both)) == {mine, theirs}
    only_me = await listed(reader.client, server_id=rig.staging, requested_by=str(rig.caller))
    assert ids(only_me) == [mine]


async def test_a_caller_with_no_operation_read_anywhere_is_refused(rig: Rig) -> None:
    revoke_all(rig.engine, rig.caller)
    response = await listed(rig.client)
    assert response.status_code == 403, response.text


async def test_an_unauthenticated_caller_is_refused(rig: Rig) -> None:
    anonymous = httpx.AsyncClient(transport=rig.client._transport, base_url="http://testserver")
    try:
        assert (await anonymous.get(OPERATIONS)).status_code == 401
        assert (await anonymous.get(f"/api/v1/servers/{rig.staging}/operations")).status_code == 401
    finally:
        await anonymous.aclose()


# --- filters -----------------------------------------------------------------------------------


async def test_every_filter_narrows(rig: Rig, reader: Actor) -> None:
    web = rig.foreign_operation(rig.staging, "web.service", status="FAILED")
    restart = rig.foreign_operation(rig.staging, "restart.service", status="CANCELLED")
    api = rig.foreign_operation(rig.production, "api.service", status="TIMED_OUT")
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE operations SET action_type = 'stop' WHERE id = :i"), {"i": web}
        )
    web_unit = rig.service_id(rig.staging, "web.service")

    async def ask(**params: Any) -> set[str]:
        return set(ids(await listed(reader.client, **params)))

    both = {"server_id": rig.staging}
    assert await ask(**both) == {web, restart}
    assert await ask(**both, execution_status="FAILED") == {web}
    assert await ask(**both, execution_status=["FAILED", "CANCELLED"]) == {web, restart}
    assert await ask(**both, action_type="stop") == {web}
    assert await ask(**both, action_type="restart") == {restart}
    assert await ask(**both, service_id=web_unit) == {web}
    assert await ask(**both, environment="production") == set()  # stored environment: staging
    assert await ask(server_id=rig.production, environment="production") == {api}
    assert await ask(server_id=rig.production, execution_status="FAILED") == set()


@pytest.mark.parametrize(
    "params",
    [
        {"execution_status": "DONE"},
        {"execution_status": "VERIFYING"},  # never stored (OPEN-69)
        {"action_type": "RESTART"},
        {"environment": "prod"},
        {"environment": "*"},
        {"server_id": "not-a-uuid"},
        {"requested_by": "x"},
        {"sort": "status"},
        {"sort": "created_at; DROP TABLE operations"},
        {"order": "sideways"},
        {"limit": 0},
        {"limit": 201},  # §5: more than the maximum is a 422, never a clamp
        {"cursor": "not-a-cursor"},
        {"enviroment": "production"},  # a typo that must not look like a working filter (§6)
        {"q": "anything"},
    ],
)
async def test_a_bad_parameter_is_a_422_and_never_a_silent_default(
    rig: Rig, params: dict[str, Any]
) -> None:
    response = await listed(rig.client, **params)
    assert response.status_code == 422, response.text


async def test_a_422_names_the_field_and_not_the_value(rig: Rig) -> None:
    response = await listed(rig.client, environment="prod-SECRET-VALUE")
    assert response.status_code == 422
    assert "SECRET-VALUE" not in response.text


# --- windows -----------------------------------------------------------------------------------


def iso(moment: datetime) -> str:
    return moment.isoformat()


async def test_the_default_window_is_seven_days_and_from_to_bound_created_at(rig: Rig) -> None:
    recent = rig.foreign_operation(rig.staging, "web.service", status="FAILED")
    old = rig.foreign_operation(rig.staging, "web.service", status="FAILED")
    set_times(rig, old, created_minutes_ago=8 * 24 * 60, completed=True)  # eight days ago
    rig.as_role("ADMINISTRATOR")  # widen the caller; the rows below are another person's

    assert ids(await listed(rig.client, server_id=rig.staging)) == [recent]  # default: 7 days
    now = datetime.now(UTC)
    wide = await listed(
        rig.client, server_id=rig.staging, **{"from": iso(now - timedelta(days=30)), "to": iso(now)}
    )
    assert set(ids(wide)) == {recent, old}
    # `from` / `to` bound created_at whichever way the page is sorted.
    by_completion = await listed(
        rig.client,
        server_id=rig.staging,
        sort="completed_at",
        **{"from": iso(now - timedelta(days=30)), "to": iso(now)},
    )
    assert set(ids(by_completion)) == {recent, old}


@pytest.mark.parametrize(
    "span",
    [
        {"from": "ninety-one-days"},
        {"from": "after-to"},
        {"from": "naive"},
    ],
)
async def test_a_window_outside_the_rules_is_a_422(rig: Rig, span: dict[str, str]) -> None:
    now = datetime.now(UTC)
    params: dict[str, str]
    match span["from"]:
        case "ninety-one-days":
            params = {"from": iso(now - timedelta(days=91)), "to": iso(now)}
        case "after-to":
            params = {"from": iso(now), "to": iso(now - timedelta(hours=1))}
        case _:
            params = {"from": "2026-01-01T00:00:00"}  # no offset
    response = await listed(rig.client, **params)
    assert response.status_code == 422, response.text


async def test_ninety_days_exactly_is_allowed(rig: Rig) -> None:
    now = datetime.now(UTC)
    response = await listed(rig.client, **{"from": iso(now - timedelta(days=90)), "to": iso(now)})
    assert response.status_code == 200, response.text


# --- paging ------------------------------------------------------------------------------------


@pytest.mark.parametrize("sort", ["created_at", "completed_at"])
@pytest.mark.parametrize("order", ["desc", "asc"])
async def test_paging_over_the_wire_visits_every_row_once(
    rig: Rig, reader: Actor, sort: str, order: str
) -> None:
    created = []
    for index in range(5):
        operation = rig.foreign_operation(rig.staging, "web.service", status="FAILED")
        unfinished = index % 2 == 1
        if unfinished:  # a non-terminal status, on its own unit (one in flight per target)
            with rig.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE operations SET execution_status = 'AWAITING_APPROVAL',"
                        " completed_at = NULL, target_service_id = (SELECT id FROM services"
                        " WHERE server_id = :s AND service_name = :n) WHERE id = :i"
                    ),
                    {
                        "s": rig.staging,
                        "n": ["restart.service", "ro.service"][index // 2],
                        "i": operation,
                    },
                )
        set_times(rig, operation, created_minutes_ago=index, completed=not unfinished)
        created.append(operation)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = await listed(
            reader.client, server_id=rig.staging, sort=sort, order=order, limit=2, cursor=cursor
        )
        assert page.status_code == 200, page.text
        body = page.json()
        seen.extend(item["operation_id"] for item in body["items"])
        assert body["page"]["total"] is None
        cursor = body["page"]["next_cursor"]
        if not body["page"]["has_more"]:
            assert cursor is None
            break
    assert sorted(seen) == sorted(created) and len(seen) == len(set(seen))
    if sort == "completed_at":  # unfinished rows last, in either direction
        with rig.engine.connect() as connection:
            finished = {
                str(row.id): row.completed_at is not None
                for row in connection.execute(
                    text("SELECT id, completed_at FROM operations WHERE id = ANY(:ids)"),
                    {"ids": [uuid.UUID(i) for i in seen]},
                )
            }
        flags = [finished[i] for i in seen]
        assert flags == sorted(flags, reverse=True), "unfinished operations must come last"


async def test_a_cursor_from_another_query_is_a_422(rig: Rig) -> None:
    for _ in range(3):
        rig.foreign_operation(rig.staging, "web.service", status="FAILED")
    rig.as_role("ADMINISTRATOR")
    first = await listed(rig.client, server_id=rig.staging, limit=1)
    cursor = first.json()["page"]["next_cursor"]
    assert cursor
    replayed = await listed(rig.client, server_id=rig.staging, limit=1, cursor=cursor, order="asc")
    assert replayed.status_code == 422
    other_filter = await listed(
        rig.client, server_id=rig.staging, limit=1, cursor=cursor, execution_status="FAILED"
    )
    assert other_filter.status_code == 422


# --- 23.2 --------------------------------------------------------------------------------------


async def test_the_per_server_history_is_the_same_query_with_the_server_fixed(
    rig: Rig, reader: Actor
) -> None:
    staging = rig.foreign_operation(rig.staging, "web.service", status="FAILED")
    production = rig.foreign_operation(rig.production, "api.service", status="FAILED")
    path = f"/api/v1/servers/{rig.staging}/operations"
    assert ids(await listed(reader.client, path)) == [staging]
    assert ids(await listed(reader.client, f"/api/v1/servers/{rig.production}/operations")) == [
        production
    ]
    # 23.2 fixes the server in the path, so the query parameter is not accepted there.
    assert (await listed(reader.client, path, server_id=rig.production)).status_code == 422


async def test_a_server_the_caller_may_not_read_is_a_404_not_an_empty_list(
    rig: Rig, reader: Actor
) -> None:
    rig.foreign_operation(rig.production, "api.service", status="FAILED")
    revoke_all(rig.engine, reader.id)
    grant(rig.engine, reader.id, "APPROVER", STAGING_SCOPE)
    refused = await listed(reader.client, f"/api/v1/servers/{rig.production}/operations")
    assert refused.status_code == 404, refused.text
    unknown = await listed(reader.client, f"/api/v1/servers/{uuid.uuid4()}/operations")
    assert unknown.status_code == 404
    assert refused.json()["error"]["code"] == unknown.json()["error"]["code"] == "not_found"
