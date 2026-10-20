"""The AWS SSM adapter against a stubbed AWS layer (``OPEN-79`` – ``OPEN-83``, ``OPEN-90``)."""

from __future__ import annotations

import uuid

import pytest

from app.providers.base import ProviderAdapter
from app.providers.capabilities import Concurrency, Latency, Support
from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    NotConfigured,
    NotFound,
    ProviderUnavailable,
    Throttled,
    Timeout,
)
from app.providers.types import ExecutionHandle, ServerRef, ServiceAction
from tests.providers.aws.harness import (
    COMMAND_ID,
    INSTANCE,
    REF,
    ROLE_ARN,
    SERVICE,
    T0,
    Aws,
)

OP = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


def send_params(action: str, comment: str) -> dict:
    return {
        "InstanceIds": [INSTANCE],
        "DocumentName": "DCP-ServiceControl",
        "DocumentVersion": "3",
        "Parameters": {"Action": [action], "UnitName": ["nginx.service"]},
        "Comment": comment,
        "TimeoutSeconds": 30,
        "MaxConcurrency": "1",
        "MaxErrors": "0",
    }


# --- what it is -----------------------------------------------------------------------------


def test_it_is_an_adapter_and_pins_the_aws_column_of_the_capability_matrix() -> None:
    adapter = Aws().adapter
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.adapter_id == "aws_ssm"
    assert adapter.demo is False
    caps = adapter.capabilities()
    assert caps.inventory_sync is Support.SUPPORTED
    assert caps.connectivity_check is Support.SUPPORTED
    assert dict(caps.actions) == {
        ServiceAction.START: Support.SUPPORTED,
        ServiceAction.STOP: Support.SUPPORTED,
        ServiceAction.RESTART: Support.SUPPORTED,
        ServiceAction.STATUS: Support.SUPPORTED,
        # "We have not set it up", not "AWS cannot": the document has no such action.
        ServiceAction.LOGS_TAIL: Support.NOT_CONFIGURED,
    }
    assert caps.async_handle is True
    assert caps.concurrency is Concurrency.ACCOUNT_THROTTLED
    assert caps.latency is Latency.SECONDS


def test_logs_tail_is_refused_as_not_configured_and_sends_nothing() -> None:
    aws = Aws()
    with pytest.raises(NotConfigured):
        aws.adapter.execute_operation(REF, SERVICE, ServiceAction.LOGS_TAIL, operation_id=OP)
    assert aws.factory_calls == []


# --- reads: connectivity and identity ---------------------------------------------------------


@pytest.mark.parametrize(
    ("ping", "reachable", "status"),
    [
        ("Online", True, "online"),
        ("ConnectionLost", False, "connection_lost"),
        ("Inactive", False, "inactive"),
    ],
)
def test_connectivity_reports_what_ssm_says(ping: str, reachable: bool, status: str) -> None:
    aws = Aws()
    aws.assume()
    aws.instances(ping)
    result = aws.adapter.check_connectivity(REF)
    assert (result.value.reachable, result.value.provider_status) == (reachable, status)  # type: ignore[union-attr]
    assert (result.data_state, result.demo_mode, result.adapter_id) == ("ok", False, "aws_ssm")
    aws.done()


def test_an_instance_ssm_does_not_know_is_unreachable_not_an_error() -> None:
    aws = Aws()
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})
    result = aws.adapter.check_connectivity(REF)
    assert result.value.reachable is False  # type: ignore[union-attr]
    assert result.value.provider_status == "not_registered_with_ssm"  # type: ignore[union-attr]


def test_server_info_confirms_the_instance_or_says_it_is_not_managed() -> None:
    aws = Aws()
    aws.assume()
    aws.instances("Online")
    info = aws.adapter.get_server_info(REF).value
    assert (info.provider_resource_id, info.region, info.provider_status) == (  # type: ignore[union-attr]
        INSTANCE,
        "eu-west-1",
        "online",
    )
    gone = Aws()
    gone.assume()
    gone.ssm_stub.add_response("describe_instance_information", {"InstanceInformationList": []})
    with pytest.raises(NotFound) as caught:
        gone.adapter.get_server_info(REF)
    assert caught.value.detail_code == "instance_not_managed"


# --- reads: a unit's state, which is a Run Command with Action=status -------------------------


def test_a_unit_state_is_read_by_running_the_document_with_status() -> None:
    aws = Aws()
    aws.assume()
    aws.sent(expected=send_params("status", "dcp-read"))
    aws.invocation("Success", output="active\n")
    result = aws.adapter.list_managed_services(REF, SERVICE)
    assert [(s.unit_name, s.state) for s in result.value] == [("nginx.service", "ACTIVE")]  # type: ignore[union-attr]
    assert result.data_state == "ok"
    aws.done()


