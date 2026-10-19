"""Endpoints 17.1 – 17.3 end to end (M4.2, ``OPEN-67`` - ``OPEN-77``).

Everything here runs against real PostgreSQL and Redis through the real application. The adapter
is the test-only ``ScriptedAdapter`` registered through the registry's seam: there is no real
provider, so these tests prove the request path and nothing about any cloud. The worker is not
involved -- ``enqueue_execution`` is replaced by a recorder, and commit 4 tests the task.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import redis
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.operations import service as operations_service
from app.providers import registry
from app.providers.capabilities import CapabilitySet, Support
from app.providers.types import ServiceAction
from tests.integration.inventory_helpers import (
    FULL_SCOPE,
    STAGING_SCOPE,
    account_id,
    audit_rows,
    call,
    grant,
    register,
    retire_servers,
    revoke_all,
    user_id,
)
from tests.integration.test_auth_flow import (  # noqa: F401 -- fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)
from tests.providers.scripted import ScriptedAdapter

POLICY = "test-ops-all"


class NoRestartAdapter(ScriptedAdapter):
    """Supports start and stop, and says plainly that this provider cannot restart."""

    def capabilities(self) -> CapabilitySet:
        base = super().capabilities()
        actions = dict(base.actions)
        actions[ServiceAction.RESTART] = Support.UNSUPPORTED
        return CapabilitySet(
            base.inventory_sync,
            base.connectivity_check,
            actions,
            base.async_handle,
            base.concurrency,
            base.latency,
        )


@dataclass
class Rig:
    client: httpx.AsyncClient
    engine: Engine
    caller: uuid.UUID
    redis_url: str
    monkeypatch: pytest.MonkeyPatch
    staging: str
    production: str
    enqueued: list[uuid.UUID] = field(default_factory=list)
    real_enqueue: Any = None

    def path(self, server: str, unit: str = "web.service") -> str:
        return f"/api/v1/servers/{server}/services/{unit}/operations"

    async def ask(
        self,
        server: str | None = None,
        unit: str = "web.service",
        *,
        action: str = "restart",
        key: str | None = None,
        reason: str | None = "CR-0000",
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"action": action, **(extra or {})}
        if reason is not None:
            body["reason"] = reason
        sent = {"Idempotency-Key": key or str(uuid.uuid4()), **(headers or {})}
        return await call(
            self.client, "POST", self.path(server or self.staging, unit), body, headers=sent
        )

    def as_role(self, role: str, scope: str = FULL_SCOPE) -> None:
        revoke_all(self.engine, self.caller)
        grant(self.engine, self.caller, role, scope)

    def rows(self, **where: Any) -> list[dict[str, Any]]:
        clause = " AND ".join(f"{column} = :{column}" for column in where) or "true"
        with self.engine.connect() as connection:
            result = connection.execute(
                text(f"SELECT * FROM operations WHERE {clause} ORDER BY created_at"),  # noqa: S608
                where,
            )
            return [dict(row) for row in result.mappings()]

    def service_id(self, server: str, unit: str) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.execute(
                    text("SELECT id FROM services WHERE server_id = :s AND service_name = :n"),
                    {"s": server, "n": unit},
                ).scalar_one()
            )

    def audit(self, action: str, **match: Any) -> list[dict[str, Any]]:
        rows = audit_rows(self.engine, action)
        return [r for r in rows if all(str(r.get(k)) == str(v) for k, v in match.items())]

    def set_status(self, operation_id: str, status: str) -> None:
        terminal = status in ("FAILED", "SUCCEEDED", "TIMED_OUT", "CANCELLED")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE operations SET execution_status = :s, started_at = now(),"
                    " completed_at = CASE WHEN :done THEN now() END,"
                    " observed_state_after = CASE WHEN :ok THEN 'ACTIVE' END,"
                    " observed_state_before = 'INACTIVE'"
                    " WHERE id = :i"
                ),
                {"s": status, "done": terminal, "ok": status == "SUCCEEDED", "i": operation_id},
            )

    def foreign_operation(self, server: str, unit: str, *, status: str = "PENDING") -> str:
        """An operation requested by somebody else (a second user), for visibility tests."""
        other = uuid.uuid4()
        operation = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, identity_provider_issuer,"
                    " identity_provider_subject, status) VALUES (:i, :e, 'Someone Else',"
                    " 'https://idp.invalid', :s, 'ACTIVE')"
                ),
                {"i": other, "e": f"{other}@example.invalid", "s": str(other)},
            )
            connection.execute(
                text(
                    "INSERT INTO operations (id, target_server_id, target_service_id, environment,"
                    " action_type, requested_by, approval_status, execution_status,"
                    " idempotency_key, correlation_id, provider, deadline_at, completed_at)"
                    " SELECT :o, s.server_id, s.id, v.environment, 'restart', :u, 'NOT_REQUIRED',"
                    " :st, :k, :c, 'scripted', now() + interval '300 seconds',"
                    " CASE WHEN :done THEN now() END"
                    " FROM services s JOIN servers v ON v.id = s.server_id"
                    " WHERE s.server_id = :sv AND s.service_name = :n"
                ),
                {
                    "o": operation,
                    "u": other,
                    "st": status,
                    "done": status in ("FAILED", "SUCCEEDED", "TIMED_OUT", "CANCELLED"),
                    "k": str(uuid.uuid4()),
                    "c": uuid.uuid4(),
                    "sv": server,
                    "n": unit,
                },
            )
        return str(operation)

    def reset_rate_limit(self) -> None:
        client = redis.Redis.from_url(self.redis_url)
        try:
            client.delete(f"dcp:ratelimit:service_operation:{self.caller}")
        finally:
            client.close()

    def spent(self) -> int:
        client = redis.Redis.from_url(self.redis_url)
        try:
            value = client.get(f"dcp:ratelimit:service_operation:{self.caller}")
            return int(value) if value is not None else 0  # type: ignore[arg-type]
        finally:
            client.close()


def _ensure_policy(engine: Engine) -> str:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO service_action_policies (id, name, allowed_actions,"
                " approval_policy_id, max_concurrent_operations, operation_timeout_seconds,"
                " created_at, updated_at)"
                " VALUES (gen_random_uuid(), :n, ARRAY['start','stop','restart']::varchar[],"
                " NULL, 1, 300, now(), now()) ON CONFLICT (name) DO NOTHING"
            ),
            {"n": POLICY},
        )
        return str(
            connection.execute(
                text("SELECT id FROM service_action_policies WHERE name = :n"), {"n": POLICY}
            ).scalar_one()
        )


def _named_policy(engine: Engine, name: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT id FROM service_action_policies WHERE name = :n"), {"n": name}
            ).scalar_one()
        )


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Demonstration mode off (the suite's environment has it on) and a fake registered."""
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setitem(registry._FACTORIES, "ON_PREM", ScriptedAdapter)
    yield
    get_settings.cache_clear()


