"""Deployments end to end: §21.1 – §21.3, §21.6, the §21.12 guard (M5.2, ``OPEN-132`` – ``142``).

Everything runs against real PostgreSQL and Redis through the real application. Jenkins is **not**
called: the request path never talks to it, and the worker (the next commit) is not involved -- the
enqueue is a recorder. What these tests prove, in this order:

1. **Nothing a request carries becomes a Jenkins URL, job name, node label or build parameter.**
   The adversarial test puts each into every body and query and finds each refused or ignored.
2. **Permission is checked first and on the environment *and* the application**, so a caller who
   may not deploy learns nothing about registration or configuration.
3. **The storage guarantees decide the races**: one active deployment per application and
   environment, one deployment per requester and key -- including two requests sent at once.
4. **A deployment that needs an approval is refused, never queued** (``OPEN-133``).
5. **Reads are scoped in SQL on the stored environment and application**; out of reach is ``404``.
6. **A cancel that loses the race to a worker is ``409``, never a false "cancelled".**

Committed rows are removed again: other suites count applications.
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import redis
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.deployments import lifecycle, policy
from app.deployments.digest import target_digest
from tests.integration.inventory_helpers import (
    FULL_SCOPE,
    audit_rows,
    call,
    grant,
    revoke_all,
    user_id,
)
from tests.integration.test_approvals_api import _second_login
from tests.integration.test_auth_flow import (  # noqa: F401 -- fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)
from tests.integration.test_deployment_targets_api import TEMPLATE, World
from tests.integration.test_deployment_targets_api import body as target_body

DEPLOYMENTS = "/api/v1/deployments"
TARGETS = "/api/v1/deployment-targets"
TEMPLATE_CANARY = "TEMPLATE-CANARY-7d1e-9f"
NON_PRODUCTION = (
    '{"environment":["development","staging","other"],"cloud_account":["*"],'
    '"server":["*"],"application":["*"]}'
)


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def ask_body(application: str, **overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "application_id": application,
        "environment": "staging",
        "ref": "main",
        "reason": "CR-0000",
    }
    out.update(overrides)
    return out


@dataclass
class Rig:
    world: World
    redis_url: str
    monkeypatch: pytest.MonkeyPatch
    application: str
    target: str
    enqueued: list[uuid.UUID] = field(default_factory=list)

    @property
    def client(self) -> httpx.AsyncClient:
        return self.world.client

    @property
    def engine(self) -> Engine:
        return self.world.engine

    @property
    def caller(self) -> uuid.UUID:
        return self.world.caller

    def as_role(self, role: str, scope: str = FULL_SCOPE) -> None:
        self.world.as_role(role, scope)

    @asynccontextmanager
    async def admin(self) -> AsyncIterator[None]:
        """Registering an application or a target is an Administrator's act; then back."""
        self.as_role("ADMINISTRATOR")
        try:
            yield
        finally:
            self.as_role("DEVOPS_ENGINEER")

    async def new_application(self) -> str:
        async with self.admin():
            return await self.world.application()

    async def register_target(self, application: str, **overrides: Any) -> str:
        async with self.admin():
            created = await self.world.register(application, **overrides)
        assert created.status_code == 201, created.text
        return str(created.json()["id"])

    async def ask(
        self,
        application: str | None = None,
        *,
        key_: str | None = None,
        client: httpx.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
        **overrides: Any,
    ) -> httpx.Response:
        sent = {"Idempotency-Key": key_ or str(uuid.uuid4()), **(headers or {})}
        return await call(
            client or self.client,
            "POST",
            DEPLOYMENT_PATH,
            ask_body(application or self.application, **overrides),
            headers=sent,
        )

    def rows(self, **where: Any) -> list[dict[str, Any]]:
        clause = " AND ".join(f"{column} = :{column}" for column in where) or "true"
        with self.engine.connect() as connection:
            result = connection.execute(
                text(f"SELECT * FROM deployments WHERE {clause} ORDER BY created_at"),  # noqa: S608
                where,
            )
            return [dict(row) for row in result.mappings()]

    def audit(self, action: str, **match: Any) -> list[dict[str, Any]]:
        rows = audit_rows(self.engine, action)
        return [r for r in rows if all(str(r.get(k)) == str(v) for k, v in match.items())]

    def requested_events(self) -> list[dict[str, Any]]:
        """``DEPLOYMENT_REQUESTED`` rows for this rig's application (the trail is shared)."""
        return [
            e
            for e in audit_rows(self.engine, "DEPLOYMENT_REQUESTED")
            if e["sanitized_metadata"].get("application_id") == self.application
        ]

    def insert(self, status: str = "FAILED", **overrides: Any) -> str:
        """A deployment written straight to the table, for reads and states the API cannot reach."""
        other = overrides.pop("requested_by", None) or self.caller
        row: dict[str, Any] = {
            "id": uuid.uuid4(),
            "application_id": uuid.UUID(self.application),
            "target_id": uuid.UUID(self.target),
            "environment": "staging",
            "ref": "main",
            "user": other,
            "status": status,
            "key": uuid.uuid4().hex,
            "correlation": uuid.uuid4(),
            "digest": "0" * 64,
            "build": "7" if status in ("RUNNING", "SUCCEEDED") else None,
            "failure": "PIPELINE_FAILED" if status == "FAILED" else None,
            "health": json.dumps(policy.none_configured()) if status == "SUCCEEDED" else "{}",
            "result": json.dumps(overrides.pop("result", {})),
            "finished": status in ("SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"),
            "created": overrides.pop("created", None),
        }
        row.update(overrides)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deployments (id, application_id, deployment_target_id,"
                    " environment, pipeline_reference, requested_ref, requested_by,"
                    " approval_status, execution_status, external_execution_id,"
                    " idempotency_key, correlation_id, target_digest, health_check_result,"
                    " sanitized_result, failure_reason, deadline_at, completed_at, created_at)"
                    " VALUES (:id, :application_id, :target_id, :environment,"
                    " 'team/service-deploy-canary-0001', :ref, :user, 'NOT_REQUIRED', :status,"
                    " :build, :key, :correlation, :digest, CAST(:health AS jsonb),"
                    " CAST(:result AS jsonb), :failure, now() + interval '3600 seconds',"
                    " CASE WHEN :finished THEN now() END,"
                    " COALESCE(CAST(:created AS timestamptz), now()))"
                ),
                row,
            )
        return str(row["id"])

    def set_status(self, deployment_id: str, status: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE deployments SET execution_status = :s,"
                    " external_execution_id = CASE WHEN :b THEN '7' END,"
                    " failure_reason = CASE WHEN :f THEN 'PIPELINE_FAILED' END,"
                    " health_check_result = CASE WHEN :ok THEN CAST(:h AS jsonb)"
                    " ELSE health_check_result END,"
                    " completed_at = CASE WHEN :done THEN now() END WHERE id = :i"
                ),
                {
                    "s": status,
                    "b": status in ("RUNNING", "SUCCEEDED"),
                    "f": status == "FAILED",
                    "ok": status == "SUCCEEDED",
                    "h": json.dumps(policy.none_configured()),
                    "done": status in ("SUCCEEDED", "FAILED", "CANCELLED"),
                    "i": deployment_id,
                },
            )

    def reset_rate_limit(self, environment: str = "staging") -> None:
        client = redis.Redis.from_url(self.redis_url)
        try:
            client.delete(f"dcp:ratelimit:deployment_create:{self.caller}:{environment}")
        finally:
            client.close()

    def spent(self, environment: str = "staging") -> int:
        client = redis.Redis.from_url(self.redis_url)
        try:
            value = client.get(f"dcp:ratelimit:deployment_create:{self.caller}:{environment}")
            return int(value) if value is not None else 0  # type: ignore[arg-type]
        finally:
            client.close()

    def set_spent(self, count: int, environment: str = "staging") -> None:
        client = redis.Redis.from_url(self.redis_url)
        try:
            client.set(
                f"dcp:ratelimit:deployment_create:{self.caller}:{environment}", count, ex=3600
            )
        finally:
            client.close()


