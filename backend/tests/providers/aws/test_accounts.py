"""Account-level AWS reads: registration rules, instance facts, paging, verification."""

from __future__ import annotations

from typing import Any

import pytest

from app.providers.aws import accounts
from app.providers.aws.api import target_from
from tests.providers.aws.harness import ACCOUNT, INSTANCE, REGION, ROLE_ARN, Aws

TARGET = target_from(role_arn=ROLE_ARN, region=REGION, account_reference=ACCOUNT)


def page(*ids: str, token: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"InstanceInformationList": [{"InstanceId": i, **extra} for i in ids]}
    if token:
        body["NextToken"] = token
    return body


# --- registration -------------------------------------------------------------------------------


def problem(
    account: str = ACCOUNT, reference: str = ROLE_ARN, regions: tuple[str, ...] = (REGION,)
):
    return accounts.registration_problem(account, reference, regions)


def test_a_role_arn_for_the_same_account_is_accepted() -> None:
    assert problem() is None


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"account": "1234"}, "invalid_account_reference"),
        ({"account": "12345678901a"}, "invalid_account_reference"),
        ({"reference": "AKIAIOSFODNN7EXAMPLE"}, accounts.SECRET_MISPLACED),
        ({"reference": "ASIAIOSFODNN7EXAMPLE"}, accounts.SECRET_MISPLACED),
        ({"reference": "https://x.example/token"}, accounts.SECRET_MISPLACED),
        ({"reference": "env://SOME_SECRET"}, accounts.SECRET_MISPLACED),
        ({"reference": "A" * 45}, accounts.SECRET_MISPLACED),
        ({"reference": "a role"}, "invalid_credential_reference"),
        ({"reference": "arn:aws:iam::123456789012:user/alice"}, "invalid_credential_reference"),
        ({"reference": "arn:aws-cn:iam::123456789012:role/R"}, "invalid_credential_reference"),
        ({"reference": "arn:aws:iam::999999999999:role/R"}, "credential_account_mismatch"),
        ({"regions": ("Mars",)}, "invalid_region"),
    ],
)
def test_each_wrong_registration_names_one_rule_and_never_repeats_the_value(
    kwargs: dict, code: str
) -> None:
    found = problem(**kwargs)
    assert found is not None
    location, reported, message = found
    assert reported == code
    for value in kwargs.values():
        if isinstance(value, str):
            assert value not in message
    assert location[0] == "body"


# --- instance facts -----------------------------------------------------------------------------


def test_facts_are_validated_and_a_bad_value_is_dropped_not_stored() -> None:
    aws = Aws()
    aws.assume()
    aws.ssm_stub.add_response(
        "describe_instance_information",
        {
            "InstanceInformationList": [
                {
                    "InstanceId": INSTANCE,
                    "PingStatus": "Online",
                    "PlatformName": "Ubuntu",
                    "PlatformVersion": "22.04",
                    "IPAddress": "not-an-ip",
                    "AgentVersion": "3.3; rm -rf /",
                    "PlatformType": "Linux",
                },
                {"InstanceId": "i-with-bad-shape", "PingStatus": "Online"},
            ]
        },
    )
    found = accounts.describe_instances(aws.adapter.api, TARGET, [INSTANCE, "i-with-bad-shape"])
    assert set(found) == {INSTANCE}  # the malformed id is not described at all
    facts = found[INSTANCE]
    assert facts.os_version == "Ubuntu 22.04" and facts.platform_type == "Linux"
    assert facts.private_ip is None and facts.agent_version is None


def test_only_the_requested_ids_are_described() -> None:
    aws = Aws()
    aws.assume()
    aws.ssm_stub.add_response(
        "describe_instance_information", page(INSTANCE, "i-0bbbbbbbbbbbbbbbb")
    )
    found = accounts.describe_instances(aws.adapter.api, TARGET, [INSTANCE])
    assert set(found) == {INSTANCE}


def test_describing_batches_ids_in_fifties_and_follows_next_tokens() -> None:
    ids = [f"i-{n:017x}" for n in range(120)]
    aws = Aws()
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", page(*ids[:50], token="t1"))
    aws.ssm_stub.add_response("describe_instance_information", page())
    aws.ssm_stub.add_response("describe_instance_information", page(*ids[50:100]))
    aws.ssm_stub.add_response("describe_instance_information", page(*ids[100:]))
    assert len(accounts.describe_instances(aws.adapter.api, TARGET, ids)) == 120
    aws.done()


def test_the_unregistered_count_is_bounded_and_says_when_it_was_cut() -> None:
    aws = Aws()
    aws.assume()
    for n in range(accounts.MAX_PAGES):
        aws.ssm_stub.add_response(
            "describe_instance_information", page(f"i-{n:017x}", token=f"t{n}")
        )
    count, cut = accounts.count_unregistered(aws.adapter.api, TARGET, {"i-" + "0" * 17})
    assert (count, cut) == (accounts.MAX_PAGES - 1, True)  # i-…0 is registered
    done = Aws()
    done.assume()
    done.ssm_stub.add_response(
        "describe_instance_information", page(INSTANCE, "i-0bbbbbbbbbbbbbbbb")
    )
    assert accounts.count_unregistered(done.adapter.api, TARGET, {INSTANCE}) == (1, False)


# --- verification -------------------------------------------------------------------------------


def test_verify_does_nothing_but_read_and_lists_what_it_did_not_probe() -> None:
    aws = Aws()
    aws.assume()
    aws.ssm_stub.add_response("describe_instance_information", page())
    aws.ssm_stub.add_response("describe_document", {"Document": {"Name": "DCP-ServiceControl"}})
    report = accounts.verify(aws.adapter.api, TARGET, document_version="3")
    assert (report.reachable, report.permissions_ok, report.message) == (True, True, "ok")
    assert accounts.NOT_PROBED == ("ssm:SendCommand",)
    aws.done()


def test_verify_stops_and_says_so_when_aws_does_not_answer() -> None:
    aws = Aws()
    aws.assume()
    aws.error("describe_instance_information", "ServiceUnavailable", status=503)
    report = accounts.verify(aws.adapter.api, TARGET, document_version="3")
    assert report.reachable is False and report.permissions_ok is False
    assert report.message == "provider_error"
