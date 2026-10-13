"""Mock OIDC provider — a **development aid only**.

Runs in the Compose `mocks` profile so M1.3 has a real authorisation-code +
PKCE flow to build and test against, with no cloud account and no real
identity provider (PRD §11, docs/ARCHITECTURE.md §12).

Read this before trusting anything it tells you (CLAUDE.md §7):

* **Passing against this proves the DCP client is self-consistent, not that it
  works with a real IdP.** Claim shapes, discovery quirks, redirect-URI
  registration rules and token-endpoint auth all differ per provider
  (`OPEN-01`). A real-IdP round trip is still owed.
* There is **no authentication here at all.** `/authorize` issues a code for
  whoever asks, for whichever user the query names. That is the point — it
  removes the IdP from the loop so the *session* behaviour can be tested — and
  it is also exactly why this must never be reachable from anything but a
  developer's machine.
* The signing key is generated fresh on every start, so restarting it
  invalidates issued tokens. Nothing persists.

It does implement the parts DCP's security checks actually depend on, so those
checks are meaningful: RS256 with a published JWKS and a `kid`, discovery,
`nonce` echoed into the ID token, and **PKCE S256 verified** — a mismatched
`code_verifier` is rejected, a code is single-use.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

ISSUER = os.environ.get("MOCK_IDP_ISSUER", "http://localhost:9000")
CLIENT_ID = os.environ.get("MOCK_IDP_CLIENT_ID", "dcp-local")
CLIENT_SECRET = os.environ.get("MOCK_IDP_CLIENT_SECRET", "placeholder-client-secret")
CODE_TTL_SECONDS = 120
TOKEN_TTL_SECONDS = 300

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = secrets.token_hex(8)

#: The directory of users this mock will happily impersonate. Synthetic, and
#: visibly so: `.invalid` can never resolve (RFC 2606).
USERS: dict[str, dict[str, Any]] = {
    "operator": {
        "sub": "mock-subject-operator",
        "email": "operator@example.invalid",
        "name": "Example Operator",
    },
    "admin": {
        "sub": "mock-subject-admin",
        "email": "admin@example.invalid",
        "name": "Example Administrator",
    },
    "viewer": {
        "sub": "mock-subject-viewer",
        "email": "viewer@example.invalid",
        "name": "Example Viewer",
    },
}


@dataclass(slots=True)
class PendingCode:
    user: str
    nonce: str
    challenge: str
    redirect_uri: str
    issued_at: float


_codes: dict[str, PendingCode] = {}

app = FastAPI(title="Mock OIDC provider (development aid)", docs_url=None, redoc_url=None)


def _b64u(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@app.get("/.well-known/openid-configuration")
def discovery() -> JSONResponse:
    return JSONResponse(
        {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "end_session_endpoint": f"{ISSUER}/logout",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["openid", "email", "profile"],
            "claims_supported": ["sub", "email", "email_verified", "name", "nonce"],
        }
    )


@app.get("/jwks")
def jwks() -> JSONResponse:
    numbers = _private_key.public_key().public_numbers()
    return JSONResponse(
        {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": _KID,
                    "n": _b64u(numbers.n),
                    "e": _b64u(numbers.e),
                }
            ]
        }
    )


@app.get("/authorize", response_model=None)
def authorize(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(...),
    nonce: str = Query(""),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query(""),
    response_type: str = Query("code"),
    scope: str = Query("openid"),
    user: str | None = Query(None),
) -> HTMLResponse | RedirectResponse:
    """Issue an authorisation code, picking an account.

    With no `?user=`, it renders a picker rather than guessing — so a developer
    can log in as a different synthetic user without editing anything.
    """
    if client_id != CLIENT_ID:
        raise HTTPException(status_code=400, detail="unknown client_id")
    if response_type != "code":
        raise HTTPException(status_code=400, detail="only response_type=code is supported")
    # Required, so DCP's PKCE path is always exercised and a client that
    # silently dropped PKCE would fail here instead of appearing to work.
    if code_challenge_method != "S256" or not code_challenge:
        raise HTTPException(status_code=400, detail="S256 code_challenge is required")

    if user is None or user not in USERS:
        options = "".join(
            f'<li><a href="/authorize?{urlencode({
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "response_type": response_type,
                "scope": scope,
                "user": name,
            })}">{name} — {info["email"]}</a></li>'
            for name, info in USERS.items()
        )
        return HTMLResponse(
            "<!doctype html><html><head><title>Mock IdP</title></head><body>"
            "<h1>Mock identity provider</h1>"
            "<p><strong>Development aid.</strong> No password is checked. "
            "These accounts are synthetic.</p>"
            f"<ul>{options}</ul></body></html>"
        )

    code = secrets.token_urlsafe(24)
    _codes[code] = PendingCode(
        user=user,
        nonce=nonce,
        challenge=code_challenge,
        redirect_uri=redirect_uri,
        issued_at=time.time(),
    )
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{separator}{urlencode({'code': code, 'state': state})}",
        status_code=302,
    )


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    """Exchange a code for an ID token.

    The urlencoded body is parsed directly rather than through FastAPI's
    ``Form()``, which needs ``python-multipart``. Adding a dependency to the
    backend image for the sake of a development aid is the wrong trade when
    ``parse_qs`` is one line.
    """
    form = {key: value[0] for key, value in parse_qs((await request.body()).decode()).items()}
    grant_type = form.get("grant_type", "")
    code = form.get("code", "")
    redirect_uri = form.get("redirect_uri", "")
    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    code_verifier = form.get("code_verifier", "")

    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="unsupported_grant_type")
    if client_id != CLIENT_ID or not secrets.compare_digest(client_secret, CLIENT_SECRET):
        raise HTTPException(status_code=401, detail="invalid_client")

    # Single use: popped, not read. A replayed code fails the second time.
    pending = _codes.pop(code, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if time.time() - pending.issued_at > CODE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="expired_code")
    if pending.redirect_uri != redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri_mismatch")

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    if not secrets.compare_digest(expected, pending.challenge):
        raise HTTPException(status_code=400, detail="invalid_grant: PKCE verification failed")

    profile = USERS[pending.user]
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": profile["sub"],
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
        "email": profile["email"],
        "email_verified": True,
        "name": profile["name"],
    }
    if pending.nonce:
        claims["nonce"] = pending.nonce
    id_token = jwt.encode(claims, _private_key, algorithm="RS256", headers={"kid": _KID})
    return JSONResponse(
        {
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "id_token": id_token,
            # DCP reads and drops this; included only because real providers do.
            "access_token": secrets.token_urlsafe(24),
        }
    )


@app.get("/logout", response_model=None)
def logout(post_logout_redirect_uri: str | None = Query(None)) -> HTMLResponse | RedirectResponse:
    if post_logout_redirect_uri:
        return RedirectResponse(post_logout_redirect_uri, status_code=302)
    return HTMLResponse("<!doctype html><html><body><p>Signed out.</p></body></html>")


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)  # noqa: S104 - container-local
