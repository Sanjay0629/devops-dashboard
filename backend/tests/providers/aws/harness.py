"""A stubbed AWS layer at the botocore boundary (``OPEN-90``): real clients, real parameter
validation, real exceptions, no network.

``botocore.stub.Stubber`` wraps genuine ``ssm`` and ``sts`` clients, so the request each adapter
call builds is validated against AWS's own service model, and an error is a real
``ClientError`` with the code, status and headers botocore would produce. A call the test did not
queue raises ``StubResponseError``, which is how "was this retried?" is asserted. This proves the
adapter's behaviour; it does not prove AWS accepts the document (that is the manual check).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import botocore.session
from botocore.config import Config
from botocore.stub import Stubber

from app.providers.aws.adapter import AwsSsmAdapter
from app.providers.aws.api import Credentials
from app.providers.types import ServerRef, ServiceRef

ACCOUNT = "123456789012"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/DcpExecution"
REGION = "eu-west-1"
INSTANCE = "i-0abc123def4567890"
SERVICE = ServiceRef(uuid.UUID(int=7), "nginx.service")
REF = ServerRef(INSTANCE, ACCOUNT, "AWS_SSM", REGION, ROLE_ARN)
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
COMMAND_ID = "0b7f5d0e-5d1b-4e58-a4a6-6f8f6a1c2d3e"


_SESSION = botocore.session.get_session()  # one loader, so service models are read once


def _client(service: str) -> Any:
    return _SESSION.create_client(
        service,
        region_name=REGION,
        aws_access_key_id="testing-not-a-key",
        aws_secret_access_key="testing-not-a-secret",
        config=Config(retries={"max_attempts": 0}),
    )


class Clock:
    """Time that only moves when the adapter sleeps."""

    def __init__(self) -> None:
        self.now = T0
        self.mono = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.mono += seconds
        self.now += timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self.mono

    def clock(self) -> datetime:
        return self.now


class Aws:
    def __init__(self, *, jitter: float = 0.5, version: str = "3") -> None:
        self.ssm = _client("ssm")
        self.sts = _client("sts")
        self.ssm_stub = Stubber(self.ssm)
        self.sts_stub = Stubber(self.sts)
        self.ssm_stub.activate()
        self.sts_stub.activate()
        self.time = Clock()
        self.factory_calls: list[tuple[str, str, bool, bool]] = []
        self.adapter = AwsSsmAdapter(
            document_version=version,
            client_factory=self._factory,
            sleep=self.time.sleep,
            monotonic=self.time.monotonic,
            jitter=lambda: jitter,
            clock=self.time.clock,
        )

    def _factory(
        self, service: str, region: str, credentials: Credentials | None, write: bool
    ) -> Any:
        self.factory_calls.append((service, region, credentials is not None, write))
        return {"sts": self.sts, "ssm": self.ssm}[service]

    # -- queueing ---------------------------------------------------------------------------

    def assume(self, session_name: str = "dcp-read", *, minutes: int = 15) -> None:
        self.sts_stub.add_response(
            "assume_role",
            {
                "Credentials": {
                    "AccessKeyId": "ASIATESTINGNOTAKEY1",
                    "SecretAccessKey": "testing-not-a-secret",
                    "SessionToken": "testing-not-a-token-0123456789",
                    "Expiration": self.time.now + timedelta(minutes=minutes),
                }
            },
            {
                "RoleArn": ROLE_ARN,
                "RoleSessionName": session_name,
                "DurationSeconds": 900,
            },
        )

    def sent(self, command_id: str = COMMAND_ID, expected: dict[str, Any] | None = None) -> None:
        self.ssm_stub.add_response("send_command", {"Command": {"CommandId": command_id}}, expected)

    def invocation(
        self,
        status: str,
        *,
        output: str = "",
        details: str | None = None,
        code: int | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "CommandId": COMMAND_ID,
            "InstanceId": INSTANCE,
            "Status": status,
            "StatusDetails": details or status,
            "StandardOutputContent": output,
        }
        if code is not None:
            body["ResponseCode"] = code
        self.ssm_stub.add_response(
            "get_command_invocation",
            body,
            {"CommandId": COMMAND_ID, "InstanceId": INSTANCE},
        )

    def error(
        self,
        method: str,
        code: str,
        *,
        status: int = 400,
        headers: dict[str, str] | None = None,
        sts: bool = False,
    ) -> None:
        stub = self.sts_stub if sts else self.ssm_stub
        stub.add_client_error(
            method,
            service_error_code=code,
            service_message="message naming i-0secret0instance and arn:aws:iam::999999999999:root",
            http_status_code=status,
            response_meta={"HTTPHeaders": headers or {}},
        )

    def instances(self, *pings: str) -> None:
        self.ssm_stub.add_response(
            "describe_instance_information",
            {
                "InstanceInformationList": [
                    {"InstanceId": INSTANCE, "PingStatus": ping} for ping in pings
                ]
            },
            {"Filters": [{"Key": "InstanceIds", "Values": [INSTANCE]}]},
        )

    def done(self) -> None:
        """Everything queued was used: nothing was skipped or short-circuited."""
        self.ssm_stub.assert_no_pending_responses()
        self.sts_stub.assert_no_pending_responses()
