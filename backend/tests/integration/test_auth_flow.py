"""Full authentication round trip against real PostgreSQL and real Redis.

Marked `integration` automatically by the collection hook in
``tests/integration/conftest.py``.

The IdP is an in-process ASGI app reached through an httpx ASGI transport, so
the *real* ``oidc_client`` code runs — discovery, JWKS fetch, token exchange,
signature verification — against something that behaves like a provider. What
is deliberately **not** claimed: that this proves a real IdP works. It proves
DCP's client is self-consistent (`OPEN-01`); claim shapes and registration
rules differ per provider.

What needs real infrastructure, and is therefore here rather than in the unit
suites:

* **Redis** — single-use `state` has to be atomic, a session has to actually
  expire, and the per-user index has to let an administrator terminate
  someone else's sessions.
* **PostgreSQL** — just-in-time provisioning has to hit the real unique
  constraint on ``(issuer, subject)``, and the audit rows have to satisfy
  ``ck_audit_events_actor_attribution``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import Engine, text

ISSUER = "https://idp.test.invalid"
CLIENT_ID = "dcp-integration-client"
CLIENT_SECRET = "placeholder-client-secret"
REDIRECT_URL = "http://testserver/api/v1/auth/callback"
KID = "integration-key"

#: Identities are per-test, not shared. A user row cannot be deleted once an
#: audit event references it (``audit_events.actor_id`` is ON DELETE RESTRICT,
#: deliberately — the trail outlives its subject), so a test that disables a
#: user would otherwise leak a DISABLED row into every test after it.
#:
#: The **email** has to be per-test too: ``uq_users_email_active`` is a
#: case-insensitive unique index over active users, so a shared address makes
#: the second test's provisioning fail on a real constraint. That collision is
#: itself covered, by ``test_identity_conflict_is_denied_not_a_500``.


# --- a provider that behaves like one ---------------------------------------


class FakeIdp:
    """Minimal OIDC provider: discovery, JWKS, authorize, token, with PKCE."""

    def __init__(self, key: rsa.RSAPrivateKey, subject: str) -> None:
        self.key = key
        self.subject = subject
        #: Mutable, so a test can simulate the user's address changing at the
        #: IdP while the subject stays the same.
        self.email = f"{subject}@example.invalid"
        self.codes: dict[str, dict[str, str]] = {}
        self.token_requests: list[dict[str, str]] = []

    def _jwk(self) -> dict[str, Any]:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {**public, "kid": KID, "use": "sig", "alg": "RS256"}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """Serve one provider request.

        A plain httpx MockTransport handler rather than an ASGI app: the token
        endpoint needs the raw form body and the exact status codes, and routing
        three paths by hand is shorter than wiring a framework to do it.
        """
        path = urlsplit(str(request.url)).path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [self._jwk()]})
        if path == "/token":
            return self._token(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return httpx.Response(401, json={"error": "invalid_client"})
        pending = self.codes.pop(form.get("code", ""), None)
        if pending is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        from app.auth.oidc_client import pkce_challenge

        if pkce_challenge(form.get("code_verifier", "")) != pending["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": self.id_token(nonce=pending["nonce"]),
                "access_token": "an-access-token-dcp-should-drop",
            },
        )

    def authorize(self, url: str) -> str:
        """Act as the browser at the IdP: issue a code, return the callback URL."""
        query = parse_qs(urlsplit(url).query)
        code = f"code-{uuid.uuid4()}"
        self.codes[code] = {
            "challenge": query["code_challenge"][0],
            "nonce": query["nonce"][0],
        }
        return f"{query['redirect_uri'][0]}?code={code}&state={query['state'][0]}"

    def id_token(self, *, nonce: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": ISSUER,
                "sub": self.subject,
                "aud": CLIENT_ID,
                "iat": now,
                "exp": now + 300,
                "nonce": nonce,
                "email": self.email,
                "email_verified": True,
                "name": "Integration User",
            },
            self.key,
            algorithm="RS256",
            headers={"kid": KID},
        )


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(scope="session")
def idp_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def subject() -> str:
    return f"integration-subject-{uuid.uuid4()}"


@pytest.fixture
def idp(idp_key: rsa.RSAPrivateKey, subject: str) -> FakeIdp:
    return FakeIdp(idp_key, subject)


@pytest.fixture
def redis_url() -> str:
    """Redis for these tests, from the environment.

    Deliberately not DCP_-prefixed, matching TEST_DATABASE_URL and for the same
    reason (tests/integration/conftest.py). Unset, these tests skip with a
    reason rather than silently reporting success.
    """
    import os

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set; these tests need a real Redis")
    return url


@pytest_asyncio.fixture
async def auth_app(
    migrated: Engine,
    database_url: str,
    redis_url: str,
    idp: FakeIdp,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeIdp]]:
    """The real app, with the real session store, talking to the fake IdP."""
    monkeypatch.setenv("DCP_DATABASE_URL", database_url)
    monkeypatch.setenv("DCP_REDIS_URL", redis_url)
    monkeypatch.setenv("DCP_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("DCP_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DCP_OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("DCP_OIDC_REDIRECT_URL", REDIRECT_URL)

    from app.auth import oidc_client
    from app.core.config import get_settings
    from app.core.redis import dispose_redis, get_redis
    from app.db.session import get_engine, get_session_factory, get_sync_engine

    for cached in (get_settings, get_engine, get_session_factory, get_sync_engine, get_redis):
        cached.cache_clear()
    oidc_client.reset_cache()

    # Point the OIDC client's HTTP calls at the fake provider. The client code
    # under test is unchanged — only the transport is.
    def fake_client(settings: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(idp.handle),
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
        )

    monkeypatch.setattr(oidc_client, "_client", fake_client)

    # Start from a clean Redis namespace rather than flushing: this may be the
    # developer's own Redis, and FLUSHDB would take the Celery broker with it.
    await _clear_namespace()

    from app.main import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, idp

    await _clear_namespace()
    await dispose_redis()
    for cached in (get_settings, get_engine, get_session_factory, get_sync_engine):
        cached.cache_clear()


async def _clear_namespace() -> None:
    from app.core.redis import get_redis

    client = get_redis()
    for pattern in (
        "dcp:session:*",
        "dcp:user-sessions:*",
        "dcp:oidc-login:*",
        "dcp:ratelimit:*",
        # M1.6: the dashboard aggregate cache. Left behind, a 15-second entry
        # outlives the test that wrote it and the next one reads someone
        # else's totals — which is the very failure the cache-key tests exist
        # to detect, arriving as a flake instead.
        "dcp:dashboard:*",
        # M3.3: the alert pipeline's last-seen times and refusal counters.
        "dcp:alerts:*",
    ):
        keys = [key async for key in client.scan_iter(match=pattern, count=500)]
        if keys:
            await client.delete(*keys)


# No row cleanup: audit events are append-only by design (AD-11) and hold an
# ON DELETE RESTRICT reference to the users they attribute, so these rows
# cannot be unwound. Each test therefore owns a unique subject and counts the
# audit rows *it* added rather than assuming an empty table. The schema itself
# is dropped and rebuilt once per session by the ``migrated`` fixture, so
# nothing accumulates beyond a run — and nothing outside ``dcp_test`` is
# touched.


async def _login(client: httpx.AsyncClient, idp: FakeIdp, next_path: str = "/") -> httpx.Response:
    """Drive the whole browser-side flow and return the callback response."""
    start = await client.get(
        "/api/v1/auth/login", params={"next": next_path}, follow_redirects=False
    )
    assert start.status_code == 302, start.text
    callback_url = idp.authorize(start.headers["location"])
    return await client.get(
        urlsplit(callback_url).path + "?" + urlsplit(callback_url).query,
        follow_redirects=False,
    )


def _correlation_of(response: httpx.Response) -> uuid.UUID:
    """The ``correlation_id`` the audit rows for *this* request carry.

    Mirrors ``audit.service.correlation_uuid``: the echoed header is used as
    is when it is already a UUID, and derived through the namespace otherwise.
    The only handle on a row whose actor is a system identity, since those
    rows carry no ``actor_id`` to select on.
    """
    from app.audit.service import _CORRELATION_NAMESPACE

    raw = response.headers["X-Correlation-ID"]
    try:
        return uuid.UUID(raw)
    except ValueError:
        return uuid.uuid5(_CORRELATION_NAMESPACE, raw)


def _audit_rows(
    engine: Engine,
    action_type: str,
    *,
    actor_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Audit rows of one action type, newest first.

    ``occurred_at`` defaults to ``now()``, which is the *transaction*
    timestamp rather than the insert timestamp, so ordering by it does not
    order rows by insertion and ``rows[0]`` is not reliably the row the caller
    just produced (PROGRESS.md issue 51). Pass ``actor_id`` or
    ``correlation_id`` to name that row instead of trusting the ordering;
    ``id`` (uuid7) only breaks an exact tie.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_type, actor_id, actor_label, outcome, target_type,"
                " sanitized_metadata, source_ip, user_agent, correlation_id"
                " FROM audit_events WHERE action_type = :action"
                # Static SQL with optional predicates, rather than a query
                # assembled from fragments: parameterised only (CLAUDE.md §5).
                " AND (CAST(:actor AS uuid) IS NULL OR actor_id = CAST(:actor AS uuid))"
                " AND (CAST(:correlation AS uuid) IS NULL"
                " OR correlation_id = CAST(:correlation AS uuid))"
                " ORDER BY occurred_at DESC, id DESC"
            ),
            {"action": action_type, "actor": actor_id, "correlation": correlation_id},
        ).mappings()
        return [dict(row) for row in rows]


# --- the round trip ----------------------------------------------------------


async def test_full_login_round_trip(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """IMPLEMENTATION_PLAN M1.3 acceptance: log in, then /auth/session is me."""
    client, idp = auth_app
    before = len(_audit_rows(migrated, "LOGIN_SUCCEEDED"))

    callback = await _login(client, idp, "/servers")
    assert callback.status_code == 302
    assert callback.headers["location"] == "/servers"
    assert "dcp_session" in callback.cookies or "dcp_session" in client.cookies

    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    body = session.json()
    assert body["user"]["email"] == idp.email
    assert body["user"]["status"] == "ACTIVE"
    assert body["session"]["idle_timeout_seconds"] == 1800
    # A brand-new user holds no roles: there is no implicit grant and no
    # bootstrap administrator (alembic 0002).
    assert body["roles"] == []
    assert body["permissions"] == []

    assert len(_audit_rows(migrated, "LOGIN_SUCCEEDED")) == before + 1


async def test_no_token_is_readable_by_javascript(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """AD-02: the SPA holds no token at all."""
    client, idp = auth_app
    callback = await _login(client, idp)
    set_cookies = callback.headers.get_list("set-cookie")

    session_cookie = next(c for c in set_cookies if c.startswith("dcp_session="))
    assert "HttpOnly" in session_cookie
    assert "samesite=strict" in session_cookie.lower()

    # The session value is opaque: not a JWT, and nothing decodable.
    value = client.cookies["dcp_session"]
    assert value.count(".") != 2
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(value, options={"verify_signature": False})

    # And no response in the flow carries the ID token.
    id_token = next(
        request for request in idp.token_requests if request
    )  # the exchange happened server-side
    assert id_token is not None
    assert "id_token" not in callback.text
    body = (await client.get("/api/v1/auth/session")).text
    for forbidden in ("id_token", "access_token", "eyJ", client.cookies["dcp_session"]):
        assert forbidden not in body


async def test_access_token_is_not_retained_anywhere(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """DCP calls no provider API, so holding an access token has no purpose."""
    client, idp = auth_app
    await _login(client, idp)
    from app.core.redis import get_redis

    redis = get_redis()
    async for key in redis.scan_iter(match="dcp:*", count=500):
        kind = await redis.type(key)
        if kind == "string":
            stored = await redis.get(key)
            assert "an-access-token-dcp-should-drop" not in (stored or "")


# --- state and replay --------------------------------------------------------


async def test_replayed_state_is_rejected(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """Single-use, enforced by an atomic GETDEL."""
    client, idp = auth_app
    start = await client.get("/api/v1/auth/login", follow_redirects=False)
    callback_url = idp.authorize(start.headers["location"])
    query = urlsplit(callback_url).query

    first = await client.get(f"/api/v1/auth/callback?{query}", follow_redirects=False)
    assert first.status_code == 302

    second = await client.get(f"/api/v1/auth/callback?{query}", follow_redirects=False)
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "bad_request"


async def test_unknown_state_is_rejected(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, _ = auth_app
    response = await client.get(
        "/api/v1/auth/callback",
        params={"code": "some-code", "state": "a-state-we-never-issued"},
        follow_redirects=False,
    )
    assert response.status_code == 400


async def test_missing_state_or_code_is_rejected(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, _ = auth_app
    for params in ({"code": "c"}, {"state": "s"}, {}):
        response = await client.get("/api/v1/auth/callback", params=params, follow_redirects=False)
        assert response.status_code == 400


async def test_pkce_verifier_mismatch_is_rejected(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """The IdP rejects the exchange; DCP must surface that as a 401, not a 500."""
    client, idp = auth_app
    start = await client.get("/api/v1/auth/login", follow_redirects=False)
    callback_url = idp.authorize(start.headers["location"])
    code = parse_qs(urlsplit(callback_url).query)["code"][0]
    # Corrupt the stored challenge so the verifier DCP holds cannot match.
    idp.codes[code]["challenge"] = "a-challenge-that-will-not-match"

    response = await client.get(
        f"/api/v1/auth/callback?{urlsplit(callback_url).query}", follow_redirects=False
    )
    assert response.status_code == 401


async def test_nonce_mismatch_is_rejected(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    start = await client.get("/api/v1/auth/login", follow_redirects=False)
    callback_url = idp.authorize(start.headers["location"])
    code = parse_qs(urlsplit(callback_url).query)["code"][0]
    idp.codes[code]["nonce"] = "a-nonce-dcp-did-not-send"

    response = await client.get(
        f"/api/v1/auth/callback?{urlsplit(callback_url).query}", follow_redirects=False
    )
    assert response.status_code == 401


async def test_login_state_is_not_reusable_across_browsers(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], idp_key: rsa.RSAPrivateKey
) -> None:
    """A state consumed by one client cannot then be used by another."""
    client, idp = auth_app
    start = await client.get("/api/v1/auth/login", follow_redirects=False)
    callback_url = idp.authorize(start.headers["location"])
    query = urlsplit(callback_url).query
    await client.get(f"/api/v1/auth/callback?{query}", follow_redirects=False)

    async with httpx.AsyncClient(
        transport=client._transport, base_url="http://testserver"
    ) as other:
        response = await other.get(f"/api/v1/auth/callback?{query}", follow_redirects=False)
    assert response.status_code == 400


# --- just-in-time provisioning ----------------------------------------------


async def test_provisioning_creates_exactly_one_user(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """Two logins reuse the row; the subject is the key, not the email."""
    client, idp = auth_app
    await _login(client, idp)
    await _login(client, idp)

    with migrated.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM users WHERE identity_provider_subject = :subject"),
            {"subject": idp.subject},
        ).scalar()
    assert count == 1


async def test_email_change_at_the_idp_does_not_create_a_second_user(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """docs/TESTING.md §9: subject is the identity key.

    An address can be reassigned to a different person, so keying on it would
    hand one person another's history.
    """
    client, idp = auth_app
    await _login(client, idp)

    original_id = _user_id(migrated, idp.subject)
    idp.email = "renamed@example.invalid"
    await _login(client, idp)

    with migrated.connect() as connection:
        rows = connection.execute(
            text("SELECT id, email FROM users WHERE identity_provider_subject = :subject"),
            {"subject": idp.subject},
        ).all()
    assert len(rows) == 1
    assert rows[0].id == original_id
    assert rows[0].email == "renamed@example.invalid"


async def test_a_new_user_holds_no_roles(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """No implicit grant, no bootstrap administrator."""
    client, idp = auth_app
    await _login(client, idp)
    body = (await client.get("/api/v1/auth/session")).json()
    assert body["roles"] == []
    assert body["permissions"] == []


async def test_disabled_user_cannot_log_in(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """403 plus an audited LOGIN_DENIED (docs/API_CONTRACTS.md §12.2)."""
    client, idp = auth_app
    await _login(client, idp)
    user_id = _user_id(migrated, idp.subject)
    with migrated.begin() as connection:
        connection.execute(
            text("UPDATE users SET status = 'DISABLED' WHERE id = :id"), {"id": user_id}
        )

    response = await _login(client, idp)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"

    denied = _audit_rows(migrated, "LOGIN_DENIED", correlation_id=_correlation_of(response))
    assert len(denied) == 1
    # Mandatory rule 10: attributed to the real user, not to "unknown".
    assert denied[0]["actor_id"] == user_id
    assert denied[0]["outcome"] == "DENIED"


async def test_disabling_a_user_terminates_their_sessions_immediately(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """docs/SECURITY.md §5. No re-login, no restart, next request fails."""
    client, idp = auth_app
    await _login(client, idp)
    assert (await client.get("/api/v1/auth/session")).status_code == 200

    with migrated.begin() as connection:
        connection.execute(
            text("UPDATE users SET status = 'DISABLED' WHERE identity_provider_subject = :s"),
            {"s": idp.subject},
        )

    assert (await client.get("/api/v1/auth/session")).status_code == 401
    # And the session is gone, not merely refused once.
    from app.auth import session as session_store

    assert await session_store.load(client.cookies["dcp_session"]) is None


def _user_id(engine: Engine, subject: str) -> uuid.UUID:
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT id FROM users WHERE identity_provider_subject = :s"),
            {"s": subject},
        ).scalar_one()
    return value  # type: ignore[no-any-return]


# --- session lifecycle -------------------------------------------------------


async def test_logout_makes_the_session_immediately_unusable(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """IMPLEMENTATION_PLAN M1.3 acceptance criterion."""
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]
    before = len(_audit_rows(migrated, "LOGOUT"))

    response = await client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": client.cookies["dcp_csrf"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"logged_out": True}

    from app.auth import session as session_store

    assert await session_store.load(session_id) is None
    assert len(_audit_rows(migrated, "LOGOUT")) == before + 1


async def test_post_logout_request_is_401(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    csrf = client.cookies["dcp_csrf"]
    await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    # The cookie is cleared, but a stolen copy must not work either.
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_logout_without_a_csrf_token_is_403(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """A real session is not enough: the double-submit token is required."""
    client, idp = auth_app
    await _login(client, idp)
    response = await client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"})
    assert response.status_code == 403
    # And the session survived the rejected request.
    assert (await client.get("/api/v1/auth/session")).status_code == 200


async def test_logout_with_a_mismatched_csrf_token_is_403(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    response = await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": "not-the-token"},
    )
    assert response.status_code == 403


async def test_idle_timeout_is_enforced_server_side(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """The client clock is never consulted.

    The stored record's idle deadline is moved into the past directly, which is
    the state a genuinely idle session reaches — rather than sleeping for 30
    minutes.
    """
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    from datetime import UTC, datetime, timedelta

    from app.auth import session as session_store
    from app.core.redis import get_redis

    key = session_store._key(session_id)
    record = session_store.SessionRecord.from_json(await get_redis().get(key))
    stale = session_store.SessionRecord(
        user_id=record.user_id,
        issued_at=record.issued_at,
        last_seen_at=record.last_seen_at,
        absolute_deadline=record.absolute_deadline,
        idle_deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    await get_redis().set(key, stale.to_json(), ex=3600)

    assert (await client.get("/api/v1/auth/session")).status_code == 401
    # Expired means destroyed, not merely rejected.
    assert await get_redis().get(key) is None


async def test_absolute_timeout_is_enforced_despite_activity(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    from datetime import UTC, datetime, timedelta

    from app.auth import session as session_store
    from app.core.redis import get_redis

    key = session_store._key(session_id)
    record = session_store.SessionRecord.from_json(await get_redis().get(key))
    expired = session_store.SessionRecord(
        user_id=record.user_id,
        issued_at=record.issued_at,
        last_seen_at=datetime.now(UTC),
        absolute_deadline=datetime.now(UTC) - timedelta(seconds=1),
        # Idle deadline far in the future: only the absolute one has passed.
        idle_deadline=datetime.now(UTC) + timedelta(hours=1),
    )
    await get_redis().set(key, expired.to_json(), ex=3600)

    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_idle_deadline_slides_on_use_but_never_past_the_absolute_one(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    from datetime import UTC, datetime, timedelta

    from app.auth import session as session_store
    from app.core.redis import get_redis

    key = session_store._key(session_id)
    record = session_store.SessionRecord.from_json(await get_redis().get(key))
    # Absolute deadline a minute away; the idle slide must not exceed it.
    near = session_store.SessionRecord(
        user_id=record.user_id,
        issued_at=record.issued_at,
        last_seen_at=record.last_seen_at,
        absolute_deadline=datetime.now(UTC) + timedelta(minutes=1),
        idle_deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    await get_redis().set(key, near.to_json(), ex=3600)

    refreshed = await session_store.load(session_id)
    assert refreshed is not None
    assert refreshed.idle_deadline == near.absolute_deadline


async def test_session_id_rotates_on_every_login(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """docs/SECURITY.md §5: rotated on a privilege-relevant change."""
    client, idp = auth_app
    await _login(client, idp)
    first = client.cookies["dcp_session"]
    await _login(client, idp)
    second = client.cookies["dcp_session"]
    assert first != second


async def test_rotate_keeps_the_user_and_retires_the_old_identifier(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """The primitive M1.4 calls on a role change."""
    client, idp = auth_app
    await _login(client, idp)
    old = client.cookies["dcp_session"]

    from app.auth import session as session_store

    rotated = await session_store.rotate(old)
    assert rotated is not None
    new_id, record = rotated
    assert new_id != old
    assert await session_store.load(old) is None
    loaded = await session_store.load(new_id)
    assert loaded is not None
    assert loaded.user_id == record.user_id


async def test_rotating_a_dead_session_does_not_revive_it(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    from app.auth import session as session_store

    await session_store.delete(session_id)
    assert await session_store.rotate(session_id) is None


async def test_terminate_user_sessions_kills_every_session(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """An administrator terminating another user's sessions (§5).

    Three concurrent sessions for one user, all gone in one call.
    """
    client, idp = auth_app
    await _login(client, idp)
    user_id = _user_id(migrated, idp.subject)

    from app.auth import session as session_store

    extra = [await session_store.create(user_id) for _ in range(2)]
    ids = [client.cookies["dcp_session"], *(session_id for session_id, _ in extra)]
    for session_id in ids:
        assert await session_store.load(session_id) is not None

    removed = await session_store.terminate_user_sessions(user_id)
    assert removed == 3
    for session_id in ids:
        assert await session_store.load(session_id) is None
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_deleting_a_session_is_idempotent(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    from app.auth import session as session_store

    assert await session_store.delete(session_id) is True
    assert await session_store.delete(session_id) is False
    assert await session_store.delete("never-existed") is False


async def test_a_tampered_session_record_is_discarded(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """A record this process cannot read is not a record it may trust."""
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    from app.auth import session as session_store
    from app.core.redis import get_redis

    await get_redis().set(session_store._key(session_id), "{not json", ex=3600)
    assert await session_store.load(session_id) is None
    assert (await client.get("/api/v1/auth/session")).status_code == 401


# --- permissions are read fresh, never cached -------------------------------


async def test_a_granted_role_appears_without_re_login(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """PRD §4.1: resolved per request from PostgreSQL, so a grant is immediate."""
    client, idp = auth_app
    await _login(client, idp)
    user_id = _user_id(migrated, idp.subject)
    assert (await client.get("/api/v1/auth/session")).json()["roles"] == []

    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_definition,"
                " granted_by, created_at, updated_at)"
                " SELECT gen_random_uuid(), :user_id, id,"
                ' \'{"environment": ["staging"]}\'::jsonb, :user_id, now(), now()'
                " FROM roles WHERE name = 'VIEWER'"
            ),
            {"user_id": user_id},
        )

    body = (await client.get("/api/v1/auth/session")).json()
    assert [role["name"] for role in body["roles"]] == ["VIEWER"]
    assert body["roles"][0]["scope"] == {"environment": ["staging"]}
    assert "server:read" in body["permissions"]

    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": user_id})


async def test_a_revoked_role_disappears_on_the_next_request(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    user_id = _user_id(migrated, idp.subject)

    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_definition,"
                " granted_by, created_at, updated_at)"
                " SELECT gen_random_uuid(), :user_id, id, '{}'::jsonb, :user_id, now(), now()"
                " FROM roles WHERE name = 'VIEWER'"
            ),
            {"user_id": user_id},
        )
    assert (await client.get("/api/v1/auth/session")).json()["permissions"] != []

    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": user_id})

    # No re-login, no restart.
    assert (await client.get("/api/v1/auth/session")).json()["permissions"] == []


async def test_an_expired_grant_is_not_counted(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    user_id = _user_id(migrated, idp.subject)

    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_definition,"
                " granted_by, expires_at, created_at, updated_at)"
                " SELECT gen_random_uuid(), :user_id, id, '{}'::jsonb, :user_id,"
                " now() - interval '1 hour', now(), now()"
                " FROM roles WHERE name = 'ADMINISTRATOR'"
            ),
            {"user_id": user_id},
        )

    body = (await client.get("/api/v1/auth/session")).json()
    assert body["roles"] == []
    assert body["permissions"] == []

    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": user_id})


# --- audit ------------------------------------------------------------------


async def test_auth_events_carry_no_token_material(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """PRD §4.1 / docs/SECURITY.md §5: no credential, no session id, no nonce."""
    client, idp = auth_app
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]

    with migrated.connect() as connection:
        blob = str(
            connection.execute(
                text(
                    "SELECT string_agg(sanitized_metadata::text || ' ' ||"
                    " coalesce(actor_label, '') || ' ' || action_type, ' ')"
                    " FROM audit_events"
                )
            ).scalar()
            or ""
        )
    for forbidden in (session_id, CLIENT_SECRET, "eyJ", "an-access-token", "code-", "nonce"):
        assert forbidden not in blob


async def test_a_failed_login_is_audited(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """The audit row must survive the failed request's rollback."""
    client, _ = auth_app
    response = await client.get(
        "/api/v1/auth/callback",
        params={"code": "x", "state": "never-issued"},
        follow_redirects=False,
    )
    assert response.status_code == 400

    rows = _audit_rows(migrated, "LOGIN_FAILED", correlation_id=_correlation_of(response))
    assert len(rows) == 1
    # No user to attribute to, so a named system actor owns it, and the DB
    # CHECK requires actor_id to be NULL in that case.
    assert rows[0]["actor_type"] == "SYSTEM"
    assert rows[0]["actor_id"] is None
    assert rows[0]["actor_label"] == "system:auth"
    assert rows[0]["sanitized_metadata"]["reason"] == "invalid_or_replayed_state"


