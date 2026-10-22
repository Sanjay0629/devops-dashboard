"""The audit export, 24.3 – 24.5, end to end (M4.6, ``OPEN-112``).

Real PostgreSQL and Redis through the real application. The worker is not started: ``enqueue`` is a
recorder, and the task body (:func:`app.audit.exports.build`) is called directly, which is the
code the Celery task calls. What the broker carries is asserted separately (an id and nothing
else).

The suite's database is shared and audit rows are never deleted, so each test builds its export
over a **window that only its own events fall in** (two hours ago, to the minute) and removes the
exports it made, because a leftover ``QUEUED`` row would block the next test's request.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import asyncio
import hashlib
import inspect
import itertools
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.audit import exports
from app.audit.models import EXPORT_MAX_BYTES, EXPORT_MAX_ROWS
from tests.integration.inventory_helpers import STAGING_SCOPE, call, grant, revoke_all
from tests.integration.test_approvals_api import Actor, _second_login
from tests.integration.test_audit_api import insert
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

EXPORT = "/api/v1/audit-events/export"
#: Audit rows are never deleted and the database is shared, so each test gets its own slot: a
#: window a minute wide, an hour or so in the past, that no other test's events fall in.
_SLOTS = itertools.count(1)
_slot = {"seconds": 3600}


@pytest.fixture(autouse=True)
def _own_window() -> None:
    _slot["seconds"] = 3600 + 90 * next(_SLOTS)


def window(seconds: int = 30) -> dict[str, str]:
    """This test's window: centred on its slot, ``seconds`` either side."""
    centre = datetime.now(UTC) - timedelta(seconds=_slot["seconds"])
    return {
        "from": (centre - timedelta(seconds=seconds)).isoformat(),
        "to": (centre + timedelta(seconds=seconds)).isoformat(),
    }


def events_in_window(engine: Engine, environment: str | None, count: int = 1) -> list[str]:
    """``count`` events at the centre of :func:`window`, sharing one correlation id."""
    correlation = uuid.uuid4()
    return [
        insert(engine, environment, correlation, seconds=_slot["seconds"]) for _ in range(count)
    ]


@pytest.fixture(autouse=True)
def _no_exports_left_behind(migrated: Engine) -> Iterator[None]:
    def clear() -> None:
        with migrated.begin() as connection:
            connection.execute(text("DELETE FROM audit_exports"))

    clear()
    yield
    clear()


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    sent: list[uuid.UUID] = []

    async def record(export_id: uuid.UUID) -> None:
        sent.append(export_id)

    monkeypatch.setattr(exports, "enqueue_export", record)
    return sent


@pytest.fixture
async def admin(rig: Rig, enqueued: list[uuid.UUID]) -> Rig:
    rig.as_role("ADMINISTRATOR")
    return rig


@pytest.fixture
async def other_admin(
    rig: Rig, auth_app: tuple[httpx.AsyncClient, FakeIdp]
) -> AsyncIterator[Actor]:
    actor = await _second_login(rig, auth_app[1])
    grant(rig.engine, actor.id, "ADMINISTRATOR")
    yield actor
    revoke_all(rig.engine, actor.id)
    await actor.client.aclose()


async def ask(client: httpx.AsyncClient, key: str | None = None, **body: Any) -> httpx.Response:
    payload = {**window(), **body}
    return await call(
        client, "POST", EXPORT, payload, headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )


def row(engine: Engine, export_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(text("SELECT * FROM audit_exports WHERE id = :i"), {"i": export_id})
            .mappings()
            .one()
        )


def build(engine: Engine, export_id: str) -> str:
    return exports.build(engine, uuid.UUID(export_id))


def trail(engine: Engine, action: str, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text(
                    "SELECT * FROM audit_events WHERE action_type = :a AND target_id = :t"
                    " ORDER BY occurred_at"
                ),
                {"a": action, "t": export_id},
            ).mappings()
        ]


async def ready(admin: Rig, environment: str = "staging", count: int = 3) -> str:
    events_in_window(admin.engine, environment, count)
    accepted = await ask(admin.client)
    assert accepted.status_code == 202, accepted.text
    export_id: str = accepted.json()["export_id"]
    assert build(admin.engine, export_id) == "ready"
    return export_id


# --- the request ------------------------------------------------------------------------------


