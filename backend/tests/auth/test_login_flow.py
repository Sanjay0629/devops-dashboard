"""Login endpoint behaviour that needs no IdP and no Redis.

The open-redirect matrix is the important part. An open redirect on a login
endpoint is a phishing primitive: the victim authenticates at the real domain,
sees the real login page, and is then handed to the attacker. So the check is
an allowlist of shapes, not a blocklist of bad ones.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.router import validate_next

pytestmark = pytest.mark.security


# --- `next` validation (docs/API_CONTRACTS.md §12.1) ------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "/",
        "/servers",
        "/servers/123",
        "/servers?environment=staging&sort=name",
        "/a.b/c-d_e/f",
        "/deployments?page=2",
    ],
)
def test_accepts_same_origin_relative_paths(candidate: str) -> None:
    assert validate_next(candidate) == candidate


@pytest.mark.parametrize(
    ("candidate", "attack"),
    [
        ("http://evil.example.invalid/", "absolute URL"),
        ("https://evil.example.invalid/x", "absolute URL, TLS"),
        ("//evil.example.invalid/x", "protocol-relative — a browser reads an authority"),
        ("///evil.example.invalid", "triple slash, same trick"),
        ("/\\evil.example.invalid", "backslash normalised to / by some browsers"),
        ("\\\\evil.example.invalid", "UNC-style authority"),
        ("/%2F%2Fevil.example.invalid", "percent-encoded //"),
        ("javascript:alert(1)", "scheme injection"),
        ("data:text/html,<script>alert(1)</script>", "data URL"),
        ("/path\nLocation: http://evil.invalid", "header injection via newline"),
        ("/path\rSet-Cookie: x=y", "header injection via carriage return"),
        ("/path\r\nX-Injected: 1", "CRLF injection"),
        ("/path\x00", "NUL byte"),
        ("servers", "no leading slash, resolves relative to the IdP"),
        ("..//evil", "no leading slash"),
        ("/" + "a" * 600, "over the documented length bound"),
        ("/path#@evil.example.invalid", "fragment with an authority"),
        ("/path<script>", "markup"),
        ("/path%0d%0aX: y", "encoded CRLF is not in the character class"),
    ],
)
def test_rejects_every_open_redirect_form(candidate: str, attack: str) -> None:
    assert validate_next(candidate) is None, attack


@pytest.mark.parametrize("candidate", [None, ""])
def test_absent_next_defaults_to_the_root(candidate: str | None) -> None:
    assert validate_next(candidate) == "/"


def test_rejected_next_is_a_422_and_the_value_is_not_echoed(
    client: TestClient, no_rate_limit: None
) -> None:
    """A rejected redirect target is a client bug or an attack — not a thing to
    quietly fix by falling back to "/", which would hide both."""
    response = client.get(
        "/api/v1/auth/login",
        params={"next": "http://evil.example.invalid/"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    # docs/API_CONTRACTS.md §8: never echo the rejected value.
    assert "evil.example.invalid" not in response.text


def test_over_long_next_is_rejected_by_the_query_bound(
    client: TestClient, no_rate_limit: None
) -> None:
    response = client.get(
        "/api/v1/auth/login",
        params={"next": "/" + "a" * 5000},
        follow_redirects=False,
    )
    assert response.status_code == 422


# --- unconfigured IdP -------------------------------------------------------


def test_login_without_a_configured_idp_is_503_not_configured(
    client: TestClient, no_rate_limit: None
) -> None:
    """PRD §11: the app starts without credentials. CLAUDE.md §7: it must not
    then pretend to authenticate."""
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


def test_not_configured_message_names_variables_not_values(
    client: TestClient, no_rate_limit: None
) -> None:
    message = client.get("/api/v1/auth/login", follow_redirects=False).json()["error"]["message"]
    assert "DCP_OIDC_ISSUER" in message
    assert "placeholder" not in message.lower()


def test_callback_without_a_configured_idp_is_503(client: TestClient, no_rate_limit: None) -> None:
    response = client.get(
        "/api/v1/auth/callback",
        params={"code": "x", "state": "y"},
        follow_redirects=False,
    )
    assert response.status_code == 503


# --- the protected routes reject anonymous callers --------------------------


def test_session_endpoint_without_a_cookie_is_401(client: TestClient) -> None:
    """PRD §4.1 acceptance criterion, for the one protected endpoint M1.3 has."""
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_session_endpoint_with_a_forged_cookie_is_401(client: TestClient) -> None:
    client.cookies.set("dcp_session", "a-made-up-session-identifier")
    assert client.get("/api/v1/auth/session").status_code == 401


def test_logout_without_a_session_is_rejected_before_any_work(client: TestClient) -> None:
    """CSRF is checked by middleware, so this never reaches the handler."""
    assert client.post("/api/v1/auth/logout").status_code == 403


def test_no_bearer_token_is_ever_accepted(client: TestClient) -> None:
    """docs/API_CONTRACTS.md §2: session cookie only; no bearer tokens."""
    response = client.get(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer an-access-token"},
    )
    assert response.status_code == 401


# --- the public-endpoint list is exactly what the contract says -------------


def test_only_the_documented_routes_exist(client: TestClient) -> None:
    """Read from the OpenAPI schema, which is also what the frontend types are
    generated from (docs/TECH_STACK.md §11) — so this asserts the published
    contract, not just the router object."""
    from app.main import create_app

    paths = {path for path in create_app().openapi()["paths"] if path.startswith("/api/v1/auth")}
    assert paths == {
        "/api/v1/auth/login",
        "/api/v1/auth/callback",
        "/api/v1/auth/logout",
        "/api/v1/auth/session",
    }


def test_no_endpoint_accepts_a_command_or_a_url(client: TestClient) -> None:
    """Mandatory rules 1, 2 and 9, asserted on the surface M1.3 adds.

    `next` is the only redirect-shaped input, and it cannot be absolute. No
    auth route takes a command, a script or a URL.
    """
    from app.auth.router import callback, login, logout, read_session

    for handler in (login, callback, logout, read_session):
        names = set(handler.__annotations__)
        assert not names & {"command", "script", "args", "argv", "url", "document"}


# --- rate limiting is wired to the auth class -------------------------------


def test_auth_endpoints_are_in_the_authentication_rate_limit_class() -> None:
    from app.auth import router as auth_router

    assert auth_router._RATE_BUCKET == "auth"


def test_rate_limit_failure_is_closed_for_auth() -> None:
    """docs/SECURITY.md §13: privileged classes fail closed."""
    import inspect

    from app.auth import router as auth_router

    source = inspect.getsource(auth_router._rate_limit)
    assert "fail_closed=True" in source


def test_rate_limit_fails_closed_when_redis_is_unreachable(client: TestClient) -> None:
    """Observed, not asserted structurally.

    The unit-test environment points DCP_REDIS_URL at a port nothing listens
    on (`tests/conftest.UNREACHABLE`) — whatever else the machine or the CI job
    is running — so this exercises the real failure path: the counter store is
    down and the authentication endpoint refuses rather than opening a
    brute-force window (docs/SECURITY.md §13, mandatory rule 6). Note the
    status is 429 rather than 503 — the client is told to retry, not that an
    internal dependency is down.
    """
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    # docs/API_CONTRACTS.md §1.2: a 429 must say when to come back.
    assert int(response.headers["Retry-After"]) > 0


def test_rate_limited_response_leaks_no_backend_detail(client: TestClient) -> None:
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    body = response.text.lower()
    for leak in ("redis", "localhost", "127.0.0.1", "6379", "timeout", "connect"):
        assert leak not in body