DEPLOYMENT_PATH = DEPLOYMENTS


@pytest.fixture
def jenkins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demonstration mode off (the suite's environment has it on) and a Jenkins configured.

    Configured only: nothing here calls it. The request path never does.
    """
    for name, value in {
        "DCP_DEMO_MODE": "false",
        "DCP_JENKINS_URL": "http://mock-jenkins:8080",
        "DCP_JENKINS_USER": "dcp",
        "DCP_JENKINS_TOKEN_REFERENCE": "env://JENKINS_TOKEN",
        "DCPSECRET_JENKINS_TOKEN": "mock-jenkins-token-canary-0002",
        "DCP_SSRF_HOST_ALLOWLIST": "mock-jenkins",
        "DCP_SSRF_INSECURE_HOSTS": "mock-jenkins",
        "DCP_SSRF_ALLOWED_NETWORKS": "10.0.0.0/8",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def rig(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
    migrated: Engine,
    redis_url: str,
    jenkins: None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Rig]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    world = World(client, migrated, caller)
    application = await world.application()
    template = {"fixed": {"TARGET": TEMPLATE_CANARY}, "slots": TEMPLATE["slots"]}
    created = await world.register(application, parameter_template=template)
    assert created.status_code == 201, created.text
    built = Rig(world, redis_url, monkeypatch, application, created.json()["id"])

    async def record(deployment_id: uuid.UUID) -> None:
        built.enqueued.append(deployment_id)

    monkeypatch.setattr(lifecycle, "enqueue_execution", record)
    built.as_role("DEVOPS_ENGINEER")
    for environment in ("staging", "development", "production"):
        built.reset_rate_limit(environment)
    yield built
    for environment in ("staging", "development", "production"):
        built.reset_rate_limit(environment)
    revoke_all(migrated, caller)
    ids = [uuid.UUID(a) for a in world.applications]
    with migrated.begin() as connection:
        connection.execute(
            text("DELETE FROM deployments WHERE application_id = ANY(:ids)"), {"ids": ids}
        )
        connection.execute(
            text("DELETE FROM deployment_targets WHERE application_id = ANY(:ids)"), {"ids": ids}
        )
        connection.execute(text("DELETE FROM applications WHERE id = ANY(:ids)"), {"ids": ids})


# --- 21.2: the happy path ---------------------------------------------------------------------


async def test_a_permitted_deployment_is_stored_requested_and_handed_to_the_worker(
    rig: Rig,
) -> None:
    response = await rig.ask()
    assert response.status_code == 202, response.text
    body = response.json()

    assert body["execution_status"] == "REQUESTED" and body["approval_status"] == "NOT_REQUIRED"
    assert body["idempotent_replay"] is False and body["approval"] is None
    assert body["poll_after_seconds"] == 5
    assert body["target"] == {
        "application_id": rig.application,
        "application_name": body["target"]["application_name"],
        "environment": "staging",
        "pipeline_reference": "team/service-deploy-canary-0001",
        "requested_ref": "main",
        "rollback_supported": False,
    }
    # The exact target is shown. Nothing else about the target or Jenkins is.
    assert set(body) == {
        "deployment_id",
        "execution_status",
        "approval_status",
        "target",
        "approval",
        "deadline_at",
        "correlation_id",
        "poll_after_seconds",
        "idempotent_replay",
    }

    [row] = rig.rows(id=body["deployment_id"])
    assert row["execution_status"] == "REQUESTED" and row["kind"] == "DEPLOY"
    assert row["environment"] == "staging" and row["requested_by"] == rig.caller
    assert row["requested_ref"] == "main" and row["requested_reason"] == "CR-0000"
    assert row["external_execution_id"] is None and row["queue_item_id"] is None
    assert row["health_check_result"] == {} and row["failure_reason"] is None
    assert str(row["deployment_target_id"]) == rig.target
    # deadline = request time + the setting (OPEN-136), measured from the row's own clock.
    assert (row["deadline_at"] - row["created_at"]).total_seconds() == 3_600
    assert str(row["correlation_id"]) == body["correlation_id"]
    # The digest of what the requester was shown: pipeline and template, as stored.
    assert row["target_digest"] == target_digest(
        "team/service-deploy-canary-0001",
        {"fixed": {"TARGET": TEMPLATE_CANARY}, "slots": TEMPLATE["slots"]},
    )

    assert rig.enqueued == [uuid.UUID(body["deployment_id"])]
    [event] = rig.audit("DEPLOYMENT_REQUESTED", target_id=body["deployment_id"])
    assert event["outcome"] == "SUCCESS" and event["actor_id"] == rig.caller
    assert event["environment"] == "staging" and event["target_type"] == "deployment"
    assert event["sanitized_metadata"] == {
        "application_id": rig.application,
        "deployment_target_id": rig.target,
        "requested_ref": "main",
        "reason_length": 7,  # the length only, never the reason (OPEN-142)
    }


async def test_a_reason_is_optional_and_a_missing_one_is_audited_as_none(rig: Rig) -> None:
    response = await rig.ask(reason=None)
    assert response.status_code == 202, response.text
    [event] = rig.audit("DEPLOYMENT_REQUESTED", target_id=response.json()["deployment_id"])
    assert event["sanitized_metadata"]["reason_length"] is None


async def test_neither_the_template_nor_the_digest_nor_jenkins_is_in_any_response(
    rig: Rig,
) -> None:
    body = (await rig.ask()).json()
    deployment_id = body["deployment_id"]
    digest = rig.rows(id=deployment_id)[0]["target_digest"]
    texts = [json.dumps(body)]
    for path in (f"{DEPLOYMENTS}/{deployment_id}", DEPLOYMENTS):
        got = await call(rig.client, "GET", path)
        assert got.status_code == 200, got.text
        texts.append(got.text)
    for text_ in texts:
        for forbidden in (TEMPLATE_CANARY, digest, "mock-jenkins", "canary-0002", "queue_item"):
            assert forbidden not in text_, forbidden
    # Nor in the audit trail the request wrote.
    for event in rig.audit("DEPLOYMENT_REQUESTED", target_id=deployment_id):
        assert TEMPLATE_CANARY not in json.dumps(event, default=str)


# --- idempotency ------------------------------------------------------------------------------


async def test_a_replayed_key_returns_the_original_and_changes_nothing(rig: Rig) -> None:
    first = await rig.ask(key_="replay-key-0001")
    assert first.status_code == 202
    spent = rig.spent()
    again = await rig.ask(key_="replay-key-0001")
    assert again.status_code == 200, again.text
    assert again.json()["deployment_id"] == first.json()["deployment_id"]
    assert again.json()["idempotent_replay"] is True
    assert len(rig.rows()) == 1 and len(rig.enqueued) == 1
    assert rig.spent() == spent, "a replay spends no rate-limit budget"
    assert len(rig.requested_events()) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"ref": "release/1.0"},
        {"reason": "CR-9999"},
        {"reason": None},
        {"environment": "development"},
    ],
)
async def test_a_key_reused_with_a_different_body_is_a_conflict(
    rig: Rig, change: dict[str, Any]
) -> None:
    await rig.ask(key_="conflict-key-01")
    response = await rig.ask(key_="conflict-key-01", **change)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == (
        "idempotency_key_reused_with_different_payload"
    )
    assert len(rig.rows()) == 1


async def test_a_replay_stands_even_after_the_target_is_retired(rig: Rig) -> None:
    """The replay lookup is ahead of the target lookup: an answer already given stays given."""
    first = await rig.ask(key_="stable-replay-01")
    rig.world.as_role("ADMINISTRATOR")
    rig.set_status(first.json()["deployment_id"], "FAILED")
    assert (await call(rig.client, "DELETE", f"{TARGETS}/{rig.target}")).status_code == 204
    rig.as_role("DEVOPS_ENGINEER")
    again = await rig.ask(key_="stable-replay-01")
    assert again.status_code == 200 and again.json()["idempotent_replay"] is True


async def test_a_key_belongs_to_its_requester(rig: Rig, auth_app: Any) -> None:
    other = await _second_login(rig.world, auth_app[1])  # type: ignore[arg-type]
    grant(rig.engine, other.id, "DEVOPS_ENGINEER")
    try:
        first = await rig.ask(key_="shared-key-0001")
        rig.set_status(first.json()["deployment_id"], "FAILED")
        second = await rig.ask(key_="shared-key-0001", client=other.client)
        assert second.status_code == 202, second.text
        assert second.json()["deployment_id"] != first.json()["deployment_id"]
    finally:
        revoke_all(rig.engine, other.id)
        await other.client.aclose()


async def test_the_idempotency_key_is_required_and_bounded(rig: Rig) -> None:
    for headers in ({}, {"Idempotency-Key": "short"}, {"Idempotency-Key": "x" * 65}):
        response = await call(
            rig.client, "POST", DEPLOYMENTS, ask_body(rig.application), headers=headers
        )
        assert response.status_code == 422, (headers, response.text)
    assert rig.rows() == []


# --- permission: first, and on the environment and the application ---------------------------


async def test_a_viewer_may_not_request_and_the_refusal_is_audited_twice(rig: Rig) -> None:
    rig.as_role("VIEWER")
    response = await rig.ask()
    assert response.status_code == 403, response.text
    assert rig.rows() == [] and rig.enqueued == []
    [denied] = rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)
    assert denied["outcome"] == "DENIED" and denied["actor_id"] == rig.caller
    assert denied["sanitized_metadata"] == {"reason": "permission_denied"}
    assert rig.audit("AUTHZ_DENIED", target_id=rig.application)


async def test_a_developer_with_non_production_scope_is_refused_production_by_permission(
    rig: Rig,
) -> None:
    """403, not the 503 a production request would otherwise get: permission is checked first."""
    rig.as_role("DEVELOPER", NON_PRODUCTION)
    response = await rig.ask(environment="production")
    assert response.status_code == 403, response.text
    assert rig.rows() == []
    assert rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)


async def test_a_developer_with_non_production_scope_may_deploy_staging(rig: Rig) -> None:
    rig.as_role("DEVELOPER", NON_PRODUCTION)
    assert (await rig.ask()).status_code == 202


async def test_an_approver_may_not_request(rig: Rig) -> None:
    rig.as_role("APPROVER")
    assert (await rig.ask()).status_code == 403


async def test_a_grant_for_another_application_is_not_a_grant_for_this_one(rig: Rig) -> None:
    other = uuid.uuid4()
    scope = (
        '{"environment":["staging"],"cloud_account":["*"],"server":["*"],'
        f'"application":["{other}"]}}'
    )
    rig.as_role("DEVOPS_ENGINEER", scope)
    response = await rig.ask()
    assert response.status_code == 403, response.text
    assert rig.rows() == []


async def test_a_caller_who_may_not_deploy_learns_nothing_about_registration(rig: Rig) -> None:
    """The same 403 for a registered pair and an unregistered one: nothing about config leaks."""
    rig.as_role("VIEWER")
    registered = await rig.ask()
    unregistered = await rig.ask(environment="development")
    assert registered.status_code == unregistered.status_code == 403
    assert registered.json()["error"]["code"] == unregistered.json()["error"]["code"]


async def test_writes_are_refused_in_read_only_mode(rig: Rig) -> None:
    path = "/api/v1/admin/settings/read-only-mode"
    rig.as_role("ADMINISTRATOR")
    assert (
        await call(rig.client, "POST", path, {"enabled": True, "reason": "test"})
    ).status_code == 200
    try:
        assert (await rig.ask()).status_code == 423
        fake = rig.insert("REQUESTED")
        assert (await call(rig.client, "POST", f"{DEPLOYMENTS}/{fake}/cancel")).status_code == 423
        assert (await call(rig.client, "GET", DEPLOYMENTS)).status_code == 200
    finally:
        await call(rig.client, "POST", path, {"enabled": False})
    assert len(rig.rows()) == 1  # only the one inserted by hand


# --- the target, the ref ----------------------------------------------------------------------


async def test_an_application_with_no_registered_target_is_not_registered(rig: Rig) -> None:
    other = await rig.new_application()
    response = await rig.ask(other)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "not_registered"
    [denied] = rig.audit("DEPLOYMENT_DENIED", target_id=other)
    assert denied["sanitized_metadata"] == {"reason": "not_registered"}


async def test_an_environment_with_no_target_is_not_registered(rig: Rig) -> None:
    response = await rig.ask(environment="development")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_registered"


async def test_a_disabled_target_is_not_registered(rig: Rig) -> None:
    rig.as_role("ADMINISTRATOR")
    patched = await call(
        rig.client, "PATCH", f"{TARGETS}/{rig.target}", {"status": "DISABLED"}, headers=key()
    )
    assert patched.status_code == 200, patched.text
    rig.as_role("DEVOPS_ENGINEER")
    assert (await rig.ask()).json()["error"]["code"] == "not_registered"


async def test_an_unknown_or_archived_application_is_404(rig: Rig) -> None:
    assert (await rig.ask(str(uuid.uuid4()), environment="staging")).status_code == 404
    assert rig.rows() == []


@pytest.mark.parametrize("ref", ["a b", "../x", "-flag", "a..b", "x/", ".hidden/", "a\nb", "r;rm"])
async def test_a_malformed_ref_is_a_field_error_and_stores_nothing(rig: Rig, ref: str) -> None:
    response = await rig.ask(ref=ref)
    assert response.status_code == 422, response.text
    assert rig.rows() == [] and rig.enqueued == []


async def test_an_over_long_or_empty_ref_is_a_field_error(rig: Rig) -> None:
    for ref in ("", "r" * 201):
        assert (await rig.ask(ref=ref)).status_code == 422
    assert rig.rows() == []


@pytest.mark.parametrize("ref", ["develop", "main-evil", "xmain", "release", "feature/x"])
async def test_a_ref_no_pattern_admits_is_refused_with_the_patterns_and_audited(
    rig: Rig, ref: str
) -> None:
    response = await rig.ask(ref=ref)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "not_permitted"
    assert error["details"] == {
        "reason": "ref_not_permitted",
        "allowed_ref_patterns": ["^main$", "^release/.+$"],
    }
    assert rig.rows() == []
    assert any(
        e["sanitized_metadata"] == {"reason": "ref_not_permitted"}
        for e in rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)
    )


@pytest.mark.parametrize("ref", ["main", "release/1.2", "release/2024-10/hotfix"])
async def test_a_ref_a_pattern_admits_is_accepted(rig: Rig, ref: str) -> None:
    response = await rig.ask(ref=ref)
    assert response.status_code == 202, response.text
    rig.set_status(response.json()["deployment_id"], "FAILED")
    assert rig.rows()[-1]["requested_ref"] == ref


# --- Jenkins, demonstration mode --------------------------------------------------------------


async def test_without_a_jenkins_nothing_is_stored(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "DCP_JENKINS_URL",
        "DCP_JENKINS_USER",
        "DCP_JENKINS_TOKEN_REFERENCE",
        "DCPSECRET_JENKINS_TOKEN",
    ):
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    response = await rig.ask()
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "not_configured"
    assert response.json()["error"]["details"] == {"reason": "jenkins_not_configured"}
    assert rig.rows() == [] and rig.enqueued == []
    assert any(
        e["sanitized_metadata"] == {"reason": "jenkins_not_configured"}
        for e in rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)
    )


async def test_demonstration_mode_never_runs_a_deployment(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCP_DEMO_MODE", "true")
    get_settings.cache_clear()
    response = await rig.ask()
    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"] == {"reason": "demonstration_mode"}
    assert rig.rows() == []


# --- approval: refused, never queued (OPEN-133) -----------------------------------------------


async def test_a_production_deployment_cannot_be_requested_until_approvals_exist(
    rig: Rig,
) -> None:
    await rig.register_target(
        rig.application, environment="production", approval_policy_id=rig.world.production_policy()
    )
    response = await rig.ask(environment="production", reason="CR-0001")
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "not_configured"
    assert response.json()["error"]["details"] == {"reason": "approval_workflow_unavailable"}
    assert rig.rows() == [] and rig.enqueued == []
    assert any(
        e["sanitized_metadata"] == {"reason": "approval_workflow_unavailable"}
        and e["environment"] == "production"
        for e in rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)
    )


async def test_a_staging_target_with_a_policy_is_refused_the_same_way(rig: Rig) -> None:
    other = await rig.new_application()
    with rig.engine.begin() as connection:
        policy_id = connection.execute(
            text(
                "INSERT INTO approval_policies (id, name, applies_to, environment,"
                " required_approvals, allow_self_approval, approval_window_seconds, created_at,"
                " updated_at) VALUES (gen_random_uuid(), :n, 'DEPLOYMENT', 'staging', 1, false,"
                " 1800, now(), now()) RETURNING id"
            ),
            {"n": f"test-gate-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
    try:
        await rig.register_target(other, approval_policy_id=str(policy_id))
        response = await rig.ask(other)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["details"] == {"reason": "approval_workflow_unavailable"}
        assert rig.rows(application_id=uuid.UUID(other)) == []
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM deployment_targets WHERE application_id = :a"),
                {"a": uuid.UUID(other)},
            )
            connection.execute(
                text("DELETE FROM approval_policies WHERE id = :i"), {"i": policy_id}
            )


# --- the rate limit ---------------------------------------------------------------------------


async def test_the_eleventh_request_in_an_hour_is_refused_per_identity_and_environment(
    rig: Rig,
) -> None:
    rig.set_spent(10)
    response = await rig.ask()
    assert response.status_code == 429, response.text
    assert int(response.headers["Retry-After"]) > 0
    assert rig.rows() == []
    assert any(
        e["sanitized_metadata"] == {"reason": "rate_limited"}
        for e in rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)
    )
    # Another environment has its own budget (here: refused for being unregistered, not limited).
    other = await rig.ask(environment="development")
    assert other.status_code == 422


async def test_a_request_inside_the_budget_spends_one_unit(rig: Rig) -> None:
    rig.set_spent(9)
    assert (await rig.ask()).status_code == 202
    assert rig.spent() == 10


async def test_when_the_counter_store_is_down_the_request_is_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A privileged class fails closed (SECURITY §13)."""
    from redis.exceptions import RedisError

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RedisError("down")

    monkeypatch.setattr("app.core.ratelimit.check", broken)
    response = await rig.ask()
    assert response.status_code == 429, response.text
    assert rig.rows() == []


# --- one active deployment per application and environment ------------------------------------


async def test_a_second_request_while_one_is_active_is_refused_and_audited(rig: Rig) -> None:
    first = await rig.ask()
    assert first.status_code == 202
    second = await rig.ask(ref="release/1.0")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "operation_in_progress"
    assert len(rig.rows()) == 1
    assert any(
        e["sanitized_metadata"] == {"reason": "operation_in_progress"}
        for e in rig.audit("DEPLOYMENT_DENIED", target_id=rig.application)
    )


@pytest.mark.parametrize(
    "active", ["REQUESTED", "AWAITING_APPROVAL", "APPROVED", "QUEUED", "RUNNING"]
)
async def test_every_in_flight_status_holds_the_slot(rig: Rig, active: str) -> None:
    rig.insert(active)
    response = await rig.ask()
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "operation_in_progress"


@pytest.mark.parametrize("finished", ["SUCCEEDED", "FAILED", "CANCELLED"])
async def test_a_finished_deployment_frees_the_slot(rig: Rig, finished: str) -> None:
    rig.insert(finished)
    assert (await rig.ask()).status_code == 202


async def test_another_environment_and_another_application_have_their_own_slot(rig: Rig) -> None:
    await rig.register_target(rig.application, environment="development")
    other_application = await rig.new_application()
    await rig.register_target(other_application)
    assert (await rig.ask()).status_code == 202
    assert (await rig.ask(environment="development")).status_code == 202
    assert (await rig.ask(other_application)).status_code == 202


async def test_two_requests_at_once_with_different_keys_make_exactly_one_deployment(
    rig: Rig,
) -> None:
    """The unique index decides the race, not the pre-check."""
    responses = await asyncio.gather(*(rig.ask(key_=f"race-key-{i:04d}") for i in range(6)))
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409, 409, 409, 409, 409], [r.text for r in responses]
    assert len(rig.rows()) == 1 and len(rig.enqueued) == 1


async def test_two_requests_at_once_with_one_key_make_one_deployment_and_both_succeed(
    rig: Rig,
) -> None:
    responses = await asyncio.gather(*(rig.ask(key_="same-race-key-1") for _ in range(6)))
    assert all(r.status_code in (200, 202) for r in responses), [r.text for r in responses]
    assert len({r.json()["deployment_id"] for r in responses}) == 1
    assert len(rig.rows()) == 1
    assert [r.status_code for r in responses].count(202) >= 1


# --- the adversarial test: nothing a request carries reaches Jenkins --------------------------

FORBIDDEN = {
    "jenkins_url": "http://169.254.169.254/",
    "url": "http://evil.example/",
    "base_url": "http://evil.example/",
    "job": "../../script",
    "job_name": "evil",
    "job_path": "evil/job",
    "pipeline": "evil",
    "pipeline_reference": "evil/path",
    "node": "built-in",
    "node_label": "master",
    "agent": "any",
    "parameters": {"CMD": "id"},
    "build_parameters": {"CMD": "id"},
    "params": {"CMD": "id"},
    "parameter_template": {"fixed": {"CMD": "id"}},
    "command": "id",
    "script": "rm -rf /",
    "argv": ["id"],
    "credential": "x",
    "token": "x",
    "extra_headers": {"X": "y"},
    "approval_policy_id": str(uuid.uuid4()),
    "execution_status": "SUCCEEDED",
    "requested_by": str(uuid.uuid4()),
    "commit_sha": "0" * 40,
    "deadline_at": "2999-01-01T00:00:00Z",
    "kind": "ROLLBACK",
}


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
async def test_no_body_field_reaches_jenkins_every_unknown_field_is_refused_and_nothing_stored(
    rig: Rig, name: str
) -> None:
    response = await rig.ask(**{name: FORBIDDEN[name]})
    assert response.status_code == 422, (name, response.text)
    assert rig.rows() == [] and rig.enqueued == []
    assert rig.requested_events() == []


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
async def test_no_query_parameter_reaches_anything_on_any_deployment_route(
    rig: Rig, name: str
) -> None:
    fake = rig.insert("REQUESTED")
    refused = await call(rig.client, "GET", f"{DEPLOYMENTS}?{name}=x")
    assert refused.status_code == 422, (name, refused.text)
    # A query on a route that takes none is ignored: it changes nothing, and cannot select a job.
    cancelled = await call(rig.client, "POST", f"{DEPLOYMENTS}/{fake}/cancel?{name}=x")
    assert cancelled.status_code == 200, cancelled.text
    assert [r["execution_status"] for r in rig.rows()] == ["CANCELLED"]


async def test_the_openapi_body_has_exactly_four_fields_and_forbids_the_rest() -> None:
    from app.deployments.deployment_schemas import DeploymentRequest

    schema = DeploymentRequest.model_json_schema()
    assert sorted(schema["properties"]) == ["application_id", "environment", "reason", "ref"]
    assert schema["additionalProperties"] is False


# --- 21.3: the detail -------------------------------------------------------------------------


async def test_the_detail_has_the_documented_fields_and_no_url_anywhere(rig: Rig) -> None:
    created = (await rig.ask()).json()
    response = await call(rig.client, "GET", f"{DEPLOYMENTS}/{created['deployment_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "deployment_id",
        "kind",
        "application_id",
        "application_name",
        "environment",
        "requested_ref",
        "commit_sha",
        "execution_status",
        "approval_status",
        "requested_by",
        "created_at",
        "started_at",
        "completed_at",
        "deadline_at",
        "failure_reason",
        "correlation_id",
        "target",
        "requested_reason",
        "external_execution_id",
        "pipeline_result",
        "health_check_result",
        "sanitized_result",
        "last_poll_error",
        "last_polled_at",
        "approvals",
        "rollback_of_deployment_id",
        "poll_after_seconds",
    }
    assert body["execution_status"] == "REQUESTED" and body["poll_after_seconds"] == 5
    assert body["approvals"] == [] and body["pipeline_result"] is None
    assert body["requested_by"]["id"] == str(rig.caller)
    assert "http" not in response.text and "links" not in body


