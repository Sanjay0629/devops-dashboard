"""What the policy needs to know about a person: their unexpired roles, and their policy.

``required_role`` is checked by a direct query because :class:`app.authz.matcher.Grant` carries no
role name (``OPEN-98``). Both the async API and the synchronous worker and sweeper need the same
two reads, so the statements are built once and executed by whichever session is at hand; the
rules that *use* the answers are in :mod:`app.approvals.rules` and are pure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalPolicy
from app.approvals.rules import PolicyRow
from app.authz.models import Role, UserRole


def _roles_statement(user_id: uuid.UUID) -> Select[Any]:
    now = datetime.now(UTC)
    return (
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            (UserRole.expires_at.is_(None)) | (UserRole.expires_at > now),
        )
    )


async def roles_held(db: AsyncSession, user_id: uuid.UUID) -> frozenset[str]:
    """The names of the roles this user holds right now (expired assignments excluded)."""
    return frozenset((await db.execute(_roles_statement(user_id))).scalars().all())


def roles_held_sync(session: Session, user_id: uuid.UUID) -> frozenset[str]:
    return frozenset(session.execute(_roles_statement(user_id)).scalars().all())


def policy_row(row: ApprovalPolicy | None) -> PolicyRow | None:
    """The workflow's view of a policy row, or ``None`` when it could not be read."""
    if row is None:
        return None
    return PolicyRow(
        policy_id=row.id,
        applies_to=row.applies_to,
        environment=row.environment,
        required_approvals=row.required_approvals,
        allow_self_approval=row.allow_self_approval,
        required_role=row.required_role,
        approval_window_seconds=row.approval_window_seconds,
    )
