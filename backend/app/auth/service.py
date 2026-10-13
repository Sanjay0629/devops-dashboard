"""Identity provisioning and the advisory permission read.

Two jobs, both deliberately narrow:

**Just-in-time provisioning.** A verified ID token is the only way a `users`
row comes into existence (the seed migration creates none). The identity key is
``(identity_provider_issuer, identity_provider_subject)`` — never the email,
which can be reassigned to a different person. So an address change at the IdP
updates the existing row instead of creating a second user.

**The advisory permission read.** ``/auth/session`` returns the caller's roles
and permissions *so the UI can decide what to render*. It carries no authority
(mandatory rule 5, docs/API_CONTRACTS.md §12.4) and this module holds no
matcher: scope evaluation and ``require_permission`` are M1.4, resolved per
request against the target's stored attributes. Nothing here is cached, for the
same reason — PRD §4.1 requires revocation to bite on the next request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.oidc_client import IdentityClaims
from app.authz.models import Permission, Role, RolePermission, UserRole
from app.core.logging import get_logger

logger = get_logger(__name__)


class IdentityConflictError(Exception):
    """A new identity presents an email an active user already holds.

    ``uq_users_email_active`` is a case-insensitive unique index over active
    users (docs/DATABASE.md §3.1), so this is reachable whenever an IdP issues
    a second subject for an address DCP has already seen — a re-provisioned
    account, a merged directory, or two providers.

    The PRD does not say what should happen. The default chosen here is to
    **deny the login** rather than to merge, rename or create a second row:
    merging identities silently is how one person inherits another's history,
    and the audit trail is attributed by ``user_id``. An administrator resolves
    it by retiring the old row, after which the address is reusable because the
    index is scoped to active users. Recorded as an assumption in PROGRESS.md.
    """


class UserDisabledError(Exception):
    """A disabled user authenticated successfully at the IdP.

    Carries the row so the caller can audit ``LOGIN_DENIED`` against the real
    user id — an unattributed denial is worth less than an attributed one
    (mandatory rule 10).
    """

    def __init__(self, user: User) -> None:
        super().__init__("user is disabled")
        self.user = user


@dataclass(frozen=True, slots=True)
class GrantedRole:
    name: str
    scope: dict[str, Any]


async def find_by_identity(db: AsyncSession, claims: IdentityClaims) -> User | None:
    return (
        await db.execute(
            select(User).where(
                User.identity_provider_issuer == claims.issuer,
                User.identity_provider_subject == claims.subject,
            )
        )
    ).scalar_one_or_none()


async def provision(db: AsyncSession, claims: IdentityClaims) -> tuple[User, bool]:
    """Find or create the user behind a verified ID token.

    Returns ``(user, created)``. Raises :class:`UserDisabledError` for a user whose
    status is not ``ACTIVE`` — fail closed, and *before* any session exists.

    A new user is created with **no roles**. There is no implicit grant and no
    bootstrap administrator: the first administrator is granted deliberately
    (alembic/versions/0002 docstring). The visible consequence is that a
    freshly provisioned user can sign in and see nothing, which is the correct
    default for a tool that restarts production services.
    """
    user = await find_by_identity(db, claims)
    created = False
    if user is None:
        user = User(
            identity_provider_issuer=claims.issuer,
            identity_provider_subject=claims.subject,
            email=claims.email,
            display_name=claims.display_name,
            status="ACTIVE",
        )
        db.add(user)
        try:
            # Flush, not commit: the id is needed for the audit row's actor_id,
            # and both must land in the same transaction.
            await db.flush()
        except IntegrityError as error:
            # The database refused it. Deny rather than let an unhandled error
            # become a 500 on an authentication path (mandatory rule 6).
            await db.rollback()
            logger.warning("user_provisioning_conflict")
            raise IdentityConflictError from error
        created = True
        logger.info("user_provisioned", user_id=str(user.id))
    elif user.status != "ACTIVE":
        raise UserDisabledError(user)
    else:
        # Keep display data current without touching the identity key.
        user.email = claims.email
        user.display_name = claims.display_name

    user.last_login_at = datetime.now(UTC)
    await db.flush()
    return user, created


async def granted_roles(db: AsyncSession, user_id: uuid.UUID) -> list[GrantedRole]:
    """Unexpired role grants held by this user, with their scopes."""
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(Role.name, UserRole.scope_definition)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user_id,
                (UserRole.expires_at.is_(None)) | (UserRole.expires_at > now),
            )
            .order_by(Role.name)
        )
    ).all()
    return [GrantedRole(name=row[0], scope=row[1] or {}) for row in rows]


async def effective_permissions(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """``resource_type:action`` strings for the UI. **Advisory only.**

    An ``ALLOW`` reachable through any unexpired grant is listed; a ``DENY`` on
    the same permission removes it, mirroring "any deny wins overall"
    (docs/SECURITY.md §4 rule 3). Scope is *not* applied here — it needs a
    target to be evaluated against, and this list has none. So the array can be
    wider than what the caller may actually do, which is exactly why it has no
    authority and why M1.4's matcher is the only thing that decides.
    """
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(Permission.resource_type, Permission.action, RolePermission.effect)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(
                UserRole.user_id == user_id,
                (UserRole.expires_at.is_(None)) | (UserRole.expires_at > now),
            )
        )
    ).all()
    allowed: set[str] = set()
    denied: set[str] = set()
    for resource_type, action, effect in rows:
        (denied if effect == "DENY" else allowed).add(f"{resource_type}:{action}")
    return sorted(allowed - denied)