async def test_a_finished_deployment_shows_its_result_and_the_explicit_health_statement(
    rig: Rig,
) -> None:
    done = rig.insert("SUCCEEDED", result={"pipeline_result": "SUCCESS"})
    body = (await call(rig.client, "GET", f"{DEPLOYMENTS}/{done}")).json()
    assert body["execution_status"] == "SUCCEEDED" and body["pipeline_result"] == "SUCCESS"
    assert body["external_execution_id"] == "7" and body["poll_after_seconds"] is None
    assert body["health_check_result"] == policy.none_configured()
    failed = rig.insert(
        "FAILED", result={"pipeline_result": "FAILURE", "reconciliation_required": False}
    )
    body = (await call(rig.client, "GET", f"{DEPLOYMENTS}/{failed}")).json()
    assert body["failure_reason"] == "PIPELINE_FAILED" and body["pipeline_result"] == "FAILURE"


async def test_an_unknown_id_is_404(rig: Rig) -> None:
    assert (await call(rig.client, "GET", f"{DEPLOYMENTS}/{uuid.uuid4()}")).status_code == 404
    assert (await call(rig.client, "GET", f"{DEPLOYMENTS}/not-a-uuid")).status_code == 422


async def test_visibility_is_the_requester_or_deployment_read_over_the_stored_scope(
    rig: Rig, auth_app: Any
) -> None:
    other = await _second_login(rig.world, auth_app[1])
    mine = (await rig.ask()).json()["deployment_id"]
    try:
        # No role at all: not the requester, holds nothing -- the route's own floor refuses (403).
        refused = await call(other.client, "GET", f"{DEPLOYMENTS}/{mine}")
        assert refused.status_code == 403
        # A reader of the wrong environment, or of another application, sees it as absent.
        for scope in (
            '{"environment":["development"]}',
            f'{{"environment":["staging"],"application":["{uuid.uuid4()}"]}}',
        ):
            grant(rig.engine, other.id, "VIEWER", scope)
            assert (await call(other.client, "GET", f"{DEPLOYMENTS}/{mine}")).status_code == 404
            revoke_all(rig.engine, other.id)
        # A reader of this environment and application sees it.
        grant(rig.engine, other.id, "VIEWER", '{"environment":["staging"]}')
        assert (await call(other.client, "GET", f"{DEPLOYMENTS}/{mine}")).status_code == 200
    finally:
        revoke_all(rig.engine, other.id)
        await other.client.aclose()


