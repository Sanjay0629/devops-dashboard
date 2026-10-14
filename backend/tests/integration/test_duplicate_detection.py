"""Duplicate provider resource ids, within their account (PRD §4.3 acceptance).

Its own file because it is an acceptance criterion in its own right, and
because the interesting cases are the ones where the duplicate is *not* a
duplicate: a different account, or a retired row whose identifier should be
reusable. A unique index that forbade those would be a different bug.

The partial predicate on ``deleted_at IS NULL`` is the mechanism for the last
one, and ``cloud_account_id`` being ``NOT NULL`` is the mechanism for the
first — PostgreSQL compares ``NULL``s as distinct in a unique index, so a
nullable account column would silently permit the duplicates the criterion
forbids (docs/DATABASE.md §4.2).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import Engine

from tests.integration.inventory_helpers import (
    SERVERS,
    account_id,
    call,
    grant,
    register,
    server_body,
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


@pytest.fixture
async def administrator(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> tuple[httpx.AsyncClient, uuid.UUID]:
    client, provider = auth_app
    await _login(client, provider)
    grant(migrated, user_id(migrated, provider.subject), "ADMINISTRATOR")
    return client, account_id(migrated)


async def test_a_duplicate_resource_id_in_the_same_account_is_409(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    """PRD §4.3 acceptance, verbatim."""
    client, account = administrator
    resource_id = f"host-{uuid.uuid4()}"
    first = await register(client, account, provider_resource_id=resource_id)
    assert first.status_code == 201, first.text

    second = await register(client, account, provider_resource_id=resource_id)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "duplicate_resource"


async def test_the_same_resource_id_in_a_different_account_is_allowed(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
    migrated: Engine,
) -> None:
    """ "Within their relevant account" — two providers may reuse an id."""
    client, account = administrator
    other = account_id(migrated, "OTHER")
    resource_id = f"host-{uuid.uuid4()}"

    first = await register(client, account, provider_resource_id=resource_id)
    assert first.status_code == 201, first.text
    second = await register(client, other, provider_resource_id=resource_id)
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]


async def test_a_resource_id_is_reusable_after_the_server_is_retired(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    """The unique index is partial on ``deleted_at IS NULL`` for this reason.

    A decommissioned instance id can legitimately be registered again — most
    obviously when the same host is re-registered after a mistake — and a
    total unique index would make a soft delete permanent in a way the
    operator never asked for.
    """
    client, account = administrator
    resource_id = f"host-{uuid.uuid4()}"
    first = await register(client, account, provider_resource_id=resource_id)
    assert first.status_code == 201, first.text

    retired = await call(client, "DELETE", f"{SERVERS}/{first.json()['id']}")
    assert retired.status_code == 204, retired.text

    again = await register(client, account, provider_resource_id=resource_id)
    assert again.status_code == 201, again.text
    assert again.json()["id"] != first.json()["id"]


async def test_an_idempotency_key_stays_claimed_after_the_server_is_retired(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    """The mirror image of the test above, and the reason the two unique
    indexes have different predicates.

    The *resource id* index is partial on ``deleted_at IS NULL`` so an id can
    be reused after a retire. The *idempotency* index deliberately is not: a
    client that retries the original create after the server was retired must
    get the original row back (§7), not a second registration. Together these
    two tests pin both halves, and `tests/test_db_units.py` asserts the index
    predicates that make them true.
    """
    client, account = administrator
    key = str(uuid.uuid4())
    body = server_body(account)
    first = await call(client, "POST", SERVERS, body, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text

    retired = await call(client, "DELETE", f"{SERVERS}/{first.json()['id']}")
    assert retired.status_code == 204, retired.text

    replay = await call(client, "POST", SERVERS, body, headers={"Idempotency-Key": key})
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["idempotent_replay"] is True


async def test_a_duplicate_application_slug_is_409(
    administrator: tuple[httpx.AsyncClient, uuid.UUID],
) -> None:
    """§18.2: ``slug`` is unique."""
    client, _account = administrator
    slug = f"app-{uuid.uuid4().hex[:10]}"
    body = {"name": f"First {slug}", "slug": slug}
    first = await call(
        client, "POST", "/api/v1/applications", body, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert first.status_code == 201, first.text

    second = await call(
        client,
        "POST",
        "/api/v1/applications",
        {"name": f"Second {slug}", "slug": slug},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "duplicate_resource"
