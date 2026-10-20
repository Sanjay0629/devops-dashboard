"""§26.2 - §26.6, §26.1 and §14.8 end to end, against a stubbed AWS layer (M4.3).

The AWS adapter is the production one; its botocore clients are stubbed. A stub proves what DCP
does with an answer, never that AWS gives it -- only the manual check in infra/aws/README.md does.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.providers import registry, sync
from tests.integration.inventory_helpers import call
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
from tests.providers.aws.harness import INSTANCE, ROLE_ARN, Aws

PRODUCTION_ONLY = (
    '{"environment":["production"],"cloud_account":["*"],"server":["*"],"application":["*"]}'
)
ACCOUNTS = "/api/v1/cloud-accounts"
ACCOUNT = "123456789012"
BODY: dict[str, Any] = {
    "provider": "AWS",
    "account_reference": ACCOUNT,
    "display_name": "Stubbed sandbox",
    "region_configuration": {"allowed_regions": ["eu-west-1"], "default_region": "eu-west-1"},
    "credential_reference": ROLE_ARN,
}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[Aws]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    monkeypatch.setenv("DCP_AWS_SSM_DOCUMENT_VERSION", "3")
    get_settings.cache_clear()
    stubbed = Aws()
    monkeypatch.setitem(registry._FACTORIES, "AWS", lambda: stubbed.adapter)
    yield stubbed
    get_settings.cache_clear()


@pytest.fixture
def cleanup(rig: Rig) -> Iterator[None]:
    yield
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE cloud_accounts SET deleted_at = now() WHERE provider = 'AWS'")
        )


async def register(rig: Rig, **changes: Any):
    return await call(rig.client, "POST", ACCOUNTS, BODY | changes)


async def an_account(rig: Rig) -> str:
    response = await register(rig)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def audit_rows(rig: Rig, action: str) -> list[dict[str, Any]]:
    with rig.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT * FROM audit_events WHERE action_type = :a ORDER BY occurred_at"),
            {"a": action},
        ).mappings()
        return [dict(r) for r in rows]


def verify_ok(aws: Aws) -> None:
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})
    aws.ssm_stub.add_response(
        "describe_document", {"Document": {"Name": "DCP-ServiceControl", "Status": "Active"}}
    )


# --- §26.3 register ---------------------------------------------------------------------------


async def test_an_aws_account_is_registered_with_a_role_arn_and_shows_no_secret(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    response = await register(rig)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["credential_reference"] == ROLE_ARN  # an identifier, not a credential
    assert len(body["credential_fingerprint"]) == 12
    assert body["status"] == "ACTIVE" and body["verification"] is None  # unknown, not fine
    assert set(body) == {
        "id", "provider", "account_reference", "display_name", "region_configuration",
        "credential_reference", "credential_fingerprint", "status", "last_sync_at",
        "last_sync_error", "verification", "created_at", "updated_at",
    }  # fmt: skip
    [row] = [
        r
        for r in audit_rows(rig, "CLOUD_ACCOUNT_REGISTERED")
        if r["target_id"] == uuid.UUID(body["id"])
    ]
    assert row["actor_id"] == rig.caller and ROLE_ARN not in str(row["sanitized_metadata"])


async def test_a_duplicate_account_is_409(rig: Rig, aws: Aws, cleanup: None) -> None:
    await an_account(rig)
    assert (await register(rig)).status_code == 409


@pytest.mark.parametrize("provider", ["AZURE", "ON_PREM", "OTHER"])
async def test_a_provider_with_no_adapter_cannot_be_registered(
    rig: Rig, aws: Aws, cleanup: None, provider: str
) -> None:
    response = await register(rig, provider=provider)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["code"] == "no_adapter_for_provider"


@pytest.mark.parametrize(
    "reference",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "https://sts.example/assume?token=hunter2hunter2",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEYexamplekey",
        "secretstore://prod/aws",
    ],
)
async def test_a_secret_shaped_credential_is_refused_and_never_echoed(
    rig: Rig, aws: Aws, cleanup: None, reference: str
) -> None:
    response = await register(rig, credential_reference=reference)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["code"] == (
        "credentials_must_be_stored_in_secret_manager"
    )
    assert reference not in response.text


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"credential_reference": "not an arn"}, "invalid_credential_reference"),
        (
            {"credential_reference": "arn:aws:iam::999999999999:role/Other"},
            "credential_account_mismatch",
        ),
        ({"account_reference": "12345"}, "invalid_account_reference"),
    ],
)
async def test_a_malformed_reference_names_the_rule(
    rig: Rig, aws: Aws, cleanup: None, changes: dict, code: str
) -> None:
    response = await register(rig, **changes)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["code"] == code


@pytest.mark.parametrize(
    "regions",
    [
        {"allowed_regions": ["eu-west-1"], "default_region": "us-east-1"},
        {"allowed_regions": [], "default_region": "eu-west-1"},
        {"allowed_regions": ["EU West"], "default_region": "EU West"},
        {"allowed_regions": ["eu-west-1"], "default_region": "eu-west-1", "extra": 1},
    ],
)
async def test_the_region_boundary_is_validated(
    rig: Rig, aws: Aws, cleanup: None, regions: dict
) -> None:
    assert (await register(rig, region_configuration=regions)).status_code == 422


async def test_no_field_accepts_a_command(rig: Rig, aws: Aws, cleanup: None) -> None:
    for field in ("command", "script", "document_name", "endpoint_url", "access_key"):
        assert (await register(rig, **{field: "x"})).status_code == 422


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"])
async def test_only_an_administrator_may_manage_accounts(
    rig: Rig, aws: Aws, cleanup: None, role: str
) -> None:
    account = await an_account(rig)
    rig.narrow(role)
    for method, path, body in (
        ("GET", ACCOUNTS, None),
        ("POST", ACCOUNTS, BODY),
        ("PATCH", f"{ACCOUNTS}/{account}", {"display_name": "x"}),
        ("POST", f"{ACCOUNTS}/{account}/verify", None),
        ("POST", f"{ACCOUNTS}/{account}/sync", None),
    ):
        response = await call(rig.client, method, path, body)
        assert response.status_code == 403, (method, path)


# --- §26.2 / §26.4 ----------------------------------------------------------------------------


async def test_the_list_shows_the_account_and_its_cached_verification(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    listed = (await call(rig.client, "GET", ACCOUNTS)).json()["items"]
    mine = next(a for a in listed if a["id"] == account)
    assert mine["verification"] is None
    verify_ok(aws)
    assert (await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")).status_code == 200
    listed = (await call(rig.client, "GET", ACCOUNTS)).json()["items"]
    assert next(a for a in listed if a["id"] == account)["verification"]["permissions_ok"] is True


async def test_an_edit_changes_what_it_names_and_is_audited_by_field_name_only(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    response = await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", {"display_name": "Renamed"})
    assert response.status_code == 200 and response.json()["display_name"] == "Renamed"
    [row] = [
        r for r in audit_rows(rig, "CLOUD_ACCOUNT_UPDATED") if r["target_id"] == uuid.UUID(account)
    ]
    assert row["sanitized_metadata"]["fields"] == ["display_name"]


@pytest.mark.parametrize(
    "changes",
    [{"provider": "AZURE"}, {"account_reference": "999999999999"}, {"status": "ERROR"}],
)
async def test_what_cannot_change_is_refused(
    rig: Rig, aws: Aws, cleanup: None, changes: dict
) -> None:
    account = await an_account(rig)
    assert (await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", changes)).status_code == 422


async def test_disabling_is_audited_twice_and_re_enabling_is_not_a_disable(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    assert (
        await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", {"status": "DISABLED"})
    ).json()["status"] == "DISABLED"
    disabled = [
        r for r in audit_rows(rig, "CLOUD_ACCOUNT_DISABLED") if r["target_id"] == uuid.UUID(account)
    ]
    assert len(disabled) == 1
    await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", {"status": "ACTIVE"})
    again = [
        r for r in audit_rows(rig, "CLOUD_ACCOUNT_DISABLED") if r["target_id"] == uuid.UUID(account)
    ]
    assert len(again) == 1


async def test_changing_the_credential_clears_the_cached_verification(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    verify_ok(aws)
    await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")
    response = await call(
        rig.client,
        "PATCH",
        f"{ACCOUNTS}/{account}",
        {"credential_reference": "arn:aws:iam::123456789012:role/Another"},
    )
    assert response.status_code == 200 and response.json()["verification"] is None


async def test_an_unknown_account_is_404(rig: Rig, aws: Aws) -> None:
    missing = uuid.uuid4()
    for method, path in (
        ("PATCH", f"{ACCOUNTS}/{missing}"),
        ("POST", f"{ACCOUNTS}/{missing}/verify"),
        ("POST", f"{ACCOUNTS}/{missing}/sync"),
    ):
        response = await call(
            rig.client, method, path, {"display_name": "x"} if method == "PATCH" else None
        )
        assert response.status_code == 404, (method, path)


# --- §26.5 verify -----------------------------------------------------------------------------


async def test_verify_reports_what_a_read_only_probe_can_prove_and_lists_what_it_cannot(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    verify_ok(aws)
    response = await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["reachable"], body["permissions_ok"], body["message"]) == (True, True, "ok")
    assert body["missing_permissions"] == [] and body["not_probed"] == ["ssm:SendCommand"]
    aws.done()
    assert audit_rows(rig, "CLOUD_ACCOUNT_VERIFIED")[-1]["outcome"] == "SUCCESS"


async def test_verify_names_a_missing_read_permission(rig: Rig, aws: Aws, cleanup: None) -> None:
    account = await an_account(rig)
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})
    aws.error("describe_document", "AccessDeniedException", status=403)
    body = (await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")).json()
    assert body["permissions_ok"] is False and body["missing_permissions"] == [
        "ssm:DescribeDocument"
    ]
    assert body["message"] == "access_denied"
    assert audit_rows(rig, "CLOUD_ACCOUNT_VERIFIED")[-1]["outcome"] == "FAILURE"


async def test_verify_says_so_when_the_role_cannot_be_assumed(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    aws.error("assume_role", "AccessDenied", status=403, sts=True)
    body = (await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")).json()
    assert body["reachable"] is False and body["permissions_ok"] is False
    assert (
        body["missing_permissions"] == ["sts:AssumeRole"]
        and body["message"] == "assume_role_denied"
    )


async def test_verify_with_a_missing_document_is_not_ok(rig: Rig, aws: Aws, cleanup: None) -> None:
    account = await an_account(rig)
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})
    aws.error("describe_document", "InvalidDocument")
    body = (await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")).json()
    assert body["permissions_ok"] is False and body["message"] == "document_not_found"


async def test_verify_is_a_503_when_the_adapter_is_not_configured(
    rig: Rig, aws: Aws, cleanup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await an_account(rig)
    monkeypatch.setattr(registry, "_FACTORIES", {})
    response = await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")
    assert response.status_code == 503 and response.json()["error"]["code"] == "not_configured"


async def test_a_static_key_makes_the_adapter_unconfigured(
    rig: Rig, cleanup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.providers.aws import build_adapter

    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    monkeypatch.setenv("DCP_AWS_SSM_DOCUMENT_VERSION", "3")
    get_settings.cache_clear()
    monkeypatch.setitem(registry._FACTORIES, "AWS", build_adapter)
    account = await an_account(rig)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATESTINGNOTAKEY00")
    response = await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")
    assert response.status_code == 503
    assert response.json()["error"]["details"]["reason"] == "static_credentials_present"


# --- §26.1 ------------------------------------------------------------------------------------


async def integration_row(rig: Rig, name_part: str) -> dict[str, Any]:
    rows = (await call(rig.client, "GET", "/api/v1/integrations/status")).json()["integrations"]
    return next(r for r in rows if name_part in r["name"])


async def test_the_status_page_has_a_row_per_account_and_unproven_is_not_ok(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    row = await integration_row(rig, "Stubbed sandbox")
    assert (row["status"], row["last_error_message"]) == ("degraded", "not_verified_yet")
    verify_ok(aws)
    await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")
    assert (await integration_row(rig, "Stubbed sandbox"))["status"] == "ok"
    await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", {"status": "DISABLED"})
    row = await integration_row(rig, "Stubbed sandbox")
    assert (row["status"], row["last_error_message"]) == ("not_configured", "account_disabled")


async def test_a_failed_verification_is_unavailable_with_its_reason(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    aws.error("assume_role", "AccessDenied", status=403, sts=True)
    await call(rig.client, "POST", f"{ACCOUNTS}/{account}/verify")
    row = await integration_row(rig, "Stubbed sandbox")
    assert (row["status"], row["last_error_message"]) == ("unavailable", "assume_role_denied")


# --- §26.6 sync -------------------------------------------------------------------------------


async def test_a_sync_request_enqueues_two_ids_and_nothing_else(
    rig: Rig, aws: Aws, cleanup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers.celery_app import celery_app

    sent: list[tuple[str, tuple, str]] = []

    class Result:
        id = "task-1"

    def fake(name: str, args: list, queue: str, retry: bool) -> Result:
        sent.append((name, tuple(args), queue))
        return Result()

    monkeypatch.setattr(celery_app, "send_task", fake)
    account = await an_account(rig)
    response = await call(rig.client, "POST", f"{ACCOUNTS}/{account}/sync")
    assert response.status_code == 202 and response.json() == {"task_id": "task-1"}
    assert sent == [("dcp.cloud.sync_account", (account, str(rig.caller)), "integrations")]
    assert audit_rows(rig, "CLOUD_ACCOUNT_SYNC_REQUESTED")


async def test_a_second_sync_while_one_runs_is_409(
    rig: Rig, aws: Aws, cleanup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await an_account(rig)
    with rig.engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_lock(:ns, hashtext(:i))"),
            {"ns": 7431, "i": account},
        )
        try:
            response = await call(rig.client, "POST", f"{ACCOUNTS}/{account}/sync")
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "operation_in_progress"
            assert sync.run(rig.engine, uuid.UUID(account), rig.caller) == {
                "outcome": "already_running"
            }
        finally:
            holder.execute(
                text("SELECT pg_advisory_unlock(:ns, hashtext(:i))"), {"ns": 7431, "i": account}
            )


async def test_a_sync_of_a_disabled_account_is_refused(rig: Rig, aws: Aws, cleanup: None) -> None:
    account = await an_account(rig)
    await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", {"status": "DISABLED"})
    assert (await call(rig.client, "POST", f"{ACCOUNTS}/{account}/sync")).status_code == 503


def put_in_account(rig: Rig, account: str, server: str, resource: str = INSTANCE) -> None:
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE servers SET cloud_account_id = :a, region = 'eu-west-1',"
                " management_method = 'AWS_SSM', provider_resource_id = :r, status = 'MANAGED',"
                " os_version = NULL, private_ip = NULL WHERE id = :s"
            ),
            {"a": account, "s": server, "r": resource},
        )


def server_row(rig: Rig, server: str) -> dict[str, Any]:
    with rig.engine.connect() as connection:
        return dict(
            connection.execute(text("SELECT * FROM servers WHERE id = :s"), {"s": server})
            .mappings()
            .one()
        )


def instances_page(*items: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {"InstanceInformationList": list(items)}
    if token:
        page["NextToken"] = token
    return page


async def test_a_sync_reconciles_registered_servers_and_never_creates_or_re_environments_one(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    other = "i-0aaaaaaaaaaaaaaaa"
    put_in_account(rig, account, rig.other_staging, other)
    before = server_row(rig, rig.staging)
    count_before = server_count(rig)
    aws.assume()
    # the registered servers: SSM knows one of the two
    aws.ssm_stub.add_response(
        "describe_instance_information",
        instances_page(
            {
                "InstanceId": INSTANCE,
                "PingStatus": "Online",
                "PlatformName": "Ubuntu",
                "PlatformVersion": "22.04",
                "PlatformType": "Linux",
                "IPAddress": "10.0.0.7",
                "AgentVersion": "3.3.2746.0",
            }
        ),
    )
    # the account-wide count: the known one, plus two instances DCP has no server for
    aws.ssm_stub.add_response(
        "describe_instance_information",
        instances_page(
            {"InstanceId": INSTANCE},
            {"InstanceId": "i-0bbbbbbbbbbbbbbbb"},
            {"InstanceId": "i-0cccccccccccccccc"},
        ),
    )

    result = sync.run(rig.engine, uuid.UUID(account), rig.caller)

    assert result["outcome"] == "synced"
    assert (result["servers_checked"], result["servers_not_in_ssm"], result["servers_updated"]) == (
        2,
        1,
        1,
    )
    assert result["unregistered_managed_instances"] == 2  # the two DCP has no server for
    after = server_row(rig, rig.staging)
    assert after["os_version"] == "Ubuntu 22.04" and str(after["private_ip"]) == "10.0.0.7"
    assert after["provider_metadata"]["ssm"] == {
        "platform_type": "Linux",
        "agent_version": "3.3.2746.0",
    }
    # What a person owns is untouched.
    assert after["environment"] == before["environment"]
    assert after["hostname"] == before["hostname"] and after["status"] == before["status"]
    assert after["management_method"] == "AWS_SSM" and str(after["cloud_account_id"]) == account
    assert server_count(rig) == count_before  # nothing was registered
    stored = next(
        a for a in (await call(rig.client, "GET", ACCOUNTS)).json()["items"] if a["id"] == account
    )
    assert stored["last_sync_at"] is not None and stored["last_sync_error"] is None
    [row] = [
        r for r in audit_rows(rig, "CLOUD_ACCOUNT_SYNCED") if r["target_id"] == uuid.UUID(account)
    ]
    assert row["actor_label"] == "system:cloud-sync" and row["actor_id"] == rig.caller
    assert row["sanitized_metadata"]["servers_not_in_ssm"] == 1
    aws.done()


async def test_a_sync_never_overwrites_what_a_person_entered(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE servers SET os_version = 'Hand written', private_ip = '10.9.9.9'"
                " WHERE id = :s"
            ),
            {"s": rig.staging},
        )
    aws.assume()
    aws.ssm_stub.add_response(
        "describe_instance_information",
        instances_page(
            {
                "InstanceId": INSTANCE,
                "PlatformName": "Ubuntu",
                "PlatformVersion": "22.04",
                "IPAddress": "10.0.0.7",
            }
        ),
    )
    aws.ssm_stub.add_response(
        "describe_instance_information", instances_page({"InstanceId": INSTANCE})
    )
    sync.run(rig.engine, uuid.UUID(account), rig.caller)
    row = server_row(rig, rig.staging)
    assert row["os_version"] == "Hand written" and str(row["private_ip"]) == "10.9.9.9"


async def test_a_failed_sync_records_a_closed_code_and_leaves_servers_alone(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    before = server_row(rig, rig.staging)
    aws.error("assume_role", "AccessDenied", status=403, sts=True)
    result = sync.run(rig.engine, uuid.UUID(account), rig.caller)
    assert (result["outcome"], result["error"]) == ("failed", "assume_role_denied")
    assert server_row(rig, rig.staging)["os_version"] == before["os_version"]
    stored = next(
        a for a in (await call(rig.client, "GET", ACCOUNTS)).json()["items"] if a["id"] == account
    )
    assert stored["last_sync_error"] == "assume_role_denied"
    assert audit_rows(rig, "CLOUD_ACCOUNT_SYNCED")[-1]["outcome"] == "FAILURE"


def server_count(rig: Rig) -> int:
    with rig.engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM servers")).scalar_one())


# --- §14.8 ------------------------------------------------------------------------------------


def check_path(server: str) -> str:
    return f"/api/v1/servers/{server}/connectivity-check"


async def test_a_reachable_server_is_reported_and_nothing_about_it_changes(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    before = server_row(rig, rig.staging)
    aws.assume()
    aws.instances("Online")
    response = await call(rig.client, "POST", check_path(rig.staging))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["reachable"], body["provider_status"], body["data_state"]) == (
        True,
        "online",
        "ok",
    )
    after = server_row(rig, rig.staging)
    for column in ("agent_status", "status", "last_seen_at", "updated_at"):
        assert after[column] == before[column], column
    [row] = [
        r
        for r in audit_rows(rig, "SERVER_CONNECTIVITY_CHECKED")
        if r["target_id"] == uuid.UUID(rig.staging)
    ]
    assert row["outcome"] == "SUCCESS" and row["actor_id"] == rig.caller


async def test_a_server_ssm_does_not_know_is_an_answer_not_an_error(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})
    response = await call(rig.client, "POST", check_path(rig.staging))
    assert response.status_code == 200
    assert response.json()["reachable"] is False
    assert response.json()["provider_status"] == "not_registered_with_ssm"


async def test_a_provider_that_cannot_answer_is_an_error_never_a_false(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    aws.assume()
    aws.error("describe_instance_information", "AccessDeniedException", status=403)
    response = await call(rig.client, "POST", check_path(rig.staging))
    assert response.status_code == 502 and "reachable" not in response.text
    assert audit_rows(rig, "SERVER_CONNECTIVITY_CHECKED")[-1]["outcome"] == "FAILURE"


async def test_no_adapter_or_a_disabled_account_is_503_before_anything_is_asked(
    rig: Rig, aws: Aws, cleanup: None
) -> None:
    # rig.staging is on the seeded ON_PREM account: no adapter.
    response = await call(rig.client, "POST", check_path(rig.staging))
    assert response.status_code == 503 and response.json()["error"]["code"] == "not_configured"
    account = await an_account(rig)
    put_in_account(rig, account, rig.other_staging)
    await call(rig.client, "PATCH", f"{ACCOUNTS}/{account}", {"status": "DISABLED"})
    assert (await call(rig.client, "POST", check_path(rig.other_staging))).status_code == 503
    aws.done()  # nothing was queued and nothing was called


async def test_the_check_needs_server_write_in_scope(rig: Rig, aws: Aws, cleanup: None) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    rig.narrow("VIEWER")
    assert (await call(rig.client, "POST", check_path(rig.staging))).status_code == 403
    rig.narrow("ADMINISTRATOR", PRODUCTION_ONLY)
    # Writable role, wrong scope: the server reads as absent.
    assert (await call(rig.client, "POST", check_path(rig.staging))).status_code == 404
    rig.narrow("ADMINISTRATOR")
    assert (await call(rig.client, "POST", check_path(str(uuid.uuid4())))).status_code == 404


async def test_the_check_is_rate_limited_per_server(rig: Rig, aws: Aws, cleanup: None) -> None:
    account = await an_account(rig)
    put_in_account(rig, account, rig.staging)
    aws.assume()
    for _ in range(10):
        aws.instances("Online")
    for _ in range(10):
        assert (await call(rig.client, "POST", check_path(rig.staging))).status_code == 200
    limited = await call(rig.client, "POST", check_path(rig.staging))
    assert limited.status_code == 429 and "Retry-After" in limited.headers


def test_the_sync_task_takes_two_ids_and_nothing_else(migrated: Engine) -> None:
    import inspect

    from app.workers.tasks import cloud

    assert list(inspect.signature(cloud.sync_account).parameters) == ["account_id", "requested_by"]
    assert cloud.sync_account("nope", "nope") == {"outcome": "bad_id"}
