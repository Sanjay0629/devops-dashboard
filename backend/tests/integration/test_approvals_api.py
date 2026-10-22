"""The approval workflow end to end (M4.5, ``OPEN-93`` - ``OPEN-104``): request, queue, decide,
cancel and expire, against real PostgreSQL and Redis through the real application.

Two people matter in these tests, so there are two logged-in clients: the **requester** (the
``rig`` caller, a DevOps Engineer) and an **approver** (a second login holding ``APPROVER``). The
adapter is the test-only ``ScriptedAdapter`` and ``enqueue_execution`` is a recorder: this
proves the workflow, and nothing about any cloud. The worker's half -- the evidence re-check
and the bypass suite -- is ``test_approval_bypass.py``.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.approvals import sweep
from app.approvals.store import decide_statement, expire_statement
from app.operations import execute
from app.providers import registry
from tests.integration.inventory_helpers import (
    STAGING_SCOPE,
    call,
    grant,
    revoke_all,
    user_id,
)
from tests.integration.test_auth_flow import FakeIdp, _login, auth_app  # noqa: F401
from tests.integration.test_operations_api import (  # noqa: F401 -- fixtures used by name
    Rig,
    adapter,
    idp,
    idp_key,
    redis_url,
    rig,
    subject,
)
from tests.operations.fakes import Scripted

PRODUCTION_POLICY = "production-service-operations"


@pytest.fixture(autouse=True)
def _leave_no_expired_operation_behind(migrated: Engine) -> Iterator[None]:
    """Rows are shared with the rest of the session, and one thing must not outlive these tests:
    an operation whose approval is ``EXPIRED`` has no older equivalent, so migration 0012's
    downgrade refuses while one exists -- which is the behaviour, and would fail the migration
    round-trip test for the wrong reason if another suite ran after this one."""
    yield
    with migrated.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM approvals WHERE operation_id IN"
                " (SELECT id FROM operations WHERE approval_status = 'EXPIRED')"
            )
        )
        connection.execute(text("DELETE FROM operations WHERE approval_status = 'EXPIRED'"))


@dataclass
class Actor:
    client: httpx.AsyncClient
    id: uuid.UUID


@pytest.fixture
async def approver(
    rig: Rig,
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> AsyncIterator[Actor]:
    """A second, separately logged-in person who holds ``APPROVER`` everywhere."""
    actor = await _second_login(rig, auth_app[1])
    grant(rig.engine, actor.id, "APPROVER")
    yield actor
    revoke_all(rig.engine, actor.id)
    await actor.client.aclose()


async def _second_login(rig: Rig, provider: FakeIdp) -> Actor:
    original = (provider.subject, provider.email)
    other = f"second-subject-{uuid.uuid4()}"
    provider.subject, provider.email = other, f"{other}@example.invalid"
    client = httpx.AsyncClient(transport=rig.client._transport, base_url="http://testserver")
    try:
        await _login(client, provider)
    finally:
        provider.subject, provider.email = original
    return Actor(client, user_id(rig.engine, other))


async def decide(
    actor: Actor, operation: str, verb: str = "approve", **body: Any
) -> httpx.Response:
    return await call(actor.client, "POST", f"/api/v1/operations/{operation}/{verb}", body or None)


async def waiting(rig: Rig, unit: str = "gated.service", action: str = "restart") -> dict[str, Any]:
    response = await rig.ask(rig.production, unit, action=action)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    assert body["execution_status"] == "AWAITING_APPROVAL"
    return body


def approval_row(engine: Engine, operation_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM approvals WHERE operation_id = :o"), {"o": operation_id}
            )
            .mappings()
            .one()
        )
    return dict(row)


def alert_status(engine: Engine, service_id: str) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT status FROM alert_events WHERE rule_name = 'ApprovalPending'"
                    " AND target_id = :t ORDER BY first_seen_at"
                ),
                {"t": service_id},
            ).scalars()
        )


@contextmanager
def policy_changed(engine: Engine, **columns: Any) -> Iterator[None]:
    """Edit the seeded production policy for one test and put it back."""
    names = ", ".join(columns)
    with engine.begin() as connection:
        original = connection.execute(
            text(f"SELECT {names} FROM approval_policies WHERE name = :n"),  # noqa: S608
            {"n": PRODUCTION_POLICY},
        ).one()
        assignments = ", ".join(f"{name} = :{name}" for name in columns)
        connection.execute(
            text(f"UPDATE approval_policies SET {assignments} WHERE name = :n"),  # noqa: S608
            {"n": PRODUCTION_POLICY, **columns},
        )
    try:
        yield
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(f"UPDATE approval_policies SET {assignments} WHERE name = :n"),  # noqa: S608
                {"n": PRODUCTION_POLICY, **dict(zip(columns, original, strict=True))},
            )


def lapse(engine: Engine, operation_id: str) -> None:
    """Move the approval window into the past, as time passing would."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE approvals SET expires_at = now() - interval '1 second'"
                " WHERE operation_id = :o"
            ),
            {"o": operation_id},
        )


