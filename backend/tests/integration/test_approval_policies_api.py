"""The Administrator's approval-policy editor, endpoints 25.11 – 25.14 (M4.5, ``OPEN-105``).

Real PostgreSQL and Redis through the real application. What matters here is who may use it
(``admin:policies``, Administrator only), that every change leaves an ``APPROVAL_POLICY_CHANGED``
audit row with the before and after, and that the editor cannot save what the workflow would not
honour (more than one approval) or remove what something still points at.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.integration.inventory_helpers import audit_rows, call, grant, revoke_all, user_id
from tests.integration.test_auth_flow import (  # noqa: F401 -- fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)

PATH = "/api/v1/admin/approval-policies"
SEEDED = "production-service-operations"


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": f"test-policy-{uuid.uuid4().hex[:10]}",
        "environment": "staging",
        "applies_to": "SERVICE_OPERATION",
        "required_approvals": 1,
        "allow_self_approval": False,
        "required_role": "APPROVER",
        "approval_window_seconds": 900,
    }
    body.update(overrides)
    return body


@pytest.fixture
async def admin(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    redis_url: str,  # noqa: F811
) -> AsyncIterator[tuple[httpx.AsyncClient, uuid.UUID, Engine]]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    yield client, caller, migrated
    revoke_all(migrated, caller)
    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM approvals WHERE requested_by = :u"), {"u": caller})
        connection.execute(text("DELETE FROM approval_policies WHERE name LIKE 'test-policy-%'"))


Admin = tuple[httpx.AsyncClient, uuid.UUID, Engine]


def _changes(engine: Engine, policy_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in audit_rows(engine, "APPROVAL_POLICY_CHANGED")
        if str(row["target_id"]) == policy_id
    ]


async def _create(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await call(client, "POST", PATH, _body(**overrides))
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# --- reading and creating ---------------------------------------------------------------------


async def test_the_list_shows_the_seeded_policies(admin: Admin) -> None:
    client, _, _ = admin
    response = await call(client, "GET", PATH)
    assert response.status_code == 200, response.text
    by_name = {item["name"]: item for item in response.json()["items"]}
    seeded = by_name[SEEDED]
    assert seeded["environment"] == "production" and seeded["applies_to"] == "SERVICE_OPERATION"
    assert seeded["required_approvals"] == 1 and seeded["allow_self_approval"] is False
    assert seeded["required_role"] == "APPROVER" and seeded["approval_window_seconds"] == 3600
    assert "production-deployments" in by_name


async def test_creating_a_policy_is_audited_with_its_fields(admin: Admin) -> None:
    client, caller, engine = admin
    created = await _create(client, allow_self_approval=True)
    assert created["allow_self_approval"] is True and created["approval_window_seconds"] == 900
    [event] = _changes(engine, created["policy_id"])
    assert event["actor_id"] == caller and event["outcome"] == "SUCCESS"
    metadata = event["sanitized_metadata"]
    assert metadata["change"] == "created" and metadata["before"] is None
    assert metadata["after"]["name"] == created["name"]
    assert metadata["after"]["allow_self_approval"] is True
    listed = await call(client, "GET", PATH)
    assert created["policy_id"] in {item["policy_id"] for item in listed.json()["items"]}


async def test_a_duplicate_name_is_a_409(admin: Admin) -> None:
    client, _, _ = admin
    body = _body()
    assert (await call(client, "POST", PATH, body)).status_code == 201
    again = await call(client, "POST", PATH, body)
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate_resource"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"required_approvals": 2}, "required_approvals"),  # would be refused at request time
        ({"required_approvals": 0}, "required_approvals"),
        ({"approval_window_seconds": 59}, "approval_window_seconds"),
        ({"approval_window_seconds": 86_401}, "approval_window_seconds"),
        ({"required_role": "NO_SUCH_ROLE"}, "required_role"),
        ({"required_role": "approver"}, "required_role"),
        ({"environment": "prod"}, "environment"),
        ({"applies_to": "SERVER"}, "applies_to"),
        ({"name": ""}, "name"),
        ({"name": "bad/name"}, "name"),
        ({"allow_self_approval": "yes please"}, "allow_self_approval"),
        ({"unknown_field": 1}, "unknown_field"),
    ],
)
async def test_what_the_workflow_would_not_honour_cannot_be_saved(
    admin: Admin, change: dict[str, Any], field: str
) -> None:
    client, _, engine = admin
    body = _body(**change)
    response = await call(client, "POST", PATH, body)
    assert response.status_code == 422, response.text
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM approval_policies WHERE name = :n"), {"n": body["name"]}
        ).scalar_one()
    assert count == 0


# --- editing ----------------------------------------------------------------------------------


async def test_editing_changes_only_the_named_fields_and_audits_before_and_after(
    admin: Admin,
) -> None:
    client, caller, engine = admin
    created = await _create(client)
    policy_id = created["policy_id"]

    response = await call(
        client,
        "PATCH",
        f"{PATH}/{policy_id}",
        {"allow_self_approval": True, "approval_window_seconds": 120},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["allow_self_approval"] is True and edited["approval_window_seconds"] == 120
    assert edited["name"] == created["name"] and edited["environment"] == "staging"
    assert edited["required_role"] == "APPROVER"

    updated = [
        e for e in _changes(engine, policy_id) if e["sanitized_metadata"]["change"] == "updated"
    ]
    [event] = updated
    assert event["actor_id"] == caller
    assert event["sanitized_metadata"]["before"]["allow_self_approval"] is False
    assert event["sanitized_metadata"]["after"]["allow_self_approval"] is True
    assert event["sanitized_metadata"]["before"]["approval_window_seconds"] == 900
    assert event["sanitized_metadata"]["after"]["approval_window_seconds"] == 120


async def test_a_change_that_changes_nothing_writes_no_audit_row(admin: Admin) -> None:
    client, _, engine = admin
    created = await _create(client)
    response = await call(
        client, "PATCH", f"{PATH}/{created['policy_id']}", {"environment": "staging"}
    )
    assert response.status_code == 200
    assert len(_changes(engine, created["policy_id"])) == 1  # only the creation


@pytest.mark.parametrize(
    "change",
    [
        {"applies_to": "DEPLOYMENT"},  # fixed at creation
        {"required_approvals": 2},
        {"required_role": "NO_SUCH_ROLE"},
        {"approval_window_seconds": None},  # an explicit null has no meaning
        {"name": None},
        {"surprise": True},
    ],
)
async def test_an_edit_the_editor_does_not_allow_is_422_and_changes_nothing(
    admin: Admin, change: dict[str, Any]
) -> None:
    client, _, engine = admin
    created = await _create(client)
    response = await call(client, "PATCH", f"{PATH}/{created['policy_id']}", change)
    assert response.status_code == 422, response.text
    assert len(_changes(engine, created["policy_id"])) == 1


async def test_renaming_onto_an_existing_name_is_a_409_and_changes_nothing(admin: Admin) -> None:
    client, _, engine = admin
    first, second = await _create(client), await _create(client)
    response = await call(client, "PATCH", f"{PATH}/{second['policy_id']}", {"name": first["name"]})
    assert response.status_code == 409, response.text
    assert len(_changes(engine, second["policy_id"])) == 1
    listed = await call(client, "GET", PATH)
    names = {i["policy_id"]: i["name"] for i in listed.json()["items"]}
    assert names[second["policy_id"]] == second["name"]


async def test_an_unknown_policy_is_404(admin: Admin) -> None:
    client, _, _ = admin
    missing = f"{PATH}/{uuid.uuid4()}"
    assert (await call(client, "PATCH", missing, {"allow_self_approval": True})).status_code == 404
    assert (await call(client, "DELETE", missing)).status_code == 404


# --- deleting ---------------------------------------------------------------------------------


async def test_deleting_an_unreferenced_policy_is_audited(admin: Admin) -> None:
    client, caller, engine = admin
    created = await _create(client)
    response = await call(client, "DELETE", f"{PATH}/{created['policy_id']}")
    assert response.status_code == 204, response.text
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM approval_policies WHERE id = :i"),
                {"i": created["policy_id"]},
            ).scalar_one()
            == 0
        )
    deleted = [
        e
        for e in _changes(engine, created["policy_id"])
        if e["sanitized_metadata"]["change"] == "deleted"
    ]
    [event] = deleted
    assert event["actor_id"] == caller and event["sanitized_metadata"]["after"] is None
    assert event["sanitized_metadata"]["before"]["name"] == created["name"]


async def test_a_policy_a_service_still_links_cannot_be_deleted(admin: Admin) -> None:
    client, _, engine = admin
    listed = await call(client, "GET", PATH)
    seeded = next(i for i in listed.json()["items"] if i["name"] == SEEDED)
    response = await call(client, "DELETE", f"{PATH}/{seeded['policy_id']}")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "policy_in_use"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM approval_policies WHERE name = :n"), {"n": SEEDED}
            ).scalar_one()
            == 1
        )


async def test_a_policy_an_approval_request_references_cannot_be_deleted(admin: Admin) -> None:
    client, caller, engine = admin
    created = await _create(client)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approvals (id, operation_type, operation_id, approval_policy_id,"
                " requested_by, decision, expires_at) VALUES (gen_random_uuid(),"
                " 'SERVICE_OPERATION', gen_random_uuid(), :p, :u, 'PENDING',"
                " now() + interval '1 hour')"
            ),
            {"p": created["policy_id"], "u": caller},
        )
    response = await call(client, "DELETE", f"{PATH}/{created['policy_id']}")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "policy_in_use"


# --- who may ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"])
async def test_nobody_but_an_administrator_may_edit_policies(admin: Admin, role: str) -> None:
    client, caller, engine = admin
    created = await _create(client)  # made while still an Administrator
    revoke_all(engine, caller)
    grant(engine, caller, role)
    policy = f"{PATH}/{created['policy_id']}"
    for method, path, body in (
        ("GET", PATH, None),
        ("POST", PATH, _body()),
        ("PATCH", policy, {"allow_self_approval": True}),
        ("DELETE", policy, None),
    ):
        response = await call(client, method, path, body)
        assert response.status_code == 403, (method, response.text)
    # And nothing moved.
    revoke_all(engine, caller)
    grant(engine, caller, "ADMINISTRATOR")
    listed = await call(client, "GET", PATH)
    unchanged = next(i for i in listed.json()["items"] if i["policy_id"] == created["policy_id"])
    assert unchanged["allow_self_approval"] is False
    assert len(_changes(engine, created["policy_id"])) == 1


async def test_the_editor_is_refused_in_read_only_mode(admin: Admin) -> None:
    client, _, engine = admin
    created = await _create(client)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE system_settings SET value = 'true'::jsonb WHERE key = 'read_only_mode'")
        )
    try:
        for method, path, body in (
            ("POST", PATH, _body()),
            ("PATCH", f"{PATH}/{created['policy_id']}", {"allow_self_approval": True}),
            ("DELETE", f"{PATH}/{created['policy_id']}", None),
        ):
            response = await call(client, method, path, body)
            assert response.status_code == 423, (method, response.text)
        assert (await call(client, "GET", PATH)).status_code == 200  # reads keep working
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE system_settings SET value = 'false'::jsonb WHERE key = 'read_only_mode'"
                )
            )