@pytest.fixture
async def rig(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    redis_url: str,  # noqa: F811
    adapter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Rig]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    account = account_id(migrated)
    all_policy = _ensure_policy(migrated)
    gated_policy = _named_policy(migrated, "restart-only")
    read_only_policy = _named_policy(migrated, "read-only")

    ids: list[str] = []
    for display, environment in (("Staging", "staging"), ("Production", "production")):
        response = await register(client, account, display_name=display, environment=environment)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    staging, production = ids
    with migrated.begin() as connection:
        connection.execute(
            text("UPDATE servers SET status = 'MANAGED' WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(i) for i in ids]},
        )
    services = [
        (staging, "web.service", all_policy),
        (staging, "restart.service", gated_policy),
        (staging, "ro.service", read_only_policy),
        (staging, "bare.service", None),
        (production, "api.service", all_policy),
        (production, "gated.service", gated_policy),
    ]
    for server, unit, policy in services:
        body: dict[str, Any] = {"service_name": unit}
        if policy is not None:
            body["action_policy_id"] = policy
        created = await call(client, "POST", f"/api/v1/servers/{server}/services", body)
        assert created.status_code == 201, created.text

    built = Rig(client, migrated, caller, redis_url, monkeypatch, staging, production)
    built.real_enqueue = operations_service.enqueue_execution

    async def record(operation_id: uuid.UUID) -> None:
        built.enqueued.append(operation_id)

    monkeypatch.setattr(operations_service, "enqueue_execution", record)
    built.as_role("DEVOPS_ENGINEER")
    built.reset_rate_limit()
    yield built
    built.reset_rate_limit()
    revoke_all(migrated, caller)
    retire_servers(migrated, ids)


def denied(rig: Rig, service_id: str) -> list[dict[str, Any]]:
    return rig.audit("SERVICE_OPERATION_DENIED", target_id=service_id)


# --- 17.1: the happy path ---------------------------------------------------------------------


async def test_a_permitted_restart_creates_one_pending_operation_and_hands_the_worker_its_id(
    rig: Rig,
) -> None:
    response = await rig.ask()
    assert response.status_code == 202, response.text
    body = response.json()

    assert body["execution_status"] == "PENDING" and body["approval_status"] == "NOT_REQUIRED"
    assert body["action_type"] == "restart" and body["idempotent_replay"] is False
    assert body["target"] == {
        "server_id": rig.staging,
        "server_display_name": "Staging",
        "service_name": "web.service",
        "environment": "staging",
    }
    assert body["requested_by"]["id"] == str(rig.caller)
    assert body["approval"] is None and body["poll_after_seconds"] == 2
    assert body["requested_reason"] == "CR-0000"

    [row] = rig.rows(id=body["operation_id"])
    assert row["execution_status"] == "PENDING" and row["provider"] == "scripted"
    assert row["environment"] == "staging" and str(row["requested_by"]) == str(rig.caller)
    assert row["parent_operation_id"] is None and row["external_execution_id"] is None
    # deadline = request time + the policy's 300 s (OPEN-70), measured from the row's own clock.
    assert (row["deadline_at"] - row["created_at"]).total_seconds() == 300

    assert rig.enqueued == [uuid.UUID(body["operation_id"])]
    [event] = rig.audit("SERVICE_OPERATION_REQUESTED", target_id=body["operation_id"])
    assert event["outcome"] == "SUCCESS" and event["actor_id"] == rig.caller
    assert event["environment"] == "staging"


async def test_the_status_endpoint_shows_what_was_requested_and_no_verified_state_yet(
    rig: Rig,
) -> None:
    created = (await rig.ask()).json()
    response = await call(rig.client, "GET", f"/api/v1/operations/{created['operation_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_status"] == "PENDING" and body["poll_after_seconds"] == 2
    assert body["observed_state_after"] is None and body["verified_at"] is None
    assert body["failure_reason"] is None and body["parent_operation_id"] is None
    assert body["sanitized_result"] == {"adapter_id": "scripted"}


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
async def test_each_requestable_action_is_accepted_when_the_policy_allows_it(
    rig: Rig, action: str
) -> None:
    response = await rig.ask(action=action)
    assert response.status_code == 202, response.text
    assert response.json()["action_type"] == action


# --- idempotency ------------------------------------------------------------------------------


async def test_a_replay_returns_the_original_and_executes_nothing_again(rig: Rig) -> None:
    key = str(uuid.uuid4())
    first = await rig.ask(key=key)
    second = await rig.ask(key=key)

    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["operation_id"] == first.json()["operation_id"]
    assert second.json()["idempotent_replay"] is True
    assert len(rig.rows(idempotency_key=key)) == 1
    assert len(rig.enqueued) == 1  # nothing re-executed
    assert (
        len(rig.audit("SERVICE_OPERATION_REQUESTED", target_id=first.json()["operation_id"])) == 1
    )


async def test_a_replay_of_a_live_operation_is_the_original_not_a_409_and_costs_no_budget(
    rig: Rig,
) -> None:
    """SECURITY §16 amended by OPEN-73: replay is checked before the in-flight guard."""
    key = str(uuid.uuid4())
    assert (await rig.ask(key=key)).status_code == 202
    spent = rig.spent()
    replay = await rig.ask(key=key)
    assert replay.status_code == 200 and replay.json()["execution_status"] == "PENDING"
    assert rig.spent() == spent


@pytest.mark.parametrize(
    "change",
    [{"action": "stop"}, {"reason": "a different reason"}, {"unit": "restart.service"}],
)
async def test_the_same_key_with_a_different_body_is_a_409(
    rig: Rig, change: dict[str, str]
) -> None:
    key = str(uuid.uuid4())
    assert (await rig.ask(key=key)).status_code == 202
    response = await rig.ask(key=key, **change)  # type: ignore[arg-type]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert (
        response.json()["error"]["details"]["reason"]
        == "idempotency_key_reused_with_different_payload"
    )
    assert len(rig.rows(idempotency_key=key)) == 1


