"""Authentication endpoints (docs/API_CONTRACTS.md §12).

The four public-facing halves of AD-02's backend-for-frontend: the browser
talks only to DCP, DCP talks to the IdP, and no token ever reaches JavaScript.

``/login`` and ``/callback`` are two of the three documented public endpoints
(docs/SECURITY.md §20), so each is individually hardened:

* ``next`` is a same-origin **relative** path or the request is rejected. An
  open redirect on a login endpoint is a phishing primitive — it lets an
  attacker send a victim through a genuine login at a genuine domain and land
  them somewhere else.
* ``state`` is single-use, consumed atomically on the callback.
* Both are rate limited per client address, and the limit **fails closed**:
  these are the authentication class in docs/SECURITY.md §13.
* Every outcome is audited without any token material (PRD §4.1).
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth import oidc_client, service
from app.auth import session as session_store
from app.auth.cookies import clear_auth_cookies, set_csrf_cookie, set_session_cookie
from app.auth.dependencies import CurrentUser, client_ip, user_agent
from app.auth.oidc_client import LoginAttempt, OidcError
from app.auth.schemas import (
    LogoutResponse,
    SessionResponse,
    SessionRole,
    SessionUser,
    SessionWindow,
)
from app.core.config import Settings, get_settings
from app.core.csrf import new_csrf_token
from app.core.logging import get_logger
from app.core.ratelimit import enforce
from app.db.session import get_session

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

#: docs/API_CONTRACTS.md §12.1, verbatim. Anchored at both ends, so no prefix
#: match can sneak a scheme or an authority in.
_NEXT_PATTERN: Final = re.compile(r"^/[A-Za-z0-9_\-/?=&.]{0,512}$")
_DEFAULT_NEXT: Final = "/"
_RATE_BUCKET: Final = "auth"


def validate_next(candidate: str | None) -> str | None:
    """Return a safe relative path, or ``None`` if the input is unacceptable.

    Rejected, each for its own reason:

    * ``http://evil/`` — absolute URL, the obvious open redirect.
    * ``//evil.test/x`` — protocol-relative; a browser reads this as an
      authority, so it is an absolute URL that merely looks relative. This is
      the form a pattern of "must start with /" lets through, which is why the
      check is explicit rather than implied.
    * ``/\\evil.test`` — backslashes are normalised to ``/`` by some browsers,
      making this another authority form. The character class excludes them.
    * anything with a newline or control character — header injection into the
      ``Location`` response header.
    """
    if candidate is None or candidate == "":
        return _DEFAULT_NEXT
    if candidate.startswith("//") or not _NEXT_PATTERN.match(candidate):
        return None
    return candidate


async def _audited(db: AsyncSession, failure: HTTPException) -> HTTPException:
    """Commit the staged audit row, then hand back the error to raise.

    An authentication failure must leave a trail even though the request fails
    (PRD §4.1). ``get_session`` rolls back on any exception, so relying on it
    to flush a row staged on a failing request would silently lose exactly the
    events that matter most. Committing here makes the audit write explicit and
    independent of how the error is later handled.
    """
    await db.commit()
    return failure


async def _rate_limit(request: Request, settings: Settings) -> None:
    identity = client_ip(request) or "unknown"
    await enforce(
        _RATE_BUCKET,
        identity,
        limit=settings.auth_rate_limit_max,
        window_seconds=settings.auth_rate_limit_window_seconds,
        # Authentication is a privileged class: if the counter store is down we
        # refuse rather than open a brute-force window (docs/SECURITY.md §13).
        fail_closed=True,
    )


@router.get(
    "/login",
    status_code=status.HTTP_302_FOUND,
    summary="Begin OIDC login",
    response_class=RedirectResponse,
)
async def login(
    request: Request,
    next: Annotated[  # noqa: A002 - the query parameter is named `next` by contract
        str | None,
        Query(max_length=512, description="Same-origin relative path to return to"),
    ] = None,
) -> RedirectResponse:
    settings = get_settings()
    await _rate_limit(request, settings)

    # Input validation comes before the configuration check, on purpose: this
    # is the trust boundary (CLAUDE.md §5), and whether a redirect target is
    # acceptable has nothing to do with whether an IdP happens to be
    # configured. The other order would answer 503 to an open-redirect attempt
    # and hide it.
    next_path = validate_next(next)
    if next_path is None:
        # 422, not a silent fallback to "/": a rejected redirect target is a
        # client bug or an attack, and quietly fixing it hides both. The
        # rejected value is deliberately not echoed (docs/API_CONTRACTS.md §8).
        logger.warning("login_next_rejected")
        raise HTTPException(
            status_code=422,
            detail="next must be a same-origin relative path.",
        )

    # Raises 503 not_configured when no IdP is configured, rather than
    # redirecting to a URL built from None (CLAUDE.md §7).
    oidc_client.require_configured(settings)

    attempt = LoginAttempt(
        nonce=oidc_client.new_state(),
        verifier=oidc_client.new_pkce_verifier(),
        next_path=next_path,
    )
    state = oidc_client.new_state()
    await oidc_client.store_login_attempt(state, attempt)
    metadata = await oidc_client.get_metadata()
    target = oidc_client.authorization_url(metadata, state, attempt)
    logger.info("login_started")
    # 302 and no body. The Location points at the issuer's own authorization
    # endpoint, already proven to be on the configured issuer's origin.
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


@router.get(
    "/callback",
    status_code=status.HTTP_302_FOUND,
    summary="OIDC redirect: create the session",
    response_class=RedirectResponse,
)
async def callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    state: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    settings = get_settings()
    await _rate_limit(request, settings)
    oidc_client.require_configured(settings)

    ip, agent = client_ip(request), user_agent(request)

    def deny(reason: str, status_code: int) -> HTTPException:
        """Audit a failed login and build the response. Never logs token material.

        The audit row is staged on the request's session, which the dependency
        commits as the exception propagates — ``get_session`` rolls back only
        on an exception, and an ``HTTPException`` raised by the *caller* of
        this function is handled by FastAPI after the dependency completes.
        See the explicit commit below for why that is made unconditional.
        """
        audit.record(
            db,
            action_type="LOGIN_FAILED",
            outcome="FAILURE",
            # No user row to attribute to, so a named system actor owns the
            # event. ck_audit_events_actor_attribution requires actor_id to be
            # NULL for a non-USER actor, which is exactly this case.
            actor_type="SYSTEM",
            actor_label="system:auth",
            target_type="system",
            client_ip=ip,
            user_agent=agent,
            metadata={"reason": reason},
        )
        logger.warning("login_callback_rejected", reason=reason)
        return HTTPException(status_code=status_code, detail="Authentication failed.")

    # The IdP reported a failure (user cancelled, consent denied, …). The value
    # is provider-supplied text, so it is recorded as data and never echoed.
    if error is not None:
        raise await _audited(db, deny("provider_error", status.HTTP_401_UNAUTHORIZED))
    if not code or not state:
        raise await _audited(db, deny("missing_parameters", status.HTTP_400_BAD_REQUEST))

    attempt = await oidc_client.consume_login_attempt(state)
    if attempt is None:
        # Unknown, expired, or already consumed — a replayed state lands here,
        # because consume is an atomic GETDEL.
        raise await _audited(db, deny("invalid_or_replayed_state", status.HTTP_400_BAD_REQUEST))

    try:
        id_token = await oidc_client.exchange_code(code, attempt.verifier)
        claims = await oidc_client.verify_id_token(id_token, expected_nonce=attempt.nonce)
    except OidcError as failure:
        raise await _audited(
            db, deny("token_verification_failed", status.HTTP_401_UNAUTHORIZED)
        ) from failure

    try:
        user, created = await service.provision(db, claims)
    except service.IdentityConflictError as conflict:
        # 403 rather than 409: a 409 would confirm to an unauthenticated caller
        # that the address is registered here. No actor_id — the conflicting
        # row belongs to a different identity, and attributing the denial to it
        # would put one person's failure in another person's trail.
        audit.record(
            db,
            action_type="LOGIN_DENIED",
            outcome="DENIED",
            actor_type="SYSTEM",
            actor_label="system:auth",
            target_type="user",
            client_ip=ip,
            user_agent=agent,
            metadata={"reason": "identity_conflict"},
        )
        logger.warning("login_denied_identity_conflict")
        raise await _audited(
            db,
            HTTPException(
                status_code=403,
                detail="This account cannot be provisioned. Contact an administrator.",
            ),
        ) from conflict
    except service.UserDisabledError as disabled:
        # 403 with its own action type (§12.2): the identity is genuine and the
        # authentication succeeded; DCP is refusing the account.
        audit.record(
            db,
            action_type="LOGIN_DENIED",
            outcome="DENIED",
            actor_id=disabled.user.id,
            actor_label=disabled.user.email,
            target_type="user",
            target_id=disabled.user.id,
            client_ip=ip,
            user_agent=agent,
            metadata={"reason": "user_disabled"},
        )
        logger.warning("login_denied_disabled_user", user_id=str(disabled.user.id))
        raise await _audited(
            db,
            HTTPException(status_code=403, detail="This account is disabled."),
        ) from disabled

    # A fresh identifier for every login: the session is created here, never
    # carried over from before authentication (docs/SECURITY.md §5 rotation).
    session_id, _record = await session_store.create(user.id, client_ip=ip, user_agent=agent)
    audit.record(
        db,
        action_type="LOGIN_SUCCEEDED",
        outcome="SUCCESS",
        actor_id=user.id,
        actor_label=user.email,
        target_type="user",
        target_id=user.id,
        client_ip=ip,
        user_agent=agent,
        # No token, no session id, no nonce, no code (docs/SECURITY.md §5).
        metadata={
            "provisioned": created,
            "email_verified": claims.email_verified,
        },
    )

    response = RedirectResponse(attempt.next_path, status_code=status.HTTP_302_FOUND)
    set_session_cookie(response, session_id, settings)
    set_csrf_cookie(response, new_csrf_token(), settings)
    logger.info("login_succeeded", user_id=str(user.id), provisioned=created)
    return response


@router.post("/logout", summary="Destroy the session")
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
    current: CurrentUser,
) -> LogoutResponse:
    """Destroy the server-side session, clear the cookies, audit.

    Requires a valid session and a CSRF token (the middleware enforces the
    latter). Idempotent by contract — but a *second* call arrives without a
    live session and is answered ``401`` by ``require_session``, which is the
    documented behaviour for any authenticated endpoint after logout. The
    idempotency that matters is that the session is gone either way.
    """
    settings = get_settings()
    await session_store.delete(current.session_id)
    audit.record(
        db,
        action_type="LOGOUT",
        outcome="SUCCESS",
        actor_id=current.user.id,
        actor_label=current.user.email,
        target_type="user",
        target_id=current.user.id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    clear_auth_cookies(response, settings)
    logger.info("logout", user_id=str(current.user.id))
    return LogoutResponse()


@router.get("/session", summary="Current identity and effective permissions")
async def read_session(
    db: Annotated[AsyncSession, Depends(get_session)],
    current: CurrentUser,
) -> SessionResponse:
    """The identity the UI renders from.

    Roles and permissions are read from PostgreSQL on **every** call, never
    from the session record, so a revoked grant disappears on the next request
    with no re-login (PRD §4.1).
    """
    settings = get_settings()
    roles = await service.granted_roles(db, current.user.id)
    permissions = await service.effective_permissions(db, current.user.id)
    return SessionResponse(
        user=SessionUser(
            id=current.user.id,
            email=current.user.email,
            display_name=current.user.display_name,
            status=current.user.status,
        ),
        roles=[SessionRole(name=role.name, scope=role.scope) for role in roles],
        permissions=permissions,
        session=SessionWindow(
            expires_at=current.record.absolute_deadline,
            idle_timeout_seconds=settings.session_idle_timeout_seconds,
        ),
        demo_mode=settings.demo_mode,
    )
