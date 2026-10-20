"""botocore failures to the provider error taxonomy (``OPEN-82``).

**Provider text never survives.** An AWS error message can name an instance, an account or a
policy; the only thing kept is a closed ``detail_code`` chosen here from the error *code*, and
the original is chained with ``raise ... from`` so it stays in the server-side log.
"""

from __future__ import annotations

from typing import Final

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    NotFound,
    ProviderError,
    ProviderUnavailable,
    Throttled,
    Timeout,
)

ADAPTER_ID: Final = "aws_ssm"

#: ``call`` values: which AWS operation failed. Closed, because it picks the actionable code.
CALLS: Final = frozenset(
    {
        "assume_role",
        "send_command",
        "get_command_invocation",
        "describe_instance_information",
        "describe_document",
        "list_commands",
    }
)

_THROTTLED = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "ThrottledException",
        "TooManyRequestsException",
        "RequestLimitExceeded",
        "RequestThrottled",
        "RequestThrottledException",
        "SlowDown",
        "ProvisionedThroughputExceededException",
    }
)
_AUTHENTICATION = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
        "SignatureDoesNotMatch",
        "InvalidSignatureException",
        "AuthFailure",
        "MissingAuthenticationToken",
        "InvalidToken",
        "IncompleteSignature",
    }
)
_AUTHORIZATION = frozenset(
    {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
        "UnauthorizedAccess",
        "NotAuthorized",
    }
)
#: error code -> the closed reason an operator can act on
_NOT_FOUND = {
    "InvalidInstanceId": "instance_not_managed",
    "InvalidDocument": "document_not_found",
    "InvalidDocumentVersion": "document_version_not_found",
    "InvalidCommandId": "command_not_found",
    "InvocationDoesNotExist": "command_not_found",
}
_UNAVAILABLE = frozenset(
    {
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalServerError",
        "InternalFailure",
        "InternalError",
        "ServerException",
    }
)
_TIMEOUT = frozenset({"RequestTimeout", "RequestTimeoutException"})

_DENIED_DETAIL = {
    "assume_role": "assume_role_denied",
    "send_command": "send_command_denied",
}


def _retry_after(error: ClientError) -> float | None:
    headers = error.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    raw = headers.get("retry-after")
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


def map_error(error: Exception, *, call: str) -> ProviderError:
    """The taxonomy class for ``error``. Never raises for an unrecognised failure."""
    if call not in CALLS:
        raise ValueError("call is not a known AWS operation")
    kw = {"adapter_id": ADAPTER_ID}
    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code", ""))
        status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)
        if code in _THROTTLED:
            return Throttled(retry_after=_retry_after(error), **kw)
        if code in _AUTHENTICATION:
            return AuthenticationError(detail_code="credentials_rejected", **kw)
        if code in _AUTHORIZATION:
            return AuthorizationError(detail_code=_DENIED_DETAIL.get(call, "access_denied"), **kw)
        if code in _NOT_FOUND:
            return NotFound(detail_code=_NOT_FOUND[code], **kw)
        if code in _TIMEOUT:
            return Timeout(**kw)
        if code in _UNAVAILABLE or status >= 500:
            return ProviderUnavailable(detail_code="provider_error", **kw)
        return ProviderUnavailable(detail_code="unmapped_error", **kw)
    if isinstance(error, ConnectTimeoutError | ReadTimeoutError):
        return Timeout(**kw)
    if isinstance(error, NoCredentialsError | PartialCredentialsError):
        return AuthenticationError(detail_code="no_credentials", **kw)
    if isinstance(error, EndpointConnectionError | ConnectionClosedError):
        return ProviderUnavailable(detail_code="endpoint_unreachable", **kw)
    if isinstance(error, BotoCoreError):
        return ProviderUnavailable(detail_code="unmapped_error", **kw)
    return ProviderUnavailable(detail_code="unmapped_error", **kw)