async def test_concurrent_submissions_of_one_key_produce_exactly_one_operation(rig: Rig) -> None:
    key = str(uuid.uuid4())
    responses = await asyncio.gather(*[rig.ask(key=key) for _ in range(4)])

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 202], [
        (r.status_code, r.text) for r in responses
    ]
    assert len({r.json()["operation_id"] for r in responses}) == 1
    assert len(rig.rows(idempotency_key=key)) == 1
    assert len(rig.enqueued) == 1


async def test_concurrent_submissions_with_different_keys_produce_exactly_one_operation(
    rig: Rig,
) -> None:
    responses = await asyncio.gather(*[rig.ask() for _ in range(4)])

    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409, 409, 409], [(r.status_code, r.text) for r in responses]
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 409} == {
        "operation_in_progress"
    }
    assert len(rig.rows(target_service_id=rig.service_id(rig.staging, "web.service"))) == 1
    assert len(rig.enqueued) == 1


async def test_a_second_request_for_a_live_target_is_409_and_audited_until_it_is_cancelled(
    rig: Rig,
) -> None:
    first = (await rig.ask()).json()
    again = await rig.ask()
    assert again.status_code == 409 and again.json()["error"]["code"] == "operation_in_progress"
    [event] = denied(rig, rig.service_id(rig.staging, "web.service"))
    assert event["sanitized_metadata"]["reason"] == "operation_in_progress"

    cancelled = await call(rig.client, "POST", f"/api/v1/operations/{first['operation_id']}/cancel")
    assert cancelled.status_code == 200
    assert (await rig.ask()).status_code == 202  # the slot is free again