def test_a_pending_command_is_polled_with_a_backoff_until_it_finishes() -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    for status in ("Pending", "InProgress", "Delayed"):
        aws.invocation(status)
    aws.invocation("Success", output="failed")
    state = aws.adapter.list_managed_services(REF, SERVICE).value[0].state  # type: ignore[index]
    assert state == "FAILED"
    assert aws.time.sleeps == [1.0, 1.0, 2.0]


def test_the_command_not_existing_yet_is_treated_as_pending() -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    aws.error("get_command_invocation", "InvocationDoesNotExist")
    aws.invocation("Success", output="inactive")
    assert aws.adapter.list_managed_services(REF, SERVICE).value[0].state == "INACTIVE"  # type: ignore[index]


@pytest.mark.parametrize(
    ("token", "state"),
    [
        ("active", "ACTIVE"),
        ("inactive", "INACTIVE"),
        ("failed", "FAILED"),
        ("activating", "ACTIVATING"),
        ("deactivating", "DEACTIVATING"),
        ("reloading", "UNKNOWN"),
        ("maintenance", "UNKNOWN"),
    ],
)
def test_every_active_state_maps_to_the_closed_set(token: str, state: str) -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    aws.invocation("Success", output=f"{token}\n")
    assert aws.adapter.list_managed_services(REF, SERVICE).value[0].state == state  # type: ignore[index]


@pytest.mark.parametrize(
    "output", ["", "active; id", "ACTIVE", "x" * 80, "a b", "active\ninactive"]
)
def test_output_that_is_not_one_state_token_is_never_trusted(output: str) -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    aws.invocation("Success", output=output)
    with pytest.raises(ProviderUnavailable) as caught:
        aws.adapter.list_managed_services(REF, SERVICE)
    assert caught.value.detail_code == "unparseable_output"


def test_a_unit_the_host_does_not_have_is_an_empty_answer_not_inactive() -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    aws.invocation("Failed", code=4)
    result = aws.adapter.list_managed_services(REF, SERVICE)
    assert result.data_state == "ok"
    assert result.value == []


@pytest.mark.parametrize(
    ("status", "details", "expected", "code"),
    [
        ("Failed", "Failed", ProviderUnavailable, "status_command_failed"),
        ("Cancelled", "Cancelled", ProviderUnavailable, "status_command_failed"),
        ("TimedOut", "ExecutionTimedOut", Timeout, "status_command_timed_out"),
        ("Failed", "DeliveryTimedOut", Timeout, "status_command_timed_out"),
        ("Failed", "Undeliverable", NotFound, "instance_not_managed"),
        ("Failed", "InvalidPlatform", NotFound, "instance_not_managed"),
        ("Failed", "AccessDenied", AuthorizationError, "send_command_denied"),
    ],
)
def test_a_failed_status_command_maps_to_a_class_and_an_actionable_code(
    status: str, details: str, expected: type, code: str
) -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    aws.invocation(status, details=details, code=1)
    with pytest.raises(expected) as caught:
        aws.adapter.list_managed_services(REF, SERVICE)
    assert caught.value.detail_code == code


def test_a_command_that_never_finishes_times_out_instead_of_polling_forever() -> None:
    aws = Aws()
    aws.assume()
    aws.sent()
    for _ in range(40):
        aws.invocation("InProgress")
    with pytest.raises(Timeout):
        aws.adapter.list_managed_services(REF, SERVICE)
    assert sum(aws.time.sleeps) >= 30


# --- credentials ------------------------------------------------------------------------------


def test_the_read_session_is_cached_and_refreshed_before_it_expires() -> None:
    aws = Aws()
    aws.assume(minutes=15)
    aws.instances("Online")
    aws.instances("Online")
    aws.adapter.check_connectivity(REF)
    aws.adapter.check_connectivity(REF)  # no second AssumeRole
    aws.time.now = T0.replace(minute=14)  # 14 of 15 minutes used: inside the refresh margin
    aws.assume(minutes=15)
    aws.instances("Online")
    aws.adapter.check_connectivity(REF)
    aws.done()


def test_an_authentication_failure_re_assumes_once_and_repeats_the_read() -> None:
    aws = Aws()
    aws.assume()
    aws.error("describe_instance_information", "ExpiredTokenException", status=403)
    aws.assume()
    aws.instances("Online")
    assert aws.adapter.check_connectivity(REF).value.reachable is True  # type: ignore[union-attr]
    aws.done()


def test_a_second_authentication_failure_is_terminal() -> None:
    aws = Aws()
    aws.assume()
    aws.error("describe_instance_information", "ExpiredTokenException", status=403)
    aws.assume()
    aws.error("describe_instance_information", "ExpiredTokenException", status=403)
    with pytest.raises(AuthenticationError):
        aws.adapter.check_connectivity(REF)
    aws.done()


