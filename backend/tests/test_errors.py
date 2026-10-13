"""Error envelope and structured validation errors (docs/API_CONTRACTS.md §3, §8)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError, NotConfiguredError, register_exception_handlers
from app.core.middleware import CorrelationIdMiddleware

ENVELOPE_KEYS = {"code", "message", "correlation_id", "details"}


def test_404_uses_the_standard_envelope(client: TestClient) -> None:
    body = client.get("/no-such-route").json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["code"] == "not_found"


def test_405_is_mapped(client: TestClient) -> None:
    """A wrong method on an existing route maps to ``method_not_allowed``.

    The CSRF credentials are supplied because M1.3 put ``CsrfMiddleware``
    outside the router: a state-changing request is rejected with ``403``
    *before* routing, so without them this would never reach the 405. That
    precedence is deliberate — no handler and no database session should be
    created for a request that fails CSRF — and it is asserted directly by
    ``test_csrf_is_checked_before_routing`` below.
    """
    client.cookies.set("dcp_csrf", "a-token")
    response = client.post(
        "/healthz", headers={"Origin": "http://testserver", "X-CSRF-Token": "a-token"}
    )
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_csrf_is_checked_before_routing(client: TestClient) -> None:
    """A state-changing request without CSRF is refused whatever the path.

    Including a path that does not exist — so probing for routes with POST
    reveals nothing about which ones are real.
    """
    for path in ("/healthz", "/no-such-route", "/api/v1/auth/logout"):
        response = client.post(path)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden"


def test_correlation_id_is_present_in_the_envelope(client: TestClient) -> None:
    response = client.get("/no-such-route")
    assert response.json()["error"]["correlation_id"] == response.headers["X-Correlation-ID"]


# --- a throwaway app exercising the remaining handlers ----------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(start|stop|restart)$")
    count: int = Field(ge=1, le=10)


@pytest.fixture
def error_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    @app.post("/validate")
    async def _validate(_body: _Body) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/app-error")
    async def _app_error() -> None:
        raise NotConfiguredError()

    @app.get("/custom-error")
    async def _custom_error() -> None:
        raise AppError("Placeholder failure.", details={"hint": "placeholder"})

    @app.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("internal detail: connection to db.internal failed")

    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_shape(error_client: TestClient) -> None:
    response = error_client.post("/validate", json={"action": "delete", "count": 99})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    codes = {tuple(f["loc"]): f["code"] for f in error["details"]["fields"]}
    assert codes[("body", "action")] == "pattern"
    assert codes[("body", "count")] == "max_value"


def test_unknown_field_is_rejected(error_client: TestClient) -> None:
    response = error_client.post("/validate", json={"action": "start", "count": 1, "surprise": "x"})
    assert response.status_code == 422
    codes = {f["code"] for f in response.json()["error"]["details"]["fields"]}
    assert "unknown_field" in codes


def test_missing_field_code(error_client: TestClient) -> None:
    response = error_client.post("/validate", json={"count": 1})
    codes = {f["code"] for f in response.json()["error"]["details"]["fields"]}
    assert "required" in codes


@pytest.mark.security
def test_validation_error_does_not_echo_the_rejected_value(
    error_client: TestClient,
) -> None:
    """A rejected credential must not be reflected back (API_CONTRACTS §8)."""
    canary = "CANARY-rejected-secret-value"
    response = error_client.post("/validate", json={"action": canary, "count": 1})
    assert canary not in response.text


def test_app_error_carries_its_code(error_client: TestClient) -> None:
    response = error_client.get("/app-error")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


def test_app_error_details_are_passed_through(error_client: TestClient) -> None:
    response = error_client.get("/custom-error")
    assert response.status_code == 500
    assert response.json()["error"]["details"] == {"hint": "placeholder"}


@pytest.mark.security
def test_unhandled_exception_reveals_nothing(error_client: TestClient) -> None:
    response = error_client.get("/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "An unexpected error occurred."
    for forbidden in ("db.internal", "RuntimeError", "Traceback", "internal detail"):
        assert forbidden not in response.text
