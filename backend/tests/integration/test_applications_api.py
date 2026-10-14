"""Application endpoints end to end (§18.1–§18.5).

The permission split is the point of most of this file. §18 gives reads to
``server:read`` — which every role holds — and writes to
``admin:integrations``, which migration 0004 renames from the seeded
``integration:manage`` and which only ADMINISTRATOR holds. These are the first
endpoints to require it, so this is where the rename either worked or did not.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.integration.inventory_helpers import (
    APPLICATIONS,
    application_body,
    audit_rows,
    call,
    grant,
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


async def _create(client: httpx.AsyncClient, **overrides: object) -> httpx.Response:
    return await call(
        client,
        "POST",
        APPLICATIONS,
        application_body(**overrides),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


@pytest.fixture
async def administrator(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> tuple[httpx.AsyncClient, uuid.UUID]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    grant(migrated, caller, "ADMINISTRATOR")
    return client, caller


def test_the_permission_was_renamed_by_migration_0004(migrated: Engine) -> None:
    """PROGRESS.md issue 39: the catalogue now matches the contracts.

    Asserted against the real catalogue rather than the migration source: the
    revision is an ``UPDATE`` keyed on the old name, so "it ran" and "the row
    is right" are different claims.
    """
    with migrated.connect() as connection:
        names = {
            f"{row[0]}:{row[1]}"
            for row in connection.execute(
                text("SELECT resource_type, action FROM permissions")
            ).all()
        }
        granted = {
            f"{row[0]}:{row[1]}"
            for row in connection.execute(
                text(
                    "SELECT p.resource_type, p.action FROM role_permissions rp"
                    " JOIN roles r ON r.id = rp.role_id"
                    " JOIN permissions p ON p.id = rp.permission_id"
                    " WHERE r.name = 'ADMINISTRATOR'"
                )
            ).all()
        }
    assert "admin:integrations" in names
    assert "integration:manage" not in names
    # The UPDATE kept the permission id, so the role's grant followed it.
    assert "admin:integrations" in granted


async def test_register_read_and_list_an_application(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    client, _caller = administrator
    created = await _create(client, owner_team="payments")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "ACTIVE"
    assert body["idempotent_replay"] is False
    # No deployment targets exist until M5, and "none registered" is not the
    # same claim as "this product cannot tell you yet".
    assert body["deployment_targets"]["data_state"] == "not_configured"

    detail = await call(client, "GET", f"{APPLICATIONS}/{body['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["servers"] == []

    listed = await call(client, "GET", APPLICATIONS, params={"q": body["slug"]})
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]


async def test_registration_is_audited(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
    migrated: Engine,
) -> None:
    client, caller = administrator
    created = await _create(client)
    assert created.status_code == 201, created.text
    rows = [
        row
        for row in audit_rows(migrated, "APPLICATION_REGISTERED")
        if str(row["target_id"]) == created.json()["id"]
    ]
    assert len(rows) == 1
    assert rows[0]["actor_id"] == caller
    assert rows[0]["target_type"] == "application"


async def test_an_idempotent_replay_returns_the_original(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    client, _caller = administrator
    key = str(uuid.uuid4())
    body = application_body()
    first = await call(client, "POST", APPLICATIONS, body, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    second = await call(client, "POST", APPLICATIONS, body, headers={"Idempotency-Key": key})
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotent_replay"] is True


async def test_metadata_is_editable_and_audited(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
    migrated: Engine,
) -> None:
    client, _caller = administrator
    created = (await _create(client)).json()
    response = await call(
        client,
        "PATCH",
        f"{APPLICATIONS}/{created['id']}",
        {"description": "Now documented.", "owner_team": "platform"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "Now documented."
    assert any(
        str(row["target_id"]) == created["id"]
        for row in audit_rows(migrated, "APPLICATION_UPDATED")
    )


async def test_archiving_removes_it_from_reads_and_is_audited(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
    migrated: Engine,
) -> None:
    client, _caller = administrator
    created = (await _create(client)).json()
    response = await call(client, "DELETE", f"{APPLICATIONS}/{created['id']}")
    assert response.status_code == 204, response.text

    assert (await call(client, "GET", f"{APPLICATIONS}/{created['id']}")).status_code == 404
    listed = await call(client, "GET", APPLICATIONS, params={"q": created["slug"]})
    assert listed.json()["items"] == []
    assert any(
        str(row["target_id"]) == created["id"]
        for row in audit_rows(migrated, "APPLICATION_ARCHIVED")
    )


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"])
async def test_a_non_administrator_can_read_but_not_write(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    role: str,
) -> None:
    """§18: reads are ``server:read``, writes are ``admin:integrations``."""
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    grant(migrated, caller, role)
    try:
        assert (await call(client, "GET", APPLICATIONS)).status_code == 200

        created = await _create(client)
        assert created.status_code == 403, created.text
        assert created.json()["error"]["code"] == "forbidden"

        patched = await call(client, "PATCH", f"{APPLICATIONS}/{uuid.uuid4()}", {"owner_team": "x"})
        assert patched.status_code == 403, patched.text

        deleted = await call(client, "DELETE", f"{APPLICATIONS}/{uuid.uuid4()}")
        assert deleted.status_code == 403, deleted.text
    finally:
        revoke_all(migrated, caller)


async def test_a_health_check_url_off_the_allowlist_is_refused(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    """docs/SECURITY.md §19: destinations are registered, validated at write time.

    No ``DCP_SSRF_HOST_ALLOWLIST`` is configured for these tests, so every
    host is refused — the fail-closed default, asserted rather than assumed.
    """
    client, _caller = administrator
    response = await call(
        client,
        "POST",
        APPLICATIONS,
        application_body(
            health_check_config={
                "checks": [
                    {
                        "name": "metadata",
                        "url": "http://169.254.169.254/latest/meta-data",
                        "environment": "production",
                    }
                ]
            }
        ),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text
    locations = [field["loc"] for field in response.json()["error"]["details"]["fields"]]
    assert ["body", "health_check_config", "checks", "0", "url"] in locations


async def test_free_text_search_is_not_a_wildcard_injection(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    """§6: ``q`` is parameterised and its own wildcards are literal.

    A bare ``%`` must match the literal character, not every row — otherwise
    the filter silently stops filtering, which on a scoped collection is worse
    than an error.
    """
    client, _caller = administrator
    created = await _create(client)
    assert created.status_code == 201, created.text

    for term in ("%", "_", "%%", "' OR 1=1 --"):
        response = await call(client, "GET", APPLICATIONS, params={"q": term})
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [], f"q={term!r} matched rows"
