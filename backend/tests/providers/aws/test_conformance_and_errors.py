"""The shared conformance harness, and the error table, for the AWS adapter."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

from app.providers.aws.adapter import AwsSsmAdapter
from app.providers.aws.errors import map_error
from app.providers.errors import (
    TAXONOMY,
    AuthenticationError,
    AuthorizationError,
    NotFound,
    ProviderUnavailable,
    Throttled,
    Timeout,
)
from tests.providers import conformance
from tests.providers.aws.harness import REF


class Denying:
    """A client whose every method is refused, with a real botocore error."""

    def __getattr__(self, name: str) -> Any:
        def call(**_: Any) -> Any:
            raise ClientError(
                {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
                name,
            )

        return call


def test_the_adapter_passes_the_shared_conformance_harness() -> None:
    adapter = AwsSsmAdapter(document_version="1", client_factory=lambda *_: Denying())
    conformance.check(adapter, demo=False, server=REF)


def _client_error(
    code: str, status: int = 400, headers: dict[str, str] | None = None
) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": "names i-0secret and arn:aws:iam::999999999999:root",
            },
            "ResponseMetadata": {"HTTPStatusCode": status, "HTTPHeaders": headers or {}},
        },
        "Operation",
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        *[
            (c, Throttled)
            for c in (
                "ThrottlingException",
                "Throttling",
                "TooManyRequestsException",
                "RequestLimitExceeded",
                "SlowDown",
            )
        ],
        *[
            (c, AuthenticationError)
            for c in (
                "ExpiredToken",
                "ExpiredTokenException",
                "InvalidClientTokenId",
                "UnrecognizedClientException",
                "SignatureDoesNotMatch",
            )
        ],
        *[
            (c, AuthorizationError)
            for c in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation")
        ],
        *[
            (c, NotFound)
            for c in (
                "InvalidInstanceId",
                "InvalidDocument",
                "InvalidCommandId",
                "InvocationDoesNotExist",
            )
        ],
        ("RequestTimeout", Timeout),
        ("InternalServerError", ProviderUnavailable),
        ("ServiceUnavailable", ProviderUnavailable),
        ("NeverHeardOfIt", ProviderUnavailable),
    ],
)
def test_every_error_code_maps_to_one_class(code: str, expected: type) -> None:
    mapped = map_error(_client_error(code), call="send_command")
    assert type(mapped) is expected
    assert mapped.adapter_id == "aws_ssm"
    assert type(mapped) in TAXONOMY


def test_a_5xx_with_an_unknown_code_is_unavailable() -> None:
    assert isinstance(
        map_error(_client_error("Weird", status=502), call="send_command"), ProviderUnavailable
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectTimeoutError(endpoint_url="https://ssm.example"), Timeout),
        (ReadTimeoutError(endpoint_url="https://ssm.example"), Timeout),
        (EndpointConnectionError(endpoint_url="https://ssm.example"), ProviderUnavailable),
        (ConnectionClosedError(endpoint_url="https://ssm.example"), ProviderUnavailable),
        (NoCredentialsError(), AuthenticationError),
    ],
)
def test_network_and_credential_failures_map_to_the_taxonomy(
    error: Exception, expected: type
) -> None:
    assert type(map_error(error, call="describe_instance_information")) is expected


def test_retry_after_is_taken_only_when_it_is_a_sane_number() -> None:
    assert (
        map_error(
            _client_error("ThrottlingException", headers={"retry-after": "7"}), call="send_command"
        ).retry_after
        == 7.0
    )  # type: ignore[attr-defined]
    for bad in ("soon", "-3", "nan"):
        mapped = map_error(
            _client_error("ThrottlingException", headers={"retry-after": bad}), call="send_command"
        )
        assert mapped.retry_after is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("call", "detail"),
    [
        ("assume_role", "assume_role_denied"),
        ("send_command", "send_command_denied"),
        ("describe_document", "access_denied"),
    ],
)
def test_an_authorisation_failure_names_what_to_fix(call: str, detail: str) -> None:
    assert map_error(_client_error("AccessDenied", 403), call=call).detail_code == detail


def test_no_provider_text_survives_the_mapping() -> None:
    for code in ("AccessDenied", "InvalidInstanceId", "Weird", "ThrottlingException"):
        mapped = map_error(_client_error(code), call="send_command")
        shown = f"{mapped!s} {mapped.args} {mapped.detail_code} {mapped.message}"
        assert "i-0secret" not in shown
        assert "999999999999" not in shown


def test_an_unknown_call_name_is_refused() -> None:
    with pytest.raises(ValueError, match="not a known"):
        map_error(_client_error("x"), call="run_instances")
