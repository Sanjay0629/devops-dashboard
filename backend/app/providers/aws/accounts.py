"""Account-level AWS reads: verify an account (§26.5) and describe its instances (§26.6).

These are not adapter methods: they act on an *account*, not on a server and a service, and the
adapter interface is deliberately limited to the closed types of a service operation. They go
through the same :class:`AwsApi`, so the same credentials, retries and error mapping apply.

**Everything here is a read.** ``ssm:SendCommand`` is *not probed* (``OPEN-87``): the only way to
test it is to send a command, and a probe that sends a command is an operation nobody asked for.
The execution role's deny of the shell documents is checked offline
(``scripts/verify_aws_role.py``), never by trying one.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.providers.aws.adapter import DOCUMENT_NAME
from app.providers.aws.api import READ_SESSION_NAME, REGION, ROLE_ARN, AwsApi, Target
from app.providers.errors import AuthorizationError, NotFound, ProviderError

#: What §26.5 cannot prove without acting. Reported, never guessed.
NOT_PROBED: Final = ("ssm:SendCommand",)
BATCH: Final = 50
#: A bound on the "how many managed instances are not registered" count.
MAX_PAGES: Final = 10

_INSTANCE_ID: Final = re.compile(r"(i|mi)-[0-9a-f]{8,32}")
_TEXT: Final = re.compile(r"[A-Za-z0-9 ._()+-]{1,64}")


@dataclass(frozen=True)
class VerifyReport:
    reachable: bool
    permissions_ok: bool
    missing_permissions: tuple[str, ...]
    #: A closed code: ``ok`` or the first thing that is wrong.
    message: str


def verify(api: AwsApi, target: Target, *, document_version: str) -> VerifyReport:
    """Read-only permission probe: assume the role, list managed instances, find the document."""
    try:
        api.credentials(target, READ_SESSION_NAME, refresh=True)
    except ProviderError as error:
        denied = ("sts:AssumeRole",) if isinstance(error, AuthorizationError) else ()
        return VerifyReport(False, False, denied, error.detail_code or error.provider_error_class)
    probes: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
        ("ssm:DescribeInstanceInformation", "describe_instance_information",
         "describe_instance_information", {"MaxResults": 5}),
        ("ssm:DescribeDocument", "describe_document", "describe_document",
         {"Name": DOCUMENT_NAME, "DocumentVersion": document_version}),
    )  # fmt: skip
    missing: list[str] = []
    message = "ok"
    for permission, method, call, arguments in probes:
        try:
            api.call(target, "ssm", method, call=call, **arguments)
        except AuthorizationError:
            missing.append(permission)
            message = message if message != "ok" else "access_denied"
        except NotFound as error:
            message = message if message != "ok" else (error.detail_code or "not_found")
        except ProviderError as error:
            # AWS did not answer: nothing about permissions can be said.
            return VerifyReport(
                False, False, tuple(missing), error.detail_code or error.provider_error_class
            )
    return VerifyReport(True, not missing and message == "ok", tuple(missing), message)


@dataclass(frozen=True)
class InstanceFacts:
    instance_id: str
    ping_status: str | None
    os_version: str | None
    private_ip: str | None
    platform_type: str | None
    agent_version: str | None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and _TEXT.fullmatch(value) else None


def _facts(raw: dict[str, Any]) -> InstanceFacts | None:
    instance_id = raw.get("InstanceId")
    if not isinstance(instance_id, str) or _INSTANCE_ID.fullmatch(instance_id) is None:
        return None  # an id we would not accept at registration is not described
    name, version = _text(raw.get("PlatformName")), _text(raw.get("PlatformVersion"))
    address: str | None = None
    try:
        address = str(ipaddress.IPv4Address(str(raw.get("IPAddress"))))
    except ValueError:
        address = None
    return InstanceFacts(
        instance_id,
        _text(raw.get("PingStatus")),
        _text(f"{name} {version}") if name and version else name,
        address,
        _text(raw.get("PlatformType")),
        _text(raw.get("AgentVersion")),
    )


def describe_instances(
    api: AwsApi, target: Target, instance_ids: Sequence[str]
) -> dict[str, InstanceFacts]:
    """What SSM says about exactly these instances. An id SSM does not know is simply absent."""
    found: dict[str, InstanceFacts] = {}
    for start in range(0, len(instance_ids), BATCH):
        chunk = list(instance_ids[start : start + BATCH])
        token: str | None = None
        for _ in range(MAX_PAGES):
            arguments: dict[str, Any] = {
                "Filters": [{"Key": "InstanceIds", "Values": chunk}],
                "MaxResults": BATCH,
            }
            if token:
                arguments["NextToken"] = token
            response = api.call(
                target,
                "ssm",
                "describe_instance_information",
                call="describe_instance_information",
                **arguments,
            )
            for raw in response.get("InstanceInformationList", []):
                facts = _facts(raw)
                if facts is not None and facts.instance_id in chunk:
                    found[facts.instance_id] = facts
            token = response.get("NextToken")
            if not token:
                break
    return found


def count_unregistered(api: AwsApi, target: Target, registered: set[str]) -> tuple[int, bool]:
    """Managed instances in the region that DCP has no server for, and whether the count was cut."""
    count = 0
    token: str | None = None
    for _ in range(MAX_PAGES):
        arguments: dict[str, Any] = {"MaxResults": BATCH}
        if token:
            arguments["NextToken"] = token
        response = api.call(
            target,
            "ssm",
            "describe_instance_information",
            call="describe_instance_information",
            **arguments,
        )
        for raw in response.get("InstanceInformationList", []):
            instance_id = raw.get("InstanceId")
            if isinstance(instance_id, str) and instance_id not in registered:
                count += 1
        token = response.get("NextToken")
        if not token:
            return count, False
    return count, True


# --- registration (§26.3) ------------------------------------------------------------------------

SECRET_MISPLACED: Final = "credentials_must_be_stored_in_secret_manager"  # noqa: S105
_ACCOUNT_ID: Final = re.compile(r"[0-9]{12}")
_SECRET_SHAPED: Final = re.compile(r"(AKIA|ASIA)[A-Z0-9]{16}|[A-Za-z0-9/+=]{40,}")


def registration_problem(
    account_reference: str, credential_reference: str, regions: Sequence[str]
) -> tuple[list[str], str, str] | None:
    """``(location, closed code, message)`` for the first thing wrong, else ``None``.

    The value is never repeated: a rejected credential reference may *be* a credential.
    """
    if _ACCOUNT_ID.fullmatch(account_reference) is None:
        return (
            ["body", "account_reference"],
            "invalid_account_reference",
            "An AWS account reference is the 12-digit account id.",
        )
    match = ROLE_ARN.fullmatch(credential_reference)
    if match is None:
        if "://" in credential_reference or _SECRET_SHAPED.search(credential_reference):
            return (
                ["body", "credential_reference"],
                SECRET_MISPLACED,
                "A credential belongs in the secret store or, for AWS, is an IAM role ARN. "
                "Give a role ARN, never a key.",
            )
        return (
            ["body", "credential_reference"],
            "invalid_credential_reference",
            "An AWS credential reference is an IAM role ARN: arn:aws:iam::<account>:role/<name>.",
        )
    if match.group("account") != account_reference:
        return (
            ["body", "credential_reference"],
            "credential_account_mismatch",
            "The role ARN belongs to a different AWS account than the account reference.",
        )
    if any(REGION.fullmatch(region) is None for region in regions):
        return (
            ["body", "region_configuration"],
            "invalid_region",
            "A region is a name such as eu-west-1.",
        )
    return None
