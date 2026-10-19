"""§26.8 - §26.10, §20.6 and the notification additions to §20.1, §20.2 and §26.1, end to end.

A caller's authority is narrowed live and the answer is watched narrowing; alerts are seeded
through ``store.fire`` (so each carries the delivery the real outbox creates); the canary
"secret" is a URL that must appear in **no** response, audit row or stored delivery.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from tests.integration.inventory_helpers import audit_rows, call
from tests.integration.test_alerts_api import Rig
from tests.integration.test_alerts_api import rig as rig
from tests.integration.test_auth_flow import (  # noqa: F401 -- fixtures are used by name
    FakeIdp,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)

CHANNELS = "/api/v1/notification-channels"
ALERTS = "/api/v1/alerts"
INTEGRATIONS = "/api/v1/integrations/status"
READ_ONLY_PATH = "/api/v1/admin/settings/read-only-mode"
CANARY = "https://hooks.example.test/services/T0/B0/canary-secret-value"


@pytest.fixture(autouse=True)
def _destinations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "hooks.example.test")
    monkeypatch.setenv("DCPSECRET_OPS_HOOK", CANARY)
    get_settings.cache_clear()


@pytest.fixture
def cleanup(migrated: Engine) -> Iterator[None]:
    """Channels and deliveries outlive a test unless removed (the schema is per session)."""
    yield
    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM notification_deliveries"))
        connection.execute(text("DELETE FROM notification_channels"))


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": f"ops-{uuid.uuid4().hex[:8]}",
        "channel_type": "SLACK",
        "destination_reference": "env://OPS_HOOK",
    }
    data.update(overrides)
    return data


async def make(rig: Rig, **overrides: Any) -> dict[str, Any]:
    response = await call(rig.client, "POST", CHANNELS, body(**overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


def delivery_for(rig: Rig, alert_id: str) -> dict[str, Any]:
    with rig.engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT * FROM notification_deliveries WHERE alert_event_id = :a"),
                {"a": alert_id},
            )
            .mappings()
            .one()
        )


def set_delivery(rig: Rig, delivery: uuid.UUID, **columns: Any) -> None:
    sets = {
        "SENT": "status='SENT', sent_at=now(), next_attempt_at=NULL, last_error=NULL",
        "FAILED": "status='FAILED', sent_at=NULL, next_attempt_at=NULL, last_error='http_404',"
        " attempt_count=1",
        "ABANDONED": "status='ABANDONED', sent_at=NULL, next_attempt_at=NULL, last_error='timeout',"
        " attempt_count=5",
        "PENDING": "status='PENDING', sent_at=NULL, next_attempt_at=now()",
    }
    with rig.engine.begin() as connection:
        connection.execute(
            text(f"UPDATE notification_deliveries SET {sets[columns['status']]} WHERE id = :i"),  # noqa: S608
            {"i": delivery},
        )


# --- §26.8 - §26.10: who may ----------------------------------------------------------------


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"])
async def test_only_an_administrator_manages_channels(rig: Rig, cleanup: None, role: str) -> None:
    channel = await make(rig)
    rig.narrow(role)
    assert (await call(rig.client, "GET", CHANNELS)).status_code == 403
    assert (await call(rig.client, "POST", CHANNELS, body())).status_code == 403
    path = f"{CHANNELS}/{channel['id']}"
    assert (await call(rig.client, "PATCH", path, {"status": "DISABLED"})).status_code == 403
    with rig.engine.connect() as connection:
        status = connection.execute(
            text("SELECT status FROM notification_channels WHERE id = :i"), {"i": channel["id"]}
        ).scalar_one()
    assert status == "ACTIVE"  # the refused PATCH changed nothing


async def test_an_unknown_query_parameter_is_refused(rig: Rig, cleanup: None) -> None:
    response = await call(rig.client, "GET", CHANNELS, params={"status": "ACTIVE"})
    assert response.status_code == 422


# --- create ---------------------------------------------------------------------------------


async def test_create_returns_the_reference_never_the_value_and_audits_field_names(
    rig: Rig, cleanup: None, capfd: pytest.CaptureFixture[str]
) -> None:
    created = await make(
        rig, routing_rules={"severity": ["CRITICAL"], "environment": ["production"]}
    )
    assert created["destination_reference"] == "env://OPS_HOOK"
    assert created["routing_rules"] == {
        "severity": ["CRITICAL"],
        "environment": ["production"],
        "target_type": [],
    }
    assert created["routing_rules_valid"] is True and created["status"] == "ACTIVE"
    assert created["deliveries"]["pending"] == 0 and created["deliveries"]["last_error"] is None
    listed = await call(rig.client, "GET", CHANNELS)
    assert listed.status_code == 200 and listed.json()["email_available"] is False

    for text_ in (created, listed.json()):
        assert "canary" not in json.dumps(text_)
    rows = [
        r
        for r in audit_rows(rig.engine, "NOTIFICATION_CHANNEL_CHANGED")
        if str(r["target_id"]) == created["id"]
    ]
    assert len(rows) == 1
    metadata = rows[0]["sanitized_metadata"]
    assert metadata["change"] == "created" and metadata["channel_type"] == "SLACK"
    assert "destination_reference" in metadata["fields"]
    assert "env://" not in json.dumps(metadata) and "canary" not in json.dumps(metadata)
    captured = capfd.readouterr()
    assert "canary-secret-value" not in captured.out + captured.err


async def test_the_destination_is_validated_without_ever_being_echoed(
    rig: Rig, cleanup: None
) -> None:
    async def refused(**overrides: Any) -> httpx.Response:
        response = await call(rig.client, "POST", CHANNELS, body(**overrides))
        assert response.status_code == 422, response.text
        assert "canary" not in response.text and "hooks.example" not in response.text
        return response

    def code(response: httpx.Response) -> str:
        return str(response.json()["error"]["details"]["fields"][0]["code"])

    # A URL is the credential itself: it belongs in the secret store, not in a request.
    misplaced = await refused(destination_reference=CANARY)
    assert code(misplaced) == "credentials_must_be_stored_in_secret_manager"
    assert code(await refused(destination_reference="not a reference")) == (
        "invalid_destination_reference"
    )
    assert code(await refused(destination_reference="env://NOT_SET_ANYWHERE")) == (
        "reference_not_resolvable"
    )
    # Resolves, but to a host that is not registered configuration.
    rig.monkeypatch.setenv("DCPSECRET_ELSEWHERE", "https://evil.example.test/x")
    assert code(await refused(destination_reference="env://ELSEWHERE")) == (
        "destination_not_allowed"
    )
    rig.monkeypatch.setenv("DCPSECRET_PLAIN_HTTP", "http://hooks.example.test/x")
    assert code(await refused(destination_reference="env://PLAIN_HTTP")) == (
        "destination_not_allowed"
    )
    # Email needs a mail server configured, and an address, not a reference.
    assert code(await refused(channel_type="EMAIL", destination_reference="ops@example.com")) == (
        "not_configured"
    )
    assert (
        code(await refused(channel_type="EMAIL", destination_reference="ops@example.com\nBcc: x"))
        == "invalid_destination_reference"
    )


async def test_email_channels_work_once_smtp_is_configured(rig: Rig, cleanup: None) -> None:
    rig.monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "hooks.example.test,smtp.example.test")
    rig.monkeypatch.setenv("DCP_SMTP_HOST", "smtp.example.test")
    rig.monkeypatch.setenv("DCP_SMTP_FROM", "dcp@example.test")
    get_settings.cache_clear()
    created = await make(rig, channel_type="EMAIL", destination_reference="ops@example.com")
    assert created["destination_reference"] == "ops@example.com"
    assert (await call(rig.client, "GET", CHANNELS)).json()["email_available"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"routing_rules": {"severity": ["URGENT"]}},
        {"routing_rules": {"unknown": []}},
        {"channel_type": "PAGER"},
        {"status": "PAUSED"},
        {"name": ""},
        {"name": "bad\nname"},
        {"name": "x" * 101},
        {"surprise": True},
        {"destination_reference": "e" * 513},
    ],
)
async def test_a_malformed_body_is_422(rig: Rig, cleanup: None, bad: dict[str, Any]) -> None:
    response = await call(rig.client, "POST", CHANNELS, body(**bad))
    assert response.status_code == 422
    assert "canary" not in response.text


async def test_channel_names_are_unique(rig: Rig, cleanup: None) -> None:
    first = await make(rig)
    duplicate = await call(rig.client, "POST", CHANNELS, body(name=first["name"]))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"


# --- update ---------------------------------------------------------------------------------


async def test_patch_edits_audits_and_cannot_change_the_type(rig: Rig, cleanup: None) -> None:
    channel = await make(rig)
    path = f"{CHANNELS}/{channel['id']}"
    renamed = await call(rig.client, "PATCH", path, {"name": "renamed", "status": "DISABLED"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "renamed" and renamed.json()["status"] == "DISABLED"
    rows = [
        r
        for r in audit_rows(rig.engine, "NOTIFICATION_CHANNEL_CHANGED")
        if str(r["target_id"]) == channel["id"] and r["sanitized_metadata"]["change"] == "updated"
    ]
    assert len(rows) == 1 and rows[0]["sanitized_metadata"]["fields"] == ["name", "status"]

    assert (await call(rig.client, "PATCH", path, {"channel_type": "EMAIL"})).status_code == 422
    assert (await call(rig.client, "PATCH", path, {"id": str(uuid.uuid4())})).status_code == 422
    assert (await call(rig.client, "PATCH", path, {})).status_code == 200  # a no-op is allowed
    assert (
        len(
            [
                r
                for r in audit_rows(rig.engine, "NOTIFICATION_CHANNEL_CHANGED")
                if str(r["target_id"]) == channel["id"]
            ]
        )
        == 2  # created, updated -- the empty patch wrote nothing
    )
    missing = f"{CHANNELS}/{uuid.uuid4()}"
    assert (await call(rig.client, "PATCH", missing, {"status": "DISABLED"})).status_code == 404


async def test_enabling_a_channel_checks_its_destination_again(rig: Rig, cleanup: None) -> None:
    channel = await make(rig, status="DISABLED")
    rig.monkeypatch.delenv("DCPSECRET_OPS_HOOK")  # the secret was removed meanwhile
    path = f"{CHANNELS}/{channel['id']}"
    refused = await call(rig.client, "PATCH", path, {"status": "ACTIVE"})
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["fields"][0]["code"] == "reference_not_resolvable"
    rig.monkeypatch.setenv("DCPSECRET_OPS_HOOK", CANARY)
    assert (await call(rig.client, "PATCH", path, {"status": "ACTIVE"})).status_code == 200


async def test_a_duplicate_name_on_patch_is_409(rig: Rig, cleanup: None) -> None:
    first, second = await make(rig), await make(rig)
    response = await call(
        rig.client, "PATCH", f"{CHANNELS}/{second['id']}", {"name": first["name"]}
    )
    assert response.status_code == 409


# --- the alert detail and the list ----------------------------------------------------------


async def test_the_alert_detail_shows_who_was_told_and_how_it_went(rig: Rig, cleanup: None) -> None:
    channel = await make(rig, name="primary")
    alert_id, _ = rig.seed(rig.staging)
    shown = (await rig.detail(alert_id)).json()
    (note,) = shown["notifications"]
    assert note["channel_name"] == "primary" and note["channel_type"] == "SLACK"
    assert note["status"] == "PENDING" and note["attempt_count"] == 0
    assert note["retryable"] is False and note["sent_at"] is None
    assert note["channel_id"] == channel["id"]

    set_delivery(rig, uuid.UUID(note["delivery_id"]), status="FAILED")
    failed = (await rig.detail(alert_id)).json()["notifications"][0]
    assert failed["status"] == "FAILED" and failed["last_error"] == "http_404"
    assert failed["retryable"] is True and failed["next_attempt_at"] is None
    assert "canary" not in json.dumps(failed) and "hooks." not in json.dumps(failed)


async def test_the_list_says_when_alerts_are_being_sent_nowhere(rig: Rig, cleanup: None) -> None:
    before = (await rig.list(limit=1)).json()["coverage"]["notification_channels_active"]
    assert before == 0
    channel = await make(rig)
    assert (await rig.list(limit=1)).json()["coverage"]["notification_channels_active"] == 1
    await call(rig.client, "PATCH", f"{CHANNELS}/{channel['id']}", {"status": "DISABLED"})
    assert (await rig.list(limit=1)).json()["coverage"]["notification_channels_active"] == 0


async def test_an_alert_the_caller_cannot_read_shows_no_deliveries(rig: Rig, cleanup: None) -> None:
    await make(rig)
    production, _ = rig.seed(rig.production)
    rig.narrow(
        "DEVOPS_ENGINEER", '{"environment":["staging"],"cloud_account":["*"],"server":["*"]}'
    )
    assert (await rig.detail(production)).status_code == 404


# --- §20.6 retry ----------------------------------------------------------------------------


async def retry(rig: Rig, alert_id: str, delivery_id: str) -> httpx.Response:
    return await call(rig.client, "POST", f"{ALERTS}/{alert_id}/notifications/{delivery_id}/retry")


@pytest.mark.parametrize("status", ["FAILED", "ABANDONED"])
async def test_retry_makes_the_delivery_due_again_with_a_fresh_budget(
    rig: Rig, cleanup: None, status: str
) -> None:
    await make(rig)
    alert_id, _ = rig.seed(rig.staging)
    row = delivery_for(rig, alert_id)
    set_delivery(rig, row["id"], status=status)

    response = await retry(rig, alert_id, str(row["id"]))
    assert response.status_code == 202, response.text
    after = response.json()
    assert after["status"] == "PENDING" and after["attempt_count"] == 0
    stored = delivery_for(rig, alert_id)
    assert stored["status"] == "PENDING" and stored["next_attempt_at"] is not None
    audited = [
        r
        for r in audit_rows(rig.engine, "NOTIFICATION_RETRY_REQUESTED")
        if str(r["target_id"]) == str(row["id"])
    ]
    assert len(audited) == 1
    assert audited[0]["sanitized_metadata"]["previous_status"] == status


@pytest.mark.parametrize("status", ["SENT", "PENDING"])
async def test_a_sent_or_waiting_delivery_is_not_retried(
    rig: Rig, cleanup: None, status: str
) -> None:
    await make(rig)
    alert_id, _ = rig.seed(rig.staging)
    row = delivery_for(rig, alert_id)
    set_delivery(rig, row["id"], status=status)
    response = await retry(rig, alert_id, str(row["id"]))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"
    assert response.json()["error"]["details"]["current_status"] == status
    assert audit_rows_for(rig, row["id"]) == []


def audit_rows_for(rig: Rig, delivery: uuid.UUID) -> list[dict[str, Any]]:
    return [
        r
        for r in audit_rows(rig.engine, "NOTIFICATION_RETRY_REQUESTED")
        if str(r["target_id"]) == str(delivery)
    ]


async def test_retrying_on_a_disabled_channel_is_refused_up_front(rig: Rig, cleanup: None) -> None:
    channel = await make(rig)
    alert_id, _ = rig.seed(rig.staging)
    row = delivery_for(rig, alert_id)
    set_delivery(rig, row["id"], status="FAILED")
    await call(rig.client, "PATCH", f"{CHANNELS}/{channel['id']}", {"status": "DISABLED"})
    response = await retry(rig, alert_id, str(row["id"]))
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "channel_disabled"
    assert delivery_for(rig, alert_id)["status"] == "FAILED"


async def test_a_delivery_of_another_alert_or_nothing_is_404(rig: Rig, cleanup: None) -> None:
    await make(rig)
    first, _ = rig.seed(rig.staging)
    second, _ = rig.seed(rig.staging)
    row = delivery_for(rig, first)
    set_delivery(rig, row["id"], status="FAILED")
    assert (await retry(rig, second, str(row["id"]))).status_code == 404
    assert (await retry(rig, first, str(uuid.uuid4()))).status_code == 404
    assert (await retry(rig, str(uuid.uuid4()), str(row["id"]))).status_code == 404
    assert delivery_for(rig, first)["status"] == "FAILED"


async def test_retry_follows_the_alerts_scope_and_permission(rig: Rig, cleanup: None) -> None:
    await make(rig)
    production, _ = rig.seed(rig.production)
    row = delivery_for(rig, production)
    set_delivery(rig, row["id"], status="FAILED")
    # Holds alert:acknowledge, but not for production.
    rig.narrow(
        "DEVOPS_ENGINEER", '{"environment":["staging"],"cloud_account":["*"],"server":["*"]}'
    )
    assert (await retry(rig, production, str(row["id"]))).status_code == 404
    # Can read alerts but not act on them.
    rig.narrow("VIEWER")
    assert (await retry(rig, production, str(row["id"]))).status_code == 403
    assert delivery_for(rig, production)["status"] == "FAILED"


async def test_two_people_retrying_at_once_get_one_winner(rig: Rig, cleanup: None) -> None:
    await make(rig)
    alert_id, _ = rig.seed(rig.staging)
    row = delivery_for(rig, alert_id)
    set_delivery(rig, row["id"], status="ABANDONED")
    results = await asyncio.gather(
        *[retry(rig, alert_id, str(row["id"])) for _ in range(4)], return_exceptions=False
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(202) == 1 and codes.count(409) == 3
    assert len(audit_rows_for(rig, row["id"])) == 1


async def test_retry_is_blocked_in_read_only_mode(rig: Rig, cleanup: None) -> None:
    await make(rig)
    alert_id, _ = rig.seed(rig.staging)
    row = delivery_for(rig, alert_id)
    set_delivery(rig, row["id"], status="FAILED")
    enabled = await call(rig.client, "POST", READ_ONLY_PATH, {"enabled": True, "reason": "test"})
    assert enabled.status_code == 200
    try:
        blocked = await retry(rig, alert_id, str(row["id"]))
        assert blocked.status_code == 423 and blocked.json()["error"]["code"] == "read_only_mode"
        assert (await call(rig.client, "GET", CHANNELS)).status_code == 200
    finally:
        await call(rig.client, "POST", READ_ONLY_PATH, {"enabled": False})
    assert delivery_for(rig, alert_id)["status"] == "FAILED"


# --- §26.1 ----------------------------------------------------------------------------------


async def rows_named(rig: Rig, prefix: str) -> list[dict[str, Any]]:
    page = (await call(rig.client, "GET", INTEGRATIONS)).json()
    return [r for r in page["integrations"] if r["name"].startswith(prefix)]


async def test_no_channels_is_one_honest_row(rig: Rig, cleanup: None) -> None:
    (row,) = await rows_named(rig, "Notification channels")
    assert row["status"] == "not_configured"
    assert row["last_error_message"] == "no_channel_registered"


async def test_each_channel_is_a_row_decided_by_what_its_deliveries_did(
    rig: Rig, cleanup: None
) -> None:
    channel = await make(rig, name="primary")
    (row,) = await rows_named(rig, "Notifications: primary")
    # Registered and never used is unproven, not healthy.
    assert row["status"] == "degraded" and row["last_error_message"] == "no_delivery_attempted_yet"
    assert not await rows_named(rig, "Notification channels")

    alert_id, _ = rig.seed(rig.staging)
    delivery = delivery_for(rig, alert_id)["id"]
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE notification_deliveries SET attempt_count = 1 WHERE id = :i"),
            {"i": delivery},
        )
    (row,) = await rows_named(rig, "Notifications: primary")
    assert row["status"] == "degraded" and row["last_error_message"] == "delivery_in_progress"

    set_delivery(rig, delivery, status="SENT")
    (row,) = await rows_named(rig, "Notifications: primary")
    assert row["status"] == "ok" and row["last_success_at"] is not None

    set_delivery(rig, delivery, status="ABANDONED")
    (row,) = await rows_named(rig, "Notifications: primary")
    assert row["status"] == "unavailable" and row["last_error_message"] == "timeout"
    assert row["last_error_at"] is not None

    await call(rig.client, "PATCH", f"{CHANNELS}/{channel['id']}", {"status": "DISABLED"})
    (row,) = await rows_named(rig, "Notifications: primary")
    assert row["status"] == "not_configured" and row["last_error_message"] == "channel_disabled"
    for r in await rows_named(rig, "Notifications"):
        assert "://" not in (r["last_error_message"] or "")


async def test_channel_health_counts_the_last_day(rig: Rig, cleanup: None) -> None:
    await make(rig, name="primary")
    sent, _ = rig.seed(rig.staging)
    failed, _ = rig.seed(rig.staging)
    waiting, _ = rig.seed(rig.staging)
    set_delivery(rig, delivery_for(rig, sent)["id"], status="SENT")
    set_delivery(rig, delivery_for(rig, failed)["id"], status="FAILED")
    health = (await call(rig.client, "GET", CHANNELS)).json()["items"][0]["deliveries"]
    assert health["pending"] == 1 and health["sent_24h"] == 1 and health["failed_24h"] == 1
    assert health["last_error"] == "http_404"
    assert health["last_success_at"] is not None and health["last_failure_at"] is not None
    assert waiting  # one alert left pending


# --- the adversarial one --------------------------------------------------------------------


async def test_a_crafted_alert_reaches_only_registered_channels_with_a_fixed_template(
    rig: Rig, cleanup: None
) -> None:
    """SECURITY.md section 21 after M3.4: an alert payload can add a delivery row for a
    *registered* channel and nothing else -- no new destination, no changed channel."""
    channel = await make(rig, name="primary")
    hostile = "<!channel> https://evil.example.test/steal?x=1 \n\nBcc: attacker@example.net"
    alert_id, _ = rig.seed(rig.staging, summary=hostile)
    with rig.engine.connect() as connection:
        destinations = connection.execute(
            text("SELECT DISTINCT channel_id FROM notification_deliveries")
        ).all()
        stored_channel = connection.execute(
            text("SELECT destination_reference, status, routing_rules FROM notification_channels")
        ).one()
    assert [d[0] for d in destinations] == [uuid.UUID(channel["id"])]
    assert stored_channel.destination_reference == "env://OPS_HOOK"
    assert delivery_for(rig, alert_id)["channel_id"] == uuid.UUID(channel["id"])
