"""An operation carried out through the REAL ``AwsSsmAdapter`` against a stubbed AWS layer (M4.3).

M4.2 proved the executor against a scripted fake. Here the adapter is the production one, its
botocore clients are stubbed at the botocore boundary, and PostgreSQL is real: the request the
executor makes, the handle it persists, the polls and the verification read all go through the
code that will run against AWS. **That is still not evidence that AWS accepts the document or the
calls** -- only the manual check in infra/aws/README.md is.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from app.providers import registry
from tests.integration.test_operations_worker import (  # noqa: F401 -- fixture used by name
    World,
    no_sleep,
    world,
)
from tests.providers.aws.harness import COMMAND_ID, ROLE_ARN, Aws

ACCOUNT_NUMBER = ROLE_ARN.split(":")[4]


def _account(world: World, reference: str, arn: str) -> uuid.UUID:  # noqa: F811
    account = uuid.uuid4()
    with world.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cloud_accounts (id, provider, account_reference, display_name,"
                " region_configuration, credential_reference, status, created_at, updated_at)"
                " VALUES (:i, 'AWS', :r, 'stubbed AWS account',"
                " CAST(:rc AS jsonb), :arn, 'ACTIVE', now(), now())"
            ),
            {
                "i": account,
                "r": reference,
                "rc": '{"allowed_regions": ["eu-west-1"], "default_region": "eu-west-1"}',
                "arn": arn,
            },
        )
    return account


def _retire(world: World, account: uuid.UUID) -> None:  # noqa: F811
    with world.engine.begin() as connection:
        connection.execute(
            text("UPDATE cloud_accounts SET deleted_at = now() WHERE id = :i"), {"i": account}
        )


@pytest.fixture
def aws(world: World) -> Iterator[Aws]:  # noqa: F811
    stubbed = Aws()
    account = _account(world, ACCOUNT_NUMBER, ROLE_ARN)
    world.account = account
    world.monkeypatch.setitem(registry._FACTORIES, "AWS", lambda: stubbed.adapter)
    yield stubbed
    _retire(world, account)


def an_aws_target(world: World) -> tuple[World, uuid.UUID]:  # noqa: F811
    target = world.add(unit="web.service")
    with world.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE servers SET region = 'eu-west-1', management_method = 'AWS_SSM',"
                " provider_resource_id = 'i-0abc123def4567890' WHERE id = :s"
            ),
            {"s": target.server},
        )
    return world, world.operation(target, provider="aws_ssm")


def read_status(aws: Aws, state: str) -> None:
    """One ``Action=status`` read: SendCommand, then the invocation that answers it."""
    aws.sent()
    aws.invocation("Success", output=f"{state}\n")


def test_a_restart_runs_through_the_aws_adapter_and_is_verified(world: World, aws: Aws) -> None:  # noqa: F811
    world, operation = an_aws_target(world)
    aws.assume()  # the shared read session: identity check and the pre-flight read
    aws.instances("Online")
    read_status(aws, "inactive")
    aws.assume(f"dcp-{operation}")  # the operation's own session, for the one write
    aws.sent()
    aws.invocation("InProgress")
    aws.invocation("Success")
    read_status(aws, "active")  # verification

    assert world.run(operation, sleep=no_sleep) == "SUCCEEDED"

    row = world.row(operation)
    assert row["execution_status"] == "SUCCEEDED"
    assert (row["observed_state_before"], row["observed_state_after"]) == ("INACTIVE", "ACTIVE")
    assert row["external_execution_id"] == COMMAND_ID  # persisted before polling began
    assert row["provider"] == "aws_ssm"
    assert row["sanitized_result"]["steps"][-2:] == ["completed", "verified"]
    aws.done()


def test_a_unit_that_is_not_active_afterwards_is_a_failed_verification(
    world: World,  # noqa: F811
    aws: Aws,
) -> None:
    world, operation = an_aws_target(world)
    aws.assume()
    aws.instances("Online")
    read_status(aws, "inactive")
    aws.assume(f"dcp-{operation}")
    aws.sent()
    aws.invocation("Success")
    for _ in range(3):  # the verification reads are repeated while a state may still settle
        read_status(aws, "failed")

    assert world.run(operation, sleep=no_sleep) == "FAILED:VERIFICATION_MISMATCH"
    row = world.row(operation)
    assert row["execution_status"] == "FAILED" and row["observed_state_after"] == "FAILED"


def test_throttling_beyond_the_retry_budget_fails_without_reconciliation(
    world: World,  # noqa: F811
    aws: Aws,
) -> None:
    world, operation = an_aws_target(world)
    aws.assume()
    aws.instances("Online")
    read_status(aws, "inactive")
    aws.assume(f"dcp-{operation}")
    for _ in range(3):
        aws.error("send_command", "ThrottlingException", headers={"retry-after": "1"})

    assert world.run(operation, sleep=no_sleep) == "FAILED:PROVIDER_THROTTLED"
    row = world.row(operation)
    assert row["external_execution_id"] is None
    assert row["sanitized_result"]["reconciliation_required"] is False  # refused before acting
    aws.done()


def test_a_role_that_may_not_send_commands_is_a_configuration_failure(
    world: World,  # noqa: F811
    aws: Aws,
) -> None:
    world, operation = an_aws_target(world)
    aws.assume()
    aws.instances("Online")
    read_status(aws, "inactive")
    aws.assume(f"dcp-{operation}")
    aws.error("send_command", "AccessDeniedException", status=403)

    assert world.run(operation, sleep=no_sleep) == "FAILED:PROVIDER_AUTHORIZATION"
    row = world.row(operation)
    assert row["sanitized_result"]["reason_code"] == "authorization"
    aws.done()


def test_an_instance_ssm_no_longer_knows_is_refused_before_anything_is_sent(
    world: World,  # noqa: F811
    aws: Aws,
) -> None:
    world, operation = an_aws_target(world)
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})

    assert world.run(operation, sleep=no_sleep) == "FAILED:TARGET_VALIDATION_FAILED"
    aws.done()  # only the identity read was queued, and only it was made


def test_a_static_key_in_the_environment_stops_the_operation_before_the_provider(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.aws import build_adapter

    monkeypatch.setitem(registry._FACTORIES, "AWS", build_adapter)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATESTINGNOTAKEY00")
    account = _account(world, "210987654321", "arn:aws:iam::210987654321:role/DcpExecution")
    world.account = account
    try:
        world, operation = an_aws_target(world)
        assert world.run(operation, sleep=no_sleep) == "FAILED:NOT_CONFIGURED"
        assert world.row(operation)["external_execution_id"] is None
    finally:
        _retire(world, account)
