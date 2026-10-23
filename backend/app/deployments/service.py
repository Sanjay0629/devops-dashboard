"""Deployment-target lookups (``OPEN-118``).

``resolve_target`` is what §21.2 will call in M5.2: an application and an environment name the
**one** registered, active target, or the answer is ``422 not_registered``. It is not a ``404``
(the application exists) and it never falls back to anything -- no default target, no "closest"
environment.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import ENVIRONMENTS
from app.core.errors import AppError
from app.deployments.models import DeploymentTarget


class NotRegisteredError(AppError):
    status_code = 422
    code = "not_registered"
    message = "No active deployment target is registered for that application and environment."


async def resolve_target(
    db: AsyncSession, application_id: uuid.UUID, environment: str
) -> DeploymentTarget:
    """The single ``ACTIVE``, non-retired target for the pair, or :class:`NotRegisteredError`."""
    if environment not in ENVIRONMENTS:
        raise NotRegisteredError
    target = (
        await db.execute(
            select(DeploymentTarget).where(
                DeploymentTarget.application_id == application_id,
                DeploymentTarget.environment == environment,
                DeploymentTarget.deleted_at.is_(None),
                DeploymentTarget.status == "ACTIVE",
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotRegisteredError
    return target
