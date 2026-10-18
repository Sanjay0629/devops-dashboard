"""Shared setup for the inventory integration suites.

Not a conftest: these are plain helpers, and importing them by name keeps it
obvious in each test file where the grant, the account or the request headers
came from. The authentication fixtures themselves are re-exported from
``test_auth_flow`` exactly as ``test_admin_roles`` does, because logging in is
not what any of these files is testing.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import Engine, text

SERVERS = "/api/v1/servers"
APPLICATIONS = "/api/v1/applications"

#: Reaches everything: the shape the bootstrap CLI grants.
FULL_SCOPE = (
    '{"environment":["development","staging","production","other"],'
    '"cloud_account":["*"],"server":["*"],"application":["*"]}'
)
#: Non-production only, and no explicit production entry — so rule 4 applies.
STAGING_SCOPE = '{"environment":["staging"],"cloud_account":["*"],"server":["*"]}'


def grant(engine: Engine, user_id: uuid.UUID, role: str, scope: str = FULL_SCOPE) -> uuid.UUID:
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


def retire_servers(engine: Engine, server_ids: list[str]) -> None:
    """Soft-delete servers and their services, so a test does not leave them behind.

    The schema is rebuilt once per session, not per test, and several assertions are
    about *every* readable server or service: a log search by environment refuses past
    200 servers, and a dashboard count of unobserved units counts every service row. A
    test that registers servers must therefore take them away again.
    """
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE services SET deleted_at = now() WHERE server_id = ANY(:ids)"),
            {"ids": [uuid.UUID(i) for i in server_ids]},
        )
        connection.execute(
            text("UPDATE servers SET deleted_at = now() WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(i) for i in server_ids]},
        )


def revoke_all(engine: Engine, user_id: uuid.UUID) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": user_id})


def user_id(engine: Engine, subject: str) -> uuid.UUID:
    with engine.connect() as connection:
        return connection.execute(  # type: ignore[no-any-return]
            text("SELECT id FROM users WHERE identity_provider_subject = :s"),
            {"s": subject},
        ).scalar_one()


def account_id(engine: Engine, provider: str = "ON_PREM") -> uuid.UUID:
    """The cloud account migration 0002 seeds, or one created for this provider."""
    with engine.begin() as connection:
        existing = connection.execute(
            text(
                "SELECT id FROM cloud_accounts WHERE provider = :p AND deleted_at IS NULL LIMIT 1"
            ),
            {"p": provider},
        ).scalar_one_or_none()
        if existing is not None:
            return existing  # type: ignore[no-any-return]
        return connection.execute(  # type: ignore[no-any-return]
            text(
                "INSERT INTO cloud_accounts (id, provider, account_reference, display_name,"
                " region_configuration, status, created_at, updated_at)"
                " VALUES (gen_random_uuid(), :p, :ref, :name, '{}'::jsonb, 'ACTIVE',"
                " now(), now()) RETURNING id"
            ),
            {"p": provider, "ref": f"test-{provider}-{uuid.uuid4()}", "name": f"Test {provider}"},
        ).scalar_one()


def disable_account(engine: Engine, identifier: uuid.UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE cloud_accounts SET status = 'DISABLED' WHERE id = :id"),
            {"id": identifier},
        )


def audit_rows(engine: Engine, action_type: str) -> list[dict[str, Any]]:
    """Every audit row of one action type.

    Callers narrow this to their own row by ``target_id`` and must keep doing
    so: ``occurred_at`` defaults to ``now()``, the *transaction* timestamp
    rather than the insert timestamp, so the order here is not insertion order
    and the newest-looking row is not reliably the caller's own (PROGRESS.md
    issue 51). ``id`` (uuid7) only breaks an exact tie.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_id, actor_label, outcome, target_type, target_id,"
                " environment, sanitized_metadata"
                " FROM audit_events WHERE action_type = :a"
                " ORDER BY occurred_at DESC, id DESC"
            ),
            {"a": action_type},
        ).mappings()
        return [dict(row) for row in rows]


def server_body(account: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """A valid §14.2 body against an ``ON_PREM`` account.

    ``ON_PREM`` because its ``provider_resource_id`` pattern is the permissive
    one; the provider-specific patterns get their own test.
    """
    body: dict[str, Any] = {
        "cloud_account_id": str(account),
        "provider_resource_id": f"host-{uuid.uuid4()}",
        "hostname": f"h{uuid.uuid4().hex[:10]}.internal.example",
        "display_name": "Test Server",
        "operating_system": "LINUX",
        "environment": "staging",
        "management_method": "RESTRICTED_AGENT",
    }
    body.update(overrides)
    return body


def application_body(**overrides: Any) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    body: dict[str, Any] = {"name": f"App {suffix}", "slug": f"app-{suffix}"}
    body.update(overrides)
    return body


async def call(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """A request carrying the CSRF credentials a browser would send.

    The CSRF middleware runs before routing, so a state-changing call without
    these never reaches the permission dependency and would be a ``403`` for
    entirely the wrong reason.
    """
    sent = dict(headers or {})
    if method != "GET":
        token = client.cookies.get("dcp_csrf")
        assert token is not None, "login should have set the CSRF cookie"
        sent.setdefault("X-CSRF-Token", token)
        sent.setdefault("Origin", "http://testserver")
    return await client.request(method, path, json=body, headers=sent, params=params)


async def register(
    client: httpx.AsyncClient,
    account: uuid.UUID,
    *,
    key: str | None = None,
    **overrides: Any,
) -> httpx.Response:
    """``POST /servers`` with a fresh Idempotency-Key unless one is given."""
    return await call(
        client,
        "POST",
        SERVERS,
        server_body(account, **overrides),
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )
