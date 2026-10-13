"""Fixtures for the authentication suites.

The ID-token rejection matrix needs tokens a *correct* IdP would never issue —
wrong issuer, wrong audience, `alg: none`, an HMAC token signed with the public
key. So the tokens here are minted in-process from a test keypair rather than
obtained from the mock IdP container, which by design only emits valid ones.

No network: the JWKS and discovery caches are populated directly, so these are
unit tests under the rules in docs/TESTING.md §16.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import oidc_client

ISSUER = "https://idp.example.invalid"
CLIENT_ID = "dcp-test-client"
KID = "test-key-1"
OTHER_KID = "test-key-2"

OIDC_ENV = {
    "DCP_OIDC_ISSUER": ISSUER,
    "DCP_OIDC_CLIENT_ID": CLIENT_ID,
    "DCP_OIDC_CLIENT_SECRET": "placeholder-client-secret",
    "DCP_OIDC_REDIRECT_URL": "https://dcp.example.invalid/api/v1/auth/callback",
}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _segment(payload: dict[str, Any]) -> str:
    return _b64u(json.dumps(payload, separators=(",", ":")).encode())


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**public, "kid": kid, "use": "sig", "alg": "RS256"}


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    # 2048 bits: key generation is the slowest thing in this suite, and the
    # tests verify the plumbing, not the key size.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def rotated_key() -> rsa.RSAPrivateKey:
    """A second key, published only after a JWKS refresh (rotation test)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configure an IdP, and reset the provider cache around every test."""
    for key, value in OIDC_ENV.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    oidc_client.reset_cache()
    yield
    oidc_client.reset_cache()
    get_settings.cache_clear()


@pytest.fixture
def primed_cache(
    oidc_settings: None,
    signing_key: rsa.RSAPrivateKey,
) -> oidc_client.ProviderMetadata:
    """Fill the discovery and JWKS caches so no HTTP call is attempted."""
    metadata = oidc_client.ProviderMetadata(
        issuer=ISSUER,
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
        end_session_endpoint=None,
    )
    oidc_client._cache.metadata = metadata
    oidc_client._cache.metadata_fetched_at = time.monotonic()
    oidc_client._cache.jwks = {"keys": [_jwk(signing_key, KID)]}
    oidc_client._cache.jwks_fetched_at = time.monotonic()
    return metadata


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token(signing_key: rsa.RSAPrivateKey) -> TokenFactory:
    """Mint an ID token, valid by default and broken on request."""

    def factory(
        *,
        key: rsa.RSAPrivateKey | None = None,
        kid: str | None = KID,
        algorithm: str = "RS256",
        issuer: str = ISSUER,
        audience: str | None = CLIENT_ID,
        subject: str = "subject-123",
        nonce: str | None = "test-nonce",
        exp_offset: int = 300,
        iat_offset: int = 0,
        extra: dict[str, Any] | None = None,
        omit: tuple[str, ...] = (),
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": issuer,
            "sub": subject,
            "iat": now + iat_offset,
            "exp": now + exp_offset,
            "email": "operator@example.invalid",
            "email_verified": True,
            "name": "Example Operator",
        }
        if audience is not None:
            claims["aud"] = audience
        if nonce is not None:
            claims["nonce"] = nonce
        claims.update(extra or {})
        for name in omit:
            claims.pop(name, None)
        headers = {"kid": kid} if kid is not None else {}
        if algorithm == "none":
            # PyJWT refuses to *encode* alg=none without an explicit opt-in, so
            # the token is assembled by hand. This is the attack being tested:
            # a verifier that honours the token's own `alg` accepts it.
            return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(claims)}."
        if algorithm.startswith("HS"):
            # Key confusion, assembled by hand because PyJWT refuses to *encode*
            # an HMAC token with an asymmetric key — a good defence on its side,
            # but it means the forgery has to be built directly to be tested at
            # all. The secret is the IdP's public key, which the verifier also
            # holds, so a verifier that takes `alg` from the token accepts this.
            import hmac

            from cryptography.hazmat.primitives import serialization

            secret = (
                (key or signing_key)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            signing_input = (
                f"{_segment({'alg': algorithm, 'typ': 'JWT', **headers})}.{_segment(claims)}"
            ).encode()
            mac = hmac.new(secret, signing_input, "sha256").digest()
            return f"{signing_input.decode()}.{_b64u(mac)}"
        signing: Any = key if key is not None else signing_key
        return jwt.encode(claims, signing, algorithm=algorithm, headers=headers)

    return factory


@pytest.fixture
def no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the rate limiter out of the way.

    Not autouse: the fail-closed behaviour is itself a control under test
    (``test_rate_limit_fails_closed_when_redis_is_unreachable``), and a global
    stub would hide it.
    """
    from app.core import ratelimit

    async def allow(bucket: str, identity: str, **kwargs: object) -> ratelimit.Verdict:
        return ratelimit.Verdict(allowed=True, remaining=99, reset_seconds=60)

    monkeypatch.setattr(ratelimit, "check", allow)