async def test_a_request_creates_a_queued_row_audits_it_and_hands_the_worker_an_id(
    admin: Rig, enqueued: list[uuid.UUID]
) -> None:
    response = await ask(admin.client)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "QUEUED" and body["idempotent_replay"] is False
    assert enqueued == [uuid.UUID(body["export_id"])]

    stored = row(admin.engine, body["export_id"])
    assert stored["status"] == "QUEUED" and stored["requested_by"] == admin.caller
    assert stored["content"] is None and stored["format"] == "jsonl"
    assert stored["expires_at"] - stored["created_at"] == timedelta(hours=24)

    [event] = trail(admin.engine, "AUDIT_EXPORT_REQUESTED", body["export_id"])
    assert event["actor_id"] == admin.caller and event["outcome"] == "SUCCESS"
    assert set(event["sanitized_metadata"]) == {"export_id", "from", "to", "format"}


async def test_a_replayed_key_returns_the_original_and_enqueues_nothing(
    admin: Rig, enqueued: list[uuid.UUID]
) -> None:
    body = window()
    headers = {"Idempotency-Key": "export-replay-0001"}
    first = await call(admin.client, "POST", EXPORT, body, headers=headers)
    assert first.status_code == 202, first.text

    replay = await call(admin.client, "POST", EXPORT, body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["export_id"] == first.json()["export_id"]
    assert replay.json()["idempotent_replay"] is True
    assert enqueued == [uuid.UUID(first.json()["export_id"])]

    # The same key with a *different* window is a conflict, not a replay.
    different = await call(admin.client, "POST", EXPORT, window(seconds=45), headers=headers)
    assert different.status_code == 409, different.text
    reason = different.json()["error"]["details"]["reason"]
    assert reason == "idempotency_key_reused_with_different_payload"


async def test_a_second_request_while_one_is_in_flight_is_refused(admin: Rig) -> None:
    assert (await ask(admin.client)).status_code == 202
    second = await ask(admin.client)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "export_in_progress"


@pytest.mark.parametrize(
    "mutate",
    [
        {"to_before_from": True},
        {"too_long": True},
        {"no_offset": True},
        {"format": "csv"},
        {"unknown": "field"},
    ],
)
async def test_a_bad_request_is_a_422(admin: Rig, mutate: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    body: dict[str, Any] = {"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()}
    if mutate.get("to_before_from"):
        body = {"from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()}
    elif mutate.get("too_long"):
        body = {"from": (now - timedelta(days=32)).isoformat(), "to": now.isoformat()}
    elif mutate.get("no_offset"):
        body = {"from": "2026-01-01T00:00:00", "to": "2026-01-02T00:00:00"}
    else:
        body.update(mutate)
    response = await call(
        admin.client, "POST", EXPORT, body, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert response.status_code == 422, response.text


async def test_thirty_one_days_exactly_is_allowed(admin: Rig) -> None:
    now = datetime.now(UTC)
    response = await call(
        admin.client,
        "POST",
        EXPORT,
        {"from": (now - timedelta(days=31)).isoformat(), "to": now.isoformat()},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202, response.text


async def test_a_request_without_an_idempotency_key_is_a_422(admin: Rig) -> None:
    response = await call(admin.client, "POST", EXPORT, window())
    assert response.status_code == 422


# --- who may ask ------------------------------------------------------------------------------


async def test_audit_read_without_admin_integrations_is_refused_and_audited(rig: Rig) -> None:
    rig.as_role("APPROVER")  # holds audit:read, not admin:integrations
    before = _denials(rig.engine, rig.caller)
    for method, path in (
        ("POST", EXPORT),
        ("GET", f"{EXPORT}s/{uuid.uuid4()}"),
        ("GET", f"{EXPORT}s/{uuid.uuid4()}/download"),
    ):
        body = window() if method == "POST" else None
        headers = {"Idempotency-Key": str(uuid.uuid4())} if method == "POST" else None
        response = await call(rig.client, method, path, body, headers=headers)
        assert response.status_code == 403, f"{method} {path}: {response.text}"
    assert _denials(rig.engine, rig.caller) == before + 3


def _denials(engine: Engine, user: uuid.UUID) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE action_type = 'AUTHZ_DENIED'"
                    " AND actor_id = :u"
                    " AND sanitized_metadata->>'permission' = 'admin:integrations'"
                ),
                {"u": user},
            ).scalar_one()
        )


@pytest.mark.parametrize("role", ["VIEWER", "DEVOPS_ENGINEER"])
async def test_a_role_without_audit_read_never_reaches_the_export(rig: Rig, role: str) -> None:
    rig.as_role(role)
    response = await call(
        rig.client, "POST", EXPORT, window(), headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert response.status_code == 403


async def test_an_unauthenticated_caller_is_refused(rig: Rig) -> None:
    anonymous = httpx.AsyncClient(transport=rig.client._transport, base_url="http://testserver")
    try:
        assert (await anonymous.get(f"{EXPORT}s/{uuid.uuid4()}")).status_code == 401
        assert (await anonymous.get(f"{EXPORT}s/{uuid.uuid4()}/download")).status_code == 401
    finally:
        await anonymous.aclose()


async def test_someone_elses_export_is_a_404_for_status_and_download(
    admin: Rig, other_admin: Actor
) -> None:
    export_id = await ready(admin)
    for path in (f"{EXPORT}s/{export_id}", f"{EXPORT}s/{export_id}/download"):
        response = await call(other_admin.client, "GET", path)
        assert response.status_code == 404, response.text
    assert row(admin.engine, export_id)["status"] == "READY"  # the stranger spent nothing


async def test_an_unknown_export_is_a_404(admin: Rig) -> None:
    for suffix in ("", "/download"):
        response = await call(admin.client, "GET", f"{EXPORT}s/{uuid.uuid4()}{suffix}")
        assert response.status_code == 404


# --- the build --------------------------------------------------------------------------------


async def test_the_file_holds_exactly_the_events_in_the_window_and_the_status_describes_it(
    admin: Rig,
) -> None:
    mine = events_in_window(admin.engine, "staging", 3)
    accepted = (await ask(admin.client)).json()
    assert build(admin.engine, accepted["export_id"]) == "ready"

    status = await call(admin.client, "GET", f"{EXPORT}s/{accepted['export_id']}")
    body = status.json()
    assert body["status"] == "READY" and body["row_count"] == 3 and body["failure_code"] is None
    assert body["retrieved_at"] is None and body["sha256"] and body["size_bytes"] > 0

    download = await call(admin.client, "GET", f"{EXPORT}s/{accepted['export_id']}/download")
    assert download.status_code == 200, download.text
    lines = download.content.decode().splitlines()
    assert {json.loads(line)["id"] for line in lines} == set(mine)
    assert hashlib.sha256(download.content).hexdigest() == body["sha256"]
    assert len(download.content) == body["size_bytes"]


async def test_the_file_is_scoped_to_what_the_requester_may_read_and_redacted(
    admin: Rig,
) -> None:
    """A staging-only Administrator gets staging events, not production and not the login."""
    staging = str(uuid.uuid4())
    with admin.engine.begin() as connection:  # a row written before a redaction rule improved
        connection.execute(
            text(
                "INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type,"
                " environment, correlation_id, outcome, sanitized_metadata)"
                " VALUES (:i, now() - :s * interval '1 second', 'SYSTEM', 'system:test',"
                " 'SCOPE_TEST', 'staging', :c, 'SUCCESS', CAST(:m AS jsonb))"
            ),
            {
                "i": staging,
                "s": _slot["seconds"],
                "c": uuid.uuid4(),
                "m": '{"note": "password=hunter2hunter2"}',
            },
        )
    production = events_in_window(admin.engine, "production", 1)[0]
    login = events_in_window(admin.engine, None, 1)[0]
    revoke_all(admin.engine, admin.caller)
    grant(admin.engine, admin.caller, "ADMINISTRATOR", STAGING_SCOPE)

    accepted = (await ask(admin.client)).json()
    assert build(admin.engine, accepted["export_id"]) == "ready"
    download = await call(admin.client, "GET", f"{EXPORT}s/{accepted['export_id']}/download")
    ids = {json.loads(line)["id"] for line in download.content.decode().splitlines()}
    assert ids == {staging} and production not in ids and login not in ids
    assert b"hunter2hunter2" not in download.content and b"[REDACTED]" in download.content


async def test_an_export_over_the_row_budget_fails_and_is_never_truncated(
    admin: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    events_in_window(admin.engine, "staging", 4)
    monkeypatch.setattr(exports, "EXPORT_MAX_ROWS", 3)
    accepted = (await ask(admin.client)).json()
    assert build(admin.engine, accepted["export_id"]) == "export_too_large"

    stored = row(admin.engine, accepted["export_id"])
    assert stored["status"] == "FAILED" and stored["failure_code"] == "export_too_large"
    assert stored["content"] is None and stored["completed_at"] is not None
    [event] = trail(admin.engine, "AUDIT_EXPORT_FAILED", accepted["export_id"])
    assert event["actor_type"] == "SYSTEM" and event["actor_id"] is None
    assert event["sanitized_metadata"]["code"] == "export_too_large"
    assert event["sanitized_metadata"]["on_behalf_of"] == str(admin.caller)
    download = await call(admin.client, "GET", f"{EXPORT}s/{accepted['export_id']}/download")
    assert download.status_code == 409 and b'"actor_label"' not in download.content
    # And the requester is free to narrow the window and ask again.
    assert (await ask(admin.client)).status_code == 202


async def test_an_export_over_the_byte_budget_fails_too(
    admin: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    events_in_window(admin.engine, "staging", 2)
    monkeypatch.setattr(exports, "EXPORT_MAX_BYTES", 100)
    accepted = (await ask(admin.client)).json()
    assert build(admin.engine, accepted["export_id"]) == "export_too_large"
    assert row(admin.engine, accepted["export_id"])["status"] == "FAILED"


async def test_the_budgets_the_code_enforces_are_the_ones_the_schema_holds() -> None:
    assert (
        (exports.EXPORT_MAX_ROWS, exports.EXPORT_MAX_BYTES)
        == (
            EXPORT_MAX_ROWS,
            EXPORT_MAX_BYTES,
        )
        == (100_000, 64 * 1024 * 1024)
    )


async def test_a_requester_demoted_between_request_and_build_gets_no_data(admin: Rig) -> None:
    events_in_window(admin.engine, "staging", 2)
    accepted = (await ask(admin.client)).json()
    revoke_all(admin.engine, admin.caller)
    grant(admin.engine, admin.caller, "APPROVER")  # audit:read, but no longer admin:integrations

    assert build(admin.engine, accepted["export_id"]) == "requester_not_authorised"
    stored = row(admin.engine, accepted["export_id"])
    assert stored["status"] == "FAILED" and stored["failure_code"] == "requester_not_authorised"
    assert stored["content"] is None and stored["row_count"] is None
    [event] = trail(admin.engine, "AUDIT_EXPORT_FAILED", accepted["export_id"])
    assert event["sanitized_metadata"]["code"] == "requester_not_authorised"


async def test_a_requester_disabled_since_asking_gets_no_data(admin: Rig) -> None:
    accepted = (await ask(admin.client)).json()
    with admin.engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET status = 'DISABLED' WHERE id = :u"), {"u": admin.caller}
        )
    try:
        assert build(admin.engine, accepted["export_id"]) == "requester_not_authorised"
    finally:
        with admin.engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET status = 'ACTIVE' WHERE id = :u"), {"u": admin.caller}
            )


async def test_a_second_delivery_of_the_task_builds_nothing(admin: Rig) -> None:
    export_id = await ready(admin)
    before = row(admin.engine, export_id)["sha256"]
    assert build(admin.engine, export_id) == "not_queued"
    assert row(admin.engine, export_id)["sha256"] == before


async def test_an_empty_window_is_a_ready_empty_file(admin: Rig) -> None:
    accepted = (await ask(admin.client)).json()  # nothing inserted near two hours ago
    assert build(admin.engine, accepted["export_id"]) == "ready"
    download = await call(admin.client, "GET", f"{EXPORT}s/{accepted['export_id']}/download")
    assert download.status_code == 200 and download.content == b""


# --- single retrieval -------------------------------------------------------------------------


async def test_the_file_leaves_once_and_the_download_is_what_is_audited(admin: Rig) -> None:
    export_id = await ready(admin)
    assert trail(admin.engine, "AUDIT_EXPORTED", export_id) == []  # building is not leaving

    first = await call(admin.client, "GET", f"{EXPORT}s/{export_id}/download")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in first.headers["content-disposition"]
    assert first.headers["cache-control"] == "no-store"

    stored = row(admin.engine, export_id)
    assert stored["status"] == "RETRIEVED" and stored["content"] is None
    assert stored["retrieved_at"] is not None

    second = await call(admin.client, "GET", f"{EXPORT}s/{export_id}/download")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "export_not_available"
    assert second.content != first.content

    [event] = trail(admin.engine, "AUDIT_EXPORTED", export_id)
    metadata = event["sanitized_metadata"]
    assert set(metadata) == {"export_id", "row_count", "size_bytes", "sha256"}
    assert metadata["sha256"] == hashlib.sha256(first.content).hexdigest()
    assert event["actor_id"] == admin.caller
    status = await call(admin.client, "GET", f"{EXPORT}s/{export_id}")
    assert status.json()["status"] == "RETRIEVED" and status.json()["retrieved_at"]


@pytest.mark.parametrize("state", ["QUEUED", "FAILED"])
async def test_an_export_that_is_not_ready_is_never_a_file(admin: Rig, state: str) -> None:
    events_in_window(admin.engine, "staging", 1)
    accepted = (await ask(admin.client)).json()
    if state == "FAILED":
        with admin.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE audit_exports SET status = 'FAILED', failure_code = 'internal_error',"
                    " completed_at = now() WHERE id = :i"
                ),
                {"i": accepted["export_id"]},
            )
    response = await call(admin.client, "GET", f"{EXPORT}s/{accepted['export_id']}/download")
    assert response.status_code == 409 and b'"actor_label"' not in response.content


async def test_an_expired_export_is_never_served_even_before_the_sweep_runs(admin: Rig) -> None:
    export_id = await ready(admin)
    with admin.engine.begin() as connection:
        connection.execute(
            text("UPDATE audit_exports SET expires_at = now() - interval '1 second' WHERE id = :i"),
            {"i": export_id},
        )
    response = await call(admin.client, "GET", f"{EXPORT}s/{export_id}/download")
    assert response.status_code == 409
    assert row(admin.engine, export_id)["status"] == "READY"  # nothing was spent by the refusal


async def test_two_downloads_racing_have_one_winner_six_times(admin: Rig) -> None:
    for _ in range(6):
        export_id = await ready(admin, count=1)
        results = await asyncio.gather(
            *(call(admin.client, "GET", f"{EXPORT}s/{export_id}/download") for _ in range(2))
        )
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        assert len(trail(admin.engine, "AUDIT_EXPORTED", export_id)) == 1


def test_a_second_session_blocks_on_the_first_and_then_finds_nothing(
    migrated: Engine,
) -> None:
    """The guarantee at the SQL level, deterministically: the loser waits, then loses.

    Removing ``status = 'READY'`` from the statement's locked sub-select makes the second session
    return a row again, and this fails.
    """
    from tests.integration.test_alert_schema import a_user
    from tests.integration.test_audit_exports_schema import export

    with migrated.begin() as connection:
        requester = a_user(connection)
        export_id = export(connection, requester, "READY")

    parameters = {"id": export_id, "requester": requester}
    outcome: dict[str, Any] = {}
    try:
        first = migrated.connect()
        transaction = first.begin()
        won = first.execute(exports._SPEND, parameters).mappings().first()
        assert won is not None and bytes(won["content"]) == b"{}\n{}\n{}\n"

        def second() -> None:
            with migrated.begin() as connection:
                outcome["row"] = connection.execute(exports._SPEND, parameters).mappings().first()
            outcome["done"] = time.monotonic()

        thread = threading.Thread(target=second)
        thread.start()
        time.sleep(0.8)
        assert thread.is_alive(), "the second download must wait for the first to finish"
        released = time.monotonic()
        transaction.commit()
        first.close()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert outcome["row"] is None and outcome["done"] >= released
    finally:
        with migrated.begin() as connection:
            connection.execute(text("DELETE FROM audit_exports WHERE id = :i"), {"i": export_id})
            connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": requester})


async def test_a_failed_audit_write_leaves_the_file_unspent(
    admin: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_id = await ready(admin)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the audit table is unavailable")

    monkeypatch.setattr("app.audit.exports.audit.record", refuse)
    try:
        response = await call(admin.client, "GET", f"{EXPORT}s/{export_id}/download")
    except RuntimeError:
        response = None
    if response is not None:
        assert response.status_code == 500 and b'"actor_label"' not in response.content
    monkeypatch.undo()
    stored = row(admin.engine, export_id)
    assert stored["status"] == "READY" and stored["content"] is not None  # nothing left


# --- expiry and the abandoned -----------------------------------------------------------------


async def test_the_sweep_expires_an_undownloaded_export_and_deletes_the_file(admin: Rig) -> None:
    export_id = await ready(admin)
    with admin.engine.begin() as connection:
        connection.execute(
            text("UPDATE audit_exports SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": export_id},
        )
    assert exports.sweep(admin.engine) == {"expired": 1, "failed": 0}
    stored = row(admin.engine, export_id)
    assert stored["status"] == "EXPIRED" and stored["content"] is None
    [event] = trail(admin.engine, "AUDIT_EXPORT_EXPIRED", export_id)
    assert event["actor_type"] == "SYSTEM" and event["actor_label"] == exports.SWEEP_LABEL
    assert event["sanitized_metadata"]["on_behalf_of"] == str(admin.caller)
    download = await call(admin.client, "GET", f"{EXPORT}s/{export_id}/download")
    assert download.status_code == 409


async def test_the_sweep_leaves_a_live_export_alone(admin: Rig) -> None:
    export_id = await ready(admin)
    assert exports.sweep(admin.engine) == {"expired": 0, "failed": 0}
    assert row(admin.engine, export_id)["status"] == "READY"


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING"])
async def test_the_sweep_fails_an_abandoned_export_so_its_requester_can_ask_again(
    admin: Rig, state: str
) -> None:
    accepted = (await ask(admin.client)).json()
    with admin.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE audit_exports SET status = :s,"
                " created_at = now() - interval '20 minutes' WHERE id = :i"
            ),
            {"s": state, "i": accepted["export_id"]},
        )
    assert (await ask(admin.client)).status_code == 409  # still blocked until the sweep runs
    assert exports.sweep(admin.engine) == {"expired": 0, "failed": 1}

    stored = row(admin.engine, accepted["export_id"])
    assert stored["status"] == "FAILED" and stored["failure_code"] == "internal_error"
    [event] = trail(admin.engine, "AUDIT_EXPORT_FAILED", accepted["export_id"])
    assert event["sanitized_metadata"]["reason"] == "abandoned"
    assert (await ask(admin.client)).status_code == 202


async def test_a_recent_queued_export_is_not_abandoned(admin: Rig) -> None:
    accepted = (await ask(admin.client)).json()
    assert exports.sweep(admin.engine) == {"expired": 0, "failed": 0}
    assert row(admin.engine, accepted["export_id"])["status"] == "QUEUED"


async def test_a_build_that_loses_to_the_sweep_stores_nothing(
    admin: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep failed a slow build while it worked: the build must not resurrect the row."""
    events_in_window(admin.engine, "staging", 1)
    accepted = (await ask(admin.client)).json()
    real = exports.read.view

    def slow(event: Any) -> Any:
        with admin.engine.begin() as connection:  # the sweep gets there first
            connection.execute(
                text(
                    "UPDATE audit_exports SET status = 'FAILED', failure_code = 'internal_error',"
                    " completed_at = now() WHERE id = :i"
                ),
                {"i": accepted["export_id"]},
            )
        return real(event)

    monkeypatch.setattr(exports.read, "view", slow)
    assert build(admin.engine, accepted["export_id"]) == "superseded"
    stored = row(admin.engine, accepted["export_id"])
    assert stored["status"] == "FAILED" and stored["content"] is None


# --- the task ---------------------------------------------------------------------------------


def _tasks(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import the task module with the *test* Redis as broker (the Celery singleton trap)."""
    from app.core.config import get_settings

    monkeypatch.setenv("DCP_REDIS_URL", redis_url)
    get_settings.cache_clear()
    from app.workers.tasks import audit as audit_tasks

    return audit_tasks


def test_the_export_task_takes_an_id_and_nothing_else(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _tasks(redis_url, monkeypatch)
    assert list(inspect.signature(tasks.export_audit.run).parameters) == ["export_id"]
    assert tasks.export_audit.name == exports.TASK_NAME
    assert tasks.export_audit.name in tasks.celery_app.tasks
    assert tasks.export_audit.queue == "maintenance"
    for bad in ("not-a-uuid", "", "'; DROP TABLE audit_events; --", None):
        assert tasks.export_audit.run(bad) == {"outcome": "bad_id"}


def test_the_sweep_is_scheduled_on_the_maintenance_queue(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _tasks(redis_url, monkeypatch)
    entry = tasks.celery_app.conf.beat_schedule["sweep-audit-exports"]
    assert entry["task"] == "dcp.audit.sweep_exports"
    assert entry["options"]["queue"] == "maintenance" and entry["schedule"] <= 60
    assert "dcp.audit.sweep_exports" in tasks.celery_app.tasks
    assert tasks.sweep_exports.queue == "maintenance"