# --- 17.1: refusals ---------------------------------------------------------------------------


async def test_a_viewer_cannot_request_and_the_denial_is_audited_with_no_row(rig: Rig) -> None:
    rig.as_role("VIEWER")
    response = await rig.ask()
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"
    assert rig.rows(target_server_id=rig.staging) == []
    permissions = [
        r["sanitized_metadata"]["permission"]
        for r in rig.audit("AUTHZ_DENIED", actor_id=rig.caller)
    ]
    assert "service:restart" in permissions
    assert rig.enqueued == []


async def test_a_caller_with_no_service_permission_at_all_is_refused_at_the_route(
    rig: Rig,
) -> None:
    revoke_all(rig.engine, rig.caller)
    response = await rig.ask()
    assert response.status_code == 403
    assert rig.rows(target_server_id=rig.staging) == []


async def test_a_server_outside_the_callers_scope_is_404_not_403(rig: Rig) -> None:
    rig.as_role("DEVOPS_ENGINEER", STAGING_SCOPE)
    response = await rig.ask(rig.production, "api.service")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert rig.rows(target_server_id=rig.production) == []
    assert (await rig.ask()).status_code == 202  # staging is still in scope


async def test_production_is_refused_for_an_engineer_whose_grant_names_only_staging(
    rig: Rig,
) -> None:
    """Rule 4: ``*`` never implies production, and a staging scope has no production entry."""
    rig.as_role(
        "DEVOPS_ENGINEER", '{"environment":["staging"],"cloud_account":["*"],"server":["*"]}'
    )
    assert (await rig.ask(rig.production, "api.service")).status_code == 404