def test_a_refused_assume_role_is_an_authorisation_problem_with_an_actionable_code() -> None:
    aws = Aws()
    aws.error("assume_role", "AccessDenied", status=403, sts=True)
    with pytest.raises(AuthorizationError) as caught:
        aws.adapter.check_connectivity(REF)
    assert caught.value.detail_code == "assume_role_denied"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"credential_reference": None}, "credential_reference_missing"),
        ({"credential_reference": "secretstore://x/y"}, "credential_reference_invalid"),
        (
            {"credential_reference": "arn:aws:iam::999999999999:role/Other"},
            "credential_account_mismatch",
        ),
        ({"region": None}, "region_missing"),
        ({"region": "mars-1"}, "region_invalid"),
    ],
)
def test_a_gap_in_the_stored_facts_is_not_configured_and_no_call_is_made(
    changes: dict, code: str
) -> None:
    ref = ServerRef(
        INSTANCE,
        "123456789012",
        "AWS_SSM",
        **({"region": "eu-west-1", "credential_reference": ROLE_ARN} | changes),
    )
    aws = Aws()
    with pytest.raises(NotConfigured) as caught:
        aws.adapter.check_connectivity(ref)
    assert caught.value.detail_code == code
    assert aws.factory_calls == []


# --- the write --------------------------------------------------------------------------------


def test_a_restart_sends_the_registered_document_once_and_returns_the_command_id() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.sent(expected=send_params("restart", f"dcp-op-{OP}"))
    handle = aws.adapter.execute_operation(REF, SERVICE, ServiceAction.RESTART, operation_id=OP)
    assert (handle.adapter_id, handle.external_execution_id) == ("aws_ssm", COMMAND_ID)
    assert handle.submitted_at == T0
    aws.done()
    # The write used the write client, and its own session, not the shared read one.
    assert ("ssm", "eu-west-1", True, True) in aws.factory_calls


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_each_write_action_carries_exactly_its_action_and_the_registered_unit(action: str) -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.sent(expected=send_params(action, f"dcp-op-{OP}"))
    aws.adapter.execute_operation(REF, SERVICE, ServiceAction(action), operation_id=OP)
    aws.done()


def test_throttling_is_retried_honouring_retry_after_then_succeeds() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.error("send_command", "ThrottlingException", headers={"retry-after": "4"})
    aws.error("send_command", "ThrottlingException")
    aws.sent()
    handle = aws.adapter.execute_operation(REF, SERVICE, ServiceAction.RESTART, operation_id=OP)
    assert handle.external_execution_id == COMMAND_ID
    # First wait is AWS's own retry-after; the second is full jitter (0.5 * 2**1).
    assert aws.time.sleeps == [4.0, 1.0]
    aws.done()


def test_throttling_gives_up_after_three_attempts_and_carries_retry_after() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    for _ in range(3):
        aws.error("send_command", "TooManyRequestsException", headers={"retry-after": "2"})
    with pytest.raises(Throttled) as caught:
        aws.adapter.execute_operation(REF, SERVICE, ServiceAction.STOP, operation_id=OP)
    assert caught.value.retry_after == 2.0
    assert len(aws.time.sleeps) == 2  # a wait between attempts, not after the last
    aws.done()


def test_throttling_does_not_hammer_past_the_invoke_bound() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.error("send_command", "ThrottlingException", headers={"retry-after": "45"})
    with pytest.raises(Throttled):
        aws.adapter.execute_operation(REF, SERVICE, ServiceAction.STOP, operation_id=OP)
    assert aws.time.sleeps == []  # waiting 45 s would exceed the 30 s bound: refused, not slept
    aws.done()


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        ("InternalServerError", 500, ProviderUnavailable),
        ("ServiceUnavailable", 503, ProviderUnavailable),
        ("RequestTimeout", 408, Timeout),
        ("AccessDeniedException", 403, AuthorizationError),
        ("InvalidInstanceId", 400, NotFound),
        ("InvalidDocument", 400, NotFound),
        ("SomethingNew", 400, ProviderUnavailable),
    ],
)
def test_no_other_write_failure_is_ever_retried(code: str, status: int, expected: type) -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.error("send_command", code, status=status)
    with pytest.raises(expected):
        aws.adapter.execute_operation(REF, SERVICE, ServiceAction.RESTART, operation_id=OP)
    aws.done()  # exactly one call was queued, and exactly one was made
    assert aws.time.sleeps == []