async def test_audit_rows_are_correlated_with_the_request(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    client, idp = auth_app
    correlation = "abcdef0123456789"
    start = await client.get(
        "/api/v1/auth/login",
        follow_redirects=False,
        headers={"X-Correlation-ID": correlation},
    )
    callback_url = idp.authorize(start.headers["location"])
    response = await client.get(
        f"/api/v1/auth/callback?{urlsplit(callback_url).query}",
        follow_redirects=False,
        headers={"X-Correlation-ID": correlation},
    )
    assert response.status_code == 302
    assert response.headers["X-Correlation-ID"] == correlation

    from app.audit.service import _CORRELATION_NAMESPACE

    expected = uuid.uuid5(_CORRELATION_NAMESPACE, correlation)
    rows = _audit_rows(migrated, "LOGIN_SUCCEEDED", actor_id=_user_id(migrated, idp.subject))
    assert len(rows) == 1
    assert rows[0]["correlation_id"] == expected


async def test_login_audit_records_the_client_address_and_agent(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    client, idp = auth_app
    await _login(client, idp)
    rows = _audit_rows(migrated, "LOGIN_SUCCEEDED", actor_id=_user_id(migrated, idp.subject))
    assert len(rows) == 1
    assert rows[0]["user_agent"] is not None


async def test_audit_events_remain_append_only(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """AD-11, re-asserted on the rows M1.3 writes."""
    client, idp = auth_app
    await _login(client, idp)
    import sqlalchemy.exc

    for statement in (
        "UPDATE audit_events SET outcome = 'SUCCESS' WHERE action_type = 'LOGIN_SUCCEEDED'",
        "DELETE FROM audit_events WHERE action_type = 'LOGIN_SUCCEEDED'",
    ):
        with pytest.raises(sqlalchemy.exc.DatabaseError), migrated.begin() as connection:
            connection.execute(text(statement))


# --- rate limiting -----------------------------------------------------------


async def test_auth_rate_limit_is_enforced(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    """docs/SECURITY.md §13: 10 per 5 minutes per IP on /auth/*."""
    client, _ = auth_app
    from app.core.config import get_settings

    limit = get_settings().auth_rate_limit_max
    statuses = [
        (await client.get("/api/v1/auth/login", follow_redirects=False)).status_code
        for _ in range(limit + 3)
    ]
    assert statuses[:limit] == [302] * limit
    assert statuses[limit:] == [429] * 3


async def test_rate_limited_response_carries_retry_after(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],
) -> None:
    client, _ = auth_app
    from app.core.config import get_settings

    limit = get_settings().auth_rate_limit_max
    for _ in range(limit):
        await client.get("/api/v1/auth/login", follow_redirects=False)
    response = await client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 429
    assert 0 < int(response.headers["Retry-After"]) <= 300


# --- no session identifier in any log line ----------------------------------


async def test_no_session_identifier_in_any_log_line(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], capsys: pytest.CaptureFixture[str]
) -> None:
    """Canary sweep over the logs produced by a whole login/logout cycle."""
    client, idp = auth_app
    capsys.readouterr()
    await _login(client, idp)
    session_id = client.cookies["dcp_session"]
    csrf = client.cookies["dcp_csrf"]
    await client.get("/api/v1/auth/session")
    await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    output = capsys.readouterr().out
    assert session_id not in output
    assert csrf not in output
    assert CLIENT_SECRET not in output
    assert "eyJ" not in output


async def test_identity_conflict_is_denied_not_a_500(
    auth_app: tuple[httpx.AsyncClient, FakeIdp], migrated: Engine
) -> None:
    """A second subject presenting an active user's address is refused cleanly.

    ``uq_users_email_active`` is real, so this path is reachable in production
    (a re-provisioned directory account, two providers). The default chosen for
    it is *deny*, not merge — see ``service.IdentityConflictError``. What must
    not happen is an unhandled 500 on an authentication path.
    """
    client, idp = auth_app
    await _login(client, idp)
    taken = idp.email

    # A different identity, same address.
    other = FakeIdp(idp.key, f"integration-subject-{uuid.uuid4()}")
    other.email = taken
    async with httpx.AsyncClient(
        transport=client._transport, base_url="http://testserver"
    ) as second_browser:
        from app.auth import oidc_client

        original = oidc_client._client
        try:
            oidc_client._client = lambda settings: httpx.AsyncClient(  # type: ignore[assignment]
                transport=httpx.MockTransport(other.handle),
                timeout=httpx.Timeout(5.0),
                follow_redirects=False,
            )
            response = await _login(second_browser, other)
        finally:
            oidc_client._client = original  # type: ignore[assignment]

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    # A 409 would confirm the address is registered; the message does not
    # mention the address at all.
    assert taken not in response.text

    denied = _audit_rows(migrated, "LOGIN_DENIED", correlation_id=_correlation_of(response))
    assert len(denied) == 1
    assert denied[0]["sanitized_metadata"]["reason"] == "identity_conflict"
    # No second user row was created.
    with migrated.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM users WHERE lower(email) = lower(:e)"),
            {"e": taken},
        ).scalar()
    assert count == 1
