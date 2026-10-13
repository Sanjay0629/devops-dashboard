"""OIDC client: discovery, JWKS, token exchange, ID-token verification.

Scope: **authentication only**. Nothing here ever produces a role, a permission
or a scope. Authorisation is DCP's own, resolved from PostgreSQL per request
(AD-03), so that a permission can be revoked without an IdP change — which is
why roles are deliberately not read from token claims (OPEN-01).

Three boundaries worth naming, because each one is a real attack otherwise:

**The discovery document is provider output, i.e. untrusted data.** It is used
to *select endpoints to call*, so every URL in it is checked to share the
configured issuer's origin before anything is fetched (mandatory rule 9, no
user-supplied URL; docs/SECURITY.md §19). A compromised or spoofed discovery
response therefore cannot redirect the token exchange — with the client secret
in it — to an attacker's host.

**Algorithms are allowlisted, never taken from the token.** `alg: none` and the
HMAC family are rejected outright: accepting `HS256` against a JWKS public key
is the classic key-confusion forgery.

**JWKS refresh is bounded.** An unknown `kid` triggers at most one refetch per
``oidc_jwks_min_refresh_seconds``, so a stream of forged key ids cannot be
amplified into a request flood against the IdP (docs/TESTING.md §9).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import status

from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotConfiguredError
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

#: Asymmetric signatures only. An ID token is verified against a *public* key
#: published by the IdP; a symmetric alg would mean verifying it against
#: something we also hold, which is how key confusion becomes forgery.
ALLOWED_ALGORITHMS: Final[tuple[str, ...]] = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
    "PS384",
    "PS512",
)

_DISCOVERY_PATH: Final = "/.well-known/openid-configuration"
#: Cap on a provider response we parse, so an enormous body cannot be used to
#: exhaust memory on an unauthenticated path.
_MAX_RESPONSE_BYTES: Final = 256 * 1024


class OidcError(AppError):
    """ID-token or authorisation-response verification failed.

    Deliberately one error for every verification failure. ``message`` never
    names which check failed, because "wrong audience" and "bad signature" are
    different hints to hand an attacker. The reason is logged server-side.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication failed."


class ProviderUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_unavailable"
    message = "The identity provider is unavailable."


@dataclass(frozen=True, slots=True)
class IdentityClaims:
    """The verified subset of an ID token that DCP acts on.

    Only these five values leave this module. The raw token does not: nothing
    downstream has a reason to hold token material, and anything holding it
    could log it.
    """

    issuer: str
    subject: str
    email: str
    display_name: str
    #: Unverified in the general case, so recorded for the audit row rather
    #: than trusted. Email is display and routing only, never an authorisation
    #: input (app/auth/models.py).
    email_verified: bool


# --- PKCE --------------------------------------------------------------------


