"""RBAC model: roles, the permission catalogue, and scoped grants.

``role_permissions`` is an addition beyond PRD §6, which defines ``roles``,
``permissions`` and ``user_roles`` but no link between roles and permissions —
without it RBAC cannot be expressed at all (docs/DATABASE.md §3.4, §17).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check

#: The five PRD §3 roles. VIEWER is this project's identifier for the PRD's
#: "Read-Only User" (docs/SECURITY.md §3).
ROLE_NAMES: Final[tuple[str, ...]] = (
    "VIEWER",
    "DEVELOPER",
    "DEVOPS_ENGINEER",
    "APPROVER",
    "ADMINISTRATOR",
)

PERMISSION_EFFECTS: Final[tuple[str, ...]] = ("ALLOW", "DENY")

RESOURCE_TYPES: Final[tuple[str, ...]] = (
    "dashboard",
    "server",
    "metrics",
    "service",
    "logs",
    "alert",
    "deployment",
    "operation",
    "audit",
    "admin",
    "integration",
)


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    # System roles are seeded by migration and cannot be renamed or deleted
    # through the API.
    is_system: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (enum_check("name", ROLE_NAMES, "name"),)


class Permission(Base, UUIDPrimaryKeyMixin):
    """A ``(resource_type, action)`` capability.

    Seeded by migration and **not** editable at runtime: the permission
    catalogue is code, and its role matrix lives in docs/SECURITY.md §4.
    No ``updated_at`` for the same reason.
    """

    __tablename__ = "permissions"

    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Constraints inherent to the permission itself, e.g.
    # {"environment_class": "non_production"} for a non-prod-only deploy grant.
    scope_definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "action",
            "scope_definition",
            name="uq_permissions_resource_type_action_scope_definition",
        ),
        enum_check("resource_type", RESOURCE_TYPES, "resource_type"),
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="RESTRICT"), primary_key=True
    )
    # DENY beats ALLOW in the matcher (docs/SECURITY.md §4 rule 3).
    effect: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'ALLOW'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (enum_check("effect", PERMISSION_EFFECTS, "effect"),)


class UserRole(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A role granted to a user within a scope.

    Surrogate primary key, because the same role may legitimately be granted
    twice to one user with different scopes.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    # {"environment": ["production"], "server": ["*"], ...} — validated by
    # Pydantic in the authorisation layer, not by the database
    # (docs/DATABASE.md §3.5). "*" on environment does NOT include production.
    scope_definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Attribution for privilege changes (mandatory rule 10).
    granted_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # Supports time-boxed elevation.
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "scope_definition",
            name="uq_user_roles_user_id_role_id_scope_definition",
        ),
        # Resolved on every request, so this index is on the hot path
        # (docs/ARCHITECTURE.md §9.2).
        Index("ix_user_roles_user_id", "user_id"),
    )
