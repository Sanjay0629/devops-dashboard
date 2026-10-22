"""What the audit trail is shown to guarantee (M4.6, ``OPEN-113`` – ``OPEN-115``, PRD §4.12).

Four claims, each tested for what it is and no more:

* **A failed audit write fails the request and changes nothing** -- the converse of "every
  privileged route leaves an event" (SECURITY §18: a committed change always has its event).
* **No secret in any audit row** -- a canary sweep. Free text that users type into privileged
  requests (a reason, a description) is planted with strings no redaction rule would catch, so a
  code path that stored it verbatim would show; then *every column of every row written* is read
  back, and so is an export of them.
* **Immutability, for the role the application uses** -- ``UPDATE`` / ``DELETE`` / ``TRUNCATE``
  refused through the application's own engines. **Not** shown: that a least-privilege role would
  refuse the same (there is none until M6.2), and a partition ``DETACH`` is *not* blocked by the
  database today -- see the strict ``xfail`` below, which turns red the day it is.
* **Nothing in the application issues a write to the trail** other than ``INSERT`` -- a source
  gate, because the database alone cannot be the only thing standing between a bug and the record.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from app.audit import exports
from app.core.config import get_settings
from app.db.session import get_engine, get_sync_engine
from tests.integration.inventory_helpers import application_body, call, grant, revoke_all
from tests.integration.test_approvals_api import Actor, _second_login
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

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


# --- a failed audit write fails the request --------------------------------------------------


async def test_a_failed_audit_write_fails_the_request_and_leaves_the_state_unchanged(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 10, from the other side: no change without its event."""
    rig.as_role("ADMINISTRATOR")
    path = f"/api/v1/servers/{rig.staging}/services"

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the audit table is unavailable")

    monkeypatch.setattr("app.audit.service.record", refuse)
    try:
        response = await call(rig.client, "POST", path, {"service_name": "never.service"})
    except RuntimeError:  # the test transport re-raises server errors; either way, no change
        response = None
    monkeypatch.undo()
    if response is not None:
        assert response.status_code == 500, response.text

    with rig.engine.connect() as connection:
        found = connection.execute(
            text("SELECT count(*) FROM services WHERE service_name = 'never.service'")
        ).scalar_one()
    assert found == 0, "the service was registered although its audit row could not be written"


# --- no secret in any audit row ---------------------------------------------------------------


def _canary(label: str) -> str:
    """Secret-shaped enough to matter, and unlike anything a redaction pattern would mask, so a
    verbatim copy anywhere cannot be hidden by the net underneath."""
    return f"canary-{label}-{uuid.uuid4().hex}"


@pytest.fixture
async def second(rig: Rig, auth_app: tuple[httpx.AsyncClient, FakeIdp]) -> AsyncIterator[Actor]:
    actor = await _second_login(rig, auth_app[1])
    grant(rig.engine, actor.id, "APPROVER")
    yield actor
    revoke_all(rig.engine, actor.id)
    await actor.client.aclose()


def _everything_written_since(engine: Engine, since: datetime) -> str:
    """Every column of every audit row written since ``since``, as one string."""
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT row_to_json(e)::text FROM audit_events e WHERE occurred_at >= :s"),
            {"s": since},
        )
        return "\n".join(row[0] for row in rows)