@pytest.mark.parametrize(
    ("server", "unit"),
    [
        ("staging", "missing.service"),  # not registered
        ("unknown", "web.service"),  # no such server
    ],
)
async def test_an_unregistered_unit_or_unknown_server_is_404(
    rig: Rig, server: str, unit: str
) -> None:
    target = rig.staging if server == "staging" else str(uuid.uuid4())
    assert (await rig.ask(target, unit)).status_code == 404


async def test_a_server_that_is_not_managed_is_404(rig: Rig) -> None:
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE servers SET status = 'PENDING' WHERE id = :i"), {"i": rig.staging}
        )
    assert (await rig.ask()).status_code == 404
    assert rig.rows(target_server_id=rig.staging) == []


async def test_a_unit_registered_to_another_server_is_not_reachable_through_this_one(
    rig: Rig,
) -> None:
    """SECURITY §14.4: a valid service name from a *different* server is rejected."""
    assert (await rig.ask(rig.staging, "api.service")).status_code == 404


@pytest.mark.parametrize(
    ("unit", "action"),
    [
        ("ro.service", "restart"),  # policy allows only status / logs_tail
        ("restart.service", "stop"),  # policy allows restart but not stop
        ("bare.service", "restart"),  # no policy at all: no action permitted (fail closed)
    ],
)
async def test_an_action_outside_the_services_policy_is_403_and_audited(
    rig: Rig, unit: str, action: str
) -> None:
    response = await rig.ask(unit=unit, action=action)
    assert response.status_code == 403, response.text
    assert rig.rows(target_service_id=rig.service_id(rig.staging, unit)) == []
    [event] = denied(rig, rig.service_id(rig.staging, unit))
    assert event["sanitized_metadata"]["reason"] == "action_not_in_policy"
    assert event["sanitized_metadata"]["action"] == action


async def test_a_disabled_cloud_account_is_refused_and_audited(rig: Rig) -> None:
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE cloud_accounts SET status = 'DISABLED' WHERE id ="
                " (SELECT cloud_account_id FROM servers WHERE id = :i)"
            ),
            {"i": rig.staging},
        )
    try:
        response = await rig.ask()
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "cloud_account_not_active"
        assert rig.rows(target_server_id=rig.staging) == []
        assert denied(rig, rig.service_id(rig.staging, "web.service"))
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE cloud_accounts SET status = 'ACTIVE' WHERE id ="
                    " (SELECT cloud_account_id FROM servers WHERE id = :i)"
                ),
                {"i": rig.staging},
            )


# --- 17.1: validation -------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["status", "logs_tail", "exec", "RESTART", "", "restart; id"])
async def test_only_start_stop_restart_are_accepted(rig: Rig, action: str) -> None:
    response = await rig.ask(action=action)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    assert rig.rows(target_server_id=rig.staging) == []


@pytest.mark.parametrize(
    "field_name",
    ["command", "script", "args", "argv", "shell", "document", "timeout", "host", "url", "params"],
)
async def test_no_field_can_carry_a_command_or_anything_else(rig: Rig, field_name: str) -> None:
    response = await rig.ask(extra={field_name: "systemctl restart sshd"})
    assert response.status_code == 422, response.text
    assert "systemctl" not in response.text  # a rejected value is never echoed
    assert rig.rows(target_server_id=rig.staging) == []


@pytest.mark.parametrize("key", [None, "short", "x" * 65])
async def test_the_idempotency_key_is_required_and_bounded(rig: Rig, key: str | None) -> None:
    body = {"action": "restart", "reason": "CR-0000"}
    headers = {} if key is None else {"Idempotency-Key": key}
    response = await call(rig.client, "POST", rig.path(rig.staging), body, headers=headers)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("reason", ["x" * 501, "line\nbreak", "tab\there", "nul\x00byte", "   "])
async def test_a_reason_must_be_short_printable_text(rig: Rig, reason: str) -> None:
    assert (await rig.ask(reason=reason)).status_code == 422


