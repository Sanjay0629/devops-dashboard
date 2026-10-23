"""Deployment-target lookups and registration (``OPEN-118``, ``OPEN-126``).

``resolve_target`` is what §21.2 will call in M5.2: an application and an environment name the
**one** registered, active target, or the answer is ``422 not_registered``. It is not a ``404``
(the application exists) and it never falls back to anything -- no default target, no "closest"
environment.

The rest is the administrator's surface (§21.9 – §21.12). Nothing here fetches or builds a Jenkins
URL: a target is data, validated against closed grammars by ``app.deployments.schemas``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import ENVIRONMENTS, ApprovalPolicy
from app.authz.matcher import Target
from app.core.config import get_settings
from app.core.errors import AppError, FieldValidationError
from app.deployments.models import DeploymentTarget
from app.deployments.schemas import (
    JenkinsConfiguration,
    TargetCreate,
    TargetDetail,
    TargetSummary,
    TargetUpdate,
    cross_field_problems,
)
from app.deployments.template import ParameterTemplate
from app.inventory.models import Application
from app.inventory.service import (
    DuplicateResourceError,
    NotFoundError,
    _idempotency_conflict,
    get_application,
)


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


# --- registration (§21.8 - §21.12, ``OPEN-126``) ------------------------------------------------


class NotPermittedError(AppError):
    """The caller holds the permission, and the request still asks for something not allowed."""

    status_code = 422
    code = "not_permitted"
    message = "That is not permitted."

    def __init__(self, reason: str) -> None:
        super().__init__(self.message, details={"reason": reason})


class ApprovalPolicyRequiredError(AppError):
    status_code = 422
    code = "approval_policy_required"
    message = "A production deployment target must have an approval policy."


def target_for_matcher(target: DeploymentTarget) -> Target:
    """What the matcher judges a target by: stored values, never a request's (SECURITY.md §4)."""
    return Target(environment=target.environment, application=str(target.application_id))


def summary(target: DeploymentTarget, application_name: str) -> TargetSummary:
    return TargetSummary(
        id=target.id,
        application_id=target.application_id,
        application_name=application_name,
        environment=target.environment,  # type: ignore[arg-type]
        pipeline_reference=target.pipeline_reference,
        allowed_ref_patterns=list(target.allowed_ref_patterns),
        requires_approval=target.approval_policy_id is not None,
        rollback_supported=target.rollback_supported,
        status=target.status,  # type: ignore[arg-type]
        updated_at=target.updated_at,
    )


async def detail(db: AsyncSession, target: DeploymentTarget) -> TargetDetail:
    name = (
        await db.execute(select(Application.name).where(Application.id == target.application_id))
    ).scalar_one()
    return TargetDetail(
        **summary(target, name).model_dump(),
        approval_policy_id=target.approval_policy_id,
        parameter_template=ParameterTemplate.model_validate(target.parameter_template),
        health_check_config=dict(target.health_check_config),
        rollback_pipeline_reference=target.rollback_pipeline_reference,
        created_at=target.created_at,
    )


def jenkins_configuration() -> JenkinsConfiguration:
    settings = get_settings()
    return JenkinsConfiguration(
        status="configured" if settings.jenkins_url else "not_configured",
        demo_mode=settings.demo_mode,
    )


async def get_target(db: AsyncSession, target_id: uuid.UUID) -> DeploymentTarget:
    target = await db.get(DeploymentTarget, target_id)
    if target is None or target.deleted_at is not None:
        raise NotFoundError
    return target


async def _check_policy(db: AsyncSession, policy_id: uuid.UUID | None, environment: str) -> None:
    """``OPEN-123``: a ``DEPLOYMENT`` policy for this environment, required for production."""
    if policy_id is None:
        if environment == "production":
            raise ApprovalPolicyRequiredError
        return
    policy = await db.get(ApprovalPolicy, policy_id)
    if (
        policy is None
        or policy.applies_to != "DEPLOYMENT"
        or policy.environment not in (environment, "*")
    ):
        raise NotPermittedError("approval_policy_not_applicable")