async def test_the_requester_sees_their_own_even_after_losing_read_scope(rig: Rig) -> None:
    mine = (await rig.ask()).json()["deployment_id"]
    rig.as_role("VIEWER", '{"environment":["development"]}')
    assert (await call(rig.client, "GET", f"{DEPLOYMENTS}/{mine}")).status_code == 200


# --- 21.1: the list ---------------------------------------------------------------------------


async def test_the_list_is_scoped_in_sql_to_your_own_and_what_you_may_read(
    rig: Rig, auth_app: Any
) -> None:
    other = await _second_login(rig.world, auth_app[1])
    grant(rig.engine, other.id, "DEVOPS_ENGINEER")
    try:
        development_target = await rig.register_target(rig.application, environment="development")
        theirs = rig.insert("FAILED", requested_by=other.id)
        mine = rig.insert("FAILED")
        development = rig.insert(
            "FAILED",
            requested_by=other.id,
            environment="development",
            target_id=uuid.UUID(development_target),
        )

        async def seen_by(client: httpx.AsyncClient) -> set[str]:
            got = await call(client, "GET", DEPLOYMENTS)
            assert got.status_code == 200, got.text
            return {i["deployment_id"] for i in got.json()["items"]}

        # A staging-only reader: staging (the requester's and somebody else's), never development
        # that somebody else requested.
        rig.as_role("VIEWER", '{"environment":["staging"]}')
        assert await seen_by(rig.client) == {theirs, mine}
        # A reader of nothing they could use: only what they requested themselves.
        revoke_all(rig.engine, other.id)
        grant(rig.engine, other.id, "VIEWER", '{"environment":["other"]}')
        assert await seen_by(other.client) == {theirs, development}
        # A reader of development sees the development one and, as requester, their own.
        revoke_all(rig.engine, other.id)
        grant(rig.engine, other.id, "VIEWER", '{"environment":["development"]}')
        assert await seen_by(other.client) == {theirs, development}
        rig.as_role("VIEWER", '{"environment":["development"]}')
        assert await seen_by(rig.client) == {mine, development}
    finally:
        revoke_all(rig.engine, other.id)
        await other.client.aclose()


