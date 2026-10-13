"""ID-token verification matrix (docs/TESTING.md §9, IMPLEMENTATION_PLAN M1.3).

Every case here is a token that must be refused. The interesting ones are not
"expired" — they are the two forgeries that work against a verifier which
trusts the token's own header:

* ``alg: none`` — an unsigned token.
* ``HS256`` signed with the IdP's **public** key, which the verifier also holds.

Both are closed by taking the algorithm list from our own allowlist and never
from the token.
"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import oidc_client
from app.auth.oidc_client import OidcError

from .conftest import CLIENT_ID, ISSUER, KID, OTHER_KID, TokenFactory, _jwk

pytestmark = pytest.mark.security

NONCE = "test-nonce"


async def test_accepts_a_valid_token(primed_cache: object, make_token: TokenFactory) -> None:
    claims = await oidc_client.verify_id_token(make_token(), expected_nonce=NONCE)
    assert claims.subject == "subject-123"
    assert claims.issuer == ISSUER
    assert claims.email == "operator@example.invalid"
    assert claims.email_verified is True


async def test_rejects_a_bad_signature(
    primed_cache: object, make_token: TokenFactory, rotated_key: rsa.RSAPrivateKey
) -> None:
    # Right kid, wrong key: the JWKS entry for KID will not verify it.
    token = make_token(key=rotated_key, kid=KID)
    with pytest.raises(OidcError):
        await oidc_client.verify_id_token(token, expected_nonce=NONCE)


async def test_rejects_a_tampered_payload(primed_cache: object, make_token: TokenFactory) -> None:
    header, _payload, signature = make_token().split(".")
    other = make_token(subject="attacker").split(".")[1]
    with pytest.raises(OidcError):
        await oidc_client.verify_id_token(f"{header}.{other}.{signature}", expected_nonce=NONCE)


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"issuer": "https://evil.example.invalid"}, "wrong iss"),
        ({"audience": "some-other-client"}, "wrong aud"),
        ({"audience": None}, "missing aud"),
        ({"exp_offset": -3600}, "expired"),
        ({"iat_offset": 3600}, "iat too far in the future"),
        ({"nonce": "a-different-nonce"}, "wrong nonce"),
        ({"nonce": None}, "missing nonce"),
        ({"omit": ("sub",)}, "missing sub"),
        ({"omit": ("iat",)}, "missing iat"),
        ({"omit": ("exp",)}, "missing exp"),
        ({"algorithm": "none"}, "unsigned"),
        ({"algorithm": "HS256"}, "key confusion"),
        ({"kid": "no-such-key"}, "unknown kid"),
    ],
)
async def test_rejection_matrix(
    primed_cache: object,
    make_token: TokenFactory,
    kwargs: dict[str, object],
    why: str,
) -> None:
    token = make_token(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(OidcError):
        await oidc_client.verify_id_token(token, expected_nonce=NONCE)


async def test_clock_skew_is_tolerated_but_bounded(
    primed_cache: object, make_token: TokenFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token expired by less than the configured skew is still accepted.

    And one expired by more is not — the leeway is a tolerance for clock drift,
    not a grace period that revives dead tokens.
    """
    from app.core.config import get_settings

    skew = get_settings().oidc_clock_skew_seconds
    just_expired = make_token(exp_offset=-(skew // 2))
    claims = await oidc_client.verify_id_token(just_expired, expected_nonce=NONCE)
    assert claims.subject == "subject-123"

    long_expired = make_token(exp_offset=-(skew * 10))
    with pytest.raises(OidcError):
        await oidc_client.verify_id_token(long_expired, expected_nonce=NONCE)


async def test_nonce_comparison_rejects_a_prefix(
    primed_cache: object, make_token: TokenFactory
) -> None:
    """A nonce must match in full, not merely start the same way."""
    token = make_token(nonce=NONCE)
    with pytest.raises(OidcError):
        await oidc_client.verify_id_token(token, expected_nonce=NONCE[:4])


async def test_unparseable_token_is_rejected_not_raised_raw(primed_cache: object) -> None:
    for garbage in ("", "not-a-jwt", "a.b", "a.b.c.d"):
        with pytest.raises(OidcError):
            await oidc_client.verify_id_token(garbage, expected_nonce=NONCE)


async def test_error_message_does_not_name_the_failed_check(
    primed_cache: object, make_token: TokenFactory
) -> None:
    """One message for every failure (docs/API_CONTRACTS.md §3).

    "wrong audience" and "bad signature" are different hints to hand an
    attacker, so the client is told neither.
    """
    messages = set()
    for kwargs in ({"issuer": "https://evil.invalid"}, {"exp_offset": -3600}, {"nonce": "x"}):
        try:
            await oidc_client.verify_id_token(
                make_token(**kwargs),  # type: ignore[arg-type]
                expected_nonce=NONCE,
            )
        except OidcError as error:
            messages.add(error.message)
    assert messages == {"Authentication failed."}


# --- JWKS rotation ----------------------------------------------------------


async def test_rotated_key_is_accepted_after_a_refresh(
    primed_cache: object,
    make_token: TokenFactory,
    signing_key: rsa.RSAPrivateKey,
    rotated_key: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token signed with a newly published key is accepted (docs/TESTING.md §9)."""
    refreshes = 0

    async def fake_get_json(url: str, settings: object) -> dict[str, object]:
        nonlocal refreshes
        refreshes += 1
        return {"keys": [_jwk(signing_key, KID), _jwk(rotated_key, OTHER_KID)]}

    monkeypatch.setattr(oidc_client, "_get_json", fake_get_json)
    # The cache was primed without the rotated key, and far enough in the past
    # that the minimum refresh interval has elapsed.
    oidc_client._cache.jwks_fetched_at = time.monotonic() - 3600

    token = make_token(key=rotated_key, kid=OTHER_KID)
    claims = await oidc_client.verify_id_token(token, expected_nonce=NONCE)
    assert claims.subject == "subject-123"
    assert refreshes == 1


async def test_unknown_kid_does_not_refetch_unboundedly(
    primed_cache: object,
    make_token: TokenFactory,
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """Forged key ids must not become an amplifier against the IdP.

    The first unknown `kid` is allowed to trigger one refresh; the next
    ten, arriving inside the minimum interval, trigger none.
    """
    refreshes = 0

    async def fake_get_json(url: str, settings: object) -> dict[str, object]:
        nonlocal refreshes
        refreshes += 1
        return {"keys": [_jwk(signing_key, KID)]}

    monkeypatch.setattr(oidc_client, "_get_json", fake_get_json)
    oidc_client._cache.jwks_fetched_at = time.monotonic() - 3600

    for index in range(10):
        with pytest.raises(OidcError):
            await oidc_client.verify_id_token(
                make_token(kid=f"forged-{index}"), expected_nonce=NONCE
            )
    assert refreshes == 1


async def test_encryption_only_key_is_not_used_for_verification(
    primed_cache: object, make_token: TokenFactory, signing_key: rsa.RSAPrivateKey
) -> None:
    """A `use: enc` JWK must never verify a signature."""
    entry = {**_jwk(signing_key, KID), "use": "enc"}
    oidc_client._cache.jwks = {"keys": [entry]}
    with pytest.raises(OidcError):
        await oidc_client.verify_id_token(make_token(), expected_nonce=NONCE)


async def test_malformed_jwk_entry_is_skipped_not_fatal(
    primed_cache: object, make_token: TokenFactory, signing_key: rsa.RSAPrivateKey
) -> None:
    oidc_client._cache.jwks = {"keys": ["not-an-object", {"kty": "RSA"}, _jwk(signing_key, KID)]}
    claims = await oidc_client.verify_id_token(make_token(), expected_nonce=NONCE)
    assert claims.subject == "subject-123"


# --- claim handling ---------------------------------------------------------


async def test_missing_email_does_not_fabricate_a_plausible_address(
    primed_cache: object, make_token: TokenFactory
) -> None:
    """A substituted address must be obviously unusable, not plausible.

    `.invalid` can never resolve (RFC 2606), so a notification sent to it
    fails loudly instead of reaching a stranger.
    """
    claims = await oidc_client.verify_id_token(
        make_token(omit=("email", "name")), expected_nonce=NONCE
    )
    assert claims.email.endswith("@users.noreply.invalid")
    assert claims.email_verified is False


async def test_oversized_claims_are_truncated_to_the_column_widths(
    primed_cache: object, make_token: TokenFactory
) -> None:
    token = make_token(extra={"email": "a" * 1000 + "@example.invalid", "name": "n" * 1000})
    claims = await oidc_client.verify_id_token(token, expected_nonce=NONCE)
    assert len(claims.email) == 320
    assert len(claims.display_name) == 256


async def test_roles_in_claims_are_ignored(primed_cache: object, make_token: TokenFactory) -> None:
    """Authorisation never comes from the IdP (OPEN-01, AD-03).

    `IdentityClaims` has no field for a role, so a provider that asserts
    `groups: ["ADMINISTRATOR"]` cannot grant anything.
    """
    token = make_token(extra={"groups": ["ADMINISTRATOR"], "roles": ["admin"]})
    claims = await oidc_client.verify_id_token(token, expected_nonce=NONCE)
    assert not any("role" in field or "group" in field for field in claims.__slots__)


# --- the algorithm allowlist itself ----------------------------------------


def test_allowlist_contains_no_symmetric_or_none_algorithm() -> None:
    assert "none" not in oidc_client.ALLOWED_ALGORITHMS
    assert not any(alg.startswith("HS") for alg in oidc_client.ALLOWED_ALGORITHMS)


def test_jwt_decode_is_never_called_with_verify_disabled() -> None:
    """A grep gate: `verify_signature: False` must not appear in app code."""
    from pathlib import Path

    source = Path(oidc_client.__file__).read_text(encoding="utf-8")
    assert '"verify_signature": True' in source
    assert "verify_signature': False" not in source
    assert '"verify_signature": False' not in source


# --- discovery-document validation -----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.invalid/token",  # different host
        "http://idp.example.invalid/token",  # different scheme
        "https://idp.example.invalid:8443/token",  # different port
        "https://idp.example.invalid.evil.test/token",  # suffix trick
        "file:///etc/passwd",
        "not-a-url",
        "",
    ],
)
def test_off_origin_endpoint_is_rejected(url: str) -> None:
    """A discovery document cannot point the token exchange elsewhere.

    The token request carries the client secret, so an endpoint URL that leaves
    the issuer's origin is an exfiltration primitive (mandatory rule 9).
    """
    assert oidc_client._same_origin(url, ISSUER) is False


@pytest.mark.parametrize(
    "url",
    [
        f"{ISSUER}/token",
        f"{ISSUER}/oauth2/v2/token",
        f"{ISSUER}/token?x=1",
    ],
)
def test_same_origin_endpoint_is_accepted(url: str) -> None:
    assert oidc_client._same_origin(url, ISSUER) is True


# --- PKCE -------------------------------------------------------------------


def test_pkce_challenge_is_s256_of_the_verifier() -> None:
    import base64
    import hashlib

    verifier = oidc_client.new_pkce_verifier()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode()
        .rstrip("=")
    )
    assert oidc_client.pkce_challenge(verifier) == expected
    # No padding: RFC 7636 base64url has none, and a provider comparing
    # literally would reject a padded value.
    assert "=" not in oidc_client.pkce_challenge(verifier)


def test_pkce_verifier_length_is_within_rfc_7636_bounds() -> None:
    for _ in range(20):
        assert 43 <= len(oidc_client.new_pkce_verifier()) <= 128


def test_pkce_verifiers_are_unique() -> None:
    assert len({oidc_client.new_pkce_verifier() for _ in range(100)}) == 100


def test_state_values_are_unique_and_long_enough() -> None:
    states = {oidc_client.new_state() for _ in range(100)}
    assert len(states) == 100
    # token_urlsafe(32) → 43 characters, i.e. 256 bits.
    assert all(len(state) >= 43 for state in states)


def test_authorization_url_carries_pkce_and_no_secret(
    primed_cache: oidc_client.ProviderMetadata,
) -> None:
    from urllib.parse import parse_qs, urlsplit

    attempt = oidc_client.LoginAttempt(nonce="n", verifier="v" * 43, next_path="/servers")
    url = oidc_client.authorization_url(primed_cache, "the-state", attempt)
    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [oidc_client.pkce_challenge("v" * 43)]
    assert query["state"] == ["the-state"]
    assert query["nonce"] == ["n"]
    assert query["client_id"] == [CLIENT_ID]
    # The verifier stays on the server; only its hash is published. And the
    # client secret never appears in a URL the browser will follow.
    assert "v" * 43 not in url
    assert "placeholder-client-secret" not in url
    assert "code_verifier" not in query