def test_an_authentication_failure_on_the_write_re_assumes_once() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.error("send_command", "InvalidClientTokenId", status=403)
    aws.assume(f"dcp-{OP}")
    aws.sent()
    aws.adapter.execute_operation(REF, SERVICE, ServiceAction.RESTART, operation_id=OP)
    aws.done()


def test_a_send_command_denied_by_the_role_names_the_missing_permission() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.error("send_command", "AccessDeniedException", status=403)
    with pytest.raises(AuthorizationError) as caught:
        aws.adapter.execute_operation(REF, SERVICE, ServiceAction.RESTART, operation_id=OP)
    assert caught.value.detail_code == "send_command_denied"


def test_a_response_without_a_command_id_is_not_a_handle() -> None:
    aws = Aws()
    aws.assume(f"dcp-{OP}")
    aws.ssm_stub.add_response("send_command", {"Command": {"CommandId": "x" * 36}})
    with pytest.raises(ProviderUnavailable) as caught:
        aws.adapter.execute_operation(REF, SERVICE, ServiceAction.RESTART, operation_id=OP)
    assert caught.value.detail_code == "unexpected_response"


# --- polling a write --------------------------------------------------------------------------


def handle(aws: Aws) -> ExecutionHandle:
    return ExecutionHandle("aws_ssm", COMMAND_ID, aws.time.now)


@pytest.mark.parametrize(
    ("status", "details", "state", "reason"),
    [
        ("Pending", None, "IN_PROGRESS", None),
        ("InProgress", None, "IN_PROGRESS", None),
        ("Delayed", None, "IN_PROGRESS", None),
        ("Cancelling", None, "IN_PROGRESS", None),
        ("Success", None, "COMPLETED", None),
        ("Cancelled", None, "CANCELLED", "cancelled"),
        ("Failed", None, "FAILED", "command_failed"),
        ("Failed", "DeliveryTimedOut", "FAILED", "delivery_timed_out"),
        ("Failed", "ExecutionTimedOut", "FAILED", "execution_timed_out"),
        ("Failed", "Undeliverable", "FAILED", "undeliverable"),
        ("Failed", "AccessDenied", "FAILED", "access_denied"),
        ("TimedOut", "TimedOut", "FAILED", "command_timed_out"),
    ],
)
def test_an_invocation_status_maps_to_the_providers_closed_states(
    status: str, details: str | None, state: str, reason: str | None
) -> None:
    aws = Aws()
    aws.assume()
    aws.invocation(status, details=details)
    result = aws.adapter.get_operation_status(REF, handle(aws)).value
    assert (result.state, result.reason_code) == (state, reason)  # type: ignore[union-attr]


def test_an_unrecognised_status_is_an_error_not_a_guess() -> None:
    aws = Aws()
    aws.assume()
    aws.invocation("Teleporting")
    with pytest.raises(ProviderUnavailable) as caught:
        aws.adapter.get_operation_status(REF, handle(aws))
    assert caught.value.detail_code == "unmapped_status"


def test_a_command_not_visible_yet_is_in_progress_but_not_forever() -> None:
    aws = Aws()
    aws.assume()
    aws.error("get_command_invocation", "InvocationDoesNotExist")
    young = aws.adapter.get_operation_status(REF, handle(aws)).value
    assert young.state == "IN_PROGRESS"  # type: ignore[union-attr]
    aws.error("get_command_invocation", "InvocationDoesNotExist")
    old = ExecutionHandle("aws_ssm", COMMAND_ID, T0.replace(year=2026, month=10, day=20, hour=11))
    with pytest.raises(NotFound):
        aws.adapter.get_operation_status(REF, old)


# --- nothing leaks ----------------------------------------------------------------------------


def test_provider_text_never_reaches_an_exception() -> None:
    aws = Aws()
    aws.assume()
    aws.error("describe_instance_information", "AccessDenied", status=403)
    with pytest.raises(AuthorizationError) as caught:
        aws.adapter.check_connectivity(REF)
    shown = f"{caught.value!s} {caught.value.args} {caught.value.detail_code}"
    for secret in ("i-0secret0instance", "999999999999", "arn:aws"):
        assert secret not in shown


def test_only_the_registered_unit_and_instance_are_sent() -> None:
    # The arguments are a closed shape: nothing a caller supplies can add a parameter.
    aws = Aws()
    arguments = aws.adapter._command(REF, ServiceAction.RESTART, SERVICE, comment="dcp-op-x")
    assert set(arguments) == {
        "InstanceIds",
        "DocumentName",
        "DocumentVersion",
        "Parameters",
        "Comment",
        "TimeoutSeconds",
        "MaxConcurrency",
        "MaxErrors",
    }
    assert set(arguments["Parameters"]) == {"Action", "UnitName"}
    assert "Targets" not in arguments