async def test_filters_only_narrow_never_widen(rig: Rig, auth_app: Any) -> None:
    other = await _second_login(rig.world, auth_app[1])
    try:
        development_target = await rig.register_target(rig.application, environment="development")
        rig.insert(
            "FAILED",
            requested_by=other.id,
            environment="development",
            target_id=uuid.UUID(development_target),
        )
        mine = rig.insert("FAILED")
        rig.as_role("VIEWER", '{"environment":["staging"]}')
        # Asking for somebody else's, or for development, returns what the caller may see: nothing.
        by_other = await call(rig.client, "GET", f"{DEPLOYMENTS}?requested_by={other.id}")
        assert by_other.json()["items"] == []
        development = await call(rig.client, "GET", f"{DEPLOYMENTS}?environment=development")
        assert development.json()["items"] == []
        own = await call(rig.client, "GET", f"{DEPLOYMENTS}?requested_by={rig.caller}")
        assert [i["deployment_id"] for i in own.json()["items"]] == [mine]
    finally:
        await other.client.aclose()


async def test_the_list_filters_and_item_shape(rig: Rig) -> None:
    failed = rig.insert("FAILED")
    ok = rig.insert("SUCCEEDED", result={"pipeline_result": "SUCCESS"})
    rig.insert("CANCELLED")
    body = (await call(rig.client, "GET", f"{DEPLOYMENTS}?execution_status=SUCCEEDED")).json()
    assert [i["deployment_id"] for i in body["items"]] == [ok]
    both = await call(
        rig.client, "GET", f"{DEPLOYMENTS}?execution_status=SUCCEEDED&execution_status=FAILED"
    )
    assert {i["deployment_id"] for i in both.json()["items"]} == {ok, failed}
    item = body["items"][0]
    assert set(item) == {
        "deployment_id",
        "kind",
        "application_id",
        "application_name",
        "environment",
        "requested_ref",
        "commit_sha",
        "execution_status",
        "approval_status",
        "requested_by",
        "created_at",
        "started_at",
        "completed_at",
        "deadline_at",
        "failure_reason",
        "correlation_id",
    }
    assert body["page"] == {"limit": 50, "next_cursor": None, "has_more": False, "total": None}
    for path in (
        f"{DEPLOYMENTS}?application_id={rig.application}",
        f"{DEPLOYMENTS}?environment=staging&kind=DEPLOY",
    ):
        assert len((await call(rig.client, "GET", path)).json()["items"]) == 3
    assert (await call(rig.client, "GET", f"{DEPLOYMENTS}?kind=ROLLBACK")).json()["items"] == []


@pytest.mark.parametrize(
    "query",
    [
        "environment=prod",
        "execution_status=DONE",
        "execution_status=TIMED_OUT",
        "kind=REDEPLOY",
        "limit=0",
        "limit=201",
        "application_id=nope",
        "sort=created_at",
        "order=asc",
        "from=2026-01-01T00:00:00",  # no UTC offset
        "from=2026-02-01T00:00:00Z&to=2026-01-01T00:00:00Z",  # from after to
        "from=2025-01-01T00:00:00Z&to=2026-01-01T00:00:00Z",  # over 90 days
        "cursor=garbage",
    ],
)
async def test_a_bad_query_is_422_never_a_silent_clamp(rig: Rig, query: str) -> None:
    response = await call(rig.client, "GET", f"{DEPLOYMENTS}?{query}")
    assert response.status_code == 422, (query, response.text)


async def test_paging_is_stable_and_a_cursor_cannot_change_the_query(rig: Rig) -> None:
    ids = [rig.insert("FAILED") for _ in range(5)]
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        path = f"{DEPLOYMENTS}?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await call(rig.client, "GET", path)).json()
        seen += [i["deployment_id"] for i in page["items"]]
        cursor = page["page"]["next_cursor"]
        if not page["page"]["has_more"]:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))
    first = (await call(rig.client, "GET", f"{DEPLOYMENTS}?limit=2")).json()["page"]["next_cursor"]
    replayed = await call(
        rig.client, "GET", f"{DEPLOYMENTS}?limit=2&execution_status=FAILED&cursor={first}"
    )
    assert replayed.status_code == 422
    assert replayed.json()["error"]["details"]["fields"][0]["code"] == "invalid_cursor"


async def test_the_default_window_is_seven_days_and_a_stated_one_is_taken_exactly(
    rig: Rig,
) -> None:
    old = rig.insert("FAILED", created="2020-01-01T00:00:00+00:00")
    recent = rig.insert("FAILED")
    ids = lambda r: {i["deployment_id"] for i in r.json()["items"]}  # noqa: E731
    assert ids(await call(rig.client, "GET", DEPLOYMENTS)) == {recent}
    stated = await call(
        rig.client, "GET", f"{DEPLOYMENTS}?from=2019-12-31T00:00:00Z&to=2020-01-02T00:00:00Z"
    )
    assert ids(stated) == {old}


# --- 21.6: cancel -----------------------------------------------------------------------------


