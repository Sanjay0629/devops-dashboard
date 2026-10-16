"""The one authorisation dependency (docs/SECURITY.md §4 rule 7, §20).

``require_session`` says *who*; :func:`require_permission` says *whether*. Every
non-public route carries one, and a route that carries neither is a test
failure, not a judgement call (``tests/authz/test_route_coverage.py``).

Two gates, in this order:

1. **Permission.** The caller's grants are resolved from PostgreSQL on every
   request (``app/authz/service.resolve_grants``) and handed to the matcher.
   Denial is ``403`` plus an ``AUTHZ_DENIED`` audit row, committed before the
   error propagates so the trail survives the failed request.
2. **Read-only mode.** A state-changing method is refused with ``423
   read_only_mode`` while the incident switch is on (docs/SECURITY.md §27).

Read-only mode is checked *here*, inside the permission dependency, rather than
in middleware with a list of exempt paths. Every state-changing endpoint has a
permission dependency by construction, so this covers all of them with nothing
to keep in sync; and the paths that must keep working during an incident —
health probes, login, logout, alert ingestion — are exactly the ones with no
permission dependency. The one endpoint that must work *because* the switch is
on, the toggle itself, says so explicitly with ``allow_in_read_only=True``.

Order matters: permission first. A caller who may not use an endpoint learns
nothing about whether the portal is contained.

There is no bypass. No ``if settings.debug``, no "skip in tests", no
environment that turns this off — asserted by a grep gate in
``tests/authz/test_route_coverage.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Request, status
from fastapi import params as fastapi_params
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedUser, client_ip, require_session, user_agent
from app.auth.models import User
from app.authz import service
from app.authz.matcher import Grant, Target, permits
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import get_session

logger = get_logger(__name__)

#: Methods that change state. ``GET``/``HEAD``/``OPTIONS`` stay available in
#: read-only mode, which is the entire point of the mode (docs/SECURITY.md §27:
#: "leaving monitoring fully usable").
_UNSAFE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Marks a dependency as carrying a permission, for the route-coverage test.
PERMISSION_ATTRIBUTE: Final = "__dcp_permission__"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    #: Deliberately uniform and uninformative: the same text whatever the
    #: reason, so a probe cannot map out scopes by reading error messages
    #: (docs/SECURITY.md §20). The reason is in the audit row instead.
    message = "You do not have permission to perform this action."


class ReadOnlyModeError(AppError):
    status_code = status.HTTP_423_LOCKED
    code = "read_only_mode"
    message = "The portal is in read-only mode. State-changing actions are disabled."


@dataclass(frozen=True, slots=True)
class Authorized:
    """An authenticated caller whose permission for this route was checked.

    Carries the resolved grants so a handler that needs the scoped question
    too — "may they do it *to this row*" — can ask the same matcher without a
    second database round trip.
    """

    user: User
    session_id: str
    grants: tuple[Grant, ...]

    def permits(self, resource_type: str, action: str, target: Target | None = None) -> bool:
        """The scoped question, for use after the target row is loaded.

        docs/SECURITY.md §20 requires ownership and scope to be validated after
        the row exists, which no dependency can do for the handler. Same
        matcher, same rules — the only difference is that a target is known.
        """
        return permits(self.grants, resource_type, action, target).allowed


def require_permission(
    resource_type: str,
    action: str,
    *,
    allow_in_read_only: bool = False,
) -> fastapi_params.Depends:
    """Build the dependency enforcing ``resource_type:action`` on a route.

    ``allow_in_read_only`` is for the endpoints that must work *while* the
    portal is contained: the toggle that turns read-only mode back off, and
    reads that happen to need a ``POST`` body (§15.3's metric comparison).
    """
    permission = f"{resource_type}:{action}"

    async def dependency(
        request: Request,
        current: Annotated[AuthenticatedUser, Depends(require_session)],
        db: Annotated[AsyncSession, Depends(get_session)],
    ) -> Authorized:
        try:
            grants = await service.resolve_grants(db, current.user.id)
        except SQLAlchemyError as error:
            # Fail closed (mandatory rule 6): "cannot resolve" is a denial, not
            # a pass. No audit row — the write would use the same broken
            # session — so this is the one denial that lives only in the log.
            logger.warning("grant_resolution_failed", error=str(error))
            raise ForbiddenError from error

        decision = permits(grants, resource_type, action)
        if not decision.allowed:
            await _deny(
                db,
                request,
                current,
                permission=permission,
                reason=decision.reason,
            )
            raise ForbiddenError

        blocked_by_read_only = (
            request.method in _UNSAFE_METHODS
            and not allow_in_read_only
            and await service.is_read_only(db)
        )
        if blocked_by_read_only:
            await _deny(db, request, current, permission=permission, reason="read_only_mode")
            raise ReadOnlyModeError

        return Authorized(
            user=current.user,
            session_id=current.session_id,
            grants=tuple(grants),
        )

    # The route-coverage test looks for this, so a route cannot satisfy the
    # test with any dependency that merely happens to be present.
    setattr(dependency, PERMISSION_ATTRIBUTE, (resource_type, action))
    # ``params.Depends`` directly rather than ``Depends(...)``: identical
    # object, and it is typed, where the helper returns Any.
    return fastapi_params.Depends(dependency=dependency, use_cache=True)


async def _deny(
    db: AsyncSession,
    request: Request,
    current: AuthenticatedUser,
    *,
    permission: str,
    reason: str,
) -> None:
    """Audit a refusal and commit it, so the failed request still leaves a trail."""
    service.record_denial(
        db,
        user_id=current.user.id,
        user_label=current.user.email,
        permission=permission,
        reason=reason,
        method=request.method,
        path=request.url.path,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    try:
        await db.commit()
    except SQLAlchemyError as error:
        # The denial still stands; it is the record that was lost.
        logger.error("authz_denial_audit_failed", error=str(error))
    logger.info("authz_denied", permission=permission, reason=reason)