def new_pkce_verifier() -> str:
    """RFC 7636 code verifier: 43–128 unreserved characters."""
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    """S256 challenge. The ``plain`` method is not offered and never will be."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# --- provider metadata -------------------------------------------------------


def _same_origin(url: str, issuer: str) -> bool:
    """Is ``url`` on the issuer's exact scheme/host/port?

    The control that stops a tampered discovery document from pointing the
    token exchange — which carries the client secret — somewhere else.
    """
    try:
        candidate, reference = urlsplit(url), urlsplit(issuer)
    except ValueError:
        return False
    if candidate.scheme not in ("http", "https") or not candidate.hostname:
        return False
    return (
        candidate.scheme == reference.scheme
        and candidate.hostname == reference.hostname
        and candidate.port == reference.port
    )


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None


class _Cache:
    """Process-local caches for the discovery document and the JWKS.

    Not in Redis: this is small, cheap to rebuild, identical in every replica,
    and keeping it in-process means an IdP fetch cannot be poisoned by writing
    to a shared store.
    """

    def __init__(self) -> None:
        self.metadata: ProviderMetadata | None = None
        self.metadata_fetched_at: float = 0.0
        self.jwks: dict[str, Any] | None = None
        self.jwks_fetched_at: float = 0.0

    def clear(self) -> None:
        self.__init__()  # type: ignore[misc]


_cache = _Cache()


def reset_cache() -> None:
    """Drop cached provider metadata. Used by tests and on a config change."""
    _cache.clear()


def _client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.oidc_http_timeout_seconds),
        # A redirect on a metadata or token endpoint is either a
        # misconfiguration or an attempt to move the request off the issuer's
        # origin. Either way, do not follow it.
        follow_redirects=False,
    )


async def _get_json(url: str, settings: Settings) -> dict[str, Any]:
    try:
        async with _client(settings) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise ProviderUnavailableError
            body: Any = response.json()
    except ProviderUnavailableError:
        raise
    except Exception as error:
        # The URL is from configuration, not from a user, but it may carry a
        # hostname that should not reach a client. Logged, not returned.
        logger.warning("oidc_fetch_failed", error=str(error))
        raise ProviderUnavailableError from error
    if not isinstance(body, dict):
        logger.warning("oidc_response_not_an_object")
        raise ProviderUnavailableError
    return body


def require_configured(settings: Settings | None = None) -> Settings:
    """Fail with ``503 not_configured`` when no IdP is configured.

    PRD §11: the application must start without production credentials. It must
    not then *pretend* to authenticate (CLAUDE.md §7).
    """
    settings = settings or get_settings()
    if not settings.oidc_configured:
        raise NotConfiguredError(
            "No identity provider is configured. Set DCP_OIDC_ISSUER, "
            "DCP_OIDC_CLIENT_ID, DCP_OIDC_CLIENT_SECRET and DCP_OIDC_REDIRECT_URL."
        )
    return settings


async def get_metadata(*, force: bool = False) -> ProviderMetadata:
    """Fetch (and cache) the discovery document, validating it."""
    settings = require_configured()
    issuer = str(settings.oidc_issuer)
    now = time.monotonic()
    cached = _cache.metadata
    if (
        not force
        and cached is not None
        and now - _cache.metadata_fetched_at < settings.oidc_metadata_ttl_seconds
    ):
        return cached

    document = await _get_json(f"{issuer}{_DISCOVERY_PATH}", settings)

    # The document's own issuer must match the configured one exactly, or the
    # `iss` check on the ID token is validating against a value the provider
    # chose rather than one we did.
    if document.get("issuer") != issuer:
        logger.error("oidc_issuer_mismatch")
        raise ProviderUnavailableError

    required = ("authorization_endpoint", "token_endpoint", "jwks_uri")
    endpoints: dict[str, str] = {}
    for name in required:
        value = document.get(name)
        if not isinstance(value, str) or not _same_origin(value, issuer):
            logger.error("oidc_endpoint_rejected", endpoint=name)
            raise ProviderUnavailableError
        endpoints[name] = value

    end_session = document.get("end_session_endpoint")
    if not (isinstance(end_session, str) and _same_origin(end_session, issuer)):
        end_session = None

    metadata = ProviderMetadata(
        issuer=issuer,
        authorization_endpoint=endpoints["authorization_endpoint"],
        token_endpoint=endpoints["token_endpoint"],
        jwks_uri=endpoints["jwks_uri"],
        end_session_endpoint=end_session,
    )
    _cache.metadata = metadata
    _cache.metadata_fetched_at = now
    return metadata


async def _get_jwks(*, force: bool = False) -> dict[str, Any]:
    settings = require_configured()
    now = time.monotonic()
    if (
        not force
        and _cache.jwks is not None
        and now - _cache.jwks_fetched_at < settings.oidc_metadata_ttl_seconds
    ):
        return _cache.jwks
    metadata = await get_metadata()
    jwks = await _get_json(metadata.jwks_uri, settings)
    _cache.jwks = jwks
    _cache.jwks_fetched_at = now
    return jwks


async def _signing_key(kid: str | None) -> jwt.PyJWK:
    """Resolve a key id against the JWKS, refreshing once for an unknown one.

    Key *rotation* is the reason the refresh exists: a token signed with a key
    published after the cache was filled must be accepted. The minimum
    interval is the reason it is bounded: without it, forged key ids turn this
    endpoint into an amplifier pointed at the IdP.
    """
    settings = get_settings()
    jwks = await _get_jwks()
    key = _match_key(jwks, kid)
    if key is not None:
        return key
    if time.monotonic() - _cache.jwks_fetched_at >= settings.oidc_jwks_min_refresh_seconds:
        jwks = await _get_jwks(force=True)
        key = _match_key(jwks, kid)
        if key is not None:
            return key
    logger.warning("oidc_unknown_signing_key")
    raise OidcError


def _match_key(jwks: dict[str, Any], kid: str | None) -> jwt.PyJWK | None:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return None
    for entry in keys:
        if not isinstance(entry, dict):
            continue
        if kid is not None and entry.get("kid") != kid:
            continue
        # A `use: enc` key is for encryption; using it to verify a signature is
        # a key-reuse mistake regardless of what the token's header asks for.
        if entry.get("use") not in (None, "sig"):
            continue
        if entry.get("alg") is not None and entry.get("alg") not in ALLOWED_ALGORITHMS:
            continue
        try:
            return jwt.PyJWK.from_dict(entry)
        except Exception:
            logger.warning("oidc_malformed_jwk")
            continue
    return None


# --- token exchange and verification ----------------------------------------


async def exchange_code(code: str, verifier: str) -> str:
    """Trade an authorisation code for an ID token. Returns the raw ID token.

    The access token is read and dropped: DCP calls no provider API on the
    user's behalf, so retaining one would be holding a credential with no use
    for it. The client secret goes in the POST body to a token endpoint already
    proven to be on the issuer's origin.
    """
    settings = require_configured()
    metadata = await get_metadata()
    secret = settings.oidc_client_secret
    if secret is None:  # pragma: no cover - require_configured already proved this
        # Not an assert: `python -O` strips those, and the one thing this guard
        # must never do is disappear in a production build.
        raise NotConfiguredError
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(settings.oidc_redirect_url),
        "client_id": str(settings.oidc_client_id),
        "client_secret": secret.get_secret_value(),
        "code_verifier": verifier,
    }
    try:
        async with _client(settings) as client:
            response = await client.post(
                metadata.token_endpoint,
                data=form,
                headers={"Accept": "application/json"},
            )
    except Exception as error:
        logger.warning("oidc_token_exchange_unreachable", error=str(error))
        raise ProviderUnavailableError from error

    if response.status_code >= 500:
        logger.warning("oidc_token_endpoint_error", status_code=response.status_code)
        raise ProviderUnavailableError
    if response.status_code != 200:
        # A 4xx here means the code was wrong, replayed, or the PKCE verifier
        # did not match. All of them are authentication failures. The provider's
        # body is NOT logged: on some providers it echoes the submitted form.
        logger.warning("oidc_token_exchange_rejected", status_code=response.status_code)
        raise OidcError

    try:
        payload: Any = response.json()
    except ValueError as error:
        raise ProviderUnavailableError from error
    if not isinstance(payload, dict):
        raise ProviderUnavailableError
    id_token = payload.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        logger.warning("oidc_token_response_without_id_token")
        raise OidcError
    return id_token


async def verify_id_token(id_token: str, *, expected_nonce: str) -> IdentityClaims:
    """Verify an ID token end to end, or raise :class:`OidcError`.

    Checked: signature against the JWKS, ``alg`` against the allowlist,
    ``iss``, ``aud``, ``exp``, ``iat`` (both with bounded skew), and ``nonce``.
    No partial acceptance — docs/SECURITY.md §2.
    """
    settings = require_configured()
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as error:
        logger.warning("oidc_unparseable_id_token")
        raise OidcError from error

    algorithm = header.get("alg")
    if algorithm not in ALLOWED_ALGORITHMS:
        # Covers `alg: none` and the HMAC family in one check.
        logger.warning("oidc_disallowed_algorithm", alg=str(algorithm))
        raise OidcError

    key = await _signing_key(header.get("kid"))
    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key=key,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=str(settings.oidc_client_id),
            issuer=str(settings.oidc_issuer),
            leeway=settings.oidc_clock_skew_seconds,
            options={
                "require": ["iss", "aud", "exp", "iat", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.PyJWTError as error:
        # The exception type says which check failed; the client is told
        # nothing beyond "authentication failed".
        logger.warning("oidc_id_token_rejected", reason=type(error).__name__)
        raise OidcError from error

    # Nonce: replay defence, and PyJWT has no built-in check for it.
    # compare_digest because this is an equality check on a secret value.
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
        logger.warning("oidc_nonce_mismatch")
        raise OidcError

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        logger.warning("oidc_id_token_without_subject")
        raise OidcError

    claimed_email = claims.get("email")
    has_email = isinstance(claimed_email, str) and bool(claimed_email)
    # `.invalid` can never resolve (RFC 2606), so a notification sent to a
    # substituted address fails loudly rather than reaching a stranger.
    email = claimed_email if has_email else f"{subject}@users.noreply.invalid"
    name = claims.get("name") or claims.get("preferred_username") or email
    return IdentityClaims(
        issuer=str(settings.oidc_issuer),
        subject=subject,
        # Bounded to the column widths in app/auth/models.py, so an oversized
        # claim is truncated here rather than raising from the INSERT.
        email=str(email)[:320],
        display_name=str(name)[:256],
        # An address we made up is not a verified one, whatever the provider
        # asserted about the address it did not send.
        email_verified=has_email and bool(claims.get("email_verified", False)),
    )


# --- login attempt state (state / nonce / PKCE verifier) --------------------
# In Redis rather than in a cookie: single-use is the requirement
# (docs/SECURITY.md §2), and a cookie cannot be consumed server-side. A
# `state` that can be replayed is a CSRF hole on the callback.

_LOGIN_STATE_PREFIX: Final = "dcp:oidc-login:"


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    nonce: str
    verifier: str
    #: Where to send the browser after a successful login. Validated as a
    #: same-origin relative path *before* being stored, so the callback never
    #: has to re-reason about it.
    next_path: str


def new_state() -> str:
    return secrets.token_urlsafe(32)


def _login_state_key(state: str) -> str:
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    return f"{_LOGIN_STATE_PREFIX}{digest}"


async def store_login_attempt(state: str, attempt: LoginAttempt) -> None:
    settings = get_settings()
    await get_redis().set(
        _login_state_key(state),
        json.dumps(
            {
                "nonce": attempt.nonce,
                "verifier": attempt.verifier,
                "next_path": attempt.next_path,
            },
            separators=(",", ":"),
        ),
        ex=settings.oidc_login_state_ttl_seconds,
    )


async def consume_login_attempt(state: str) -> LoginAttempt | None:
    """Fetch and delete a login attempt. ``None`` if absent or already used.

    ``GETDEL`` makes single-use atomic. A read-then-delete would let two
    concurrent callbacks both succeed with the same ``state``, which is exactly
    the replay this is meant to stop.
    """
    if not state:
        return None
    raw = await get_redis().getdel(_login_state_key(state))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return LoginAttempt(
            nonce=str(data["nonce"]),
            verifier=str(data["verifier"]),
            next_path=str(data["next_path"]),
        )
    except (ValueError, KeyError, TypeError):
        logger.warning("oidc_login_state_unreadable")
        return None


def authorization_url(metadata: ProviderMetadata, state: str, attempt: LoginAttempt) -> str:
    settings = require_configured()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": str(settings.oidc_client_id),
            "redirect_uri": str(settings.oidc_redirect_url),
            "scope": " ".join(settings.oidc_scopes),
            "state": state,
            "nonce": attempt.nonce,
            "code_challenge": pkce_challenge(attempt.verifier),
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in metadata.authorization_endpoint else "?"
    return f"{metadata.authorization_endpoint}{separator}{query}"
