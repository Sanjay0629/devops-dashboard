"""User and role administration (docs/API_CONTRACTS.md §25).

Every function here changes who can do what, so each one is written around the
same four properties:

* **One transaction.** The state change and its audit row commit together
  (docs/DATABASE.md §12) — the caller's request session does the commit, so a
  rolled-back change cannot leave an audit row claiming it happened.
* **No escalation through delegation.** Production reach can only be granted by
  someone who holds it (§25.5).
* **No self-inflicted lockout.** The last Administrator grant cannot be revoked
  and its holder cannot be disabled; nobody can disable themselves (§25.4,
  §25.6).
* **Attribution.** Every event names the administrator who caused it
  (mandatory rule 10).

There is no ``permissions_version`` to bump. Permissions are resolved per
request from PostgreSQL (``app/authz/service.py``), so a change here is visible
on the target's next request with nothing to invalidate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import CreateUserRequest, GrantRoleRequest, UpdateUserRequest
from app.auth.models import User
from app.authz import service as authz
from app.authz.models import Role, UserRole
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.inventory.models import Application, CloudAccount, Server

logger = get_logger(__name__)

ADMINISTRATOR = "ADMINISTRATOR"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource does not exist."


class DuplicateResourceError(AppError):
    status_code = 409
    code = "duplicate_resource"
    message = "That resource already exists."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class NotPermittedError(AppError):
    """Refused on semantic grounds rather than permission grounds (§25.4).

    ``422 not_permitted``, not ``403``: the caller *has* the permission, and the
    action is refused because of what it would do.
    """

    status_code = 422
    code = "not_permitted"
    message = "That action is not permitted."


class DelegationEscalationError(NotPermittedError):
    """The caller tried to grant reach they do not hold themselves (§25.5).

    A distinct class, not a flag: the router audits this as an escalation
    attempt, and an audit row that called every ``422`` from this endpoint an
    escalation would be a false record of what happened.
    """

    message = "Granting production scope requires holding production scope yourself."


# --- reads -------------------------------------------------------------------


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError
    return user


def users_query() -> Select[User]:
    # Ordered by a unique-per-active-user column so paging is stable; without a
    # deterministic order, two pages can repeat or skip a row.
    return select(User).order_by(User.email)


async def count_users(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(User))).scalar_one())


async def grants_for(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[UserRole, str]]:
    """Every role assignment on this user, expired ones included.

    Expired grants are shown deliberately: an administrator looking at a user
    needs to see that a grant existed and lapsed, which is exactly what the
    per-request resolution hides.
    """
    rows = (
        await db.execute(
            select(UserRole, Role.name)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.name, UserRole.created_at)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


# --- writes ------------------------------------------------------------------


async def create_user(db: AsyncSession, request: CreateUserRequest) -> User:
    """Pre-create a user (§25.2) so roles can be granted before first login."""
    issuer = request.identity_provider_issuer or get_settings().oidc_issuer
    if not issuer:
        raise NotPermittedError(
            "No identity provider is configured, so identity_provider_issuer is required."
        )
    user = User(
        identity_provider_issuer=issuer,
        identity_provider_subject=request.identity_provider_subject,
        email=request.email,
        display_name=request.display_name,
        status="ACTIVE",
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as error:
        # Either the identity key or the active-email index. Both are
        # duplicates from the caller's point of view, and saying which would
        # confirm a specific identity exists.
        await db.rollback()
        raise DuplicateResourceError(
            "A user with that identity or email address already exists."
        ) from error
    return user


async def update_user(
    db: AsyncSession,
    user: User,
    request: UpdateUserRequest,
    *,
    actor_id: uuid.UUID,
) -> tuple[User, bool]:
    """Apply §25.4. Returns ``(user, disabled_now)``.

    One refusal: nobody disables themselves, because locking the last
    Administrator out is a self-inflicted outage (§25.4).

    There is deliberately **no** "cannot disable the last administrator" check
    here, even though §25.6 refuses the equivalent *revocation*. It would be
    unreachable: every caller of this endpoint holds a live ADMINISTRATOR grant
    of their own, so a target who is not the caller is never the last
    administrator, and a target who *is* the caller is refused above. An
    unreachable guard on a security path is worse than none — it reads as
    cover that does not exist.
    """
    if request.status == "DISABLED" and user.id == actor_id:
        raise NotPermittedError("You cannot disable your own account.")

    disabled_now = request.status == "DISABLED" and user.status != "DISABLED"
    if request.display_name is not None:
        user.display_name = request.display_name
    if request.status is not None:
        user.status = request.status
    await db.flush()
    return user, disabled_now


async def grant_role(
    db: AsyncSession,
    user: User,
    request: GrantRoleRequest,
    *,
    granted_by: uuid.UUID,
) -> UserRole:
    """Apply §25.5, including the no-escalation-through-delegation check."""
    scope = request.scope_definition.as_stored()

    if "production" in (scope.get("environment") or ()) and not await authz.holds_production_scope(
        db, granted_by
    ):
        # Not a 403 from the permission layer: the caller may grant roles, and
        # is refused for *what* they tried to grant. Audited as a denial by the
        # router, because an escalation attempt is worth seeing in the trail.
        raise DelegationEscalationError

    await _validate_scope_references(db, scope)

    role_id = (
        await db.execute(select(Role.id).where(Role.name == request.role_name))
    ).scalar_one_or_none()
    if role_id is None:
        # The five roles are seeded by migration, so this means the database is
        # not at the revision this code expects.
        raise NotFoundError("That role does not exist.")

    grant = UserRole(
        user_id=user.id,
        role_id=role_id,
        scope_definition=scope,
        granted_by=granted_by,
        expires_at=request.expires_at,
    )
    db.add(grant)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        # uq_user_roles_user_id_role_id_scope_definition: the identical grant
        # already exists (§25.5).
        raise DuplicateResourceError("That grant already exists for this user.") from error
    return grant


async def revoke_role(
    db: AsyncSession,
    user: User,
    user_role_id: uuid.UUID,
) -> tuple[UserRole, str]:
    """Apply §25.6. Refuses to remove the last Administrator grant."""
    row = (
        await db.execute(
            select(UserRole, Role.name)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.id == user_role_id, UserRole.user_id == user.id)
        )
    ).first()
    if row is None:
        raise NotFoundError("That grant does not exist for this user.")
    grant, role_name = row[0], row[1]

    if role_name == ADMINISTRATOR and await _count_administrator_grants(db) <= 1:
        raise ConflictError("The last Administrator grant cannot be revoked.")

    await db.delete(grant)
    await db.flush()
    return grant, role_name


# --- invariants --------------------------------------------------------------


def _live_administrator_grants() -> Select[int]:
    """Count of unexpired Administrator grants held by active users.

    Expiry and user status both matter: a grant that has lapsed, or belongs to
    a disabled account, cannot administer anything, so counting it would let
    the real last administrator be revoked.
    """
    now = datetime.now(UTC)
    return (
        select(func.count())
        .select_from(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .where(
            Role.name == ADMINISTRATOR,
            User.status == "ACTIVE",
            or_(UserRole.expires_at.is_(None), UserRole.expires_at > now),
        )
    )


async def _count_administrator_grants(db: AsyncSession) -> int:
    return int((await db.execute(_live_administrator_grants())).scalar_one())


async def _validate_scope_references(db: AsyncSession, scope: dict[str, list[str]]) -> None:
    """Every referenced id must exist (§25.5).

    A grant naming a server that does not exist is not harmless: the row would
    sit there looking like reach, and would silently start granting the moment
    something is registered with that id. ``"*"`` references nothing and needs
    no check.
    """
    for dimension, model in (
        ("cloud_account", CloudAccount),
        ("server", Server),
        ("application", Application),
    ):
        wanted = {value for value in scope.get(dimension, ()) if value != "*"}
        if not wanted:
            continue
        ids = {uuid.UUID(value) for value in wanted}
        found = set(
            (
                await db.execute(
                    select(model.id).where(
                        model.id.in_(ids),
                        model.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = ids - found
        if missing:
            raise NotPermittedError(f"Unknown {dimension} reference in scope_definition.")
