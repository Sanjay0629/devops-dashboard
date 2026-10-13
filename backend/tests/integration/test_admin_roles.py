"""RBAC enforcement and role administration, end to end.

Here rather than in ``tests/authz/`` (where IMPLEMENTATION_PLAN.md M1.4 lists
it) because every assertion in this file needs the real schema and the real
session store: the seeded role matrix, the ``user_roles`` unique constraint,
the ``AUTHZ_DENIED`` rows under the attribution ``CHECK``, Redis session
termination, and the durable read-only row. The collection hook in
``tests/integration/conftest.py`` marks everything in this directory
``integration``, which is also what keeps these out of the unit run.

What this covers, from docs/TESTING.md §8 and M1.4's acceptance criteria:

* each of the five roles against every privileged endpoint — the authorisation
  matrix, including the ``curl``-with-a-valid-session path (rule 5);
* grant → request → revoke → request, with no re-login and no restart (PRD
  §4.1);
* delegation escalation refused, last-Administrator revocation refused,
  self-disable refused (§25.4–§25.6);
* ``AUTHZ_DENIED`` audit rows actually written, attributed, and free of request
  content;
* fail-closed on a scope-resolution fault (rule 9);
* read-only mode blocking writes while reads keep working;
* the bootstrap CLI, including its refusal to run twice;
* the ``permissions`` array proven advisory (PROGRESS.md issue 36).

The authentication plumbing is reused verbatim from ``test_auth_flow.py`` —
same fake IdP, same fixtures — because logging in is not what is under test
here.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.integration.test_auth_flow import (  # noqa: F401 — fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)

ADMIN_USERS = "/api/v1/admin/users"
READ_ONLY_PATH = "/api/v1/admin/settings/read-only-mode"

#: Every privileged endpoint added by M1.4, with the permission it requires and
#: a body where the method needs one. Used to drive the role matrix.
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", ADMIN_USERS, None),
    ("GET", f"{ADMIN_USERS}/00000000-0000-0000-0000-000000000000", None),
    ("GET", "/api/v1/admin/roles", None),
    ("GET", "/api/v1/admin/permissions", None),
    (
        "POST",
        ADMIN_USERS,
        {
            "email": "nobody@example.invalid",
            "display_name": "Nobody",
            "identity_provider_subject": "subject-that-will-never-be-used",
        },
    ),
    ("PATCH", f"{ADMIN_USERS}/00000000-0000-0000-0000-000000000000", {"display_name": "x"}),
    (
        "POST",
        f"{ADMIN_USERS}/00000000-0000-0000-0000-000000000000/roles",
        {"role_name": "VIEWER", "scope_definition": {"environment": ["staging"]}},
    ),
    (
        "DELETE",
        f"{ADMIN_USERS}/00000000-0000-0000-0000-000000000000/roles/"
        "00000000-0000-0000-0000-000000000000",
        None,
    ),
    (
        "POST",
        f"{ADMIN_USERS}/00000000-0000-0000-0000-000000000000/sessions/terminate",
        None,
    ),
    ("POST", READ_ONLY_PATH, {"enabled": True, "reason": "test"}),
]

NON_ADMIN_ROLES = ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"]


# --- helpers -----------------------------------------------------------------


def _grant(
    engine: Engine,
    user_id: uuid.UUID,
    role: str,
    scope: str = '{"environment":["*"]}',
) -> uuid.UUID:
    """Insert a grant directly, for setting up a caller's authority."""
    with engine.begin() as connection:
        return connection.execute(  # type: ignore[no-any-return]
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_definition,"
                " granted_by, created_at, updated_at)"
                " SELECT gen_random_uuid(), :user_id, id, CAST(:scope AS jsonb),"
                " :user_id, now(), now() FROM roles WHERE name = :role"
                " RETURNING id"
            ),
            {"user_id": user_id, "role": role, "scope": scope},
        ).scalar_one()


def _revoke_all(engine: Engine, user_id: uuid.UUID) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": user_id})


def _user_id(engine: Engine, subject_value: str) -> uuid.UUID:
    with engine.connect() as connection:
        return connection.execute(  # type: ignore[no-any-return]
            text("SELECT id FROM users WHERE identity_provider_subject = :s"),
            {"s": subject_value},
        ).scalar_one()


