"""User and role administration endpoints (docs/API_CONTRACTS.md §25).

The first routes in this codebase that carry a permission. Each one declares it
explicitly through ``require_permission``, which is the only thing that
authorises anything here — the frontend hides the admin UI from non-admins for
clarity, and that is not a control (docs/SECURITY.md §6). A ``curl`` with a
valid Viewer session is refused exactly as the UI path is, which
``tests/integration/test_admin_roles.py`` asserts endpoint by endpoint.

``admin:users`` and ``admin:roles`` are separate permissions because §25 and
docs/SECURITY.md §4 name them separately. Both are held only by
``ADMINISTRATOR`` today, so the split carries no behavioural difference yet;
it carries the option of one without the other later, which collapsing them
into a single ``admin:manage`` would have thrown away.

Audit coverage, all with the administrator as actor: ``USER_CREATED``,
``USER_ENABLED``, ``USER_DISABLED``, ``ROLE_GRANTED``, ``ROLE_REVOKED``,
``SESSION_TERMINATED``, ``READ_ONLY_MODE_CHANGED``. The reads are not audited
(§25 asks for none), except that their *denials* are, by the dependency.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service
from app.admin.schemas import (
    CreateUserRequest,
    GrantRoleRequest,
    OffsetPage,
    PermissionEntry,
    PermissionList,
    ReadOnlyModeRequest,
    ReadOnlyModeResponse,
    RoleDetail,
    RoleGrant,
    RoleList,
    RolePermissionEntry,
    TerminateSessionsResponse,
    UpdateUserRequest,
    UserDetail,
    UserList,
    UserSummary,
)
from app.audit import service as audit
from app.auth import session as session_store
from app.auth.dependencies import client_ip, user_agent
from app.auth.models import User
from app.authz import service as authz
from app.authz.dependencies import Authorized, require_permission
from app.authz.models import UserRole
from app.core.logging import get_logger
from app.db.session import get_session

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

Db = Annotated[AsyncSession, Depends(get_session)]
UserId = Annotated[uuid.UUID, Path(description="User identifier")]

#: Dependencies, built once. Each marks its route for the coverage test.
ManageUsers = Annotated[Authorized, require_permission("admin", "users")]
ManageRoles = Annotated[Authorized, require_permission("admin", "roles")]
#: The containment switch has to work *while* the portal is contained, so it is
#: the one route exempt from the read-only gate (docs/SECURITY.md §27).
ManageSettings = Annotated[
    Authorized, require_permission("admin", "manage", allow_in_read_only=True)
]


def _grant_model(grant: UserRole, role_name: str) -> RoleGrant:
    return RoleGrant(
        id=grant.id,
        role_name=role_name,
        scope_definition=grant.scope_definition or {},
        granted_by=grant.granted_by,
        expires_at=grant.expires_at,
        created_at=grant.created_at,
    )


def _user_detail(user: User, grants: list[tuple[UserRole, str]]) -> UserDetail:
    """Built field by field rather than from ``user.__dict__``.

    The mapped instance's ``__dict__`` carries SQLAlchemy's own state key,
    which ``extra="forbid"`` rejects — and naming the fields is what keeps a
    column added later from appearing in an API response by accident.
    """
    return UserDetail(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        identity_provider_issuer=user.identity_provider_issuer,
        identity_provider_subject=user.identity_provider_subject,
        roles=[_grant_model(grant, role_name) for grant, role_name in grants],
    )


# --- 25.1 / 25.2 -------------------------------------------------------------


@router.get("/users", summary="List users")
async def list_users(
    db: Db,
    _authorized: ManageUsers,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    """Offset pagination, per §5 for small administrative collections.

    No scope filtering: users are not scoped resources, and an administrator
    who may administer any user may see all of them.
    """
    total = await service.count_users(db)
    rows = (await db.execute(service.users_query().limit(limit).offset(offset))).scalars().all()
    return UserList(
        items=[UserSummary.model_validate(row, from_attributes=True) for row in rows],
        page=OffsetPage(
            limit=limit,
            offset=offset,
            total=total,
            has_more=offset + len(rows) < total,
        ),
    )


@router.post("/users", status_code=201, summary="Pre-create a user")
async def create_user(
    request: Request,
    body: CreateUserRequest,
    db: Db,
    authorized: ManageUsers,
) -> UserDetail:
    user = await service.create_user(db, body)
    audit.record(
        db,
        action_type="USER_CREATED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="user",
        target_id=user.id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"email": user.email, "pre_created": True},
    )
    await db.commit()
    logger.info("user_pre_created", user_id=str(user.id))
    return _user_detail(user, [])


# --- 25.3 / 25.4 -------------------------------------------------------------


@router.get("/users/{user_id}", summary="User detail including role grants")
async def read_user(user_id: UserId, db: Db, _authorized: ManageUsers) -> UserDetail:
    user = await service.get_user(db, user_id)
    grants = await service.grants_for(db, user.id)
    return _user_detail(user, grants)


@router.patch("/users/{user_id}", summary="Enable, disable, or rename a user")
async def update_user(
    request: Request,
    user_id: UserId,
    body: UpdateUserRequest,
    db: Db,
    authorized: ManageUsers,
) -> UserDetail:
    """Disabling terminates the user's sessions in the same unit of work (§25.4).

    The session store is Redis, so "same transaction" cannot be literal. The
    order is what makes it safe: the row is flushed first, then the sessions are
    destroyed, then the transaction commits. A failure between them leaves the
    user disabled with sessions already gone, never enabled with sessions
    destroyed — and ``require_session`` rejects a session whose user is no
    longer ``ACTIVE`` anyway, so a leftover session is not usable.
    """
    user = await service.get_user(db, user_id)
    previous_status = user.status
    user, disabled_now = await service.update_user(db, user, body, actor_id=authorized.user.id)

    terminated = await session_store.terminate_user_sessions(user.id) if disabled_now else 0

    if body.status is not None and body.status != previous_status:
        audit.record(
            db,
            action_type="USER_DISABLED" if body.status == "DISABLED" else "USER_ENABLED",
            outcome="SUCCESS",
            actor_id=authorized.user.id,
            actor_label=authorized.user.email,
            target_type="user",
            target_id=user.id,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={
                "from": previous_status,
                "to": body.status,
                "sessions_terminated": terminated,
            },
        )
    await db.commit()
    grants = await service.grants_for(db, user.id)
    return _user_detail(user, grants)


# --- 25.5 / 25.6 -------------------------------------------------------------


@router.post("/users/{user_id}/roles", status_code=201, summary="Grant a role with scope")
async def grant_role(
    request: Request,
    user_id: UserId,
    body: GrantRoleRequest,
    db: Db,
    authorized: ManageRoles,
) -> RoleGrant:
    user = await service.get_user(db, user_id)
    try:
        grant = await service.grant_role(db, user, body, granted_by=authorized.user.id)
    except service.DelegationEscalationError:
        # An escalation attempt is worth a trail of its own, not just a 422.
        # Narrow on purpose: the other 422 from this endpoint is an unknown
        # scope reference, which is a typo, not an attempt to gain reach.
        authz.record_denial(
            db,
            user_id=authorized.user.id,
            user_label=authorized.user.email,
            permission="admin:roles",
            reason="delegation_escalation",
            method=request.method,
            path=request.url.path,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            target_type="user",
            target_id=user.id,
        )
        await db.commit()
        raise
    audit.record(
        db,
        action_type="ROLE_GRANTED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="user_role",
        target_id=grant.id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        # Before/after for a new grant: there was none, and this is it (§25.5).
        metadata={
            "user_id": str(user.id),
            "role_name": body.role_name,
            "scope_before": None,
            "scope_after": grant.scope_definition,
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        },
    )
    await db.commit()
    logger.info("role_granted", role=body.role_name, user_id=str(user.id))
    return _grant_model(grant, body.role_name)


@router.delete("/users/{user_id}/roles/{user_role_id}", status_code=204, summary="Revoke a grant")
async def revoke_role(
    request: Request,
    user_id: UserId,
    user_role_id: Annotated[uuid.UUID, Path(description="Grant identifier")],
    db: Db,
    authorized: ManageRoles,
) -> None:
    user = await service.get_user(db, user_id)
    grant, role_name = await service.revoke_role(db, user, user_role_id)
    audit.record(
        db,
        action_type="ROLE_REVOKED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="user_role",
        target_id=user_role_id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={
            "user_id": str(user.id),
            "role_name": role_name,
            "scope_before": grant.scope_definition,
            "scope_after": None,
        },
    )
    await db.commit()
    logger.info("role_revoked", role=role_name, user_id=str(user.id))


# --- 25.7 --------------------------------------------------------------------


@router.post("/users/{user_id}/sessions/terminate", summary="Terminate all sessions")
async def terminate_sessions(
    request: Request,
    user_id: UserId,
    db: Db,
    authorized: ManageUsers,
) -> TerminateSessionsResponse:
    """Containment step, so it stays available and audited (docs/SECURITY.md §27)."""
    user = await service.get_user(db, user_id)
    terminated = await session_store.terminate_user_sessions(user.id)
    audit.record(
        db,
        action_type="SESSION_TERMINATED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="user",
        target_id=user.id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        # The count, never a session identifier (docs/SECURITY.md §5).
        metadata={"sessions_terminated": terminated},
    )
    await db.commit()
    return TerminateSessionsResponse(terminated=terminated)


# --- 25.8 / 25.9 -------------------------------------------------------------


@router.get("/roles", summary="Roles and their permissions")
async def list_roles(db: Db, _authorized: ManageRoles) -> RoleList:
    rows = await authz.roles_with_permissions(db)
    return RoleList(
        items=[
            RoleDetail(
                name=name,
                description=description,
                permissions=[RolePermissionEntry(**entry) for entry in permissions],
            )
            for name, description, permissions in rows
        ]
    )


@router.get("/permissions", summary="Permission catalogue")
async def list_permissions(db: Db, _authorized: ManageRoles) -> PermissionList:
    return PermissionList(
        items=[
            PermissionEntry(
                permission=f"{resource_type}:{action}",
                resource_type=resource_type,
                action=action,
                description=description,
            )
            for resource_type, action, description in await authz.permission_catalogue(db)
        ]
    )


# --- 25.10 -------------------------------------------------------------------


@router.post("/settings/read-only-mode", summary="Toggle incident read-only mode")
async def set_read_only_mode(
    request: Request,
    body: ReadOnlyModeRequest,
    db: Db,
    authorized: ManageSettings,
) -> ReadOnlyModeResponse:
    """Block or restore state-changing endpoints (docs/SECURITY.md §27).

    Durable, so a restart or a Redis flush does not quietly re-enable writes;
    exempt from its own gate, so it can be switched back off.
    """
    if body.enabled and body.reason is None:
        raise service.NotPermittedError("A reason is required when enabling read-only mode.")
    await authz.set_read_only(db, enabled=body.enabled, actor_id=authorized.user.id)
    audit.record(
        db,
        action_type="READ_ONLY_MODE_CHANGED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="system",
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"enabled": body.enabled, "reason": body.reason},
    )
    await db.commit()
    logger.warning("read_only_mode_changed", enabled=body.enabled)
    return ReadOnlyModeResponse(read_only_mode=body.enabled)