# --- the request ------------------------------------------------------------------------------


async def test_a_gated_request_waits_and_tells_approvers(rig: Rig) -> None:
    body = await waiting(rig)
    service_id = rig.service_id(rig.production, "gated.service")
    operation = body["operation_id"]

    assert body["approval_status"] == "PENDING" and body["poll_after_seconds"] == 2
    summary = body["approval"]
    assert summary["required_approvals"] == 1 and summary["decision"] == "PENDING"
    assert summary["required_role"] == "APPROVER" and summary["approved_by"] is None
    assert body["deadline_at"] == summary["expires_at"]  # the only deadline is the window
    assert rig.enqueued == []

    [row] = rig.rows(id=operation)
    row_approval = approval_row(rig.engine, operation)
    assert row_approval["decision"] == "PENDING" and row_approval["decided_at"] is None
    assert row_approval["requested_by"] == rig.caller and row_approval["approved_by"] is None
    assert (row_approval["expires_at"] - row_approval["created_at"]).total_seconds() == 3600
    assert row["deadline_at"] == row_approval["expires_at"]
    assert str(row_approval["id"]) == summary["approval_id"]

    [event] = rig.audit("SERVICE_OPERATION_REQUESTED", target_id=operation)
    assert event["sanitized_metadata"]["approval_required"] is True
    with rig.engine.connect() as connection:
        linked = connection.execute(
            text("SELECT approval_id FROM audit_events WHERE target_id = :t AND action_type = :a"),
            {"t": operation, "a": "SERVICE_OPERATION_REQUESTED"},
        ).scalar_one()
    assert str(linked) == summary["approval_id"]
    assert alert_status(rig.engine, service_id) == ["FIRING"]


async def test_a_production_stop_with_no_policy_uses_the_strict_defaults(rig: Rig) -> None:
    body = await waiting(rig, "api.service", "stop")
    row = approval_row(rig.engine, body["operation_id"])
    assert row["approval_policy_id"] is None  # the built-in defaults, not a policy row
    assert (row["expires_at"] - row["created_at"]).total_seconds() == 3600
    assert body["approval"]["required_role"] == "APPROVER"


async def test_a_replay_of_a_waiting_request_is_the_original(rig: Rig) -> None:
    key = str(uuid.uuid4())
    first = await rig.ask(rig.production, "gated.service", key=key)
    second = await rig.ask(rig.production, "gated.service", key=key)
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["operation_id"] == first.json()["operation_id"]
    assert second.json()["execution_status"] == "AWAITING_APPROVAL"
    with rig.engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM approvals WHERE operation_id = :o"),
            {"o": first.json()["operation_id"]},
        ).scalar_one()
    assert count == 1 and rig.enqueued == []


async def test_a_reason_is_required_for_a_gated_request(rig: Rig) -> None:
    response = await rig.ask(rig.production, "gated.service", reason=None)
    assert response.status_code == 422, response.text
    assert rig.rows(target_service_id=rig.service_id(rig.production, "gated.service")) == []


async def test_a_waiting_request_holds_the_targets_one_slot(rig: Rig) -> None:
    await waiting(rig)
    again = await rig.ask(rig.production, "gated.service", action="restart")
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "operation_in_progress"


async def test_a_policy_asking_for_two_approvals_is_refused_not_treated_as_one(
    rig: Rig,
) -> None:
    with policy_changed(rig.engine, required_approvals=2):
        response = await rig.ask(rig.production, "gated.service")
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "not_configured"
    assert error["details"]["reason"] == "approval_policy_unsupported"
    assert rig.rows(target_service_id=rig.service_id(rig.production, "gated.service")) == []
    [event] = [
        e
        for e in rig.audit("SERVICE_OPERATION_DENIED")
        if str(e["target_id"]) == rig.service_id(rig.production, "gated.service")
    ]
    assert event["sanitized_metadata"]["reason"] == "approval_policy_unsupported"


