"""The taxonomy, its API mapping (API_CONTRACTS §3) and the read/write retry table (§10.2)."""

from __future__ import annotations

import pytest

from app.core.errors import NotConfiguredError
from app.providers.errors import (
    TAXONOMY,
    AuthenticationError,
    NotConfigured,
    NotFound,
    PartialFailure,
    ProviderError,
    ProviderUnavailable,
    Throttled,
    Timeout,
    UnsupportedOperation,
    retries_allowed,
    to_app_error,
)

EXPECTED = {
    "authentication": (502, "provider_error"),
    "authorization": (502, "provider_error"),
    "not_found": (502, "provider_error"),
    "throttled": (503, "provider_unavailable"),
    "timeout": (504, "provider_timeout"),
    "unsupported_operation": (422, "unsupported_operation"),
    "partial_failure": (502, "provider_error"),
    "provider_unavailable": (503, "provider_unavailable"),
    "not_configured": (503, "not_configured"),
}


def test_the_taxonomy_is_exactly_the_nine_documented_classes() -> None:
    assert {c.provider_error_class for c in TAXONOMY} == set(EXPECTED)
    assert len(TAXONOMY) == 9


@pytest.mark.parametrize("cls", TAXONOMY, ids=lambda c: c.__name__)
def test_each_class_maps_to_its_documented_status_and_code(cls: type[ProviderError]) -> None:
    app_error = to_app_error(cls(adapter_id="demo", detail_code="why_not"))
    assert (app_error.status_code, app_error.code) == EXPECTED[cls.provider_error_class]
    assert app_error.details == {
        "provider_error_class": cls.provider_error_class,
        "provider": "demo",
        "reason": "why_not",
    }
    assert app_error.message == cls.message


def test_not_configured_reuses_the_existing_error_type() -> None:
    assert isinstance(to_app_error(NotConfigured()), NotConfiguredError)


def test_not_found_is_not_a_404() -> None:
    # A 404 would say DCP's own resource is absent; this says the provider lost it.
    assert to_app_error(NotFound()).status_code == 502


def test_unsupported_is_never_a_generic_failure() -> None:
    assert to_app_error(UnsupportedOperation()).code == "unsupported_operation"


@pytest.mark.parametrize(
    ("retry_after", "header"),
    [(None, None), (0, "1"), (0.2, "1"), (7.1, "8"), (99999, "3600")],
)
def test_throttled_carries_retry_after(retry_after: float | None, header: str | None) -> None:
    headers = to_app_error(Throttled(retry_after=retry_after)).response_headers()
    assert (headers or {}).get("Retry-After") == header


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_throttled_rejects_a_nonsense_retry_after(bad: float) -> None:
    with pytest.raises(ValueError, match="retry_after"):
        Throttled(retry_after=bad)


@pytest.mark.parametrize(
    "text",
    ["Access denied for arn:aws:iam::1:role/x", "Bearer abc", "has space", "UPPER", "", "x" * 65],
)
def test_provider_text_cannot_become_a_detail_code(text: str) -> None:
    with pytest.raises(ValueError, match="closed code"):
        Timeout(detail_code=text)


def test_the_exception_text_is_the_class_not_provider_text() -> None:
    assert str(Timeout(detail_code="slow")) == "timeout"


def test_a_malformed_adapter_id_is_refused() -> None:
    with pytest.raises(ValueError, match="adapter_id"):
        Timeout(adapter_id="Not An Id")


def test_no_message_names_a_provider_or_a_host() -> None:
    for cls in TAXONOMY:
        assert "arn:" not in cls.message
        assert "http" not in cls.message


# ARCHITECTURE.md §10.2: (reads, writes) retries after the first attempt.
@pytest.mark.parametrize(
    ("cls", "reads", "writes"),
    [
        (Throttled, 5, 5),
        (Timeout, 2, 0),
        (ProviderUnavailable, 5, 0),
        (AuthenticationError, 1, 1),
        (PartialFailure, 0, 0),
        (UnsupportedOperation, 0, 0),
        (NotFound, 0, 0),
        (NotConfigured, 0, 0),
    ],
)
def test_the_retry_table(cls: type[ProviderError], reads: int, writes: int) -> None:
    assert retries_allowed(cls, write=False) == reads
    assert retries_allowed(cls, write=True) == writes


def test_a_write_is_never_retried_when_the_provider_may_have_acted() -> None:
    # The ambiguous classes: the request may have been accepted. Reconcile, do not retry.
    for cls in (Timeout, ProviderUnavailable, PartialFailure):
        assert retries_allowed(cls, write=True) == 0


def test_every_class_has_a_retry_answer() -> None:
    for cls in TAXONOMY:
        assert retries_allowed(cls, write=True) >= 0