async def test_the_requester_cancels_before_any_claim_and_the_slot_is_freed(rig: Rig) -> None:
    created = (await rig.ask()).json()["deployment_id"]
    response = await call(rig.client, "POST", f"{DEPLOYMENTS}/{created}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_status"] == "CANCELLED" and body["completed_at"] is not None
    assert body["poll_after_seconds"] is None
    [row] = rig.rows(id=created)
    assert row["execution_status"] == "CANCELLED" and row["next_poll_at"] is None
    [event] = rig.audit("DEPLOYMENT_CANCELLED", target_id=created)
    assert event["actor_id"] == rig.caller and event["outcome"] == "SUCCESS"
    assert event["sanitized_metadata"] == {"application_id": rig.application, "from": "REQUESTED"}
    assert (await rig.ask()).status_code == 202, "the slot is free again"


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"])
async def test_a_deployment_a_worker_has_claimed_or_finished_cannot_be_cancelled(
    rig: Rig, status: str
) -> None:
    fake = rig.insert(status)
    response = await call(rig.client, "POST", f"{DEPLOYMENTS}/{fake}/cancel")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_state_transition"
    assert rig.rows(id=fake)[0]["execution_status"] == status
    assert rig.audit("DEPLOYMENT_CANCELLED", target_id=fake) == []


async def test_awaiting_approval_is_not_cancellable_until_the_approval_workflow_exists(
    rig: Rig,
) -> None:
    fake = rig.insert("AWAITING_APPROVAL")
    response = await call(rig.client, "POST", f"{DEPLOYMENTS}/{fake}/cancel")
    assert response.status_code == 409, response.text
    assert rig.rows(id=fake)[0]["execution_status"] == "AWAITING_APPROVAL"


async def test_a_cancel_that_loses_the_race_to_a_claim_is_409_never_a_false_cancelled(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = (await rig.ask()).json()["deployment_id"]
    real = lifecycle.transition_statement

    def claimed_first(*args: Any, **kwargs: Any) -> Any:
        # A worker claims the row between the handler's read and its write.
        rig.set_status(created, "QUEUED")
        return real(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "transition_statement", claimed_first)
    response = await call(rig.client, "POST", f"{DEPLOYMENTS}/{created}/cancel")
    assert response.status_code == 409, response.text
    assert rig.rows(id=created)[0]["execution_status"] == "QUEUED"
    assert rig.audit("DEPLOYMENT_CANCELLED", target_id=created) == []


async def test_someone_else_may_cancel_only_as_an_approver_in_scope(
    rig: Rig, auth_app: Any
) -> None:
    other = await _second_login(rig.world, auth_app[1])
    try:
        created = (await rig.ask()).json()["deployment_id"]
        # Can read it, may not cancel it: 403, and the refusal is audited.
        grant(rig.engine, other.id, "VIEWER", '{"environment":["staging"]}')
        refused = await call(other.client, "POST", f"{DEPLOYMENTS}/{created}/cancel")
        assert refused.status_code == 403, refused.text
        assert rig.rows(id=created)[0]["execution_status"] == "REQUESTED"
        assert rig.audit("AUTHZ_DENIED", target_id=created)
        # Cannot even see it: 404, not 403.
        revoke_all(rig.engine, other.id)
        grant(rig.engine, other.id, "VIEWER", '{"environment":["development"]}')
        hidden = await call(other.client, "POST", f"{DEPLOYMENTS}/{created}/cancel")
        assert hidden.status_code == 404
        # An approver over staging may.
        revoke_all(rig.engine, other.id)
        grant(rig.engine, other.id, "APPROVER", '{"environment":["staging"]}')
        allowed = await call(other.client, "POST", f"{DEPLOYMENTS}/{created}/cancel")
        assert allowed.status_code == 200, allowed.text
        [event] = rig.audit("DEPLOYMENT_CANCELLED", target_id=created)
        assert event["actor_id"] == other.id, "the canceller is the actor, not the requester"
    finally:
        revoke_all(rig.engine, other.id)
        await other.client.aclose()


async def test_cancel_of_an_unknown_deployment_is_404(rig: Rig) -> None:
    response = await call(rig.client, "POST", f"{DEPLOYMENTS}/{uuid.uuid4()}/cancel")
    assert response.status_code == 404


# --- 21.12: a target with a live deployment cannot be retired ---------------------------------


@pytest.mark.parametrize(
    "live", ["REQUESTED", "AWAITING_APPROVAL", "APPROVED", "QUEUED", "RUNNING"]
)
async def test_a_target_with_a_live_deployment_cannot_be_retired(rig: Rig, live: str) -> None:
    rig.insert(live)
    rig.as_role("ADMINISTRATOR")
    response = await call(rig.client, "DELETE", f"{TARGETS}/{rig.target}")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_state_transition"
    with rig.engine.connect() as connection:
        deleted = connection.execute(
            text("SELECT deleted_at FROM deployment_targets WHERE id = :i"), {"i": rig.target}
        ).scalar_one()
    assert deleted is None
    assert rig.audit("DEPLOYMENT_TARGET_RETIRED", target_id=rig.target) == []


@pytest.mark.parametrize("finished", ["SUCCEEDED", "FAILED", "CANCELLED"])
async def test_a_target_whose_deployments_have_finished_can_be_retired(
    rig: Rig, finished: str
) -> None:
    rig.insert(finished)
    rig.as_role("ADMINISTRATOR")
    assert (await call(rig.client, "DELETE", f"{TARGETS}/{rig.target}")).status_code == 204


async def test_retiring_is_possible_once_the_live_deployment_is_cancelled(rig: Rig) -> None:
    created = (await rig.ask()).json()["deployment_id"]
    rig.as_role("ADMINISTRATOR")
    assert (await call(rig.client, "DELETE", f"{TARGETS}/{rig.target}")).status_code == 409
    assert (await call(rig.client, "POST", f"{DEPLOYMENTS}/{created}/cancel")).status_code == 200
    assert (await call(rig.client, "DELETE", f"{TARGETS}/{rig.target}")).status_code == 204


# --- the target an application has is the one it is registered with --------------------------


async def test_a_target_registered_through_the_api_is_what_a_deployment_names(rig: Rig) -> None:
    body = (await rig.ask()).json()
    assert (
        body["target"]["pipeline_reference"] == target_body(rig.application)["pipeline_reference"]
    )
    [row] = rig.rows(id=body["deployment_id"])
    assert str(row["deployment_target_id"]) == rig.target
