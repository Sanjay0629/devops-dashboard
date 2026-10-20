"""The Administrator's editor for approval policies, endpoints 25.11 – 25.14 (``OPEN-105``).

Policies were seeded and unreachable from the application; a team that cannot separate requester
from approver had no way to say so. This is that way, and it is a privilege: ``admin:policies``,
held by ``ADMINISTRATOR`` only, every change audited ``APPROVAL_POLICY_CHANGED`` with the before and
after of every field, in the same transaction as the change.

What it deliberately cannot do, so the editor cannot weaken the workflow past what it implements:

* ``required_approvals`` is ``1``. A policy needing more is refused at request time (``OPEN-95``),
  so one cannot be saved.
* ``applies_to`` is fixed at creation.
* A policy that a service action policy links, or that any approval request references, cannot be
  deleted: removing it would silently remove a gate or orphan a decision's evidence.

What it *can* do is turn ``allow_self_approval`` on or move a policy to another environment. Both
are the point of an editor, and the audit row is their guard.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import Approval, ApprovalPolicy
from app.audit import service as audit
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.authz.models import Role
from app.core.errors import field_error
from app.core.logging import get_logger
from app.db.session import get_session
from app.inventory.models import ServiceActionPolicy
from app.inventory.service import ConflictError, DuplicateResourceError, NotFoundError

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/approval-policies", tags=["admin"])

Db = Annotated[AsyncSession, Depends(get_session)]
PolicyId = Annotated[uuid.UUID, Path(description="Approval policy identifier")]
ManagePolicies = Annotated[Authorized, require_permission("admin", "policies")]

PolicyName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$",
    ),
]
RoleName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z_]{0,31}$")]
PolicyEnvironment = Literal["development", "staging", "production", "other", "*"]
Window = Annotated[int, Field(ge=60, le=86_400)]

#: The audited fields, in one place so "before" and "after" cannot list different things.
FIELDS = (
    "name",
    "environment",
    "applies_to",
    "required_approvals",
    "allow_self_approval",
    "required_role",
    "approval_window_seconds",
)


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PolicyName
    environment: PolicyEnvironment
    applies_to: Literal["SERVICE_OPERATION", "DEPLOYMENT"]
    #: Only ``1``: a policy needing more is refused at request time (``OPEN-95``).
    required_approvals: Literal[1] = 1
    allow_self_approval: bool = False
    required_role: RoleName = "APPROVER"
    approval_window_seconds: Window = 3_600


class PolicyUpdate(BaseModel):
    """Only the fields named change. ``applies_to`` is not editable."""

    model_config = ConfigDict(extra="forbid")

    name: PolicyName | None = None
    environment: PolicyEnvironment | None = None
    required_approvals: Literal[1] | None = None
    allow_self_approval: bool | None = None
    required_role: RoleName | None = None
    approval_window_seconds: Window | None = None

    @model_validator(mode="after")
    def _no_explicit_null(self) -> PolicyUpdate:
        """Omitted means "leave alone"; an explicit ``null`` has no meaning and is refused."""
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("a field that is present must not be null")
        return self


class PolicyOut(BaseModel):
    policy_id: uuid.UUID
    name: str
    environment: str
    applies_to: str
    required_approvals: int
    allow_self_approval: bool
    required_role: str
    approval_window_seconds: int
    created_at: datetime
    updated_at: datetime


class PolicyList(BaseModel):
    items: list[PolicyOut]


def _out(row: ApprovalPolicy) -> PolicyOut:
    return PolicyOut(
        policy_id=row.id,
        name=row.name,
        environment=row.environment,
        applies_to=row.applies_to,
        required_approvals=row.required_approvals,
        allow_self_approval=row.allow_self_approval,
        required_role=row.required_role,
        approval_window_seconds=row.approval_window_seconds,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshot(row: ApprovalPolicy) -> dict[str, Any]:
    return {name: getattr(row, name) for name in FIELDS}


def _audit(
    db: AsyncSession,
    request: Request,
    authorized: Authorized,
    policy_id: uuid.UUID,
    change: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    audit.record(
        db,
        action_type="APPROVAL_POLICY_CHANGED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="approval_policy",
        target_id=policy_id,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"change": change, "before": before, "after": after},
    )
    logger.warning("approval_policy_changed", policy_id=str(policy_id), change=change)


async def _require_role(db: AsyncSession, role: str) -> None:
    found = (await db.execute(select(Role.id).where(Role.name == role))).first()
    if found is None:
        raise field_error(["body", "required_role"], "not_registered", "That role does not exist.")


async def _load(db: AsyncSession, policy_id: uuid.UUID) -> ApprovalPolicy:
    row = await db.get(ApprovalPolicy, policy_id)
    if row is None:
        raise NotFoundError
    return row


def _duplicate() -> DuplicateResourceError:
    return DuplicateResourceError("An approval policy with that name already exists.")


@router.get("", dependencies=[ReadBudget], summary="List approval policies")
async def list_policies(db: Db, _authorized: ManagePolicies) -> PolicyList:
    rows = (await db.execute(select(ApprovalPolicy).order_by(ApprovalPolicy.name))).scalars().all()
    return PolicyList(items=[_out(row) for row in rows])


@router.post("", status_code=201, summary="Create an approval policy")
async def create_policy(
    request: Request, body: PolicyCreate, db: Db, authorized: ManagePolicies
) -> PolicyOut:
    await _require_role(db, body.required_role)
    row = ApprovalPolicy(**body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise _duplicate() from error
    _audit(db, request, authorized, row.id, "created", None, _snapshot(row))
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.patch("/{policy_id}", summary="Edit an approval policy")
async def update_policy(
    request: Request, policy_id: PolicyId, body: PolicyUpdate, db: Db, authorized: ManagePolicies
) -> PolicyOut:
    row = await _load(db, policy_id)
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    if "required_role" in changes:
        await _require_role(db, changes["required_role"])
    before = _snapshot(row)
    for name, value in changes.items():
        setattr(row, name, value)
    after = _snapshot(row)
    if after == before:
        return _out(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise _duplicate() from error
    _audit(db, request, authorized, policy_id, "updated", before, after)
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.delete("/{policy_id}", status_code=204, summary="Delete an unreferenced approval policy")
async def delete_policy(
    request: Request, policy_id: PolicyId, db: Db, authorized: ManagePolicies
) -> Response:
    row = await _load(db, policy_id)
    linked = (
        await db.execute(
            select(ServiceActionPolicy.id)
            .where(ServiceActionPolicy.approval_policy_id == policy_id)
            .limit(1)
        )
    ).first()
    used = (
        await db.execute(
            select(Approval.id).where(Approval.approval_policy_id == policy_id).limit(1)
        )
    ).first()
    if linked is not None or used is not None:
        error = ConflictError("That policy is still in use and cannot be deleted.")
        error.details = {"reason": "policy_in_use"}
        raise error
    before = _snapshot(row)
    await db.delete(row)
    try:
        await db.flush()
    except IntegrityError as error:  # a reference appeared between the checks and the delete
        await db.rollback()
        conflict = ConflictError("That policy is still in use and cannot be deleted.")
        conflict.details = {"reason": "policy_in_use"}
        raise conflict from error
    _audit(db, request, authorized, policy_id, "deleted", before, None)
    await db.commit()
    return Response(status_code=204)