async def test_free_text_typed_into_privileged_requests_reaches_no_audit_row_or_export(
    rig: Rig,
    second: Actor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "hooks.example.test")
    secret_url = f"https://hooks.example.test/services/{_canary('webhook')}"
    monkeypatch.setenv("DCPSECRET_OPS_HOOK", secret_url)
    get_settings.cache_clear()

    async def no_enqueue(_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(exports, "enqueue_export", no_enqueue)

    started = datetime.now(UTC) - timedelta(seconds=5)
    reasons = {name: _canary(name) for name in ("request", "rejection", "channel-name")}
    #: Two fields **are** recorded by design: an incident switch must say why (SECURITY §27), and
    #: an inventory edit audits what changed, before and after. So the claim for them is the
    #: weaker, true one: a secret-shaped value typed into either is masked on the way in.
    switch_secret = _canary("switch")
    description_secret = _canary("description")
    rig.as_role("ADMINISTRATOR")

    # An operation request with a free-text reason, rejected with another.
    asked = await rig.ask(rig.production, "gated.service", reason=reasons["request"])
    assert asked.status_code == 202, asked.text
    rejected = await call(
        second.client,
        "POST",
        f"/api/v1/operations/{asked.json()['operation_id']}/reject",
        {"reason": reasons["rejection"]},
    )
    assert rejected.status_code == 200, rejected.text

    # The incident switch with a reason; an application edit with a description.
    path = "/api/v1/admin/settings/read-only-mode"
    on = await call(
        rig.client,
        "POST",
        path,
        {"enabled": True, "reason": f"investigating, password={switch_secret}"},
    )
    assert on.status_code == 200, on.text
    off = await call(rig.client, "POST", path, {"enabled": False, "reason": "restore"})
    assert off.status_code == 200, off.text
    created = await call(
        rig.client,
        "POST",
        "/api/v1/applications",
        application_body(),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    edited = await call(
        rig.client,
        "PATCH",
        f"/api/v1/applications/{created.json()['id']}",
        {"description": f"owner notes, token={description_secret}"},
    )
    assert edited.status_code == 200, edited.text

    # A notification channel whose destination is a secret, resolved only at send time.
    channel = await call(
        rig.client,
        "POST",
        "/api/v1/notification-channels",
        {
            "name": reasons["channel-name"],
            "channel_type": "SLACK",
            "destination_reference": "env://OPS_HOOK",
        },
    )
    assert channel.status_code == 201, channel.text

    # And the trail is read, filtered, and exported.
    await call(rig.client, "GET", "/api/v1/audit-events", params={"actor_id": str(rig.caller)})
    window = {
        "from": (started - timedelta(minutes=1)).isoformat(),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }
    requested = await call(
        rig.client,
        "POST",
        "/api/v1/audit-events/export",
        window,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert requested.status_code == 202, requested.text
    export_id = requested.json()["export_id"]
    assert exports.build(rig.engine, uuid.UUID(export_id)) == "ready"
    download = await call(rig.client, "GET", f"/api/v1/audit-events/exports/{export_id}/download")
    assert download.status_code == 200

    written = _everything_written_since(rig.engine, started)
    assert "SERVICE_OPERATION_REQUESTED" in written and "AUDIT_QUERIED" in written
    assert "READ_ONLY_MODE_CHANGED" in written and "investigating" in written  # recorded...
    assert "owner notes" in written  # the change is recorded, before and after...
    for masked in (switch_secret, description_secret):  # ...with anything secret-shaped masked
        assert masked not in written and masked not in download.text
    for label, canary in {**reasons, "destination": secret_url}.items():
        assert canary not in written, f"the {label} reached an audit row"
        assert canary not in download.text, f"the {label} reached the export"
    with rig.engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_exports"))
        connection.execute(
            text("DELETE FROM notification_channels WHERE name = :n"),
            {"n": reasons["channel-name"]},
        )


async def test_an_audit_query_records_which_filters_were_used_and_never_what_was_found(
    rig: Rig,
) -> None:
    rig.as_role("ADMINISTRATOR")
    marker = _canary("marker")
    insert = text(
        "INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type,"
        " environment, correlation_id, outcome, sanitized_metadata)"
        " VALUES (:i, now(), 'SYSTEM', 'system:test', 'SCOPE_TEST', 'staging', :c, 'SUCCESS',"
        " CAST(:m AS jsonb))"
    )
    correlation = uuid.uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            insert, {"i": uuid.uuid4(), "c": correlation, "m": f'{{"finding": "{marker}"}}'}
        )
    response = await call(
        rig.client, "GET", "/api/v1/audit-events", params={"correlation_id": str(correlation)}
    )
    assert marker in response.text  # the reader is shown it...
    with rig.engine.connect() as connection:
        queried = connection.execute(
            text(
                "SELECT row_to_json(e)::text FROM audit_events e WHERE action_type ="
                " 'AUDIT_QUERIED' AND actor_id = :a ORDER BY occurred_at DESC, id DESC LIMIT 1"
            ),
            {"a": rig.caller},
        ).scalar_one()
    assert marker not in queried  # ...and the record of showing it does not hold it
    assert str(correlation) in queried  # only the id that was asked for


# --- immutability, for the role the application uses -----------------------------------------

INSERT_EVENT = text(
    "INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type,"
    " correlation_id, outcome, sanitized_metadata)"
    " VALUES (:i, now(), 'SYSTEM', 'system:test', 'IMMUTABILITY_TEST', :c, 'SUCCESS', '{}'::jsonb)"
)
ATTEMPTS = [
    "UPDATE audit_events SET outcome = 'FAILURE' WHERE id = :i",
    "UPDATE audit_events SET actor_label = 'someone else' WHERE id = :i",
    "DELETE FROM audit_events WHERE id = :i",
    "TRUNCATE audit_events",
]


@pytest.mark.parametrize("statement", ATTEMPTS)
def test_the_applications_sync_engine_cannot_alter_or_remove_an_event(
    migrated: Engine, statement: str
) -> None:
    """The engine the workers use. The test database role is a superuser or the owner, which
    privileges alone would not stop -- the triggers do (DATABASE.md §8)."""
    engine = get_sync_engine()
    event = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(INSERT_EVENT, {"i": event, "c": uuid.uuid4()})
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(text(statement), {"i": event} if ":i" in statement else {})
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT outcome FROM audit_events WHERE id = :i"), {"i": event}
            ).scalar_one()
            == "SUCCESS"
        )