def _check_health_checks(config: dict[str, Any]) -> None:
    if config:
        raise NotPermittedError("health_checks_not_available_yet")


def _matches(target: DeploymentTarget, request: TargetCreate) -> bool:
    return all(
        (
            target.application_id == request.application_id,
            target.environment == request.environment,
            target.pipeline_reference == request.pipeline_reference,
            target.parameter_template == request.parameter_template.model_dump(),
            list(target.allowed_ref_patterns) == request.allowed_ref_patterns,
            target.approval_policy_id == request.approval_policy_id,
            target.rollback_supported == request.rollback_supported,
            target.rollback_pipeline_reference == request.rollback_pipeline_reference,
        )
    )


async def create_target(
    db: AsyncSession, request: TargetCreate, *, created_by: uuid.UUID, idempotency_key: str
) -> tuple[DeploymentTarget, bool]:
    """§21.10. Returns ``(target, replayed)``."""
    replay = (
        await db.execute(
            select(DeploymentTarget).where(
                DeploymentTarget.created_by == created_by,
                DeploymentTarget.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if not _matches(replay, request):
            raise _idempotency_conflict()
        return replay, True

    await get_application(db, request.application_id)  # 404 for a missing or archived one
    _check_health_checks(request.health_check_config)
    await _check_policy(db, request.approval_policy_id, request.environment)

    target = DeploymentTarget(
        application_id=request.application_id,
        environment=request.environment,
        pipeline_reference=request.pipeline_reference,
        parameter_template=request.parameter_template.model_dump(),
        allowed_ref_patterns=request.allowed_ref_patterns,
        approval_policy_id=request.approval_policy_id,
        health_check_config={},
        rollback_supported=request.rollback_supported,
        rollback_pipeline_reference=request.rollback_pipeline_reference,
        status="ACTIVE",
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    db.add(target)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise DuplicateResourceError(
            "A deployment target is already registered for that application and environment."
        ) from error
    return target, False


async def update_target(
    db: AsyncSession, target: DeploymentTarget, request: TargetUpdate
) -> tuple[DeploymentTarget, list[str]]:
    """§21.11. Returns the target and the **names** of the fields that changed."""
    fields = request.model_dump(exclude_unset=True)
    template = (
        request.parameter_template
        if request.parameter_template is not None
        else ParameterTemplate.model_validate(target.parameter_template)
    )
    patterns = (
        list(target.allowed_ref_patterns)
        if request.allowed_ref_patterns is None
        else request.allowed_ref_patterns
    )
    supported = (
        target.rollback_supported
        if request.rollback_supported is None
        else request.rollback_supported
    )
    rollback_reference = fields.get(
        "rollback_pipeline_reference", target.rollback_pipeline_reference
    )
    problems = cross_field_problems(template, patterns, supported, rollback_reference)
    if problems:
        raise FieldValidationError(
            [
                {"loc": ["body", name], "code": "value_error", "message": message}
                for name, message in problems
            ]
        )
    if "approval_policy_id" in fields:
        await _check_policy(db, fields["approval_policy_id"], target.environment)
    elif request.status == "ACTIVE":
        await _check_policy(db, target.approval_policy_id, target.environment)

    changed: list[str] = []
    for name, value in fields.items():
        if name == "parameter_template":
            value = template.model_dump()
        if getattr(target, name) != value:
            setattr(target, name, value)
            changed.append(name)
    if changed:
        await db.flush()
    return target, changed


async def retire_target(db: AsyncSession, target: DeploymentTarget) -> DeploymentTarget:
    """§21.12: soft delete. The in-flight-deployment guard arrives with ``deployments`` (M5.2)."""
    target.status = "DISABLED"
    target.deleted_at = datetime.now(UTC)
    await db.flush()
    return target
