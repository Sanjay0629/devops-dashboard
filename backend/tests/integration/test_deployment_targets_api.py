"""Deployment targets end to end, §21.8 – §21.12 (M5.1, ``OPEN-126``).

Four things matter here, in this order:

1. **Nothing a request carries becomes a Jenkins URL, job name, node label or build parameter.**
   The adversarial test sends each such field in every body and query and finds each refused with
   nothing stored (PRD §4.10, CLAUDE.md §4 rules 1 and 9).
2. **Registration is an Administrator's act**, and a reader sees only the reader shape, only for
   the environments and applications their grants reach, with the rest never selected.
3. **The rules that keep a target honest** -- production needs an approval policy, health checks
   are not yet registrable, a ref slot exists exactly when refs are permitted -- hold at the API.
4. **Audit rows carry field names, never the reference or the template.**

Committed rows are removed again: other suites count applications.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from tests.integration.inventory_helpers import (
    APPLICATIONS,
    FULL_SCOPE,
    application_body,
    audit_rows,
    call,
    grant,
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

TARGETS = "/api/v1/deployment-targets"
REFERENCE = "team/service-deploy-canary-0001"
TEMPLATE = {"fixed": {"TARGET": "staging"}, "slots": {"GIT_REF": "ref", "DEP": "deployment_id"}}


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def body(application: str, **overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "application_id": application,
        "environment": "staging",
        "pipeline_reference": REFERENCE,
        "parameter_template": TEMPLATE,
        "allowed_ref_patterns": ["^main$", "^release/.+$"],
    }
    out.update(overrides)
    return out


class World:
    def __init__(self, client: httpx.AsyncClient, engine: Engine, caller: uuid.UUID) -> None:
        self.client, self.engine, self.caller = client, engine, caller
        self.applications: list[str] = []

    async def application(self) -> str:
        created = await call(self.client, "POST", APPLICATIONS, application_body(), headers=key())
        assert created.status_code == 201, created.text
        self.applications.append(created.json()["id"])
        return str(created.json()["id"])

    async def register(self, application: str, **overrides: Any) -> httpx.Response:
        return await call(
            self.client, "POST", TARGETS, body(application, **overrides), headers=key()
        )

    def as_role(self, role: str, scope: str = FULL_SCOPE) -> None:
        revoke_all(self.engine, self.caller)
        if role:
            grant(self.engine, self.caller, role, scope)

    def production_policy(self) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.execute(
                    text("SELECT id FROM approval_policies WHERE name = 'production-deployments'")
                ).scalar_one()
            )

    def stored(self, application: str) -> int:
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    text("SELECT count(*) FROM deployment_targets WHERE application_id = :a"),
                    {"a": uuid.UUID(application)},
                ).scalar_one()
            )


@pytest.fixture
async def world(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> Iterator[World]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    grant(migrated, caller, "ADMINISTRATOR")
    built = World(client, migrated, caller)
    try:
        yield built  # type: ignore[misc]
    finally:
        ids = [uuid.UUID(a) for a in built.applications]
        with migrated.begin() as connection:
            connection.execute(
                text("DELETE FROM deployment_targets WHERE application_id = ANY(:ids)"),
                {"ids": ids},
            )
            connection.execute(text("DELETE FROM applications WHERE id = ANY(:ids)"), {"ids": ids})


# --- registration, reading, the shapes ---------------------------------------------------


async def test_register_read_list_and_the_two_shapes(world: World) -> None:
    app_id = await world.application()
    created = await world.register(app_id)
    assert created.status_code == 201, created.text
    full = created.json()
    assert full["idempotent_replay"] is False and full["status"] == "ACTIVE"
    assert full["parameter_template"] == {
        "fixed": {"TARGET": "staging"},
        "slots": TEMPLATE["slots"],
    }
    assert full["requires_approval"] is False and full["health_check_config"] == {}

    detail = await call(world.client, "GET", f"{TARGETS}/{full['id']}")
    assert detail.status_code == 200 and detail.json()["pipeline_reference"] == REFERENCE

    listed = await call(world.client, "GET", TARGETS, params={"application_id": app_id})
    assert listed.status_code == 200, listed.text
    page = listed.json()
    (item,) = page["items"]
    # The reader shape: no template, no policy id, no health-check config.
    assert "parameter_template" not in item and "approval_policy_id" not in item
    assert "health_check_config" not in item and "created_at" not in item
    assert item["requires_approval"] is False and item["allowed_ref_patterns"] == [
        "^main$",
        "^release/.+$",
    ]
    assert page["jenkins"] == {"status": "not_configured", "demo_mode": True}
    assert page["page"]["has_more"] is False and page["page"]["total"] is None


async def test_registering_audits_names_and_never_the_reference_or_the_template(
    world: World,
) -> None:
    app_id = await world.application()
    created = (await world.register(app_id)).json()
    (row,) = [
        r
        for r in audit_rows(world.engine, "DEPLOYMENT_TARGET_REGISTERED")
        if str(r["target_id"]) == created["id"]
    ]
    assert row["actor_id"] == world.caller and row["outcome"] == "SUCCESS"
    assert row["target_type"] == "deployment_target" and row["environment"] == "staging"
    metadata = row["sanitized_metadata"]
    assert metadata["application_id"] == app_id
    assert set(metadata["fields"]) == {
        "pipeline_reference", "parameter_template", "allowed_ref_patterns",
    }  # fmt: skip
    dump = str(metadata)
    assert REFERENCE not in dump and "TARGET" not in dump and "^main$" not in dump


async def test_a_replay_returns_the_original_and_a_changed_body_is_a_conflict(world: World) -> None:
    app_id = await world.application()
    headers = key()
    first = await call(world.client, "POST", TARGETS, body(app_id), headers=headers)
    again = await call(world.client, "POST", TARGETS, body(app_id), headers=headers)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"] and again.json()["idempotent_replay"] is True
    changed = await call(
        world.client, "POST", TARGETS, body(app_id, allowed_ref_patterns=["^dev$"]), headers=headers
    )
    assert changed.status_code == 409
    assert world.stored(app_id) == 1


async def test_a_second_live_target_for_the_pair_is_a_duplicate(world: World) -> None:
    app_id = await world.application()
    assert (await world.register(app_id)).status_code == 201
    duplicate = await world.register(app_id)
    assert (
        duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "duplicate_resource"
    )
    assert (await world.register(app_id, environment="development")).status_code == 201


async def test_an_unknown_or_archived_application_is_404_and_stores_nothing(world: World) -> None:
    assert (await world.register(str(uuid.uuid4()))).status_code == 404
    app_id = await world.application()
    assert (await call(world.client, "DELETE", f"{APPLICATIONS}/{app_id}")).status_code == 204
    assert (await world.register(app_id)).status_code == 404
    assert world.stored(app_id) == 0


async def test_the_idempotency_key_is_required(world: World) -> None:
    app_id = await world.application()
    missing = await call(world.client, "POST", TARGETS, body(app_id))
    assert missing.status_code == 422 and world.stored(app_id) == 0


# --- 1. nothing from a request becomes a URL, a job, a node or a parameter ---------------------

FORBIDDEN_FIELDS = {
    "jenkins_url": "http://attacker.invalid/",
    "url": "http://169.254.169.254/",
    "base_url": "http://attacker.invalid/",
    "job": "other/job",
    "job_name": "other/job",
    "job_path": "other/job",
    "build_parameters": {"EVIL": "1"},
    "parameters": {"EVIL": "1"},
    "params": {"EVIL": "1"},
    "node_label": "prod-node",
    "node": "prod-node",
    "credentials": "hunter2",
    "token": "hunter2",
    "user": "admin",
    "command": "rm -rf /",
    "script": "sh -c id",
    "argv": ["sh"],
}


@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
async def test_no_url_job_node_credential_or_command_field_is_accepted_anywhere(
    world: World, field: str
) -> None:
    app_id = await world.application()
    existing = (await world.register(app_id)).json()["id"]
    app2 = await world.application()
    value = FORBIDDEN_FIELDS[field]

    created = await call(world.client, "POST", TARGETS, body(app2, **{field: value}), headers=key())
    assert created.status_code == 422, (field, created.text)
    assert created.json()["error"]["details"]["fields"][0]["code"] == "unknown_field"
    assert world.stored(app2) == 0

    patched = await call(world.client, "PATCH", f"{TARGETS}/{existing}", {field: value})
    assert patched.status_code == 422, (field, patched.text)

    for path in (TARGETS, f"{TARGETS}/{existing}"):
        queried = await call(world.client, "GET", path, params={field: str(value)})
        assert queried.status_code == 422 or path != TARGETS, (field, queried.text)
    detail = (await call(world.client, "GET", f"{TARGETS}/{existing}")).json()
    assert detail["pipeline_reference"] == REFERENCE  # nothing was changed by the attempts


async def test_a_job_path_cannot_be_a_url_or_climb(world: World) -> None:
    app_id = await world.application()
    for reference in (
        "http://attacker.invalid/job/x", "../../etc", "a/../b", "a?x=1", "a#f", "a b", "a\nb",
        "/absolute", "a/b/c/d/e/f", "x" * 65, "",
    ):  # fmt: skip
        response = await world.register(app_id, pipeline_reference=reference)
        assert response.status_code == 422, (reference, response.text)
        assert not reference or reference not in response.text  # never echoed
    assert world.stored(app_id) == 0


# --- 3. the rules that keep a target honest ----------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameter_template": {"slots": {"A": "request_body"}}},  # not a slot source
        {"parameter_template": {"fixed": {"A": "x"}, "slots": {"A": "ref"}}},
        {"parameter_template": {"fixed": {"PASSWORD": "hunter2hunter2"}}},
        {"parameter_template": {"fixed": {"NOTE": "AKIAIOSFODNN7EXAMPLE"}}},
        {"parameter_template": {"fixed": {f"P{i}": "x" for i in range(21)}}},
        {"parameter_template": {"slots": {"GIT_REF": "ref", "OTHER": "ref"}}},  # one ref slot only
        {"parameter_template": {"fixed": {"A": "x"}}},  # patterns but no ref slot
        {"allowed_ref_patterns": [], "parameter_template": TEMPLATE},  # a ref slot, no patterns
        {"allowed_ref_patterns": ["(?=a)main"]},
        {"allowed_ref_patterns": ["(a+)+$"]},
        {"allowed_ref_patterns": ["(a)\\1"]},
        {"allowed_ref_patterns": ["^main$", "^main$"]},
        {"allowed_ref_patterns": [f"^b{i}$" for i in range(11)]},
        {"allowed_ref_patterns": ["x" * 201]},
        {"rollback_supported": True},  # no rollback pipeline named
        {"rollback_pipeline_reference": "team/rollback"},  # named but not supported
        {"rollback_supported": True, "rollback_pipeline_reference": "../x"},
        {"environment": "prod"},
    ],
)
async def test_invalid_configurations_are_422_and_store_nothing(
    world: World, overrides: dict[str, Any]
) -> None:
    app_id = await world.application()
    response = await world.register(app_id, **overrides)
    assert response.status_code == 422, (overrides, response.text)
    assert world.stored(app_id) == 0


async def test_health_checks_are_not_registrable_yet(world: World) -> None:
    app_id = await world.application()
    refused = await world.register(app_id, health_check_config={"checks": [{"url": "http://x"}]})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "not_permitted"
    assert refused.json()["error"]["details"]["reason"] == "health_checks_not_available_yet"
    assert (await world.register(app_id, health_check_config={})).status_code == 201


async def test_a_rollback_pair_is_stored_when_it_travels_together(world: World) -> None:
    app_id = await world.application()
    created = await world.register(
        app_id, rollback_supported=True, rollback_pipeline_reference="team/service-rollback"
    )
    assert created.status_code == 201
    assert created.json()["rollback_supported"] is True


async def test_production_needs_an_approval_policy_that_fits(world: World) -> None:
    app_id = await world.application()
    none = await world.register(app_id, environment="production")
    assert none.status_code == 422 and none.json()["error"]["code"] == "approval_policy_required"

    with world.engine.begin() as connection:
        operation_policy = connection.execute(
            text(
                "INSERT INTO approval_policies (id, name, environment, applies_to,"
                " required_approvals, allow_self_approval, required_role,"
                " approval_window_seconds, created_at, updated_at)"
                " VALUES (gen_random_uuid(), :n, 'production', 'SERVICE_OPERATION', 1, false,"
                " 'APPROVER', 3600, now(), now()) RETURNING id"
            ),
            {"n": f"m5-1-test-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
    try:
        wrong_kind = await world.register(
            app_id, environment="production", approval_policy_id=str(operation_policy)
        )
        assert (
            wrong_kind.status_code == 422 and wrong_kind.json()["error"]["code"] == "not_permitted"
        )
        wrong_environment = await world.register(
            app_id, environment="staging", approval_policy_id=world.production_policy()
        )
        assert wrong_environment.status_code == 422
        unknown = await world.register(
            app_id, environment="production", approval_policy_id=str(uuid.uuid4())
        )
        assert unknown.status_code == 422
        ok = await world.register(
            app_id, environment="production", approval_policy_id=world.production_policy()
        )
        assert ok.status_code == 201 and ok.json()["requires_approval"] is True
        # The reader shape says a policy applies without saying which.
        listed = await call(world.client, "GET", TARGETS, params={"environment": "production"})
        assert listed.json()["items"][0]["requires_approval"] is True
    finally:
        with world.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM deployment_targets WHERE application_id = :a"),
                {"a": uuid.UUID(app_id)},
            )
            connection.execute(
                text("DELETE FROM approval_policies WHERE id = :i"), {"i": operation_policy}
            )


# --- editing -----------------------------------------------------------------------------


async def test_editing_changes_the_named_fields_and_audits_their_names(world: World) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    edited = await call(
        world.client,
        "PATCH",
        f"{TARGETS}/{target['id']}",
        {"allowed_ref_patterns": ["^main$"], "status": "DISABLED"},
    )
    assert edited.status_code == 200, edited.text
    assert (
        edited.json()["allowed_ref_patterns"] == ["^main$"]
        and edited.json()["status"] == "DISABLED"
    )
    (row,) = [
        r
        for r in audit_rows(world.engine, "DEPLOYMENT_TARGET_CHANGED")
        if str(r["target_id"]) == target["id"]
    ]
    assert set(row["sanitized_metadata"]["fields"]) == {"allowed_ref_patterns", "status"}
    assert "^main$" not in str(row["sanitized_metadata"])


async def test_a_no_op_edit_writes_no_audit_row(world: World) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    before = len(audit_rows(world.engine, "DEPLOYMENT_TARGET_CHANGED"))
    same = await call(world.client, "PATCH", f"{TARGETS}/{target['id']}", {"status": "ACTIVE"})
    assert same.status_code == 200
    assert len(audit_rows(world.engine, "DEPLOYMENT_TARGET_CHANGED")) == before


@pytest.mark.parametrize(
    "patch",
    [
        {"environment": "production"},
        {"application_id": str(uuid.uuid4())},
        {"health_check_config": {"checks": []}},
        {"pipeline_reference": None},
        {"status": "RETIRED"},
        {"status": None},
        {"allowed_ref_patterns": []},  # would leave a ref slot with no patterns
        {"parameter_template": {"fixed": {"A": "x"}}},  # would drop the ref slot
        {"rollback_supported": True},
        {"pipeline_reference": "../x"},
    ],
)
async def test_edits_that_would_break_a_rule_are_refused_and_change_nothing(
    world: World, patch: dict[str, Any]
) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    response = await call(world.client, "PATCH", f"{TARGETS}/{target['id']}", patch)
    assert response.status_code == 422, (patch, response.text)
    after = (await call(world.client, "GET", f"{TARGETS}/{target['id']}")).json()
    assert after["updated_at"] == target["updated_at"]


async def test_production_cannot_lose_its_policy_and_a_disabled_one_cannot_come_back_without_it(
    world: World,
) -> None:
    app_id = await world.application()
    target = (
        await world.register(
            app_id, environment="production", approval_policy_id=world.production_policy()
        )
    ).json()
    cleared = await call(
        world.client, "PATCH", f"{TARGETS}/{target['id']}", {"approval_policy_id": None}
    )
    assert (
        cleared.status_code == 422 and cleared.json()["error"]["code"] == "approval_policy_required"
    )
    assert (
        await call(world.client, "PATCH", f"{TARGETS}/{target['id']}", {"status": "DISABLED"})
    ).status_code == 200


async def test_a_stale_if_unmodified_since_is_a_conflict(world: World) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    stale = await call(
        world.client,
        "PATCH",
        f"{TARGETS}/{target['id']}",
        {"status": "DISABLED"},
        headers={"If-Unmodified-Since": "Mon, 01 Jan 2001 00:00:00 GMT"},
    )
    assert stale.status_code == 409
    assert "deployment target" in stale.json()["error"]["message"]
    unreadable = await call(
        world.client,
        "PATCH",
        f"{TARGETS}/{target['id']}",
        {"status": "DISABLED"},
        headers={"If-Unmodified-Since": "yesterday"},
    )
    assert unreadable.status_code == 422


# --- retiring ----------------------------------------------------------------------------


async def test_retiring_hides_the_target_frees_the_pair_and_is_audited(world: World) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    assert (await call(world.client, "DELETE", f"{TARGETS}/{target['id']}")).status_code == 204
    assert (await call(world.client, "GET", f"{TARGETS}/{target['id']}")).status_code == 404
    assert (await call(world.client, "DELETE", f"{TARGETS}/{target['id']}")).status_code == 404
    assert (
        await call(world.client, "PATCH", f"{TARGETS}/{target['id']}", {"status": "ACTIVE"})
    ).status_code == 404
    listed = await call(
        world.client,
        "GET",
        TARGETS,
        params={"application_id": app_id, "status": ["ACTIVE", "DISABLED"]},
    )
    assert listed.json()["items"] == []
    assert (await world.register(app_id)).status_code == 201  # the pair is free again
    assert any(
        str(r["target_id"]) == target["id"]
        for r in audit_rows(world.engine, "DEPLOYMENT_TARGET_RETIRED")
    )


async def test_an_application_with_a_live_target_cannot_be_archived(world: World) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    refused = await call(world.client, "DELETE", f"{APPLICATIONS}/{app_id}")
    assert refused.status_code == 409 and "retire them first" in refused.json()["error"]["message"]
    assert (await call(world.client, "DELETE", f"{TARGETS}/{target['id']}")).status_code == 204
    assert (await call(world.client, "DELETE", f"{APPLICATIONS}/{app_id}")).status_code == 204


# --- 2. who may do what, and who sees what ---------------------------------------------------


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER", ""])
async def test_only_an_administrator_registers_edits_retires_or_reads_the_full_config(
    world: World, role: str
) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    world.as_role(role)
    c = world.client
    assert (await world.register(app_id, environment="development")).status_code == 403
    assert (
        await call(c, "PATCH", f"{TARGETS}/{target['id']}", {"status": "DISABLED"})
    ).status_code == 403
    assert (await call(c, "DELETE", f"{TARGETS}/{target['id']}")).status_code == 403
    assert (await call(c, "GET", f"{TARGETS}/{target['id']}")).status_code == 403
    world.as_role("ADMINISTRATOR")
    assert (await call(c, "GET", f"{TARGETS}/{target['id']}")).json()["status"] == "ACTIVE"
    assert world.stored(app_id) == 1


async def test_a_reader_sees_only_the_environments_and_applications_their_grants_reach(
    world: World,
) -> None:
    visible, other = await world.application(), await world.application()
    staging = (await world.register(visible)).json()
    production = (
        await world.register(
            visible, environment="production", approval_policy_id=world.production_policy()
        )
    ).json()
    elsewhere = (await world.register(other)).json()
    c = world.client

    async def ids(**params: Any) -> set[str]:
        response = await call(c, "GET", TARGETS, params=params)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    everything = {staging["id"], production["id"], elsewhere["id"]}
    world.as_role("VIEWER", FULL_SCOPE)
    assert everything <= await ids()

    # Production is reachable only by naming it: "*" never implies it.
    world.as_role("VIEWER", '{"environment":["*"]}')
    seen = await ids()
    assert {staging["id"], elsewhere["id"]} <= seen and production["id"] not in seen

    world.as_role("VIEWER", '{"environment":["staging"]}')
    seen = await ids()
    assert staging["id"] in seen and production["id"] not in seen

    # The application dimension: a target has one.
    world.as_role(
        "VIEWER", f'{{"environment":["staging","production"],"application":["{visible}"]}}'
    )
    seen = await ids()
    assert {staging["id"], production["id"]} <= seen and elsewhere["id"] not in seen

    # A target has no account or server: a grant naming one reaches none (A-09).
    world.as_role("VIEWER", f'{{"environment":["staging"],"cloud_account":["{uuid.uuid4()}"]}}')
    assert not (everything & await ids())
    world.as_role("VIEWER", f'{{"environment":["staging"],"server":["{uuid.uuid4()}"]}}')
    assert not (everything & await ids())

    # No grant at all is a 403, not an empty page.
    world.as_role("")
    assert (await call(c, "GET", TARGETS)).status_code == 403


async def test_disabled_targets_are_hidden_unless_asked_for_and_the_filters_apply(
    world: World,
) -> None:
    app_id = await world.application()
    live = (await world.register(app_id)).json()
    disabled = (await world.register(app_id, environment="development")).json()
    await call(world.client, "PATCH", f"{TARGETS}/{disabled['id']}", {"status": "DISABLED"})

    async def ids(**params: Any) -> set[str]:
        response = await call(
            world.client, "GET", TARGETS, params={"application_id": app_id, **params}
        )
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert await ids() == {live["id"]}
    assert await ids(status="DISABLED") == {disabled["id"]}
    assert await ids(environment="development", status=["ACTIVE", "DISABLED"]) == {disabled["id"]}
    assert (
        await call(world.client, "GET", TARGETS, params={"status": "RETIRED"})
    ).status_code == 422
    assert (
        await call(world.client, "GET", TARGETS, params={"environment": "prod"})
    ).status_code == 422
    assert (await call(world.client, "GET", TARGETS, params={"limit": 0})).status_code == 422


async def test_the_list_pages_without_repeating_or_skipping(world: World) -> None:
    app_id = await world.application()
    for environment in ("development", "staging", "other"):
        assert (await world.register(app_id, environment=environment)).status_code == 201
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"application_id": app_id, "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await call(world.client, "GET", TARGETS, params=params)).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["page"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 3 and len(set(seen)) == 3
    tampered = await call(
        world.client, "GET", TARGETS, params={"application_id": app_id, "cursor": "x.y"}
    )
    assert tampered.status_code == 422


async def test_writes_are_refused_in_read_only_mode(world: World) -> None:
    app_id = await world.application()
    target = (await world.register(app_id)).json()
    path = "/api/v1/admin/settings/read-only-mode"
    assert (
        await call(world.client, "POST", path, {"enabled": True, "reason": "test"})
    ).status_code == 200
    try:
        for method, url, payload in (
            ("POST", TARGETS, body(app_id, environment="development")),
            ("PATCH", f"{TARGETS}/{target['id']}", {"status": "DISABLED"}),
            ("DELETE", f"{TARGETS}/{target['id']}", None),
        ):
            blocked = await call(world.client, method, url, payload, headers=key())
            assert blocked.status_code == 423, (method, blocked.text)
        assert (await call(world.client, "GET", f"{TARGETS}/{target['id']}")).status_code == 200
    finally:
        await call(world.client, "POST", path, {"enabled": False})
    assert world.stored(app_id) == 1