# --- the queue and the detail -----------------------------------------------------------------


async def test_the_queue_shows_the_four_facts_and_the_self_requested_flag(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    listed = await call(approver.client, "GET", "/api/v1/approvals")
    assert listed.status_code == 200, listed.text
    [item] = [i for i in listed.json()["items"] if i["operation_id"] == body["operation_id"]]
    assert item["target"] == {
        "server_id": rig.production,
        "server_display_name": "Production",
        "service_name": "gated.service",
        "environment": "production",
    }
    assert item["action_type"] == "restart" and item["reason"] == "CR-0000"
    assert item["decision"] == "PENDING" and item["expires_at"] == body["approval"]["expires_at"]
    assert item["requested_by"]["id"] == str(rig.caller)
    assert item["self_requested"] is False and item["self_approval_allowed"] is False
    assert item["approved_by"] is None and item["operation_type"] == "SERVICE_OPERATION"

    # The requester, who also holds APPROVER, sees the same row flagged as theirs.
    grant(rig.engine, rig.caller, "APPROVER")
    mine = await call(rig.client, "GET", "/api/v1/approvals")
    [own] = [i for i in mine.json()["items"] if i["operation_id"] == body["operation_id"]]
    assert own["self_requested"] is True and own["self_approval_allowed"] is False


async def test_the_detail_adds_policy_decisions_and_the_last_observed_state(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    response = await call(
        approver.client, "GET", f"/api/v1/approvals/{body['approval']['approval_id']}"
    )
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["policy"]["required_approvals"] == 1
    assert detail["policy"]["allow_self_approval"] is False
    assert detail["policy"]["required_role"] == "APPROVER"
    assert detail["policy"]["policy_id"] is not None
    assert detail["decisions_so_far"] == []
    assert set(detail["target_state"]) == {"state", "observed_at", "data_state"}
    assert detail["target_state"]["data_state"] in ("ok", "stale", "unavailable")


async def test_the_queue_requires_the_approve_permission(rig: Rig) -> None:
    for path in ("/api/v1/approvals", f"/api/v1/approvals/{uuid.uuid4()}"):
        response = await call(rig.client, "GET", path)  # a DevOps Engineer cannot approve
        assert response.status_code == 403, (path, response.text)
    response = await decide_as_requester(rig, str(uuid.uuid4()))
    assert response.status_code == 403


async def decide_as_requester(rig: Rig, operation: str) -> httpx.Response:
    return await call(rig.client, "POST", f"/api/v1/operations/{operation}/approve", None)


async def test_the_queue_is_scoped_in_the_sql_and_an_out_of_scope_request_is_invisible(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    revoke_all(rig.engine, approver.id)
    grant(rig.engine, approver.id, "APPROVER", STAGING_SCOPE)  # production is not named

    listed = await call(approver.client, "GET", "/api/v1/approvals")
    assert body["operation_id"] not in {i["operation_id"] for i in listed.json()["items"]}
    detail = await call(
        approver.client, "GET", f"/api/v1/approvals/{body['approval']['approval_id']}"
    )
    assert detail.status_code == 404
    attempt = await decide(approver, body["operation_id"])
    assert attempt.status_code == 404  # never 403: it is not theirs to know about
    assert approval_row(rig.engine, body["operation_id"])["decision"] == "PENDING"


async def test_the_queue_filters_and_pages(rig: Rig, approver: Actor) -> None:
    first = await waiting(rig, "gated.service")
    second = await waiting(rig, "api.service", "stop")
    ids = {first["operation_id"], second["operation_id"]}

    page = await call(approver.client, "GET", "/api/v1/approvals", params={"limit": 1})
    assert page.status_code == 200 and page.json()["page"]["has_more"] is True
    cursor = page.json()["page"]["next_cursor"]
    seen = {i["operation_id"] for i in page.json()["items"]}
    rest = await call(
        approver.client, "GET", "/api/v1/approvals", params={"limit": 50, "cursor": cursor}
    )
    seen |= {i["operation_id"] for i in rest.json()["items"]}
    assert ids <= seen

    staging = await call(
        approver.client, "GET", "/api/v1/approvals", params={"environment": "staging"}
    )
    assert ids.isdisjoint({i["operation_id"] for i in staging.json()["items"]})
    bad_cursor = await call(approver.client, "GET", "/api/v1/approvals", params={"cursor": "nope"})
    assert bad_cursor.status_code == 422
    deployments = await call(
        approver.client, "GET", "/api/v1/approvals", params={"operation_type": "DEPLOYMENT"}
    )
    assert deployments.status_code == 422
    too_many = await call(approver.client, "GET", "/api/v1/approvals", params={"limit": 201})
    assert too_many.status_code == 422


# --- approving --------------------------------------------------------------------------------


async def test_an_approval_commits_the_decision_the_audit_row_and_hands_the_worker_its_id(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    operation = body["operation_id"]
    service_id = rig.service_id(rig.production, "gated.service")

    response = await decide(approver, operation, reason="Looked at CR-0000")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["execution_status"] == "APPROVED" and detail["approval_status"] == "APPROVED"
    assert detail["approval"]["decision"] == "APPROVED"
    assert detail["approval"]["approved_by"]["id"] == str(approver.id)
    assert detail["approval"]["decided_at"] is not None

    [row] = rig.rows(id=operation)
    decided = approval_row(rig.engine, operation)
    assert decided["decision"] == "APPROVED" and decided["approved_by"] == approver.id
    assert decided["reason"] == "Looked at CR-0000" and decided["decided_at"] is not None
    # The execution gets its full 300 s from the approval, not from the request (OPEN-96).
    assert (row["deadline_at"] - decided["decided_at"]).total_seconds() == 300
    assert rig.enqueued == [uuid.UUID(operation)]

    [event] = rig.audit("SERVICE_OPERATION_APPROVED", target_id=operation)
    assert event["actor_id"] == approver.id and event["outcome"] == "SUCCESS"
    assert event["sanitized_metadata"]["reason_length"] == len("Looked at CR-0000")
    assert "Looked at CR-0000" not in str(event["sanitized_metadata"])  # user text: length only
    with rig.engine.connect() as connection:
        linked = connection.execute(
            text("SELECT approval_id FROM audit_events WHERE target_id = :t AND action_type = :a"),
            {"t": operation, "a": "SERVICE_OPERATION_APPROVED"},
        ).scalar_one()
    assert str(linked) == body["approval"]["approval_id"]
    assert alert_status(rig.engine, service_id) == ["RESOLVED"]

    again = await decide(approver, operation)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "invalid_state_transition"
    assert again.json()["error"]["details"]["reason"] == "not_awaiting_approval"
    assert rig.enqueued == [uuid.UUID(operation)]  # nothing was enqueued a second time


async def test_a_rejection_needs_a_reason_and_ends_the_request_without_execution(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    operation = body["operation_id"]
    service_id = rig.service_id(rig.production, "gated.service")

    missing = await decide(approver, operation, "reject")
    assert missing.status_code == 422, missing.text
    assert approval_row(rig.engine, operation)["decision"] == "PENDING"  # nothing changed

    response = await decide(approver, operation, "reject", reason="Not during the release")
    assert response.status_code == 200, response.text
    assert response.json()["execution_status"] == "REJECTED"
    assert response.json()["approval_status"] == "REJECTED"
    [row] = rig.rows(id=operation)
    assert row["completed_at"] is not None and row["started_at"] is None
    assert row["sanitized_result"]["reason_code"] == "approval_rejected"
    decided = approval_row(rig.engine, operation)
    assert decided["decision"] == "REJECTED" and decided["approved_by"] == approver.id
    assert decided["reason"] == "Not during the release"
    assert rig.enqueued == []
    [event] = rig.audit("SERVICE_OPERATION_REJECTED", target_id=operation)
    assert event["actor_id"] == approver.id
    assert alert_status(rig.engine, service_id) == ["RESOLVED"]
    # The slot is free again.
    assert (await rig.ask(rig.production, "gated.service")).status_code == 202


async def test_a_decision_body_cannot_name_anything_but_a_reason(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    for extra in ({"approved_by": str(approver.id)}, {"decision": "APPROVED"}, {"force": True}):
        response = await decide(approver, body["operation_id"], **extra)
        assert response.status_code == 422, response.text
    assert approval_row(rig.engine, body["operation_id"])["decision"] == "PENDING"


async def test_an_unknown_operation_is_404(rig: Rig, approver: Actor) -> None:
    assert (await decide(approver, str(uuid.uuid4()))).status_code == 404


async def test_an_operation_that_was_never_gated_cannot_be_decided(
    rig: Rig,
    approver: Actor,
) -> None:
    created = (await rig.ask()).json()  # staging, ungated: PENDING
    response = await decide(approver, created["operation_id"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "not_awaiting_approval"


# --- separation of duties, scope and role -----------------------------------------------------


async def test_a_requester_cannot_approve_their_own_request(rig: Rig) -> None:
    grant(rig.engine, rig.caller, "APPROVER")  # one person holding both roles
    body = await waiting(rig)
    operation = body["operation_id"]

    response = await decide_as_requester(rig, operation)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "self_approval_not_permitted"
    assert approval_row(rig.engine, operation)["decision"] == "PENDING"
    assert rig.enqueued == []
    [event] = rig.audit("SERVICE_OPERATION_DENIED", target_id=operation)
    assert event["outcome"] == "DENIED" and event["actor_id"] == rig.caller
    assert event["sanitized_metadata"]["reason"] == "self_approval_not_permitted"
    assert event["sanitized_metadata"]["phase"] == "approval_decision"
    # Rejecting your own request is a decision too, and equally refused.
    own_reject = await call(
        rig.client, "POST", f"/api/v1/operations/{operation}/reject", {"reason": "changed my mind"}
    )
    assert own_reject.status_code == 403
    # Cancel is how a requester withdraws it: always legal.
    cancelled = await call(rig.client, "POST", f"/api/v1/operations/{operation}/cancel", None)
    assert cancelled.status_code == 200 and cancelled.json()["execution_status"] == "CANCELLED"


async def test_a_policy_that_allows_self_approval_lets_the_requester_decide(
    rig: Rig,
) -> None:
    grant(rig.engine, rig.caller, "APPROVER")
    with policy_changed(rig.engine, allow_self_approval=True):
        body = await waiting(rig)
        listed = await call(rig.client, "GET", "/api/v1/approvals")
        [item] = [i for i in listed.json()["items"] if i["operation_id"] == body["operation_id"]]
        assert item["self_requested"] is True and item["self_approval_allowed"] is True
        response = await decide_as_requester(rig, body["operation_id"])
        assert response.status_code == 200, response.text
    assert response.json()["execution_status"] == "APPROVED"
    [event] = rig.audit("SERVICE_OPERATION_APPROVED", target_id=body["operation_id"])
    assert event["sanitized_metadata"]["self_decided"] is True


async def test_an_approver_without_the_policys_role_is_refused(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    with policy_changed(rig.engine, required_role="DEVOPS_ENGINEER"):
        response = await decide(approver, body["operation_id"])
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "approver_role_required"
    [event] = rig.audit("SERVICE_OPERATION_DENIED", target_id=body["operation_id"])
    assert event["sanitized_metadata"]["reason"] == "approver_role_required"


async def test_an_approver_who_can_read_but_not_approve_here_is_refused_and_it_is_audited(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    revoke_all(rig.engine, approver.id)
    grant(rig.engine, approver.id, "VIEWER")  # may read operations in production
    grant(rig.engine, approver.id, "APPROVER", STAGING_SCOPE)  # may approve in staging only

    response = await decide(approver, body["operation_id"])
    assert response.status_code == 403, response.text
    assert "reason" not in (response.json()["error"].get("details") or {})  # scope stays uniform
    [event] = rig.audit("SERVICE_OPERATION_DENIED", target_id=body["operation_id"])
    assert event["sanitized_metadata"]["reason"] == "out_of_scope"
    assert approval_row(rig.engine, body["operation_id"])["decision"] == "PENDING"


async def test_a_stored_environment_not_the_servers_current_label_decides_scope(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    with rig.engine.begin() as connection:  # the server is re-labelled after the request
        connection.execute(
            text("UPDATE servers SET environment = 'staging' WHERE id = :i"), {"i": rig.production}
        )
    try:
        revoke_all(rig.engine, approver.id)
        grant(rig.engine, approver.id, "APPROVER", STAGING_SCOPE)
        response = await decide(approver, body["operation_id"])
        # The row says production, so a staging approver has no standing over it -- to decide it
        # or, since M4.6 fixed PROGRESS issue 164, to see it.
        # 404, never 403 (§2): out of scope for the stored environment means not visible at all.
        assert response.status_code == 404, response.text
        assert rig.audit("SERVICE_OPERATION_DENIED", target_id=body["operation_id"]) == []
        listed = await call(approver.client, "GET", "/api/v1/approvals")
        assert body["operation_id"] not in {i["operation_id"] for i in listed.json()["items"]}
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text("UPDATE servers SET environment = 'production' WHERE id = :i"),
                {"i": rig.production},
            )


async def test_visibility_follows_the_stored_environment_not_the_servers_current_label(
    rig: Rig,
    approver: Actor,
) -> None:
    """Issue 164, fixed in M4.6 (``OPEN-107``): §17.2 reads the stored environment."""
    body = await waiting(rig)
    operation = f"/api/v1/operations/{body['operation_id']}"
    with rig.engine.begin() as connection:  # re-labelled staging after the request was made
        connection.execute(
            text("UPDATE servers SET environment = 'staging' WHERE id = :i"), {"i": rig.production}
        )
    try:
        revoke_all(rig.engine, approver.id)
        grant(rig.engine, approver.id, "APPROVER", STAGING_SCOPE)
        refused = await call(approver.client, "GET", operation)
        assert refused.status_code == 404, refused.text  # the row says production
        revoke_all(rig.engine, approver.id)
        grant(rig.engine, approver.id, "APPROVER")  # production named
        allowed = await call(approver.client, "GET", operation)
        assert allowed.status_code == 200, allowed.text
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text("UPDATE servers SET environment = 'production' WHERE id = :i"),
                {"i": rig.production},
            )


# --- cancel -----------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["requester", "approver"])
async def test_cancelling_a_waiting_request_ends_its_approval_and_frees_the_slot(
    rig: Rig,
    approver: Actor,
    who: str,
) -> None:
    body = await waiting(rig)
    operation = body["operation_id"]
    service_id = rig.service_id(rig.production, "gated.service")
    client = rig.client if who == "requester" else approver.client

    response = await call(client, "POST", f"/api/v1/operations/{operation}/cancel", None)
    assert response.status_code == 200, response.text
    assert response.json()["execution_status"] == "CANCELLED"
    assert response.json()["approval_status"] == "EXPIRED"
    assert response.json()["failure_reason"] is None
    [row] = rig.rows(id=operation)
    assert row["sanitized_result"]["reason_code"] == "cancelled" and row["completed_at"]
    assert approval_row(rig.engine, operation)["decision"] == "EXPIRED"
    [event] = rig.audit("SERVICE_OPERATION_CANCELLED", target_id=operation)
    assert event["outcome"] == "SUCCESS"
    assert alert_status(rig.engine, service_id) == ["RESOLVED"]
    assert (await rig.ask(rig.production, "gated.service")).status_code == 202  # slot is free

    late = await decide(approver, operation)
    assert late.status_code == 409, late.text
    assert late.json()["error"]["details"]["reason"] == "not_awaiting_approval"


# --- expiry -----------------------------------------------------------------------------------


async def test_a_decision_after_the_window_is_409_approval_expired_and_the_slot_is_freed(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    operation = body["operation_id"]
    service_id = rig.service_id(rig.production, "gated.service")
    lapse(rig.engine, operation)

    response = await decide(approver, operation)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_state_transition"
    assert response.json()["error"]["details"]["reason"] == "approval_expired"
    [row] = rig.rows(id=operation)
    assert row["execution_status"] == "CANCELLED" and row["approval_status"] == "EXPIRED"
    assert row["sanitized_result"]["reason_code"] == "approval_expired"
    assert approval_row(rig.engine, operation)["decision"] == "EXPIRED"
    assert rig.enqueued == []
    [event] = rig.audit("SERVICE_OPERATION_CANCELLED", target_id=operation)
    assert event["actor_label"] == "system:approval-sweeper"
    assert alert_status(rig.engine, service_id) == ["RESOLVED"]
    # A later attempt says the same, from the stored state, without needing the sweep.
    again = await decide(approver, operation)
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "approval_expired"


async def test_the_sweep_cancels_a_lapsed_request_on_behalf_of_its_requester(
    rig: Rig,
) -> None:
    body = await waiting(rig)
    operation = body["operation_id"]
    service_id = rig.service_id(rig.production, "gated.service")
    other = await waiting(rig, "api.service", "stop")  # still inside its window
    lapse(rig.engine, operation)

    assert sweep.sweep(rig.engine) >= 1

    [row] = rig.rows(id=operation)
    assert row["execution_status"] == "CANCELLED" and row["approval_status"] == "EXPIRED"
    assert row["failure_reason"] is None and row["completed_at"] is not None
    assert row["sanitized_result"]["reason_code"] == "approval_expired"
    assert approval_row(rig.engine, operation)["decision"] == "EXPIRED"
    [event] = rig.audit("SERVICE_OPERATION_CANCELLED", target_id=operation)
    assert event["actor_label"] == "system:approval-sweeper" and event["actor_id"] == rig.caller
    assert event["sanitized_metadata"]["on_behalf_of"] == str(rig.caller)
    assert alert_status(rig.engine, service_id) == ["RESOLVED"]
    # The one still inside its window is untouched, and the lapsed one's slot is free.
    assert rig.rows(id=other["operation_id"])[0]["execution_status"] == "AWAITING_APPROVAL"
    assert sweep.sweep(rig.engine) == 0
    assert (await rig.ask(rig.production, "gated.service")).status_code == 202


async def test_the_reaper_runs_the_sweep(rig: Rig) -> None:
    from app.operations import execute

    body = await waiting(rig)
    lapse(rig.engine, body["operation_id"])
    summary = execute.reap(rig.engine, enqueue=lambda _id: None)
    assert summary["approvals_expired"] >= 1
    assert rig.rows(id=body["operation_id"])[0]["execution_status"] == "CANCELLED"


async def test_the_deadline_sweep_never_times_out_a_request_that_is_waiting(
    rig: Rig,
) -> None:
    """``deadline_at`` while waiting is the approval window, so even a long wait is not a
    timeout; only the window ends it."""
    from app.operations import execute

    body = await waiting(rig)
    execute.reap(rig.engine, enqueue=lambda _id: None)
    assert rig.rows(id=body["operation_id"])[0]["execution_status"] == "AWAITING_APPROVAL"


# --- the races: the approval row is the arbiter -----------------------------------------------


async def test_a_decision_racing_a_cancel_has_exactly_one_winner(
    rig: Rig,
    approver: Actor,
) -> None:
    outcomes: list[tuple[int, int]] = []
    for _ in range(6):
        rig.reset_rate_limit()
        body = await waiting(rig)
        operation = body["operation_id"]
        approved, cancelled = await asyncio.gather(
            decide(approver, operation),
            call(rig.client, "POST", f"/api/v1/operations/{operation}/cancel", None),
        )
        outcomes.append((approved.status_code, cancelled.status_code))
        [row] = rig.rows(id=operation)
        decision = approval_row(rig.engine, operation)["decision"]
        assert {approved.status_code, cancelled.status_code} == {200, 409}, outcomes
        if approved.status_code == 200:
            assert (row["execution_status"], decision) == ("APPROVED", "APPROVED")
            rig.set_status(operation, "FAILED")  # free the slot for the next round
        else:
            assert (row["execution_status"], decision) == ("CANCELLED", "EXPIRED")
    assert len(rig.enqueued) == sum(1 for approved, _ in outcomes if approved == 200)


def _hold_decision_open(
    engine: Engine, approval_id: uuid.UUID, decider: uuid.UUID
) -> tuple[Any, Any]:
    """Run a decision inside an open transaction: it holds the approval row's lock."""
    connection = engine.connect()
    transaction = connection.begin()
    won = connection.execute(
        decide_statement(approval_id, decision="APPROVED", decider=decider, reason=None)
    ).first()
    assert won is not None
    return connection, transaction


async def test_the_guard_decides_a_race_at_the_sql_level_not_by_timing(
    rig: Rig,
    approver: Actor,
) -> None:
    """Two sessions, one open request: the second writer blocks on the row and then loses."""
    body = await waiting(rig)
    approval_id = uuid.UUID(body["approval"]["approval_id"])
    connection, transaction = _hold_decision_open(rig.engine, approval_id, approver.id)
    result: dict[str, Any] = {}

    def loser() -> None:
        with rig.engine.begin() as other:
            result["cancel"] = other.execute(
                expire_statement(approval_id, only_if_due=False)
            ).first()
            result["second_decision"] = other.execute(
                decide_statement(approval_id, decision="REJECTED", decider=rig.caller, reason="x")
            ).first()

    thread = threading.Thread(target=loser)
    thread.start()
    time.sleep(0.5)
    assert thread.is_alive()  # blocked on the row lock the first writer holds
    transaction.commit()
    connection.close()
    thread.join(10)
    assert result == {"cancel": None, "second_decision": None}
    assert approval_row(rig.engine, body["operation_id"])["decision"] == "APPROVED"


async def test_the_window_and_the_sweep_exclude_each_other_exactly(
    rig: Rig,
    approver: Actor,
) -> None:
    inside = await waiting(rig)
    inside_id = uuid.UUID(inside["approval"]["approval_id"])
    with rig.engine.begin() as connection:
        assert connection.execute(expire_statement(inside_id, only_if_due=True)).first() is None
        assert (
            connection.execute(
                decide_statement(inside_id, decision="APPROVED", decider=approver.id, reason=None)
            ).first()
            is not None
        )
    rig.set_status(inside["operation_id"], "FAILED")

    rig.reset_rate_limit()
    past = await waiting(rig)
    past_id = uuid.UUID(past["approval"]["approval_id"])
    lapse(rig.engine, past["operation_id"])
    with rig.engine.begin() as connection:
        assert (
            connection.execute(
                decide_statement(past_id, decision="APPROVED", decider=approver.id, reason=None)
            ).first()
            is None
        )
        assert connection.execute(expire_statement(past_id, only_if_due=True)).first() is not None


async def test_a_second_decision_statement_finds_nothing_to_decide(
    rig: Rig,
    approver: Actor,
) -> None:
    body = await waiting(rig)
    approval_id = uuid.UUID(body["approval"]["approval_id"])
    with rig.engine.begin() as connection:
        first = connection.execute(
            decide_statement(approval_id, decision="APPROVED", decider=approver.id, reason=None)
        ).first()
        second = connection.execute(
            decide_statement(approval_id, decision="REJECTED", decider=rig.caller, reason="no")
        ).first()
    assert first is not None and second is None
    assert approval_row(rig.engine, body["operation_id"])["approved_by"] == approver.id


# --- the whole path: request, approve, then the worker ----------------------------------------


def _provider(rig: Rig, **script: Any) -> Scripted:
    fake = Scripted(unit="gated.service", before="INACTIVE", after=("ACTIVE",), **script)
    rig.monkeypatch.setitem(registry._FACTORIES, "ON_PREM", lambda: fake)
    return fake


async def test_a_gated_restart_requested_approved_and_run_ends_succeeded_and_verified(
    rig: Rig,
    approver: Actor,
) -> None:
    fake = _provider(rig)
    body = await waiting(rig)
    operation = uuid.UUID(body["operation_id"])
    assert (await decide(approver, str(operation))).status_code == 200
    assert rig.enqueued == [operation]

    outcome = await asyncio.to_thread(execute.run, rig.engine, operation, sleep=lambda _: None)

    assert outcome == "SUCCEEDED"
    [row] = rig.rows(id=str(operation))
    assert row["execution_status"] == "SUCCEEDED" and row["approval_status"] == "APPROVED"
    assert row["observed_state_after"] == "ACTIVE" and row["failure_reason"] is None
    assert fake.submitted == [(execute.ServiceAction.RESTART, operation)]
    # The audit trail names the person who asked, the person who decided, and the worker.
    assert rig.audit("SERVICE_OPERATION_REQUESTED", target_id=str(operation))
    assert rig.audit("SERVICE_OPERATION_APPROVED", target_id=str(operation))
    [completed] = rig.audit("SERVICE_OPERATION_COMPLETED", target_id=str(operation))
    assert completed["outcome"] == "SUCCESS"


async def test_a_direct_execution_attempt_on_a_waiting_request_does_nothing(
    rig: Rig,
) -> None:
    """The adversarial case in IMPLEMENTATION_PLAN M4.5: skipping the approval step entirely."""
    fake = _provider(rig)
    body = await waiting(rig)
    operation = uuid.UUID(body["operation_id"])

    outcome = await asyncio.to_thread(execute.run, rig.engine, operation, sleep=lambda _: None)

    assert outcome == "not_claimable"
    assert fake.submit_attempts == 0 and fake.submitted == []
    assert rig.rows(id=str(operation))[0]["execution_status"] == "AWAITING_APPROVAL"


async def test_a_rejected_request_cannot_be_run(rig: Rig, approver: Actor) -> None:
    fake = _provider(rig)
    body = await waiting(rig)
    operation = uuid.UUID(body["operation_id"])
    assert (await decide(approver, str(operation), "reject", reason="No")).status_code == 200

    outcome = await asyncio.to_thread(execute.run, rig.engine, operation, sleep=lambda _: None)

    assert outcome == "not_claimable"
    assert fake.submit_attempts == 0
