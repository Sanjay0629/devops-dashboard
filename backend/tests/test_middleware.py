"""Correlation ids and security headers (AD-18, docs/SECURITY.md §7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.middleware import CORRELATION_HEADER, DURATION_HEADER, SECURITY_HEADERS


def test_correlation_id_generated_when_absent(client: TestClient) -> None:
    response = client.get("/healthz")
    assert len(response.headers[CORRELATION_HEADER]) >= 8


def test_supplied_correlation_id_is_echoed(client: TestClient) -> None:
    supplied = "corr-0123456789abcdef"
    response = client.get("/healthz", headers={CORRELATION_HEADER: supplied})
    assert response.headers[CORRELATION_HEADER] == supplied


@pytest.mark.security
@pytest.mark.parametrize(
    "hostile",
    [
        "short",
        "x" * 200,
        "has spaces",
        "inject\r\nX-Evil: 1",
        "../../etc/passwd",
        "<script>alert(1)</script>",
    ],
)
def test_hostile_correlation_id_is_replaced_not_reflected(client: TestClient, hostile: str) -> None:
    """An untrusted header that lands in logs and audit rows is bounded first."""
    response = client.get("/healthz", headers={CORRELATION_HEADER: hostile})
    returned = response.headers[CORRELATION_HEADER]
    assert returned != hostile
    assert "\r" not in returned and "\n" not in returned
    assert response.status_code == 200


def test_duration_header_present(client: TestClient) -> None:
    assert float(client.get("/healthz").headers[DURATION_HEADER]) >= 0


@pytest.mark.security
@pytest.mark.parametrize("header", sorted(SECURITY_HEADERS))
def test_security_header_on_every_response(client: TestClient, header: str) -> None:
    assert client.get("/healthz").headers[header] == SECURITY_HEADERS[header]


@pytest.mark.security
def test_security_headers_present_on_error_responses(client: TestClient) -> None:
    """Headers must survive the error path, which is where they get forgotten."""
    response = client.get("/no-such-route")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.security
def test_csp_has_no_unsafe_inline_script(client: TestClient) -> None:
    csp = client.get("/healthz").headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.security
def test_no_hsts_over_plain_http(client: TestClient) -> None:
    """A cached HSTS policy from local HTTP development is a self-inflicted outage."""
    assert "Strict-Transport-Security" not in client.get("/healthz").headers