async def test_a_production_request_needs_a_reason(rig: Rig) -> None:
    response = await rig.ask(rig.production, "api.service", reason=None)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["loc"] == ["body", "reason"]
    assert (await rig.ask(rig.production, "api.service", reason="CR-1")).status_code == 202


async def test_a_staging_request_does_not(rig: Rig) -> None:
    response = await rig.ask(reason=None)
    assert response.status_code == 202 and response.json()["requested_reason"] is None


async def test_a_malformed_unit_name_in_the_path_is_422_before_any_lookup(rig: Rig) -> None:
    assert (await rig.ask(unit="rm -rf;id.service")).status_code == 422


# --- 17.1: read-only mode and the rate limit --------------------------------------------------


async def test_read_only_mode_refuses_with_423(rig: Rig) -> None:
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE system_settings SET value = 'true'::jsonb WHERE key = 'read_only_mode'")
        )
    try:
        response = await rig.ask()
        assert response.status_code == 423 and response.json()["error"]["code"] == "read_only_mode"
        assert rig.rows(target_server_id=rig.staging) == []
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE system_settings SET value = 'false'::jsonb WHERE key = 'read_only_mode'"
                )
            )


async def test_the_sixth_request_in_a_minute_is_429_with_retry_after_and_is_audited(
    rig: Rig,
) -> None:
    units = ["web.service"]
    for _ in range(5):
        response = await rig.ask(unit=units[0])
        assert response.status_code in (202, 409), response.text  # 409: the slot is taken
    limited = await rig.ask(unit=units[0])
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    reasons = [
        e["sanitized_metadata"]["reason"]
        for e in denied(rig, rig.service_id(rig.staging, "web.service"))
    ]
    assert "rate_limited" in reasons


