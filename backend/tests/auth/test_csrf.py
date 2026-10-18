"""CSRF double-submit and origin validation (docs/SECURITY.md §11).

The middleware is mounted on a throwaway app with a trivial echo route, so
these tests exercise the control itself rather than any particular endpoint —
and so a future endpoint cannot accidentally be the only thing covered.

The cases that matter are the ones an attacker actually has: a cross-origin
form post (no header obtainable), a token lifted from a different session, and
a missing `Origin`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, CsrfMiddleware, new_csrf_token
from app.core.errors import register_exception_handlers

pytestmark = pytest.mark.security

ORIGIN = "http://testserver"
TOKEN = "a-csrf-token-value"


@pytest.fixture
def csrf_client() -> Iterator[TestClient]:
    app = FastAPI()
    app.add_middleware(CsrfMiddleware)
    register_exception_handlers(app)

    @app.get("/thing")
    def read() -> dict[str, bool]:
        return {"read": True}

    @app.post("/thing")
    def write() -> dict[str, bool]:
        return {"written": True}

    @app.patch("/thing")
    def patch() -> dict[str, bool]:
        return {"patched": True}

    @app.delete("/thing")
    def remove() -> dict[str, bool]:
        return {"deleted": True}

    with TestClient(app) as client:
        yield client


# --- the happy path, so the rest is not passing by accident -----------------


def test_matching_token_and_origin_is_accepted(csrf_client: TestClient) -> None:
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post(
        "/thing",
        headers={CSRF_HEADER_NAME: TOKEN, "Origin": ORIGIN},
    )
    assert response.status_code == 200
    assert response.json() == {"written": True}


def test_get_is_never_checked(csrf_client: TestClient) -> None:
    """§11 rule 4: GET never changes state, so it has nothing to protect."""
    assert csrf_client.get("/thing").status_code == 200


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_every_state_changing_method_is_checked(csrf_client: TestClient, method: str) -> None:
    response = getattr(csrf_client, method)("/thing", headers={"Origin": ORIGIN})
    assert response.status_code == 403


# --- the failure matrix (docs/TESTING.md §8, "CSRF") ------------------------


def test_missing_header_is_rejected(csrf_client: TestClient) -> None:
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post("/thing", headers={"Origin": ORIGIN})
    assert response.status_code == 403


def test_missing_cookie_is_rejected(csrf_client: TestClient) -> None:
    response = csrf_client.post("/thing", headers={CSRF_HEADER_NAME: TOKEN, "Origin": ORIGIN})
    assert response.status_code == 403


def test_mismatched_token_is_rejected(csrf_client: TestClient) -> None:
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post(
        "/thing", headers={CSRF_HEADER_NAME: "a-different-token", "Origin": ORIGIN}
    )
    assert response.status_code == 403


def test_token_from_another_session_is_rejected(csrf_client: TestClient) -> None:
    """The attacker holds a valid token — just not this session's."""
    csrf_client.cookies.set(CSRF_COOKIE_NAME, new_csrf_token())
    response = csrf_client.post(
        "/thing", headers={CSRF_HEADER_NAME: new_csrf_token(), "Origin": ORIGIN}
    )
    assert response.status_code == 403


def test_cross_origin_request_is_rejected(csrf_client: TestClient) -> None:
    """Even with a matching token, a foreign Origin is refused.

    Layer 3 catches the case where the token leaked some other way.
    """
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post(
        "/thing",
        headers={CSRF_HEADER_NAME: TOKEN, "Origin": "https://evil.example.invalid"},
    )
    assert response.status_code == 403


def test_absent_origin_and_referer_is_rejected(csrf_client: TestClient) -> None:
    """Silence is not consent: no Origin means we were told nothing."""
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post("/thing", headers={CSRF_HEADER_NAME: TOKEN})
    assert response.status_code == 403


def test_referer_is_accepted_when_origin_is_absent(csrf_client: TestClient) -> None:
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post(
        "/thing",
        headers={CSRF_HEADER_NAME: TOKEN, "Referer": f"{ORIGIN}/some/page"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://testserver.evil.invalid",  # suffix
        "http://evil.invalid/?x=http://testserver",  # substring in a query
        "https://testserver",  # scheme differs
        "http://testserver:8080",  # port differs
        "null",  # a sandboxed iframe or a redirect-stripped origin
        "",
    ],
)
def test_near_miss_origins_are_rejected(csrf_client: TestClient, origin: str) -> None:
    """A substring or suffix match would be a bypass; the check is exact."""
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post("/thing", headers={CSRF_HEADER_NAME: TOKEN, "Origin": origin})
    assert response.status_code == 403


def test_an_unauthenticated_request_is_still_checked(csrf_client: TestClient) -> None:
    """ "No session" must not be a way to skip the check (login CSRF)."""
    response = csrf_client.post("/thing", headers={"Origin": ORIGIN})
    assert response.status_code == 403


# --- shape of the rejection -------------------------------------------------


def test_rejection_uses_the_standard_error_envelope(csrf_client: TestClient) -> None:
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    body = csrf_client.post("/thing", headers={"Origin": ORIGIN}).json()
    assert body["error"]["code"] == "forbidden"
    assert set(body["error"]) == {"code", "message", "correlation_id", "details"}


def test_rejection_does_not_echo_the_submitted_token(csrf_client: TestClient) -> None:
    csrf_client.cookies.set(CSRF_COOKIE_NAME, TOKEN)
    response = csrf_client.post(
        "/thing", headers={CSRF_HEADER_NAME: "submitted-value", "Origin": ORIGIN}
    )
    assert "submitted-value" not in response.text
    assert TOKEN not in response.text


# --- tokens -----------------------------------------------------------------


def test_csrf_tokens_are_unique_and_long() -> None:
    tokens = {new_csrf_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) >= 43 for token in tokens)


# --- exemptions -------------------------------------------------------------


def test_exempt_paths_are_exact_not_prefixes() -> None:
    """A new endpoint must not inherit an exemption by nesting under one."""
    app = FastAPI()
    app.add_middleware(CsrfMiddleware, exempt_paths=frozenset({"/api/v1/webhooks/alerts"}))
    register_exception_handlers(app)

    @app.post("/api/v1/webhooks/alerts")
    def webhook() -> dict[str, bool]:
        return {"ingested": True}

    @app.post("/api/v1/webhooks/alerts/escalate")
    def nested() -> dict[str, bool]:
        return {"escalated": True}

    with TestClient(app) as client:
        assert client.post("/api/v1/webhooks/alerts").status_code == 200
        assert client.post("/api/v1/webhooks/alerts/escalate").status_code == 403


def test_the_app_exempts_exactly_the_alert_webhook() -> None:
    """M3.3's webhook is the one non-browser caller; nothing else is exempt."""
    from app.main import create_app

    app = create_app()
    exempt = [middleware for middleware in app.user_middleware if middleware.cls is CsrfMiddleware]
    assert len(exempt) == 1
    assert exempt[0].kwargs.get("exempt_paths", frozenset()) == frozenset(
        {"/api/v1/webhooks/alerts"}
    )