def _create_other_user(engine: Engine, label: str) -> uuid.UUID:
    """A second user to administer, created directly rather than by logging in."""
    with engine.begin() as connection:
        return connection.execute(  # type: ignore[no-any-return]
            text(
                "INSERT INTO users (id, identity_provider_issuer,"
                " identity_provider_subject, email, display_name, status,"
                " created_at, updated_at)"
                " VALUES (gen_random_uuid(), 'https://idp.test.invalid', :subject,"
                " :email, 'Target User', 'ACTIVE', now(), now()) RETURNING id"
            ),
            {"subject": f"target-{label}", "email": f"target-{label}@example.invalid"},
        ).scalar_one()


def _denied_rows(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_id, actor_type, outcome, sanitized_metadata, target_type"
                " FROM audit_events WHERE action_type = 'AUTHZ_DENIED'"
                " ORDER BY occurred_at DESC"
            )
        ).mappings()
        return [dict(row) for row in rows]


def _audit_count(engine: Engine, action_type: str) -> int:
    with engine.connect() as connection:
        return connection.execute(  # type: ignore[no-any-return]
            text("SELECT count(*) FROM audit_events WHERE action_type = :a"),
            {"a": action_type},
        ).scalar_one()


async def _call(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    """Issue a request with the CSRF credentials a browser would send.

    The CSRF middleware runs before routing (assumption A-08), so a state
    changing call without these never reaches the permission dependency and
    would be a 403 for the wrong reason.
    """
    headers = {}
    if method != "GET":
        token = client.cookies.get("dcp_csrf")
        assert token is not None, "login should have set the CSRF cookie"
        headers = {"X-CSRF-Token": token, "Origin": "http://testserver"}
    return await client.request(method, path, json=body, headers=headers)


# --- the matrix --------------------------------------------------------------


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
async def test_non_administrators_are_refused_every_admin_endpoint(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    role: str,
) -> None:
    """docs/SECURITY.md §4 matrix: only ADMINISTRATOR holds admin:users/roles.

    A direct request with a valid session and an insufficient role is refused
    identically to the UI path, which is mandatory rule 5 — there is no UI
    involved here at all.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, role)
    try:
        for method, path, body in ENDPOINTS:
            response = await _call(client, method, path, body)
            assert response.status_code == 403, f"{role} {method} {path} -> {response.text}"
            assert response.json()["error"]["code"] == "forbidden"
    finally:
        _revoke_all(migrated, caller)


async def test_an_unauthenticated_request_is_401_not_403(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Identity before authority: no session cannot be a permission answer."""
    client, _provider = auth_app
    response = await client.get(ADMIN_USERS)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_an_administrator_is_permitted(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        listing = await client.get(ADMIN_USERS)
        assert listing.status_code == 200
        body = listing.json()
        assert body["page"]["total"] >= 1
        assert any(item["email"] == f"{provider.subject}@example.invalid" for item in body["items"])

        roles = await client.get("/api/v1/admin/roles")
        assert roles.status_code == 200
        names = {item["name"] for item in roles.json()["items"]}
        assert names == {"VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER", "ADMINISTRATOR"}

        catalogue = await client.get("/api/v1/admin/permissions")
        assert catalogue.status_code == 200
        permissions = {item["permission"] for item in catalogue.json()["items"]}
        # The two the contract names for §25, added by migration 0003.
        assert {"admin:users", "admin:roles"} <= permissions
    finally:
        _revoke_all(migrated, caller)


async def test_approver_and_administrator_permissions_match_the_matrix(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Separation of duties, as seeded: Administrator cannot approve.

    docs/SECURITY.md §4 keeps ``deployment:approve`` and ``operation:approve``
    away from ADMINISTRATOR deliberately (PRD §3.6, OPEN-10). Asserted through
    the API rather than against the migration, so a later seed change is
    caught.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        listing = (await client.get("/api/v1/admin/roles")).json()
        roles = {item["name"]: item for item in listing["items"]}
        administrator = {entry["permission"] for entry in roles["ADMINISTRATOR"]["permissions"]}
        approver = {entry["permission"] for entry in roles["APPROVER"]["permissions"]}
        viewer = {entry["permission"] for entry in roles["VIEWER"]["permissions"]}

        assert "deployment:approve" not in administrator
        assert "operation:approve" not in administrator
        assert {"deployment:approve", "operation:approve"} <= approver
        assert {"admin:users", "admin:roles"} <= administrator
        assert not any(permission.startswith("admin:") for permission in viewer)
        assert "server:write" not in viewer
    finally:
        _revoke_all(migrated, caller)


# --- revocation (PRD §4.1) ---------------------------------------------------


async def test_grant_then_revoke_takes_effect_on_the_next_request(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """The acceptance criterion: no re-login, no redeploy, no cache to clear."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)

    assert (await client.get(ADMIN_USERS)).status_code == 403
    grant_id = _grant(migrated, caller, "ADMINISTRATOR")
    assert (await client.get(ADMIN_USERS)).status_code == 200

    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM user_roles WHERE id = :id"), {"id": grant_id})
    assert (await client.get(ADMIN_USERS)).status_code == 403


async def test_an_expired_grant_does_not_authorise(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_definition,"
                " granted_by, expires_at, created_at, updated_at)"
                ' SELECT gen_random_uuid(), :id, id, \'{"environment":["*"]}\'::jsonb,'
                " :id, now() - interval '1 minute', now(), now()"
                " FROM roles WHERE name = 'ADMINISTRATOR'"
            ),
            {"id": caller},
        )
    try:
        assert (await client.get(ADMIN_USERS)).status_code == 403
    finally:
        _revoke_all(migrated, caller)


async def test_an_empty_scope_grants_nothing(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """docs/DATABASE.md §3.5, through the real endpoint rather than the matcher."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR", scope="{}")
    try:
        assert (await client.get(ADMIN_USERS)).status_code == 403
    finally:
        _revoke_all(migrated, caller)


async def test_a_malformed_scope_denies(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Fail closed on unreadable scope (rule 5), written by hand as one would be."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR", scope='{"team":["payments"]}')
    try:
        assert (await client.get(ADMIN_USERS)).status_code == 403
    finally:
        _revoke_all(migrated, caller)


async def test_resolution_failure_denies(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault injection into scope resolution ⇒ 403 (docs/TESTING.md §8 rule 9)."""
    from sqlalchemy.exc import OperationalError

    from app.authz import dependencies as authz_dependencies

    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")

    async def explode(*_args: object, **_kwargs: object) -> list[object]:
        raise OperationalError("SELECT 1", {}, Exception("injected"))

    monkeypatch.setattr(authz_dependencies.service, "resolve_grants", explode)
    try:
        response = await client.get(ADMIN_USERS)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
    finally:
        monkeypatch.undo()
        _revoke_all(migrated, caller)


# --- the advisory permissions array (PROGRESS.md issue 36) -------------------


async def test_the_permissions_array_is_advisory(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """An endpoint rejects an action the array implies.

    ``/auth/session`` lists permissions scope-blind, so a grant with an empty
    scope still advertises ``admin:users`` while the matcher refuses it. That
    is the whole claim behind "carries no authority": the array said yes and
    the server said 403.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR", scope="{}")
    try:
        session = (await client.get("/api/v1/auth/session")).json()
        assert "admin:users" in session["permissions"]
        assert (await client.get(ADMIN_USERS)).status_code == 403
    finally:
        _revoke_all(migrated, caller)


# --- role administration (§25.5, §25.6) --------------------------------------


async def test_granting_and_revoking_a_role_through_the_api(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    target = _create_other_user(migrated, f"grant-{uuid.uuid4().hex[:8]}")
    granted_before = _audit_count(migrated, "ROLE_GRANTED")
    try:
        created = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {"role_name": "DEVOPS_ENGINEER", "scope_definition": {"environment": ["staging"]}},
        )
        assert created.status_code == 201, created.text
        grant = created.json()
        assert grant["role_name"] == "DEVOPS_ENGINEER"
        assert grant["scope_definition"] == {"environment": ["staging"]}
        assert grant["granted_by"] == str(caller)
        assert _audit_count(migrated, "ROLE_GRANTED") == granted_before + 1

        detail = (await client.get(f"{ADMIN_USERS}/{target}")).json()
        assert [row["role_name"] for row in detail["roles"]] == ["DEVOPS_ENGINEER"]

        duplicate = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {"role_name": "DEVOPS_ENGINEER", "scope_definition": {"environment": ["staging"]}},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "duplicate_resource"

        revoked_before = _audit_count(migrated, "ROLE_REVOKED")
        removed = await _call(client, "DELETE", f"{ADMIN_USERS}/{target}/roles/{grant['id']}")
        assert removed.status_code == 204
        assert _audit_count(migrated, "ROLE_REVOKED") == revoked_before + 1
        assert (await client.get(f"{ADMIN_USERS}/{target}")).json()["roles"] == []
    finally:
        _revoke_all(migrated, target)
        _revoke_all(migrated, caller)


async def test_granting_production_scope_requires_holding_it(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """No privilege escalation through delegation (§25.5)."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    # Administrator, but only in staging.
    _grant(migrated, caller, "ADMINISTRATOR", scope='{"environment":["staging"]}')
    target = _create_other_user(migrated, f"escalate-{uuid.uuid4().hex[:8]}")
    denied_before = len(_denied_rows(migrated))
    try:
        attempt = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {"role_name": "DEVOPS_ENGINEER", "scope_definition": {"environment": ["production"]}},
        )
        assert attempt.status_code == 422
        assert attempt.json()["error"]["code"] == "not_permitted"
        # And the attempt is in the trail, not just in the response.
        rows = _denied_rows(migrated)
        assert len(rows) == denied_before + 1
        assert rows[0]["sanitized_metadata"]["reason"] == "delegation_escalation"
        assert rows[0]["actor_id"] == caller

        # Non-production delegation from the same caller still works.
        allowed = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {"role_name": "DEVOPS_ENGINEER", "scope_definition": {"environment": ["staging"]}},
        )
        assert allowed.status_code == 201
    finally:
        _revoke_all(migrated, target)
        _revoke_all(migrated, caller)


async def test_an_administrator_holding_production_may_delegate_it(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR", scope='{"environment":["production","staging"]}')
    target = _create_other_user(migrated, f"delegate-{uuid.uuid4().hex[:8]}")
    try:
        created = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {"role_name": "APPROVER", "scope_definition": {"environment": ["production"]}},
        )
        assert created.status_code == 201, created.text
    finally:
        _revoke_all(migrated, target)
        _revoke_all(migrated, caller)


async def test_the_last_administrator_grant_cannot_be_revoked(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25.6 — the one refusal that stops the estate locking itself out."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    grant_id = _grant(migrated, caller, "ADMINISTRATOR")
    try:
        response = await _call(client, "DELETE", f"{ADMIN_USERS}/{caller}/roles/{grant_id}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

        # With a second administrator present, the same revocation is allowed.
        second = _create_other_user(migrated, f"second-admin-{uuid.uuid4().hex[:8]}")
        _grant(migrated, second, "ADMINISTRATOR")
        try:
            assert (
                await _call(client, "DELETE", f"{ADMIN_USERS}/{caller}/roles/{grant_id}")
            ).status_code == 204
        finally:
            _revoke_all(migrated, second)
    finally:
        _revoke_all(migrated, caller)


async def test_an_expired_administrator_grant_does_not_count_as_the_last_one(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """A lapsed grant administers nothing, so it cannot protect a revocation."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    grant_id = _grant(migrated, caller, "ADMINISTRATOR")
    other = _create_other_user(migrated, f"lapsed-{uuid.uuid4().hex[:8]}")
    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_definition,"
                " granted_by, expires_at, created_at, updated_at)"
                ' SELECT gen_random_uuid(), :other, id, \'{"environment":["*"]}\'::jsonb,'
                " :other, now() - interval '1 day', now(), now()"
                " FROM roles WHERE name = 'ADMINISTRATOR'"
            ),
            {"other": other},
        )
    try:
        response = await _call(client, "DELETE", f"{ADMIN_USERS}/{caller}/roles/{grant_id}")
        assert response.status_code == 409
    finally:
        _revoke_all(migrated, other)
        _revoke_all(migrated, caller)


# --- user administration (§25.2, §25.4, §25.7) -------------------------------


async def test_pre_creating_a_user_then_granting_before_first_login(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25.2. The subject is supplied, because the column is NOT NULL (OPEN-26)."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    label = uuid.uuid4().hex[:8]
    created_before = _audit_count(migrated, "USER_CREATED")
    try:
        created = await _call(
            client,
            "POST",
            ADMIN_USERS,
            {
                "email": f"precreated-{label}@example.invalid",
                "display_name": "Pre-created Operator",
                "identity_provider_subject": f"precreated-subject-{label}",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["roles"] == []
        assert body["status"] == "ACTIVE"
        assert _audit_count(migrated, "USER_CREATED") == created_before + 1

        duplicate = await _call(
            client,
            "POST",
            ADMIN_USERS,
            {
                "email": f"precreated-{label}@example.invalid",
                "display_name": "Pre-created Operator",
                "identity_provider_subject": f"precreated-subject-{label}",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "duplicate_resource"

        grant = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{body['id']}/roles",
            {"role_name": "VIEWER", "scope_definition": {"environment": ["development"]}},
        )
        assert grant.status_code == 201
        _revoke_all(migrated, uuid.UUID(body["id"]))
    finally:
        _revoke_all(migrated, caller)


async def test_no_endpoint_accepts_or_returns_a_password(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25: no endpoint sets, resets or returns a password — none exists."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        rejected = await _call(
            client,
            "POST",
            ADMIN_USERS,
            {
                "email": "with-password@example.invalid",
                "display_name": "Nope",
                "identity_provider_subject": "nope",
                "password": "placeholder",
            },
        )
        assert rejected.status_code == 422
        codes = {field["code"] for field in rejected.json()["error"]["details"]["fields"]}
        assert "unknown_field" in codes
        # And the rejected value is not echoed back.
        assert "placeholder" not in rejected.text

        listing = await client.get(ADMIN_USERS)
        assert "password" not in listing.text.lower()
    finally:
        _revoke_all(migrated, caller)


async def test_disabling_a_user_terminates_sessions_and_audits(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25.4, with the session store actually checked."""
    from app.auth import session as session_store

    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    target = _create_other_user(migrated, f"disable-{uuid.uuid4().hex[:8]}")
    victim_session, _record = await session_store.create(target)
    disabled_before = _audit_count(migrated, "USER_DISABLED")
    try:
        assert await session_store.load(victim_session) is not None
        response = await _call(client, "PATCH", f"{ADMIN_USERS}/{target}", {"status": "DISABLED"})
        assert response.status_code == 200
        assert response.json()["status"] == "DISABLED"
        assert await session_store.load(victim_session) is None
        assert _audit_count(migrated, "USER_DISABLED") == disabled_before + 1

        enabled_before = _audit_count(migrated, "USER_ENABLED")
        again = await _call(client, "PATCH", f"{ADMIN_USERS}/{target}", {"status": "ACTIVE"})
        assert again.status_code == 200
        assert _audit_count(migrated, "USER_ENABLED") == enabled_before + 1
    finally:
        _revoke_all(migrated, caller)


async def test_an_administrator_cannot_disable_themselves(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25.4 — a self-inflicted outage, refused with 422 not_permitted."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        response = await _call(client, "PATCH", f"{ADMIN_USERS}/{caller}", {"status": "DISABLED"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_permitted"
        assert (await client.get(f"{ADMIN_USERS}/{caller}")).json()["status"] == "ACTIVE"
    finally:
        _revoke_all(migrated, caller)


async def test_terminating_another_users_sessions(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25.7, a containment step that must stay available and audited."""
    from app.auth import session as session_store

    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    target = _create_other_user(migrated, f"terminate-{uuid.uuid4().hex[:8]}")
    first, _ = await session_store.create(target)
    second, _ = await session_store.create(target)
    before = _audit_count(migrated, "SESSION_TERMINATED")
    try:
        response = await _call(client, "POST", f"{ADMIN_USERS}/{target}/sessions/terminate")
        assert response.status_code == 200
        assert response.json()["terminated"] == 2
        assert await session_store.load(first) is None
        assert await session_store.load(second) is None
        assert _audit_count(migrated, "SESSION_TERMINATED") == before + 1
        # No session identifier in the audit row (docs/SECURITY.md §5).
        with migrated.connect() as connection:
            metadata = connection.execute(
                text(
                    "SELECT sanitized_metadata FROM audit_events"
                    " WHERE action_type = 'SESSION_TERMINATED'"
                    " ORDER BY occurred_at DESC LIMIT 1"
                )
            ).scalar_one()
        assert metadata == {"sessions_terminated": 2}
    finally:
        _revoke_all(migrated, caller)


async def test_an_unknown_user_is_404(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        response = await client.get(f"{ADMIN_USERS}/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    finally:
        _revoke_all(migrated, caller)


async def test_scope_references_must_exist(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§25.5: "referenced ids must exist"."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    target = _create_other_user(migrated, f"scope-ref-{uuid.uuid4().hex[:8]}")
    denied_before = len(_denied_rows(migrated))
    try:
        response = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {
                "role_name": "VIEWER",
                "scope_definition": {"server": [str(uuid.uuid4())]},
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_permitted"
        # And it is *not* audited as an escalation attempt: an unknown id is a
        # typo, and an audit row calling it an escalation would be a false
        # record of what happened.
        assert len(_denied_rows(migrated)) == denied_before
    finally:
        _revoke_all(migrated, target)
        _revoke_all(migrated, caller)


async def test_scope_validation_rejects_nonsense(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Validation at the trust boundary, with ``extra="forbid"`` (CLAUDE.md §5)."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    target = _create_other_user(migrated, f"bad-scope-{uuid.uuid4().hex[:8]}")
    bodies: list[dict[str, Any]] = [
        {"role_name": "SUPERUSER", "scope_definition": {"environment": ["staging"]}},
        {"role_name": "VIEWER", "scope_definition": {"team": ["payments"]}},
        {"role_name": "VIEWER", "scope_definition": {"environment": ["prod"]}},
        {"role_name": "VIEWER", "scope_definition": {"server": ["not-a-uuid"]}},
        {"role_name": "VIEWER", "scope_definition": {"environment": []}},
        {
            "role_name": "VIEWER",
            "scope_definition": {"server": ["*", str(uuid.uuid4())]},
        },
        {
            "role_name": "VIEWER",
            "scope_definition": {"environment": ["staging"]},
            "expires_at": "2020-01-01T00:00:00Z",
        },
    ]
    try:
        for body in bodies:
            response = await _call(client, "POST", f"{ADMIN_USERS}/{target}/roles", body)
            assert response.status_code == 422, f"{body} -> {response.status_code}"
    finally:
        _revoke_all(migrated, target)
        _revoke_all(migrated, caller)


# --- AUTHZ_DENIED audit ------------------------------------------------------


async def test_a_denial_is_audited_and_attributed(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Mandatory rule 10 plus docs/API_CONTRACTS.md §2: 403 + AUTHZ_DENIED."""
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    before = len(_denied_rows(migrated))

    assert (await client.get(ADMIN_USERS)).status_code == 403

    rows = _denied_rows(migrated)
    assert len(rows) == before + 1
    row = rows[0]
    assert row["actor_type"] == "USER"
    assert row["actor_id"] == caller
    assert row["outcome"] == "DENIED"
    assert row["target_type"] == "system"
    assert row["sanitized_metadata"] == {
        "permission": "admin:users",
        "reason": "no_matching_grant",
        "method": "GET",
        "path": ADMIN_USERS,
    }


# --- read-only mode (docs/SECURITY.md §27) -----------------------------------


async def test_read_only_mode_blocks_writes_and_leaves_reads_working(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    target = _create_other_user(migrated, f"read-only-{uuid.uuid4().hex[:8]}")
    changed_before = _audit_count(migrated, "READ_ONLY_MODE_CHANGED")
    try:
        enabled = await _call(
            client, "POST", READ_ONLY_PATH, {"enabled": True, "reason": "integration test"}
        )
        assert enabled.status_code == 200
        assert enabled.json() == {"read_only_mode": True}
        assert _audit_count(migrated, "READ_ONLY_MODE_CHANGED") == changed_before + 1

        # Reads keep working — that is the point of the mode.
        assert (await client.get(ADMIN_USERS)).status_code == 200
        session = (await client.get("/api/v1/auth/session")).json()
        assert session["read_only_mode"] is True

        # Writes are refused with 423.
        blocked = await _call(
            client,
            "POST",
            f"{ADMIN_USERS}/{target}/roles",
            {"role_name": "VIEWER", "scope_definition": {"environment": ["staging"]}},
        )
        assert blocked.status_code == 423
        assert blocked.json()["error"]["code"] == "read_only_mode"

        # Logging out still works: containment must not trap the operator.
        assert (await _call(client, "POST", "/api/v1/auth/logout")).status_code == 200
        await _login(client, provider)

        # And the switch itself still works, which is why it is exempt.
        restored = await _call(client, "POST", READ_ONLY_PATH, {"enabled": False})
        assert restored.status_code == 200
        assert restored.json() == {"read_only_mode": False}
        assert (
            await _call(
                client,
                "POST",
                f"{ADMIN_USERS}/{target}/roles",
                {"role_name": "VIEWER", "scope_definition": {"environment": ["staging"]}},
            )
        ).status_code == 201
    finally:
        with migrated.begin() as connection:
            connection.execute(
                text(
                    "UPDATE system_settings SET value = 'false'::jsonb WHERE key = 'read_only_mode'"
                )
            )
        _revoke_all(migrated, target)
        _revoke_all(migrated, caller)


async def test_read_only_mode_survives_a_restart(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Durable by design: the row is in PostgreSQL, not Redis or the process.

    Asserted by reading the row back directly, which is what a restarted
    process would do.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        await _call(client, "POST", READ_ONLY_PATH, {"enabled": True, "reason": "durability"})
        with migrated.connect() as connection:
            row = connection.execute(
                text("SELECT value, updated_by FROM system_settings WHERE key = 'read_only_mode'")
            ).one()
        assert row[0] is True
        assert row[1] == caller
    finally:
        with migrated.begin() as connection:
            connection.execute(
                text(
                    "UPDATE system_settings SET value = 'false'::jsonb WHERE key = 'read_only_mode'"
                )
            )
        _revoke_all(migrated, caller)


async def test_enabling_read_only_mode_requires_a_reason(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    _grant(migrated, caller, "ADMINISTRATOR")
    try:
        response = await _call(client, "POST", READ_ONLY_PATH, {"enabled": True})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_permitted"
    finally:
        _revoke_all(migrated, caller)


# --- the bootstrap CLI (PROGRESS.md issue 37) --------------------------------


async def test_bootstrap_grants_the_first_administrator_once(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """The way out of "nobody can grant anything" — and only once."""
    from app.admin import bootstrap as bootstrap_module
    from app.db.session import get_session_factory

    client, provider = auth_app
    await _login(client, provider)
    caller = _user_id(migrated, provider.subject)
    email = f"{provider.subject}@example.invalid"
    granted_before = _audit_count(migrated, "ROLE_GRANTED")

    factory = get_session_factory()
    try:
        async with factory() as db:
            grant_id = await bootstrap_module.bootstrap(db, email)
        assert grant_id is not None
        assert _audit_count(migrated, "ROLE_GRANTED") == granted_before + 1

        # The grant works immediately, through the API, with no re-login.
        assert (await client.get(ADMIN_USERS)).status_code == 200

        # Production is named explicitly, because "*" never implies it.
        with migrated.connect() as connection:
            scope = connection.execute(
                text("SELECT scope_definition FROM user_roles WHERE id = :id"),
                {"id": grant_id},
            ).scalar_one()
        assert "production" in scope["environment"]

        # Attributed to the named system actor, not to the granted user.
        with migrated.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT actor_type, actor_id, actor_label, sanitized_metadata"
                        " FROM audit_events WHERE action_type = 'ROLE_GRANTED'"
                        " ORDER BY occurred_at DESC LIMIT 1"
                    )
                )
                .mappings()
                .one()
            )
        assert row["actor_type"] == "SYSTEM"
        assert row["actor_id"] is None
        assert row["actor_label"] == "system:bootstrap"
        assert row["sanitized_metadata"]["bootstrap"] is True

        # Refused a second time: it cannot mint another administrator.
        async with factory() as db:
            with pytest.raises(bootstrap_module.BootstrapRefusedError) as refusal:
                await bootstrap_module.bootstrap(db, email)
        assert refusal.value.code == bootstrap_module.EXIT_ALREADY_BOOTSTRAPPED
    finally:
        _revoke_all(migrated, caller)


async def test_bootstrap_refuses_an_unknown_address(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """It grants to an existing user, so it cannot invent an identity."""
    from app.admin import bootstrap as bootstrap_module
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        with pytest.raises(bootstrap_module.BootstrapRefusedError) as refusal:
            await bootstrap_module.bootstrap(db, f"nobody-{uuid.uuid4().hex}@example.invalid")
    assert refusal.value.code == bootstrap_module.EXIT_NO_SUCH_USER
