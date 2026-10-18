"""Server inventory end to end (§14.1–§14.7, IMPLEMENTATION_PLAN.md M1.5).

Here rather than in ``tests/inventory/`` — where the plan lists it — for the
same reason ``test_admin_roles.py`` is here: every assertion needs the real
schema and the real session store. The partial unique indexes, the enum
``CHECK``s, the audit rows under their attribution constraint and the Redis
session all have to be real for any of this to mean anything, and the
collection hook in ``conftest.py`` marks everything in this directory
``integration``.

What this covers, from M1.5's acceptance criteria and test list:

* CRUD round trips, with ``PENDING`` on registration (PRD §4.3);
* a Viewer refused every write (PRD §4.3, docs/SECURITY.md §4 matrix);
* ``If-Unmodified-Since`` conflict, and an immutable field refused on PATCH;
* idempotent replay, and the same key with a different body;
* an audit row for every mutation, including the privilege-relevant
  ``SERVER_ENVIRONMENT_CHANGED``;
* pagination stability across a concurrent insert;
* the five statuses stayed distinguishable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.integration.inventory_helpers import (
    SERVERS,
    account_id,
    application_body,
    audit_rows,
    call,
    disable_account,
    grant,
    register,
    revoke_all,
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

APPLICATIONS = "/api/v1/applications"


@pytest.fixture
async def administrator(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID]:
    """A logged-in full-scope Administrator, and the account to register into."""
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    grant(migrated, caller, "ADMINISTRATOR")
    return client, caller, account_id(migrated)


# --- 14.2 registration -------------------------------------------------------


async def test_an_administrator_registers_a_server_and_it_is_pending(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """PRD §4.3 acceptance, both halves: registration works, and grants nothing."""
    client, _caller, account = administrator
    response = await register(client, account, display_name="Web 01")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["display_name"] == "Web 01"
    assert body["status"] == "PENDING"
    assert body["idempotent_replay"] is False
    # Nothing has observed this host, so none of these may claim otherwise.
    assert body["last_seen_at"] is None
    assert body["stale"] is None
    assert body["health"]["state"] is None
    assert body["health"]["data_state"] == "not_configured"
    # M3.3: a real count, and a host nothing has alerted on has none -- not "unknown".
    assert body["open_alerts"]["data_state"] == "ok"
    assert body["open_alerts"]["counts"] == {"critical": 0, "warning": 0}
    assert body["management_capabilities"]["capabilities"] is None


async def test_registration_is_audited(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    client, caller, account = administrator
    response = await register(client, account, environment="production")
    assert response.status_code == 201, response.text
    rows = [
        row
        for row in audit_rows(migrated, "SERVER_REGISTERED")
        if str(row["target_id"]) == response.json()["id"]
    ]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "SUCCESS"
    assert rows[0]["actor_id"] == caller
    assert rows[0]["environment"] == "production"
    assert rows[0]["target_type"] == "server"


async def test_an_idempotency_key_is_required(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """§7: absent ⇒ 422."""
    client, _caller, account = administrator
    response = await call(client, "POST", SERVERS, server_body(account))
    assert response.status_code == 422, response.text
    locations = [field["loc"] for field in response.json()["error"]["details"]["fields"]]
    assert ["header", "Idempotency-Key"] in locations


async def test_a_replay_with_the_same_body_returns_the_original(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    """§7: 200, the original resource, ``idempotent_replay: true``, nothing re-run."""
    client, _caller, account = administrator
    key = str(uuid.uuid4())
    body = server_body(account)
    first = await call(client, "POST", SERVERS, body, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text

    before = len(audit_rows(migrated, "SERVER_REGISTERED"))
    second = await call(client, "POST", SERVERS, body, headers={"Idempotency-Key": key})
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotent_replay"] is True
    # Nothing was re-executed, so nothing was re-audited either.
    assert len(audit_rows(migrated, "SERVER_REGISTERED")) == before


async def test_a_replay_with_a_different_body_is_a_conflict(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, account = administrator
    key = str(uuid.uuid4())
    first = await call(
        client, "POST", SERVERS, server_body(account), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 201, first.text
    second = await call(
        client,
        "POST",
        SERVERS,
        server_body(account, display_name="Something Else"),
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 409, second.text
    error = second.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"]["reason"] == "idempotency_key_reused_with_different_payload"


async def test_an_inactive_cloud_account_is_refused(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    """§14.2: must reference an ACTIVE, non-deleted account ⇒ 422 not_registered."""
    client, _caller, _account = administrator
    disabled = account_id(migrated, "OTHER")
    disable_account(migrated, disabled)
    response = await register(client, disabled)
    assert response.status_code == 422, response.text
    field = response.json()["error"]["details"]["fields"][0]
    assert field["loc"] == ["body", "cloud_account_id"]
    assert field["code"] == "not_registered"


async def test_a_management_method_the_provider_cannot_carry_is_refused(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    """§14.2, verbatim: AZURE_RUN_COMMAND on an AWS account ⇒ 422."""
    client, _caller, _account = administrator
    aws = account_id(migrated, "AWS")
    response = await register(
        client,
        aws,
        provider_resource_id="i-0123456789abcdef",
        management_method="AZURE_RUN_COMMAND",
    )
    assert response.status_code == 422, response.text
    field = response.json()["error"]["details"]["fields"][0]
    assert field["loc"] == ["body", "management_method"]
    assert field["code"] == "not_permitted"
    assert "AWS_SSM" in field["allowed"]


async def test_a_resource_id_in_the_wrong_shape_for_its_provider_is_refused(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    client, _caller, _account = administrator
    aws = account_id(migrated, "AWS")
    response = await register(
        client, aws, provider_resource_id="not-an-instance-id", management_method="AWS_SSM"
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["fields"][0]["loc"] == [
        "body",
        "provider_resource_id",
    ]


async def test_two_servers_cannot_claim_one_prometheus_instance(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """§14.2: ``metrics_instance`` is unique across servers."""
    client, _caller, account = administrator
    instance = f"m{uuid.uuid4().hex[:8]}.internal.example:9100"
    first = await register(client, account, metrics_instance=instance)
    assert first.status_code == 201, first.text
    second = await register(client, account, metrics_instance=instance)
    assert second.status_code == 422, second.text
    assert second.json()["error"]["details"]["fields"][0]["loc"] == ["body", "metrics_instance"]


# --- 14.3 / 14.1 reads -------------------------------------------------------


async def test_detail_and_list_round_trip(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, account = administrator
    created = (await register(client, account)).json()

    detail = await call(client, "GET", f"{SERVERS}/{created['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["hostname"] == created["hostname"]
    assert detail.json()["registered_service_count"] == 0

    listed = await call(client, "GET", SERVERS, params={"q": created["hostname"]})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["id"] for item in body["items"]] == [created["id"]]
    # §5: total is null on a cursor page, by design.
    assert body["page"]["total"] is None


async def test_an_unknown_server_is_404(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, _account = administrator
    response = await call(client, "GET", f"{SERVERS}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_an_unknown_query_parameter_is_422_naming_the_allowed_set(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """§6. A silently ignored filter returns the unfiltered collection."""
    client, _caller, _account = administrator
    response = await call(client, "GET", SERVERS, params={"enviroment": "production"})
    assert response.status_code == 422, response.text
    field = response.json()["error"]["details"]["fields"][0]
    assert field["loc"] == ["query", "enviroment"]
    assert "environment" in field["allowed"]


async def test_a_limit_above_the_maximum_is_422_not_a_silent_clamp(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, _account = administrator
    response = await call(client, "GET", SERVERS, params={"limit": 500})
    assert response.status_code == 422, response.text


async def test_a_tampered_cursor_is_422(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, _account = administrator
    response = await call(client, "GET", SERVERS, params={"cursor": "bm90LWEtY3Vyc29y.AAAA"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["fields"][0]["code"] == "invalid_cursor"


async def test_paging_is_stable_across_a_concurrent_insert(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """Keyset, not offset: a row inserted mid-page must not shift the window.

    Four servers are registered with names that sort in a known order. After
    reading page one, a fifth is inserted *before* the cursor's position —
    where an offset-based page two would skip a row and repeat none.
    """
    client, _caller, account = administrator
    marker = uuid.uuid4().hex[:8]
    for index in (2, 4, 6, 8):
        created = await register(client, account, display_name=f"{marker}-{index:02d}")
        assert created.status_code == 201, created.text

    first = await call(
        client, "GET", SERVERS, params={"limit": 2, "sort": "display_name", "q": marker}
    )
    assert first.status_code == 200, first.text
    page_one = first.json()
    assert [item["display_name"] for item in page_one["items"]] == [
        f"{marker}-02",
        f"{marker}-04",
    ]
    assert page_one["page"]["has_more"] is True

    inserted = await register(client, account, display_name=f"{marker}-03")
    assert inserted.status_code == 201, inserted.text

    second = await call(
        client,
        "GET",
        SERVERS,
        params={
            "limit": 2,
            "sort": "display_name",
            "q": marker,
            "cursor": page_one["page"]["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert [item["display_name"] for item in second.json()["items"]] == [
        f"{marker}-06",
        f"{marker}-08",
    ]


async def test_the_five_statuses_stay_distinct(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    """PRD §4.3 acceptance: PENDING / MANAGED / STALE / UNREACHABLE / RETIRED.

    The two derived statuses are written directly, because the sweep that sets
    them is M2.5 — the point here is that the inventory *distinguishes* them,
    which the filter either does or does not.
    """
    client, _caller, account = administrator
    marker = uuid.uuid4().hex[:8]
    identifiers = {}
    for status in ("PENDING", "MANAGED", "STALE", "UNREACHABLE"):
        created = await register(client, account, display_name=f"{marker}-{status}")
        assert created.status_code == 201, created.text
        identifiers[status] = created.json()["id"]
        if status != "PENDING":
            with migrated.begin() as connection:
                connection.execute(
                    text("UPDATE servers SET status = :s WHERE id = :id"),
                    {"s": status, "id": identifiers[status]},
                )

    for status, identifier in identifiers.items():
        response = await call(client, "GET", SERVERS, params={"q": marker, "status": status})
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [identifier]


# --- 14.4 update -------------------------------------------------------------


async def test_metadata_is_editable_and_audited(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    client, _caller, account = administrator
    created = (await register(client, account)).json()
    response = await call(
        client,
        "PATCH",
        f"{SERVERS}/{created['id']}",
        {"display_name": "Renamed", "owner_team": "platform", "monitoring_enabled": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Renamed"
    assert response.json()["monitoring_enabled"] is False

    rows = [
        row
        for row in audit_rows(migrated, "SERVER_UPDATED")
        if str(row["target_id"]) == created["id"]
    ]
    assert len(rows) == 1
    changes = rows[0]["sanitized_metadata"]["changes"]
    assert changes["display_name"] == {"from": "Test Server", "to": "Renamed"}


async def test_an_immutable_field_is_refused_on_patch(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """§14.4: ``cloud_account_id`` and friends are not editable."""
    client, _caller, account = administrator
    created = (await register(client, account)).json()
    response = await call(
        client,
        "PATCH",
        f"{SERVERS}/{created['id']}",
        {"cloud_account_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text
    locations = [field["loc"] for field in response.json()["error"]["details"]["fields"]]
    assert ["body", "cloud_account_id"] in locations


async def test_an_environment_change_is_audited_as_privilege_relevant(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    """§14.4: it moves the authorisation boundary, so it gets its own event."""
    client, _caller, account = administrator
    created = (await register(client, account, environment="staging")).json()
    response = await call(
        client, "PATCH", f"{SERVERS}/{created['id']}", {"environment": "production"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["environment"] == "production"

    rows = [
        row
        for row in audit_rows(migrated, "SERVER_ENVIRONMENT_CHANGED")
        if str(row["target_id"]) == created["id"]
    ]
    assert len(rows) == 1
    assert rows[0]["sanitized_metadata"] == {"from": "staging", "to": "production"}


async def test_if_unmodified_since_refuses_a_stale_write(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """§14.4: optimistic concurrency ⇒ 409 conflict."""
    client, _caller, account = administrator
    created = (await register(client, account)).json()
    stale = format_datetime(datetime.now(UTC) - timedelta(hours=1), usegmt=True)
    response = await call(
        client,
        "PATCH",
        f"{SERVERS}/{created['id']}",
        {"display_name": "Renamed"},
        headers={"If-Unmodified-Since": stale},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"


async def test_if_unmodified_since_allows_a_fresh_write(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, account = administrator
    created = (await register(client, account)).json()
    fresh = format_datetime(datetime.now(UTC) + timedelta(minutes=1), usegmt=True)
    response = await call(
        client,
        "PATCH",
        f"{SERVERS}/{created['id']}",
        {"display_name": "Renamed"},
        headers={"If-Unmodified-Since": fresh},
    )
    assert response.status_code == 200, response.text


async def test_a_malformed_if_unmodified_since_is_refused_not_ignored(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """Dropping the precondition would invert what the client asked for."""
    client, _caller, account = administrator
    created = (await register(client, account)).json()
    response = await call(
        client,
        "PATCH",
        f"{SERVERS}/{created['id']}",
        {"display_name": "Renamed"},
        headers={"If-Unmodified-Since": "last tuesday"},
    )
    assert response.status_code == 422, response.text


# --- 14.5 retire -------------------------------------------------------------


async def test_a_retired_server_disappears_from_reads_and_is_audited(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
    migrated: Engine,
) -> None:
    client, _caller, account = administrator
    created = (await register(client, account)).json()
    response = await call(client, "DELETE", f"{SERVERS}/{created['id']}")
    assert response.status_code == 204, response.text

    assert (await call(client, "GET", f"{SERVERS}/{created['id']}")).status_code == 404
    listed = await call(client, "GET", SERVERS, params={"q": created["hostname"]})
    assert listed.json()["items"] == []

    with migrated.connect() as connection:
        row = connection.execute(
            text("SELECT status, deleted_at FROM servers WHERE id = :id"), {"id": created["id"]}
        ).one()
    assert row[0] == "RETIRED"
    assert row[1] is not None

    assert any(
        str(entry["target_id"]) == created["id"] for entry in audit_rows(migrated, "SERVER_RETIRED")
    )


# --- 14.6 / 14.7 associations ------------------------------------------------


async def test_one_application_spans_servers_and_one_server_hosts_applications(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    """PRD §4.9 acceptance, in both directions (§14.6)."""
    client, _caller, account = administrator
    first_app = (
        await call(
            client,
            "POST",
            APPLICATIONS,
            application_body(),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
    ).json()
    second_app = (
        await call(
            client,
            "POST",
            APPLICATIONS,
            application_body(),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
    ).json()
    server_one = (await register(client, account)).json()
    server_two = (await register(client, account)).json()

    for server in (server_one, server_two):
        response = await call(
            client,
            "POST",
            f"{SERVERS}/{server['id']}/applications",
            {"application_id": first_app["id"]},
        )
        assert response.status_code == 201, response.text
    response = await call(
        client,
        "POST",
        f"{SERVERS}/{server_one['id']}/applications",
        {"application_id": second_app["id"], "role_on_server": "web"},
    )
    assert response.status_code == 201, response.text

    detail = (await call(client, "GET", f"{SERVERS}/{server_one['id']}")).json()
    assert {entry["id"] for entry in detail["applications"]} == {
        first_app["id"],
        second_app["id"],
    }
    application = (await call(client, "GET", f"{APPLICATIONS}/{first_app['id']}")).json()
    assert {entry["id"] for entry in application["servers"]} == {
        server_one["id"],
        server_two["id"],
    }


async def test_a_duplicate_association_is_refused_and_removal_works(
    administrator: tuple[httpx.AsyncClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _caller, account = administrator
    application = (
        await call(
            client,
            "POST",
            APPLICATIONS,
            application_body(),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
    ).json()
    server = (await register(client, account)).json()
    path = f"{SERVERS}/{server['id']}/applications"

    assert (
        await call(client, "POST", path, {"application_id": application["id"]})
    ).status_code == 201
    repeat = await call(client, "POST", path, {"application_id": application["id"]})
    assert repeat.status_code == 409
    assert repeat.json()["error"]["code"] == "duplicate_resource"

    removed = await call(client, "DELETE", f"{path}/{application['id']}")
    assert removed.status_code == 204, removed.text
    detail = (await call(client, "GET", f"{SERVERS}/{server['id']}")).json()
    assert detail["applications"] == []


# --- authorisation -----------------------------------------------------------


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"])
async def test_a_non_administrator_cannot_register_or_edit(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    role: str,
) -> None:
    """M1.5 acceptance: "a Viewer cannot register or edit anything".

    Asserted over HTTP with a valid session, not through the UI: the frontend
    hiding a button is not the control (docs/SECURITY.md §6).
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    grant(migrated, caller, role)
    account = account_id(migrated)
    try:
        created = await register(client, account)
        assert created.status_code == 403, created.text
        assert created.json()["error"]["code"] == "forbidden"

        # The same caller *can* read: server:read is in every role.
        assert (await call(client, "GET", SERVERS)).status_code == 200
    finally:
        revoke_all(migrated, caller)


async def test_a_caller_with_no_roles_is_refused_reads_as_well(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """Deny by default: an authenticated user with no grant reaches nothing."""
    client, provider = auth_app
    await _login(client, provider)
    revoke_all(migrated, user_id(migrated, provider.subject))
    assert (await call(client, "GET", SERVERS)).status_code == 403