async def test_the_limit_fails_closed_when_the_counter_store_is_unreachable(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import RedisError

    from app.core import ratelimit

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RedisError("down")

    monkeypatch.setattr(ratelimit, "check", broken)
    response = await rig.ask()
    assert response.status_code == 429  # a privileged class denies rather than allows
    assert rig.rows(target_server_id=rig.staging) == []


# --- 17.1: when nothing can act ---------------------------------------------------------------


async def test_in_demonstration_mode_a_request_is_503_and_creates_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCP_DEMO_MODE", "true")
    get_settings.cache_clear()
    response = await rig.ask()
    assert response.status_code == 503 and response.json()["error"]["code"] == "not_configured"
    assert response.json()["error"]["details"]["reason"] == "demonstration_mode"
    assert rig.rows(target_server_id=rig.staging) == [] and rig.enqueued == []
    [event] = denied(rig, rig.service_id(rig.staging, "web.service"))
    assert event["sanitized_metadata"]["reason"] == "demonstration_mode"


async def test_with_no_adapter_for_the_provider_a_request_is_503_and_creates_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(registry._FACTORIES, "ON_PREM")
    response = await rig.ask()
    assert response.status_code == 503
    assert response.json()["error"]["details"]["reason"] == "no_adapter_for_provider"
    assert rig.rows(target_server_id=rig.staging) == []


async def test_an_action_the_provider_cannot_do_is_422_unsupported_not_a_generic_failure(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(registry._FACTORIES, "ON_PREM", NoRestartAdapter)
    response = await rig.ask(action="restart")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unsupported_operation"
    assert rig.rows(target_server_id=rig.staging) == []
    assert (await rig.ask(action="stop")).status_code == 202  # the others still work


async def test_an_unconfigured_caller_learns_nothing_about_configuration(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permission is checked before the adapter: a viewer gets 403, not 503."""
    monkeypatch.delitem(registry._FACTORIES, "ON_PREM")
    rig.as_role("VIEWER")
    assert (await rig.ask()).status_code == 403


@pytest.mark.parametrize(
    ("unit", "action", "basis"),
    [
        ("api.service", "stop", "production_stop"),  # a production stop, whatever the rows say
        ("gated.service", "restart", "policy"),  # the seeded production approval policy
    ],
)
async def test_an_operation_that_needs_approval_is_refused_not_queued_until_m4_5(
    rig: Rig, unit: str, action: str, basis: str
) -> None:
    response = await rig.ask(rig.production, unit, action=action)
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "not_configured"
    assert error["details"]["reason"] == "approval_workflow_unavailable"
    assert error["details"]["basis"] == basis
    service_id = rig.service_id(rig.production, unit)
    assert rig.rows(target_service_id=service_id) == []  # no AWAITING_APPROVAL row to wedge it
    assert rig.enqueued == []
    [event] = denied(rig, service_id)
    assert event["sanitized_metadata"]["reason"] == "approval_workflow_unavailable"


async def test_the_same_policy_does_not_gate_staging(rig: Rig) -> None:
    assert (await rig.ask(unit="restart.service", action="restart")).status_code == 202


async def test_enqueue_failure_does_not_fail_a_request_that_already_committed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(operations_service, "enqueue_execution", rig.real_enqueue)

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(operations_service.asyncio, "to_thread", broken)
    response = await rig.ask()
    assert response.status_code == 202, response.text
    assert len(rig.rows(id=response.json()["operation_id"])) == 1  # the healer will pick it up


# --- 17.2 -------------------------------------------------------------------------------------


async def test_a_verified_success_shows_the_evidence_and_verifying_never_appears(rig: Rig) -> None:
    created = (await rig.ask()).json()
    rig.set_status(created["operation_id"], "SUCCEEDED")
    body = (await call(rig.client, "GET", f"/api/v1/operations/{created['operation_id']}")).json()
    assert body["execution_status"] == "SUCCEEDED" and body["poll_after_seconds"] is None
    assert body["observed_state_before"] == "INACTIVE" and body["observed_state_after"] == "ACTIVE"
    assert body["verified_at"] == body["completed_at"] and body["started_at"] is not None


async def test_a_running_operation_has_no_verified_time(rig: Rig) -> None:
    created = (await rig.ask()).json()
    rig.set_status(created["operation_id"], "RUNNING")
    body = (await call(rig.client, "GET", f"/api/v1/operations/{created['operation_id']}")).json()
    assert body["execution_status"] == "RUNNING" and body["verified_at"] is None
    assert body["poll_after_seconds"] == 2


async def test_the_requester_always_reads_their_own_operation_even_after_losing_scope(
    rig: Rig,
) -> None:
    created = (await rig.ask()).json()
    rig.as_role("VIEWER", '{"environment":["development"],"cloud_account":["*"],"server":["*"]}')
    response = await call(rig.client, "GET", f"/api/v1/operations/{created['operation_id']}")
    assert response.status_code == 200


async def test_someone_elses_operation_needs_operation_read_over_its_environment(
    rig: Rig,
) -> None:
    foreign = rig.foreign_operation(rig.staging, "web.service")
    rig.as_role("VIEWER", STAGING_SCOPE)
    assert (await call(rig.client, "GET", f"/api/v1/operations/{foreign}")).status_code == 200
    rig.as_role("VIEWER", '{"environment":["development"],"cloud_account":["*"],"server":["*"]}')
    assert (await call(rig.client, "GET", f"/api/v1/operations/{foreign}")).status_code == 404
    revoke_all(rig.engine, rig.caller)
    assert (await call(rig.client, "GET", f"/api/v1/operations/{foreign}")).status_code == 403


async def test_an_unknown_operation_is_404(rig: Rig) -> None:
    assert (await call(rig.client, "GET", f"/api/v1/operations/{uuid.uuid4()}")).status_code == 404


async def test_the_openapi_request_body_is_one_closed_enum_and_a_reason(rig: Rig) -> None:
    schema = (await rig.client.get("/openapi.json")).json()
    components = schema["components"]["schemas"]
    request_body = components["OperationRequest"]
    assert set(request_body["properties"]) == {"action", "reason"}
    assert request_body["additionalProperties"] is False
    assert components["OperationRequest"]["properties"]["action"]["enum"] == [
        "start",
        "stop",
        "restart",
    ]


# --- 17.3 -------------------------------------------------------------------------------------


async def test_the_requester_can_cancel_before_a_worker_claims_it(rig: Rig) -> None:
    created = (await rig.ask()).json()
    response = await call(
        rig.client, "POST", f"/api/v1/operations/{created['operation_id']}/cancel"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_status"] == "CANCELLED" and body["poll_after_seconds"] is None
    assert body["completed_at"] is not None and body["observed_state_after"] is None
    [event] = rig.audit("SERVICE_OPERATION_CANCELLED", target_id=created["operation_id"])
    assert event["actor_id"] == rig.caller


async def test_cancelling_twice_is_409(rig: Rig) -> None:
    created = (await rig.ask()).json()
    url = f"/api/v1/operations/{created['operation_id']}/cancel"
    assert (await call(rig.client, "POST", url)).status_code == 200
    again = await call(rig.client, "POST", url)
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state_transition"
    assert len(rig.audit("SERVICE_OPERATION_CANCELLED", target_id=created["operation_id"])) == 1


@pytest.mark.parametrize("status", ["RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT"])
async def test_a_claimed_or_finished_operation_cannot_be_cancelled(rig: Rig, status: str) -> None:
    """A false "cancelled" would be a lie about system state (§17.3)."""
    created = (await rig.ask()).json()
    rig.set_status(created["operation_id"], status)
    response = await call(
        rig.client, "POST", f"/api/v1/operations/{created['operation_id']}/cancel"
    )
    assert response.status_code == 409
    [row] = rig.rows(id=created["operation_id"])
    assert row["execution_status"] == status


async def test_someone_who_is_not_the_requester_needs_operation_approve_to_cancel(rig: Rig) -> None:
    foreign = rig.foreign_operation(rig.staging, "web.service")
    url = f"/api/v1/operations/{foreign}/cancel"

    rig.as_role("VIEWER", STAGING_SCOPE)  # can see it, may not cancel it
    refused = await call(rig.client, "POST", url)
    assert refused.status_code == 403
    assert "operation:approve" in [
        r["sanitized_metadata"]["permission"]
        for r in rig.audit("AUTHZ_DENIED", actor_id=rig.caller)
    ]
    assert rig.rows(id=foreign)[0]["execution_status"] == "PENDING"

    rig.as_role("APPROVER", STAGING_SCOPE)
    allowed = await call(rig.client, "POST", url)
    assert allowed.status_code == 200 and allowed.json()["execution_status"] == "CANCELLED"


async def test_cancelling_an_operation_out_of_scope_is_404(rig: Rig) -> None:
    foreign = rig.foreign_operation(rig.production, "api.service")
    rig.as_role("APPROVER", STAGING_SCOPE)
    assert (
        await call(rig.client, "POST", f"/api/v1/operations/{foreign}/cancel")
    ).status_code == 404


# --- the in-flight guard on inventory writes --------------------------------------------------


async def test_a_service_cannot_be_deregistered_while_an_operation_is_in_flight(rig: Rig) -> None:
    rig.as_role("ADMINISTRATOR")
    created = (await rig.ask()).json()
    url = f"/api/v1/servers/{rig.staging}/services/web.service"
    refused = await call(rig.client, "DELETE", url)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "operation_in_progress"

    await call(rig.client, "POST", f"/api/v1/operations/{created['operation_id']}/cancel")
    assert (await call(rig.client, "DELETE", url)).status_code == 204


async def test_a_server_cannot_be_retired_while_an_operation_is_in_flight(rig: Rig) -> None:
    rig.as_role("ADMINISTRATOR")
    created = (await rig.ask()).json()
    refused = await call(rig.client, "DELETE", f"/api/v1/servers/{rig.staging}")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "operation_in_progress"

    await call(rig.client, "POST", f"/api/v1/operations/{created['operation_id']}/cancel")
    assert (await call(rig.client, "DELETE", f"/api/v1/servers/{rig.staging}")).status_code == 204
