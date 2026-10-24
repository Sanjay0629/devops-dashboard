"""Every privileged route, driven, and its audit trail read back (M4.6, ``OPEN-114``).

PRD §4.12's acceptance criterion: *every privileged operation can be traced to its initiating
identity*. ``tests/audit_registry.py`` lists every state-changing route and what it must audit;
``tests/authz/test_audit_registry.py`` checks that list against the live routing table. This file
does the other half for the routes it can build the world for: it **calls the route** with a
correlation id of its own, then reads ``audit_events`` for exactly that id and asserts that each
expected action is there, that it succeeded, and that every ``USER`` row was written by the person
who made the call.

The routes that need a stubbed AWS layer or a seeded alert are ``elsewhere`` in the registry, each
naming the test that covers it; the static check proves that test exists and mentions the action.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.audit import exports
from app.core.config import get_settings
from tests.audit_registry import REGISTRY
from tests.integration.inventory_helpers import (
    account_id,
    application_body,
    call,
    grant,
    retire_servers,
    revoke_all,
    server_body,
)
from tests.integration.test_approvals_api import Actor, _second_login, waiting
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

Driver = Callable[["Ctx"], Awaitable[None]]


@dataclass
class Ctx:
    rig: Rig
    second: Actor
    #: The correlation id and caller of the request under test.
    correlation: uuid.UUID | None = None
    actor: uuid.UUID | None = None
    cleanups: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)

    async def act(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        by: Actor | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """The request under test: it carries a correlation id of its own."""
        self.correlation = uuid.uuid4()
        self.actor = by.id if by else self.rig.caller
        sent = {"X-Correlation-ID": str(self.correlation), **(headers or {})}
        response = await call(
            by.client if by else self.rig.client, method, path, body, headers=sent
        )
        assert 200 <= response.status_code < 300, (
            f"{method} {path}: {response.status_code} {response.text}"
        )
        return response

    async def setup(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        by: Actor | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        response = await call(
            by.client if by else self.rig.client, method, path, body, headers=headers
        )
        assert 200 <= response.status_code < 300, (
            f"{method} {path}: {response.status_code} {response.text}"
        )
        return response


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# --- the drivers ------------------------------------------------------------------------------


async def admin_user_create(c: Ctx) -> None:
    subject = str(uuid.uuid4())
    await c.act(
        "POST",
        "/api/v1/admin/users",
        {
            "email": f"{subject}@example.invalid",
            "display_name": "Completeness",
            "identity_provider_subject": subject,
        },
    )


async def _new_user(c: Ctx) -> str:
    subject = str(uuid.uuid4())
    created = await c.setup(
        "POST",
        "/api/v1/admin/users",
        {
            "email": f"{subject}@example.invalid",
            "display_name": "Completeness",
            "identity_provider_subject": subject,
        },
    )
    return str(created.json()["id"])


async def admin_user_disable(c: Ctx) -> None:
    user = await _new_user(c)
    await c.act("PATCH", f"/api/v1/admin/users/{user}", {"status": "DISABLED"})


GRANT = {"role_name": "VIEWER", "scope_definition": {"environment": ["staging"]}}


async def admin_role_grant(c: Ctx) -> None:
    user = await _new_user(c)
    await c.act("POST", f"/api/v1/admin/users/{user}/roles", GRANT)


async def admin_role_revoke(c: Ctx) -> None:
    user = await _new_user(c)
    granted = await c.setup("POST", f"/api/v1/admin/users/{user}/roles", GRANT)
    grant_id = granted.json().get("id") or granted.json().get("user_role_id")
    await c.act("DELETE", f"/api/v1/admin/users/{user}/roles/{grant_id}")


async def admin_sessions_terminate(c: Ctx) -> None:
    user = await _new_user(c)
    await c.act("POST", f"/api/v1/admin/users/{user}/sessions/terminate")


async def admin_read_only(c: Ctx) -> None:
    path = "/api/v1/admin/settings/read-only-mode"

    async def restore() -> None:
        await call(c.rig.client, "POST", path, {"enabled": False, "reason": "restore"})

    c.cleanups.append(restore)
    await c.act("POST", path, {"enabled": True, "reason": "audit completeness"})


def policy_body() -> dict[str, Any]:
    return {
        "name": unique("completeness"),
        "environment": "staging",
        "applies_to": "SERVICE_OPERATION",
        "required_approvals": 1,
        "allow_self_approval": False,
        "required_role": "APPROVER",
        "approval_window_seconds": 900,
    }


POLICIES = "/api/v1/admin/approval-policies"


async def policy_create(c: Ctx) -> None:
    created = await c.act("POST", POLICIES, policy_body())
    c.cleanups.append(
        lambda: call(c.rig.client, "DELETE", f"{POLICIES}/{created.json()['policy_id']}")
    )


async def policy_edit(c: Ctx) -> None:
    created = await c.setup("POST", POLICIES, policy_body())
    policy = created.json()["policy_id"]
    c.cleanups.append(lambda: call(c.rig.client, "DELETE", f"{POLICIES}/{policy}"))
    await c.act("PATCH", f"{POLICIES}/{policy}", {"approval_window_seconds": 1200})


async def policy_delete(c: Ctx) -> None:
    created = await c.setup("POST", POLICIES, policy_body())
    await c.act("DELETE", f"{POLICIES}/{created.json()['policy_id']}")


def _retire_later(c: Ctx, server: str) -> None:
    c.cleanups.append(lambda: retire_servers(c.rig.engine, [server]))


async def _new_server(c: Ctx) -> str:
    body = server_body(account_id(c.rig.engine), display_name="Completeness")
    created = await c.setup("POST", "/api/v1/servers", body, headers=key())
    server = str(created.json()["id"])
    _retire_later(c, server)
    return server


async def server_register(c: Ctx) -> None:
    body = server_body(account_id(c.rig.engine), display_name="Completeness")
    created = await c.act("POST", "/api/v1/servers", body, headers=key())
    _retire_later(c, str(created.json()["id"]))


async def server_edit(c: Ctx) -> None:
    await c.act("PATCH", f"/api/v1/servers/{c.rig.staging}", {"display_name": "Renamed"})


async def server_retire(c: Ctx) -> None:
    await c.act("DELETE", f"/api/v1/servers/{await _new_server(c)}")


async def _new_application(c: Ctx) -> str:
    created = await c.setup("POST", "/api/v1/applications", application_body(), headers=key())
    return str(created.json()["id"])


async def server_application_add(c: Ctx) -> None:
    application = await _new_application(c)
    await c.act(
        "POST", f"/api/v1/servers/{c.rig.staging}/applications", {"application_id": application}
    )


async def server_application_remove(c: Ctx) -> None:
    application = await _new_application(c)
    path = f"/api/v1/servers/{c.rig.staging}/applications"
    await c.setup("POST", path, {"application_id": application})
    await c.act("DELETE", f"{path}/{application}")


async def service_register(c: Ctx) -> None:
    await c.act(
        "POST",
        f"/api/v1/servers/{c.rig.staging}/services",
        {"service_name": "completeness.service"},
    )


async def service_edit(c: Ctx) -> None:
    await c.act(
        "PATCH",
        f"/api/v1/servers/{c.rig.staging}/services/web.service",
        {"monitoring_enabled": False},
    )


async def service_deregister(c: Ctx) -> None:
    await c.act("DELETE", f"/api/v1/servers/{c.rig.staging}/services/bare.service")


async def application_register(c: Ctx) -> None:
    await c.act("POST", "/api/v1/applications", application_body(), headers=key())


async def application_edit(c: Ctx) -> None:
    application = await _new_application(c)
    await c.act("PATCH", f"/api/v1/applications/{application}", {"description": "Documented."})


async def application_archive(c: Ctx) -> None:
    await c.act("DELETE", f"/api/v1/applications/{await _new_application(c)}")


def _remove_application_later(c: Ctx, application: str) -> None:
    """Targets and the application, so a committed row is not left for other suites to count."""

    def remove() -> None:
        with c.rig.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM deployments WHERE application_id = :a"),
                {"a": uuid.UUID(application)},
            )
            connection.execute(
                text("DELETE FROM deployment_targets WHERE application_id = :a"),
                {"a": uuid.UUID(application)},
            )
            connection.execute(
                text("DELETE FROM applications WHERE id = :a"), {"a": uuid.UUID(application)}
            )

    c.cleanups.append(remove)


async def _new_target(c: Ctx) -> str:
    application = await _new_application(c)
    _remove_application_later(c, application)
    created = await c.setup(
        "POST",
        "/api/v1/deployment-targets",
        {
            "application_id": application,
            "environment": "staging",
            "pipeline_reference": "team/completeness",
            "parameter_template": {"slots": {"GIT_REF": "ref"}},
            "allowed_ref_patterns": ["^main$"],
        },
        headers=key(),
    )
    return str(created.json()["id"])


async def deployment_target_register(c: Ctx) -> None:
    application = await _new_application(c)
    _remove_application_later(c, application)
    await c.act(
        "POST",
        "/api/v1/deployment-targets",
        {
            "application_id": application,
            "environment": "staging",
            "pipeline_reference": "team/completeness",
        },
        headers=key(),
    )


async def deployment_target_edit(c: Ctx) -> None:
    target = await _new_target(c)
    await c.act("PATCH", f"/api/v1/deployment-targets/{target}", {"status": "DISABLED"})


async def deployment_target_retire(c: Ctx) -> None:
    await c.act("DELETE", f"/api/v1/deployment-targets/{await _new_target(c)}")


async def _deployable(c: Ctx) -> str:
    """An application with a staging target, a Jenkins configured (never called) and no worker."""
    from app.deployments import lifecycle

    for name, value in {
        "DCP_JENKINS_URL": "http://mock-jenkins:8080",
        "DCP_JENKINS_USER": "dcp",
        "DCP_JENKINS_TOKEN_REFERENCE": "env://JENKINS_TOKEN",
        "DCPSECRET_JENKINS_TOKEN": "mock-jenkins-token-for-completeness",
        "DCP_SSRF_HOST_ALLOWLIST": "hooks.example.test,mock-jenkins",
        "DCP_SSRF_INSECURE_HOSTS": "mock-jenkins",
        "DCP_SSRF_ALLOWED_NETWORKS": "10.0.0.0/8",
    }.items():
        c.rig.monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    async def nothing(_deployment: uuid.UUID) -> None:
        return None

    c.rig.monkeypatch.setattr(lifecycle, "enqueue_execution", nothing)
    application = await _new_application(c)
    _remove_application_later(c, application)
    await c.setup(
        "POST",
        "/api/v1/deployment-targets",
        {
            "application_id": application,
            "environment": "staging",
            "pipeline_reference": "team/completeness",
            "parameter_template": {"slots": {"GIT_REF": "ref"}},
            "allowed_ref_patterns": ["^main$"],
        },
        headers=key(),
    )
    return application


async def deployment_request(c: Ctx) -> None:
    application = await _deployable(c)
    await c.act(
        "POST",
        "/api/v1/deployments",
        {"application_id": application, "environment": "staging", "ref": "main"},
        headers=key(),
    )


async def deployment_cancel(c: Ctx) -> None:
    application = await _deployable(c)
    created = await c.setup(
        "POST",
        "/api/v1/deployments",
        {"application_id": application, "environment": "staging", "ref": "main"},
        headers=key(),
    )
    await c.act("POST", f"/api/v1/deployments/{created.json()['deployment_id']}/cancel")


async def operation_request(c: Ctx) -> None:
    await c.act(
        "POST",
        c.rig.path(c.rig.staging, "web.service"),
        {"action": "restart", "reason": "CR-0000"},
        headers=key(),
    )


async def _gated_request(c: Ctx) -> str:
    body = await waiting(c.rig)
    return str(body["operation_id"])


async def operation_cancel(c: Ctx) -> None:
    operation = await _gated_request(c)
    await c.act("POST", f"/api/v1/operations/{operation}/cancel")


async def operation_approve(c: Ctx) -> None:
    operation = await _gated_request(c)
    await c.act("POST", f"/api/v1/operations/{operation}/approve", by=c.second)


async def operation_reject(c: Ctx) -> None:
    operation = await _gated_request(c)
    await c.act(
        "POST", f"/api/v1/operations/{operation}/reject", {"reason": "not today"}, by=c.second
    )


CHANNELS = "/api/v1/notification-channels"


def channel_body() -> dict[str, Any]:
    return {
        "name": unique("ops"),
        "channel_type": "SLACK",
        "destination_reference": "env://OPS_HOOK",
    }


async def channel_create(c: Ctx) -> None:
    await c.act("POST", CHANNELS, channel_body())


async def channel_edit(c: Ctx) -> None:
    created = await c.setup("POST", CHANNELS, channel_body())
    await c.act("PATCH", f"{CHANNELS}/{created.json()['id']}", {"name": unique("renamed")})


async def audit_export_request(c: Ctx) -> None:
    now = datetime.now(UTC)
    await c.act(
        "POST",
        "/api/v1/audit-events/export",
        {"from": (now - timedelta(hours=1)).isoformat(), "to": now.isoformat()},
        headers=key(),
    )


async def logout(c: Ctx) -> None:
    await c.act("POST", "/api/v1/auth/logout", by=c.second)


DRIVERS: dict[tuple[str, str], Driver] = {
    ("POST", "/api/v1/admin/users"): admin_user_create,
    ("PATCH", "/api/v1/admin/users/{user_id}"): admin_user_disable,
    ("POST", "/api/v1/admin/users/{user_id}/roles"): admin_role_grant,
    ("DELETE", "/api/v1/admin/users/{user_id}/roles/{user_role_id}"): admin_role_revoke,
    ("POST", "/api/v1/admin/users/{user_id}/sessions/terminate"): admin_sessions_terminate,
    ("POST", "/api/v1/admin/settings/read-only-mode"): admin_read_only,
    ("POST", "/api/v1/admin/approval-policies"): policy_create,
    ("PATCH", "/api/v1/admin/approval-policies/{policy_id}"): policy_edit,
    ("DELETE", "/api/v1/admin/approval-policies/{policy_id}"): policy_delete,
    ("POST", "/api/v1/servers"): server_register,
    ("PATCH", "/api/v1/servers/{server_id}"): server_edit,
    ("DELETE", "/api/v1/servers/{server_id}"): server_retire,
    ("POST", "/api/v1/servers/{server_id}/applications"): server_application_add,
    ("DELETE", "/api/v1/servers/{server_id}/applications/{application_id}"): (
        server_application_remove
    ),
    ("POST", "/api/v1/servers/{server_id}/services"): service_register,
    ("PATCH", "/api/v1/servers/{server_id}/services/{service_name}"): service_edit,
    ("DELETE", "/api/v1/servers/{server_id}/services/{service_name}"): service_deregister,
    ("POST", "/api/v1/deployment-targets"): deployment_target_register,
    ("PATCH", "/api/v1/deployment-targets/{target_id}"): deployment_target_edit,
    ("DELETE", "/api/v1/deployment-targets/{target_id}"): deployment_target_retire,
    ("POST", "/api/v1/deployments"): deployment_request,
    ("POST", "/api/v1/deployments/{deployment_id}/cancel"): deployment_cancel,
    ("POST", "/api/v1/applications"): application_register,
    ("PATCH", "/api/v1/applications/{application_id}"): application_edit,
    ("DELETE", "/api/v1/applications/{application_id}"): application_archive,
    ("POST", "/api/v1/servers/{server_id}/services/{service_name}/operations"): operation_request,
    ("POST", "/api/v1/operations/{operation_id}/cancel"): operation_cancel,
    ("POST", "/api/v1/operations/{operation_id}/approve"): operation_approve,
    ("POST", "/api/v1/operations/{operation_id}/reject"): operation_reject,
    ("POST", "/api/v1/notification-channels"): channel_create,
    ("PATCH", "/api/v1/notification-channels/{channel_id}"): channel_edit,
    ("POST", "/api/v1/audit-events/export"): audit_export_request,
    ("POST", "/api/v1/auth/logout"): logout,
}

DRIVEN = sorted(key for key, entry in REGISTRY.items() if entry.kind == "driven")


def test_every_driven_route_has_a_driver_and_every_driver_a_route() -> None:
    assert set(DRIVERS) == set(DRIVEN), sorted(set(DRIVERS) ^ set(DRIVEN))


# --- the test ---------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _destinations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "hooks.example.test")
    monkeypatch.setenv("DCPSECRET_OPS_HOOK", "https://hooks.example.test/services/T0/B0/x")
    get_settings.cache_clear()


@pytest.fixture
async def ctx(
    rig: Rig,
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Ctx]:
    async def _enqueue_nothing(_export: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(exports, "enqueue_export", _enqueue_nothing)
    rig.as_role("ADMINISTRATOR")
    second = await _second_login(rig, auth_app[1])
    grant(rig.engine, second.id, "APPROVER")
    context = Ctx(rig, second)
    yield context
    for cleanup in reversed(context.cleanups):
        outcome = cleanup()
        if outcome is not None:
            await outcome
    with rig.engine.begin() as connection:
        # Rows other suites count must not outlive this one: exports, channels, associations.
        connection.execute(text("DELETE FROM audit_exports"))
        connection.execute(text("DELETE FROM notification_deliveries"))
        connection.execute(text("DELETE FROM notification_channels"))
        connection.execute(
            text("DELETE FROM application_servers WHERE server_id = ANY(:ids)"),
            {"ids": [uuid.UUID(rig.staging), uuid.UUID(rig.production)]},
        )
    revoke_all(rig.engine, second.id)
    await second.client.aclose()


def _events(engine: Engine, correlation: uuid.UUID) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT action_type, actor_type, actor_id, actor_label, outcome"
                    " FROM audit_events WHERE correlation_id = :c"
                ),
                {"c": correlation},
            ).mappings()
        ]


@pytest.mark.parametrize("route", DRIVEN, ids=[f"{m} {p}" for m, p in DRIVEN])
async def test_a_privileged_route_leaves_its_events_attributed_to_the_caller(
    ctx: Ctx, route: tuple[str, str]
) -> None:
    await DRIVERS[route](ctx)
    assert ctx.correlation is not None and ctx.actor is not None

    events = _events(ctx.rig.engine, ctx.correlation)
    expected = set(REGISTRY[route].actions)
    written = {event["action_type"] for event in events}
    assert expected <= written, f"{route}: wrote {sorted(written)}, expected {sorted(expected)}"
    for event in events:
        if event["action_type"] in expected:
            assert event["outcome"] == "SUCCESS", event
    # Traceable to the initiating identity: every human row names the person who made the call.
    people = [event for event in events if event["actor_type"] == "USER"]
    assert people, f"{route}: no event names the caller"
    assert {event["actor_id"] for event in people} == {ctx.actor}, people
    # And a system actor, where there is one, is named rather than anonymous.
    assert all(event["actor_label"] for event in events)
