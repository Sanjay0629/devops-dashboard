"""The adapter error taxonomy, its API mapping and its retry table (``OPEN-60``).

Nine classes, exactly ARCHITECTURE.md §7.1. **The provider's own text never enters one of
these**: the message is a fixed sentence per class and the only variable part is a closed
``detail_code`` (``^[a-z][a-z0-9_]{0,63}$``), so a provider payload, hostname or token cannot
be carried to a client by an exception. An adapter that wants the original kept for the log
chains it with ``raise ... from``; the chain stays server-side.
"""

from __future__ import annotations

import math
import re
from typing import ClassVar, Final

from app.core.errors import AppError, NotConfiguredError

_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ADAPTER_ID: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
#: A provider can ask for hours; a client told to wait that long is better told to look at
#: the integrations page.
RETRY_AFTER_CAP_SECONDS: Final = 3600


class ProviderError(Exception):
    provider_error_class: ClassVar[str]
    message: ClassVar[str]
    http_status: ClassVar[int]
    http_code: ClassVar[str]

    def __init__(self, *, adapter_id: str | None = None, detail_code: str | None = None) -> None:
        if adapter_id is not None and _ADAPTER_ID.fullmatch(adapter_id) is None:
            raise ValueError("adapter_id is malformed")
        if detail_code is not None and _CODE.fullmatch(detail_code) is None:
            raise ValueError("detail_code must be a closed code, not text")
        super().__init__(self.provider_error_class)
        self.adapter_id = adapter_id
        self.detail_code = detail_code


class AuthenticationError(ProviderError):
    provider_error_class = "authentication"
    message = (
        "The cloud provider rejected DCP's credentials. Check the cloud account's configuration."
    )
    http_status, http_code = 502, "provider_error"


class AuthorizationError(ProviderError):
    provider_error_class = "authorization"
    message = "The cloud provider denied the request. Check the execution role's permissions."
    http_status, http_code = 502, "provider_error"


class NotFound(ProviderError):
    provider_error_class = "not_found"
    message = "The cloud provider no longer has this resource. The inventory may be out of date."
    http_status, http_code = 502, "provider_error"


class Throttled(ProviderError):
    provider_error_class = "throttled"
    message = "The cloud provider is rate limiting requests. Try again shortly."
    http_status, http_code = 503, "provider_unavailable"

    def __init__(
        self,
        *,
        retry_after: float | None = None,
        adapter_id: str | None = None,
        detail_code: str | None = None,
    ) -> None:
        super().__init__(adapter_id=adapter_id, detail_code=detail_code)
        if retry_after is not None and not (math.isfinite(retry_after) and retry_after >= 0):
            raise ValueError("retry_after must be a finite, non-negative number of seconds")
        self.retry_after = retry_after


class Timeout(ProviderError):
    provider_error_class = "timeout"
    message = "The cloud provider did not answer in time."
    http_status, http_code = 504, "provider_timeout"


class UnsupportedOperation(ProviderError):
    provider_error_class = "unsupported_operation"
    message = "This operation is not supported by this provider."
    http_status, http_code = 422, "unsupported_operation"


class PartialFailure(ProviderError):
    provider_error_class = "partial_failure"
    message = "The operation succeeded on some targets and failed on others."
    http_status, http_code = 502, "provider_error"


class ProviderUnavailable(ProviderError):
    provider_error_class = "provider_unavailable"
    message = "The cloud provider is unavailable."
    http_status, http_code = 503, "provider_unavailable"


class NotConfigured(ProviderError):
    provider_error_class = "not_configured"
    message = "No provider adapter is configured for this account."
    http_status, http_code = 503, "not_configured"


TAXONOMY: Final[tuple[type[ProviderError], ...]] = (
    AuthenticationError,
    AuthorizationError,
    NotFound,
    Throttled,
    Timeout,
    UnsupportedOperation,
    PartialFailure,
    ProviderUnavailable,
    NotConfigured,
)


def to_app_error(error: ProviderError) -> AppError:
    """The API's view of ``error`` (API_CONTRACTS §3). Never carries provider text."""
    details: dict[str, str] = {"provider_error_class": error.provider_error_class}
    if error.adapter_id is not None:
        details["provider"] = error.adapter_id
    if error.detail_code is not None:
        details["reason"] = error.detail_code
    if isinstance(error, NotConfigured):
        return NotConfiguredError(error.message, details=details)
    app_error = AppError(error.message, details=details)
    app_error.status_code = error.http_status
    app_error.code = error.http_code
    if isinstance(error, Throttled) and error.retry_after is not None:
        seconds = min(RETRY_AFTER_CAP_SECONDS, max(1, math.ceil(error.retry_after)))
        app_error.headers = {"Retry-After": str(seconds)}
    return app_error


#: Retries allowed after the first attempt: ``(read, write)`` (ARCHITECTURE.md §10.2). A write
#: is retried only where the provider refused the request **before acting**; anything that
#: leaves it unclear whether the provider acted is reconciled from the persisted handle,
#: never retried (CLAUDE.md §5). This is data only: the executor arrives with the first adapter.
_RETRIES: Final[dict[type[ProviderError], tuple[int, int]]] = {
    Throttled: (5, 5),
    Timeout: (2, 0),
    ProviderUnavailable: (5, 0),
    AuthenticationError: (1, 1),  # once, after a forced credential refresh
}


def retries_allowed(error: type[ProviderError], *, write: bool) -> int:
    reads, writes = _RETRIES.get(error, (0, 0))
    return writes if write else reads
