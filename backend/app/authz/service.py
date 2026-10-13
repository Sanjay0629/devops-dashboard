"""Resolving authority from PostgreSQL, per request (AD-03, PRD §4.1).

**There is no cache and no ``permissions_version`` counter.** That is an agreed
divergence from `AD-03` as written, approved by the product owner rather than
taken here (CLAUDE.md §3): PRD §4.1's requirement is per-request resolution,
the cache is an optimisation with no measured need, and a wrong invalidation
can only ever make revocation *less* immediate than no cache at all. The
documents that described a cache were corrected in the same change. Revisit
only if a measurement shows this is too slow; the counter is additive whenever
that happens.

So every privileged request runs :func:`resolve_grants` — one indexed join on
``user_roles`` — and a revoked grant is gone on the caller's next request with
no re-login, no restart and no invalidation step to get wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.authz.matcher import Grant
from app.authz.models import (
    READ_ONLY_MODE_KEY,
    Permission,
    Role,
    RolePermission,
    SystemSetting,
    UserRole,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


async def resolve_grants(db: AsyncSession, user_id: uuid.UUID) -> list[Grant]:
    """Every permission this user can reach right now, with its grant's scope.

    Expired assignments are excluded in the query, so the matcher needs no
    clock. A user with no role assignments resolves to an empty list, which the
    matcher denies on — deny by default is the absence of a grant, not a
    special case anywhere.
    """
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(
                Permission.resource_type,
                Permission.action,
                RolePermission.effect,
                UserRole.scope_definition,
            )
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(
                UserRole.user_id == user_id,
                (UserRole.expires_at.is_(None)) | (UserRole.expires_at > now),
            )
        )
    ).all()
    return [
        Grant(
            resource_type=row[0],
            action=row[1],
            effect=row[2],
            scope=row[3] or {},
        )
        for row in rows
    ]


async def holds_production_scope(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Does this user hold any unexpired grant naming ``production``?

    The delegation check behind docs/API_CONTRACTS.md §25.5: an Administrator
    may not grant production reach they do not have themselves. Read from the
    grants rather than from the role, because reach lives on the assignment.
    """
    grants = await resolve_grants(db, user_id)
    return any("production" in (grant.scope.get("environment") or ()) for grant in grants)


async def is_read_only(db: AsyncSession) -> bool:
    """Is the portal in incident read-only mode (docs/SECURITY.md §27)?

    Fails **closed**: an unreadable or missing setting answers ``True``, so a
    database problem cannot quietly re-enable writes during the one situation
    this switch exists for. The row is seeded by migration 0003, so "missing"
    means something is wrong rather than "not configured yet".
    """
    try:
        setting = await db.get(SystemSetting, READ_ONLY_MODE_KEY)
    except SQLAlchemyError as error:
        logger.warning("read_only_mode_lookup_failed", error=str(error))
        return True
    # Compared against the JSON literals rather than coerced: a truthiness test
    # would read the string "false" as enabled.
    if setting is not None:
        if setting.value is True:
            return True
        if setting.value is False:
            return False
    # Missing row, or a value that is not a boolean: unreadable, so closed.
    logger.warning("read_only_mode_unreadable")
    return True


async def set_read_only(db: AsyncSession, *, enabled: bool, actor_id: uuid.UUID) -> None:
    """Flip the switch. The caller's transaction commits it with the audit row."""
    await db.execute(
        update(SystemSetting)
        .where(SystemSetting.key == READ_ONLY_MODE_KEY)
        .values(value=enabled, updated_by=actor_id)
    )


def record_denial(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    user_label: str,
    permission: str,
    reason: str,
    method: str,
    path: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
    target_type: str = "system",
    target_id: uuid.UUID | None = None,
    environment: str | None = None,
) -> None:
    """Stage an ``AUTHZ_DENIED`` event (docs/API_CONTRACTS.md §28).

    The caller commits it — a denial must leave a trail even though the request
    fails, and the per-request session rolls back on the exception that carries
    the ``403``.
    """
    audit.record(
        db,
        action_type="AUTHZ_DENIED",
        outcome="DENIED",
        actor_id=user_id,
        actor_label=user_label,
        target_type=target_type,
        target_id=target_id,
        environment=environment,
        client_ip=client_ip,
        user_agent=user_agent,
        # The permission asked for, the rule that refused it, and where. No
        # request body, no scope dump: enough to reconstruct the attempt.
        metadata={"permission": permission, "reason": reason, "method": method, "path": path},
    )


async def permission_catalogue(db: AsyncSession) -> list[tuple[str, str, str]]:
    """``(resource_type, action, description)`` for every seeded permission."""
    rows = (
        await db.execute(
            select(Permission.resource_type, Permission.action, Permission.description).order_by(
                Permission.resource_type, Permission.action
            )
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


async def roles_with_permissions(db: AsyncSession) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """``(name, description, [{permission, effect}, …])`` for every role."""
    rows = (
        await db.execute(
            select(
                Role.name,
                Role.description,
                Permission.resource_type,
                Permission.action,
                RolePermission.effect,
            )
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(Permission, Permission.id == RolePermission.permission_id)
            .order_by(Role.name, Permission.resource_type, Permission.action)
        )
    ).all()
    collected: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for name, description, resource_type, action, effect in rows:
        _, permissions = collected.setdefault(name, (description, []))
        if resource_type is not None:
            permissions.append({"permission": f"{resource_type}:{action}", "effect": effect})
    return [
        (name, description, permissions) for name, (description, permissions) in collected.items()
    ]