@pytest.mark.parametrize("statement", ATTEMPTS)
async def test_the_applications_request_engine_cannot_alter_or_remove_an_event(
    migrated: Engine, statement: str
) -> None:
    """The engine the API uses for every request."""
    engine = get_engine()
    event = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(INSERT_EVENT, {"i": event, "c": uuid.uuid4()})
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as connection:
            await connection.execute(text(statement), {"i": event} if ":i" in statement else {})
    async with engine.connect() as connection:
        outcome = (
            await connection.execute(
                text("SELECT outcome FROM audit_events WHERE id = :i"), {"i": event}
            )
        ).scalar_one()
    assert outcome == "SUCCESS"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Detaching or dropping an audit partition is how retention is meant to work (export, "
        "verify, DROP PARTITION), so the database does not forbid it today: the application role "
        "is the table's owner. M6.2 separates a least-privilege role from the migration owner. "
        "When that lands this starts to pass, strict=True turns that red, and the marker comes off."
    ),
)
def test_detaching_an_audit_partition_is_refused(connection: Connection) -> None:
    partition = connection.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid"
            " JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'audit_events' LIMIT 1"
        )
    ).scalar_one()
    with pytest.raises(DBAPIError):
        connection.execute(text(f"ALTER TABLE audit_events DETACH PARTITION {partition}"))


# --- nothing in the application writes to the trail except INSERT ----------------------------

#: A write to ``audit_events`` other than an INSERT, in SQL text or in ORM spelling.
WRITE = re.compile(
    r"(?is)\b(update|delete\s+from|truncate(?:\s+table)?|detach\s+partition|drop\s+table"
    r"|alter\s+table)\s+(?:only\s+)?(?:public\.)?audit_events\b"
    r"|\b(?:update|delete)\(\s*AuditEvent\b"
    r"|AuditEvent\.__table__\.(?:update|delete)"
    r"|\.delete\(\s*(?:event|audit_event)\s*\)"
)


def test_no_application_code_issues_anything_but_an_insert_against_the_trail() -> None:
    hits = [
        f"{path.relative_to(APP_ROOT)}:{text_.count(chr(10), 0, match.start()) + 1}: "
        f"{match.group(0)[:60]!r}"
        for path in sorted(APP_ROOT.rglob("*.py"))
        for text_ in [path.read_text(encoding="utf-8")]
        for match in WRITE.finditer(text_)
    ]
    assert not hits, hits


def test_the_gate_catches_what_it_is_looking_for() -> None:
    """A gate that matches nothing proves nothing."""
    for bad in (
        "UPDATE audit_events SET outcome = 'x'",
        "delete from audit_events",
        "TRUNCATE audit_events",
        "ALTER TABLE audit_events DETACH PARTITION p",
        "session.execute(update(AuditEvent).values(outcome='x'))",
        "AuditEvent.__table__.delete()",
    ):
        assert WRITE.search(bad), bad
    for fine in (
        "INSERT INTO audit_events (id) VALUES (1)",
        "SELECT * FROM audit_events",
        "select(AuditEvent)",
        "UPDATE audit_exports SET status = 'READY'",
    ):
        assert not WRITE.search(fine), fine
